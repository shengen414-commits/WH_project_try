"""OV9281/V4L2 black-tape demo, visible in a browser without an OpenCV GUI.

python PyScripts/camera_line_demo.py --camera /dev/video0
python PyScripts/camera_line_demo.py --demo
"""
import argparse
from dataclasses import asdict, replace
import io
import json
import math
from pathlib import Path
import sys
import threading
import time

import cv2
from flask import Flask, Response, jsonify, render_template, request, send_file

from camera_line_detector import (
    BlackTapeDetector, DetectorConfig, empty_result, render_overlay, synthetic_frame,
)


class LatestCamera:
    """A dedicated reader replaces a single slot; detection never queues frames."""
    def __init__(self, source, width, height, fps, fourcc, backend="auto"):
        if source.isdecimal():
            source = int(source)
        api = {"auto": cv2.CAP_V4L2 if sys.platform.startswith("linux") else cv2.CAP_ANY,
               "v4l2": cv2.CAP_V4L2, "any": cv2.CAP_ANY}[backend]
        self.cap = cv2.VideoCapture(source, api)
        if not self.cap.isOpened():
            self.cap.release()
            raise RuntimeError(f"Cannot open camera {source}; check /dev/video*, permissions and other camera processes")
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_FPS, fps)
        # Best effort only. Reader remains independent of detection/HTTP speed.
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        code = int(self.cap.get(cv2.CAP_PROP_FOURCC))
        self.info = {"source": str(source), "width": self.cap.get(cv2.CAP_PROP_FRAME_WIDTH),
                     "height": self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT),
                     "reported_fps": self.cap.get(cv2.CAP_PROP_FPS),
                     "fourcc": "".join(chr((code >> (8 * i)) & 255) for i in range(4)),
                     "backend": self.cap.getBackendName()}
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.frame = None
        self.sequence = 0
        self.read_at = 0.0
        self.capture_fps = 0.0
        self.error = None
        self.thread = threading.Thread(target=self._read, name="VisionCamera", daemon=True)
        self.thread.start()

    def _read(self):
        frames = 0
        started = time.monotonic()
        try:
            while not self.stop_event.is_set():
                ok, frame = self.cap.read()
                now = time.monotonic()
                if not ok:
                    with self.lock:
                        self.error = "camera_read_failed"
                    self.stop_event.wait(0.03)
                    continue
                frames += 1
                with self.lock:
                    self.frame = frame
                    self.sequence += 1
                    self.read_at = now
                    self.error = None
                    if now - started >= 1:
                        self.capture_fps = frames / (now - started)
                        frames, started = 0, now
        except Exception as error:
            with self.lock:
                self.error = str(error)
        finally:
            self.cap.release()

    def latest(self):
        with self.lock:
            return self.sequence, self.read_at, self.frame, self.capture_fps, self.error

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=2)


