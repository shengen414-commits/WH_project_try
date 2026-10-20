"""Exercise HTTP recording/control against fake hardware, never open real ports."""
import csv
import importlib
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PyScripts.telemetry import CsvRecorder, EncoderStream
from PyScripts.steering_requests import SteeringRequests
from PyScripts.manual_steering import ManualSteering


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cv2 = Mock()
        cv2.VideoCapture.return_value.isOpened.return_value = False
        # Import must not connect to physical devices or start acquisition threads.
        with patch.dict('sys.modules', {'cv2': cv2, 'numpy': Mock(), 'serial': Mock()}), \
             patch('threading.Thread.start'), patch('time.sleep'), \
             patch('os.makedirs'):
            cls.app = importlib.import_module('PyScripts.Camera_mit_Rotation_dashboard')
        cls.app.cam_left.running = cls.app.cam_right.running = False

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        self.app.SAVE_DIR = self.directory.name
        self.app.SPEED_RECORD_DIR = str(Path(self.directory.name) / 'Speed_Records')
        self.app.speed_recorder = self.app.session_recorder = None
        self.app.encoder_stream = EncoderStream()
        self.app.imu_visualization.reset()
        self.app.sensor_state.update({'last_encoder_rx_mono': time.monotonic(), 'throttle': 1500, 'mode': 'WEB'})
        self.app.sensor_state.update({'last_serial_line': '', 'last_serial_rx_mono': 0, 'serial_error': None})
        self.app.sensor_state.update(steering={}, last_steering_rx_mono=0)
        self.app.steering_requests = SteeringRequests()
        self.app.manual_steering = ManualSteering()
        self.app.esp32_serial = Mock(is_open=True, port='/dev/ttyUSB0')
        self.app.sync_coordinator.state = {'active': False, 'phase': 'idle', 'events': []}
        self.app.record_workflow_active.clear()
        self.app.brake_sequence_active.clear()
        self.app.estop_ignore_until = 0
        self.client = self.app.app.test_client()

    def tearDown(self):
        for recorder in (self.app.speed_recorder, self.app.session_recorder):
            if recorder:
                recorder.stop()
                self.assertTrue(recorder.done.wait(3))

    def receive(self, chunks):
        chunks = iter(chunks)
        serial = self.app.esp32_serial
        serial.in_waiting = 1

        def read(_):
            try:
                return next(chunks)
            except StopIteration:
                raise KeyboardInterrupt  # Terminate the reader's infinite hardware loop in this test.
        serial.read.side_effect = read
        with self.assertRaises(KeyboardInterrupt):
            self.app.read_esp32_data()

    def test_speed_csv_receives_split_lines_and_control_keeps_working(self):
        self.assertEqual(self.client.post('/speed_record/start').status_code, 200)
        self.receive([b'[ENC],0,10,', b'0\n[ENC],1,20,2\n[DRIVE] mode: RC | pwm: 1600\n',
                      b'[ENC],3,40,6\n'])
        self.assertEqual(self.client.post('/set_throttle?val=1600').status_code, 200)
        self.app.esp32_serial.write.assert_called_with(b'T1600\n')
        response = self.client.post('/speed_record/stop').get_json()
        self.assertEqual((response['sample_count'], response['sequence_gaps']), (3, 1))
        self.app.esp32_serial.reset_input_buffer.assert_not_called()
        with open(response['file_path'], newline='', encoding='utf-8') as file:
            rows = list(csv.DictReader(file))
        self.assertEqual([int(row['Position']) for row in rows], [0, 2, 6])
        self.assertEqual(rows[-1]['mode'], 'RC')

    def test_stale_encoder_is_not_recorded_as_a_success(self):
        self.app.sensor_state['last_encoder_rx_mono'] = 0
        self.assertEqual(self.client.post('/speed_record/start').status_code, 409)
        self.assertEqual(self.client.get('/start_record').status_code, 409)
        self.assertFalse(self.client.get('/sensor_stats').get_json()['encoder_fresh'])

    def test_imu_page_commands_and_serial_stream_share_reader(self):
        self.assertEqual(self.client.get('/imu').status_code, 200)
        self.app.esp32_serial.write.return_value = 2
        self.assertEqual(self.client.post('/imu/command', json={'action':'start'}).status_code, 200)
        self.app.esp32_serial.write.assert_called_with(b'O\n')
        self.assertFalse(self.app.encoder_ready())
        self.receive([b'[IMU],100,0,0,1,0,0,0,25,0,NO\n'])
        state = self.client.get('/imu/status').get_json()
        self.assertTrue(state['fresh'])
        self.assertEqual(state['sample']['acceleration'], [0,0,1])
        self.assertEqual(self.app.encoder_public_state()['debug_mode'], 'imu')
        self.assertEqual(self.client.post('/imu/command', json={'action':'zero'}).status_code, 200)
        self.assertEqual(self.client.post('/imu/command', json={'action':'calibrate'}).status_code, 200)
        self.app.esp32_serial.write.assert_called_with(b'Z\n')
        self.assertEqual(self.client.post('/imu/command', json={'action':'stop'}).status_code, 200)
        self.app.esp32_serial.write.assert_called_with(b'R\n')
        self.assertFalse(self.client.get('/imu/status').get_json()['fresh'])
        self.app.esp32_serial.reset_input_buffer.assert_not_called()

    def test_imu_bad_uart_byte_is_reported_instead_of_silently_deleted(self):
        raw = b'[IMU],100,0\xff,0,1,0,0,0,25,0,NO\n'
        self.receive([raw[:15],raw[15:]])
        state = self.client.get('/imu/status').get_json()
        self.assertFalse(state['fresh'])
        self.assertIn('ax 不是数字',state['diagnostics']['last_invalid']['reason'])
        self.assertIn('\ufffd',state['diagnostics']['last_invalid']['raw'])
        self.receive([b'[IMU],120,0,0,1,0,0,0,25,0,NO\n'])
        state = self.client.get('/imu/status').get_json()
        self.assertTrue(state['fresh'])
        self.assertEqual(state['diagnostics']['invalid_frames'],1)
        self.assertEqual(state['diagnostics']['consecutive_invalid'],0)

    def test_uart_read_failure_closes_once_and_invalidates_sensor_streams(self):
        self.app.imu_visualization.feed('[IMU],100,0,0,1,0,0,0,25,0,NO')
        self.app.sensor_state['last_steering_rx_mono'] = time.monotonic()
        port = self.app.esp32_serial
        port.in_waiting = 1
        port.read.side_effect = [b'[IMU],120,0,0',OSError('device disconnected or multiple access on port?')]
        port.close.side_effect = lambda: setattr(port,'is_open',False)
        self.app.read_esp32_data()
        port.close.assert_called_once()
        self.assertEqual(port.read.call_count,2)
        self.assertFalse(self.app.encoder_ready())
        self.assertFalse(self.app.imu_visualization.status()['fresh'])
        self.assertEqual(self.app.sensor_state['last_steering_rx_mono'],0)
        self.assertIn('重启 Dashboard',self.app.sensor_state['serial_error'])
        self.assertEqual(self.client.post('/imu/command',json={'action':'start'}).status_code,409)
        self.assertFalse(self.client.get('/imu/status').get_json()['serial_open'])

    def test_uart_timeout_is_not_treated_as_disconnection(self):
        self.receive([b'',b'[IMU],120,0,0,1,0,0,0,25,0,NO\n'])
        self.app.esp32_serial.close.assert_not_called()
        self.assertTrue(self.client.get('/imu/status').get_json()['fresh'])

    def test_imu_commands_block_recording_bad_requests_and_closed_port(self):
        self.assertEqual(self.client.post('/imu/command', json=[]).status_code, 400)
        self.assertEqual(self.client.post('/imu/command', json={'action':'zero'}).status_code, 409)
        self.assertEqual(self.client.post('/imu/command', json={'action':'calibrate'}).status_code, 409)
        self.app.sync_coordinator.state['active'] = True
        self.assertEqual(self.client.post('/imu/command', json={'action':'start'}).status_code, 409)
        self.app.sync_coordinator.state['active'] = False
        self.assertEqual(self.client.post('/speed_record/start').status_code, 200)
        self.assertEqual(self.client.post('/imu/command', json={'action':'start'}).status_code, 409)
        self.client.post('/speed_record/stop')
        self.app.esp32_serial.is_open = False
        self.assertEqual(self.client.post('/imu/command', json={'action':'start'}).status_code, 409)
        self.app.esp32_serial.is_open = True
        self.app.esp32_serial.write.return_value = 1
        self.assertEqual(self.client.post('/imu/command', json={'action':'start'}).status_code, 503)

    def test_dashboard_exposes_sync_panel_without_connecting_a_device(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('sync-start', response.get_data(as_text=True))
        self.assertIn('js/sync_recording.js', response.get_data(as_text=True))
        status = self.client.get('/sync_record/status')
        self.assertEqual(status.status_code, 200)
        self.assertFalse(status.get_json()['pressure']['connected'])

    def test_combined_status_contains_all_ui_data(self):
        response = self.client.get('/dashboard/status')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.get_json()), {'sensor', 'fps', 'record', 'speed_record', 'sync', 'conversion', 'replay'})
        self.assertEqual(response.get_json()['sensor']['throttle'], 1500)

    def test_servo_debug_explains_stale_encoder_and_normal_command_restores_data(self):
        self.app.sensor_state.update(last_encoder_rx_mono=0, last_serial_rx_mono=time.monotonic(),
                                     last_serial_line='[SERVO] GPIO12 target=1500 us PWM=ready')
        state = self.client.get('/dashboard/status').get_json()['sync']
        self.assertFalse(state['encoder_fresh'])
        self.assertEqual(state['encoder']['debug_mode'], 'servo')
        with patch.object(self.app.time, 'monotonic', return_value=time.monotonic() + 2):
            self.assertIsNone(self.app.encoder_public_state()['debug_mode'])  # Old last line does not prove a live debug stream.
        response = self.client.post('/sync_record/encoder/normal')
        self.assertEqual(response.status_code, 200)
        self.app.esp32_serial.write.assert_called_once_with(b'R\n')
        self.assertFalse(response.get_json()['status']['encoder_fresh'])  # Wait for real ENC, not command acknowledgment.
        self.receive([b'[ENC],0,10,0\n'])  # Stationary wheels also produce valid samples.
        state = self.client.get('/dashboard/status').get_json()['sync']
        self.assertTrue(state['encoder_fresh'])
        self.assertIsNone(state['encoder']['debug_mode'])

    def test_mpu_debug_explains_paused_encoder(self):
        self.app.sensor_state.update(last_encoder_rx_mono=0)
        self.receive([b'[MPU] a=0.00,0.00,1.00 w=0.0,0.0,0.0 T=25.0 z=0.0 cal=NO\n'])
        state = self.client.get('/dashboard/status').get_json()['sync']
        self.assertFalse(state['encoder_fresh'])
        self.assertEqual(state['encoder']['debug_mode'], 'imu')

    def test_normal_command_requires_serial_and_no_active_capture(self):
        self.app.esp32_serial.is_open = False
        self.assertEqual(self.client.post('/sync_record/encoder/normal').status_code, 409)
        self.app.esp32_serial.write.assert_not_called()
        self.app.esp32_serial.is_open = True
        self.app.sync_coordinator.state['active'] = True
        self.assertEqual(self.client.post('/sync_record/encoder/normal').status_code, 409)
        self.app.esp32_serial.write.assert_not_called()
        self.app.sync_coordinator.state['active'] = False
        with self.app.sync_coordinator.operation_lock:
            self.assertEqual(self.client.post('/sync_record/encoder/normal').status_code, 409)
        self.app.esp32_serial.write.side_effect = OSError('USB disconnected')
        response = self.client.post('/sync_record/encoder/normal')
        self.assertEqual(response.status_code, 503)
        self.assertIn('USB disconnected', response.get_json()['error'])
        self.assertFalse(self.app.sync_coordinator.operation_lock.locked())

    def test_line_steering_request_waits_for_firmware_confirmation_and_keeps_encoder(self):
        response = self.client.post('/steering/line', json={'enabled': True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['status'], 'requested')
        request_id = response.get_json()['request_id']
        self.app.esp32_serial.write.assert_called_with(f'N1,{request_id}\n'.encode())
        self.assertNotIn('mode', self.client.get('/sensor_stats').get_json()['steering'])
        self.receive([f'[FOLLOW_ACK] id={request_id} action=1 ok=1 mask=16 raw=16 lvl=1 reason=armed\n'.encode(),
                      b'[FOLLOW] mode=LINE e=0.143 pwm=1504 mask=16 gyro=0 reason=tracking\n[ENC],0,10,0\n'])
        state = self.client.get('/sensor_stats').get_json()
        self.assertTrue(state['encoder_fresh'])
        self.assertEqual(state['steering']['mode'], 'LINE')
        self.assertTrue(state['steering']['fresh'])
        self.assertEqual(state['steering']['request']['status'], 'confirmed')
        stop = self.client.post('/steering/line', json={'enabled': False})
        self.assertEqual(stop.status_code, 200)
        self.app.esp32_serial.write.assert_called_with(f"N0\nN0,{stop.get_json()['request_id']}\n".encode())

    def test_steering_rejection_is_visible_even_while_firmware_stays_off(self):
        response = self.client.post('/steering/line', json={'enabled': True})
        request_id = response.get_json()['request_id']
        self.receive([f'[FOLLOW_ACK] id={request_id} action=1 ok=0 mask=255 raw=255 lvl=1 reason=line_ambiguous\n'.encode()])
        state = self.client.get('/dashboard/status').get_json()['sensor']['steering']
        self.assertEqual(state['request']['status'], 'rejected')
        self.assertIn('全通道命中', state['request']['message'])
        self.assertIn('raw=11111111', state['request']['message'])

    def test_line_steering_requires_idle_controls_and_valid_payload(self):
        for payload in ({}, {'enabled': 'true'}, []):
            self.assertEqual(self.client.post('/steering/line', json=payload).status_code, 400)
        self.app.sensor_state['throttle'] = 1600
        self.assertEqual(self.client.post('/steering/line', json={'enabled': True}).status_code, 409)
        self.assertEqual(self.client.post('/steering/line', json={'enabled': False}).status_code, 200)
        self.app.sensor_state.update(throttle=1500, mode='RC')
        self.assertEqual(self.client.post('/steering/line', json={'enabled': True}).status_code, 409)
        self.app.esp32_serial.is_open = False
        self.assertEqual(self.client.post('/steering/line', json={'enabled': False}).status_code, 409)

    def test_multi_source_and_speed_record_simultaneously_without_sd_commands(self):
        camera = Mock(is_active=True, available=True, record_error=None)
        camera.record_done = threading.Event()
        camera.record_done.set()
        camera.name = 'Left'

        def start_record(session_id, duration_sec):
            camera.session_id = session_id
            camera.record_done.clear()
            return True

        def finish_record():
            folder = Path(self.app.SAVE_DIR) / camera.session_id / 'Left'
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f'{time.time_ns()}.jpg').write_bytes(b'fake image')
            camera.record_done.set()
        camera.start_record.side_effect = start_record
        camera.finish_record.side_effect = finish_record
        factory = lambda path, duration: CsvRecorder(path, min(duration, 0.2))
        with patch.object(self.app, 'cam_left', camera), patch.object(self.app, 'CsvRecorder', factory):
            self.assertEqual(self.client.post('/speed_record/start').status_code, 200)
            response = self.client.get('/start_record')
            self.assertEqual(response.status_code, 200)
            session = response.get_json()['session_id']
            self.receive([b'[ENC],0,10,0\n[ENC],1,20,1\n'])
            # Same session cannot be started while files are being finalized.
            self.assertEqual(self.client.get('/start_record').status_code, 409)
            deadline = time.monotonic() + 3
            while self.app.record_workflow_active.is_set() and time.monotonic() < deadline:
                time.sleep(0.01)
            state = self.client.get('/record/status').get_json()
            self.assertEqual(state['status'], 'saved', state)
            self.assertEqual(state['sample_count'], 2)
            self.assertTrue((Path(self.app.SAVE_DIR) / session / 'sync_meta.json').exists())
            with open(Path(self.app.SAVE_DIR) / session / 'sensor_data_enhanced.csv', newline='', encoding='utf-8') as file:
                rows = list(csv.DictReader(file))
            self.assertTrue(rows[0]['left_image'])
            self.assertEqual(rows[0]['host_received_time_ns'], rows[0]['python_time_ns_est'])
            self.app.esp32_serial.write.assert_not_called()

    def test_recording_does_not_block_brake_endpoint(self):
        self.assertEqual(self.client.post('/speed_record/start').status_code, 200)
        with patch.object(self.app.threading.Thread, 'start'):
            response = self.client.post('/e_stop?val=1600')
        self.assertEqual(response.status_code, 200)
        self.app.esp32_serial.reset_input_buffer.assert_not_called()

    def test_empty_multi_source_reports_error_and_keeps_raw_csv(self):
        path = Path(self.directory.name) / 'empty_session' / 'sensor_data.csv'
        recorder = CsvRecorder(path, duration_sec=0.05)
        camera = Mock(name='camera', record_error=None)
        camera.record_done = threading.Event()
        camera.record_done.set()
        self.app.record_workflow_active.set()
        self.app.finalize_session(recorder, 'empty_session', [camera])
        self.assertEqual(self.app.session_status['status'], 'error')
        self.assertTrue(path.exists())
        self.assertFalse(self.app.record_workflow_active.is_set())

    def manual_start(self):
        self.app.esp32_serial.write.side_effect = lambda packet: len(packet)
        response = self.client.post('/steering/manual', json={'action': 'start'})
        self.assertEqual(response.status_code, 200, response.get_json())
        request_id = response.get_json()['request_id']
        self.app.esp32_serial.write.assert_called_with(f'V\nS0000,{request_id}\n'.encode())
        self.receive([f'[SERVO_ACK] id={request_id} ok=1 target=1500 low=1200 mid=1500 high=1800 manual=1 ready=1 proto=2 reason=ok\n'.encode()])
        return request_id

    def test_manual_panel_is_folded_and_commands_have_firmware_confirmation(self):
        html = self.client.get('/').get_data(as_text=True)
        self.assertIn('<details id="manual-steering-panel" class="manual-steering-panel">', html)
        self.manual_start()
        state = self.client.get('/steering/manual/status').get_json()
        self.assertTrue(state['active'] and state['fresh'] and state['precise'])
        self.assertEqual(state['request']['status'], 'confirmed')
        response = self.client.post('/steering/manual', json={'action': 'set', 'pulse_us': 1505})
        self.assertEqual(response.status_code, 200)
        move = response.get_json()['request_id']
        self.app.esp32_serial.write.assert_called_with(f'S1505,{move}\n'.encode())
        self.receive([f'[SERVO_ACK] id={move} ok=1 target=1505 low=1450 mid=1500 high=1550 manual=1 ready=1 reason=ok\n'.encode()])
        self.assertEqual(self.client.get('/dashboard/status').get_json()['sensor']['manual_steering']['target_us'], 1505)
        with self.app.sync_coordinator.operation_lock:
            stop = self.client.post('/steering/manual', json={'action': 'stop'}).get_json()['request_id']
        self.app.esp32_serial.write.assert_called_with(f'C\nR\nS0001,{stop}\n'.encode())
        self.receive([f'[SERVO_ACK] id={stop} ok=1 target=1500 low=1450 mid=1500 high=1550 manual=0 ready=1 reason=ok\n[ENC],0,10,0\n'.encode()])
        self.assertFalse(self.client.get('/steering/manual/status').get_json()['blocking'])
        self.assertTrue(self.app.encoder_ready())

    def test_manual_test_blocks_drive_autonomy_and_encoder_recordings(self):
        self.manual_start()
        self.assertEqual(self.client.post('/set_throttle?val=1600').status_code, 409)
        self.assertEqual(self.client.post('/set_throttle?val=1500').status_code, 200)
        self.assertEqual(self.client.post('/steering/line', json={'enabled': True}).status_code, 409)
        self.assertEqual(self.client.post('/speed_record/start').status_code, 409)
        self.assertEqual(self.client.get('/start_record').status_code, 409)
        self.assertEqual(self.client.post('/imu/command', json={'action': 'start'}).status_code, 409)
        self.app.esp32_serial.reset_input_buffer.assert_not_called()

    def test_manual_can_reach_wider_range_and_save_current_straight_position(self):
        self.manual_start()
        response = self.client.post('/steering/manual', json={'action': 'set', 'pulse_us': 1600})
        self.assertEqual(response.status_code, 200)
        request_id = response.get_json()['request_id']
        self.receive([f'[SERVO_ACK] id={request_id} ok=1 target=1600 low=1200 mid=1500 high=1800 manual=1 ready=1 proto=2 reason=ok\n'.encode()])
        response = self.client.post('/steering/manual', json={'action': 'calibrate'})
        self.assertEqual(response.status_code, 200)
        save = response.get_json()['request_id']
        self.app.esp32_serial.write.assert_called_with(f'S0002,{save}\n'.encode())
        self.receive([f'[SERVO_ACK] id={save} ok=1 target=1600 low=1300 mid=1600 high=1900 manual=1 ready=1 proto=2 reason=ok\n'.encode()])
        state = self.client.get('/steering/manual/status').get_json()
        self.assertEqual((state['low_us'], state['center_us'], state['high_us']), (1300, 1600, 1900))

    def test_manual_entry_rejects_recording_throttle_rc_and_conflicting_operations(self):
        self.app.sensor_state['throttle'] = 1600
        self.assertEqual(self.client.post('/steering/manual', json={'action': 'start'}).status_code, 409)
        self.app.sensor_state.update(throttle=1500, mode='RC')
        self.assertEqual(self.client.post('/steering/manual', json={'action': 'start'}).status_code, 409)
        self.app.sensor_state['mode'] = 'WEB'
        self.app.sync_coordinator.state['active'] = True
        self.assertEqual(self.client.post('/steering/manual', json={'action': 'start'}).status_code, 409)
        self.app.sync_coordinator.state['active'] = False
        with self.app.sync_coordinator.operation_lock:
            self.assertEqual(self.client.post('/steering/manual', json={'action': 'start'}).status_code, 409)
        self.assertEqual(self.client.post('/speed_record/start').status_code, 200)
        self.assertEqual(self.client.post('/steering/manual', json={'action': 'start'}).status_code, 409)
        self.app.esp32_serial.write.assert_not_called()

    def test_manual_validation_short_writes_and_unavailable_serial(self):
        for body in ([], {'action': 'bad'}, {'action': 'set', 'pulse_us': True},
                     {'action': 'set', 'pulse_us': 2000}, {'action': 'set', 'pulse_us': 1505.5}):
            self.assertEqual(self.client.post('/steering/manual', json=body).status_code, 400)
        self.assertEqual(self.client.post('/steering/manual', json={'action': 'set', 'pulse_us': 1505}).status_code, 409)
        self.app.esp32_serial.write.return_value = 1
        self.assertEqual(self.client.post('/steering/manual', json={'action': 'start'}).status_code, 503)
        self.assertEqual(self.client.get('/steering/manual/status').get_json()['request']['status'], 'error')
        self.app.esp32_serial.is_open = False
        self.assertEqual(self.client.post('/steering/manual', json={'action': 'stop'}).status_code, 409)

    def test_camera_uses_deadline_instead_of_required_frame_count(self):
        camera = self.app.cam_left
        camera.is_active = camera.available = camera.running = True
        camera.record_done.set()
        camera.cap = Mock()
        frame = Mock()

        def read():
            camera.running = False
            return True, frame

        camera.cap.read.side_effect = read
        recorder = Mock()
        with patch.object(self.app, 'AsyncImageRecorder', return_value=recorder), patch.object(camera.replay, 'submit'):
            self.assertTrue(camera.start_record('deadline_test', duration_sec=3))
            with patch.object(self.app.time, 'monotonic_ns', return_value=int((camera.record_deadline + 0.01) * 1e9)):
                camera._update()
        self.assertFalse(camera.is_recording)
        recorder.submit.assert_not_called()  # Late frames excluded regardless of frame count.
        recorder.finish.assert_called_once()
        camera.available = False


if __name__ == '__main__':
    unittest.main()
