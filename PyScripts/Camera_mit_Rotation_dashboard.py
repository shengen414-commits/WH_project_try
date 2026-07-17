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

# 确保录像保存根目录存在
SAVE_DIR = "Car_Records"
os.makedirs(SAVE_DIR, exist_ok=True)
PPR = 12.0
KMH_PER_RPM = 0.007173
SPEED_RECORD_DIR = os.path.join(SAVE_DIR, "Speed_Records")
os.makedirs(SPEED_RECORD_DIR, exist_ok=True)
SPEED_RECORD_SAMPLE_INTERVAL_SEC = 0.01  # 速度记录到csv采样间隔,esp32的传感器采样长度在ESP32代码中更改
SPEED_RECORD_MAX_DURATION_SEC = 10 * 60
SD_COPY_SAFE_THROTTLE_DELTA = 20
SD_COPY_STOP_SPEED_PPS_THRESHOLD = 1.0
SD_COPY_STOP_STABLE_SEC = 1.2
SD_COPY_WAIT_LOG_SEC = 5.0
SD_COPY_TIMEOUT_SEC = 8.0
SERIAL_INPUT_FLUSH_THRESHOLD = 8192
SERIAL_DEBUG_PRINT = True
SERIAL_DEBUG_MIN_INTERVAL_SEC = 0.5
BRAKE_REVERSE_DURATION_SEC = 2.0
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
    "serial_flush_count": 0,
    "last_drive_line": "",
}

try:
    esp32_serial = serial.Serial(
        '/dev/ttyUSB0', 115200, timeout=0.1, write_timeout=0.05)
    esp32_serial.reset_input_buffer()
    print("已清空启动积压数据！")
    print("✅ 成功连接到 ESP32 霍尔传感器模块！")
except Exception as e:
    print(f"⚠️ 无法连接到 ESP32: {e}")
    esp32_serial = None


# 🚀 增加一个用于存储高频历史轨迹的队列 (记录过去10次的点)
history_buffer = deque(maxlen=10)
serial_write_lock = threading.Lock()
serial_transfer_active = threading.Event()
record_workflow_active = threading.Event()
brake_sequence_active = threading.Event()
brake_sequence_lock = threading.Lock()
brake_sequence_token = 0
speed_record_lock = threading.Lock()
speed_record_state = {
    "active": False,
    "session_id": None,
    "file_path": None,
    "started_at_ns": None,
    "started_at_iso": None,
    "stopped_at_ns": None,
    "stopped_at_iso": None,
    "sample_count": 0,
    "stop_reason": None,
    "stop_event": None,
    "thread": None,
    "esp32_recording": False,
}
estop_ignore_until = 0.0


def is_drive_neutral():
    return abs(int(sensor_state.get("throttle", 1500)) - 1500) <= SD_COPY_SAFE_THROTTLE_DELTA


def is_car_stopped():
    return is_drive_neutral() and abs(float(sensor_state.get("speed_pps", 0.0))) <= SD_COPY_STOP_SPEED_PPS_THRESHOLD


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


def get_current_speed_metrics():
    speed_pps = float(sensor_state.get("speed_pps", 0.0))
    rpm = (speed_pps / PPR) * 60.0
    kmh = abs(rpm) * KMH_PER_RPM
    return {
        "position": int(sensor_state.get("position", 0)),
        "speed_pps": speed_pps,
        "rpm": rpm,
        "kmh": kmh,
        "mode": sensor_state.get("mode", "WEB"),
        "throttle": int(sensor_state.get("throttle", 1500)),
    }


