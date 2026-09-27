"""End-to-end mobility proof; uses real UE traffic, stack logs and independent pcap decoding."""
import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import time
from pcap_contract import validate_x2_pdml

def security_algorithms(text):
    return {'ciphering': sorted(set(re.findall(r'"cipheringAlgorithm": "([^"]+)"', text))),
            'integrity': sorted(set(re.findall(r'"integrityProtAlgorithm": "([^"]+)"', text)))}

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT/'build/results'
parser = argparse.ArgumentParser()
parser.add_argument('--runtime', choices=['compose','containerlab'], default='containerlab')
parser.add_argument('--mode', choices=['s1','x2'], default='x2')
parser.add_argument('--fault-test', action='store_true')
parser.add_argument('--max-loss-percent', type=float, default=5)
args = parser.parse_args()
prefix = 'clab-srs-mobility-' if args.runtime == 'containerlab' else 'srs-mobility-'
suffix = '' if args.runtime == 'containerlab' else '-1'
def name(node): return prefix+node+suffix

def call(command, check=True):
    return subprocess.run(command, text=True, capture_output=True, check=check)

def execute(node, command, check=True):
    return call(['docker','exec',name(node),*command],check)

def log(node):
    path=OUT/(node+'.log')
    return path.read_text(errors='replace') if path.exists() else ''

def ue_log():
    path=OUT/'ue-stdout.log'
    return path.read_text(errors='replace') if path.exists() else ''

def wait(predicate, description, timeout=180):
    print(description, flush=True)
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if predicate(): return
        time.sleep(.2)
    raise RuntimeError('Timed out: '+description)

def address():
    result=execute('ue',['ip','netns','exec','ue1','ip','-4','-o','addr','show','tun_srsue'],False)
    found=re.search(r'inet ([\d.]+)/',result.stdout)
    return found.group(1) if found else None

def move(x,duration=8):
    print(f'Moving UE to x={x} over {duration} radio seconds',flush=True)
    result=execute('channel',['python3','/lab/scripts/control.py','--position',str(x),'0','--duration',str(duration)])
    print(result.stdout.strip(),flush=True)

def handovers(): return ue_log().count('HO successful')
def switch_ack(): return sum(log(n).count('X2 PathSwitch acknowledged') for n in ('enb1','enb2'))
def timeouts(): return sum(log(n).count('X2 handover timeout') for n in ('enb1','enb2'))
def decoded(path, display_filter):
    # Separate decoder container also works after capture-producing containers stop.
    result=call(['docker','run','--rm','-v',f'{ROOT}:/lab','--entrypoint','tshark',
                 'srsran-mobility-core:verified','-r',f'/lab/build/results/{path}',
                 '-Y',display_filter,'-T','fields','-e','frame.time_epoch','-e','_ws.col.Info'])
    return result.stdout

def acl(add):
    call(['docker','run','--rm','--network','container:'+name('enb2'),'--cap-add','NET_ADMIN',
          '--entrypoint','iptables','srsran-mobility-core:verified', '-I' if add else '-D',
          'OUTPUT','-p','sctp','--dport','36422','-j','DROP'])

wait(lambda: 'Network attach successful' in ue_log() and address(), 'Waiting for LTE attach and UE interface')
if handovers(): raise RuntimeError('Start a fresh run at x=10 before verification')
if args.mode=='x2':
    wait(lambda: 'X2 Setup response ready=1' in log('enb1'), 'Waiting for native X2 setup')
ip_before=address()
attach_count=ue_log().count('Network attach successful')
ping_log=OUT/'verification-ping.log'
handle=ping_log.open('w')
ping=subprocess.Popen(['docker','exec',name('ue'),'sh','-c',
    'ip netns exec ue1 ping -D -i 0.1 10.45.0.1 & probe_pid=$!; echo "$probe_pid" > /lab/build/results/probe.pid; wait "$probe_pid"'],stdout=handle,stderr=subprocess.STDOUT)
blocked=False
failure=None
ip_after=None
try:
    wait(lambda: ping_log.read_text().count('64 bytes')>=20,'Waiting for baseline UE traffic',60)
    move(190)
    wait(lambda: handovers()==1 and (args.mode!='x2' or switch_ack()>=1),'Waiting for forward handover')
    if address()!=ip_before: raise RuntimeError('UE IP changed during forward handover')
    move(10)
    wait(lambda: handovers()==2 and (args.mode!='x2' or switch_ack()>=2),'Waiting for reverse handover')
    if args.fault_test:
        if args.mode!='x2': raise RuntimeError('Fault test requires X2 mode')
        count=timeouts()
        acl(True); blocked=True
        move(123)
        wait(lambda: timeouts()>=count+1,'Waiting for source X2 preparation timeout',90)
        # Idle target logging may remain buffered. Let both radio timers expire;
        # require both timeout records after shutdown flushes their logs.
        move(123,3)
        if handovers()!=2 or switch_ack()!=2: raise RuntimeError('Blocked X2 unexpectedly completed a handover')
        acl(False); blocked=False
        move(10,3)
        move(190)
        wait(lambda: handovers()==3 and switch_ack()>=3,'Waiting for X2 recovery handover')
    if address()!=ip_before or ue_log().count('Network attach successful')!=attach_count:
        raise RuntimeError('Handover replaced the UE session')
    before=ping_log.read_text().count('64 bytes')
    wait(lambda: ping_log.read_text().count('64 bytes')>=before+20,'Waiting for post-handover traffic',60)
