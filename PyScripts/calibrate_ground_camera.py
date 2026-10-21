"""Create a validated ground calibration from intrinsics and measured point pairs.

python -m PyScripts.calibrate_ground_camera --input points.json --output camera.json
At least six training and two independent validation pairs are required.
"""
import argparse
import json
from pathlib import Path


def calibrate(data):
    import cv2
    import numpy as np
    try:
        from .vehicle_state.vision import validate_camera
    except ImportError:
        from vehicle_state.vision import validate_camera
    pairs = data['pairs']
    training = [p for p in pairs if p.get('validation') is False]
    validation = [p for p in pairs if p.get('validation') is True]
    if len(training) < 6 or len(validation) < 2:
        raise ValueError('需要至少6个训练点和2个独立验证点，每点明确 validation=true/false')
    k = np.array(data['camera_matrix'], dtype=np.float64)
    distortion = np.array(data['dist_coeffs'], dtype=np.float64)
    def arrays(group):
        image = np.array([p['image'] for p in group], dtype=np.float64).reshape(-1, 1, 2)
        ground = np.array([p['ground'] for p in group], dtype=np.float64).reshape(-1, 2)
        if not np.isfinite(image).all() or not np.isfinite(ground).all():
            raise ValueError('点坐标必须有限')
        return cv2.undistortPoints(image, k, distortion, P=k), ground
    image, ground = arrays(training)
    if any(np.linalg.matrix_rank(points-points.mean(axis=0)) < 2 for points in (image[:, 0], ground)):
        raise ValueError('标定点不能共线，请覆盖完整地面区域')
    h, _ = cv2.findHomography(image, ground, method=0)
    if h is None or np.linalg.cond(h) > 1e10:
        raise ValueError('地面变换退化')
    check_image, check_ground = arrays(validation)
    errors = np.linalg.norm(cv2.perspectiveTransform(check_image, h)[:, 0]-check_ground, axis=1)
    if float(errors.max()) > .02:
        raise ValueError(f'独立验证最大误差 {errors.max()*1000:.1f}mm 超过20mm')
    result = {'schema': 1, 'image_size': data['image_size'], 'camera_matrix': k.tolist(),
              'dist_coeffs': distortion.flatten().tolist(), 'homography': h.tolist(),
              'image_hull': cv2.convexHull(image.astype(np.float32))[:, 0].tolist(),
              'validation_max_m': float(errors.max()), 'latency_ms': data.get('latency_ms'),
              'reference': 'rear_axle_x_forward_y_left_m'}
    return validate_camera(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = calibrate(json.loads(Path(args.input).read_text(encoding='utf-8')))
    with Path(args.output).open('x', encoding='utf-8') as file:
        json.dump(result, file, ensure_ascii=False, indent=2, allow_nan=False)
    print(f"已保存地面标定，独立验证最大误差 {result['validation_max_m']*1000:.1f}mm；延迟为空时保持仅预览")


if __name__ == '__main__':
    main()
