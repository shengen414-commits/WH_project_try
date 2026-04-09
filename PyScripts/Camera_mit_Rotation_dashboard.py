import cv2
import os
import time
import threading
import serial
import re
import numpy as np
from collections import deque
from flask import Flask, Response, render_template_string, jsonify, request

# 确保录像保存根目录存在
SAVE_DIR = "Car_Records"
os.makedirs(SAVE_DIR, exist_ok=True)

# =================================================================
# 传感器后台数据读取与速度计算线程
# =================================================================
sensor_state = {
    "position": 0,
    "speed_pps": 0.0,
    "last_time_ms": 0,
    "last_pos": 0
}

try:
    esp32_serial = serial.Serial('/dev/ttyUSB0', 115200, timeout=1)
    esp32_serial.reset_input_buffer() 
    print("已清空启动积压数据！")
    print("✅ 成功连接到 ESP32 霍尔传感器模块！")
except Exception as e:
    print(f"⚠️ 无法连接到 ESP32: {e}")
    esp32_serial = None



# 🚀 增加一个用于存储高频历史轨迹的队列 (记录过去10次的点)
history_buffer = deque(maxlen=10) 

def read_esp32_data():
    '''牺牲一定响应速度，换取高速下速度波动毛刺减少
    ESP32在激活录制的时候10ms一次传回，此时画出来的速度图会波动很大，因为脉冲只能取整，
    10ms一次的向上向下圆整差的一个脉冲，也就是1/12ppr 每10ms差这么多圈数，在换算成一分钟RPM的时候带来的误差很大
    所以需要滤波处理，采用的是100ms 差分滑动窗口，累计过去100ms的脉冲数再算速度
    舍去了大概50ms的响应速度，得到高速下rpm的平滑，波动小'''
    global sensor_state
    if esp32_serial is None:
        return

    pattern = re.compile(r"时间:\s*(\d+)\s*ms\s*\|\s*位置:\s*(-?\d+)")

    while True:
        try:
            # 防积压机制
            if esp32_serial.in_waiting > 1000:
                esp32_serial.reset_input_buffer()
                continue

            if esp32_serial.in_waiting > 0:
                line = esp32_serial.readline().decode('utf-8', errors='ignore').strip()
                match = pattern.search(line)
                if match:
                    curr_time_ms = int(match.group(1))
                    curr_pos = int(match.group(2))

                    dt_sec = (curr_time_ms - sensor_state["last_time_ms"]) / 1000.0

                    if dt_sec > 0 and sensor_state["last_time_ms"] != 0:
                        raw_speed = (curr_pos - sensor_state["last_pos"]) / dt_sec

                        if dt_sec > 0.5:
                            # 1. 待机模式 (1000ms)：时间跨度大，完全没有量化误差，直接使用！
                            smoothed_speed = raw_speed
                            history_buffer.clear() # 刚从录制切回待机，清空旧轨迹
                        else:
                            # 2. 录制模式 (10ms)：启动 M/T 差分窗口算法
                            history_buffer.append((curr_time_ms, curr_pos))
                            
                            # 必须等攒够 10 个点 (约 100ms) 才开始差分计算
                            if len(history_buffer) == history_buffer.maxlen:
                                # 拿出 100ms 前的数据
                                old_time_ms, old_pos = history_buffer[0]
                                window_dt = (curr_time_ms - old_time_ms) / 1000.0
                                
                                # 计算这 100ms 跨度内的平滑速度
                                window_speed = (curr_pos - old_pos) / window_dt
                                
                                # 在此基础上，再加一层极弱的 EMA 滤波，让 4000 RPM 的波形呈现完美的流线型
                                alpha = 0.3
                                smoothed_speed = (alpha * window_speed) + ((1 - alpha) * sensor_state["speed_pps"])
                            else:
                                # 刚点录制的前 0.1 秒，用原始速度过渡
                                smoothed_speed = raw_speed 
                    else:
                        smoothed_speed = 0.0

                    # 更新全局状态
                    sensor_state["position"] = curr_pos
                    sensor_state["speed_pps"] = smoothed_speed
                    sensor_state["last_time_ms"] = curr_time_ms
                    sensor_state["last_pos"] = curr_pos
        except Exception as e:
            time.sleep(0.1)

threading.Thread(target=read_esp32_data, daemon=True).start()

