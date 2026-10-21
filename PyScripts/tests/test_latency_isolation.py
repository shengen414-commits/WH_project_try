"""Fault injection: stalled disks/processing must not stall acquisition or status."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np

from PyScripts.camera_recording import AsyncImageRecorder, DiskReplayBuffer
from PyScripts.vehicle_state.profile import ProfileStore
from PyScripts.vehicle_state.recording import TraceWriter
from PyScripts.vehicle_state.service import VehicleLab
from PyScripts.vehicle_state.contracts import VisionObservation
from PyScripts.diagnostic_output import DiagnosticOutput


class LatencyIsolationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def responsive(self, operation):
        result, errors, done = [], [], threading.Event()
        def run():
            try:
                result.append(operation())
            except Exception as exc:
                errors.append(exc)
            finally:
                done.set()
        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        self.assertTrue(done.wait(.5), 'operation waited for the deliberately stalled worker')
        worker.join()
        if errors:
            raise errors[0]
        return result[0]

    def test_trace_open_is_async_queue_is_bounded_and_stop_does_not_wait_for_disk(self):
        entered, release = threading.Event(), threading.Event()
        original = TraceWriter._open
        def blocked(writer):
            entered.set(); release.wait(3)
            return original(writer)
        with patch.object(TraceWriter, '_open', blocked):
            writer = self.responsive(lambda: TraceWriter(self.root/'trace.jsonl', {}, queue_size=2))
            try:
                self.assertTrue(entered.wait(1))
                self.assertTrue(writer.submit('sample', {'n': 1}))
                self.assertTrue(writer.submit('sample', {'n': 2}))
                self.assertFalse(writer.submit('sample', {'n': 3}))
                state = self.responsive(writer.status)
                self.assertEqual((state['queue_depth'], state['drops']), (2, 1))
                self.assertFalse(state['ready'])
                self.responsive(writer.close)
                self.assertFalse(writer.done.is_set())
            finally:
                release.set(); self.assertTrue(writer.done.wait(2))
        rows = [json.loads(line) for line in writer.path.read_text().splitlines()]
        self.assertEqual([row['kind'] for row in rows], ['profile', 'sample', 'sample'])
        self.assertEqual(writer.status()['written_events'], 2)

    def test_trace_byte_budget_and_open_failure_complete_without_hanging(self):
        with patch.object(TraceWriter, '_open', side_effect=OSError('storage full')):
            writer = TraceWriter(self.root/'missing.jsonl', {})
            self.assertTrue(writer.done.wait(1))
            self.assertIn('storage full', writer.status()['error'])
            self.assertFalse(writer.submit('sample', {}))
        writer = TraceWriter(self.root/'small.jsonl', {}, max_bytes=8)
        try:
            self.assertFalse(writer.submit('sample', {'payload': 'x'*20}))
            self.assertEqual(writer.status()['drops'], 1)
        finally:
            writer.close(); self.assertTrue(writer.done.wait(2))

    def test_profile_fsync_does_not_lock_readers_and_publishes_only_after_commit(self):
        store = ProfileStore(self.root/'profile.json')
        entered, release, done = threading.Event(), threading.Event(), threading.Event()
        errors = []
        def sync(_):
            entered.set(); release.wait(3)
        def save():
            try:
                profile = store.snapshot(); profile['geometry']['wheelbase_m'] = .3
                store.save(profile)
            except Exception as exc:
                errors.append(exc)
            finally:
                done.set()
        with patch('PyScripts.vehicle_state.profile.os.fsync', sync):
            worker = threading.Thread(target=save); worker.start()
            try:
                self.assertTrue(entered.wait(1))
                old = self.responsive(store.snapshot)
                self.assertIsNone(old['geometry']['wheelbase_m'])
                self.assertEqual(old['revision'], 0)
            finally:
                release.set(); self.assertTrue(done.wait(2)); worker.join()
        self.assertFalse(errors)
        self.assertEqual(store.snapshot()['geometry']['wheelbase_m'], .3)
        self.assertEqual(store.snapshot()['revision'], 1)

    def test_stalled_state_worker_leaves_uart_status_and_stop_responsive(self):
        entered, release = threading.Event(), threading.Event()
        writes = []
        lab = VehicleLab(ProfileStore(self.root/'p.json'), self.root, writes.append)
        lab.owned = True
        def blocked(*_):
            entered.set(); release.wait(3)
        with patch.object(lab, 'feed', blocked):
            lab._ensure_receiver()
            try:
                lab.enqueue('[LAB],1000,1,0', time.monotonic())
                self.assertTrue(entered.wait(1))
                for i in range(100):
                    self.responsive(lambda: lab.enqueue(f'[OBS],{i}', time.monotonic()))
                self.assertLessEqual(lab.inbox.qsize(), 64)
                self.assertGreater(lab.inbox_drops, 0)
                state = self.responsive(lab.status)
                self.assertTrue(state['busy'])
                self.assertFalse(state['state']['valid'])
                self.assertTrue(self.responsive(lab.blocking))
                self.responsive(lambda: lab.command('stop'))
                self.assertEqual(writes[-1], b'R\n')
            finally:
                release.set(); lab.shutdown(2)
        self.assertFalse(lab.receiver.is_alive())

    def test_old_queued_status_cannot_confirm_entry_or_refresh_state(self):
        lab = VehicleLab(ProfileStore(self.root/'p.json'), self.root, lambda _: None)
        lab.owned = True
        lab.enqueue('[LAB],1000,1,0', time.monotonic()-.2)
        lab.enqueue('[LAB_MSG] barrier', time.monotonic())
        event = threading.Event(); original = lab.feed
        def feed(line, received):
            result = original(line, received)
            event.set()
            return result
        with patch.object(lab, 'feed', feed):
            lab._ensure_receiver()
            try:
                self.assertTrue(event.wait(1))
                self.assertFalse(lab.active)
                self.assertIsNone(lab.last_received)
                self.assertEqual(lab.inbox_stale, 1)
            finally:
                lab.shutdown()

    def test_clock_probe_is_independent_of_estimator_lock(self):
        written = threading.Event()
        lab = VehicleLab(ProfileStore(self.root/'p.json'), self.root, lambda _: written.set())
        worker = threading.Thread(target=lab._sync_work)
        try:
            with lab.lock:
                worker.start()
                self.assertTrue(written.wait(1.5))
        finally:
            lab.stop_event.set(); worker.join(1)

    def test_slow_camera_detection_cannot_publish_an_expired_frame(self):
        now = [10.]
        frame = np.zeros((16, 16, 3), dtype=np.uint8)
        lab = VehicleLab(ProfileStore(self.root/'p.json'), self.root, lambda _: None,
                         camera=lambda: (1, 10., frame), clock=lambda: now[0])
        def detect(_):
            now[0] = 10.3
            lab.stop_event.set()
            return {}, None, None
        with patch('PyScripts.camera_line_detector.BlackTapeDetector') as detector, \
             patch.object(lab, 'queue_vision') as submit, patch('cv2.imencode') as encode:
            detector.return_value.detect.side_effect = detect
            lab._work()
        self.assertIn('过期', lab.vision['reason'])
        submit.assert_not_called(); encode.assert_not_called()

    def test_slow_console_is_bounded_and_cannot_wait_on_receiver(self):
        output = DiagnosticOutput(capacity=1)
        # Deliberately leave the worker unstarted to simulate blocked console output.
        with patch('threading.Thread.start'):
            self.assertTrue(output.submit('first'))
            self.assertFalse(output.submit('second'))
        self.assertEqual(output.queue.qsize(), 1)
        self.assertEqual(output.drops, 1)

    def test_camera_enqueues_metric_result_before_preview_without_estimator_lock(self):
        frame = np.zeros((16, 16, 3), dtype=np.uint8)
        lab = VehicleLab(ProfileStore(self.root/'p.json'), self.root, lambda _: None,
                         camera=lambda: (1, 10., frame), clock=lambda: 10.01)
        lab.active = lab.owned = True
        request = int(lab.timer.probe(10.)[1:]); lab.timer.reply(request, 1000, 10.004)
        lab.store.profile['camera'] = {'fixture': True}
        observation = VisionObservation(10., 1, 0., 0., (.001, 0., 0., .001), .9)
        reached = threading.Event(); depths = []
        def encode(*_):
            depths.append(lab.inbox.qsize())
            lab.stop_event.set(); reached.set()
            return False, None
        with patch('PyScripts.camera_line_detector.BlackTapeDetector') as detector, \
             patch('PyScripts.camera_line_detector.render_overlay', return_value=frame), \
             patch('PyScripts.vehicle_state.vision.ground_observation', return_value=observation), \
             patch('cv2.imencode', side_effect=encode):
            detector.return_value.detect.return_value = ({'reason': 'valid', 'confidence': .9,
                                                         'near_error_px': 0, 'image_angle_deg': 0}, None, None)
            worker = threading.Thread(target=lab._work, daemon=True)
            try:
                with lab.lock:
                    worker.start()
                    self.assertTrue(reached.wait(.5))
            finally:
                lab.stop_event.set(); worker.join(1)
        self.assertEqual(depths, [1])

    def test_image_directory_creation_is_async_and_queue_stays_bounded(self):
        entered, release = threading.Event(), threading.Event()
        original = Path.mkdir
        target = self.root/'images'
        def mkdir(path, *args, **kwargs):
            if path == target:
                entered.set(); release.wait(3)
            return original(path, *args, **kwargs)
        with patch.object(Path, 'mkdir', mkdir):
            writer = self.responsive(lambda: AsyncImageRecorder(target, queue_size=1))
            try:
                self.assertTrue(entered.wait(1))
                frame = np.zeros((16, 16, 3), dtype=np.uint8)
                self.assertTrue(self.responsive(lambda: writer.submit(frame, 1, 1, 1)))
                self.assertFalse(self.responsive(lambda: writer.submit(frame, 2, 2, 2)))
                self.assertEqual(writer.status()['queue_drops'], 1)
                self.responsive(writer.finish)
            finally:
                release.set(); self.assertTrue(writer.done.wait(2))

    def test_replay_disk_lock_cannot_block_capture_status_or_save_request(self):
        buffer = DiskReplayBuffer(self.root/'cache', min_free_bytes=0)
        entered, release = threading.Event(), threading.Event()
        original = buffer._open_segment
        def blocked(*args):
            entered.set(); release.wait(3)
            return original(*args)
        with patch.object(buffer, '_open_segment', blocked):
            buffer.enable(True)
            try:
                frame = np.zeros((16, 16, 3), dtype=np.uint8)
                self.assertTrue(buffer.submit(frame, 1, time.monotonic_ns(), 1))
                self.assertTrue(entered.wait(1))
                self.assertFalse(self.responsive(lambda: buffer.submit(frame, 2, time.monotonic_ns(), 2)))
                self.assertTrue(self.responsive(buffer.status)['busy'])
                with self.assertRaisesRegex(RuntimeError, '忙'):
                    self.responsive(lambda: buffer.snapshot(time.monotonic_ns()))
                with self.assertRaisesRegex(RuntimeError, '忙'):
                    self.responsive(lambda: buffer.enable(False))
            finally:
                release.set(); buffer.close()
        self.assertFalse(buffer.thread.is_alive())


if __name__ == '__main__':
    unittest.main()
