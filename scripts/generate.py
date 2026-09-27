"""Generate disposable lab configs; identities and keys are synthetic."""
import configparser
import json
import pathlib
import secrets
from scenario_config import validate

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / 'build'
OUT.mkdir(exist_ok=True)
SOURCE = ROOT / 'vendor/srsRAN_4G'
scenario = validate(json.loads((ROOT / 'config/scenario.json').read_text()))
(OUT / 'scenario.json').write_text(json.dumps(scenario, indent=2))
# Generate once per lab workspace to keep credentials consistent across restart.
subscriber_path = OUT / 'subscriber.json'
if not subscriber_path.exists():
    subscriber_path.write_text(json.dumps({'imsi': '00101' + ''.join(str(secrets.randbelow(10)) for _ in range(10)),
                                           'k': secrets.token_hex(16), 'opc': secrets.token_hex(16)}, indent=2))
    subscriber_path.chmod(0o600)
subscriber = json.loads(subscriber_path.read_text())
if 'imei' not in subscriber:
    subscriber['imei'] = '00000000' + ''.join(str(secrets.randbelow(10)) for _ in range(7))
    subscriber_path.write_text(json.dumps(subscriber, indent=2))
    subscriber_path.chmod(0o600)
for c in scenario['cells']:
    name = c['name']
    port = 2101 if name == 'enb1' else 2201
    ip = '10.87.0.21' if name == 'enb1' else '10.87.0.22'
    ini = configparser.ConfigParser(strict=False)
    ini.read(SOURCE / 'srsenb/enb.conf.example')
    for key, val in {'enb_id': hex(c['enb_id']), 'mcc': '001', 'mnc': '01', 'mme_addr': '10.87.0.10',
                     'gtp_bind_addr': ip, 's1c_bind_addr': ip, 'n_prb': '6'}.items():
        ini['enb'][key] = val
    ini['enb_files'] = {'sib_config': '/lab/build/sib.conf', 'rr_config': f'/lab/build/{name}-rr.conf', 'rb_config': '/lab/build/rb.conf'}
    ini['rf']['device_name'] = 'zmq'
    # Exercise real AS ciphering and integrity across security-context transfer.
    ini['expert']['eea_pref_list'] = 'EEA2'
    ini['expert']['eia_pref_list'] = 'EIA2'
    ini['rf']['device_args'] = f'tx_port=tcp://*:{port},rx_port=tcp://10.87.0.30:{port-1},id={name},base_srate=1.92e6'
    ini['log']['all_level'] = 'info'
    ini['log']['rrc_level'] = 'debug'
    ini['log']['s1ap_level'] = 'debug'
    ini['log']['filename'] = f'/lab/build/results/{name}.log'
    ini['pcap']['enable'] = 'true'
    ini['pcap']['filename'] = f'/lab/build/results/{name}-mac.pcap'
    with open(OUT / f'{name}.conf', 'w') as f: ini.write(f)
    base = (SOURCE / 'srsenb/rr.conf.example').read_text().split('cell_list =')[0]
    neighbors = ',\n'.join('{eci = %s; dl_earfcn = 3350; pci = %s;}' % (hex(n['enb_id']*256+1), n['pci']) for n in scenario['cells'])
    body = '''cell_list = ({cell_id = 1; tac = 7; pci = %s; root_seq_idx = %s; dl_earfcn = 3350;
ho_active = true; scell_list = (); meas_cell_list = (%s);
meas_report_desc = ({eventA = 3; a3_offset = 3; hysteresis = 1; time_to_trigger = 160;
trigger_quant = "RSRP"; max_report_cells = 2; report_interv = 120; report_amount = 8;});
meas_quant_desc = {rsrq_config = 4; rsrp_config = 4;};}); nr_cell_list = ();\n''' % (c['pci'], 204 if name == 'enb1' else 264, neighbors)
    (OUT / f'{name}-rr.conf').write_text(base+body)
for name in ('sib', 'rb'):
    (OUT / f'{name}.conf').write_text((SOURCE / f'srsenb/{name}.conf.example').read_text().replace('prach_freq_offset = 4', 'prach_freq_offset = 0'))
ini = configparser.ConfigParser(strict=False)
ini.read(SOURCE / 'srsue/ue.conf.example')
ini['rf']['device_name'] = 'zmq'
ini['rf']['device_args'] = 'tx_port=tcp://*:2001,rx_port=tcp://10.87.0.30:2000,id=ue,base_srate=1.92e6'
ini['usim']['algo'] = 'milenage'
for key in ('imsi','k','opc'): ini['usim'][key] = subscriber[key]
ini['usim']['imei'] = subscriber['imei']
ini['nas']['apn'] = 'internet'
ini['gw']['netns'] = 'ue1'
ini['log']['all_level'] = 'info'
ini['log']['rrc_level'] = 'debug'
ini['log']['filename'] = '/lab/build/results/ue.log'
ini['pcap']['enable'] = 'mac,nas'
ini['pcap']['mac_filename'] = '/lab/build/results/ue-mac.pcap'
ini['pcap']['nas_filename'] = '/lab/build/results/ue-nas.pcap'
with open(OUT / 'ue.conf','w') as f: ini.write(f)
(OUT / 'ue.conf').chmod(0o600)
(OUT / 'results').mkdir(exist_ok=True)
print('Generated disposable configs in', OUT)