# =================================================================
# 核心：高帧率后台“黑匣子”线程
# =================================================================
class HighSpeedCamera:
    def __init__(self, src=0, name="Cam", fps=200):
        # 初始化时稍微错峰，防止 USB 带宽瞬间冲顶
        if "Right" in name:
            time.sleep(1.0) 
        else:
            time.sleep(0.2)

        self.name = name
        self.src = src
        self.target_fps = fps
        self.cap = None
        self.available = False
        
        # --- 新增：休眠开关 ---
        # 如果是右眼，初始状态设为休眠
        self.is_active = False if name == "Right" else True 

        self.buffer = deque(maxlen=int(fps * 2.0))
        self.running = True
        self.real_fps = 0.0
        
        self.is_recording = False
        self.record_frames = []
        self.record_target_count = 0
        self.record_session_id = ""

        # 如果初始状态为激活，则立刻打开相机
        if self.is_active:
            self._open_camera()

        self.thread = threading.Thread(target=self._update, name=f"Thread-{name}", daemon=True)
        self.thread.start()

    def _open_camera(self):
        """尝试打开底层摄像头硬件"""
        if self.available: return
        self.cap = cv2.VideoCapture(self.src, cv2.CAP_V4L2)
        if not self.cap.isOpened():
            print(f"⚠️ [{self.name}] 摄像头打不开！请检查 /dev/video{self.src}")
            self.available = False
        else:
            self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            # 注释掉 FPS 设置以防触发驱动重置 (根据你之前的硬件反馈)
            self.cap.set(cv2.CAP_PROP_FPS, self.target_fps)
            self.available = True
            print(f"✅ [{self.name}] 摄像头硬件已连接并初始化。")

    def _close_camera(self):
        """释放底层硬件"""
        self.available = False
        if self.cap:
            self.cap.release()
            self.cap = None
        self.buffer.clear()
        self.real_fps = 0.0
        print(f"💤 [{self.name}] 摄像头已释放硬件，进入休眠。")

    def set_active(self, state):
        """供外部(网页)调用的开关接口"""
        if state and not self.is_active:
            print(f"🔄 准备唤醒 {self.name} 相机...")
            self._open_camera()
            self.is_active = True
        elif not state and self.is_active:
            print(f"🔄 准备休眠 {self.name} 相机...")
            self.is_active = False
            self._close_camera()

    def start_record(self, session_id, duration_sec=3):
        if not self.is_active or not self.available or self.is_recording:
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
            # 如果处于休眠状态，或者硬件不可用，则挂起线程
            if not self.is_active or not self.available:
                time.sleep(0.5)
                continue
                
            ret, frame = self.cap.read()
            if ret:
                curr_ns = time.time_ns() 
                self.buffer.append(frame)
                
                if self.is_recording:
                    self.record_frames.append((curr_ns, frame.copy()))
                    if len(self.record_frames) >= self.record_target_count:
                        self.is_recording = False
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
        save_path = os.path.join(SAVE_DIR, session_id, self.name)
        os.makedirs(save_path, exist_ok=True)
        print(f"⏳ [{self.name}] 正在保存 {len(frames_to_save)} 张图片...")
        for timestamp_ns, f in frames_to_save:
            filename = os.path.join(save_path, f"{timestamp_ns}.jpg")
            cv2.imwrite(filename, f, [cv2.IMWRITE_JPEG_QUALITY, 95])
        print(f"💾 [{self.name}] 图片保存完成！")
        # 🚀 核心救命代码加在这里：强制同步磁盘！
        os.sync()       
        print(f"✅ [{self.name}] 磁盘同步完成，数据已绝对安全！")

    def get_latest_frame(self):
        if not self.is_active:
            # 休眠时返回灰屏提示
            idle_img = np.zeros((480, 640, 3), dtype=np.uint8)
            idle_img[:] = (50, 50, 50)
            cv2.putText(idle_img, f"{self.name} STANDBY", (180, 240), 
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (150, 150, 150), 2)
            return idle_img
            
        if not self.available or not self.buffer:
            error_img = np.zeros((480, 640, 3), dtype=np.uint8)
            cv2.putText(error_img, f"{self.name} NO SIGNAL (/dev/video{self.src})", 
                        (50, 240), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
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

cam_left = HighSpeedCamera(src=0, name="Left", fps=200)
cam_right = HighSpeedCamera(src=2, name="Right", fps=200)


@app.route('/')
def index():
    return render_template_string('''
        <html>
            <head>
                <title>双路高速感知终端</title>
                //<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
                <style>
                    body { background: #1a1a1a; color: #00ff00; font-family: monospace; text-align: center; margin: 0; padding: 20px;}
                    .container { display: flex; justify-content: center; gap: 20px; padding: 10px; flex-wrap: wrap;}
                    .camera-box { border: 2px solid #333; background: #000; padding: 10px; border-radius: 8px; transition: 0.3s; }
                    img { width: 480px; height: 360px; background: #222; transition: 0.3s;}
                    .single-mode img { width: 640px; height: 480px; }
                    .stats { margin-top: 10px; font-size: 1.2em; }
                    .top-bar { display: flex; justify-content: space-between; align-items: center; width: 90%; margin: 0 auto; }
                    .toggle-container { background: #222; padding: 10px 20px; border-radius: 20px; border: 1px solid #444;}
                    .switch { position: relative; display: inline-block; width: 60px; height: 34px; vertical-align: middle; margin-left: 10px;}
                    .switch input { opacity: 0; width: 0; height: 0; }
                    .slider { position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background-color: #555; transition: .4s; border-radius: 34px;}
                    .slider:before { position: absolute; content: ""; height: 26px; width: 26px; left: 4px; bottom: 4px; background-color: white; transition: .4s; border-radius: 50%;}
                    input:checked + .slider { background-color: #00aaff; }
                    input:checked + .slider:before { transform: translateX(26px); }

                    .dashboard { margin: 10px auto; width: 90%; border: 2px solid #00aaff; background: #001122; border-radius: 10px; padding: 20px; box-shadow: 0 0 15px rgba(0, 170, 255, 0.3); }
                    .dash-metrics { display: flex; justify-content: space-around; margin-bottom: 20px; }
                    .metric-box { background: rgba(0, 0, 0, 0.5); padding: 10px 30px; border-radius: 8px; border: 1px solid #333; min-width: 200px; }
                    .metric-label { font-size: 1em; color: #888; }
                    .metric-value { font-size: 2.5em; font-weight: bold; margin-top: 5px; }
                    .val-pos { color: #00aaff; } 
                    .val-spd { color: #ff8800; } 
                    .charts-container { display: flex; justify-content: space-between; gap: 20px; height: 250px; }
                    .chart-box { flex: 1; background: #0a0a0a; border: 1px solid #333; border-radius: 8px; padding: 10px; }

                    .btn-group { margin-top: 20px; }
                    button { background: #ff4444; color: white; border: none; padding: 15px 30px; font-size: 18px; font-weight: bold; cursor: pointer; border-radius: 5px; transition: 0.3s;}
                    button:hover { background: #cc0000; transform: scale(1.05); }
                    button:disabled { background: #555; cursor: not-allowed; transform: none; }
                </style>
            </head>
            <body>
                <div class="top-bar">
                    <h1>🚗  CAR  </h1>
                    <div class="toggle-container">
                        <span>双目模式 (Right Cam)</span>
                        <label class="switch">
                            <input type="checkbox" id="cam-toggle" onchange="toggleRightCam()">
                            <span class="slider"></span>
                        </label>
                    </div>
                </div>
                
                <div class="dashboard">
                    <div class="dash-metrics">
                        <div class="metric-box">
                            <div class="metric-label">累计圈数 (Revolutions)</div>
                            <div class="metric-value val-pos" id="sensor-pos">0.00</div>
                        </div>
                        <div class="metric-box">
                            <div class="metric-label">瞬时转速 (RPM)</div>
                            <div class="metric-value val-spd" id="sensor-speed">0.0</div>
                        </div>
                    </div>
                    <div class="charts-container">
                        <div class="chart-box"><canvas id="posChart"></canvas></div>
                        <div class="chart-box"><canvas id="speedChart"></canvas></div>
                    </div>
                </div>

                <div class="container" id="video-container">
                    <div class="camera-box single-mode" id="box-left">
                        <h3>LEFT CAMERA</h3>
                        <img src="/video_feed/left">
                        <div class="stats" id="fps-left">FPS: 0.0</div>
                    </div>
                    <div class="camera-box" id="box-right" style="display: none;">
                        <h3>RIGHT CAMERA</h3>
                        <img src="/video_feed/right" id="img-right">
                        <div class="stats" id="fps-right">STANDBY</div>
                    </div>
                </div>
                
                <div class="btn-group">
                    <button id="rec-btn" onclick="startRecord()">🔴 记录多源融合数据 (3秒)</button>
                </div>

                <script>
                    const PPR = 12.0; // 编码器分辨率：每圈12个脉冲

                    function toggleRightCam() {
                        const isChecked = document.getElementById('cam-toggle').checked;
                        const rightBox = document.getElementById('box-right');
                        const leftBox = document.getElementById('box-left');
                        fetch(`/toggle_cam?state=${isChecked ? 'on' : 'off'}`);
                        if (isChecked) {
                            rightBox.style.display = 'block';
                            leftBox.classList.remove('single-mode');
                            document.getElementById('img-right').src = "/video_feed/right?" + new Date().getTime();
                        } else {
                            rightBox.style.display = 'none';
                            leftBox.classList.add('single-mode');
                            document.getElementById('fps-right').innerText = "STANDBY";
                        }
                    }

                    const maxDataPoints = 50;
                    let timeTicks = 0;
                    const commonOptions = {
                        responsive: true, maintainAspectRatio: false, animation: false,
                        scales: { x: { display: false }, y: { grid: { color: '#333' }, ticks: { color: '#888' } } },
                        plugins: { legend: { display: false } }
                    };

                    const posChart = new Chart(document.getElementById('posChart').getContext('2d'), {
                        type: 'line',
                        data: { labels: [], datasets: [{ label: 'Revolutions', data: [], borderColor: '#00aaff', borderWidth: 2, pointRadius: 0, tension: 0.1 }] },
                        options: { ...commonOptions, plugins: { title: { display: true, text: 'Position (Revolutions)', color: '#00aaff' } } }
                    });

                    const speedChart = new Chart(document.getElementById('speedChart').getContext('2d'), {
                        type: 'line',
                        data: { labels: [], datasets: [{ label:'RPM', data: [], borderColor: '#ff8800', borderWidth: 2, pointRadius: 0, tension: 0.1 }] },
                        options: { ...commonOptions, plugins: { title: { display: true, text: 'Speed (RPM)', color: '#ff8800' } } }
                    });

                    setInterval(() => {
                        fetch('/fps_stats').then(r => r.json()).then(data => {
                            document.getElementById('fps-left').innerText = "实时: " + data.left + " FPS";
                            if (document.getElementById('cam-toggle').checked) {
                                document.getElementById('fps-right').innerText = "实时: " + data.right + " FPS";
                            }
                        });
                    }, 1000);

                    // --- 核心：在此处进行单位换算 ---
                    setInterval(() => {
                        fetch('/sensor_stats').then(r => r.json()).then(data => {
                            // 换算逻辑：
                            // 1. 圈数 (Revs) = 总脉冲 / PPR
                            const revolutions = (parseFloat(data.position) / PPR).toFixed(2);
                            
                            // 2. RPM = (脉冲/秒 / PPR) * 60秒
                            const rpm = ((parseFloat(data.speed) / PPR) * 60).toFixed(1);

                            document.getElementById('sensor-pos').innerText = revolutions;
                            document.getElementById('sensor-speed').innerText = rpm;

                            posChart.data.labels.push(timeTicks);
                            posChart.data.datasets[0].data.push(revolutions);
                            speedChart.data.labels.push(timeTicks);
                            speedChart.data.datasets[0].data.push(rpm);

                            if (posChart.data.labels.length > maxDataPoints) {
                                posChart.data.labels.shift(); posChart.data.datasets[0].data.shift();
                                speedChart.data.labels.shift(); speedChart.data.datasets[0].data.shift();
                            }
                            posChart.update(); speedChart.update();
                            timeTicks++;
                        });
                    }, 100);

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
                                        btn.innerText = "💾 正在写入硬盘和SD卡...";
                                        setTimeout(() => {
                                            btn.disabled = false;
                                            btn.innerText = "🔴 记录多源融合数据 (3秒)";
                                        }, 2500); 
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
        # 休眠时减慢轮询速率节省 CPU
        time.sleep(0.04 if camera.is_active else 0.5) 

@app.route('/video_feed/left')
def video_left():
    return Response(gen_stream(cam_left), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/video_feed/right')
def video_right():
    return Response(gen_stream(cam_right), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/fps_stats')
def fps_stats():
    return jsonify({"left": f"{cam_left.real_fps:.1f}", "right": f"{cam_right.real_fps:.1f}"})

@app.route('/sensor_stats')
def sensor_stats():
    return jsonify({
        "position": sensor_state["position"],
        "speed": f"{sensor_state['speed_pps']:.1f}"
    })

# --- 新增：接收前端开关右摄像头的指令 ---
@app.route('/toggle_cam')
def toggle_cam():
    state_str = request.args.get('state', 'off')
    is_on = (state_str == 'on')
    cam_right.set_active(is_on)
    return jsonify({"status": "success", "right_active": is_on})

@app.route('/start_record')
def start_record():
    session_id = time.strftime("%Y%m%d_%H%M%S")
    # 左相机必定录制
    cam_left.start_record(session_id=session_id, duration_sec=3)
    
    # 只有当右相机激活时才录制右侧
    if cam_right.is_active:
        cam_right.start_record(session_id=session_id, duration_sec=3)
        
    print(f"📢 开始录制批次: {session_id} (单目/双目模式已自动识别)")
    
    if esp32_serial:
        esp32_serial.write(b's') 
        def stop_esp_recording():
            time.sleep(3.0) 
            esp32_serial.write(b'p') 
        threading.Thread(target=stop_esp_recording, daemon=True).start()
    
    return jsonify({"status": "started"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, threaded=True)