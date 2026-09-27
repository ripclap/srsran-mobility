import copy
import importlib.util
import json
import pathlib
import unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('scenario',ROOT/'scripts/scenario_config.py')
scenario=importlib.util.module_from_spec(spec)
spec.loader.exec_module(scenario)

class ScenarioTests(unittest.TestCase):
    def setUp(self): self.fixture=json.loads((ROOT/'config/scenario.json').read_text())
    def test_default_profile(self): self.assertIs(scenario.validate(self.fixture),self.fixture)
    def test_does_not_accept_misleading_radio_clock(self):
        for field,value in [('sample_rate_hz',23040000),('frame_samples',23040)]:
            data=copy.deepcopy(self.fixture);data[field]=value
            with self.assertRaises(ValueError): scenario.validate(data)
    def test_bad_physical_settings(self):
        for field,value in [('beamwidth_deg',0),('position_m',[0]),('tx_power_dbm',float('nan')),('pci',65537)]:
            data=copy.deepcopy(self.fixture);data['cells'][0][field]=value
            with self.assertRaises(ValueError): scenario.validate(data)
    def test_bad_impairments(self):
        for settings in ({'delay_samples':.5},{'fading':'fake'},{'coherence_frames':0},{'doppler_hz':float('inf')},{'channel_success':True}):
            data=copy.deepcopy(self.fixture);data['cells'][0]['channel']=settings
            with self.assertRaises(ValueError): scenario.validate(data)
    def test_ambiguous_cells(self):
        data=copy.deepcopy(self.fixture);data['cells'][1]['pci']=data['cells'][0]['pci']
        with self.assertRaises(ValueError): scenario.validate(data)

if __name__=='__main__': unittest.main()
