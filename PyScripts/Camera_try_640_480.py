import cv2
import os
import time
import threading
from collections import deque
from flask import Flask, Response, render_template_string

# =================================================================
# 核心：高帧率后台“黑匣子”线程
# =================================================================
class HighSpeedCamera:
    def __init__(self, src=0, width=640, height=480, fps=210):
        self.cap = cv2.VideoCapture(src, cv2.CAP_V4L2)
        # 强制开启高速模式
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        # 强制关闭自动曝光，手动设置一个极短的曝光值
        self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1) # 1 为手动模式
        self.cap.set(cv2.CAP_PROP_EXPOSURE, 5)      # 设置为 5ms 或更低
        self.cap.set(cv2.CAP_PROP_FPS, fps)
        
        # 内存缓存区：保留最近 2 秒的所有原始帧（210fps * 2s ≈ 420帧）
        self.buffer_size = int(fps * 2.0)
        self.buffer = deque(maxlen=self.buffer_size)
        
        self.running = True
        self.real_fps = 0.0
        self.is_saving = False
        self.thread = threading.Thread(target=self._update, daemon=True)
        self.thread.start()

    def _update(self):
        prev_time = time.time()
        frame_count = 0
        while self.running:
            ret, frame = self.cap.read()
            if ret:
                # 只有不在保存文件时才往里写，防止内存竞争
                if not self.is_saving:
                    self.buffer.append(frame)
                frame_count += 1
                if frame_count % 20 == 0:
                    curr_time = time.time()
                    self.real_fps = 20 / (curr_time - prev_time)
                    prev_time = curr_time

    def get_latest_frame(self):
        return self.buffer[-1].copy() if self.buffer else None

    def save_slomo(self):
        """将内存中的高速帧导出为慢动作视频"""
        self.is_saving = True
        frames = list(self.buffer)
        if not frames: return "No frames in buffer"
        
        timestamp = int(time.time())
        folder = f"Capture_{timestamp}"
        os.makedirs(folder, exist_ok=True)
        
        # 导出视频（以 30fps 播放，实现约 7 倍慢动作）
        out = cv2.VideoWriter(f"{folder}/slomo_210fps.mp4", 
                             cv2.VideoWriter_fourcc(*'mp4v'), 30.0, (640, 480))
        for f in frames:
            out.write(f)
        out.release()
        
        self.is_saving = False
        return f"Saved {len(frames)} frames to {folder}/"

# =================================================================
# Flask 网页控制台
# =================================================================
app = Flask(__name__)
# 注意：这里我们尝试开启 210 FPS 的后台采集！
cam = HighSpeedCamera(src=0, fps=210)

@app.route('/')
def index():
    # 网页前端：增加一个点击按钮发送请求到 /capture
    return render_template_string('''
        <html>
            <body style="background: #222; color: white; text-align: center; font-family: sans-serif;">
                <h1>🚗 汽车工程：高速视觉采集终端</h1>
                <div style="margin-bottom: 20px;">
                    <img src="/video_feed" style="border: 2px solid #00ff00; width: 640px;">
                </div>
                <button onclick="fetch('/capture')" style="padding: 15px 30px; font-size: 20px; cursor: pointer; background: #ff4444; color: white; border: none; border-radius: 5px;">
                    📸 触发 2秒 高速抓取 (210 FPS)
                </button>
                <p id="status">系统就绪，后台实时帧率: {{ fps }}</p>
                <script>
                    setInterval(() => {
                        fetch('/fps').then(r => r.text()).then(t => {
                            document.getElementById('status').innerText = "后台实时帧率: " + t + " FPS";
                        })
                    }, 1000);
                </script>
            </body>
        </html>
    ''', fps=0)

@app.route('/video_feed')
def video_feed():
    def generate():
        while True:
            frame = cam.get_latest_frame()
            if frame is not None:
                # 预览流：为了网络带宽，我们压缩并限制预览频率
                ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n\r\n')
            time.sleep(0.04) # 预览限制在 25fps
    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/capture')
def capture():
    res = cam.save_slomo()
    print(f"⚡ [EVENT]: {res}")
    return res

@app.route('/fps')
def get_fps():
    return f"{cam.real_fps:.1f}"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, threaded=True)