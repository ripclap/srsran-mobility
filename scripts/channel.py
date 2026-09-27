"""I/Q channel for one virtual LTE UE and two independent eNBs.

No protocol messages or handover success is fabricated. Source complex64
samples are attenuated and summed; srsUE performs synchronization/decoding.
"""
import argparse
import collections
import json
import math
import pathlib
import queue
import threading
import time
import numpy as np
import zmq
import faulthandler
import signal
import uuid
from scenario_config import validate
faulthandler.register(signal.SIGUSR1, all_threads=True)


def link_gain(cell, position, model):
    distance = max(math.dist(cell['position_m'], position), model['reference_distance_m'])
    loss = model['reference_loss_db'] + 10 * model['path_loss_exponent'] * math.log10(distance / model['reference_distance_m'])
    angle = math.degrees(math.atan2(position[1] - cell['position_m'][1], position[0] - cell['position_m'][0]))
    delta = (angle - cell.get('azimuth_deg', 0) + 180) % 360 - 180
    antenna_loss = min(cell.get('max_antenna_loss_db', 0), 12 * (delta / cell.get('beamwidth_deg', 360)) ** 2)
    received = cell['tx_power_dbm'] - loss - antenna_loss
    # A calibration offset maps calculated power to normalized virtual I/Q.
    gain = 10 ** ((received - model['iq_reference_dbm']) / 20)
    return gain, {'distance_m': distance, 'path_loss_db': loss + antenna_loss, 'predicted_rx_dbm': received}


class LinkFilter:
    """Optional integer sample delay, deterministic Doppler, and block Rayleigh fading.

    Delay includes the preceding frame tail. Doppler phase is continuous across
    frames. Block fading is an approximation, not a standards channel profile.
    """
    def __init__(self, count, rate, settings, seed):
        self.rate = rate
        self.delay = int(settings.get('delay_samples', 0))
        if self.delay < 0 or self.delay > count:
            raise ValueError('delay_samples must be between zero and frame_samples')
        self.tail = np.zeros(self.delay, dtype=np.complex64)
        self.frequency = float(settings.get('doppler_hz', 0))
        self.fading = settings.get('fading', 'none')
        if self.fading not in ('none', 'block_rayleigh'):
            raise ValueError('fading must be none or block_rayleigh')
        self.rng = np.random.default_rng(seed)
        self.sample_index = 0
        self.coefficient = 1+0j
        self.coherence = max(1, int(settings.get('coherence_frames', 100)))
        self.frames = 0

    def apply(self, samples):
        result = samples
        if self.delay:
            result = np.concatenate((self.tail, samples[:-self.delay]))
            self.tail = samples[-self.delay:].copy()
        if self.frequency:
            phase = 2 * np.pi * self.frequency * (self.sample_index + np.arange(len(samples))) / self.rate
            result = result * np.exp(1j * phase)
        if self.fading == 'block_rayleigh':
            if self.frames % self.coherence == 0:
                self.coefficient = (self.rng.normal() + 1j*self.rng.normal()) / math.sqrt(2)
            result = result * self.coefficient
        self.frames += 1
        self.sample_index += len(samples)
        return result.astype(np.complex64, copy=False)


def mix_frames(frames, gains):
    if len(frames) != len(gains) or not frames:
        raise ValueError('Every cell needs a frame and gain')
    mixed = np.zeros_like(frames[0], dtype=np.complex64)
    for samples, gain in zip(frames, gains):
        if samples.shape != mixed.shape:
            raise ValueError('Cell frames have different dimensions')
        mixed += samples * gain
    return mixed


def validate_control(data, cells):
    if set(data) - {'position_m', 'gain_offsets_db'}:
        raise ValueError('Unknown control fields')
    for field, count in [('position_m', 2), ('gain_offsets_db', cells)]:
        if field in data:
            if not isinstance(data[field], list) or len(data[field]) != count or any(
                    not isinstance(x, (float, int)) or isinstance(x, bool) or not math.isfinite(x) for x in data[field]):
                raise ValueError('Invalid ' + field)
    if any(abs(x) > 120 for x in data.get('gain_offsets_db', [])):
        raise ValueError('Gain offsets must be within +/-120 dB')
    return data


class Source:
    def __init__(self, context, endpoint):
        self.socket = context.socket(zmq.REQ)
        self.socket.setsockopt(zmq.LINGER, 0)
        self.socket.connect(endpoint)
        self.buffer = np.empty(0, dtype=np.complex64)
        self.endpoint = endpoint
        self.received = 0

    def read(self, count):
        while len(self.buffer) < count:
            self.socket.send(b'\x00')
            if not self.socket.poll(20000):
                raise TimeoutError('Radio source stopped responding: ' + self.endpoint)
            raw = self.socket.recv()
            self.received += 1
            if not raw or len(raw) % 8:
                raise ValueError('Invalid complex64 radio frame')
            self.buffer = np.concatenate((self.buffer, np.frombuffer(raw, dtype='<c8')))
        result, self.buffer = self.buffer[:count], self.buffer[count:]
        return result


class Input:
    def __init__(self, context, endpoint, count):
        self.frames = queue.Queue(maxsize=20)
        self.thread = threading.Thread(target=self.pump, args=(context, endpoint, count), daemon=True)
        self.thread.start()

    def pump(self, context, endpoint, count):
        source = Source(context, endpoint)
        try:
            while True:
                self.frames.put(source.read(count))
        except Exception as exc:
            print(f'Fatal input failure: {endpoint}: {exc}', flush=True)
            import os
            os._exit(1)

    def read(self):
        return self.frames.get()


