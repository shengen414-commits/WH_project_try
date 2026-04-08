import cv2
import os
import time
import threading
import numpy as np
from collections import deque
from flask import Flask, Response, render_template_string, jsonify

#Web预览

# 确保录像保存文件夹存在
SAVE_DIR = "Car_Records"
os.makedirs(SAVE_DIR, exist_ok=True)

# =================================================================
# 核心：高帧率后台“黑匣子”线程（支持多路）
# =================================================================
class HighSpeedCamera:
    def __init__(self, src=0, name="Cam", fps=30):
        if "Right" in name:
            time.sleep(2.0) 
        else:
            time.sleep(0.2)

        self.name = name
        self.src = src
        self.target_fps = fps
        self.cap = cv2.VideoCapture(src, cv2.CAP_V4L2)
        
        if not self.cap.isOpened():
            print(f"⚠️ [{name}] 摄像头打不开！请检查 /dev/video{src}")
            self.available = False
        else:
            self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            self.cap.set(cv2.CAP_PROP_FPS, fps)
            self.available = True
            print(f"✅ [{name}] 初始化成功")

        self.buffer = deque(maxlen=int(fps * 2.0))
        self.running = True
        self.real_fps = 0.0
        
        # --- 新增：录制控制状态 ---
        self.is_recording = False
        self.record_frames = []
        self.record_target_count = 0
        self.record_timestamp = ""

        self.thread = threading.Thread(target=self._update, name=f"Thread-{name}", daemon=True)
        self.thread.start()

    def start_record(self, duration_sec=3):
        """触发录制任务"""
        if not self.available or self.is_recording:
            return False
        self.record_target_count = int(self.target_fps * duration_sec)
        self.record_frames = []
        self.record_timestamp = time.strftime("%Y%m%d_%H%M%S")
        self.is_recording = True # 开启录制闸门
        return True

    def _update(self):
        prev_time = time.time()
        frame_count = 0
        while self.running:
            if not self.available:
                time.sleep(1)
                continue
                
            ret, frame = self.cap.read()
            if ret:
                self.buffer.append(frame)
                
                # --- 新增：如果处于录制模式，则把画面存入专属列表 ---
                if self.is_recording:
                    self.record_frames.append(frame.copy())
                    # 达到了3秒所需的帧数，停止录制并保存
                    if len(self.record_frames) >= self.record_target_count:
                        self.is_recording = False
                        # 开启一个新线程去写硬盘，防止阻塞接下来的画面采集
                        threading.Thread(target=self._save_video_to_disk, 
                                         args=(self.record_frames, self.record_timestamp)).start()

                frame_count += 1
                if frame_count % 20 == 0:
                    curr_time = time.time()
                    self.real_fps = 20 / (curr_time - prev_time)
                    prev_time = curr_time
            else:
                time.sleep(0.01)

    def _save_video_to_disk(self, frames_to_save, timestamp):
        """后台保存视频的方法"""
        filename = os.path.join(SAVE_DIR, f"{timestamp}_{self.name}.mp4")
        print(f"⏳ [{self.name}] 正在保存 3 秒视频: {filename} ...")
        
        # 使用 mp4v 编码，以目标 FPS 写入
        out = cv2.VideoWriter(filename, cv2.VideoWriter_fourcc(*'mp4v'), 
                              self.target_fps, (640, 480))
        for f in frames_to_save:
            out.write(f)
        out.release()
        print(f"💾 [{self.name}] 保存完成！")

    def get_latest_frame(self):
        if not self.available or not self.buffer:
            error_img = np.zeros((480, 640, 3), dtype=np.uint8)
            cv2.putText(error_img, f"{self.name} NO SIGNAL (idx:{self.src})", 
                        (100, 240), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
            return error_img
            
        frame = self.buffer[-1].copy()
        
        # --- 新增：如果在录制中，给网页画面加个红点提示 ---
        if self.is_recording:
            cv2.circle(frame, (30, 30), 10, (0, 0, 255), -1)
            cv2.putText(frame, "REC", (50, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
            
        return frame

# =================================================================
# Flask 逻辑
# =================================================================
app = Flask(__name__)

# 初始化摄像头
cam_left = HighSpeedCamera(src=0, name="Left", fps=200)
cam_right = HighSpeedCamera(src=2, name="Right", fps=200)

@app.route('/')
def index():
    return render_template_string('''
        <html>
            <head>
                <title>双路高速感知终端</title>
                <style>
                    body { background: #1a1a1a; color: #00ff00; font-family: monospace; text-align: center; }
                    .container { display: flex; justify-content: center; gap: 10px; padding: 20px; }
                    .camera-box { border: 2px solid #333; background: #000; padding: 10px; border-radius: 8px; }
                    img { width: 600px; height: 450px; background: #222; }
                    .stats { margin-top: 10px; font-size: 1.2em; }
                    .btn-group { margin-top: 20px; }
                    button { background: #ff4444; color: white; border: none; padding: 15px 30px; font-size: 18px; font-weight: bold; cursor: pointer; border-radius: 5px; }
                    button:hover { background: #cc0000; }
                    button:disabled { background: #555; cursor: not-allowed; }
                </style>
            </head>
            <body>
                <h1>🚗 FUTURE CAR: 双路监控系统</h1>
                <div class="container">
                    <div class="camera-box">
                        <h3>LEFT CAMERA (/dev/video0)</h3>
                        <img src="/video_feed/left">
                        <div class="stats" id="fps-left">FPS: 0.0</div>
                    </div>
                    <div class="camera-box">
                        <h3>RIGHT CAMERA (/dev/video2)</h3>
                        <img src="/video_feed/right">
                        <div class="stats" id="fps-right">FPS: 0.0</div>
                    </div>
                </div>
                <div class="btn-group">
                    <button id="rec-btn" onclick="startRecord()">🔴 记录当前 3 秒钟</button>
                </div>
                <script>
                    setInterval(() => {
                        fetch('/fps_stats').then(r => r.json()).then(data => {
                            document.getElementById('fps-left').innerText = "实时采集: " + data.left + " FPS";
                            document.getElementById('fps-right').innerText = "实时采集: " + data.right + " FPS";
                        });
                    }, 1000);

                    function startRecord() {
                        const btn = document.getElementById('rec-btn');
                        btn.disabled = true;
                        btn.innerText = "⏳ 正在录制 (剩余 3 秒)...";
                        
                        // 通知后端开始录制
                        fetch('/start_record').then(r => r.json()).then(data => {
                            if(data.status === 'started') {
                                let timeLeft = 3;
                                let timer = setInterval(() => {
                                    timeLeft -= 1;
                                    if(timeLeft <= 0) {
                                        clearInterval(timer);
                                        btn.innerText = "💾 正在保存到硬盘...";
                                        setTimeout(() => {
                                            btn.disabled = false;
                                            btn.innerText = "🔴 记录当前 3 秒钟";
                                        }, 1500);
                                    } else {
                                        btn.innerText = `⏳ 正在录制 (剩余 ${timeLeft} 秒)...`;
                                    }
                                }, 1000);
                            }
                        });
                    }
                </script>
            </body>
        </html>
    ''')

def gen_stream(camera):
    while True:
        frame = camera.get_latest_frame()
        if frame is not None:
            ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 60])
            yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n\r\n')
        time.sleep(0.04)

@app.route('/video_feed/left')
def video_left():
    return Response(gen_stream(cam_left), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/video_feed/right')
def video_right():
    return Response(gen_stream(cam_right), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/fps_stats')
def fps_stats():
    return jsonify({"left": f"{cam_left.real_fps:.1f}", "right": f"{cam_right.real_fps:.1f}"})

# --- 新增：接收网页录制指令的路由 ---
@app.route('/start_record')
def start_record():
    # 同时触发两个摄像头的录制
    cam_left.start_record(duration_sec=3)
    cam_right.start_record(duration_sec=3)
    print("📢 收到网页指令，开始同步录制 3 秒...")
    return jsonify({"status": "started"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, threaded=True)