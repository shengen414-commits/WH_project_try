"""Black-tape center detection in image coordinates; no actuator commands.

Several horizontal bands contribute stripe-center candidates. Consensus across
bands rejects isolated dark patches. Angles are image angles, not vehicle yaw.
"""
from dataclasses import dataclass
import math

import cv2
import numpy as np


@dataclass(frozen=True)
class DetectorConfig:
    work_width: int = 320
    roi_top: float = 0.35
    roi_bottom: float = 0.95
    roi_left: float = 0.04
    roi_right: float = 0.96
    target_x: float = 0.5
    lookahead: float = 0.4  # Fraction down the ROI; smaller means farther ahead.
    threshold_mode: str = "otsu"
    threshold: int = 80
    adaptive_block: int = 61
    adaptive_c: int = 12
    min_width: float = 0.008
    max_width: float = 0.18
    min_contrast: float = 12.0
    bands: int = 18
    min_coverage: float = 0.55
    max_angle: float = 60.0

    def validate(self):
        numeric = (self.roi_top, self.roi_bottom, self.roi_left, self.roi_right,
                   self.target_x, self.lookahead, self.min_width, self.max_width,
                   self.min_contrast, self.min_coverage, self.max_angle)
        if not all(math.isfinite(value) for value in numeric):
            raise ValueError("Configuration must contain finite numbers")
        if not 0 <= self.roi_top < self.roi_bottom <= 1 or self.roi_bottom - self.roi_top < 0.2:
            raise ValueError("ROI top/bottom must span at least 20% of the image")
        if not 0 <= self.roi_left < self.roi_right <= 1 or self.roi_right - self.roi_left < 0.2:
            raise ValueError("ROI left/right must span at least 20% of the image")
        if not 0 <= self.target_x <= 1 or not 0.1 <= self.lookahead <= 0.8:
            raise ValueError("Invalid target or lookahead")
        if self.threshold_mode not in {"otsu", "fixed", "adaptive"} or not 0 <= self.threshold <= 255:
            raise ValueError("Invalid threshold mode/value")
        if not 160 <= self.work_width <= 640 or not 8 <= self.bands <= 32:
            raise ValueError("Work width 160..640 and bands 8..32 required")
        if not 0 < self.min_width < self.max_width <= 0.35:
            raise ValueError("Invalid tape width limits")
        if not 1 <= self.min_contrast <= 100 or not 0.3 <= self.min_coverage <= 1:
            raise ValueError("Invalid contrast/coverage")
        if not 5 <= self.max_angle <= 75 or self.adaptive_block < 3 or self.adaptive_block % 2 == 0:
            raise ValueError("Invalid angle/adaptive block")
        return self


def empty_result(reason, width=0, height=0):
    return {"valid": False, "reason": reason, "confidence": 0.0,
            "near_error_px": None, "near_error_norm": None,
            "lookahead_error_norm": None, "image_angle_deg": None,
            "near_xy": None, "lookahead_xy": None, "line_ends": None,
            "points": [], "candidates": [], "coverage": 0.0,
            "residual_px": None, "threshold_value": None,
            "width": width, "height": height, "roi": None}


def to_gray(frame):
    if frame is None or frame.size == 0:
        raise ValueError("Empty camera frame")
    if frame.dtype != np.uint8:
        raise ValueError("Demo expects decoded 8-bit gray/BGR frames; configure the V4L2 format")
    if frame.ndim == 2:
        return frame
    if frame.ndim == 3 and frame.shape[2] == 1:
        return frame[:, :, 0]
    if frame.ndim == 3 and frame.shape[2] in (3, 4):
        return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY if frame.shape[2] == 3 else cv2.COLOR_BGRA2GRAY)
    raise ValueError("Unsupported camera image shape")


