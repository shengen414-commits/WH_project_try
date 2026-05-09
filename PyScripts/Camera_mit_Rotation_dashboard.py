import cv2
import os
import time
import threading
import serial
import re
import numpy as np
from collections import deque
from flask import Flask, Response, render_template, jsonify, request

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
    "last_pos": 0,
    "mode": "WEB",      # 新增：当前控制权
    "throttle": 1500    # 新增：当前真实油门
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
    drive_pattern = re.compile(r"\[DRIVE\] 模式:\s*(RC|WEB)\s*\|\s*油门:\s*(\d+)")
    
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
                else:
                    #   新增：如果不是传感器数据，看看是不是驱动状态数据！
                    match_drive = drive_pattern.search(line)
                    if match_drive:
                        sensor_state["mode"] = match_drive.group(1)
                        sensor_state["throttle"] = int(match_drive.group(2))
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
            
            # 🚀 极其关键的漏网之鱼：强行把底层缓存池设为 1，拒绝积压历史画面！
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            
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
    return render_template('index.html')

def gen_stream(camera):
    while True:
        frame = camera.get_latest_frame()
        if frame is not None:
            # 🚀 降维打击：长宽缩小一半，极大减轻网络和手机浏览器的解码负担
            small_frame = cv2.resize(frame, (320, 240))
            
            ret, buffer = cv2.imencode('.jpg', small_frame, [cv2.IMWRITE_JPEG_QUALITY, 60])
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
        "speed": f"{sensor_state['speed_pps']:.1f}",
        "mode": sensor_state["mode"],         # 🚀 新增
        "throttle": sensor_state["throttle"]  # 🚀 新增
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

# --- 新增：接收前端油门控制指令 ---
@app.route('/set_throttle')
def set_throttle():
    # 默认油门为 1500 (中位/停止)
    val_str = request.args.get('val', '1500') 
    try:
        val = int(val_str)
        # 安全断言保护
        if 1000 <= val <= 2000:
            if esp32_serial and esp32_serial.is_open:
                # 按照 ESP32 设定的协议，发送 "T1600\n"
                command = f"T{val}\n"
                esp32_serial.write(command.encode('utf-8'))
                print(f"🎮 下发油门指令: {val} us")
                return jsonify({"status": "success", "throttle": val})
            else:
                return jsonify({"status": "error", "message": "串口未连接"}), 500
        else:
            return jsonify({"status": "error", "message": "油门值越界"}), 400
    except ValueError:
        return jsonify({"status": "error", "message": "无效的油门数值"}), 400
    
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, threaded=True)