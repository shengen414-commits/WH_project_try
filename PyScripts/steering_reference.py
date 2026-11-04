"""Optional manual-test reference. Loading/querying never changes a controller."""
import copy
import json
import math
from pathlib import Path

try:
    from .vehicle_state.profile import defaults, validate, actuator_table, angle_to_pulse, finite
except ImportError:
    from vehicle_state.profile import defaults, validate, actuator_table, angle_to_pulse, finite


DEFAULT_PATH = Path(__file__).resolve().parent / 'calibration' / 'steering_reference.json'


class SteeringReference:
    """Degrees refer to the measured front wheel, left positive/right negative.

    This is an estimate for the recorded test condition, not a motion command,
    measured live feedback, mandatory controller mapping or firmware travel limit.
    """
    def __init__(self, data):
        if (not isinstance(data, dict) or data.get('schema') != 1 or
                data.get('usage') != 'reference_only' or
                type(data.get('center_us')) is not int or data['center_us'] != 1500):
            raise ValueError('参考表需为schema=1、reference_only、中位1500μs')
        profile = defaults()
        try:
            profile['actuator'] = {
                'center_us': data['center_us'],
                'points': [{k: p[k] for k in ('pulse_us', 'angle_deg')} for p in data['points']],
                'endpoints': {}}
            validate(profile)
            points = actuator_table(profile, 1500, allow_probe=True)
            if any(b['angle_deg'] <= a['angle_deg'] for a, b in zip(points, points[1:])):
                raise ValueError('参考方向必须为PWM增大时向左转，角度严格递增')
            if (data['endpoints']['right_us'], data['endpoints']['left_us']) != (points[0]['pulse_us'], points[-1]['pulse_us']):
                raise ValueError('参考端点需与插值表两端一致；饱和重复点应单独记录')
            if not any(p['pulse_us'] == 1500 and p['angle_deg'] == 0 for p in points):
                raise ValueError('参考中位必须为1500μs / 0°')
        except (KeyError, TypeError) as exc:
            raise ValueError('参考表字段不完整') from exc
        self._profile = profile
        self._points = tuple((p['pulse_us'], p['angle_deg']) for p in points)
        self._data = copy.deepcopy(data)

    def snapshot(self):
        return copy.deepcopy(self._data)

    def angle_to_pulse_deg(self, angle_deg):
        """Return a rounded reference PWM, without sending or applying it."""
        if not finite(angle_deg):
            raise ValueError('参考转角必须是有限数，单位为度')
        return angle_to_pulse(self._profile, math.radians(angle_deg), 1500, allow_probe=True)

    def pulse_to_angle_deg(self, pulse_us):
        """Estimate only inside the useful reference range; never extrapolate."""
        if not finite(pulse_us) or not self._points[0][0] <= pulse_us <= self._points[-1][0]:
            raise ValueError('PWM超出参考范围；不外推转角')
        for (low, left), (high, right) in zip(self._points, self._points[1:]):
            if low <= pulse_us <= high:
                return left + (pulse_us-low)/(high-low)*(right-left)
        raise ValueError('没有可用参考插值区间')


def load_reference(path=None):
    """Explicitly load this reference or an independently measured replacement."""
    return SteeringReference(json.loads(Path(path if path is not None else DEFAULT_PATH).read_text(encoding='utf-8')))
