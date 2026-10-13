import csv
import json
import socket
import struct
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from flask import Flask
from PyScripts.sync_recording import SyncRecorder, SyncRecordingError, align_records
from PyScripts.sync_web import register_sync_routes
from PyScripts.telemetry import EncoderStream, FIELDS, CsvRecorder
from mps4264_app.controller import MPS4264Controller, MPSControllerError
from mps4264_app.tests.test_mps4264 import FakeDevice, make_frame


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        reserved = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        reserved.bind(('127.0.0.1', 0))
        port = reserved.getsockname()[1]
        reserved.close()
        self.fake = FakeDevice(port, endian='<')
        def send_timed_udp():
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
                for number in (1, 2, 3):
                    raw = bytearray(make_frame(number, endian='<'))
                    struct.pack_into('<4I', raw, 332, 12, number * 20_000_000, 0, 0)
                    udp.sendto(raw, ('127.0.0.1', port))
                    time.sleep(.02)
        self.fake._send_udp = send_timed_udp
        self.controller = MPS4264Controller(self.directory.name)
        self.controller.connect('127.0.0.1', self.fake.port)
        self.controller.device_info()
        self.ready = True
        self.sync = SyncRecorder(self.directory.name, lambda: self.ready, self.controller)
        self.feed_stop = threading.Event()
        self.feeder = None
        app = Flask(__name__)
        register_sync_routes(app, self.sync)
        self.browser = app.test_client()

    def tearDown(self):
        self.feed_stop.set()
        if self.feeder:
            self.feeder.join(2)
        self.sync.stop_event.set()
        if self.sync.worker:
            self.sync.worker.join(5)
        self.controller.disconnect()
        self.fake.close()
        self.directory.cleanup()

    def feed(self):
        def run():
            stream = EncoderStream()
            seq = 0
            while not self.feed_stop.wait(0.01):
                self.sync.submit(stream.decode(f'[ENC],{seq},{seq * 10},{seq}', time.time_ns()))
                seq += 1
        self.feeder = threading.Thread(target=run, daemon=True)
        self.feeder.start()

    def wait(self):
        self.sync.worker.join(5)
        self.assertFalse(self.sync.worker.is_alive())
        return self.sync.status()

    def test_full_session_real_udp_and_shared_monotonic_time(self):
        self.feed()
        response = self.browser.post('/sync_record/start', json={'duration_sec': 2})
        self.assertEqual(response.status_code, 200, response.get_json())
        state = self.wait()
        self.assertEqual(state['phase'], 'saved', state)
        self.assertFalse(state['active'])
        folder = Path(state['folder'])
        self.assertEqual((folder / 'pressure.dat').stat().st_size, 3 * 348)
        self.assertEqual(state['alignment']['pressure_rows'], 3)
        self.assertGreater(state['alignment']['matched_rows'], 0)
        self.assertEqual(state['stop_reason'], 'pressure_fps_complete')
        self.assertGreaterEqual(state['pressure']['first_receive_monotonic_ns'], state['origin_monotonic_ns'])
        manifest = json.loads((folder / 'session.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['phase'], 'saved')
        self.assertIn('stop_command_confirmed', [event['event'] for event in manifest['events']])
        self.assertIn('STOP', self.fake.commands)
        plotted = self.browser.get('/plot/api/data', query_string={'filename': f"{state['session_id']}/synchronized.csv"})
        self.assertEqual(plotted.status_code, 200, plotted.get_json())
        self.assertEqual(plotted.get_json()['source_format'], 'Synchronized')
        self.assertEqual(len(plotted.get_json()['speed']), 3)

    def test_stop_failure_keeps_file_open_and_retry_finishes(self):
        self.feed()
        with patch.object(self.controller, 'stop', side_effect=MPSControllerError('STOP timeout')):
            self.sync.start(2)
            state = self.wait()
        self.assertEqual(state['phase'], 'stop_failed')
        self.assertTrue(state['active'])
        self.assertTrue(self.controller.status()['file_open'])
        with self.assertRaises(SyncRecordingError):
            self.sync.device_operation('disconnect', {})
        with self.assertRaises(SyncRecordingError):
            self.sync.start(2)
        self.sync.stop()
        state = self.wait()
        self.assertEqual(state['phase'], 'saved', state)
        self.assertFalse(self.controller.status()['file_open'])

    def test_startup_scan_failure_rolls_back_owned_files(self):
        with patch.object(self.controller, 'scan', side_effect=OSError('scan failed')):
            self.sync.start(1)
            state = self.wait()
        self.assertEqual(state['phase'], 'error')
        self.assertFalse(self.controller.status()['file_open'])
        self.assertTrue((Path(state['folder']) / 'session.json').exists())

    def test_stale_speed_stops_both_and_retains_partial_data(self):
        self.controller.set_parameters(5, 0)
        self.feed()
        self.sync.start(2)
        deadline = time.monotonic() + 2
        while self.controller.status()['frames_received'] < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.ready = False
        state = self.wait()
        self.assertEqual(state['phase'], 'error')
        self.assertIn('编码器', state['error'])
        self.assertFalse(self.controller.status()['scanning'])
        self.assertTrue((Path(state['folder']) / 'pressure.dat').exists())

    def test_preflight_and_http_validation(self):
        self.ready = False
        self.assertEqual(self.browser.post('/sync_record/start', json={}).status_code, 409)
        self.ready = True
        for duration in (-1, 0, 601, 'nan'):
            self.assertEqual(self.browser.post('/sync_record/start', json={'duration_sec': duration}).status_code, 400)
        self.assertEqual(self.browser.post('/sync_record/start', json=[]).status_code, 400)
        self.assertEqual(self.browser.post('/sync_record/device/connect', json={'init_network': 'false'}).status_code, 400)
        self.assertEqual(self.browser.post('/sync_record/stop', json={}).status_code, 409)

    def test_status_does_not_wait_for_device_operation(self):
        with self.sync.operation_lock:
            result = self.browser.get('/sync_record/status')
            self.assertEqual(result.status_code, 200)
            self.assertTrue(result.get_json()['device_busy'])

    def test_manual_stop_keeps_data_and_closes_both(self):
        self.controller.set_parameters(5, 0)
        self.feed()
        self.sync.start(2)
        deadline = time.monotonic() + 2
        while self.controller.status()['frames_received'] < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.browser.post('/sync_record/stop', json={}).status_code, 200)
        state = self.wait()
        self.assertEqual(state['phase'], 'saved', state)
        self.assertEqual(state['stop_reason'], 'manual_stop')
        self.assertTrue(state['speed']['finalized'])

    def test_connect_can_skip_network_configuration(self):
        with patch('PyScripts.sync_recording.ensure_ipv4_alias') as init_network:
            response = self.browser.post('/sync_record/device/connect', json={
                'device_ip': '127.0.0.1', 'control_port': self.fake.port, 'init_network': False})
            self.assertEqual(response.status_code, 200)
            init_network.assert_not_called()

    def test_terminal_stop_uses_coordinated_shutdown(self):
        self.controller.set_parameters(5, 0)
        self.feed()
        response = self.browser.post('/sync_record/device/command', json={'command': 'SCAN', 'duration_sec': 2})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.browser.post('/sync_record/device/command', json={'command': 'LIST S'}).status_code, 409)
        deadline = time.monotonic() + 2
        while self.controller.status()['frames_received'] < 3 and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(self.browser.post('/sync_record/device/command', json={'command': 'STOP'}).status_code, 200)
        self.assertEqual(self.wait()['phase'], 'saved')

    def test_pressure_only_files_conversion_plot_and_download(self):
        self.ready = False  # Manual pressure acquisition does not require encoder hardware.
        self.assertEqual(self.browser.post('/sync_record/device/new-file', json={'name': 'manual'}).status_code, 200)
        self.assertEqual(self.browser.post('/sync_record/device/command', json={'command': 'VER'}).status_code, 200)
        self.assertEqual(self.browser.post('/sync_record/device/command', json={'command': 'SCAN'}).status_code, 200)
        deadline = time.monotonic() + 2
        while self.controller.status()['frames_received'] < 3 and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(self.browser.post('/sync_record/device/command', json={'command': 'STOP'}).status_code, 200)
        self.assertEqual(self.browser.post('/sync_record/device/close-file', json={}).status_code, 200)
        name = self.browser.get('/sync_record/files').get_json()['files'][0]['name']
        self.assertEqual(name, 'Pressure_Only/manual.dat')
        self.assertEqual(self.browser.post('/sync_record/convert', json={'filename': name}).status_code, 200)
        deadline = time.monotonic() + 3
        while self.sync.conversion_status()['phase'] == 'running' and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(self.sync.conversion_status()['phase'], 'done')
        self.assertIn('Pressure_Only/manual.csv', self.browser.get('/plot/api/files').get_json()['files'])
        plotted = self.browser.get('/plot/api/data?filename=Pressure_Only/manual.csv')
        self.assertEqual(plotted.status_code, 200, plotted.get_json())
        response = self.browser.get('/sync_record/download?filename=Pressure_Only/manual.dat')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 3 * 348)
        response.close()

    def test_terminal_and_library_validation(self):
        self.assertEqual(self.browser.post('/sync_record/device/command', json={'command': 'VER\nSTOP'}).status_code, 400)
        self.assertEqual(self.browser.post('/sync_record/device/calz', json={}).status_code, 400)
        self.assertEqual(self.browser.post('/sync_record/device/calz', json={'zero_pressure_confirmed': 'yes'}).status_code, 400)
        self.assertEqual(self.browser.get('/sync_record/download?filename=../private.dat').status_code, 400)
        self.assertEqual(self.browser.post('/sync_record/convert', json={'filename': '../private.dat'}).status_code, 400)
        self.assertEqual(self.browser.get('/plot/api/data?filename=../../private.csv').status_code, 400)

    def test_integrated_command_includes_real_tcp_timing_and_disk_log(self):
        response = self.browser.post('/sync_record/device/command', json={'command': 'VER'}, headers={'X-MPS-Trace-ID': 'integrated-ver'})
        self.assertEqual(response.status_code, 200)
        timing = response.get_json()['_timing']
        self.assertEqual(timing['trace_id'], 'integrated-ver')
        self.assertEqual(timing['commands'][0]['command'], 'VER')
        self.assertTrue(timing['commands'][0]['prompt_received'])
        self.assertIsNotNone(timing['commands'][0]['first_tcp_byte_ms'])
        self.assertGreaterEqual(timing['server_ms'], timing['commands'][0]['response_ms'])
        self.assertIn('coordinator_lock_wait_ms', timing)
        self.assertEqual(response.headers['X-MPS-Trace-ID'], 'integrated-ver')
        path = Path(self.directory.name) / 'Sync_Records/diagnostics/command_timing.jsonl'
        record = json.loads(path.read_text(encoding='utf-8').splitlines()[-1])
        self.assertEqual(record['trace_id'], 'integrated-ver')


