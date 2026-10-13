"""One host-clock session combining encoder CSV and MPS4264 raw pressure frames."""
import bisect
import csv
import itertools
import json
import math
import threading
import time
from datetime import datetime
from pathlib import Path

from mps4264_app.controller import MPS4264Controller
from mps4264_app.network import ensure_ipv4_alias
from mps4264_app.protocol import convert_binary_to_csv
try:
    from .telemetry import CsvRecorder
except ImportError:
    from telemetry import CsvRecorder


class SyncRecordingError(RuntimeError):
    pass


def align_records(folder, origin_ns, max_delta_ms=30):
    """Nearest received speed sample; leave long gaps/outside coverage unmatched.

    Pressure CSV and index are joined in file order AND verified by frame number,
    preserving duplicate/reordered frame records rather than using a lossy dict.
    """
    folder = Path(folder)
    speeds = []
    with (folder / 'speed.csv').open(newline='', encoding='utf-8') as file:
        for row in csv.DictReader(file):
            speeds.append((int(row['host_receive_monotonic_ns']), row))
    times = [sample[0] for sample in speeds]
    if times != sorted(times):
        raise ValueError('车速单调时间顺序异常')
    fields = ['session_elapsed_ms', 'pressure_receive_monotonic_ns', 'pressure_receive_unix_ns',
              'speed_receive_monotonic_ns', 'speed_sequence', 'speed_device_epoch',
              'speed_position', 'speed_rpm', 'speed_kmh', 'speed_throttle', 'speed_mode',
              'speed_minus_pressure_ms', 'match_valid', 'match_reason']
    total = matched = 0
    temporary = folder / 'synchronized.csv.part'
    with (folder / 'pressure.csv').open(newline='', encoding='utf-8') as pressure, \
         (folder / 'pressure.index.csv').open(newline='', encoding='utf-8') as index, \
         temporary.open('x', newline='', encoding='utf-8') as output:
        reader = csv.DictReader(pressure)
        writer = csv.DictWriter(output, fieldnames=fields + reader.fieldnames)
        writer.writeheader()
        for row, entry in itertools.zip_longest(reader, csv.DictReader(index)):
            if row is None or entry is None or int(row['frame']) != int(entry['frame']):
                raise ValueError('压力 CSV 与索引的行数或帧号不一致，拒绝错误匹配')
            stamp = int(entry['host_receive_monotonic_ns'])
            result = dict(row)
            result.update(session_elapsed_ms=(stamp - origin_ns) / 1e6,
                          pressure_receive_monotonic_ns=stamp,
                          pressure_receive_unix_ns=entry['host_receive_unix_ns'],
                          match_valid=0, match_reason='no_speed_samples')
            if speeds:
                at = bisect.bisect_left(times, stamp)
                candidates = [i for i in (at - 1, at) if 0 <= i < len(times)]
                nearest = min(candidates, key=lambda i: abs(times[i] - stamp))
                delta = (times[nearest] - stamp) / 1e6
                result['speed_minus_pressure_ms'] = delta
                if stamp < times[0] or stamp > times[-1]:
                    result['match_reason'] = 'outside_speed_coverage'
                elif abs(delta) > max_delta_ms:
                    result['match_reason'] = 'speed_gap'
                elif 0 < at < len(times) and speeds[at - 1][1]['device_epoch'] != speeds[at][1]['device_epoch'] and times[at] != stamp:
                    result['match_reason'] = 'device_reboot_boundary'
                else:
                    speed = speeds[nearest][1]
                    result.update(speed_receive_monotonic_ns=times[nearest], speed_sequence=speed['sequence'],
                                  speed_device_epoch=speed['device_epoch'], speed_position=speed['Position'],
                                  speed_rpm=speed['rpm'], speed_kmh=speed['kmh'], speed_throttle=speed['throttle'],
                                  speed_mode=speed['mode'], match_valid=1, match_reason='nearest_receive_time')
                    matched += 1
            writer.writerow(result)
            total += 1
    temporary.replace(folder / 'synchronized.csv')
    return {'pressure_rows': total, 'matched_rows': matched, 'unmatched_rows': total - matched,
            'max_match_delta_ms': max_delta_ms, 'method': 'nearest_host_receive_time'}


