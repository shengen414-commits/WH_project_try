"""Measured geometry and actuator calibration; unknown values stay unknown."""
import copy
import json
import math
import os
from pathlib import Path
import threading


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def defaults():
    return {'schema': 1, 'revision': 0, 'geometry': {
        'line_pitch_m': None, 'line_x_m': None, 'line_y_m': None,
        'meters_per_count': None, 'encoder_sign': None, 'wheelbase_m': None,
        # User confirmed the sensor Z axis points down. X/Y remain unverified.
        'imu_yaw_sign': -1, 'imu_axes': None},
        'camera': None, 'actuator': {'center_us': None, 'points': []}}


def validate(profile):
    if not isinstance(profile, dict) or set(profile) != set(defaults()):
        raise ValueError('标定文件字段不完整或版本不支持')
    if profile['schema'] != 1 or type(profile['revision']) is not int or profile['revision'] < 0:
        raise ValueError('标定版本无效')
    g = profile['geometry']
    if not isinstance(g, dict) or set(g) != set(defaults()['geometry']):
        raise ValueError('几何参数字段无效')
    ranges = {'line_pitch_m': (.001, .1), 'line_x_m': (-2, 3), 'line_y_m': (-1, 1),
              'meters_per_count': (1e-7, 1), 'wheelbase_m': (.05, 3)}
    for key, (low, high) in ranges.items():
        v = g[key]
        if v is not None and (not finite(v) or not low <= v <= high):
            raise ValueError(f'{key} 超出允许范围，单位为米；未知请留空')
    if type(g['imu_yaw_sign']) is not int or g['imu_yaw_sign'] not in (-1, 1):
        raise ValueError('Z 朝下使用 -1；Z 朝上使用 +1')
    if g['encoder_sign'] is not None and (type(g['encoder_sign']) is not int or g['encoder_sign'] not in (-1, 1)):
        raise ValueError('编码器方向未知留空；手推向前计数增加填+1，减少填-1')
    axes = g['imu_axes']
    if axes is not None:
        if not isinstance(axes, list) or len(axes) != 3 or any(type(a) is not int for a in axes) or sorted(map(abs, axes)) != [1, 2, 3]:
            raise ValueError('安装轴映射应为带符号的 1/2/3 排列')
        inversions = sum(abs(axes[i]) > abs(axes[j]) for i in range(3) for j in range(i+1, 3))
        if (-1)**inversions * math.prod(1 if a > 0 else -1 for a in axes) != 1:
            raise ValueError('安装轴必须保持右手坐标；不能只翻转单轴')
        if axes[2] != 3*g['imu_yaw_sign']:
            raise ValueError('当前偏航接口要求传感器 Z 轴竖直，轴映射与 Z 方向必须一致')
    a = profile['actuator']
    if not isinstance(a, dict) or set(a) != {'center_us', 'points'} or not isinstance(a['points'], list) or len(a['points']) > 32:
        raise ValueError('舵机标定表无效')
    if a['center_us'] is not None and (type(a['center_us']) is not int or not 1300 <= a['center_us'] <= 1700):
        raise ValueError('中位必须来自固件确认')
    pulses = set()
    for p in a['points']:
        if not isinstance(p, dict) or set(p) != {'pulse_us', 'angle_deg'} or type(p['pulse_us']) is not int or not 1000 <= p['pulse_us'] <= 2000 or not finite(p['angle_deg']) or abs(p['angle_deg']) > 45:
            raise ValueError('每点需要确认的脉宽和人工测得的前轮角度（左正右负，±45°内）')
        if p['pulse_us'] in pulses:
            raise ValueError('脉宽重复')
        pulses.add(p['pulse_us'])
    c = profile['camera']
    if c is not None:
        from .vision import validate_camera
        validate_camera(c)
    json.dumps(profile, allow_nan=False)
    return profile


def actuator_table(profile, actual_center=None):
    a = profile['actuator']
    if a['center_us'] is None or actual_center is None or a['center_us'] != actual_center:
        raise ValueError('标定中位未确认或已变化，请重新标定转角')
    points = sorted(a['points'], key=lambda p: p['pulse_us'])
    if len(points) < 3:
        raise ValueError('至少需要左、直行、右三个实测点，建议每侧再增加一点')
    center = [p for p in points if p['pulse_us'] == actual_center and abs(p['angle_deg']) <= .5]
    angles = [p['angle_deg'] for p in points]
    differences = [b-a for a, b in zip(angles, angles[1:])]
    if not center or min(angles) > -3 or max(angles) < 3 or not (all(d > .1 for d in differences) or all(d < -.1 for d in differences)):
        raise ValueError('需要中位约0°、左右各至少3°，且角度随脉宽严格单调')
    if any(not actual_center-300 <= p['pulse_us'] <= actual_center+300 for p in points):
        raise ValueError('标定点超出当前中位±300μs')
    return points


def angle_to_pulse(profile, angle_rad, actual_center):
    """Future actuator adapter: interpolation only; never energizes hardware."""
    if not finite(angle_rad):
        raise ValueError('转角必须有限')
    points = sorted(actuator_table(profile, actual_center), key=lambda p: p['angle_deg'])
    angle = math.degrees(angle_rad)
    if not points[0]['angle_deg'] <= angle <= points[-1]['angle_deg']:
        raise ValueError('目标超出实测转角范围；禁止外推')
    for left, right in zip(points, points[1:]):
        if left['angle_deg'] <= angle <= right['angle_deg']:
            f = (angle-left['angle_deg'])/(right['angle_deg']-left['angle_deg'])
            return round(left['pulse_us']+f*(right['pulse_us']-left['pulse_us']))
    raise ValueError('没有可用插值区间')


class ProfileStore:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.write_lock = threading.Lock()
        self.profile, self.error = defaults(), None
        if self.path.exists():
            try:
                self.profile = validate(json.loads(self.path.read_text(encoding='utf-8')))
            except (ValueError, TypeError, OSError) as exc:
                self.error = f'标定文件未载入：{exc}'

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.profile)

    def save(self, profile):
        # Serialize writers, but never hold the snapshot lock across filesystem I/O.
        with self.write_lock:
            candidate = copy.deepcopy(validate(profile))
            with self.lock:
                candidate['revision'] = self.profile['revision']+1
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            try:
                with temporary.open('w', encoding='utf-8') as file:
                    json.dump(candidate, file, ensure_ascii=False, indent=2, allow_nan=False)
                    file.flush(); os.fsync(file.fileno())
                os.replace(temporary, self.path)
            finally:
                if temporary.exists():
                    temporary.unlink()
            with self.lock:
                self.profile, self.error = candidate, None
            return self.snapshot()