class VisionSession:
    def __init__(self, config, camera=None, demo=False, preview_fps=12, max_fps=120):
        self.config = config.validate()
        self.camera = camera
        self.demo = demo
        self.preview_fps = preview_fps
        self.max_fps = max_fps
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.result = empty_result("waiting_for_frame")
        self.jpeg = {}
        self.output_sequence = 0
        self.processed_at = 0.0
        self.read_at = 0.0
        self.processing_ms = 0.0
        self.processing_fps = 0.0
        self.capture_fps = 0.0
        self.skipped_frames = 0
        self.last_error = None
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._process, name="VisionDetector", daemon=True)
        self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)
        if self.camera:
            self.camera.close()

    def status(self):
        with self.lock:
            result = dict(self.result)
            now = time.monotonic()
            age = now - self.read_at if self.read_at else None
            camera_error = self.camera.latest()[-1] if self.camera else None
            if age is None or age > 0.25 or camera_error:
                result.update(valid=False, reason=camera_error or "frame_stale", confidence=0.0,
                              near_error_px=None, near_error_norm=None,
                              lookahead_error_norm=None, image_angle_deg=None)
            return {"detection": result, "config": asdict(self.config), "demo": self.demo,
                    "camera": self.camera.info if self.camera else {"source": "SIMULATED"},
                    "frame_age_ms": age * 1000 if age is not None else None,
                    "processing_ms": self.processing_ms, "processing_fps": self.processing_fps,
                    "capture_fps": self.capture_fps, "skipped_frames": self.skipped_frames,
                    "sequence": self.output_sequence, "last_error": self.last_error}

    def configure(self, values):
        allowed = {"roi_top", "roi_bottom", "target_x", "lookahead", "threshold_mode", "threshold"}
        if not isinstance(values, dict) or not values or set(values) - allowed:
            raise ValueError("Unsupported configuration fields")
        converted = {}
        for key, value in values.items():
            if key == "threshold_mode":
                if not isinstance(value, str):
                    raise ValueError("threshold_mode must be a string")
                converted[key] = value
            else:
                if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
                    raise ValueError(f"{key} must be a finite number")
                converted[key] = int(value) if key == "threshold" else float(value)
        with self.lock:
            config = replace(self.config, **converted).validate()
            self.config = config
            return asdict(config)

    def image(self, kind):
        with self.lock:
            return self.output_sequence, self.jpeg.get(kind)

    def _process(self):
        last_input_sequence = 0
        start = stat_at = time.monotonic()
        last_preview = 0
        frames = 0
        try:
            while not self.stop_event.is_set():
                loop_at = time.monotonic()
                if self.demo:
                    input_sequence = last_input_sequence + 1
                    read_at = loop_at
                    frame = synthetic_frame(loop_at - start)
                    capture_fps = self.max_fps
                else:
                    input_sequence, read_at, frame, capture_fps, error = self.camera.latest()
                    if frame is None or input_sequence == last_input_sequence or error:
                        self.stop_event.wait(0.002)
                        continue
                skipped = max(0, input_sequence - last_input_sequence - 1) if last_input_sequence else 0
                last_input_sequence = input_sequence
                with self.lock:
                    config = self.config
                detector = BlackTapeDetector(config)
                detection_at = time.monotonic()
                result, mask, gray = detector.detect(frame)
                processing_ms = (time.monotonic() - detection_at) * 1000
                frames += 1
                now = time.monotonic()
                jpegs = None
                if now - last_preview >= 1 / self.preview_fps:
                    annotated = render_overlay(frame, result, config)
                    display_width = min(frame.shape[1], 800)
                    annotated = cv2.resize(annotated, (display_width, round(frame.shape[0] * display_width / frame.shape[1])))
                    jpegs = {}
                    for kind, image in (("overlay", annotated), ("mask", mask)):
                        ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 80])
                        if ok:
                            jpegs[kind] = encoded.tobytes()
                    last_preview = now
                with self.lock:
                    self.result = result
                    self.read_at = read_at
                    self.processed_at = now
                    self.processing_ms = processing_ms
                    if not self.demo:
                        self.capture_fps = capture_fps
                    self.skipped_frames += skipped
                    if now - stat_at >= 1:
                        self.processing_fps = frames / (now - stat_at)
                        if self.demo:
                            self.capture_fps = self.processing_fps
                        stat_at, frames = now, 0
                    if jpegs is not None:
                        self.jpeg = jpegs
                        self.output_sequence += 1
                self.stop_event.wait(max(0, 1 / self.max_fps - (time.monotonic() - loop_at)))
        except Exception as error:
            with self.lock:
                self.last_error = str(error)
                self.result = empty_result("processing_failed")