class SyncRecorder:
    def __init__(self, root, encoder_ready, controller=None, speed_calibration=None):
        self.root = Path(root).resolve()
        self.encoder_ready = encoder_ready
        self.controller = controller or MPS4264Controller(self.root / 'Sync_Records')
        self.lock = threading.Lock()
        self.operation_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.state = {'active': False, 'phase': 'idle', 'events': []}
        self.recorder = None
        self.worker = None
        self.network = None
        self.conversion = {'phase': 'idle'}
        self.speed_calibration = speed_calibration or {'ppr': 12.0, 'kmh_per_rpm': 0.007173}

    def _event(self, name):
        with self.lock:
            self.state['events'].append({'event': name, 'monotonic_ns': time.monotonic_ns(), 'unix_ns': time.time_ns()})

    def _update(self, **values):
        with self.lock:
            self.state.update(values)

    def status(self):
        with self.lock:
            result = {**self.state, 'events': list(self.state['events'])}
            recorder = self.recorder
        if recorder:
            result['speed'] = recorder.status()
        # TCP commands must never block status polling or car PWM endpoints.
        result['pressure'] = self.controller.try_status()
        result['encoder_fresh'] = self.encoder_ready()
        result['network'] = self.network
        result['device_busy'] = self.operation_lock.locked()
        return result

    def device_operation(self, action, values):
        if not self.operation_lock.acquire(blocking=False):
            raise SyncRecordingError('扫描阀正在执行其他操作')
        try:
            with self.lock:
                if self.state['active']:
                    raise SyncRecordingError('同步记录期间不能更改扫描阀配置')
            pressure = self.controller.status()
            if (pressure['file_open'] or pressure['scanning']) and action not in ('pressure-scan', 'pressure-stop', 'close-file', 'command'):
                raise SyncRecordingError('上一份压力文件仍未关闭，请先重试停止')
            if action == 'connect':
                if values.get('init_network', True):
                    self.network = ensure_ipv4_alias(str(values.get('interface', 'eth0')),
                                                     str(values.get('local_cidr', '191.30.90.82/16')))
                result = self.controller.connect(str(values.get('device_ip', '191.30.90.102')),
                                                 int(values.get('control_port', 23)),
                                                 bool(values.get('confirm_reboot', False)))
            elif action == 'disconnect':
                result = self.controller.disconnect()
            elif action == 'info':
                result = self.controller.device_info()
            elif action == 'udp':
                result = self.controller.configure_udp(str(values['host_ip']), int(values.get('udp_port', 50023)))
            elif action == 'parameters':
                result = self.controller.set_parameters(float(values['rate']), int(values.get('fps', 0)),
                                                         int(values.get('fast_group', 0)),
                                                         int(values.get('read_mode', 0)), int(values.get('subset_size', 16)))
            elif action == 'command':
                result = self.controller.send_command(str(values.get('command', '')),
                                                      bool(values.get('zero_pressure_confirmed', False)))
            elif action == 'save':
                result = self.controller.save_settings()
            elif action == 'calz':
                result = self.controller.calz(bool(values.get('zero_pressure_confirmed', False)))
            elif action == 'new-file':
                self.controller.data_dir = self.root / 'Sync_Records' / 'Pressure_Only'
                name = str(values.get('name') or datetime.now().strftime('pressure_%Y%m%d_%H%M%S_%f'))
                result = self.controller.new_file(name)
                with self.lock:
                    self.recorder = None
                    self.state = {'active': False, 'phase': 'idle', 'events': [],
                                  'folder': str(self.controller.data_dir), 'mode': 'pressure_only'}
            elif action == 'pressure-scan':
                result = self.controller.scan()
            elif action == 'pressure-stop':
                result = self.controller.stop()
            elif action == 'close-file':
                result = self.controller.close_file()
            else:
                raise ValueError('未知扫描阀操作')
            return {'result': result, 'status': self.status()}
        finally:
            self.operation_lock.release()

    def command(self, values):
        line = str(values.get('command', '')).strip()
        # SCAN/STOP cannot bypass ownership of an active synchronized capture.
        if line.upper() == 'STOP':
            with self.lock:
                active = self.state['active']
            if active:
                return {'result': {'command': line, 'response': '同步停止已请求；等待双方保存。'}, 'status': self.stop()}
            return self.device_operation('pressure-stop', values)
        if line.upper() == 'SCAN':
            pressure = self.controller.try_status()
            if pressure.get('busy'):
                raise SyncRecordingError('扫描阀正在执行其他操作')
            if pressure.get('file_open'):
                return self.device_operation('pressure-scan', values)
            state = self.start(values.get('duration_sec', 60), values.get('max_delta_ms', 30))
            return {'result': {'command': line, 'response': '同步记录已启动；等待双方数据。'}, 'status': state}
        return self.device_operation('command', values)

    def library_path(self, filename, suffixes=('.dat', '.csv', '.json')):
        root = (self.root / 'Sync_Records').resolve()
        path = (root / str(filename)).resolve()
        if not path.is_relative_to(root) or path.suffix.lower() not in suffixes or not path.is_file():
            raise ValueError('请选择记录目录中的有效文件')
        return path

    def files(self):
        root = self.root / 'Sync_Records'
        items = []
        if root.exists():
            for path in root.rglob('*.dat'):
                if path.is_file() and path.resolve().is_relative_to(root.resolve()):
                    items.append({'name': path.relative_to(root).as_posix(), 'bytes': path.stat().st_size,
                                  'csv_exists': path.with_suffix('.csv').is_file()})
        return sorted(items, key=lambda item: item['name'], reverse=True)

    def convert(self, values):
        if not self.operation_lock.acquire(blocking=False):
            raise SyncRecordingError('已有设备操作或转换正在运行')
        try:
            with self.lock:
                if self.state['active']:
                    raise SyncRecordingError('同步采集结束后再转换文件')
            if self.controller.status()['file_open']:
                raise SyncRecordingError('请先停止并关闭压力文件')
            source = self.library_path(values.get('filename', ''), ('.dat',))
            group = values.get('fast_group')
            if group in (None, ''):
                meta = source.with_suffix('.meta.json')
                group = json.loads(meta.read_text(encoding='utf-8')).get('fast_group') if meta.exists() else None
            else:
                group = int(group)
            with self.lock:
                self.conversion = {'phase': 'running', 'filename': str(values['filename'])}

            def run():
                try:
                    result = convert_binary_to_csv(source, source.with_suffix('.csv'), group,
                                                   overwrite=bool(values.get('overwrite', False)))
                    with self.lock:
                        self.conversion = {'phase': 'done', 'result': result}
                except Exception as exc:
                    with self.lock:
                        self.conversion = {'phase': 'error', 'error': str(exc)}
                finally:
                    self.operation_lock.release()
            threading.Thread(target=run, name='pressure-conversion', daemon=True).start()
            return self.conversion_status()
        except Exception:
            self.operation_lock.release()
            raise

    def conversion_status(self):
        with self.lock:
            return dict(self.conversion)

    def submit(self, sample):
        with self.lock:
            recorder = self.recorder
            if recorder is None or not self.state['active']:
                return
            if sample['host_receive_monotonic_ns'] >= recorder.started_mono_ns and 'first_speed_ns' not in self.state:
                self.state['first_speed_ns'] = sample['host_receive_monotonic_ns']
        recorder.submit(sample)

    def start(self, duration_sec=60, max_delta_ms=30):
        duration_sec, max_delta_ms = float(duration_sec), float(max_delta_ms)
        if not math.isfinite(duration_sec) or not 1 <= duration_sec <= 600:
            raise ValueError('同步记录时长需为 1–600 秒')
        if not math.isfinite(max_delta_ms) or not 1 <= max_delta_ms <= 1000:
            raise ValueError('匹配容差需为 1–1000 ms')
        if not self.operation_lock.acquire(blocking=False):
            raise SyncRecordingError('扫描阀正在执行其他操作')
        try:
            with self.lock:
                if self.state['active']:
                    raise SyncRecordingError('已有同步记录正在运行或等待 STOP 确认')
            if not self.encoder_ready():
                raise SyncRecordingError('没有新鲜编码器数据，请确认 ESP32 正常固件和串口模式')
            pressure = self.controller.status()
            if not pressure['connected'] or pressure['reboot_required'] or pressure['scanning'] or pressure['file_open']:
                raise SyncRecordingError('请先连接扫描阀、完成重启确认并关闭已有压力文件')
            if pressure['rate_hz'] is None or pressure['fps'] is None or not pressure['udp_target_ip']:
                raise SyncRecordingError('请先读取设备信息或发送参数，确认 RATE、FPS 和 UDP 目标')
            session = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            folder = self.root / 'Sync_Records' / session
            folder.mkdir(parents=True, exist_ok=False)
            self.stop_event.clear()
            with self.lock:
                self.recorder = None
                self.state = {'active': True, 'phase': 'starting', 'session_id': session,
                              'folder': str(folder), 'duration_sec': duration_sec,
                              'max_match_delta_ms': max_delta_ms, 'origin_monotonic_ns': time.monotonic_ns(),
                              'origin_unix_ns': time.time_ns(), 'events': [], 'parameters': pressure}
                self.state['speed_calibration'] = dict(self.speed_calibration)
                self.state['speed_value_method'] = '100 ms pulse window plus EMA; raw pulses preserved in speed.csv'
            self.worker = threading.Thread(target=self._run, name='sync-recording', daemon=True)
            self.worker.start()
        finally:
            self.operation_lock.release()
        return self.status()

    def stop(self):
        with self.lock:
            active = self.state['active']
            retry = self.state['phase'] == 'stop_failed'
        if not active:
            raise SyncRecordingError('没有正在运行的同步记录')
        self.stop_event.set()
        if retry:
            if not self.operation_lock.acquire(blocking=False):
                raise SyncRecordingError('STOP 重试正在执行')
            self._update(phase='stopping')
            self.worker = threading.Thread(target=self._retry_finish, name='sync-stop-retry', daemon=True)
            self.worker.start()
        return self.status()

    def _run(self):
        error = None
        reason = 'manual_stop'
        try:
            with self.operation_lock:
                folder = Path(self.state['folder'])
                self.controller.data_dir = folder
                self.controller.new_file('pressure')
                self._event('pressure_listener_open')
                recorder = CsvRecorder(folder / 'speed.csv', duration_sec=self.state['duration_sec'] + 15)
                with self.lock:
                    self.recorder = recorder
                self._event('speed_recorder_open')
                self._event('scan_command_begin')
                self.controller.scan()
                self._event('scan_command_confirmed')
                self._update(phase='waiting_for_data')
                self._save_manifest()
            started = last_frame_change = time.monotonic()
            last_frames = 0
            while not self.stop_event.wait(0.1):
                pressure = self.controller.try_status()
                if pressure.get('busy'):
                    continue
                now = time.monotonic()
                frames = pressure['frames_received']
                if frames != last_frames:
                    last_frames, last_frame_change = frames, now
                    with self.lock:
                        if 'first_pressure_observed_ns' not in self.state:
                            self.state['first_pressure_observed_ns'] = time.monotonic_ns()
                            self.state['first_pressure_ns'] = pressure.get('first_receive_monotonic_ns')
                        both = 'first_speed_ns' in self.state
                    if both:
                        self._update(phase='recording')
                if pressure['last_error']:
                    raise SyncRecordingError(pressure['last_error'])
                if not self.encoder_ready():
                    raise SyncRecordingError('编码器数据中断，同步记录已结束')
                if self.recorder.status()['stop_reason'] and str(self.recorder.status()['stop_reason']).startswith('error:'):
                    raise SyncRecordingError(self.recorder.status()['stop_reason'])
                if pressure['fps'] and frames >= pressure['fps']:
                    reason = 'pressure_fps_complete'
                    break
                if now - last_frame_change > max(3, 5 / pressure['rate_hz']):
                    raise SyncRecordingError('扫描阀长时间没有有效压力帧，请检查 UDP 目标和网络')
                if now - started >= self.state['duration_sec']:
                    reason = 'max_duration'
                    break
        except Exception as exc:
            error = str(exc)
            reason = 'acquisition_error'
        with self.operation_lock:
            self._finish(reason, error)

    def _retry_finish(self):
        try:
            self._finish(self.state.get('stop_reason', 'manual_stop'), self.state.get('acquisition_error'))
        finally:
            self.operation_lock.release()

    def _finish(self, reason, error):
        self._update(phase='stopping', stop_reason=reason, acquisition_error=error)
        self._event('stop_command_begin')
        try:
            self.controller.stop()
            self._event('stop_command_confirmed')
            self.controller.close_file()
            self._event('pressure_file_closed')
            self._update(first_pressure_ns=self.controller.status().get('first_receive_monotonic_ns'))
        except Exception as exc:
            if self.recorder:
                self.recorder.stop('pressure_stop_failed')
            self._update(phase='stop_failed', active=True, error=f'压力 STOP/关闭未确认，保留文件；恢复通信后重试停止: {exc}')
            self._save_manifest()
            return
        try:
            if self.recorder:
                self.recorder.stop(reason)
                if not self.recorder.done.wait(10):
                    raise SyncRecordingError('车速文件仍在保存，原始文件保留')
                if str(self.recorder.status()['stop_reason']).startswith('error:'):
                    raise SyncRecordingError(self.recorder.status()['stop_reason'])
            self._update(phase='saving')
            if not self.recorder or not self.recorder.sample_count or not self.controller.status()['frames_received']:
                raise SyncRecordingError(error or '一方没有有效数据，原始文件已保留')
            result = self.controller.convert_file('pressure.dat')
            alignment = align_records(self.state['folder'], self.state['origin_monotonic_ns'], self.state['max_match_delta_ms'])
            self._update(alignment=alignment, conversion=result)
            if error:
                raise SyncRecordingError(error)
            self._update(phase='saved', active=False, error=None)
        except Exception as exc:
            self._update(phase='error', active=False, error=str(exc))
        self._event('session_finished')
        self._save_manifest()

    def _save_manifest(self):
        try:
            payload = self.status()
            payload['sync_method'] = 'host_receive_monotonic_ns; not hardware acquisition synchronization'
            folder = Path(payload['folder'])
            temporary = folder / 'session.json.part'
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(folder / 'session.json')
        except Exception as exc:
            self._update(manifest_error=str(exc))

    def shutdown(self):
        """Best-effort normal process exit; never claim an unconfirmed STOP succeeded."""
        self.stop_event.set()
        with self.lock:
            retry = self.state['phase'] == 'stop_failed'
        if retry:
            try:
                self.stop()
            except SyncRecordingError:
                pass
        if self.worker and self.worker.is_alive():
            self.worker.join(8)
            if self.worker.is_alive():
                return False
        with self.lock:
            if self.state['active']:
                return False
        if not self.operation_lock.acquire(blocking=False):
            return False
        try:
            self.controller.disconnect()
            return True
        finally:
            self.operation_lock.release()