def speed_record_worker(session_id, file_path, started_at_ns, started_at_iso, stop_event, esp32_recording_started):
    fieldnames = [
        "iso_time",
        "unix_time_ns",
        "elapsed_ms",
        "rpm",
        "kmh",
        "position",
        "speed_pps",
        "throttle",
        "mode",
    ]
    stop_reason = "manual_stop"
    sample_count = 0

    try:
        with open(file_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            while not stop_event.is_set():
                now_ns = time.time_ns()
                elapsed_sec = (now_ns - started_at_ns) / 1_000_000_000.0
                if elapsed_sec >= SPEED_RECORD_MAX_DURATION_SEC:
                    stop_reason = "max_duration"
                    break

                metrics = get_current_speed_metrics()
                writer.writerow({
                    "iso_time": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                    "unix_time_ns": now_ns,
                    "elapsed_ms": f"{elapsed_sec * 1000.0:.3f}",
                    "rpm": f"{metrics['rpm']:.3f}",
                    "kmh": f"{metrics['kmh']:.3f}",
                    "position": metrics["position"],
                    "speed_pps": f"{metrics['speed_pps']:.3f}",
                    "throttle": metrics["throttle"],
                    "mode": metrics["mode"],
                })
                f.flush()
                sample_count += 1

                with speed_record_lock:
                    if speed_record_state.get("session_id") == session_id:
                        speed_record_state["sample_count"] = sample_count

                stop_event.wait(SPEED_RECORD_SAMPLE_INTERVAL_SEC)
    except Exception as e:
        stop_reason = f"error: {e}"
        print(f"⚠️ 速度记录异常: {e}")
    finally:
        if esp32_recording_started:
            try:
                if esp32_serial and esp32_serial.is_open:
                    with serial_write_lock:
                        esp32_serial.write(b'p')
            except Exception as e:
                print(f"⚠️ 速度记录停止ESP32录制失败: {e}")
            finally:
                record_workflow_active.clear()

        stopped_at_ns = time.time_ns()
        stopped_at_iso = datetime.now().astimezone().isoformat(timespec="milliseconds")
        with speed_record_lock:
            if speed_record_state.get("session_id") == session_id:
                speed_record_state.update({
                    "active": False,
                    "stopped_at_ns": stopped_at_ns,
                    "stopped_at_iso": stopped_at_iso,
                    "sample_count": sample_count,
                    "stop_reason": stop_reason,
                    "stop_event": None,
                    "thread": None,
                    "esp32_recording": False,
                })
        print(
            f"✅ 速度记录结束: {file_path} ({sample_count} samples, reason={stop_reason})")


def speed_record_public_state():
    with speed_record_lock:
        started_at_ns = speed_record_state.get("started_at_ns")
        elapsed_sec = 0.0
        if speed_record_state.get("active") and started_at_ns:
            elapsed_sec = (time.time_ns() - started_at_ns) / 1_000_000_000.0
        elif started_at_ns and speed_record_state.get("stopped_at_ns"):
            elapsed_sec = (
                speed_record_state["stopped_at_ns"] - started_at_ns) / 1_000_000_000.0

        return {
            "active": speed_record_state["active"],
            "session_id": speed_record_state["session_id"],
            "file_path": speed_record_state["file_path"],
            "started_at_iso": speed_record_state["started_at_iso"],
            "stopped_at_iso": speed_record_state["stopped_at_iso"],
            "elapsed_sec": round(elapsed_sec, 3),
            "max_duration_sec": SPEED_RECORD_MAX_DURATION_SEC,
            "sample_count": speed_record_state["sample_count"],
            "stop_reason": speed_record_state["stop_reason"],
            "esp32_recording": speed_record_state.get("esp32_recording", False),
        }


def start_speed_recording():
    if esp32_serial and esp32_serial.is_open and record_workflow_active.is_set():
        return False, speed_record_public_state()

    esp32_recording_started = False
    if esp32_serial and esp32_serial.is_open:
        try:
            with serial_write_lock:
                esp32_serial.write(b's')
            record_workflow_active.set()
            esp32_recording_started = True
        except Exception as e:
            print(f"⚠️ 速度记录启动ESP32录制失败: {e}")
            record_workflow_active.clear()
            return False, speed_record_public_state()

    with speed_record_lock:
        if speed_record_state["active"]:
            already_active = True
        else:
            already_active = False

        if already_active:
            worker = None
        else:
            session_id = time.strftime("%Y%m%d_%H%M%S")
            file_name = f"speed_record_{session_id}.csv"
            file_path = os.path.join(SPEED_RECORD_DIR, file_name)
            started_at_ns = time.time_ns()
            started_at_iso = datetime.now().astimezone().isoformat(timespec="milliseconds")
            stop_event = threading.Event()

            worker = threading.Thread(
                target=speed_record_worker,
                args=(session_id, file_path, started_at_ns,
                      started_at_iso, stop_event, esp32_recording_started),
                daemon=True,
            )
            speed_record_state.update({
                "active": True,
                "session_id": session_id,
                "file_path": file_path,
                "started_at_ns": started_at_ns,
                "started_at_iso": started_at_iso,
                "stopped_at_ns": None,
                "stopped_at_iso": None,
                "sample_count": 0,
                "stop_reason": None,
                "stop_event": stop_event,
                "thread": worker,
                "esp32_recording": esp32_recording_started,
            })

    if already_active:
        if esp32_recording_started:
            try:
                with serial_write_lock:
                    esp32_serial.write(b'p')
            finally:
                record_workflow_active.clear()
        return False, speed_record_public_state()

    if worker:
        worker.start()

    print(f"🔴 速度记录开始: {file_path}")
    return True, speed_record_public_state()


def stop_speed_recording():
    with speed_record_lock:
        if not speed_record_state["active"]:
            was_active = False
            stop_event = None
            worker = None
        else:
            was_active = True
            stop_event = speed_record_state["stop_event"]
            worker = speed_record_state["thread"]

    if not was_active:
        return False, speed_record_public_state()

    if stop_event:
        stop_event.set()
    if worker:
        worker.join(timeout=2.0)

    return True, speed_record_public_state()


def wait_until_car_fully_stopped(session_id):
    stable_since = None
    last_log_time = 0.0

    while True:
        now = time.time()
        if is_car_stopped():
            if stable_since is None:
                stable_since = now
            elif now - stable_since >= SD_COPY_STOP_STABLE_SEC:
                return
        else:
            stable_since = None

        if now - last_log_time >= SD_COPY_WAIT_LOG_SEC:
            print(
                f"⏳ 批次 {session_id} 等待车辆完全停稳后复制SD数据 "
                f"(speed_pps={sensor_state.get('speed_pps', 0.0):.2f}, throttle={sensor_state.get('throttle', 1500)})"
            )
            last_log_time = now

        time.sleep(0.1)


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
                })
            except (KeyError, TypeError, ValueError):
                continue
    return rows


