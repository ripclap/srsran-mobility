import json
import pathlib
import signal
import subprocess
import time
import pymongo
import yaml

BASE = pathlib.Path('/open5gs/install')
OUT = pathlib.Path('/lab/build/results')
OUT.mkdir(parents=True, exist_ok=True)
client = pymongo.MongoClient('mongodb://10.87.0.11:27017', serverSelectionTimeoutMS=2000)
for _ in range(30):
    try:
        client.admin.command('ping')
        break
    except pymongo.errors.PyMongoError:
        time.sleep(1)
else: raise RuntimeError('Disposable MongoDB did not become ready')
sub = json.loads(pathlib.Path('/lab/build/subscriber.json').read_text())
record = {'imsi': sub['imsi'], 'security': {'k': sub['k'], 'opc': sub['opc'], 'amf': '8000', 'sqn': 0},
          'ambr': {'downlink': {'value': 10, 'unit': 2}, 'uplink': {'value': 10, 'unit': 2}},
          'slice': [{'sst': 1, 'default_indicator': True, 'session': [{'name': 'internet', 'type': 1,
                     'ambr': {'downlink': {'value': 10, 'unit': 2}, 'uplink': {'value': 10, 'unit': 2}},
                     'qos': {'index': 9, 'arp': {'priority_level': 8, 'pre_emption_capability': 1, 'pre_emption_vulnerability': 1}}}]}],
          'access_restriction_data': 32, 'subscriber_status': 0, 'network_access_mode': 0,
          'subscribed_rau_tau_timer': 12, 'schema_version': 1}
client.open5gs.subscribers.update_one({'imsi': sub['imsi']}, {'$setOnInsert': record}, upsert=True)
names = ['hss', 'pcrf', 'sgwc', 'sgwu', 'smf', 'upf', 'mme']
for name in names:
    config = yaml.safe_load((BASE / f'etc/open5gs/{name}.yaml').read_text())
    if 'db_uri' in config: config['db_uri'] = 'mongodb://10.87.0.11/open5gs'
    config['logger'] = {'file': {'path': str(OUT / (name + '.log'))}, 'level': 'debug' if name == 'mme' else 'info'}
    config['global']['max']['ue'] = 16
    if name == 'mme':
        config['mme']['s1ap']['server'] = [{'address': '10.87.0.10'}]
        for group in ('gummei', 'tai'):
            for item in config['mme'][group]:
                item['plmn_id'] = {'mcc': '001', 'mnc': '01'}
                if group == 'tai': item['tac'] = 7
        config['mme'].pop('sgsap', None)
    if name == 'sgwu': config[name]['gtpu']['server'] = [{'address': '10.87.0.10'}]
    if name == 'smf':
        config[name].pop('sbi', None)
        config[name]['dns'] = ['10.45.0.1']
    if name in ('smf','upf'): config[name]['session'] = [{'subnet': '10.45.0.0/16', 'gateway': '10.45.0.1'}]
    (OUT / (name + '.yaml')).write_text(yaml.safe_dump(config))
if subprocess.run(['ip', 'link', 'show', 'ogstun'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
    subprocess.run(['ip', 'tuntap', 'add', 'name', 'ogstun', 'mode', 'tun'], check=True)
subprocess.run(['ip', 'addr', 'replace', '10.45.0.1/16', 'dev', 'ogstun'], check=True)
subprocess.run(['ip', 'link', 'set', 'ogstun', 'up'], check=True)
processes = []
processes.append(subprocess.Popen(['tcpdump', '-i', 'any', '-U', '-w', str(OUT / 'core.pcap'),
    'sctp or udp port 2152 or udp port 2123 or udp port 8805 or icmp'], stdout=subprocess.DEVNULL, stderr=open(OUT / 'capture.log', 'w')))
for name in names:
    output = open(OUT / (name + '-stdout.log'), 'w')
    processes.append(subprocess.Popen([str(BASE / f'bin/open5gs-{name}d'), '-c', str(OUT / (name + '.yaml'))], stdout=output, stderr=subprocess.STDOUT))
    time.sleep(0.4)
stopping = False
def stop(signum, frame):
    global stopping
    stopping = True
signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
while not stopping and all(p.poll() is None for p in processes): time.sleep(0.2)
status = 0 if stopping else 1
for p in processes:
    if p.poll() is None: p.terminate()
for p in processes:
    try: p.wait(timeout=5)
    except subprocess.TimeoutExpired:
        p.kill(); p.wait()
raise SystemExit(status)
