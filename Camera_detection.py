import cv2

# 1. 打开摄像头 (如果你确认是外接摄像头，保持为 1 或改成 0 试试)
cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)

if not cap.isOpened():
    print("❌ 摄像头没打开！")
    exit()

# 2. 依次强制注入参数 (严格按照格式->分辨率->帧率的顺序)
fourcc = cv2.VideoWriter_fourcc(*'MJPG')
cap.set(cv2.CAP_PROP_FOURCC, fourcc)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480) # ⚠️ 这里我改成了标准的 480
cap.set(cv2.CAP_PROP_FPS, 200)

# 3. 逼它说实话：读取它最终真正接受了什么参数
actual_fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
# 将 fourcc 整数转回字符串查看 (比如 'MJPG' 或 'YUY2')
fourcc_str = "".join([chr((actual_fourcc >> 8 * i) & 0xFF) for i in range(4)])

print("========== 摄像头真实体检报告 ==========")
print(f"实际视频格式 (FOURCC): {fourcc_str}")
print(f"实际分辨率: {cap.get(cv2.CAP_PROP_FRAME_WIDTH)} x {cap.get(cv2.CAP_PROP_FRAME_HEIGHT)}")
print(f"系统允许的最高 FPS: {cap.get(cv2.CAP_PROP_FPS)}")
print("========================================")

cap.release()