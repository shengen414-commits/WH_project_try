"""Replay observation JSONL through the same estimator, without any hardware.

python -m PyScripts.replay_vehicle_state trace.jsonl --output state.csv
"""
import argparse
import csv
import json
from pathlib import Path
try:
    from .vehicle_state import contracts
    from .vehicle_state.estimator import StateEstimator
except ImportError:
    from vehicle_state import contracts
    from vehicle_state.estimator import StateEstimator


def replay(path, output):
    estimator = StateEstimator()
    kinds = {name: getattr(contracts, name) for name in ('ImuObservation', 'SpeedObservation', 'LineObservation', 'VisionObservation')}
    fields = ['time_s', 'lateral_error_m', 'heading_error_rad', 'yaw_rate_rad_s', 'speed_m_s',
              'sigma_y_m', 'sigma_heading_rad', 'valid', 'reason', 'rejected_updates', 'delayed_replays']
    count = 0
    with Path(path).open(encoding='utf-8') as source, Path(output).open('x', encoding='utf-8', newline='') as target:
        writer = csv.DictWriter(target, fieldnames=fields)
        writer.writeheader()
        for line in source:
            event = json.loads(line)
            if event.get('kind') != 'observation':
                continue
            data = dict(event['data'])
            kind = data.pop('type')
            estimator.submit(kinds[kind](**data))
            state = estimator.snapshot(estimator.core.t)
            writer.writerow({key: state[key] for key in fields})
            count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trace')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(f'已回放 {replay(args.trace, args.output)} 条观测；未打开硬件')


if __name__ == '__main__':
    main()
