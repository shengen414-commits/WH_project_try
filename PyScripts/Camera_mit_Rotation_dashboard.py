import cv2
import bisect
import csv
import json
import os
import time
import threading
import serial
import re
import numpy as np
from collections import deque
from datetime import datetime
from flask import Flask, Response, render_template, jsonify, request
try:
    from .telemetry import EncoderStream, CsvRecorder
except ImportError:
    from telemetry import EncoderStream, CsvRecorder

# 确保录像保存根目录存在
SAVE_DIR = "Car_Records"
os.makedirs(SAVE_DIR, exist_ok=True)
SNAPSHOT_DIR = os.path.join(SAVE_DIR, "Snapshots")
os.makedirs(SNAPSHOT_DIR, exist_ok=True)
PPR = 12.0
KMH_PER_RPM = 0.007173
SPEED_RECORD_DIR = os.path.join(SAVE_DIR, "Speed_Records")
os.makedirs(SPEED_RECORD_DIR, exist_ok=True)
SPEED_RECORD_MAX_DURATION_SEC = 10 * 60
SERIAL_DEBUG_PRINT = True
SERIAL_DEBUG_MIN_INTERVAL_SEC = 0.5
ENCODER_FRESH_SEC = 1.0
BRAKE_REVERSE_DURATION_SEC = 2.00
BRAKE_REVERSE_MIN_DELTA = 80
BRAKE_REVERSE_MAX_DELTA = 300
BRAKE_REVERSE_DEADBAND_DELTA = 50

# =================================================================
# 传感器后台数据读取与速度计算线程
# =================================================================
sensor_state = {
    "position": 0,
    "speed_pps": 0.0,
    "last_time_ms": 0,
    "last_pos": 0,
    "mode": "WEB",      # 新增：当前控制权
    "throttle": 1500,   # 新增：当前真实油门
    "last_serial_line": "",
    "last_serial_rx_wall": 0.0,
    "serial_line_count": 0,
    "last_drive_line": "",
}

try:
    esp32_serial = serial.Serial(
        os.environ.get('ESP32_SERIAL_PORT', '/dev/ttyUSB0'), 115200, timeout=0.01, write_timeout=0.05)
    esp32_serial.reset_input_buffer()
    print("已清空启动积压数据！")
    print("✅ 成功连接到 ESP32 霍尔传感器模块！")
except Exception as e:
    print(f"⚠️ 无法连接到 ESP32: {e}")
    esp32_serial = None


serial_write_lock = threading.Lock()
record_command_lock = threading.Lock()
snapshot_lock = threading.Lock()
record_workflow_active = threading.Event()
brake_sequence_active = threading.Event()
brake_sequence_lock = threading.Lock()
brake_sequence_token = 0
speed_record_lock = threading.Lock()
speed_recorder = None
session_recorder = None
session_status = {"active": False, "status": "idle"}
encoder_stream = EncoderStream(PPR, KMH_PER_RPM)
estop_ignore_until = 0.0


def clamp_throttle_value(value, fallback=1500):
    try:
        return max(1000, min(2000, int(value)))
    except (TypeError, ValueError):
        return fallback


def calculate_reverse_brake_pwm(current_pwm):
    current_pwm = clamp_throttle_value(current_pwm)
    delta = current_pwm - 1500
    if abs(delta) <= BRAKE_REVERSE_DEADBAND_DELTA:
        return 1500

    reverse_delta = int(round(abs(delta) * BRAKE_REVERSE_MAX_DELTA / 500.0))
    reverse_delta = max(BRAKE_REVERSE_MIN_DELTA, min(BRAKE_REVERSE_MAX_DELTA, reverse_delta))
    return 1500 - reverse_delta if delta > 0 else 1500 + reverse_delta


def write_esp32_throttle(pwm):
    command = f"T{clamp_throttle_value(pwm)}\n"
    with serial_write_lock:
        esp32_serial.write(command.encode('utf-8'))


