import serial
import matplotlib.pyplot as plt
import matplotlib.animation as animation
import time
import sys

# ==========================================
# 1. 核心配置
# ==========================================
PORT = 'COM12'        # 你的 ESP32 端口号
BAUD_RATE = 115200
PPR = 12             # 编码器线数 (Pulse Per Revolution)
RECORD_DURATION = 30.0 # 记录时长 (秒)

# ==========================================
# 2. 状态变量
# ==========================================
recording = False
start_time = 0
start_pulses = 0
current_total_pulses = 0  # 实时存储当前脉冲数
result_text = ""          # 用于在图表上显示结果

# ==========================================
# 3. 串口初始化
# ==========================================
try:
    ser = serial.Serial(PORT, BAUD_RATE, timeout=0.1)
    print(f"✅ 成功连接到 {PORT}")
    print("💡 操作提示: 在图表窗口按下键盘 'w' 键开始 30s 采样记录")
except Exception as e:
    print(f"❌ 串口打开失败: {e}")
    sys.exit()

# ==========================================
# 4. 图表初始化
# ==========================================
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8))
fig.canvas.manager.set_window_title('ESP32 Encoder Dashboard')

max_points = 100
x_data, pos_data, rpm_data = [], [], []
frame_count = 0
time_buffer, pos_buffer = [], []
CALC_WINDOW = 5

# ==========================================
# 5. 键盘事件处理
# ==========================================
def on_key(event):
    global recording, start_time, start_pulses, result_text
    if event.key == 'w':
        if not recording:
            recording = True
            start_time = time.time()
            start_pulses = current_total_pulses
            result_text = "🔴 正在记录 (30s)..."
            print("\n开始记录 30 秒内转过的圈数...")
        else:
            print("正在记录中，请稍后...")

# 绑定事件到窗口
fig.canvas.mpl_connect('key_press_event', on_key)

# ==========================================
# 6. 动画更新函数
# ==========================================
def animate(i):
    global frame_count, current_total_pulses, recording, result_text
    
    if ser.in_waiting > 0:
        try:
            line = ser.readline().decode('utf-8').strip()
            
            if line.startswith("Pos:"):
                pulse_count = int(line.split(":")[1])
                current_total_pulses = pulse_count # 更新当前脉冲
                current_time = time.time()
                
                # --- A. 记录历史数据 ---
                frame_count += 1
                time_buffer.append(current_time)
                pos_buffer.append(pulse_count)
                
                # --- B. 计算 RPM ---
                current_rpm = 0
                if len(time_buffer) >= CALC_WINDOW:
                    dt = current_time - time_buffer[-CALC_WINDOW]
                    dp = pulse_count - pos_buffer[-CALC_WINDOW]
                    if dt > 0:
                        current_rpm = (dp / PPR) * (60.0 / dt)
                    time_buffer.pop(0)
                    pos_buffer.pop(0)
                
                # --- C. 5秒计时逻辑 ---
                if recording:
                    elapsed = current_time - start_time
                    if elapsed >= RECORD_DURATION:
                        recording = False
                        diff_pulses = current_total_pulses - start_pulses
                        rotations = diff_pulses / PPR
                        result_text = f"✅ 记录结束! 300s内转动: {rotations:.2f} 圈"
                        print(f"\n--- 统计结果 ---")
                        print(f"脉冲增量: {diff_pulses}")
                        print(f"总圈数: {rotations:.2f} 圈")
                        print(f"平均转速: {(rotations/RECORD_DURATION)*60:.1f} RPM")
                    else:
                        result_text = f"🔴 正在记录: {elapsed:.1f}s / {RECORD_DURATION}s"

                # --- D. 绘图更新 ---
                x_data.append(frame_count)
                pos_data.append(pulse_count)
                rpm_data.append(current_rpm)
                
                x_plot = x_data[-max_points:]
                y_pos_plot = pos_data[-max_points:]
                y_rpm_plot = rpm_data[-max_points:]
                
                ax1.clear()
                ax1.plot(x_plot, y_pos_plot, color='#1f77b4', linewidth=2)
                ax1.set_title(f'Position | {result_text}', fontsize=12, color='red' if recording else 'black')
                ax1.set_ylabel('Pulses')
                ax1.grid(True, linestyle='--', alpha=0.6)
                
                ax2.clear()
                ax2.plot(x_plot, y_rpm_plot, color='#ff7f0e', linewidth=2)
                ax2.set_title('Speed (RPM)', fontsize=12)
                ax2.set_xlabel('Time (Frames)')
                ax2.set_ylabel('RPM')
                if max(abs(min(y_rpm_plot)), max(y_rpm_plot)) < 10:
                    ax2.set_ylim(-15, 15)
                ax2.grid(True, linestyle='--', alpha=0.6)
                
                plt.tight_layout()
                
        except Exception:
            pass

# ==========================================
# 7. 启动程序
# ==========================================
ani = animation.FuncAnimation(fig, animate, interval=50, cache_frame_data=False)

plt.show() 
ser.close()
print("🛑 串口已关闭。")