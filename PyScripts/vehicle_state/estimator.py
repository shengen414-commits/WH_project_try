"""Small-angle, straight-path Kalman observer with bounded delayed updates.

State [lateral error, heading error, residual gyro bias]. No accelerometer double
integration, no lateral-speed claim, no implicit zero speed when calibration is
missing. All delayed events are replayed in timestamp order, including line
observations after a camera exposure. The model deliberately excludes sideslip.
"""
import bisect
import copy
import math
from .contracts import ImuObservation, SpeedObservation, LineObservation, VisionObservation
from .profile import finite


def transpose(a):
    return list(map(list, zip(*a)))


def multiply(a, b):
    return [[sum(x*y for x, y in zip(row, col)) for col in zip(*b)] for row in a]


class _Core:
    def __init__(self):
        self.t = None
        self.x = [0., 0., 0.]
        self.p = [[.25, 0., 0.], [0., .3**2, 0.], [0., 0., .03**2]]
        self.initialized = False
        self.r = self.v = None
        self.imu_at = self.speed_at = self.anchor_at = self.vision_at = self.line_at = None
        self.gyro_calibrated = False
        self.accepted = self.rejected = 0
        self.last_reason = 'waiting_for_observations'

    def advance(self, stamp):
        if self.t is None:
            self.t = stamp
            return
        dt = stamp-self.t
        if dt <= 0:
            return
        gyro_ok = self.r is not None and self.imu_at is not None and stamp-self.imu_at <= .12 and self.gyro_calibrated
        speed_ok = self.v is not None and self.speed_at is not None and stamp-self.speed_at <= .12
        v = self.v if speed_ok and gyro_ok else 0.
        r = self.r-self.x[2] if gyro_ok else 0.
        self.x[0] += v*self.x[1]*dt + .5*v*r*dt*dt
        self.x[1] += r*dt
        f = [[1., v*dt, -.5*v*dt*dt], [0., 1., -dt if gyro_ok else 0.], [0., 0., 1.]]
        self.p = multiply(multiply(f, self.p), transpose(f))
        for i, q in enumerate((.002**2 if speed_ok and gyro_ok else .15**2,
                                .01**2 if gyro_ok else .3**2, .0005**2)):
            self.p[i][i] += q*dt
        self.t = stamp

    def scalar(self, value, h, variance):
        ph = [sum(self.p[i][j]*h[j] for j in range(3)) for i in range(3)]
        s = variance+sum(h[i]*ph[i] for i in range(3))
        residual = value-sum(h[i]*self.x[i] for i in range(3))
        if not finite(s) or s <= 0 or residual*residual/s > 9:
            self.rejected += 1; self.last_reason = 'line_innovation_rejected'
            return False
        for i in range(3):
            self.x[i] += ph[i]*residual/s
            for j in range(3):
                self.p[i][j] -= ph[i]*ph[j]/s
        self.accepted += 1
        return True

    def apply(self, observation):
        self.advance(observation.time_s)
        if isinstance(observation, ImuObservation):
            self.r = observation.yaw_rate_rad_s
            self.imu_at = observation.time_s
            self.gyro_calibrated = observation.calibrated
        elif isinstance(observation, SpeedObservation):
            self.v, self.speed_at = observation.speed_m_s, observation.time_s
        elif isinstance(observation, LineObservation):
            z = observation.offset_left_m+observation.sensor_y_m
            if not self.initialized:
                self.x[0] = -z
                self.initialized = True
            if self.scalar(z, [-1., -observation.sensor_x_m, 0.], observation.variance_m2):
                self.anchor_at = self.line_at = observation.time_s
                self.last_reason = 'line_updated'
        elif isinstance(observation, VisionObservation):
            # Joint update retains correlation between error and heading from one frame.
            r00, r01, _, r11 = observation.covariance
            s00, s01, s11 = self.p[0][0]+r00, self.p[0][1]+r01, self.p[1][1]+r11
            determinant = s00*s11-s01*s01
            if determinant <= 0:
                return
            inverse = [[s11/determinant, -s01/determinant], [-s01/determinant, s00/determinant]]
            residual = [observation.lateral_error_m-self.x[0], observation.heading_error_rad-self.x[1]]
            nis = sum(residual[i]*inverse[i][j]*residual[j] for i in range(2) for j in range(2))
            if self.initialized and nis > 11.83:
                self.rejected += 1; self.last_reason = 'vision_innovation_rejected'
                return
            if not self.initialized:
                self.x[0], self.x[1] = observation.lateral_error_m, observation.heading_error_rad
            cross = [row[:2] for row in self.p]
            gain = multiply(cross, inverse)
            for i in range(3):
                self.x[i] += sum(gain[i][j]*residual[j] for j in range(2)) if self.initialized else 0
                for j in range(3):
                    self.p[i][j] -= sum(gain[i][k]*cross[j][k] for k in range(2))
            self.initialized = True
            self.accepted += 1
            self.anchor_at = self.vision_at = observation.time_s
            self.last_reason = 'vision_updated'