def write_esp32_boost(pwm):
    command = f"B{clamp_throttle_value(pwm)}\n"
    with serial_write_lock:
        esp32_serial.write(command.encode('utf-8'))


def encoder_ready():
    return bool(esp32_serial and esp32_serial.is_open and
                time.monotonic() - sensor_state.get("last_encoder_rx_mono", 0) < ENCODER_FRESH_SEC)


def speed_record_public_state():
    with speed_record_lock:
        recorder = speed_recorder
    if recorder is None:
        return {"active": False, "sample_count": 0, "elapsed_sec": 0,
                "max_duration_sec": SPEED_RECORD_MAX_DURATION_SEC}
    state = recorder.status()
    state.update({"max_duration_sec": SPEED_RECORD_MAX_DURATION_SEC,
                  "session_id": os.path.splitext(os.path.basename(recorder.path))[0],
                  "started_at_iso": datetime.fromtimestamp(recorder.started_ns / 1e9).astimezone().isoformat(),
                  "stopped_at_iso": datetime.fromtimestamp(recorder.stopped_ns / 1e9).astimezone().isoformat()
                  if recorder.stopped_ns else None})
    return state


def start_speed_recording():
    global speed_recorder
    with record_command_lock:
        with speed_record_lock:
            if speed_recorder and not speed_recorder.done.is_set():
                error = "上一份速度记录仍在运行或保存"
            elif not encoder_ready():
                error = "没有新鲜编码器数据，请连接 ESP32 并烧录无 SD 版本固件"
            else:
                error = None
                session_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                try:
                    speed_recorder = CsvRecorder(os.path.join(SPEED_RECORD_DIR, f"speed_record_{session_id}.csv"),
                                                 SPEED_RECORD_MAX_DURATION_SEC)
                except OSError as exc:
                    error = f"无法创建速度文件: {exc}"
    state = speed_record_public_state()
    if error:
        state["message"] = error
    return error is None, state


def stop_speed_recording():
    with record_command_lock:
        with speed_record_lock:
            recorder = speed_recorder
        if recorder is None or not recorder.status()["active"]:
            return False, speed_record_public_state()
        recorder.stop()
        recorder.done.wait(2.0)
    return True, speed_record_public_state()


def build_image_index(session_dir, camera_name):
    camera_dir = os.path.join(session_dir, camera_name)
    image_index = []
    if not os.path.isdir(camera_dir):
        return image_index

    for name in os.listdir(camera_dir):
        stem, ext = os.path.splitext(name)
        if ext.lower() != ".jpg" or not stem.isdigit():
            continue
        timestamp_ns = int(stem)
        image_index.append((timestamp_ns, os.path.join(camera_name, name)))

    image_index.sort(key=lambda item: item[0])
    return image_index


def find_nearest_image(timestamp_ns, image_index):
    if not image_index:
        return "", ""

    timestamps = [item[0] for item in image_index]
    insert_at = bisect.bisect_left(timestamps, timestamp_ns)
    candidates = []
    if insert_at < len(image_index):
        candidates.append(image_index[insert_at])
    if insert_at > 0:
        candidates.append(image_index[insert_at - 1])

    image_ts, image_path = min(
        candidates, key=lambda item: abs(item[0] - timestamp_ns))
    delta_ms = (image_ts - timestamp_ns) / 1_000_000.0
    return image_path, f"{delta_ms:.3f}"