class BlackTapeDetector:
    def __init__(self, config=None):
        self.config = (config or DetectorConfig()).validate()

    def detect(self, frame):
        cfg = self.config
        gray = to_gray(frame)
        scale = min(1.0, cfg.work_width / gray.shape[1])
        gray = cv2.resize(gray, (round(gray.shape[1] * scale), round(gray.shape[0] * scale)),
                          interpolation=cv2.INTER_AREA)
        height, width = gray.shape
        if height < 80 or width < 80:
            raise ValueError("Camera image must be at least 80 x 80")
        left, right = round(cfg.roi_left * width), round(cfg.roi_right * width)
        top, bottom = round(cfg.roi_top * height), round(cfg.roi_bottom * height)
        crop = cv2.GaussianBlur(gray[top:bottom, left:right], (3, 3), 0)
        if cfg.threshold_mode == "adaptive":
            mask = cv2.adaptiveThreshold(crop, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                         cv2.THRESH_BINARY_INV, cfg.adaptive_block, cfg.adaptive_c)
            threshold = None
        else:
            flags = cv2.THRESH_BINARY_INV | (cv2.THRESH_OTSU if cfg.threshold_mode == "otsu" else 0)
            threshold, mask = cv2.threshold(crop, cfg.threshold, 255, flags)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        full_mask = np.zeros_like(gray)
        full_mask[top:bottom, left:right] = mask
        result = empty_result("no_line", width, height)
        result.update(roi=[left, top, right, bottom], threshold_value=threshold)

        candidates = []
        centers_y = np.linspace(top + 3, bottom - 4, cfg.bands).astype(int)
        minimum_width, maximum_width = max(2, round(cfg.min_width * width)), round(cfg.max_width * width)
        for band, y in enumerate(centers_y):
            relative_y = y - top
            band_mask = mask[max(0, relative_y - 2):relative_y + 3]
            occupied = np.mean(band_mask > 0, axis=0) >= 0.6
            transitions = np.diff(np.r_[False, occupied, False].astype(np.int8))
            starts, ends = np.flatnonzero(transitions == 1), np.flatnonzero(transitions == -1)
            profile = crop[max(0, relative_y - 2):relative_y + 3].mean(axis=0)
            found = []
            for start, end in zip(starts, ends):
                tape_width = end - start
                if not minimum_width <= tape_width <= maximum_width:
                    continue
                # Both sides must be visible and brighter than this stripe.
                padding = max(3, tape_width // 2)
                before = profile[max(0, start - padding):start]
                after = profile[end:min(len(profile), end + padding)]
                if len(before) < 2 or len(after) < 2:
                    continue
                darkness = float(profile[start:end].mean())
                contrast = min(float(before.mean()), float(after.mean())) - darkness
                if contrast < cfg.min_contrast:
                    continue
                found.append((left + (start + end - 1) / 2, float(y), band, contrast))
            # Bound work on cluttered floors; keep the highest-contrast candidates.
            candidates.extend(sorted(found, key=lambda point: point[3], reverse=True)[:6])
        result["candidates"] = [[p[0], p[1]] for p in candidates]
        if len(candidates) < math.ceil(cfg.bands * cfg.min_coverage):
            return result, full_mask, gray

        points = np.asarray(candidates, dtype=np.float64)
        tolerance = max(2.5, width * 0.012)
        roi_height = bottom - top
        # Use widely separated bands for hypotheses, rather than fitting edges
        # of the tape or allowing a single dark patch to dominate its centroid.
        pairs = [(i, j) for i in range(len(points)) for j in range(i + 1, len(points))
                 if abs(points[j, 1] - points[i, 1]) >= roi_height * 0.35]
        if len(pairs) > 240:
            rng = np.random.default_rng(7)
            pairs = [pairs[i] for i in rng.choice(len(pairs), 240, replace=False)]
        hypotheses = []
        if pairs:
            pair_array = np.asarray(pairs)
            first, second = points[pair_array[:, 0]], points[pair_array[:, 1]]
            slopes = (second[:, 0] - first[:, 0]) / (second[:, 1] - first[:, 1])
            allowed = np.abs(np.degrees(np.arctan(slopes))) <= cfg.max_angle
            intercepts = (first[:, 0] - slopes * first[:, 1])[allowed]
            slopes = slopes[allowed]
            distances = np.abs(points[None, :, 0] - (slopes[:, None] * points[None, :, 1] + intercepts[:, None]))
            closest = np.zeros((len(slopes), cfg.bands), dtype=int)
            good = np.zeros((len(slopes), cfg.bands), dtype=bool)
            errors = np.zeros((len(slopes), cfg.bands))
            # Vectorize over hypotheses; only the small fixed band loop remains.
            for band in range(cfg.bands):
                eligible = np.flatnonzero(points[:, 2] == band)
                if not len(eligible) or not len(slopes):
                    continue
                choices = eligible[np.argmin(distances[:, eligible], axis=1)]
                nearest = distances[np.arange(len(slopes)), choices]
                closest[:, band] = choices
                good[:, band] = nearest <= tolerance
                errors[:, band] = nearest
            counts = good.sum(axis=1)
            for hypothesis in np.flatnonzero(counts >= math.ceil(cfg.bands * cfg.min_coverage)):
                indices = closest[hypothesis, good[hypothesis]].tolist()
                selected = points[indices]
                span = float(np.ptp(selected[:, 1])) / roi_height
                if span < 0.55 or selected[:, 1].max() < top + roi_height * 0.8:
                    continue
                coverage = counts[hypothesis] / cfg.bands
                residual = float(errors[hypothesis, good[hypothesis]].mean())
                score = coverage + 0.2 * span - 0.1 * residual / tolerance
                hypotheses.append((score, slopes[hypothesis], intercepts[hypothesis], indices, residual, coverage))
        if not hypotheses:
            result["reason"] = "insufficient_straight_support"
            return result, full_mask, gray
        hypotheses.sort(key=lambda hypothesis: hypothesis[0], reverse=True)
        best = hypotheses[0]
        near_y = float(centers_y[-1])
        # Similar support for two separate lines is genuinely ambiguous.
        for alternative in hypotheses[1:]:
            near_separation = abs((best[1] - alternative[1]) * near_y + best[2] - alternative[2])
            if alternative[0] >= best[0] - 0.08 and near_separation > width * 0.1:
                result["reason"] = "ambiguous_lines"
                return result, full_mask, gray
        selected = points[best[3]]
        a, b = np.polyfit(selected[:, 1], selected[:, 0], 1)
        residual = float(np.sqrt(np.mean((selected[:, 0] - (a * selected[:, 1] + b)) ** 2)))
        if residual > tolerance or abs(math.degrees(math.atan(a))) > cfg.max_angle:
            result["reason"] = "poor_fit"
            return result, full_mask, gray
        near_x = float(a * near_y + b)
        ahead_y = float(top + cfg.lookahead * roi_height)
        # Do not invent a far lookahead position without observations there.
        if selected[:, 1].min() > ahead_y:
            result["reason"] = "no_far_support"
            return result, full_mask, gray
        ahead_x = float(a * ahead_y + b)
        target = cfg.target_x * width
        confidence = float(np.clip(best[5] * math.exp(-residual / tolerance), 0, 1))
        result.update(valid=True, reason="ok", confidence=confidence,
                      near_error_px=near_x - target, near_error_norm=(near_x - target) / (width / 2),
                      lookahead_error_norm=(ahead_x - target) / (width / 2),
                      image_angle_deg=float(math.degrees(math.atan(-a))),
                      near_xy=[near_x, near_y], lookahead_xy=[ahead_x, ahead_y],
                      line_ends=[[float(a * selected[:, 1].min() + b), float(selected[:, 1].min())],
                                 [float(a * selected[:, 1].max() + b), float(selected[:, 1].max())]],
                      points=selected[:, :2].tolist(), coverage=best[5], residual_px=residual)
        return result, full_mask, gray


def render_overlay(frame, result, config):
    """Draw in source resolution; diagnostic numbers remain processing pixels."""
    gray = to_gray(frame)
    overlay = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    height, width = gray.shape
    sx, sy = width / result["width"], height / result["height"]
    def xy(point):
        return round(point[0] * sx), round(point[1] * sy)
    roi = result["roi"]
    cv2.rectangle(overlay, xy(roi[:2]), xy(roi[2:]), (120, 160, 160), 1)
    target = round(config.target_x * width)
    cv2.line(overlay, (target, round(roi[1] * sy)), (target, round(roi[3] * sy)), (255, 190, 60), 1)
    for point in result["candidates"]:
        cv2.circle(overlay, xy(point), 2, (100, 100, 255), -1)
    if result["valid"]:
        cv2.line(overlay, xy(result["line_ends"][0]), xy(result["line_ends"][1]), (60, 255, 80), 3)
        for point in result["points"]:
            cv2.circle(overlay, xy(point), 3, (0, 255, 255), -1)
        near, ahead = xy(result["near_xy"]), xy(result["lookahead_xy"])
        cv2.circle(overlay, near, 7, (30, 255, 80), 2)
        cv2.circle(overlay, ahead, 7, (255, 100, 255), 2)
        cv2.line(overlay, (target, near[1]), near, (30, 255, 80), 2)
        message = f"dx={result['near_error_norm']:+.3f} angle={result['image_angle_deg']:+.1f} deg"
    else:
        message = "INVALID: " + result["reason"]
    cv2.rectangle(overlay, (0, 0), (width, 50), (20, 20, 20), -1)
    cv2.putText(overlay, message, (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (240, 240, 240), 1)
    cv2.putText(overlay, f"confidence={result['confidence']:.2f} | DEMO: no steering output",
                (8, 41), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (210, 210, 210), 1)
    return overlay


def synthetic_frame(seconds, width=640, height=480):
    """Clearly synthetic moving tape for preview/testing without a camera."""
    xx, yy = np.meshgrid(np.arange(width), np.arange(height))
    background = 180 + 25 * xx / width + 8 * np.sin(xx / 19) + 5 * yy / height
    frame = np.uint8(np.clip(background, 0, 255))
    near_x = width * (0.5 + 0.2 * math.sin(seconds * 0.7))
    far_x = near_x + width * 0.15 * math.sin(seconds * 0.5)
    top, bottom = round(height * 0.25), height - 1
    polygon = np.array([[far_x - 7, top], [far_x + 7, top],
                        [near_x + 18, bottom], [near_x - 18, bottom]], np.int32)
    cv2.fillPoly(frame, [polygon], 20)
    cv2.putText(frame, "SIMULATED CAMERA", (12, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.6, 50, 2)
    return frame
