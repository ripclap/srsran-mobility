import argparse
import os
import json
import pathlib
import signal
import subprocess
import time

parser = argparse.ArgumentParser()
parser.add_argument('node', choices=['enb1','enb2','ue'])
a = parser.parse_args()
out = pathlib.Path('/lab/build/results')
out.mkdir(parents=True, exist_ok=True)
children = [subprocess.Popen(['tcpdump','-i','any','-U','-w',str(out / f'{a.node}-network.pcap'),'sctp or udp port 2152 or icmp'], stderr=open(out/f'{a.node}-capture.log','w'))]
if a.node == 'ue':
    subprocess.run(['ip','netns','del','ue1'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(['ip','netns','add','ue1'],check=True)
    subprocess.run(['ip','netns','exec','ue1','ip','link','set','lo','up'],check=True)
    binary = 'srsue'
else: binary = 'srsenb'
os.environ['SRSRAN_LAB_FFTW_ESTIMATE'] = '1'
if a.node != 'ue' and os.environ.get('LAB_HANDOVER_MODE', 's1') == 'x2':
    scenario = json.loads(pathlib.Path('/lab/build/scenario.json').read_text())
    local = next(c for c in scenario['cells'] if c['name'] == a.node)
    peer = next(c for c in scenario['cells'] if c['name'] != a.node)
    os.environ.update({'SRSRAN_LAB_X2_PEER': '10.87.0.22' if a.node == 'enb1' else '10.87.0.21',
                      'SRSRAN_LAB_X2_PEER_ECI': str(peer['enb_id']*256+1),
                      'SRSRAN_LAB_X2_PCI': str(local['pci']), 'SRSRAN_LAB_X2_EARFCN': '3350'})
if os.environ.get('LAB_USE_DEV_BINARY', '0') == '1' and pathlib.Path('/lab/build/bin/' + binary).exists(): binary = '/lab/build/bin/' + binary
children.append(subprocess.Popen([binary,f'/lab/build/{a.node}.conf'], stdin=subprocess.PIPE,
    stdout=open(out/f'{a.node}-stdout.log','w'),stderr=subprocess.STDOUT))
stopping = False
def stop(*args):
    global stopping
    stopping = True
signal.signal(signal.SIGTERM,stop)
signal.signal(signal.SIGINT,stop)
while not stopping and all(p.poll() is None for p in children): time.sleep(.2)
status = 0 if stopping else 1
for p in children:
    if p.poll() is None: p.terminate()
for p in children:
    try: p.wait(timeout=5)
    except subprocess.TimeoutExpired:
        p.kill(); p.wait()
raise SystemExit(status)
