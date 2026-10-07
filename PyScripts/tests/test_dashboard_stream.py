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
        self.app.sensor_state.update({'last_encoder_rx_mono': time.monotonic(), 'throttle': 1500, 'mode': 'WEB'})
        self.app.esp32_serial = Mock(is_open=True)
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

    def test_camera_uses_deadline_instead_of_required_frame_count(self):
        camera = self.app.cam_left
        camera.is_active = camera.available = camera.running = True
        camera.record_done.set()
        camera.cap = Mock()
        frame = Mock()
        captured = []

        def read():
            camera.running = False
            return True, frame

        camera.cap.read.side_effect = read
        with patch.object(camera, '_save_images_to_disk', side_effect=lambda frames, session: captured.extend(frames)):
            self.assertTrue(camera.start_record('deadline_test', duration_sec=3))
            camera.record_frames.append((1, frame))
            with patch.object(self.app.time, 'monotonic', return_value=camera.record_deadline + 0.01):
                camera._update()
            self.assertTrue(camera.record_done.wait(2))
        self.assertFalse(camera.is_recording)
        self.assertEqual(len(captured), 1)  # Late frame excluded even far below 630 frames.
        camera.available = False


if __name__ == '__main__':
    unittest.main()