def create_app(session):
    app = Flask(__name__)

    @app.get("/")
    def index():
        return render_template("camera_line_demo.html")

    @app.get("/status")
    def status():
        return jsonify(session.status())

    @app.post("/config")
    def config():
        try:
            return jsonify(config=session.configure(request.get_json(silent=True)))
        except (ValueError, TypeError) as error:
            return jsonify(error=str(error)), 400

    @app.get("/stream/<kind>")
    def stream(kind):
        if kind not in {"overlay", "mask"}:
            return jsonify(error="Unknown stream"), 404
        def generate():
            previous = -1
            while not session.stop_event.is_set():
                sequence, jpeg = session.image(kind)
                if jpeg and sequence != previous:
                    previous = sequence
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
                session.stop_event.wait(1 / session.preview_fps)
        return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")

    @app.get("/snapshot")
    def snapshot():
        sequence, jpeg = session.image("overlay")
        if jpeg is None:
            return jsonify(error="No preview available yet"), 503
        return send_file(io.BytesIO(jpeg), mimetype="image/jpeg", as_attachment=True,
                         download_name=f"camera_line_{sequence}.jpg")

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera", default="/dev/video0" if sys.platform.startswith("linux") else "0")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--fps", type=float, default=120, help="requested capture rate; hardware may negotiate another rate")
    parser.add_argument("--fourcc", default="MJPG", help="e.g. MJPG, YUYV or GREY; check v4l2-ctl")
    parser.add_argument("--backend", choices=["auto", "v4l2", "any"], default="auto")
    parser.add_argument("--work-width", type=int, default=320)
    parser.add_argument("--roi-top", type=float, default=0.35)
    parser.add_argument("--roi-bottom", type=float, default=0.95)
    parser.add_argument("--target-x", type=float, default=0.5)
    parser.add_argument("--lookahead", type=float, default=0.4)
    parser.add_argument("--threshold-mode", choices=["otsu", "fixed", "adaptive"], default="otsu")
    parser.add_argument("--threshold", type=int, default=80)
    parser.add_argument("--min-width", type=float, default=0.008)
    parser.add_argument("--max-width", type=float, default=0.18)
    parser.add_argument("--min-contrast", type=float, default=12)
    parser.add_argument("--preview-fps", type=float, default=12)
    parser.add_argument("--max-process-fps", type=float, default=120)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5080)
    parser.add_argument("--demo", action="store_true", help="use clearly marked synthetic frames without camera hardware")
    parser.add_argument("--image", type=Path, help="detect once in a saved camera photo")
    parser.add_argument("--output", type=Path, default=Path("Car_Records/Vision_Debug"))
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or not 1 <= args.preview_fps <= 30 or not 1 <= args.max_process_fps <= 240:
        parser.error("Invalid port or processing/preview frame rate")
    if not 1 <= args.fps <= 500 or len(args.fourcc) != 4 or not 80 <= args.width <= 4096 or not 80 <= args.height <= 4096:
        parser.error("Invalid camera dimensions, FPS or FOURCC")
    config = DetectorConfig(work_width=args.work_width, roi_top=args.roi_top, roi_bottom=args.roi_bottom,
                            target_x=args.target_x, lookahead=args.lookahead, threshold_mode=args.threshold_mode,
                            threshold=args.threshold, min_width=args.min_width, max_width=args.max_width,
                            min_contrast=args.min_contrast).validate()
    if args.image:
        frame = cv2.imread(str(args.image), cv2.IMREAD_UNCHANGED)
        result, mask, gray = BlackTapeDetector(config).detect(frame)
        args.output.mkdir(parents=True, exist_ok=True)
        overlay = render_overlay(frame, result, config)
        for name, image in (("overlay.png", overlay), ("mask.png", mask)):
            if not cv2.imwrite(str(args.output / name), image):
                raise RuntimeError(f"Cannot save {name}")
        (args.output / "detection.json").write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
        print(f"Detection: {result['reason']}; files saved to {args.output.resolve()}")
        return
    camera = None if args.demo else LatestCamera(args.camera, args.width, args.height, args.fps, args.fourcc, args.backend)
    session = VisionSession(config, camera, args.demo, args.preview_fps, args.max_process_fps)
    if camera:
        print("Camera negotiated:", json.dumps(camera.info))
    else:
        print("SIMULATED CAMERA: visual preview only")
    session.start()
    print(f"Open http://<OrangePi-IP>:{args.port} in your browser. No motor or serial output.")
    try:
        create_app(session).run(host=args.host, port=args.port, threaded=True, debug=False, use_reloader=False)
    finally:
        session.close()


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, cv2.error) as error:
        raise SystemExit(str(error)) from error
