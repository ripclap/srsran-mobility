"""Set a virtual UE position or move it using the channel's radio clock."""
import argparse
import importlib.util
import json
import pathlib
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('channel', ROOT/'scripts/channel.py')
channel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(channel)
parser = argparse.ArgumentParser()
parser.add_argument('--position', nargs=2, type=float, required=True, metavar=('X','Y'))
parser.add_argument('--offsets', nargs=2, type=float)
parser.add_argument('--duration', type=float, default=0, help='Seconds of simulated radio time')
args = parser.parse_args()
control = ROOT/'build/control.json'
state_path = ROOT/'build/results/channel-state.json'

def read_state():
    for _ in range(10):
        try:
            return json.loads(state_path.read_text())
        except FileNotFoundError:
            time.sleep(.05)
    raise RuntimeError('Channel state is unavailable')

def write(position):
    data = {'position_m':position}
    if args.offsets is not None:
        data['gain_offsets_db'] = args.offsets
    channel.validate_control(data, 2)
    temp = control.with_suffix('.control-tmp')
    temp.write_text(json.dumps(data, allow_nan=False))
    temp.replace(control)

if args.duration < 0:
    parser.error('duration must be nonnegative')
if args.duration == 0:
    write(args.position)
else:
    start = read_state()
    last_progress = time.monotonic()
    last_clock = start['radio_seconds']
    while True:
        state = read_state()
        if state['run_id'] != start['run_id']:
            raise RuntimeError('Channel restarted during movement')
        clock = state['radio_seconds']
        if clock != last_clock:
            last_progress = time.monotonic()
            last_clock = clock
        if time.monotonic()-last_progress > 20:
            raise RuntimeError('Radio clock stopped; movement aborted')
        fraction = min(1, (clock-start['radio_seconds'])/args.duration)
        write([a+(b-a)*fraction for a,b in zip(start['position_m'],args.position)])
        if fraction == 1:
            break
        time.sleep(.05)
print(json.dumps({'position_m':args.position,'duration_radio_seconds':args.duration}))
