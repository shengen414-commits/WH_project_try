"""HTTP integration for independent camera replay buffers and background export."""
from datetime import datetime
import json
from pathlib import Path
import threading
import time

from flask import jsonify, request, send_file

try:
    from .camera_recording import DEFAULT_REPLAY_FPS, DEFAULT_REPLAY_HISTORY_SEC, export_replay
except ImportError:
    from camera_recording import DEFAULT_REPLAY_FPS, DEFAULT_REPLAY_HISTORY_SEC, export_replay


class CameraReplay:
    def __init__(self, root, cameras):
        self.root = Path(root).resolve()
        self.cameras = cameras
        self.lock = threading.Lock()
        self.export = {"phase": "idle", "active": False}

    def status(self):
        with self.lock:
            job = dict(self.export)
        cameras = {camera.name: {**camera.replay.status(), "camera_active": camera.is_active,
                                 "camera_available": camera.available, "capture_fps": camera.real_fps}
                   for camera in self.cameras()}
        return {"cameras": cameras, "enabled": any(item['enabled'] for item in cameras.values()),
                "history_sec": DEFAULT_REPLAY_HISTORY_SEC, "export": job}

    def configure(self, enabled, fps=DEFAULT_REPLAY_FPS):
        selected = [camera for camera in self.cameras() if camera.is_active and camera.available]
        if enabled and not selected:
            raise ValueError("请先开启至少一路相机并确认画面正常")
        for camera in self.cameras():
            camera.replay.enable(enabled and camera in selected, fps)
        return self.status()

    def save(self):
        with self.lock:
            if self.export['active']:
                raise ValueError("上一段回放还在保存，请等待完成")
            selected = [camera for camera in self.cameras() if camera.replay.status().get('cached_frames', 0)]
            if not selected:
                raise ValueError("最近一分钟没有缓存，请先开启回放并等待相机帧")
            session = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            end_ns = time.monotonic_ns()
            self.export = {"phase": "saving", "active": True, "session_id": session,
                           "message": "正在保存最近一分钟；回放采集继续运行"}
            job = dict(self.export)

        def run():
            folder = self.root / 'Replays' / session
            manifest = {"requested_end_monotonic_ns": end_ns, "requested_history_sec": DEFAULT_REPLAY_HISTORY_SEC,
                        "cameras": {}, "errors": {}, "timestamp_source": "host read completion; not exposure time"}
            files = []
            try:
                folder.mkdir(parents=True, exist_ok=False)
                snapshots = []
                for camera in selected:
                    try:
                        snapshots.append((camera, camera.replay.snapshot(end_ns)))
                    except Exception as error:
                        manifest['errors'][camera.name] = str(error)
                # Pin both cameras before copying either, preserving the same past window.
                for camera, snapshot in snapshots:
                    try:
                        result = export_replay(camera.replay, snapshot, folder, camera.name)
                        manifest['cameras'][camera.name] = result
                        files.extend([f"{session}/{result['video']}", f"{session}/{result['index']}"])
                    except Exception as error:
                        manifest['errors'][camera.name] = str(error)
                if not manifest['cameras']:
                    raise RuntimeError('; '.join(manifest['errors'].values()))
                (folder / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
                files.append(f"{session}/manifest.json")
                with self.lock:
                    self.export = {**job, "active": False, "phase": "saved" if not manifest['errors'] else "partial",
                                   "files": files, "cameras": manifest['cameras'],
                                   "message": "回放已保存" if not manifest['errors'] else "部分相机保存失败，已保留成功录像",
                                   "errors": manifest['errors']}
            except Exception as error:
                with self.lock:
                    self.export = {**job, "active": False, "phase": "error", "message": str(error), "files": files}
        threading.Thread(target=run, name='replay-export', daemon=True).start()
        return job

    def download(self, filename):
        root = (self.root / 'Replays').resolve()
        path = (root / filename).resolve()
        if not path.is_relative_to(root) or path.suffix.lower() not in {'.avi', '.csv', '.json'} or not path.is_file():
            raise ValueError("请选择有效的已保存回放文件")
        return path

    def close(self):
        for camera in self.cameras():
            camera.replay.close()


def register_camera_replay_routes(app, replay):
    @app.get('/camera/replay/status')
    def camera_replay_status():
        return jsonify(replay.status())

    @app.post('/camera/replay/config')
    def camera_replay_config():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or not isinstance(payload.get('enabled'), bool):
            return jsonify(error='enabled 必须是布尔值'), 400
        try:
            fps = payload.get('fps', DEFAULT_REPLAY_FPS)
            if isinstance(fps, bool):
                raise ValueError('帧率必须是数字')
            return jsonify(replay.configure(payload['enabled'], float(fps)))
        except (ValueError, TypeError, RuntimeError, OSError) as error:
            return jsonify(error=str(error)), 409

    @app.post('/camera/replay/save')
    def camera_replay_save():
        try:
            return jsonify(replay.save()), 202
        except ValueError as error:
            return jsonify(error=str(error)), 409

    @app.get('/camera/replay/download/<path:filename>')
    def camera_replay_download(filename):
        try:
            return send_file(replay.download(filename), as_attachment=True)
        except ValueError as error:
            return jsonify(error=str(error)), 404
