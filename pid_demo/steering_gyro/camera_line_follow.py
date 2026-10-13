#!/usr/bin/env python3
# camera_line_follow.py —— 摄像头寻迹外环(PC 端)
#
# 用 OpenCV 检测车道线,计算横向偏差 / 航向偏差,合成目标横摆角速度 r_ref,
# 通过串口以 "R <r_ref>" 形式持续下发给 ESP32 内环(对应 MODE_MANUAL 模式)。
#
# 依赖: pip install opencv-python pyserial numpy
#
# 示例:
#   python camera_line_follow.py --port COM10 --cam 0
#   python camera_line_follow.py --port COM10 --no-send   # 只看画面,不发串口

import argparse
import time

import cv2
import numpy as np
import serial


def detect_line(frame):
    """返回 (lat_err_px, head_err_deg, out_frame) 或 (None, None, out_frame)。"""
    h, w = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)

    # 深色线在浅色背景:取反 + 大津阈值,让线变白
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    roi_y0 = int(h * 0.6)          # 只取下方 40% 作感兴趣区(前瞻近端)
    roi = thresh[roi_y0:, :]
    ys, xs = np.nonzero(roi)       # 线像素坐标(ys 为 ROI 局部坐标)
    out = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)

    if len(xs) < 100:
        return None, None, out     # 丢线

    xs_f = xs.astype(np.float32)
    ys_f = (ys + roi_y0).astype(np.float32)   # 转回整帧坐标

    # 横向偏差:线质心 x 相对画面中心(像素),正值=线在右侧
    lat_err = float(xs_f.mean()) - (w / 2.0)

    # 航向偏差:拟合直线方向与竖直方向夹角(度)
    pts = np.column_stack([xs_f, ys_f]).astype(np.float32).reshape(-1, 1, 2)
    vx, vy, cx, cy = cv2.fitLine(pts, cv2.DIST_L2, 0, 0.01, 0.01)
    head_err = float(np.degrees(np.arctan2(vx[0], vy[0])))

    # 可视化
    cv2.line(out, (w // 2, 0), (w // 2, h), (255, 0, 0), 1)            # 中线
    cx_i, cy_i = int(cx), int(cy)
    p1 = (int(cx - vx * 200), int(cy - vy * 200))
    p2 = (int(cx + vx * 200), int(cy + vy * 200))
    cv2.line(out, p1, p2, (0, 0, 255), 2)                              # 拟合线
    cv2.circle(out, (int(xs_f.mean()), roi_y0 + int(ys.mean())), 5, (0, 255, 0), -1)  # 质心

    return lat_err, head_err, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', default='COM10')
    ap.add_argument('--baud', type=int, default=115200)
    ap.add_argument('--cam', type=int, default=0)
    ap.add_argument('--k-lat', type=float, default=0.15, help='横向偏差增益 deg/s per px')
    ap.add_argument('--k-head', type=float, default=0.8, help='航向偏差增益 deg/s per deg')
    ap.add_argument('--max-yaw', type=float, default=120.0, help='r_ref 限幅 deg/s')
    ap.add_argument('--hz', type=float, default=25.0, help='下发频率')
    ap.add_argument('--no-send', action='store_true', help='只显示画面,不发串口')
    args = ap.parse_args()

    ser = None
    if not args.no_send:
        ser = serial.Serial(args.port, args.baud, timeout=0.05)

    cap = cv2.VideoCapture(args.cam)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    if not cap.isOpened():
        print('[camera_line_follow] 摄像头打不开,检查 --cam 参数')
        return

    period = 1.0 / args.hz
    last = time.time()
    print('[camera_line_follow] 启动,q 退出')

    while True:
        ok, frame = cap.read()
        if not ok:
            continue

        lat_err, head_err, out = detect_line(frame)

        r_ref = 0.0
        if lat_err is not None:
            r_ref = args.k_lat * lat_err + args.k_head * head_err
            r_ref = max(-args.max_yaw, min(args.max_yaw, r_ref))

        if ser is not None:
            ser.write(f'R {r_ref:.1f}\n'.encode())   # 丢线时 r_ref=0 保持直线

        cv2.imshow('line follow', out)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

        now = time.time()
        dt = now - last
        if dt < period:
            time.sleep(period - dt)
        last = now

    cap.release()
    cv2.destroyAllWindows()
    if ser:
        ser.close()


if __name__ == '__main__':
    main()
