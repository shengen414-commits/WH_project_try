"""Single-UART shadow-lab coordinator. No automatic actuator/throttle output."""
from collections import deque
from dataclasses import asdict
from datetime import datetime
import copy
import math
import queue
import threading
import time
from .clock import DeviceClock
from .contracts import ImuObservation, SpeedObservation, LineObservation, VisionObservation
from .estimator import StateEstimator
from .profile import finite, actuator_table
from .recording import TraceWriter


class VehicleLab:
    def __init__(self, store, records, write, camera=None, clock=time.monotonic, probe=None):
        self.store, self.records, self.write, self.camera, self.clock = store, records, write, camera, clock
        self.lock = threading.RLock()
        self.owned = self.active = False
        self.request = {'status': 'idle'}
        self.timer, self.estimator = DeviceClock(), StateEstimator()
        self.raw, self.vision = {}, {'valid': False, 'reason': '未启动'}
        self.invalid_frames = 0
        self.last_received = None
        self.stop_event = threading.Event()
        self.trace = None
        self.worker = None
        self.counts = deque()
        self.previous_seq = None
        self.sequence_gaps = 0
        self.last_probe = -100.
        self.jpeg = None
        self.zero_running = False
        self.probe = probe
        self.inbox = queue.Queue(64)
        self.receiver = self.probe_worker = None
        self.shutdown_event = threading.Event()
        self.session = 0
        self.inbox_drops = self.inbox_stale = self.inbox_errors = 0
        self.queue_delay_ms = self.max_queue_delay_ms = self.processing_ms = self.max_processing_ms = 0.
        self.vision_processing_ms = self.preview_encoding_ms = 0.
        self.processing_started = None
        self.channels = {'line':True,'imu':True,'camera':True}
        self.input_gap = False
        self._status_cache = None
        self.status()

    def blocking(self):
        # Safety ownership must remain readable while an estimator update runs.
        return self.owned

    def set_channels(self, channels):
        if not isinstance(channels,dict) or set(channels)!=set(self.channels) or any(type(v) is not bool for v in channels.values()):
            raise ValueError('line、imu、camera 开关必须为布尔值')
        with self.lock:
            self.channels = dict(channels)
            self.session += 1
            self.estimator.reset(); self.counts.clear()
            if not channels['camera']:
                self.jpeg = None
                self.vision = {'valid':False,'reason':'相机识别调试已关闭'}
            if not channels['imu']: self.raw.pop('imu',None)
            if not channels['line']: self.raw.pop('line',None)
            if self.trace: self.trace.submit('channels',channels)

    def command(self, action):
        if action == 'stop':
            # Do not wait for a slow estimator or filesystem to send R.
            self.request = {'action': action, 'sent_at': self.clock(), 'status': 'pending'}
            self.stop_event.set()
            if self.trace:
                self.trace.close()
            try:
                self.write(b'R\n')
            except Exception:
                self.request.update(status='error')
                raise
            return
        with self.lock:
            if action != 'stop' and self.request.get('status') == 'pending':
                raise ValueError('上一条观察命令尚未确认')
            if action == 'start':
                if self.owned or self.worker is not None and self.worker.is_alive():
                    raise ValueError('请先退出上一轮联合观察，再重新启动')
                if self.trace is not None and not self.trace.done.is_set():
                    raise ValueError('上一轮记录仍在收尾，请稍后重试')
                self.timer, self.estimator = DeviceClock(), StateEstimator()
                self.raw, self.vision, self.counts = {}, {'valid': False, 'reason': '等待相机'}, deque()
                self.previous_seq = None
                self.sequence_gaps = 0
                self.session += 1
                self.trace = TraceWriter(self.records / f"state_{datetime.now():%Y%m%d_%H%M%S_%f}.jsonl", self.store.snapshot())
                self.stop_event = threading.Event()
                self.owned = True
                packet = b'U\n'+self.timer.probe(self.clock())
            elif action == 'zero':
                if not self.active or self.last_received is None or self.clock()-self.last_received > .7:
                    raise ValueError('先启动联合观察并等待固件确认')
                packet = b'Z\n'
                self.estimator.reset()
                self.zero_running = False
            else:
                raise ValueError('无效观察操作')
            self.request = {'action': action, 'sent_at': self.clock(), 'status': 'pending'}
        # UART contention is outside the estimator lock.
        try:
            self.write(packet)
        except Exception:
            self.request['status'] = 'error'
            self.stop_event.set()
            if self.trace:
                self.trace.close()
            raise
        if action == 'start':
            with self.lock:
                self.last_probe = self.clock()
                self._ensure_receiver()
                self.worker = threading.Thread(target=self._work, name='vehicle-state-shadow', daemon=True)
                self.worker.start()
                self.probe_worker = threading.Thread(target=self._sync_work, name='vehicle-state-clock', daemon=True)
                self.probe_worker.start()

    def _ensure_receiver(self):
        if self.receiver is None or not self.receiver.is_alive():
            self.receiver = threading.Thread(target=self._receive_work, name='vehicle-state-input', daemon=True)
            self.receiver.start()

    def enqueue(self, line, received):
        """UART reader boundary: no estimation, disk access, or estimator-lock wait."""
        if not self.owned or not line.startswith(('[ENC],', '[LAB],', '[OBS],', '[SYNC],', '[IMU],', '[LAB_MSG] ')):
            return False
        if len(line) > 512:
            self.inbox_drops += 1; self.input_gap = True
            return False
        return self._put(('uart', line, received, self.session))

    def _put(self, item):
        try:
            self.inbox.put_nowait(item)
            return True
        except queue.Full:
            try:
                self.inbox.get_nowait()
            except queue.Empty:
                pass
            self.inbox_drops += 1; self.input_gap = True
            try:
                self.inbox.put_nowait(item)
                return True
            except queue.Full:
                self.inbox_drops += 1
                return False

    def queue_vision(self, observation):
        if not isinstance(observation, VisionObservation) or not self.channels['camera'] or not self.active or self.stop_event.is_set() or not self.timer.status(self.clock())['valid'] or not -.02 <= self.clock()-observation.time_s <= .5:
            raise ValueError('视觉观测过期、观察未启动或类型错误')
        # Validate the public contract before returning a queued HTTP acknowledgement.
        StateEstimator.validate_observation(observation)
        return self._put(('vision', observation, self.clock(), self.session))

    def _receive_work(self):
        last_log = -100.
        while not self.shutdown_event.is_set():
            try:
                kind, data, received, session = self.inbox.get(timeout=.03)
            except queue.Empty:
                continue
            delay = max(0., self.clock()-received)
            self.queue_delay_ms = delay*1000
            self.max_queue_delay_ms = max(self.max_queue_delay_ms, self.queue_delay_ms)
            if session != self.session or delay > .1:
                self.inbox_stale += 1; self.input_gap = True
                continue
            started = time.perf_counter()
            self.processing_started = started
            try:
                with self.lock:
                    if session != self.session or self.clock()-received > .1:
                        self.inbox_stale += 1; self.input_gap = True
                        continue
                    if self.input_gap:
                        self.estimator.reset(); self.counts.clear(); self.input_gap = False
                    if kind == 'uart':
                        self.feed(data, received)
                    else:
                        self.submit_vision(data)
                    now = self.clock()
                    if self.trace and now-last_log >= .1:
                        self.trace.submit('state', {**self.estimator.snapshot(now), 'latency': self.latency_status()})
                        last_log = now
            except Exception as exc:
                self.inbox_errors += 1
                self.raw['last_error'] = str(exc)
                self.input_gap = True
            finally:
                self.processing_ms = (time.perf_counter()-started)*1000
                self.max_processing_ms = max(self.max_processing_ms, self.processing_ms)
                self.processing_started = None

    def _sync_work(self):
        stop_event = self.stop_event
        while not stop_event.wait(1):
            try:
                timer = self.timer
                if self.probe is not None:
                    self.probe(timer)
                else:
                    self.write(timer.probe(self.clock()))
            except Exception as exc:
                self.active = False
                self.request.update(status='error', reason=str(exc))
                self.stop_event.set()

    def shutdown(self, timeout=1):
        self.shutdown_event.set(); self.stop_event.set()
        if self.trace:
            self.trace.close()
        deadline = time.monotonic()+timeout
        for worker in (self.receiver, self.worker, self.probe_worker):
            if worker is not None and worker.is_alive():
                worker.join(max(0, deadline-time.monotonic()))

    def invalidate(self, reason):
        self.session += 1
        self.active = False
        self.last_received = None
        self.request.update(status='error', reason=reason)
        self.input_gap = True
        self.stop_event.set()
        if self.trace:
            self.trace.close()
        if self.lock.acquire(blocking=False):
            try:
                self.estimator.reset()
            finally:
                self.lock.release()

    def _submit(self, observation):
        if isinstance(observation,ImuObservation) and not self.channels['imu'] or isinstance(observation,LineObservation) and not self.channels['line'] or isinstance(observation,VisionObservation) and not self.channels['camera']:
            return
        if self.estimator.submit(observation) and self.trace:
            self.trace.submit('observation', {'type': type(observation).__name__, **asdict(observation)})

    def submit_vision(self, observation):
        """Future vision/fusion injection interface, shared by live capture and replay."""
        if not isinstance(observation, VisionObservation):
            raise ValueError('需要 SI 单位和单调时钟的 VisionObservation')
        with self.lock:
            if not self.active or not self.timer.status(self.clock())['valid']:
                raise ValueError('观察或时钟同步尚未就绪')
            if not -.02 <= self.clock()-observation.time_s <= .5:
                raise ValueError('视觉观测过期或来自未来')
            self._submit(observation)

    def feed(self, line, received=None):
        now = self.clock() if received is None else received
        with self.lock:
            if line.startswith('[ENC],'):
                if self.owned and not (self.request.get('action') == 'start' and self.request['status'] in ('pending', 'timeout')):
                    self.active = self.owned = False
                    self.request.update(status='confirmed', reason='已退出联合观察')
                    self.stop_event.set()
                    if self.trace:
                        self.trace.close()
                return False
            if not line.startswith(('[LAB],', '[OBS],', '[SYNC],', '[IMU],', '[LAB_MSG] ')) or not self.owned:
                return False
            try:
                if self.trace:
                    self.trace.submit('uart', {'received_s': now, 'line': line})
                if line.startswith('[LAB_MSG] '):
                    self.raw['driver_message'] = line[len('[LAB_MSG] '):]
                    if self.request.get('action') == 'zero' and ('ZERO rejected' in line or 'ZERO unavailable' in line):
                        self.request.update(status='rejected', reason=self.raw['driver_message'])
                    return True
                f = line.split(',')
                epoch = self.timer.epoch
                if f[0] == '[SYNC]':
                    if len(f) != 3:
                        raise ValueError('SYNC字段数错误')
                    self.timer.reply(int(f[1]), int(f[2]), now)
                    if self.timer.epoch != epoch:
                        self.estimator.reset(); self.counts.clear()
                    return True
                if f[0] == '[LAB]':
                    if len(f) != 4 or any(v not in ('0', '1') for v in f[2:]):
                        raise ValueError('LAB字段错误')
                    self.timer.unwrap(int(f[1]))
                    if self.timer.epoch != epoch:
                        self.estimator.reset(); self.counts.clear()
                    self.last_received = now
                    if self.request.get('action') != 'stop':
                        self.active = True
                    self.raw.update(imu_ready=f[2] == '1', calibrating=f[3] == '1')
                    self.zero_running |= f[3] == '1'
                    if self.request.get('action') == 'start' or self.request.get('action') == 'zero' and self.zero_running and f[3] == '0':
                        self.request.update(status='confirmed')
                    return True
                if f[0] == '[IMU]':
                    if not self.channels['imu']: return True
                    if len(f) != 11 or f[-1] not in ('NO', 'RUN', 'OK'):
                        raise ValueError('IMU字段错误')
                    ms = int(f[1]); values = list(map(float, f[2:10]))
                    if any(not finite(v) for v in values) or any(abs(v) >= 1.999 for v in values[:3]) or any(abs(v) >= 499 for v in values[3:6]):
                        raise ValueError('IMU数值无效或已饱和')
                    stamp = self.timer.stamp(ms, now)
                    self.raw['imu'] = {'device_ms': ms, 'accel_sensor_g': values[:3], 'gyro_sensor_dps': values[3:6],
                                       'temperature_c': values[6], 'corrected_gz_dps': values[7], 'calibration': f[-1],
                                       'received_s': now}
                    g = self.store.snapshot()['geometry']
                    axes = g['imu_axes']
                    # Specific force still includes gravity. Unconfirmed axes stay None.
                    self.raw['imu']['specific_force_body_m_s2'] = [values[abs(a)-1]*(1 if a > 0 else -1)*9.80665 for a in axes] if axes else None
                    self.raw['imu']['gravity_removed'] = False
                    self.zero_running |= f[-1] == 'RUN'
                    if stamp is not None:
                        self._submit(ImuObservation(stamp, self.timer.unwrap(ms), math.radians(values[7])*g['imu_yaw_sign'], f[-1] == 'OK'))
                    return True
                if len(f) != 11:
                    raise ValueError('OBS字段数错误')
                seq, ms, count, raw, mask, line_ms, pwm, center, low, high = map(int, f[1:])
                if not 0 <= seq <= 0xffffffff or not -0x80000000 <= count <= 0x7fffffff or not -1 <= raw <= 255 or not -1 <= mask <= 255 or not 1000 <= low < center < high <= 2000 or not low <= pwm <= high:
                    raise ValueError('OBS范围错误')
                if self.previous_seq is not None:
                    delta = (seq-self.previous_seq) & 0xffffffff
                    if delta == 0:
                        return True
                    if delta < 0x80000000:
                        self.sequence_gaps += delta-1
                    else:
                        self.estimator.reset(); self.counts.clear()
                        new_epoch = self.timer.epoch+1
                        self.timer = DeviceClock(); self.timer.epoch = new_epoch
                self.previous_seq = seq
                stamp = self.timer.stamp(ms, now)
                if self.timer.epoch != epoch:
                    self.estimator.reset(); self.counts.clear()
                g = self.store.snapshot()['geometry']
                self.raw['servo'] = {'target_us': pwm, 'center_us': center, 'low_us': low, 'high_us': high}
                line_reason = 'valid'
                bits = [i for i in range(8) if mask >= 0 and mask & (1 << i)]
                if mask < 0: line_reason = 'stale'
                elif not bits: line_reason = 'lost'
                elif len(bits) == 8: line_reason = 'all_active'
                elif bits[-1]-bits[0]+1 != len(bits): line_reason = 'multiple_segments'
                elif len(bits)>2: line_reason = 'too_wide'
                channel = sum(bits)/len(bits) if bits and line_reason == 'valid' else None
                self.raw['line'] = {'raw_mask': raw, 'mask': mask, 'center_channel': channel, 'reason': line_reason, 'device_ms': line_ms}
                if not self.channels['line']:
                    self.raw['line'] = {'mask':-1,'reason':'disabled','center_channel':None}
                if stamp is not None:
                    if self.counts and (stamp <= self.counts[-1][0] or stamp-self.counts[-1][0] > .2):
                        self.counts.clear()
                    self.counts.append((stamp, count))
                    while len(self.counts) > 2 and stamp-self.counts[1][0] >= .08:
                        self.counts.popleft()
                    self.raw['encoder_count'] = count
                    if len(self.counts) >= 2 and g['meters_per_count'] is not None and g['encoder_sign'] is not None:
                        start, old = self.counts[0]
                        dt = stamp-start
                        if dt >= .04:
                            dp = ((count-old+0x80000000) & 0xffffffff)-0x80000000
                            self._submit(SpeedObservation((start+stamp)/2, seq, dp*g['meters_per_count']*g['encoder_sign']/dt))
                    line_stamp = self.timer.stamp(line_ms, now)
                    if channel is not None and line_stamp is not None and -.02 <= stamp-line_stamp <= .1 and all(g[k] is not None for k in ('line_pitch_m', 'line_x_m', 'line_y_m')):
                        pitch = g['line_pitch_m']
                        self._submit(LineObservation(line_stamp, self.timer.unwrap(line_ms), (3.5-channel)*pitch, g['line_x_m'], g['line_y_m'], max((pitch/2)**2, .002**2)))
                return True
            except (ValueError, TypeError, OverflowError) as exc:
                self.invalid_frames += 1
                self.raw['last_error'] = str(exc)
                return False

    def status(self, actual_center=None):
        if self.lock.acquire(blocking=False):
            try:
                result = self._status_locked(actual_center)
                self._status_cache = copy.deepcopy(result)
                return result
            finally:
                self.lock.release()
        result = copy.deepcopy(self._status_cache)
        result.update(active=self.active, blocking=self.owned, busy=True, request=dict(self.request))
        now = self.clock()
        result['fresh'] = self.active and self.last_received is not None and now-self.last_received <= .7
        result['clock'] = self.timer.status(now)
        if 'frame_read_time_s' in result['vision']:
            result['vision']['frame_age_ms'] = (now-result['vision']['frame_read_time_s'])*1000
            result['vision']['valid'] = False
        result['state']['valid'] = False
        result['state']['reason'] = 'estimator_busy'
        result['latency'] = self.latency_status()
        result['record'] = self.trace.status() if self.trace else {'path':None,'done':True,'drops':0,'error':None}
        return result

    def latency_status(self):
        return {'input_queue_depth':self.inbox.qsize(), 'input_queue_capacity':self.inbox.maxsize,
                'input_drops':self.inbox_drops, 'stale_inputs':self.inbox_stale, 'processing_errors':self.inbox_errors,
                'queue_delay_ms':self.queue_delay_ms, 'max_queue_delay_ms':self.max_queue_delay_ms,
                'processing_ms':self.processing_ms, 'max_processing_ms':self.max_processing_ms,
                'processing_in_progress_ms':(time.perf_counter()-self.processing_started)*1000 if self.processing_started is not None else 0,
                'vision_processing_ms':self.vision_processing_ms, 'preview_encoding_ms':self.preview_encoding_ms}

    def _status_locked(self, actual_center=None):
        with self.lock:
            now = self.clock()
            if self.request.get('status') == 'pending' and now-self.request['sent_at'] > 3:
                self.request.update(status='timeout', reason='3秒未收到固件确认，请烧录新版固件或退出重试')
            profile = self.store.snapshot()
            missing = [key for key in ('line_pitch_m', 'line_x_m', 'line_y_m', 'meters_per_count', 'encoder_sign', 'wheelbase_m') if profile['geometry'][key] is None]
            state = self.estimator.snapshot(now)
            state['profile_revision'] = profile['revision']
            state['clock_uncertainty_ms'] = self.timer.status(now)['uncertainty_ms']
            fresh = self.active and self.last_received is not None and now-self.last_received <= .7
            if self.input_gap or not fresh or not self.raw.get('imu_ready', False) or not self.timer.status(now)['valid'] or self.store.error:
                state['valid'] = False
            try:
                actuator_table(profile, actual_center if actual_center is not None else self.raw.get('servo', {}).get('center_us') if fresh else None)
                actuator_ready, actuator_reason = True, '标定表可供后续插值；无实测角度反馈'
            except ValueError as exc:
                actuator_ready, actuator_reason = False, str(exc)
            vision_state = dict(self.vision)
            if 'frame_read_time_s' in vision_state:
                vision_state['frame_age_ms'] = (now-vision_state['frame_read_time_s'])*1000
                if vision_state['frame_age_ms'] > 250:
                    vision_state.update(valid=False, reason='相机帧已过期')
            return {'active': self.active, 'blocking': self.owned, 'fresh': fresh, 'channels':dict(self.channels),
                    'request': dict(self.request), 'clock': self.timer.status(now), 'state': state,
                    'raw': dict(self.raw), 'vision': vision_state, 'profile': profile,
                    'preview_available': self.jpeg is not None,
                    'profile_error': self.store.error, 'missing_geometry': missing,
                    'actuator_ready': actuator_ready, 'actuator_reason': actuator_reason,
                    'invalid_frames': self.invalid_frames, 'sequence_gaps': self.sequence_gaps,
                    'latency': self.latency_status(),
                    'record': self.trace.status() if self.trace else {'path':None,'done':True,'drops':0,'error':None},
                    'control_permitted': False}

    def _work(self):
        detector = None
        last_frame = None
        last_preview = 0
        while not self.stop_event.wait(.03):
            now = self.clock()
            try:
                if self.camera is not None and self.channels['camera']:
                    generation = self.session
                    packet = self.camera()
                    if packet is not None:
                        sequence, read_at, frame = packet
                        if sequence != last_frame and 0 <= now-read_at < .25:
                            last_frame = sequence
                            if detector is None:
                                if __package__.startswith('PyScripts.'):
                                    from ..camera_line_detector import BlackTapeDetector, render_overlay
                                else:
                                    from camera_line_detector import BlackTapeDetector, render_overlay
                                detector = BlackTapeDetector()
                            started = time.perf_counter()
                            result, _, _ = detector.detect(frame)
                            self.vision_processing_ms = (time.perf_counter()-started)*1000
                            now = self.clock()
                            if now-read_at >= .25:
                                self.vision = {'valid': False, 'reason': '相机处理完成时帧已过期',
                                               'frame_read_time_s': read_at}
                                continue
                            if generation != self.session or not self.channels['camera']:
                                continue
                            # Publish immutable metadata without waiting for estimator replay.
                            self.vision = {'valid': False, 'reason': result['reason'], 'confidence': result['confidence'],
                                           'pixel_error': result['near_error_px'], 'image_angle_deg': result['image_angle_deg'],
                                           'frame_age_ms': (now-read_at)*1000, 'frame_read_time_s': read_at}
                            calibration = self.store.snapshot()['camera']
                            if calibration is not None:
                                from .vision import ground_observation
                                observation = ground_observation(result, [frame.shape[1], frame.shape[0]], calibration, read_at, sequence)
                                queued = self.queue_vision(observation)
                                self.vision = {**self.vision, 'valid': queued, 'reason': 'metric_observation_queued' if queued else '视觉输入队列繁忙',
                                               'lateral_error_m': observation.lateral_error_m, 'heading_error_rad': observation.heading_error_rad}
                            else:
                                self.vision = {**self.vision, 'reason': '相机地面标定未完成，仅显示像素结果'}
                            # Preview compression cannot delay this frame's metric observation.
                            if now-last_preview >= .2:
                                import cv2
                                started = time.perf_counter()
                                ok, jpeg = cv2.imencode('.jpg', render_overlay(frame, result, detector.config), [cv2.IMWRITE_JPEG_QUALITY, 75])
                                self.preview_encoding_ms = (time.perf_counter()-started)*1000
                                if ok:
                                    self.jpeg = jpeg.tobytes()
                                last_preview = self.clock()
            except Exception as exc:
                self.vision = {**self.vision, 'valid': False, 'reason': str(exc)}