class Output:
    def __init__(self, context, endpoint):
        self.frames = collections.deque()
        self.condition = threading.Condition()
        self.context, self.endpoint = context, endpoint
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def put(self, raw):
        with self.condition:
            while len(self.frames) >= 20:
                self.condition.wait()
            self.frames.append(raw)
            self.condition.notify_all()

    def serve(self):
        self.socket = self.context.socket(zmq.REP)
        self.socket.bind(self.endpoint)
        try:
            while True:
                self.socket.recv()
                with self.condition:
                    while not self.frames:
                        self.condition.wait()
                    raw = self.frames.popleft()
                    self.condition.notify_all()
                self.socket.send(raw)
        except Exception as exc:
            print(f'Fatal output failure: {exc}', flush=True)
            import os
            os._exit(1)


class Channel:
    def __init__(self, scenario_path, output_dir):
        self.scenario_path = pathlib.Path(scenario_path)
        self.control_path = self.scenario_path.parent / 'control.json'
        self.scenario = validate(json.loads(self.scenario_path.read_text()))
        self.output_dir = pathlib.Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.context = zmq.Context()
        self.run_id = str(uuid.uuid4())
        self.samples = 0
        self.rng = np.random.default_rng(self.scenario['channel']['seed'])
        self.state_lock = threading.Lock()
        self.position = self.scenario['ue']['position_m']
        self.offsets = [0.0] * len(self.scenario['cells'])
        self.last_control = None
        self.dl_filters = [LinkFilter(self.scenario['frame_samples'], self.scenario['sample_rate_hz'], c.get('channel', {}), self.scenario['channel']['seed']+i) for i,c in enumerate(self.scenario['cells'])]
        self.ul_filters = [LinkFilter(self.scenario['frame_samples'], self.scenario['sample_rate_hz'], c.get('channel', {}), self.scenario['channel']['seed']+100+i) for i,c in enumerate(self.scenario['cells'])]
        self.ul_rng = np.random.default_rng(self.scenario['channel']['seed']+1000)
        self.trace = open(self.output_dir / 'channel.jsonl', 'a', buffering=1)

    def controls(self, direction="dl"):
        try:
            raw = self.control_path.read_text()
        except FileNotFoundError:
            raw = None  # Atomic rename on the host shared mount can briefly disappear.
        if raw is not None:
            if raw != self.last_control:
                data = validate_control(json.loads(raw), len(self.scenario['cells']))
                with self.state_lock:
                    self.position = data.get('position_m', self.position)
                    self.offsets = data.get('gain_offsets_db', self.offsets)
                self.last_control = raw
        with self.state_lock:
            gains, detail = [], []
            for cell, offset in zip(self.scenario['cells'], self.offsets):
                gain, info = link_gain(cell, self.position, self.scenario['channel'])
                if direction == 'ul':
                    received = self.scenario['ue'].get('tx_power_dbm', 23) - info['path_loss_db'] + cell.get('rx_gain_db', 0)
                    gain = 10 ** ((received - self.scenario['channel'].get('ul_iq_reference_dbm', -47)) / 20)
                    info['predicted_rx_dbm'] = received
                gains.append(gain * 10 ** (offset / 20))
                detail.append(dict(info, name=cell['name'], gain_offset_db=offset, iq_gain=gains[-1]))
            return gains, detail

    def downlink(self):
        # Both eNB streams advance equally: never mute a cell by stopping its clock.
        sources = [Input(self.context, c['dl_endpoint'], self.scenario['frame_samples']) for c in self.scenario['cells']]
        destination = Output(self.context, self.scenario['ue']['dl_listen'])
        count = self.scenario['frame_samples']
        while True:
            frames = [source.read() for source in sources]
            gains, detail = self.controls()
            frames = [f.apply(x) for f,x in zip(self.dl_filters, frames)]
            mixed = mix_frames(frames, gains)
            sigma = self.scenario['channel']['noise_std_iq']
            if sigma:
                mixed += (self.rng.normal(0, sigma, count) + 1j * self.rng.normal(0, sigma, count)).astype(np.complex64)
            destination.put(mixed.astype('<c8', copy=False).tobytes())
            self.samples += count
            if self.samples % (count * 100) == 0:
                state = {'run_id': self.run_id, 'wall_time': time.time(), 'radio_seconds': self.samples / self.scenario['sample_rate_hz'],
                         'position_m': self.position, 'links': detail}
                self.trace.write(json.dumps(state) + '\n')
                tmp = self.output_dir / 'channel-state.tmp'
                tmp.write_text(json.dumps(state, indent=2))
                tmp.replace(self.output_dir / 'channel-state.json')

    def uplink(self):
        source = Source(self.context, self.scenario['ue']['ul_endpoint'])
        destinations = [Output(self.context, c['ul_listen']) for c in self.scenario['cells']]
        while True:
            samples = source.read(self.scenario['frame_samples'])
            gains, _ = self.controls("ul")
            for output, gain, filt in zip(destinations, gains, self.ul_filters):
                signal_samples = filt.apply(samples) * gain
                sigma = self.scenario['channel']['noise_std_iq']
                if sigma:
                    signal_samples += self.ul_rng.normal(0, sigma, len(samples)) + 1j*self.ul_rng.normal(0, sigma, len(samples))
                output.put(signal_samples.astype('<c8').tobytes())

    def run(self):
        def uplink_supervised():
            try:
                self.uplink()
            except Exception as exc:
                print(f'Fatal uplink failure: {exc}', flush=True)
                import os
                os._exit(1)
        thread = threading.Thread(target=uplink_supervised, daemon=True)
        thread.start()
        self.downlink()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--scenario', default='/lab/build/scenario.json')
    parser.add_argument('--output', default='/lab/build/results')
    args = parser.parse_args()
    Channel(args.scenario, args.output).run()
