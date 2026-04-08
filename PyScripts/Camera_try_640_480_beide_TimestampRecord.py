import cv2
import os
import time
import threading
import numpy as np
from collections import deque
from flask import Flask, Response, render_template_string, jsonify

# 确保录像保存根目录存在
SAVE_DIR = "Car_Records"
os.makedirs(SAVE_DIR, exist_ok=True)

# =================================================================
# 核心：高帧率后台“黑匣子”线程（时间戳软同步版）
# =================================================================
class HighSpeedCamera:
    def __init__(self, src=0, name="Cam", fps=200):
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
        
        # --- 录制控制状态 ---
        self.is_recording = False
        self.record_frames = []
        self.record_target_count = 0
        self.record_session_id = ""

        self.thread = threading.Thread(target=self._update, name=f"Thread-{name}", daemon=True)
        self.thread.start()

    def start_record(self, session_id, duration_sec=3):
        """触发录制任务，接收统一的 session_id 以保证左右文件夹归属同一个批次"""
        if not self.available or self.is_recording:
            return False
        self.record_target_count = int(self.target_fps * duration_sec)
        self.record_frames = []
        self.record_session_id = session_id
        self.is_recording = True 
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
                # 获取获取到当前帧的精准纳秒级系统时间
                curr_ns = time.time_ns() 
                self.buffer.append(frame)
                
                # 如果处于录制模式，保存 (纳秒时间戳, 图像矩阵) 的元组
                if self.is_recording:
                    self.record_frames.append((curr_ns, frame.copy()))
                    
                    if len(self.record_frames) >= self.record_target_count:
                        self.is_recording = False
                        # 开启异步保存线程
                        threading.Thread(target=self._save_images_to_disk, 
                                         args=(self.record_frames, self.record_session_id)).start()

                frame_count += 1
                if frame_count % 20 == 0:
                    curr_time = time.time()
                    self.real_fps = 20 / (curr_time - prev_time)
                    prev_time = curr_time
            else:
                time.sleep(0.01)

    def _save_images_to_disk(self, frames_to_save, session_id):
        """将缓存的帧保存为以时间戳命名的图片序列"""
        # 创建层级目录：Car_Records/20260404_153000/Left/
        save_path = os.path.join(SAVE_DIR, session_id, self.name)
        os.makedirs(save_path, exist_ok=True)
        
        print(f"⏳ [{self.name}] 正在保存 {len(frames_to_save)} 张图片到 {save_path} ...")
        
        for timestamp_ns, f in frames_to_save:
            # 文件名就是精确的纳秒时间戳
            filename = os.path.join(save_path, f"{timestamp_ns}.jpg")
            # 使用 95% 的高质量 JPEG 保存，兼顾画质与硬盘写入速度
            cv2.imwrite(filename, f, [cv2.IMWRITE_JPEG_QUALITY, 95])
            
        print(f"💾 [{self.name}] 图片序列保存完成！")

    def get_latest_frame(self):
        if not self.available or not self.buffer:
            error_img = np.zeros((480, 640, 3), dtype=np.uint8)
            cv2.putText(error_img, f"{self.name} NO SIGNAL (idx:{self.src})", 
                        (100, 240), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
            return error_img
            
        frame = self.buffer[-1].copy()
        
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
                <h1>🚗 FUTURE CAR: 双路监控系统 (Soft-Sync)</h1>
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
                    <button id="rec-btn" onclick="startRecord()">🔴 记录当前 3 秒图片序列</button>
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
                        btn.innerText = "⏳ 正在抓取 (剩余 3 秒)...";
                        
                        fetch('/start_record').then(r => r.json()).then(data => {
                            if(data.status === 'started') {
                                let timeLeft = 3;
                                let timer = setInterval(() => {
                                    timeLeft -= 1;
                                    if(timeLeft <= 0) {
                                        clearInterval(timer);
                                        btn.innerText = "💾 正在疯狂写入硬盘...";
                                        setTimeout(() => {
                                            btn.disabled = false;
                                            btn.innerText = "🔴 记录当前 3 秒图片序列";
                                        }, 2500); // 写入图片较慢，多等一会儿
                                    } else {
                                        btn.innerText = `⏳ 正在抓取 (剩余 ${timeLeft} 秒)...`;
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

@app.route('/start_record')
def start_record():
    # 生成一个统一的批次文件夹名称（精确到秒）
    session_id = time.strftime("%Y%m%d_%H%M%S")
    # 两路摄像头共享同一个 session_id，保存在同一个根目录下
    cam_left.start_record(session_id=session_id, duration_sec=3)
    cam_right.start_record(session_id=session_id, duration_sec=3)
    print(f"📢 收到指令，创建同步采集批次: {session_id}")
    return jsonify({"status": "started"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, threaded=True)