import importlib.util
import pathlib
import unittest
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]/"scripts"))
import numpy as np
spec = importlib.util.spec_from_file_location('channel', pathlib.Path(__file__).resolve().parents[1] / 'scripts/channel.py')
channel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(channel)

class ChannelTests(unittest.TestCase):
    def test_two_cells_interfere_in_samples(self):
        a = np.ones(32, dtype=np.complex64)
        b = np.full(32, -1+0j, dtype=np.complex64)
        # Opposing same-frequency samples must cancel, not choose the strongest.
        np.testing.assert_array_equal(channel.mix_frames([a, b], [1, 1]), np.zeros(32))
        np.testing.assert_allclose(channel.mix_frames([a, b], [1, .25]), .75)

    def test_pathloss_and_overlap(self):
        model = {'reference_distance_m': 1, 'reference_loss_db': 40,
                 'path_loss_exponent': 2, 'iq_reference_dbm': -40}
        first = {'position_m': [0, 0], 'tx_power_dbm': 30}
        second = {'position_m': [200, 0], 'tx_power_dbm': 30}
        a, _ = channel.link_gain(first, [10, 0], model)
        b, _ = channel.link_gain(first, [100, 0], model)
        self.assertAlmostEqual(a/b, 10)  # 20 dB power, 10x amplitude
        self.assertAlmostEqual(b, channel.link_gain(second, [100, 0], model)[0])
        self.assertGreater(channel.link_gain(second, [190, 0], model)[0],
                           channel.link_gain(first, [190, 0], model)[0])

    def test_antenna_direction(self):
        model = {'reference_distance_m': 1, 'reference_loss_db': 40,
                 'path_loss_exponent': 2, 'iq_reference_dbm': -40}
        cell = {'position_m': [0, 0], 'tx_power_dbm': 30, 'azimuth_deg': 0,
                'beamwidth_deg': 90, 'max_antenna_loss_db': 30}
        forward = channel.link_gain(cell, [10, 0], model)[0]
        rear = channel.link_gain(cell, [-10, 0], model)[0]
        self.assertAlmostEqual(20*np.log10(forward/rear), 30)

    def test_delay_crosses_frame_boundary(self):
        filt = channel.LinkFilter(4, 1000, {'delay_samples': 2}, 1)
        np.testing.assert_array_equal(filt.apply(np.array([1,2,3,4], dtype=np.complex64)), [0,0,1,2])
        np.testing.assert_array_equal(filt.apply(np.array([5,6,7,8], dtype=np.complex64)), [3,4,5,6])

    def test_doppler_phase_continuity(self):
        filt = channel.LinkFilter(4, 1000, {'doppler_hz': 125}, 1)
        output = np.concatenate([filt.apply(np.ones(4)), filt.apply(np.ones(4))])
        np.testing.assert_allclose(output, np.exp(1j*2*np.pi*125*np.arange(8)/1000), atol=1e-6)

    def test_seeded_fading(self):
        settings = {'fading': 'block_rayleigh', 'coherence_frames': 2}
        a = channel.LinkFilter(16, 1000, settings, 123)
        b = channel.LinkFilter(16, 1000, settings, 123)
        first = a.apply(np.ones(16))
        np.testing.assert_array_equal(first, b.apply(np.ones(16)))
        np.testing.assert_array_equal(first, a.apply(np.ones(16)))
        self.assertFalse(np.array_equal(first, a.apply(np.ones(16))))

    def test_invalid_control_rejected(self):
        for data in ({'position_m':[float('nan'),0]}, {'position_m':[0]},
                     {'gain_offsets_db':[0]}, {'gain_offsets_db':[121,0]},
                     {'position_m':[True,0]}, {'fake_handover':True}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                channel.validate_control(data, 2)

    def test_bad_delay_rejected(self):
        for delay in (-1, 5):
            with self.assertRaises(ValueError):
                channel.LinkFilter(4, 1000, {'delay_samples':delay}, 1)

if __name__ == '__main__':
    unittest.main()
