"""Black-tape center detection in image coordinates; no actuator commands.

Horizontal bands contribute globally thresholded/local-contrast candidates.
Bounded continuous paths support straight tape and moderate curves, reject
ambiguous branches and avoid far extrapolation. No temporal hold-last tracking.
Angles are image tangents at lookahead, not vehicle yaw.
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
    max_width: float = 0.32
    min_contrast: float = 12.0
    bands: int = 18
    min_coverage: float = 0.55
    max_angle: float = 60.0
    max_curve_norm: float = 0.10  # Maximum quadratic bend / image width, not road curvature.
    max_gap_bands: int = 3

    def validate(self):
        for value in (self.work_width, self.threshold, self.adaptive_block, self.adaptive_c, self.bands, self.max_gap_bands):
            if type(value) is not int:
                raise ValueError('Pixel/count settings must be integers')
        numeric = (self.roi_top, self.roi_bottom, self.roi_left, self.roi_right,
                   self.target_x, self.lookahead, self.min_width, self.max_width,
                   self.min_contrast, self.min_coverage, self.max_angle, self.max_curve_norm)
        if not all(type(value) in (int, float) and math.isfinite(value) for value in numeric):
            raise ValueError("Configuration must contain finite numbers")
        if not 0 <= self.roi_top < self.roi_bottom <= 1 or self.roi_bottom - self.roi_top < 0.2:
            raise ValueError("ROI top/bottom must span at least 20% of the image")
        if not 0 <= self.roi_left < self.roi_right <= 1 or self.roi_right - self.roi_left < 0.2:
            raise ValueError("ROI left/right must span at least 20% of the image")
        if not 0 <= self.target_x <= 1 or not 0.1 <= self.lookahead <= 0.8:
            raise ValueError("Invalid target or lookahead")
        if type(self.threshold_mode) is not str or self.threshold_mode not in {"otsu", "fixed", "adaptive"} or not 0 <= self.threshold <= 255:
            raise ValueError("Invalid threshold mode/value")
        if not 160 <= self.work_width <= 640 or not 8 <= self.bands <= 32:
            raise ValueError("Work width 160..640 and bands 8..32 required")
        if not 0 < self.min_width < self.max_width <= 0.35:
            raise ValueError("Invalid tape width limits")
        if not 1 <= self.min_contrast <= 100 or not 0.3 <= self.min_coverage <= 1:
            raise ValueError("Invalid contrast/coverage")
        if not 5 <= self.max_angle <= 75 or not 3 <= self.adaptive_block <= 255 or self.adaptive_block % 2 == 0 or abs(self.adaptive_c) > 100:
            raise ValueError("Invalid angle/adaptive block")
        if not 0 <= self.max_curve_norm <= .2 or not 0 <= self.max_gap_bands <= 4:
            raise ValueError('Invalid curve or occlusion limit')
        return self


def empty_result(reason, width=0, height=0):
    return {"valid": False, "reason": reason, "confidence": 0.0,
            "near_error_px": None, "near_error_norm": None,
            "lookahead_error_norm": None, "image_angle_deg": None,
            "near_xy": None, "lookahead_xy": None, "line_ends": None,
            "points": [], "candidates": [], "coverage": 0.0,
            "residual_px": None, "threshold_value": None,
            "width": width, "height": height, "roi": None,
            "model": None, "centerline": [], "curve_norm": None,
            "max_gap_bands": None, "threshold_methods": []}


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

    def _paths(self, points, centers_y, width):
        """Bounded beam search through row candidates; no previous-frame state.

        Each state is (candidate indices bottom-to-top, total cost, missing rows).
        Smooth paths can skip short occlusions without joining unrelated patches.
        """
        cfg = self.config
        beam = [((), 0., 0)]
        finished = []
        per_band = [np.flatnonzero(points[:,2] == band) for band in range(len(centers_y))]
        beam_limit = min(24, max(6, 4*max(map(len,per_band))))
        max_slope = math.tan(math.radians(cfg.max_angle))
        scale = max(4., width * .025)
        for band in range(len(centers_y)-1, -1, -1):
            candidates = per_band[band]
            next_states = []
            for indices, cost, gap in beam:
                for index in candidates:
                    point = points[index]
                    extra = 0.
                    if indices:
                        last = points[indices[-1]]
                        slope = (point[0]-last[0])/(point[1]-last[1])
                        if abs(slope) > max_slope:
                            continue
                        previous_slope = 0.
                        if len(indices) > 1:
                            previous = points[indices[-2]]
                            previous_slope = (last[0]-previous[0])/(last[1]-previous[1])
                            if abs(slope-previous_slope) > .8*(gap+1):
                                continue
                        predicted = last[0]+previous_slope*(point[1]-last[1])
                        extra = .25*((point[0]-predicted)/scale)**2
                        extra += .2*abs(math.log(point[4]/last[4]))
                    next_states.append((indices+(int(index),), cost+extra, 0))
                if indices:
                    if gap < cfg.max_gap_bands:
                        next_states.append((indices, cost+1., gap+1))
                    else:
                        finished.append((indices, cost, gap))
                elif len(centers_y)-1-band < 1:
                    next_states.append((indices, cost+1., gap+1))
            # Eliminate skip-history duplicates sharing the same recent geometry.
            next_states.sort(key=lambda s: len(s[0])-.25*s[1], reverse=True)
            unique = set()
            beam = []
            for state in next_states:
                key = (state[0][:1], state[0][-3:])
                if key not in unique:
                    unique.add(key); beam.append(state)
                    if len(beam) == beam_limit:
                        break
            if not beam:
                break
        ranked = sorted(beam+finished, key=lambda s: len(s[0])-.25*s[1], reverse=True)
        unique = set()
        paths = []
        bin_width = max(4., width*.015)
        roi_height = centers_y[-1]-centers_y[0]+7
        sample_y = [centers_y[-1], centers_y[0]-3+cfg.lookahead*roi_height,
                    centers_y[0]+.2*roi_height]
        for indices, cost, _ in ranked:
            if len(indices) < math.ceil(cfg.bands*cfg.min_coverage):
                continue
            selected = points[list(reversed(indices))]
            signature = tuple(np.rint(np.interp(sample_y,selected[:,1],selected[:,0])/bin_width).astype(int))
            if signature not in unique:
                unique.add(signature); paths.append((indices, cost))
                if len(paths) == 12:
                    break
        return paths

    def _fit(self, points, centers_y, roi_height, width, tolerance):
        """Robust linear/quadratic fit with bounded support and no far extrapolation."""
        cfg = self.config
        minimum = math.ceil(cfg.bands*cfg.min_coverage)
        if len(points) < minimum:
            return None
        points = points[np.argsort(points[:, 1])]
        near_y = float(centers_y[-1])
        spacing = float(centers_y[1]-centers_y[0])
        if near_y-points[-1, 1] > spacing*1.5 or np.ptp(points[:, 1]) < .55*roi_height:
            return None
        ahead_y = float(centers_y[0]-3+cfg.lookahead*roi_height)
        if not points[0, 1] <= ahead_y <= points[-1, 1]:
            return None
        t = (points[:, 1]-near_y)/roi_height
        weights = np.sqrt(np.clip(points[:, 3]/cfg.min_contrast, 1., 4.))
        quadratic = np.polyfit(t, points[:, 0], 2, w=weights)
        errors = points[:, 0]-np.polyval(quadratic, t)
        mad = float(np.median(np.abs(errors-np.median(errors))))
        keep = np.abs(errors) <= max(tolerance, 3*1.4826*mad)
        if keep.sum() < minimum:
            return None
        all_kept = bool(keep.all())
        points, t, weights = points[keep], t[keep], weights[keep]
        gaps = np.diff(points[:, 2]).astype(int)-1
        if gaps.size and gaps.max() > cfg.max_gap_bands:
            return None
        if points[0, 1] > ahead_y or near_y-points[-1, 1] > spacing*1.5 or np.ptp(points[:, 1]) < .55*roi_height:
            return None
        linear = np.polyfit(t, points[:, 0], 1, w=weights)
        if not all_kept:
            quadratic = np.polyfit(t, points[:, 0], 2, w=weights)
        residual_linear = float(np.sqrt(np.mean((points[:, 0]-np.polyval(linear, t))**2)))
        residual_curve = float(np.sqrt(np.mean((points[:, 0]-np.polyval(quadratic, t))**2)))
        curve_norm = abs(float(quadratic[0]))/(4*width)
        use_curve = residual_linear > .75 and residual_curve < residual_linear*.65 and 0 < curve_norm <= cfg.max_curve_norm
        model = quadratic if use_curve else linear
        residual = residual_curve if use_curve else residual_linear
        if residual > tolerance:
            return None
        derivative = np.polyder(model)/roi_height
        if any(abs(math.degrees(math.atan(float(np.polyval(derivative, at))))) > cfg.max_angle
               for at in (t[0], t[-1], (ahead_y-near_y)/roi_height)):
            return None
        coverage = len(points)/cfg.bands
        width_fit = np.polyfit(t, points[:, 4], 1)
        width_noise = float(np.mean(np.abs(points[:, 4]-np.polyval(width_fit, t))))/max(2.,float(np.median(points[:, 4])))
        contrast_quality = min(1.,float(np.median(points[:, 3]))/(2*cfg.min_contrast))
        confidence = coverage*math.exp(-residual/tolerance)*math.exp(-.5*width_noise)*contrast_quality
        score = coverage-.15*residual/tolerance-.06*width_noise+.04*contrast_quality
        ys = np.linspace(points[0, 1], points[-1, 1], 32)
        xs = np.polyval(model, (ys-near_y)/roi_height)
        return dict(model='quadratic' if use_curve else 'linear', points=points,
                    near_x=float(np.polyval(model, 0)), near_y=near_y,
                    ahead_x=float(np.polyval(model, (ahead_y-near_y)/roi_height)), ahead_y=ahead_y,
                    image_angle_deg=math.degrees(math.atan(-float(np.polyval(derivative, (ahead_y-near_y)/roi_height)))),
                    centerline=np.column_stack((xs,ys)).tolist(), confidence=float(np.clip(confidence,0,1)),
                    coverage=coverage, residual_px=residual, score=score,
                    curve_norm=curve_norm if use_curve else 0., max_gap_bands=int(gaps.max()) if gaps.size else 0)

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
        masks = [mask]
        methods = [cfg.threshold_mode]
        if cfg.threshold_mode == 'otsu':
            # Horizontal black-hat recovers a narrow dark tape inside a broad
            # shadow; keep candidates separate so the broad Otsu foreground
            # cannot merge with and conceal the locally contrasted stripe.
            kernel_width = 2*max(2, round(cfg.max_width*width))+1
            local = cv2.morphologyEx(crop, cv2.MORPH_BLACKHAT, np.ones((1,kernel_width),np.uint8))
            local_mask = np.uint8(local >= cfg.min_contrast)*255
            masks.append(local_mask); methods.extend(['local_contrast','row_otsu'])
        full_mask = np.zeros_like(gray)
        full_mask[top:bottom, left:right] = np.maximum.reduce(masks)
        result = empty_result("no_line", width, height)
        result.update(roi=[left, top, right, bottom], threshold_value=threshold, threshold_methods=methods)

        candidates = []
        centers_y = np.linspace(top + 3, bottom - 4, cfg.bands).astype(int)
        if np.any(np.diff(centers_y) <= 0):
            result['reason'] = 'roi_too_short'
            return result, full_mask, gray
        minimum_width, maximum_width = max(2, round(cfg.min_width * width)), round(cfg.max_width * width)
        for band, y in enumerate(centers_y):
            relative_y = y - top
            profile = crop[max(0, relative_y - 2):relative_y + 3].mean(axis=0)
            found = []
            occupied_sets = [np.mean(candidate_mask[max(0, relative_y - 2):relative_y + 3] > 0, axis=0) >= .6
                             for candidate_mask in masks]
            if cfg.threshold_mode == 'otsu':
                # Per-row illumination/perspective: a global threshold can split
                # a wide near-field tape or merge its distant end with furniture.
                _, row_mask = cv2.threshold(np.uint8(profile),0,255,cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
                occupied_sets.append(row_mask.ravel() > 0)
            for occupied in occupied_sets:
                # Join only small internal bright defects (glare/seams); preserve
                # larger separations between distinct tape paths.
                gap_kernel = max(3, round(width*.02) | 1)
                occupied = cv2.morphologyEx(np.uint8(occupied).reshape(1,-1),cv2.MORPH_CLOSE,
                                           np.ones((1,gap_kernel),np.uint8)).ravel() > 0
                transitions = np.diff(np.r_[False, occupied, False].astype(np.int8))
                starts, ends = np.flatnonzero(transitions == 1), np.flatnonzero(transitions == -1)
                for start, end in zip(starts, ends):
                    tape_width = end - start
                    if not minimum_width <= tape_width <= maximum_width:
                        continue
                    padding = max(3, tape_width // 2)
                    before = profile[max(0, start - padding):start]
                    after = profile[end:min(len(profile), end + padding)]
                    if len(before) < 2 or len(after) < 2:
                        continue
                    darkness = float(profile[start:end].mean())
                    contrast = min(float(before.mean()), float(after.mean())) - darkness
                    if contrast < cfg.min_contrast:
                        continue
                    found.append((left + (start + end - 1) / 2, float(y), band, contrast, tape_width))
            # Bound work on cluttered floors; keep the highest-contrast candidates.
            merged = []
            for point in sorted(found, key=lambda p:p[3], reverse=True):
                if not any(abs(point[0]-p[0]) <= max(2.,min(point[4],p[4])*.25) for p in merged):
                    merged.append(point)
            candidates.extend(merged[:6])
        result["candidates"] = [[p[0], p[1]] for p in candidates]
        if len(candidates) < math.ceil(cfg.bands * cfg.min_coverage):
            return result, full_mask, gray

        points = np.asarray(candidates, dtype=np.float64)
        tolerance = max(2.5, width * 0.012)
        roi_height = bottom - top
        fits = []
        for indices, _ in self._paths(points, centers_y, width):
            fit = self._fit(points[list(indices)], centers_y, roi_height, width, tolerance)
            if fit is not None:
                fits.append(fit)
        if not fits:
            result["reason"] = "no_consistent_path"
            return result, full_mask, gray
        fits.sort(key=lambda fit:fit['score'], reverse=True)
        best = fits[0]
        ambiguity_distance = max(3*tolerance, width*.06)
        for alternative in fits[1:]:
            if alternative['score'] >= best['score']-.08 and max(
                    abs(alternative['near_x']-best['near_x']),
                    abs(alternative['ahead_x']-best['ahead_x'])) > ambiguity_distance:
                result['reason'] = 'ambiguous_lines'
                return result, full_mask, gray
        target = cfg.target_x*width
        result.update(valid=True, reason='ok', confidence=best['confidence'],
                      near_error_px=best['near_x']-target,
                      near_error_norm=(best['near_x']-target)/(width/2),
                      lookahead_error_norm=(best['ahead_x']-target)/(width/2),
                      image_angle_deg=best['image_angle_deg'],
                      near_xy=[best['near_x'],best['near_y']],
                      lookahead_xy=[best['ahead_x'],best['ahead_y']],
                      line_ends=[best['centerline'][0],best['centerline'][-1]],
                      centerline=best['centerline'], model=best['model'],
                      points=best['points'][:,:2].tolist(), coverage=best['coverage'],
                      residual_px=best['residual_px'], curve_norm=best['curve_norm'],
                      max_gap_bands=best['max_gap_bands'])
        return result, full_mask, gray


def render_overlay(frame, result, config, control_label="DEMO: no steering output"):
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
        centerline = result.get('centerline') or result['line_ends']
        cv2.polylines(overlay,[np.array([xy(p) for p in centerline],dtype=np.int32)],False,(60,255,80),3)
        for point in result["points"]:
            cv2.circle(overlay, xy(point), 3, (0, 255, 255), -1)
        near, ahead = xy(result["near_xy"]), xy(result["lookahead_xy"])
        cv2.circle(overlay, near, 7, (30, 255, 80), 2)
        cv2.circle(overlay, ahead, 7, (255, 100, 255), 2)
        cv2.line(overlay, (target, near[1]), near, (30, 255, 80), 2)
        message = f"near={result['near_error_norm']:+.3f} ahead={result['lookahead_error_norm']:+.3f} angle={result['image_angle_deg']:+.1f}"
    else:
        message = "INVALID: " + result["reason"]
    cv2.rectangle(overlay, (0, 0), (width, 50), (20, 20, 20), -1)
    cv2.putText(overlay, message, (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (240, 240, 240), 1)
    cv2.putText(overlay, f"confidence={result['confidence']:.2f} | {control_label}",
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
