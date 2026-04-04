import cv2

# 同时尝试打开两个高速通道
cap0 = cv2.VideoCapture(0, cv2.CAP_V4L2)
cap2 = cv2.VideoCapture(2, cv2.CAP_V4L2)

def setup_high_speed(cap):
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_FPS, 210)

setup_high_speed(cap0)
setup_high_speed(cap2)

if cap0.isOpened() and cap2.isOpened():
    print("🚀 英雄所见略同！双路 210 FPS 摄像头已同步开启。")
    # 读取一帧试试带宽
    ret0, _ = cap0.read()
    ret2, _ = cap2.read()
    if ret0 and ret2:
        print("✅ 带宽测试通过：双路数据提取正常。")
    else:
        print("⚠️ 警告：虽然开启了，但无法同时获取画面。建议降低 FPS 或分辨率。")
else:
    print("❌ 无法同时开启。这通常是 USB 带宽限制。")

cap0.release()
cap2.release()