def read_raw_sensor_rows(raw_csv_path):
    rows = []
    with open(raw_csv_path, "r", encoding="utf-8", errors="ignore", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                rows.append({
                    "esp32_time_ms": int(row["Time_ms"]),
                    "position": int(row["Position"]),
                    "received_ns": int(row["unix_time_ns"]),
                    "device_epoch": int(row["device_epoch"]),
                })
            except (KeyError, TypeError, ValueError):
                continue
    return rows


def write_enhanced_sensor_csv(session_id, raw_csv_path, record_start_ns, record_stop_ns):
    session_dir = os.path.join(SAVE_DIR, session_id)
    enhanced_csv = os.path.join(session_dir, "sensor_data_enhanced.csv")
    sync_meta_path = os.path.join(session_dir, "sync_meta.json")
    rows = read_raw_sensor_rows(raw_csv_path)

    if not rows:
        print(f"⚠️ 批次 {session_id} 的原始传感器CSV为空，无法生成增强版。")
        return False

    esp_first_ms = rows[0]["esp32_time_ms"]
    esp_last_ms = rows[-1]["esp32_time_ms"]

    left_images = build_image_index(session_dir, "Left")
    right_images = build_image_index(session_dir, "Right")

    fieldnames = [
        "esp32_time_ms",
        "esp32_elapsed_ms",
        "device_epoch",
        "host_received_time_ns",
        "python_time_ns_est",
        "python_elapsed_ms_est",
        "position",
        "rpm",
        "kmh",
        "left_image",
        "left_image_delta_ms",
        "right_image",
        "right_image_delta_ms",
    ]

    previous_row = None
    epoch_start_ms = esp_first_ms
    with open(enhanced_csv, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for row in rows:
            if previous_row and row["device_epoch"] != previous_row["device_epoch"]:
                epoch_start_ms = row["esp32_time_ms"]
            esp_elapsed_ms = (row["esp32_time_ms"] - epoch_start_ms) & 0xFFFFFFFF
            python_time_ns_est = row["received_ns"]

            rpm = 0.0
            if previous_row is not None:
                dt_ms = (row["esp32_time_ms"] - previous_row["esp32_time_ms"]) & 0xFFFFFFFF
                if row["device_epoch"] == previous_row["device_epoch"] and 0 < dt_ms < 0x80000000:
                    delta_pos = ((row["position"] - previous_row["position"] + 0x80000000) & 0xFFFFFFFF) - 0x80000000
                    pulses_per_sec = delta_pos / (dt_ms / 1000.0)
                    rpm = (pulses_per_sec / PPR) * 60.0

            kmh = abs(rpm) * KMH_PER_RPM
            left_image, left_delta_ms = find_nearest_image(
                python_time_ns_est, left_images)
            right_image, right_delta_ms = find_nearest_image(
                python_time_ns_est, right_images)

            writer.writerow({
                "esp32_time_ms": row["esp32_time_ms"],
                "esp32_elapsed_ms": esp_elapsed_ms,
                "device_epoch": row["device_epoch"],
                "host_received_time_ns": python_time_ns_est,
                "python_time_ns_est": python_time_ns_est,
                "python_elapsed_ms_est": f"{(python_time_ns_est - record_start_ns) / 1_000_000.0:.3f}",
                "position": row["position"],
                "rpm": f"{rpm:.3f}",
                "kmh": f"{kmh:.3f}",
                "left_image": left_image,
                "left_image_delta_ms": left_delta_ms,
                "right_image": right_image,
                "right_image_delta_ms": right_delta_ms,
            })
            previous_row = row

    sync_meta = {
        "session_id": session_id,
        "raw_sensor_csv": "sensor_data.csv",
        "enhanced_sensor_csv": "sensor_data_enhanced.csv",
        "source": "esp32_serial_stream_v1",
        "python_record_start_ns": record_start_ns,
        "python_record_stop_ns": record_stop_ns,
        "esp32_first_time_ms": esp_first_ms,
        "esp32_last_time_ms": esp_last_ms,

        "mapping": "host serial reception timestamp; camera timestamps are host frame-read times, not exposure times",
        "ppr": PPR,
        "kmh_per_rpm": KMH_PER_RPM,
        "left_image_count": len(left_images),
        "right_image_count": len(right_images),
    }
    with open(sync_meta_path, "w", encoding="utf-8") as f:
        json.dump(sync_meta, f, ensure_ascii=False, indent=2)

    print(f"✅ 增强版传感器数据已生成: {enhanced_csv}")
    return True


def read_esp32_data():
    """Read complete frames without clearing backlog or writing files here."""
    if esp32_serial is None:
        return
    drive_pattern = re.compile(r"\[DRIVE\].*?\b(RC|WEB)\b.*?(\d{3,4})")
    pending = bytearray()
    last_debug = 0.0
    while True:
        try:
            chunk = esp32_serial.read(min(max(esp32_serial.in_waiting, 1), 4096))
            if not chunk:
                continue
            pending.extend(chunk)
            while b'\n' in pending:
                raw_line, _, remainder = pending.partition(b'\n')
                pending = bytearray(remainder)
                line = raw_line.decode('utf-8', errors='ignore').strip()
                now_ns = time.time_ns()
                sensor_state["serial_error"] = None
                sensor_state["last_serial_line"] = line
                sensor_state["last_serial_rx_wall"] = now_ns / 1e9
                sensor_state["serial_line_count"] += 1
                sample = encoder_stream.decode(line, now_ns, sensor_state["throttle"], sensor_state["mode"])
                if sample is not None:
                    sensor_state.update({"position": sample["Position"], "speed_pps": sample["speed_pps"],
                                         "last_time_ms": sample["Time_ms"], "last_pos": sample["Position"],
                                         "sequence_gaps": sample["sequence_gaps"],
                                         "device_epoch": sample["device_epoch"], "last_encoder_rx_mono": time.monotonic()})
                    with speed_record_lock:
                        for recorder in (speed_recorder, session_recorder):
                            if recorder is not None:
                                recorder.submit(sample)
                else:
                    match = drive_pattern.search(line)
                    if match:
                        sensor_state["mode"] = match.group(1)
                        sensor_state["throttle"] = int(match.group(2))
                        sensor_state["last_drive_line"] = line
                    elif line.startswith('[ENC]'):
                        sensor_state["invalid_encoder_lines"] = sensor_state.get("invalid_encoder_lines", 0) + 1
                now = time.monotonic()
                if SERIAL_DEBUG_PRINT and now - last_debug >= SERIAL_DEBUG_MIN_INTERVAL_SEC:
                    print(f"ESP32 RX >> {line}")
                    last_debug = now
            if len(pending) > 4096:
                pending.clear()
                sensor_state["serial_framing_errors"] = sensor_state.get("serial_framing_errors", 0) + 1
        except Exception as exc:
            sensor_state["serial_error"] = str(exc)
            time.sleep(0.1)


threading.Thread(target=read_esp32_data, daemon=True).start()

# =================================================================
# 核心：高帧率后台“黑匣子”线程
# =================================================================


class HighSpeedCamera:
    def __init__(self, src=0, name="Cam", fps=210):
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

        # 这里只供网页预览和单张快照读取“最新帧”；高速录像有独立列表。
        # 保留 2 帧即可，避免常驻 420 张 640x480 BGR 图像（约 369 MiB）。
        self.buffer = deque(maxlen=2)
        self.latest_frame_ns = 0
        self.running = True
        self.real_fps = 0.0

        self.is_recording = False
        self.record_frames = []
        self.record_session_id = ""
        self.record_lock = threading.Lock()
        self.record_done = threading.Event()
        self.record_done.set()
        self.record_error = None
        self.record_deadline = 0.0

        # 如果初始状态为激活，则立刻打开相机
        if self.is_active:
            self._open_camera()

        self.thread = threading.Thread(
            target=self._update, name=f"Thread-{name}", daemon=True)
        self.thread.start()

    def _open_camera(self):
        """尝试打开底层摄像头硬件"""
        if self.available:
            return
        self.cap = cv2.VideoCapture(self.src, cv2.CAP_V4L2)
        if not self.cap.isOpened():
            print(f"⚠️ [{self.name}] 摄像头打不开！请检查 /dev/video{self.src}")
            self.available = False
        else:
            self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            # 请求摄像头实际支持的 210 FPS 高速档位。
            fps_set = self.cap.set(cv2.CAP_PROP_FPS, self.target_fps)

            # 不再强制 CAP_PROP_BUFFERSIZE=1。OpenCV V4L2 默认使用 4 个
            # mmap 缓冲，使摄像头采集下一帧和当前帧解码能够流水并行。
            actual_fourcc_value = int(self.cap.get(cv2.CAP_PROP_FOURCC))
            actual_fourcc = "".join(
                chr((actual_fourcc_value >> (8 * i)) & 0xFF) for i in range(4)
            )
            actual_width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            actual_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            actual_fps = self.cap.get(cv2.CAP_PROP_FPS)
            actual_buffers = int(self.cap.get(cv2.CAP_PROP_BUFFERSIZE))

            self.available = True
            print(
                f"✅ [{self.name}] 摄像头已初始化: "
                f"{actual_fourcc} {actual_width}x{actual_height} "
                f"@ {actual_fps:.1f} FPS, V4L2 buffers={actual_buffers}, "
                f"fps_set={fps_set}"
            )

    def _close_camera(self):
        """释放底层硬件"""
        self.available = False
        if self.cap:
            self.cap.release()
            self.cap = None
        self.buffer.clear()
        self.latest_frame_ns = 0
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
        with self.record_lock:
            if not self.is_active or not self.available or not self.record_done.is_set():
                return False
            self.record_frames = []
            self.record_session_id = session_id
            self.record_deadline = time.monotonic() + duration_sec
            self.record_error = None
            self.record_done.clear()
            self.is_recording = True
            return True

    def _finish_record_locked(self):
        if not self.is_recording:
            return
        self.is_recording = False
        frames = self.record_frames
        self.record_frames = []
        session_id = self.record_session_id

        def save():
            try:
                self._save_images_to_disk(frames, session_id)
                if not frames:
                    self.record_error = "录制期间没有收到相机帧"
            except Exception as exc:
                self.record_error = str(exc)
                print(f"相机保存失败 [{self.name}]: {exc}")
            finally:
                self.record_done.set()
        threading.Thread(target=save, name=f"Save-{self.name}", daemon=True).start()

    def finish_record(self):
        with self.record_lock:
            self._finish_record_locked()

    def _update(self):
        stat_started_at = time.perf_counter()
        stat_frame_count = 0
        while self.running:
            # 如果处于休眠状态，或者硬件不可用，则挂起线程
            if not self.is_active or not self.available:
                stat_started_at = time.perf_counter()
                stat_frame_count = 0
                time.sleep(0.5)
                continue

            ret, frame = self.cap.read()
            if ret:
                curr_ns = time.time_ns()
                self.buffer.append(frame)
                self.latest_frame_ns = curr_ns

                with self.record_lock:
                    if self.is_recording:
                        if time.monotonic() < self.record_deadline:
                            self.record_frames.append((curr_ns, frame))
                        else:
                            self._finish_record_locked()

                stat_frame_count += 1
                if stat_frame_count >= 100:
                    stat_now = time.perf_counter()
                    elapsed = stat_now - stat_started_at
                    if elapsed > 0:
                        self.real_fps = stat_frame_count / elapsed
                    stat_started_at = stat_now
                    stat_frame_count = 0
            else:
                time.sleep(0.01)

    def _save_images_to_disk(self, frames_to_save, session_id):
        save_path = os.path.join(SAVE_DIR, session_id, self.name)
        os.makedirs(save_path, exist_ok=True)
        print(f"⏳ [{self.name}] 正在保存 {len(frames_to_save)} 张图片...")
        for timestamp_ns, f in frames_to_save:
            filename = os.path.join(save_path, f"{timestamp_ns}.jpg")
            if not cv2.imwrite(filename, f, [cv2.IMWRITE_JPEG_QUALITY, 95]):
                raise OSError(f"图片写入失败: {filename}")
        print(f"💾 [{self.name}] 图片保存完成！")
        # Linux 上请求将图片同步到磁盘；异常由保存线程报告。
        if hasattr(os, "sync"):
            os.sync()
        print(f"✅ [{self.name}] 图片写入和同步完成")

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
            cv2.putText(frame, "REC", (50, 38),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        return frame

    def get_latest_raw_frame(self):
        """Return the newest unannotated frame for a still-image snapshot."""
        if not self.is_active or not self.available or not self.buffer:
            return None, None
        try:
            return self.latest_frame_ns, self.buffer[-1].copy()
        except IndexError:
            return None, None


# =================================================================
# Flask 逻辑
# =================================================================
app = Flask(__name__)

cam_left = HighSpeedCamera(src=0, name="Left", fps=210)
cam_right = HighSpeedCamera(src=2, name="Right", fps=210)


@app.route('/')
def index():
    return render_template('index.html')


def gen_stream(camera):
    while True:
        frame = camera.get_latest_frame()
        if frame is not None:
            # 🚀 降维打击：长宽缩小一半，极大减轻网络和手机浏览器的解码负担
            small_frame = cv2.resize(frame, (320, 240))

            ret, buffer = cv2.imencode('.jpg', small_frame, [
                                       cv2.IMWRITE_JPEG_QUALITY, 60])
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


@app.route('/capture_snapshot', methods=['POST'])
def capture_snapshot():
    with snapshot_lock:
        left_timestamp_ns, left_frame = cam_left.get_latest_raw_frame()
        right_timestamp_ns, right_frame = cam_right.get_latest_raw_frame()

        missing = []
        if left_frame is None:
            missing.append("left")
        if right_frame is None:
            missing.append("right")
        if missing:
            return jsonify({
                "status": "camera_unavailable",
                "message": f"请先开启并等待摄像头出图: {', '.join(missing)}",
                "missing": missing,
            }), 409

        snapshot_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        snapshot_path = os.path.join(SNAPSHOT_DIR, snapshot_id)
        os.makedirs(snapshot_path, exist_ok=False)
        left_path = os.path.join(snapshot_path, "left.jpg")
        right_path = os.path.join(snapshot_path, "right.jpg")

        left_saved = cv2.imwrite(
            left_path, left_frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
        right_saved = cv2.imwrite(
            right_path, right_frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
        if not left_saved or not right_saved:
            return jsonify({
                "status": "error",
                "message": "单张图片写入失败，请检查磁盘空间和目录权限",
            }), 500

    relative_folder = os.path.relpath(snapshot_path, SAVE_DIR)
    print(
        f"📸 双目单张已保存: {snapshot_path} "
        f"(left={left_timestamp_ns}, right={right_timestamp_ns})"
    )
    return jsonify({
        "status": "saved",
        "folder": relative_folder,
        "left": os.path.join(relative_folder, "left.jpg"),
        "right": os.path.join(relative_folder, "right.jpg"),
    })


@app.route('/sensor_stats')
def sensor_stats():
    return jsonify({
        "position": sensor_state["position"],
        "speed": f"{sensor_state['speed_pps']:.1f}",
        "mode": sensor_state["mode"],         # 🚀 新增
        "throttle": sensor_state["throttle"],
        "encoder_fresh": encoder_ready(),
        "sequence_gaps": sensor_state.get("sequence_gaps", 0),
        "device_epoch": sensor_state.get("device_epoch", 0),
        "invalid_encoder_lines": sensor_state.get("invalid_encoder_lines", 0),
        "serial_framing_errors": sensor_state.get("serial_framing_errors", 0),
        "serial_error": sensor_state.get("serial_error")
    })


@app.route('/speed_record/start', methods=['POST'])
def speed_record_start():
    started, state = start_speed_recording()
    status = "started" if started else "unavailable"
    return jsonify({"status": status, **state}), (200 if started else 409)


@app.route('/speed_record/stop', methods=['POST'])
def speed_record_stop():
    stopped, state = stop_speed_recording()
    status = "stopped" if stopped else "idle"
    return jsonify({"status": status, **state})


@app.route('/speed_record/status')
def speed_record_status():
    return jsonify({"status": "ok", **speed_record_public_state()})

# --- 新增：接收前端开关右摄像头的指令 ---


@app.route('/toggle_cam')
def toggle_cam():
    state_str = request.args.get('state', 'off')
    camera_name = request.args.get('camera', 'right')
    is_on = (state_str == 'on')
    if camera_name == 'left':
        cam_left.set_active(is_on)
        return jsonify({"status": "success", "camera": "left", "left_active": is_on})
    if camera_name == 'right':
        cam_right.set_active(is_on)
        return jsonify({"status": "success", "camera": "right", "right_active": is_on})
    return jsonify({"status": "error", "message": "unknown camera"}), 400


def finalize_session(recorder, session_id, cameras):
    global session_status
    try:
        recorder.done.wait()
        for camera in cameras:
            camera.finish_record()
        deadline = time.monotonic() + 30.0
        for camera in cameras:
            if not camera.record_done.wait(max(0, deadline - time.monotonic())):
                raise RuntimeError(f"{camera.name} 图片保存超时，原始 CSV 已保留")
            if camera.record_error:
                raise RuntimeError(f"{camera.name}: {camera.record_error}")
        state = recorder.status()
        if state["stop_reason"].startswith("error:"):
            raise RuntimeError(state["stop_reason"])
        enhanced = write_enhanced_sensor_csv(session_id, recorder.path, recorder.started_ns, recorder.stopped_ns)
        with open(os.path.join(SAVE_DIR, session_id, "record_stats.json"), "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        if not enhanced:
            raise RuntimeError("录制期间没有有效编码器数据，原始文件已保留")
        with speed_record_lock:
            session_status = {"active": False, "status": "saved", "session_id": session_id, **state}
    except Exception as exc:
        with speed_record_lock:
            session_status = {**recorder.status(), "active": False, "status": "error",
                              "session_id": session_id, "message": str(exc)}
        print(f"多源记录失败: {exc}")
    finally:
        record_workflow_active.clear()


@app.route('/record/status')
def record_status():
    with speed_record_lock:
        state = dict(session_status)
        if state.get("active") and session_recorder:
            live = session_recorder.status()
            state.update({key: live[key] for key in ("sample_count", "queue_drops", "sequence_gaps", "elapsed_sec")})
            state["status"] = "recording" if live["active"] else "saving"
    return jsonify(state)


@app.route('/start_record')
def start_record():
    global session_recorder, session_status
    with record_command_lock:
        if record_workflow_active.is_set():
            return jsonify({"status": "busy", "message": "上一批记录仍在保存"}), 409
        if not encoder_ready():
            return jsonify({"status": "unavailable", "message": "没有新鲜编码器数据，请连接 ESP32 并烧录无 SD 版本固件"}), 409
        cameras = [cam_left] + ([cam_right] if cam_right.is_active else [])
        if any(not cam.is_active or not cam.available or not cam.record_done.is_set() for cam in cameras):
            return jsonify({"status": "unavailable", "message": "请开启相机并等待其就绪或保存完成"}), 409
        session_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        try:
            with speed_record_lock:
                session_recorder = CsvRecorder(os.path.join(SAVE_DIR, session_id, "sensor_data.csv"), 3.0)
                recorder = session_recorder
                session_status = {"active": True, "status": "recording", "session_id": session_id}
            record_workflow_active.set()
            for camera in cameras:
                if not camera.start_record(session_id=session_id, duration_sec=3):
                    raise RuntimeError(f"{camera.name} 相机启动失败")
        except Exception as exc:
            if record_workflow_active.is_set():
                recorder.stop("start_failed")
                for camera in cameras:
                    camera.finish_record()
                record_workflow_active.clear()
                with speed_record_lock:
                    session_status = {"active": False, "status": "error", "message": str(exc)}
            return jsonify({"status": "error", "message": str(exc)}), 500
        threading.Thread(target=finalize_session, args=(recorder, session_id, cameras), daemon=True).start()
    return jsonify({"status": "started", "session_id": session_id})


# --- 新增：接收前端油门控制指令 ---


@app.route('/set_throttle', methods=['GET', 'POST'])
def set_throttle():
    global estop_ignore_until
    # 默认油门为 1500 (中位/停止)
    val_str = request.args.get('val', '1500')
    boost_enabled = request.args.get('boost', '0').lower() in ('1', 'true', 'yes', 'on')
    try:
        val = int(val_str)
        # 安全断言保护
        if 1000 <= val <= 2000:
            if brake_sequence_active.is_set():
                print(f"🛡️ 反向制动期间丢弃普通油门指令: {val} us")
                return jsonify({"status": "ignored", "reason": "brake_sequence_active", "throttle": sensor_state["throttle"]}), 409
            if time.monotonic() < estop_ignore_until:
                print(f"🛡️ 急停保护窗口内丢弃普通油门指令: {val} us")
                return jsonify({"status": "ignored", "reason": "estop_active", "throttle": 1500})
            if esp32_serial and esp32_serial.is_open:
                # 按照 ESP32 设定的协议，发送 "T1600\n"
                if boost_enabled:
                    write_esp32_boost(val)
                    print(f"🚀 下发填数增速指令: {val} us")
                else:
                    write_esp32_throttle(val)
                    print(f"🎮 下发油门指令: {val} us")
                return jsonify({"status": "success", "throttle": val, "boost": boost_enabled})
            else:
                return jsonify({"status": "error", "message": "串口未连接"}), 500
        else:
            return jsonify({"status": "error", "message": "油门值越界"}), 400
    except ValueError:
        return jsonify({"status": "error", "message": "无效的油门数值"}), 400
    except Exception as e:
        print(f"⚠️ set_throttle failed: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


def reverse_brake_sequence(token, source_pwm, brake_pwm):
    try:
        write_esp32_throttle(brake_pwm)
        sensor_state["throttle"] = brake_pwm
        print(f"🚨 [反向制动] 已下发 {brake_pwm} us，来源油门 {source_pwm} us")
        time.sleep(BRAKE_REVERSE_DURATION_SEC)

        with brake_sequence_lock:
            if token != brake_sequence_token:
                return

        write_esp32_throttle(1500)
        sensor_state["throttle"] = 1500
        print("✅ [反向制动] 2秒结束，已归中 1500 us")
    except Exception as e:
        print(f"⚠️ reverse_brake_sequence failed: {e}")
    finally:
        with brake_sequence_lock:
            if token == brake_sequence_token:
                brake_sequence_active.clear()


@app.route('/e_stop', methods=['GET', 'POST'])
def e_stop():
    """最高优先级：按当前方向反向小PWM制动2秒后归中。"""
    global estop_ignore_until, brake_sequence_token
    if esp32_serial and esp32_serial.is_open:
        payload = request.get_json(silent=True) or {}
        requested_pwm = clamp_throttle_value(
            request.args.get('val') or payload.get('val'),
            clamp_throttle_value(sensor_state.get("throttle", 1500))
        )
        sensed_pwm = clamp_throttle_value(sensor_state.get("throttle", 1500))
        source_pwm = requested_pwm if abs(requested_pwm - 1500) >= abs(sensed_pwm - 1500) else sensed_pwm
        brake_pwm = calculate_reverse_brake_pwm(source_pwm)
        estop_ignore_until = time.monotonic() + BRAKE_REVERSE_DURATION_SEC + 0.3

        with brake_sequence_lock:
            brake_sequence_token += 1
            token = brake_sequence_token
            brake_sequence_active.set()

        with serial_write_lock:
            esp32_serial.reset_output_buffer()

        threading.Thread(
            target=reverse_brake_sequence,
            args=(token, source_pwm, brake_pwm),
            daemon=True
        ).start()

        print(f"🚨 [反向制动] source={source_pwm} us -> brake={brake_pwm} us, {BRAKE_REVERSE_DURATION_SEC:.1f}s 后归中")
        return jsonify({
            "status": "success",
            "source_pwm": source_pwm,
            "brake_pwm": brake_pwm,
            "duration_sec": BRAKE_REVERSE_DURATION_SEC,
            "throttle": brake_pwm
        })
    else:
        return jsonify({"status": "error", "message": "串口未连接"}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, threaded=True)
