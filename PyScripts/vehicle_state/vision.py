"""Calibrated image-to-ground adapter. OpenCV is imported only by vision work."""
import math
from .contracts import VisionObservation
from .profile import finite


def validate_camera(c):
    keys = {'schema', 'image_size', 'camera_matrix', 'dist_coeffs', 'homography',
            'image_hull', 'validation_max_m', 'latency_ms', 'reference'}
    if not isinstance(c, dict) or set(c) != keys or c['schema'] != 1 or c['reference'] != 'rear_axle_x_forward_y_left_m':
        raise ValueError('需要相机地面标定工具生成的完整配置，坐标原点为后轴中点')
    if not isinstance(c['image_size'], list) or len(c['image_size']) != 2 or any(type(v) is not int or not 80 <= v <= 4096 for v in c['image_size']):
        raise ValueError('标定图像尺寸无效')
    for name in ('camera_matrix', 'homography'):
        m = c[name]
        if not isinstance(m, list) or len(m) != 3 or any(not isinstance(row, list) or len(row) != 3 or any(not finite(v) for v in row) for row in m):
            raise ValueError('相机矩阵必须是有限的3×3矩阵')
        determinant = sum(m[0][i]*(m[1][(i+1)%3]*m[2][(i+2)%3]-m[1][(i+2)%3]*m[2][(i+1)%3]) for i in range(3))
        if abs(determinant) < 1e-12:
            raise ValueError('相机矩阵退化')
    if c['camera_matrix'][0][0] <= 0 or c['camera_matrix'][1][1] <= 0:
        raise ValueError('相机焦距必须为正')
    if not isinstance(c['dist_coeffs'], list) or len(c['dist_coeffs']) not in (4, 5, 8, 12, 14) or any(not finite(v) for v in c['dist_coeffs']):
        raise ValueError('需要相机内参与畸变系数，不能默认镜头无畸变')
    hull = c['image_hull']
    if not isinstance(hull, list) or len(hull) < 3 or any(not isinstance(p, list) or len(p) != 2 or any(not finite(v) for v in p) for p in hull):
        raise ValueError('相机标定有效区域无效')
    if not finite(c['validation_max_m']) or not 0 <= c['validation_max_m'] <= .02:
        raise ValueError('独立验证点最大误差需小于等于20mm')
    if c['latency_ms'] is not None and (not finite(c['latency_ms']) or not 0 <= c['latency_ms'] <= 300):
        raise ValueError('读帧完成相对曝光的延迟需为0～300ms；未知保持空值')
    return c


def ground_observation(result, frame_size, calibration, read_time, sequence):
    import cv2
    import numpy as np
    c = validate_camera(calibration)
    if list(frame_size) != c['image_size']:
        raise ValueError('图像分辨率已变化，请重新标定')
    if c['latency_ms'] is None:
        raise ValueError('相机曝光到读帧的延迟未标定；只显示图像结果')
    if not result.get('valid') or result.get('confidence', 0) < .6:
        raise ValueError('胶带识别质量不足')
    points = np.array(result['points'], dtype=np.float64)
    if len(points) < 6:
        raise ValueError('胶带有效点不足')
    points *= frame_size[0]/result['width']
    k = np.array(c['camera_matrix'], dtype=np.float64)
    undistorted = cv2.undistortPoints(points.reshape(-1, 1, 2), k, np.array(c['dist_coeffs']), P=k)
    hull = np.array(c['image_hull'], dtype=np.float32)
    inside = [cv2.pointPolygonTest(hull, tuple(map(float, p)), False) >= 0 for p in undistorted[:, 0]]
    selected = undistorted[inside]
    if len(selected) < 6:
        raise ValueError('胶带超出实测地面标定区域')
    ground = cv2.perspectiveTransform(selected, np.array(c['homography'], dtype=np.float64))[:, 0]
    if not np.isfinite(ground).all() or np.ptp(ground[:, 0]) < .15:
        raise ValueError('可见胶带的地面纵向跨度不足150mm')
    x, y = ground[:, 0], ground[:, 1]
    a, b = np.polyfit(x, y, 1)
    residual = float(np.sqrt(np.mean((y-(a*x+b))**2)))
    if residual > .02 or abs(math.atan(a)) > .35:
        raise ValueError('地面拟合误差过大或超出直线小角度模型')
    # Regression covariance, including shared intercept/slope correlation.
    noise = max(.005**2, residual**2)
    sxx = float(np.sum((x-x.mean())**2))
    var_y = max(.01**2, noise*(1/len(x)+float(x.mean())**2/sxx))
    var_psi = max(math.radians(1.5)**2, noise/sxx/(1+a*a)**2)
    cov = -float(x.mean())*noise/sxx/(1+a*a)
    cov = max(-.9*math.sqrt(var_y*var_psi), min(.9*math.sqrt(var_y*var_psi), cov))
    return VisionObservation(read_time-c['latency_ms']/1000, sequence, -float(b), -math.atan(a),
                             (var_y, cov, cov, var_psi), float(result['confidence']))