class AlignmentTests(unittest.TestCase):
    def build(self, folder, epochs=(0, 0, 0)):
        with (folder / 'speed.csv').open('w', newline='', encoding='utf-8') as file:
            writer = csv.DictWriter(file, fieldnames=FIELDS)
            writer.writeheader()
            for seq, (ms, epoch) in enumerate(zip((0, 20, 100), epochs)):
                writer.writerow({'host_receive_monotonic_ns': ms * 1_000_000, 'Position': seq,
                                 'sequence': seq, 'device_epoch': epoch, 'rpm': 100, 'kmh': 1,
                                 'mode': 'WEB', 'throttle': 1500})
        with (folder / 'pressure.csv').open('w', newline='', encoding='utf-8') as file, \
             (folder / 'pressure.index.csv').open('w', newline='', encoding='utf-8') as index:
            pressure = csv.writer(file)
            times = csv.writer(index)
            pressure.writerow(['frame', 'P01'])
            times.writerow(['frame', 'host_receive_monotonic_ns', 'host_receive_unix_ns'])
            for frame, ms in ((1, 10), (1, 50), (3, 110)):
                pressure.writerow([frame, 123])
                times.writerow([frame, ms * 1_000_000, 999])

    def test_gaps_coverage_and_duplicate_frames_are_not_fabricated(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            self.build(folder)
            result = align_records(folder, 0, 15)
            self.assertEqual((result['matched_rows'], result['unmatched_rows']), (1, 2))
            with (folder / 'synchronized.csv').open(newline='', encoding='utf-8') as file:
                rows = list(csv.DictReader(file))
            self.assertEqual([row['frame'] for row in rows], ['1', '1', '3'])
            self.assertEqual(rows[1]['match_reason'], 'speed_gap')
            self.assertEqual(rows[2]['match_reason'], 'outside_speed_coverage')
            self.assertEqual(rows[1]['speed_rpm'], '')

    def test_reboot_boundary_is_unmatched(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            self.build(folder, (0, 1, 1))
            align_records(folder, 0, 30)
            with (folder / 'synchronized.csv').open(newline='', encoding='utf-8') as file:
                row = next(csv.DictReader(file))
            self.assertEqual(row['match_reason'], 'device_reboot_boundary')

    def test_index_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            self.build(folder)
            (folder / 'pressure.index.csv').write_text('frame,host_receive_monotonic_ns,host_receive_unix_ns\n99,10,20\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                align_records(folder, 0)
            self.assertFalse((folder / 'synchronized.csv').exists())

    def test_wall_clock_jump_does_not_discard_received_speed(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = CsvRecorder(Path(directory) / 'speed.csv')
            sample = EncoderStream().decode('[ENC],1,10,1', recorder.started_ns - 10_000_000_000)
            recorder.submit(sample)
            recorder.stop()
            self.assertTrue(recorder.done.wait(2))
            self.assertEqual(recorder.sample_count, 1)


if __name__ == '__main__':
    unittest.main()
