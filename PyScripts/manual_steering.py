"""Track manual servo commands against firmware replies, never inferred wheel angles."""
import re
import secrets
import threading
import time


class ManualSteering:
    def __init__(self, clock=time.monotonic, timeout=3.0):
        self.clock, self.timeout = clock, timeout
        self.lock = threading.RLock()
        self.next_id = secrets.randbelow(0x7fffffff) + 1
        self.active = self.owned = self.ready = self.precise = self.calibration_supported = False
        self.probe_supported = self.range_supported = False
        self.received = None
        self.target = None
        self.low, self.center, self.high = 1100, 1500, 1900
        self.request = {'status': 'idle'}

    def blocking(self):
        with self.lock:
            return self.owned or self.active

    def snapshot(self):
        with self.lock:
            if self.request['status'] == 'pending' and self.clock() - self.request['sent_at'] >= self.timeout:
                self.request.update(status='timeout', message='3秒未收到舵机命令确认。请烧录新版 esp32dev 固件并检查串口；可点击归中退出重试。')
            fresh = self.received is not None and self.clock() - self.received < 1.0
            return {'active': self.active, 'blocking': self.owned or self.active,
                    'fresh': fresh, 'ready': self.ready, 'precise': self.precise,
                    'calibration_supported': self.calibration_supported,
                    'probe_supported': self.probe_supported,
                    'range_supported': self.range_supported,
                    'center_min_us': 1500, 'center_max_us': 1500,
                    'target_us': self.target, 'low_us': self.low, 'center_us': self.center,
                    'high_us': self.high, 'request': dict(self.request)}

    def begin(self, action, pulse=None, limits=None):
        with self.lock:
            state = self.snapshot()
            if action != 'stop' and state['request']['status'] == 'pending':
                raise ValueError('上一条舵机命令尚未确认，请稍后再试')
            if action == 'start' and state['blocking']:
                raise ValueError('手动模式尚未退出，请先归中退出')
            if action not in ('start', 'stop', 'set', 'center', 'range'):
                raise ValueError('中位固定1500μs；请通过上下限选择调整范围')
            if action in ('set', 'center', 'range') and not (self.active and state['fresh'] and self.ready and self.precise):
                raise ValueError('请先进入手动测试，等待新版固件确认就绪')
            if action in ('set', 'center', 'range') and not self.range_supported:
                raise ValueError('固定1100～1900μs范围需要重新烧录新版 esp32dev 固件')
            if action == 'range':
                if not self.valid_range(limits):
                    raise ValueError('手动调试范围固定为1100～1900μs')
                if self.target is None or not limits[0] <= self.target <= limits[1]:
                    raise ValueError('当前输出在新范围外，请先回中后再调整范围')
            if action == 'set' and (type(pulse) is not int or not self.low <= pulse <= self.high):
                raise ValueError(f'舵机脉宽需为 {self.low}～{self.high}μs 的整数')
            self.next_id = (self.next_id + 1) & 0xffffffff or 1
            self.request = {'id': self.next_id, 'action': action, 'target_us': pulse,
                            'limits': limits, 'previous_target_us': self.target,
                            'status': 'pending', 'sent_at': self.clock(),
                            'message': '命令已发送，等待 ESP32 确认（最多3秒）'}
            self.owned = True
            value = 1 if action == 'stop' else pulse if action == 'set' else 0
            packet = f'S{value:04d},{self.next_id}\n'.encode('ascii')
            if action == 'range':
                packet = f'S{limits[0]:04d}:{limits[1]:04d},{self.next_id}\n'.encode('ascii')
            if action == 'start':
                packet = b'V\n' + packet
            elif action == 'stop':
                packet = b'C\nR\n' + packet  # Existing firmware can still center and exit.
            return self.next_id, packet

    def fail(self, request_id, message):
        with self.lock:
            if self.request.get('id') == request_id:
                self.request.update(status='error', message=message)

    def invalidate(self, message):
        with self.lock:
            self.received = None
            self.ready = False
            if self.owned or self.active:
                self.request.update(status='error', message=message)

    def normal_frame(self):
        with self.lock:
            # A queued normal sample must not confirm entry into servo mode.
            if self.request.get('action') == 'start' and self.request['status'] == 'pending':
                return
            was_manual = self.owned or self.active
            self.active = self.owned = False
            self.ready = False
            if self.request.get('action') == 'stop' and self.request['status'] in ('pending', 'timeout'):
                self.target = self.center
                self.request.update(status='confirmed', message='已恢复正常编码器上报；手动测试已退出')
            elif was_manual and self.request['status'] == 'pending':
                self.request.update(status='rejected', message='固件已离开手动模式，请重新进入后调整')

    @staticmethod
    def valid_range(limits):
        return (isinstance(limits, (tuple, list)) and len(limits) == 2 and
                all(type(value) is int for value in limits) and tuple(limits) == (1100, 1900))

    @staticmethod
    def _limits(fields):
        low, mid, high = (int(fields[key]) for key in ('low', 'mid', 'high'))
        if not 500 <= low < mid < high <= 2500:
            raise ValueError('invalid servo limits')
        return low, mid, high

    def feed(self, line):
        if not (line.startswith('[SERVO_ACK] ') or re.match(r'^\[SERVO\] GPIO\d+ target=', line)):
            return False
        fields = dict(re.findall(r'(\w+)=([^\s]+)', line))
        try:
            target = int(fields['target'])
            if not 500 <= target <= 2500:
                return False
            if line.startswith('[SERVO_ACK] '):
                request_id, ok, manual, ready = (int(fields[key]) for key in ('id', 'ok', 'manual', 'ready'))
                if not 0 < request_id <= 0xffffffff or any(value not in (0, 1) for value in (ok, manual, ready)):
                    return False
                limits = self._limits(fields)
                if not limits[0] <= target <= limits[2]:
                    return False
                version = int(fields.get('proto', '1'))
                if version >= 4 and limits[1] != 1500:
                    return False
                with self.lock:
                    if request_id != self.request.get('id') or self.request['status'] not in ('pending', 'timeout'):
                        return False
                    action = self.request['action']
                    if ok and (manual != (action != 'stop') or
                               (action != 'stop' and not ready) or
                               (action == 'set' and target != self.request['target_us'])):
                        return False
                    if ok and action == 'range' and (version < 4 or
                            target != self.request['previous_target_us'] or
                            limits != (self.request['limits'][0], 1500, self.request['limits'][1])):
                        return False
                    if ok and action in ('start', 'center', 'stop') and target != limits[1]:
                        return False
                    self.active = bool(manual)
                    self.owned = bool(manual)
                    self.ready, self.precise = bool(ready), True
                    self.calibration_supported = False
                    self.probe_supported = False
                    self.range_supported = version >= 4 and limits == (1100, 1500, 1900)
                    self.target, self.received = target, self.clock()
                    self.low, self.center, self.high = limits
                    reasons = {'pwm_failed': '舵机 PWM 初始化失败', 'out_of_range': '超出固件行程范围',
                               'not_manual': '固件不在手动模式，请重新进入',
                               'fixed_center': '中位固定1500μs', 'range_required': '请通过上下限选择调整范围',
                               'range_invalid': '范围无效或当前输出在新范围外，请先回中再调整'}
                    message = ('已归中并退出手动测试' if action == 'stop' else
                               f'范围已应用：{limits[0]}～{limits[2]}μs，中位1500μs；舵机未移动' if action == 'range' else
                               f'ESP32 已确认输出 {target}μs') if ok else reasons.get(fields.get('reason'), '固件拒绝了舵机命令')
                    self.request.update(status='confirmed' if ok else 'rejected', message=message, confirmed_at=self.clock())
                return True
            ready = fields['PWM'] == 'ready'
            limits = self._limits(fields) if 'low' in fields else (1450, 1500, 1550)
            version = int(fields.get('proto', '0'))
            if not limits[0] <= target <= limits[2] or (version >= 4 and limits[1] != 1500):
                return False
            with self.lock:
                self.active = self.owned = True
                self.ready, self.precise = ready, version >= 1
                self.calibration_supported = False
                self.probe_supported = False
                self.range_supported = version >= 4 and limits == (1100, 1500, 1900)
                self.target, self.received = target, self.clock()
                self.low, self.center, self.high = limits
            return True
        except (KeyError, ValueError):
            return False
