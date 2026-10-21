"""Exercise real JPEG/AVI decoding and bounded replay storage without cameras."""
import csv
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from PyScripts.camera_recording import AsyncImageRecorder, DiskReplayBuffer, MjpegAviWriter, encode_jpeg, export_replay


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.buffers = []
        self.frame = np.zeros((48, 64, 3), dtype=np.uint8)

    def tearDown(self):
        for buffer in self.buffers:
            buffer.close()
        self.temp.cleanup()

    def buffer(self, **options):
        buffer = DiskReplayBuffer(self.root / 'cache', min_free_bytes=0, **options)
        self.buffers.append(buffer)
        return buffer

    def feed(self, buffer, stamp, frame_id):
        # Wait for the previous write, avoiding scheduler/disk contention in synthetic bursts.
        before = buffer.frames_saved
        deadline = time.monotonic() + 3
        while not buffer.submit(self.frame, 1700000000000000000 + stamp, stamp, frame_id):
            if time.monotonic() > deadline:
                self.fail(f'could not submit frame: {buffer.status()}')
            time.sleep(.002)
        while buffer.frames_saved == before:
            if time.monotonic() > deadline:
                self.fail(f'frame was not written: {buffer.status()}')
            time.sleep(.002)

    def decode(self, path):
        cap = cv2.VideoCapture(str(path))
        try:
            self.assertTrue(cap.isOpened())
            fps = cap.get(cv2.CAP_PROP_FPS)
            frames = []
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                frames.append(frame)
            return fps, frames
        finally:
            cap.release()

    def test_muxer_is_playable_and_index_offsets_point_at_jpegs(self):
        path = self.root / 'test.avi'
        writer = MjpegAviWriter(path, 64, 48, 30)
        jpeg = encode_jpeg(self.frame, 80)
        offsets = [writer.write(jpeg) for _ in range(10)]
        writer.close()
        fps, frames = self.decode(path)
        self.assertEqual(len(frames), 10)
        self.assertAlmostEqual(fps, 30)
        with path.open('rb') as source:
            for offset in offsets:
                source.seek(offset)
                self.assertEqual(source.read(len(jpeg)), jpeg)

    def test_ordinary_recording_writes_incrementally_and_drains_queue(self):
        recorder = AsyncImageRecorder(self.root / 'images')
        self.assertTrue(recorder.submit(self.frame, 100, 200, 1))
        deadline = time.monotonic() + 3
        while not (self.root / 'images' / '100.jpg').exists() and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertTrue((self.root / 'images' / '100.jpg').exists())  # While still recording.
        recorder.submit(self.frame, 101, 201, 2)
        recorder.finish()
        self.assertTrue(recorder.done.wait(3))
        self.assertEqual(recorder.status()['frames_saved'], 2)
        with (recorder.directory / 'frames.index.csv').open(newline='') as file:
            self.assertEqual([row['frame_id'] for row in csv.DictReader(file)], ['1', '2'])
        self.assertTrue(json.loads((recorder.directory / 'recording.json').read_text())['finalized'])

    def test_queue_overflow_and_write_error_are_visible(self):
        with patch('threading.Thread.start'):
            recorder = AsyncImageRecorder(self.root / 'images', queue_size=1)
        recorder.submit(self.frame, 1, 1, 1)
        self.assertFalse(recorder.submit(self.frame, 2, 2, 2))
        self.assertEqual(recorder.status()['queue_drops'], 1)
        recorder.finish()
        with patch('PyScripts.camera_recording.encode_jpeg', side_effect=OSError('disk failed')):
            recorder._run()
        self.assertTrue(recorder.done.is_set())
        self.assertIn('disk failed', recorder.status()['error'])
        self.assertTrue(recorder.queue.empty())

    def test_snapshot_contains_only_past_window_and_does_not_stop_capture(self):
        buffer = self.buffer(history_sec=2, segment_sec=.3)
        buffer.enable(True, fps=10)
        base = time.monotonic_ns() - 2_600_000_000
        for i in range(13):
            self.feed(buffer, base + i * 200_000_000, i)
        end = base + 2_300_000_000
        snapshot = buffer.snapshot(end)
        self.assertTrue(all(part.pins == 1 for part in snapshot.segments))
        # Continue recording well beyond that window; pinned source files must survive pruning.
        original_files = [part.video for part in snapshot.segments]
        self.feed(buffer, time.monotonic_ns() + 3_000_000_000, 99)
        self.assertTrue(all(path.exists() for path in original_files))
        result = export_replay(buffer, snapshot, self.root / 'saved', 'Left')
        with (self.root / 'saved' / result['index']).open(newline='') as file:
            rows = list(csv.DictReader(file))
        ids = [int(row['frame_id']) for row in rows]
        self.assertEqual(ids, list(range(2, 12)))  # Excludes old, post-click and future frames.
        self.assertTrue(all(end - 2_000_000_000 <= int(row['host_receive_monotonic_ns']) <= end for row in rows))
        fps, frames = self.decode(self.root / 'saved' / result['video'])
        self.assertEqual(len(frames), len(ids))
        self.assertAlmostEqual(fps, 5)
        self.assertTrue(buffer.enabled)
        self.assertTrue(all(part.pins == 0 for part in snapshot.segments))

    def test_short_warmup_exports_available_frames(self):
        buffer = self.buffer()
        buffer.enable(True, fps=10)
        now = time.monotonic_ns()
        self.feed(buffer, now, 1)
        self.feed(buffer, now + 100_000_000, 2)
        result = export_replay(buffer, buffer.snapshot(now + 100_000_000), self.root / 'saved', 'Left')
        self.assertEqual(result['frames'], 2)
        self.assertAlmostEqual(result['coverage_sec'], .1)
        self.assertEqual(result['requested_window_sec'], 60)

    def test_byte_budget_and_old_segments_are_pruned(self):
        buffer = self.buffer(history_sec=120, segment_sec=.1, max_bytes=2500)
        buffer.enable(True, fps=10)
        now = time.monotonic_ns()
        for i in range(10):
            self.feed(buffer, now + i * 200_000_000, i)
        with buffer.lock:
            buffer._close_segment()
            buffer._prune(now + 2_000_000_000)
            self.assertLessEqual(sum(part.bytes for part in buffer.segments), 2500)
            self.assertTrue(buffer.limited_by_bytes)
            buffer._prune(now + 123_000_000_000)
            self.assertEqual(buffer.segments, [])
        self.assertEqual(list(buffer.cache_dir.glob('*.avi')), [])

    def test_disabled_buffer_does_not_open_files_or_enqueue(self):
        buffer = self.buffer()
        self.assertFalse(buffer.submit(self.frame, 1, 1, 1))
        self.assertIsNone(buffer.cache_dir)
        self.assertFalse(buffer.cache_root.exists())
        with self.assertRaises(RuntimeError):
            buffer.snapshot(time.monotonic_ns())
        for fps in (0, 241, float('nan')):
            with self.assertRaises(ValueError):
                buffer.enable(True, fps)

    def test_replay_queue_full_is_counted_and_does_not_grow(self):
        buffer = self.buffer(queue_size=1)
        with patch('threading.Thread.start'):
            buffer.enable(True, fps=10)
        now = time.monotonic_ns()
        self.assertTrue(buffer.submit(self.frame, 1, now, 1))
        self.assertFalse(buffer.submit(self.frame, 2, now + 100_000_000, 2))
        self.assertEqual(buffer.queue.qsize(), 1)
        self.assertEqual(buffer.status()['queue_drops'], 1)
        with self.assertRaisesRegex(RuntimeError, '繁忙'):
            buffer.snapshot(now)
        buffer.thread = None  # This deliberately unstarted worker cannot be joined.

    def test_disk_failure_stops_buffer_and_reports_error(self):
        buffer = self.buffer()
        buffer.enable(True, fps=10)
        with patch('PyScripts.camera_recording.encode_jpeg', side_effect=OSError('storage full')):
            buffer.submit(self.frame, 1, time.monotonic_ns(), 1)
            buffer.thread.join(timeout=3)
        self.assertFalse(buffer.enabled)
        self.assertIn('storage full', buffer.status()['error'])
        with self.assertRaisesRegex(RuntimeError, 'storage full'):
            buffer.snapshot(time.monotonic_ns())

    def test_export_directory_failure_releases_pins(self):
        buffer = self.buffer()
        buffer.enable(True)
        now = time.monotonic_ns()
        self.feed(buffer, now, 1)
        snapshot = buffer.snapshot(now)
        (self.root / 'file').write_text('not a directory')
        with self.assertRaises(OSError):
            export_replay(buffer, snapshot, self.root / 'file' / 'saved', 'Left')
        self.assertTrue(all(part.pins == 0 for part in snapshot.segments))

    def test_ten_minutes_of_capture_keeps_only_recent_segment_count(self):
        buffer = self.buffer()
        buffer.enable(True, fps=60)
        base = time.monotonic_ns()
        for i in range(601):
            self.feed(buffer, base + i * 1_000_000_000, i)
        # Advance the capture clock by ten minutes; do not wait ten wall-clock minutes.
        snapshot = buffer.snapshot(base + 600_000_000_000)
        self.assertLessEqual(len(buffer.segments), 32)
        self.assertLessEqual(len(list(buffer.cache_dir.glob('*.avi'))), 32)
        result = export_replay(buffer, snapshot, self.root / 'saved', 'Left')
        self.assertEqual(result['frames'], 61)  # Inclusive endpoints: t=540..600.
        self.assertEqual(result['coverage_sec'], 60)
        buffer.close()
        self.assertFalse(buffer.cache_dir.exists())
        self.assertTrue((self.root / 'saved' / result['video']).exists())

    def test_default_rate_sampling_keeps_200_of_210_capture_frames(self):
        buffer = self.buffer(queue_size=256)
        with patch('threading.Thread.start'):
            buffer.enable(True)
        base = time.monotonic_ns()
        for i in range(210):
            buffer.submit(self.frame, i, base + round(i * 1e9 / 210), i)
        self.assertEqual(buffer.queue.qsize(), 200)
        self.assertEqual(buffer.rate_skips, 10)
        self.assertEqual(buffer.queue_drops, 0)
        buffer.thread = None  # Intentionally unstarted worker.


if __name__ == '__main__':
    unittest.main()