def write_enhanced_sensor_csv(session_id, raw_csv_path, source_file, record_start_ns, record_stop_ns):
    session_dir = os.path.join(SAVE_DIR, session_id)
    enhanced_csv = os.path.join(session_dir, "sensor_data_enhanced.csv")
    sync_meta_path = os.path.join(session_dir, "sync_meta.json")
    rows = read_raw_sensor_rows(raw_csv_path)

    if not rows:
        print(f"⚠️ 批次 {session_id} 的原始传感器CSV为空，无法生成增强版。")
        return False

    esp_first_ms = rows[0]["esp32_time_ms"]
    esp_last_ms = rows[-1]["esp32_time_ms"]
    duration_ms = max(esp_last_ms - esp_first_ms, 1)
    record_duration_ns = max(record_stop_ns - record_start_ns, 1)
    ns_per_esp_ms = record_duration_ns / duration_ms

    left_images = build_image_index(session_dir, "Left")
    right_images = build_image_index(session_dir, "Right")

    fieldnames = [
        "esp32_time_ms",
        "esp32_elapsed_ms",
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
    with open(enhanced_csv, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for row in rows:
            esp_elapsed_ms = row["esp32_time_ms"] - esp_first_ms
            python_time_ns_est = int(
                record_start_ns + esp_elapsed_ms * ns_per_esp_ms)

            rpm = 0.0
            if previous_row is not None:
                dt_ms = row["esp32_time_ms"] - previous_row["esp32_time_ms"]
                if dt_ms > 0:
                    delta_pos = row["position"] - previous_row["position"]
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
        "source_sd_file": source_file,
        "python_record_start_ns": record_start_ns,
        "python_record_stop_ns": record_stop_ns,
        "esp32_first_time_ms": esp_first_ms,
        "esp32_last_time_ms": esp_last_ms,
        "esp32_duration_ms": duration_ms,
        "mapping": "linear: first ESP32 row -> python_record_start_ns, last ESP32 row -> python_record_stop_ns",
        "ppr": PPR,
        "kmh_per_rpm": KMH_PER_RPM,
        "left_image_count": len(left_images),
        "right_image_count": len(right_images),
    }
    with open(sync_meta_path, "w", encoding="utf-8") as f:
        json.dump(sync_meta, f, ensure_ascii=False, indent=2)

    print(f"✅ 增强版传感器数据已生成: {enhanced_csv}")
    return True


def copy_latest_sd_log_to_session(session_id, record_start_ns, record_stop_ns):
    """Stop-time helper: pull the latest ESP32 SD CSV into this recording folder."""
    if esp32_serial is None or not esp32_serial.is_open:
        print("⚠️ SD数据复制跳过：ESP32串口未连接")
        return False

    session_dir = os.path.join(SAVE_DIR, session_id)
    os.makedirs(session_dir, exist_ok=True)
    target_csv = os.path.join(session_dir, "sensor_data.csv")
    source_note = os.path.join(session_dir, "sensor_data_source.txt")

    wait_until_car_fully_stopped(session_id)

    serial_transfer_active.set()
    time.sleep(0.15)

    source_file = "unknown"
    data_chunks = []
    data_started = False
    data_finished = False

    try:
        with serial_write_lock:
            esp32_serial.reset_input_buffer()
            esp32_serial.write(b'r')
            esp32_serial.flush()

            deadline = time.time() + SD_COPY_TIMEOUT_SEC
            while time.time() < deadline:
                raw_line = esp32_serial.readline()
                if not raw_line:
                    continue

                decoded_line = raw_line.decode(
                    'utf-8', errors='ignore').strip()
                source_match = re.search(r"/?data\d+\.csv", decoded_line)
                if source_match:
                    source_file = source_match.group(0)

                if decoded_line == "---DATA_START---":
                    data_started = True
                    data_chunks = []
                    continue

                if decoded_line == "---DATA_END---":
                    data_finished = True
                    break

                if data_started:
                    data_chunks.append(raw_line)

        if not data_started or not data_finished:
            print("⚠️ SD数据复制失败：没有收到完整 DATA_START/DATA_END 数据段")
            with open(source_note, "w", encoding="utf-8") as f:
                f.write("SD copy failed: incomplete serial transfer.\n")
            return False

        with open(target_csv, "wb") as f:
            f.writelines(data_chunks)
        with open(source_note, "w", encoding="utf-8") as f:
            f.write(f"Copied from ESP32 SD file: {source_file}\n")
            f.write(f"Copied at: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")

        write_enhanced_sensor_csv(
            session_id, target_csv, source_file, record_start_ns, record_stop_ns)
        print(f"✅ SD传感器数据已复制到: {target_csv} (来源: {source_file})")
        return True
    except Exception as e:
        print(f"⚠️ SD数据复制异常: {e}")
        try:
            with open(source_note, "w", encoding="utf-8") as f:
                f.write(f"SD copy failed: {e}\n")
        except Exception:
            pass
        return False
    finally:
        serial_transfer_active.clear()


def read_esp32_data():
    '''牺牲一定响应速度，换取高速下速度波动毛刺减少
    ESP32在激活录制的时候10ms一次传回，此时画出来的速度图会波动很大，因为脉冲只能取整，
    10ms一次的向上向下圆整差的一个脉冲，也就是1/12ppr 每10ms差这么多圈数，在换算成一分钟RPM的时候带来的误差很大
    所以需要滤波处理，采用的是100ms 差分滑动窗口，累计过去100ms的脉冲数再算速度
    舍去了大概50ms的响应速度，得到高速下rpm的平滑，波动小'''
    global sensor_state
    if esp32_serial is None:
        return

    pattern = re.compile(r"(\d+)\s*ms\s*\|\D*(-?\d+)")
    drive_pattern = re.compile(r"\[DRIVE\].*?\b(RC|WEB)\b.*?(\d{3,4})")
    last_debug_print = 0.0

    while True:
        try:
            if serial_transfer_active.is_set():
                time.sleep(0.02)
                continue

            # 防积压机制
            if esp32_serial.in_waiting > SERIAL_INPUT_FLUSH_THRESHOLD:
                sensor_state["serial_flush_count"] += 1
                print(f"⚠️ Serial RX backlog cleared: {esp32_serial.in_waiting} bytes")
                esp32_serial.reset_input_buffer()
                continue

            if esp32_serial.in_waiting > 0:
                line = esp32_serial.readline().decode('utf-8', errors='ignore').strip()
                now_wall = time.time()
                sensor_state["last_serial_line"] = line
                sensor_state["last_serial_rx_wall"] = now_wall
                sensor_state["serial_line_count"] += 1
                if SERIAL_DEBUG_PRINT and now_wall - last_debug_print >= SERIAL_DEBUG_MIN_INTERVAL_SEC:
                    print(f"ESP32 >> {line}")
                    last_debug_print = now_wall
                match = pattern.search(line)
                if match:
                    curr_time_ms = int(match.group(1))
                    curr_pos = int(match.group(2))

                    dt_sec = (curr_time_ms -
                              sensor_state["last_time_ms"]) / 1000.0

                    if dt_sec > 0 and sensor_state["last_time_ms"] != 0:
                        raw_speed = (
                            curr_pos - sensor_state["last_pos"]) / dt_sec

                        if dt_sec > 0.5:
                            # 1. 待机模式 (1000ms)：时间跨度大，完全没有量化误差，直接使用！
                            smoothed_speed = raw_speed
                            history_buffer.clear()  # 刚从录制切回待机，清空旧轨迹
                        else:
                            # 2. 录制模式 (10ms)：启动 M/T 差分窗口算法
                            history_buffer.append((curr_time_ms, curr_pos))

                            # 必须等攒够 10 个点 (约 100ms) 才开始差分计算
                            if len(history_buffer) == history_buffer.maxlen:
                                # 拿出 100ms 前的数据
                                old_time_ms, old_pos = history_buffer[0]
                                window_dt = (curr_time_ms -
                                             old_time_ms) / 1000.0

                                # 计算这 100ms 跨度内的平滑速度
                                window_speed = (curr_pos - old_pos) / window_dt

                                # 在此基础上，再加一层极弱的 EMA 滤波，让 4000 RPM 的波形呈现完美的流线型
                                alpha = 0.3
                                smoothed_speed = (
                                    alpha * window_speed) + ((1 - alpha) * sensor_state["speed_pps"])
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
                        sensor_state["last_drive_line"] = line
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
            cv2.putText(frame, "REC", (50, 38),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
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


@app.route('/sensor_stats')
def sensor_stats():
    return jsonify({
        "position": sensor_state["position"],
        "speed": f"{sensor_state['speed_pps']:.1f}",
        "mode": sensor_state["mode"],         # 🚀 新增
        "throttle": sensor_state["throttle"]  # 🚀 新增
    })


@app.route('/speed_record/start', methods=['POST'])
def speed_record_start():
    started, state = start_speed_recording()
    status = "started" if started else "already_running"
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


@app.route('/start_record')
def start_record():
    if esp32_serial and record_workflow_active.is_set():
        return jsonify({"status": "busy", "message": "上一批传感器数据还在等待停稳或复制SD数据"}), 409

    session_id = time.strftime("%Y%m%d_%H%M%S")
    # 左相机必定录制
    cam_left.start_record(session_id=session_id, duration_sec=3)

    # 只有当右相机激活时才录制右侧
    if cam_right.is_active:
        cam_right.start_record(session_id=session_id, duration_sec=3)

    print(f"📢 开始录制批次: {session_id} (单目/双目模式已自动识别)")

    if esp32_serial:
        record_workflow_active.set()
        with serial_write_lock:
            sensor_record_start_ns = time.time_ns()
            esp32_serial.write(b's')

        def stop_esp_recording():
            try:
                time.sleep(3.0)
                with serial_write_lock:
                    sensor_record_stop_ns = time.time_ns()
                    esp32_serial.write(b'p')
                time.sleep(0.2)
                copy_latest_sd_log_to_session(
                    session_id, sensor_record_start_ns, sensor_record_stop_ns)
            finally:
                record_workflow_active.clear()
        threading.Thread(target=stop_esp_recording, daemon=True).start()

    return jsonify({"status": "started"})

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
            if serial_transfer_active.is_set():
                print(f"🛡️ SD数据复制期间丢弃普通油门指令: {val} us")
                return jsonify({"status": "ignored", "reason": "sd_copy_active", "throttle": sensor_state["throttle"]}), 409
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
    if serial_transfer_active.is_set():
        return jsonify({"status": "busy", "message": "SD数据正在复制，串口暂时被占用", "throttle": 1500}), 409
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
            esp32_serial.reset_input_buffer()

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
