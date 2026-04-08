import cv2
import os
import time
import threading
from collections import deque

# =================================================================
# 核心组件：高帧率相机后台读取线程
# =================================================================
import cv2
import os
import time
import threading
from collections import deque

class HighSpeedCamera:
    def __init__(self, src=0, width=640, height=480, fps=200, buffer_time=2.0):
        # 如果 MSMF 无法控制曝光，请尝试改为 cv2.CAP_DSHOW
        self.cap = cv2.VideoCapture(src, cv2.CAP_MSMF)
        
        self.target_fps = fps
        self.frame_interval = 1.0 / fps
        
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc('M', 'J', 'P', 'G'))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_FPS, fps)
        
        # 修正：手动曝光控制
        # 这里的设置取决于驱动，如果运行报错，请尝试捕获异常或检查 cv2.hasAttribute
        if hasattr(cv2, 'CAP_PROP_AUTO_EXPOSURE'):
            self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1) # 通常 1 是手动
        
        # 设置曝光值，-7 到 -11 之间比较稳，保证快门足够快
        self.cap.set(cv2.CAP_PROP_EXPOSURE, -7) 
        
        self.buffer_size = int(fps * buffer_time)
        self.buffer = deque(maxlen=self.buffer_size)
        
        self.running = True
        self.real_fps = 0.0
        
        self.thread = threading.Thread(target=self._update, daemon=True)
        self.thread.start()

    def _update(self):
        frame_count = 0
        last_stat_time = time.perf_counter()
        
        # 帧率控制器的基准时间
        next_frame_time = time.perf_counter()
        
        while self.running:
            current_time = time.perf_counter()
            
            # 如果还没到下一帧的时间，就稍微等等（微秒级自旋锁，比 sleep 更准）
            if current_time < next_frame_time:
                # 如果差距较大，可以先 sleep 一下释放 CPU
                if next_frame_time - current_time > 0.001:
                    time.sleep(0.0005)
                continue 
            
            ret, frame = self.cap.read()
            if ret:
                self.buffer.append(frame)
                frame_count += 1
                
                # 计算下一次应该抓取的时间点（严格累加，防止误差漂移）
                next_frame_time += self.frame_interval
                
                # 更新 FPS 统计
                if frame_count % 50 == 0:
                    now = time.perf_counter()
                    self.real_fps = 50 / (now - last_stat_time)
                    last_stat_time = now
            else:
                # 如果读取失败，也要更新基准时间，防止补偿过快
                next_frame_time = time.perf_counter() + self.frame_interval

    def get_latest_frame(self):
        if len(self.buffer) > 0:
            return self.buffer[-1].copy()
        return None

    def get_buffer_snapshot(self):
        return list(self.buffer)

    def stop(self):
        self.running = False
        self.thread.join()
        self.cap.release()


# =================================================================
# 主程序：UI 界面与状态控制
# =================================================================
def get_next_capture_num():
    existing_dirs = [d for d in os.listdir('.') if os.path.isdir(d) and d.startswith('Capture_')]
    max_num = 0
    for d in existing_dirs:
        try:
            num = int(d.split('_')[1])
            if num > max_num: max_num = num
        except ValueError:
            pass
    return max_num + 1

print("🚀 正在启动双线程高速摄像系统...")
# 初始化相机，保留 2 秒的缓存
cam = HighSpeedCamera(src=0, width=640, height=480, fps=200, buffer_time=2.0)
time.sleep(1) # 等待摄像头预热和线程启动

state = "preview"
wait_start_time = 0

print("✅ 系统就绪！注意：现在的预览窗口看起来是 30/60fps，但后台是全速在抓取的。")

while True:
    # 1. 仅从后台抽一张图来做 UI 预览，就算这里卡住，后台照样在抓
    frame = cam.get_latest_frame()
    if frame is None:
        continue

    # 2. 绘制 UI 状态
    cv2.putText(frame, f"Backend Cam FPS: {int(cam.real_fps)}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
    
    if state == "preview":
        cv2.putText(frame, "Ready (Press 's' to trigger)", (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)
    elif state == "waiting":
        elapsed = time.time() - wait_start_time
        remain = max(0, 2.0 - elapsed)
        cv2.putText(frame, f"Saving in: {remain:.1f}s", (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
        if elapsed >= 2.0:
            state = "saving"
            
    cv2.imshow('High Speed Camera Preview', frame)

    # 3. 键盘事件控制
    key = cv2.waitKey(1) & 0xFF
    if key == ord('s') and state == "preview":
        state = "waiting"
        wait_start_time = time.time()
    elif key == ord('q'):
        break

    # 4. 触发保存 (从后台线程一次性把所有帧拿出来存硬盘)
    if state == "saving":
        # 冻结并获取缓存快照
        frames_to_save = cam.get_buffer_snapshot() 
        
        capture_num = get_next_capture_num()
        folder_name = f"Capture_{capture_num}"
        video_name = f"Capture_{capture_num}_slomo.mp4"
        os.makedirs(folder_name, exist_ok=True)
        
        print(f"\n⚡ 抓取成功！获取到 {len(frames_to_save)} 帧画面，正在保存到 {folder_name}/ ...")
        
        # 存图
        for i, f in enumerate(frames_to_save):
            cv2.imwrite(f"{folder_name}/frame_{i:04d}.jpg", f)
            
        # 存慢动作视频
        print(f"🎬 正在生成慢动作视频...")
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        out = cv2.VideoWriter(video_name, fourcc, 30.0, (640, 480))
        for f in frames_to_save:
            out.write(f)
        out.release()
        
        print("✅ 保存完毕！可以继续下一次测试。\n")
        state = "preview"

cam.stop()
cv2.destroyAllWindows()