except Exception as exc:
    failure=str(exc)
finally:
    ip_after=address()
    if blocked: acl(False)
    execute('ue',['sh','-c','kill -INT "$(cat /lab/build/results/probe.pid)"'],False)
    try: ping.wait(timeout=10)
    except subprocess.TimeoutExpired: ping.terminate(); ping.wait(timeout=5)
    handle.close()
    # Flush final logs and packet buffers. Stop only these five lab containers.
    call(['docker','stop',*[name(n) for n in ('ue','enb1','enb2','channel','core')]])

pdml=call(['docker','run','--rm','-v',f'{ROOT}:/lab','--entrypoint','tshark',
           'srsran-mobility-core:verified','-r','/lab/build/results/enb1-network.pcap','-Y','x2ap','-T','pdml']).stdout
try:
    contract_counts=validate_x2_pdml(pdml)
except ValueError as exc:
    failure=failure or str(exc)
    contract_counts={}
x2_wire=decoded('enb1-network.pcap','x2ap')
s1_wire=decoded('core.pcap','s1ap')
malformed=decoded('enb1-network.pcap','x2ap && _ws.malformed')
(OUT/'verification-x2-wire.txt').write_text(x2_wire)
(OUT/'verification-s1-wire.txt').write_text(s1_wire)
text=ping_log.read_text()
statistics=re.search(r'(\d+) packets transmitted, (\d+) received, ([\d.]+)% packet loss',text)
if not statistics: failure=failure or 'Missing ping statistics'
expected=3 if args.fault_test else 2
if args.mode=='x2':
    if len(re.findall(r'\t.*PathSwitchRequestAcknowledge',s1_wire))<expected: failure=failure or 'Missing path switch acknowledgement on wire'
    if not contract_counts.get('X2SetupRequest') or not contract_counts.get('X2SetupResponse'): failure=failure or 'X2 setup exchange missing on wire'
    for message in ('HandoverRequestAcknowledge','SNStatusTransfer','UEContextRelease'):
        if x2_wire.count(message)<expected: failure=failure or 'Missing X2 '+message
    forbidden=decoded('core.pcap','s1ap.procedureCode == 0 || s1ap.procedureCode == 1')
    if forbidden.strip(): failure=failure or 'X2 mode emitted S1 handover preparation/resource allocation'
    if args.fault_test and timeouts()<2: failure=failure or 'Source/target timeout cleanup missing from flushed logs'
    if malformed.strip(): failure=failure or 'Malformed X2AP capture'
    releases=sum(log(n).count('Rx X2 UEContextRelease cleaned source') for n in ('enb1','enb2'))
    if releases<expected: failure=failure or 'Source context cleanup not observed'
else:
    if s1_wire.count('HandoverNotify')<expected: failure=failure or 'S1 handover notification missing'
    if x2_wire.strip(): failure=failure or 'S1 run unexpectedly emitted X2AP'
reply_times=[float(x) for x in re.findall(r'\[([\d.]+)\] 64 bytes',text)]
rtt=[float(x) for x in re.findall(r'time=([\d.]+) ms',text)]
security=security_algorithms((OUT/'ue.log').read_text(errors='replace'))
if security != {'ciphering':['eea2'], 'integrity':['eia2']}:
    failure=failure or 'Expected negotiated EEA2 encryption and EIA2 integrity'
summary={'passed':failure is None,'failure':failure,'runtime':args.runtime,'mode':args.mode,
         'as_security':security,
         'fault_test':args.fault_test,'x2_standard_contract_counts':contract_counts,'successful_handovers':handovers(),'ue_ip_before':ip_before,'ue_ip_after':ip_after,
         'additional_attaches':ue_log().count('Network attach successful')-attach_count,
         'max_reply_gap_seconds':max((b-a for a,b in zip(reply_times,reply_times[1:])),default=None),
         'max_rtt_ms':max(rtt,default=None),'captures':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in OUT.glob('*network.pcap')},
         'core_pcap_sha256':hashlib.sha256((OUT/'core.pcap').read_bytes()).hexdigest()}
if statistics:
    summary.update(packets_transmitted=int(statistics[1]),packets_received=int(statistics[2]),packet_loss_percent=float(statistics[3]))
    if float(statistics[3])>args.max_loss_percent: summary.update(passed=False,failure='Packet loss exceeds acceptance threshold')
(OUT/'verification.json').write_text(json.dumps(summary,indent=2))
print(json.dumps(summary,indent=2),flush=True)
raise SystemExit(0 if summary['passed'] else 1)
