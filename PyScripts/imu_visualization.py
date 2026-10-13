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
            self.invalid_frames = 0
            self.consecutive_invalid = 0
            self.last_invalid = None
            self.invalid_at = None
            self.skip_integration = False

    def _reject(self, line, reason, now):
        # Keep the last good orientation/reference. A single bad serial frame
        # must not force a fresh gravity initialization while the module moves.
        self.valid = False
        self.skip_integration = True
        self.invalid_frames += 1
        self.consecutive_invalid += 1
        reason = reason[:240]
        self.last_invalid = dict(reason=reason, raw=line[:256])
        self.invalid_at = now
        self.message = '三维数据帧无效：' + reason

    def invalidate(self, message):
        """A transport failure must invalidate even a recently received pose."""
        with self.lock:
            self.valid = False
            self.last_ms = None
            self.reference = None
            self.message = message

    @staticmethod
    def _decode(line):
        fields = [value.strip() for value in line.split(',')]
        if len(fields) != 11:
            raise ValueError(f'字段数量为 {len(fields)}，期望 11；可能丢字节或两帧粘连')
        if fields[10] not in ('NO', 'RUN', 'OK'):
            raise ValueError(f'标定状态不合法：{fields[10][:24]!r}')
        try:
            ms = int(fields[1])
        except ValueError:
            raise ValueError(f'时间戳不是整数：{fields[1][:24]!r}') from None
        if not 0 <= ms <= 0xffffffff:
            raise ValueError(f'时间戳越界：{ms}')
        values = []
        for name, field in zip(('ax', 'ay', 'az', 'gx', 'gy', 'gz', '温度', '校正 gz'), fields[2:10]):
            try:
                value = float(field)
            except ValueError:
                raise ValueError(f'{name} 不是数字：{field[:24]!r}') from None
            if not math.isfinite(value):
                raise ValueError(f'{name} 为非有限值：{field[:24]!r}')
            limit = 2.01 if name in ('ax', 'ay', 'az') else 501 if name in ('gx', 'gy', 'gz') else 510 if name == '校正 gz' else None
            if limit is not None and abs(value) > limit:
                unit = 'g' if name in ('ax', 'ay', 'az') else '°/s'
                raise ValueError(f'{name}={value:g} {unit} 超出当前固件量程校验 ±{limit:g}；检查帧完整性及固件量程')
            values.append(value)
        return ms, values, fields[10]

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
                ms, values, calibration = self._decode(line)
                ax, ay, az, gx, gy, gz, temperature, corrected_z = values
            except (ValueError, OverflowError) as exc:
                self._reject(line, str(exc), now)
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
            elif not self.skip_integration:
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
            self.skip_integration = False
            self.consecutive_invalid = 0
            self.count += 1
            self.sample = dict(time_ms=ms, acceleration=[ax, ay, az], gyro=[gx, gy, gz],
                               temperature=temperature, corrected_z=corrected_z, calibration=calibration)
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
                        count=self.count, message=self.message, relative=self.reference is not None,
                        diagnostics=dict(invalid_frames=self.invalid_frames, consecutive_invalid=self.consecutive_invalid,
                                         last_invalid=dict(self.last_invalid) if self.last_invalid else None,
                                         last_invalid_age_ms=round((time.monotonic()-self.invalid_at)*1000) if self.invalid_at is not None else None))