class StateEstimator:
    def __init__(self):
        self.reset()

    def reset(self):
        self.base, self.core = _Core(), _Core()
        self.events, self.keys, self.snapshots = [], set(), []
        self.dropped = self.duplicates = self.replays = 0

    @staticmethod
    def validate_observation(observation):
        if not isinstance(observation, (ImuObservation, SpeedObservation, LineObservation, VisionObservation)):
            raise ValueError('unsupported observation')
        values = [observation.time_s]
        if isinstance(observation, ImuObservation):
            values += [observation.yaw_rate_rad_s]
            if abs(observation.yaw_rate_rad_s) > math.radians(499):
                raise ValueError('gyro saturated')
        elif isinstance(observation, SpeedObservation):
            values += [observation.speed_m_s]
            if abs(observation.speed_m_s) > 30:
                raise ValueError('speed outside shadow model envelope')
        elif isinstance(observation, LineObservation):
            values += [observation.offset_left_m, observation.sensor_x_m, observation.sensor_y_m, observation.variance_m2]
            if observation.variance_m2 <= 0:
                raise ValueError('line covariance must be positive')
        else:
            values += [observation.lateral_error_m, observation.heading_error_rad, observation.confidence, *observation.covariance]
            if len(observation.covariance) != 4:
                raise ValueError('camera covariance needs 2x2 values')
            a, b, c, d = observation.covariance
            if a <= 0 or d <= 0 or abs(b-c) > 1e-9 or a*d-b*c <= 0 or observation.confidence < .6 or observation.confidence > 1 or abs(observation.heading_error_rad) > .35:
                raise ValueError('camera quality/model envelope/covariance rejected')
        if any(not finite(v) for v in values) or type(observation.sequence) is not int:
            raise ValueError('non-finite observation or invalid sequence')
        return observation

    def submit(self, observation):
        self.validate_observation(observation)
        key = (type(observation).__name__, observation.sequence)
        if key in self.keys:
            self.duplicates += 1
            return False
        newest = self.core.t
        if (newest is not None and observation.time_s < newest-.5) or (self.base.t is not None and observation.time_s <= self.base.t):
            self.dropped += 1
            return False
        times = [event.time_s for event in self.events]
        index = bisect.bisect_right(times, observation.time_s)
        if index < len(self.events):
            self.replays += 1
        self.events.insert(index, observation); self.keys.add(key)
        # Only replay the affected suffix. Normal in-order IMU updates are O(1).
        current = copy.deepcopy(self.snapshots[index-1] if index else self.base)
        snapshots = self.snapshots[:index]
        for event in self.events[index:]:
            current.apply(event)
            snapshots.append(copy.deepcopy(current))
        self.core, self.snapshots = current, snapshots
        trim = max(0, len(self.events)-160)
        while trim < len(self.events)-1 and self.events[trim].time_s < current.t-.6:
            trim += 1
        if trim:
            self.base = self.snapshots[trim-1]
            self.events = self.events[trim:]; self.snapshots = self.snapshots[trim:]
            self.keys = {(type(e).__name__, e.sequence) for e in self.events}
        return True

    def snapshot(self, now):
        core = copy.deepcopy(self.core)
        if core.t is not None and now >= core.t:
            core.advance(now)
        def fresh(stamp, age):
            return stamp is not None and -.02 <= now-stamp <= age
        sources = {'imu': fresh(core.imu_at, .12) and core.gyro_calibrated,
                   'speed': fresh(core.speed_at, .12), 'line': fresh(core.line_at, .25),
                   'vision': fresh(core.vision_at, .25)}
        sigma = [math.sqrt(max(0, core.p[i][i])) for i in range(3)]
        valid = core.initialized and sources['imu'] and sources['speed'] and (sources['line'] or sources['vision']) and sigma[0] < .06 and sigma[1] < .12 and abs(core.x[1]) <= .35
        reason = core.last_reason if valid else ('waiting_for_metric_reference' if not core.initialized else 'stale_uncalibrated_or_uncertain')
        return {'schema': 1, 'model': 'straight_small_angle_no_sideslip', 'time_s': now,
                'reference': 'rear_axle_x_forward_y_left_m',
                'lateral_error_m': core.x[0] if core.initialized else None,
                'heading_error_rad': core.x[1] if core.initialized else None,
                'residual_gyro_bias_rad_s': core.x[2],
                'yaw_rate_rad_s': core.r-core.x[2] if sources['imu'] else None,
                'speed_m_s': core.v if sources['speed'] else None, 'lateral_speed_m_s': None,
                'valid': valid, 'reason': reason, 'sources': sources,
                'covariance': core.p, 'sigma_y_m': sigma[0], 'sigma_heading_rad': sigma[1],
                'accepted_updates': core.accepted, 'rejected_updates': core.rejected,
                'delayed_replays': self.replays, 'dropped_events': self.dropped,
                'duplicates': self.duplicates, 'control_permitted': False}
