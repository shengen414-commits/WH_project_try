"""Six-axis bench attitude estimate. Shared serial reader is the only producer.

Quaternion maps sensor axes to a Z-up world. Gravity corrects tilt only; heading
is relative and drifts. No velocity, position or steering feedback is produced.
"""
import math
import threading
import time


def multiply(a, b):
    w, x, y, z = a
    v, i, j, k = b
    return [w*v-x*i-y*j-z*k, w*i+x*v+y*k-z*j,
            w*j-x*k+y*v+z*i, w*k+x*j-y*i+z*v]


def conjugate(q):
    return [q[0], -q[1], -q[2], -q[3]]


class ImuVisualization:
    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        with self.lock:
            self.q = [1., 0., 0., 0.]
            self.reference = None
            self.last_ms = None
            self.received = 0.
            self.sample = None
            self.message = '等待 O 模式三维数据；请先烧录新版固件'
            self.valid = False
            self.count = 0

    def feed(self, line, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            if line.startswith('[MPU]'):
                self.message = line
                if 'init ' in line or 'ERROR=' in line or 'sample stale' in line:
                    self.valid = False
                    self.last_ms = None
                    self.reference = None
                return
            if line.startswith(('[ENC]', '[SERVO]', '[LINE]','[LINE_DBG]')):
                self.valid = False
                self.last_ms = None
                self.reference = None
                self.message = 'ESP32 已离开三维测试模式'
                return
            if not line.startswith('[IMU],'):
                return
            try:
                fields = line.split(',')
                if len(fields) != 11 or fields[10] not in ('NO', 'RUN', 'OK'):
                    raise ValueError('frame')
                ms = int(fields[1])
                values = list(map(float, fields[2:10]))
                if not 0 <= ms <= 0xffffffff or not all(math.isfinite(v) for v in values):
                    raise ValueError('number')
                ax, ay, az, gx, gy, gz, temperature, corrected_z = values
                if any(abs(v) > 2.01 for v in values[:3]) or any(abs(v) > 501 for v in (gx, gy, gz)) or abs(corrected_z) > 510:
                    raise ValueError('range')
            except (ValueError, OverflowError):
                self.valid = False
                self.last_ms = None
                self.message = '三维数据帧无效'
                return
            delta = None if self.last_ms is None else (ms - self.last_ms) & 0xffffffff
            if delta == 0:
                return  # Repeated device timestamps must never be integrated twice.
            norm = math.sqrt(ax*ax + ay*ay + az*az)
            if delta is None or delta > 200 or now - self.received > .5:
                if not .85 <= norm <= 1.15:
                    self.valid = False
                    self.last_ms = None
                    self.message = '等待静止或平稳姿态以初始化重力方向'
                    return
                roll = math.atan2(ay, az)
                pitch = math.atan2(-ax, math.hypot(ay, az))
                cr, sr = math.cos(roll/2), math.sin(roll/2)
                cp, sp = math.cos(pitch/2), math.sin(pitch/2)
                self.q = [cr*cp, sr*cp, cr*sp, -sr*sp]
                self.reference = None  # No integration across a dropout/restart.
            else:
                wx, wy, wz = map(math.radians, (gx, gy, corrected_z))
                if .85 <= norm <= 1.15:
                    # Proportional gravity correction in sensor coordinates.
                    _, vx, vy, vz = multiply(multiply(conjugate(self.q), [0, 0, 0, 1]), self.q)
                    a, b, c = ax/norm, ay/norm, az/norm
                    wx += 2*(b*vz-c*vy)
                    wy += 2*(c*vx-a*vz)
                    wz += 2*(a*vy-b*vx)
                derivative = multiply(self.q, [0, wx, wy, wz])
                q = [v + d * delta/2000 for v, d in zip(self.q, derivative)]
                length = math.sqrt(sum(v*v for v in q))
                self.q = [v/length for v in q]
            self.last_ms, self.received, self.valid = ms, now, True
            self.count += 1
            self.sample = dict(time_ms=ms, acceleration=[ax, ay, az], gyro=[gx, gy, gz],
                               temperature=temperature, corrected_z=corrected_z, calibration=fields[10])
            self.message = '三维数据接收中'

    def zero(self):
        with self.lock:
            if not self.valid or time.monotonic() - self.received > .5:
                return False
            self.reference = conjugate(self.q)
            return True

    def status(self):
        with self.lock:
            q = multiply(self.reference, self.q) if self.reference else list(self.q)
            w, x, y, z = q
            angles = [math.atan2(2*(w*x+y*z), 1-2*(x*x+y*y)),
                      math.asin(max(-1., min(1., 2*(w*y-z*x)))),
                      math.atan2(2*(w*z+x*y), 1-2*(y*y+z*z))]
            age = time.monotonic() - self.received if self.received else None
            return dict(fresh=self.valid and age is not None and age < .5,
                        age_ms=round(age*1000) if age is not None else None,
                        quaternion=q, angles=list(map(math.degrees, angles)), sample=self.sample,
                        count=self.count, message=self.message, relative=self.reference is not None)
