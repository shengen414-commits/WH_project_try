import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from flask import Flask

from PyScripts.camera_recording import DiskReplayBuffer
from PyScripts.camera_replay import CameraReplay, register_camera_replay_routes


class ReplayHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.buffer = DiskReplayBuffer(self.root / 'cache', min_free_bytes=0)
        self.camera = SimpleNamespace(name='Left', is_active=True, available=True, real_fps=210, replay=self.buffer)
        self.replay = CameraReplay(self.root, lambda: [self.camera])
        app = Flask(__name__)
        register_camera_replay_routes(app, self.replay)
        self.client = app.test_client()

    def tearDown(self):
        self.replay.close()
        self.temp.cleanup()

    def wait_export(self):
        deadline = time.monotonic() + 5
        while self.replay.status()['export']['active'] and time.monotonic() < deadline:
            time.sleep(.01)
        return self.replay.status()['export']

    def test_config_and_save_reject_invalid_or_unavailable_requests(self):
        self.assertFalse(self.client.get('/camera/replay/status').json['enabled'])
        self.assertEqual(self.client.post('/camera/replay/config', json={'enabled': 'true'}).status_code, 400)
        for fps in (None, [], 0, 241, float('nan'), True):
            self.assertEqual(self.client.post('/camera/replay/config', json={'enabled': True, 'fps': fps}).status_code, 409)
        self.assertEqual(self.client.post('/camera/replay/save').status_code, 409)
        self.camera.available = False
        self.assertEqual(self.client.post('/camera/replay/config', json={'enabled': True}).status_code, 409)

    def test_enable_export_download_and_keep_buffer_running(self):
        response = self.client.post('/camera/replay/config', json={'enabled': True})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json['enabled'])
        self.assertEqual(response.json['history_sec'], 60)
        self.assertEqual(response.json['cameras']['Left']['target_fps'], 200)
        self.assertEqual(response.json['cameras']['Left']['history_sec'], 60)
        self.buffer.submit(np.zeros((48, 64, 3), dtype=np.uint8), time.time_ns(), time.monotonic_ns(), 1)
        deadline = time.monotonic() + 3
        while not self.buffer.frames_saved and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertEqual(self.client.post('/camera/replay/save').status_code, 202)
        job = self.wait_export()
        self.assertEqual(job['phase'], 'saved', job)
        self.assertTrue(self.buffer.enabled)
        for filename in job['files']:
            response = self.client.get('/camera/replay/download/' + filename)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.headers['Content-Disposition'].startswith('attachment;'))
            response.close()
        manifest = json.loads(self.replay.download(job['files'][-1]).read_text())
        self.assertEqual(manifest['requested_history_sec'], 60)
        self.assertEqual(manifest['cameras']['Left']['requested_window_sec'], 60)
        self.assertLessEqual(manifest['cameras']['Left']['last_monotonic_ns'], manifest['requested_end_monotonic_ns'])
        self.assertEqual(self.client.post('/camera/replay/config', json={'enabled': False}).status_code, 200)
        self.assertFalse(self.buffer.enabled)

    def test_two_camera_snapshots_are_pinned_before_exporting(self):
        order = []
        cameras = []
        for name in ('Left', 'Right'):
            replay = Mock()
            replay.status.return_value = {'cached_frames': 1, 'enabled': True}
            replay.snapshot.side_effect = lambda end, name=name: order.append(('snapshot', name, end)) or object()
            cameras.append(SimpleNamespace(name=name, replay=replay, is_active=True, available=True, real_fps=210))
        manager = CameraReplay(self.root, lambda: cameras)
        def export(buffer, snapshot, folder, name):
            order.append(('export', name))
            return {'video': name + '.avi', 'index': name + '.index.csv'}
        with patch('PyScripts.camera_replay.export_replay', side_effect=export):
            manager.save()
            deadline = time.monotonic() + 3
            while manager.status()['export']['active'] and time.monotonic() < deadline:
                time.sleep(.005)
        self.assertEqual([item[:2] for item in order], [('snapshot', 'Left'), ('snapshot', 'Right'), ('export', 'Left'), ('export', 'Right')])
        self.assertEqual(order[0][2], order[1][2])
        self.assertEqual(manager.status()['export']['phase'], 'saved')

    def test_duplicate_save_and_download_path_escape_are_blocked(self):
        self.replay.export = {'active': True}
        self.assertEqual(self.client.post('/camera/replay/save').status_code, 409)
        (self.root / 'outside.csv').write_text('private')
        with self.assertRaises(ValueError):
            self.replay.download('../outside.csv')
        self.assertEqual(self.client.get('/camera/replay/download/not-found.avi').status_code, 404)


if __name__ == '__main__':
    unittest.main()
