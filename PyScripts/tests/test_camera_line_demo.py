import math
from pathlib import Path
import sys
import time
import unittest

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from camera_line_detector import BlackTapeDetector, DetectorConfig, render_overlay
from camera_line_demo import VisionSession, create_app


def tape_image(near_x=320, far_x=320, width=640, height=480):
    frame = np.full((height, width), 190, np.uint8)
    top, bottom = 0, height - 1
    points = np.array([[far_x - 10, top], [far_x + 10, top],
                       [near_x + 16, bottom], [near_x - 16, bottom]], np.int32)
    cv2.fillPoly(frame, [points], 20)
    return frame


class CameraDetectorTests(unittest.TestCase):
    def test_centered_black_tape(self):
        result, mask, gray = BlackTapeDetector().detect(tape_image())
        self.assertTrue(result["valid"], result["reason"])
        self.assertLess(abs(result["near_error_norm"]), 0.01)
        self.assertLess(abs(result["image_angle_deg"]), 0.5)
        self.assertGreater(result["confidence"], 0.9)
        self.assertEqual(mask.shape, gray.shape)

    def test_offset_sign_and_calibrated_target(self):
        detector = BlackTapeDetector()
        left, _, _ = detector.detect(tape_image(180, 180))
        right, _, _ = detector.detect(tape_image(460, 460))
        self.assertLess(left["near_error_norm"], -0.4)
        self.assertGreater(right["near_error_norm"], 0.4)
        calibrated, _, _ = BlackTapeDetector(DetectorConfig(target_x=460/640)).detect(tape_image(460, 460))
        self.assertLess(abs(calibrated["near_error_norm"]), 0.01)

    def test_heading_is_separate_from_lateral_position(self):
        result, _, _ = BlackTapeDetector().detect(tape_image(320, 440))
        self.assertTrue(result["valid"], result["reason"])
        self.assertGreater(result["image_angle_deg"], 10)
        self.assertGreater(result["lookahead_error_norm"], result["near_error_norm"])

    def test_no_line_is_not_zero_error(self):
        for gray in (0, 40, 180, 255):
            result, _, _ = BlackTapeDetector().detect(np.full((480, 640), gray, np.uint8))
            self.assertFalse(result["valid"])
            self.assertIsNone(result["near_error_norm"])

    def test_small_patch_and_broad_shadow_are_rejected(self):
        for rectangle in ((280, 330, 320, 365), (150, 0, 440, 479)):
            frame = np.full((480, 640), 190, np.uint8)
            cv2.rectangle(frame, rectangle[:2], rectangle[2:], 20, -1)
            result, _, _ = BlackTapeDetector().detect(frame)
            self.assertFalse(result["valid"])

    def test_two_parallel_tapes_are_ambiguous(self):
        frame = np.minimum(tape_image(200, 200), tape_image(440, 440))
        result, _, _ = BlackTapeDetector().detect(frame)
        self.assertFalse(result["valid"])
        self.assertEqual(result["reason"], "ambiguous_lines")

    def test_horizontal_line_is_rejected(self):
        frame = np.full((480, 640), 190, np.uint8)
        cv2.rectangle(frame, (0, 300), (639, 330), 20, -1)
        result, _, _ = BlackTapeDetector().detect(frame)
        self.assertFalse(result["valid"])

    def test_partial_occlusion_and_outlier_patch(self):
        frame = tape_image(350, 300)
        frame[250:275, :] = 190
        cv2.rectangle(frame, (130, 300), (155, 320), 10, -1)
        result, _, _ = BlackTapeDetector().detect(frame)
        self.assertTrue(result["valid"], result["reason"])
        self.assertGreater(result["near_error_norm"], 0)

    def test_gray_and_bgr_same_result(self):
        frame = tape_image(350, 300)
        gray, _, _ = BlackTapeDetector().detect(frame)
        color, _, _ = BlackTapeDetector().detect(cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR))
        self.assertEqual(gray, color)
        self.assertEqual(render_overlay(frame, gray, DetectorConfig()).shape, (480, 640, 3))

    def test_uneven_light_with_adaptive_threshold(self):
        frame = tape_image(320, 350).astype(float)
        frame *= np.linspace(0.5, 1.0, frame.shape[1])[None, :]
        result, _, _ = BlackTapeDetector(DetectorConfig(threshold_mode="adaptive")).detect(frame.astype(np.uint8))
        self.assertTrue(result["valid"], result["reason"])

    def test_fixed_threshold_mode(self):
        result, _, _ = BlackTapeDetector(DetectorConfig(threshold_mode="fixed", threshold=80)).detect(tape_image())
        self.assertTrue(result["valid"])
        self.assertEqual(result["threshold_value"], 80)

    def test_bad_configuration_and_raw_format(self):
        for config in (DetectorConfig(roi_top=0.99), DetectorConfig(target_x=math.nan),
                       DetectorConfig(min_width=0.3, max_width=0.1)):
            with self.assertRaises(ValueError):
                BlackTapeDetector(config)
        with self.assertRaises(ValueError):
            BlackTapeDetector().detect(np.zeros((480, 640), np.uint16))


class CameraWebTests(unittest.TestCase):
    def setUp(self):
        self.session = VisionSession(DetectorConfig(), demo=True)
        self.client = create_app(self.session).test_client()

    def test_page_runs_without_opencv_gui(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"/stream/overlay", response.data)

    def test_config_validation_does_not_replace_good_config(self):
        response = self.client.post("/config", json={"roi_top": 0.99})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.session.config.roi_top, 0.35)
        self.assertEqual(self.client.post("/config", json={"threshold": "80"}).status_code, 400)
        self.assertEqual(self.client.post("/config", json={"steering": 1800}).status_code, 400)
        response = self.client.post("/config", json={"threshold_mode": "fixed", "threshold": 65})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.session.config.threshold, 65)

    def test_stale_camera_does_not_offer_valid_error(self):
        result, _, _ = BlackTapeDetector().detect(tape_image())
        self.session.result = result
        self.session.read_at = time.monotonic() - 1
        status = self.client.get("/status").get_json()
        self.assertFalse(status["detection"]["valid"])
        self.assertIsNone(status["detection"]["near_error_norm"])
        self.assertEqual(status["detection"]["reason"], "frame_stale")

    def test_snapshot_and_unknown_stream(self):
        self.assertEqual(self.client.get("/snapshot").status_code, 503)
        self.session.jpeg["overlay"] = b"jpeg bytes"
        response = self.client.get("/snapshot")
        self.assertEqual(response.data, b"jpeg bytes")
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertEqual(self.client.get("/stream/unknown").status_code, 404)


if __name__ == "__main__":
    unittest.main()
