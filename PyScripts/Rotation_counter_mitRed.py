import serial
import matplotlib.pyplot as plt
import matplotlib.animation as animation
import time
import sys

# ==========================================
# 1. 核心配置
# ==========================================
PORT = 'COM12'       # 你的 ESP32 端口号
BAUD_RATE = 115200
PPR = 12             # 电机编码器一圈的脉冲数

try:
    ser = serial.Serial(PORT, BAUD_RATE, timeout=0.1)
    print(f"✅ 成功连接到 {PORT}")
except Exception as e:
    print(f"❌ 串口打开失败: {e}")
    sys.exit()

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8))
fig.canvas.manager.set_window_title('ESP32 Motor & Wheel Dashboard')

max_points = 100  
x_data, pos_data, rpm_data = [], [], []
frame_count = 0

time_buffer = []
pos_buffer = []
CALC_WINDOW = 5 

# --- 测试相关的状态变量 ---
is_recording = False
record_start_time = 0
record_start_pulse = 0
record_start_wheel = 0     
current_pulse_global = 0 
current_wheel_global = 0   
last_test_result = ""    

def on_key_press(event):
    global is_recording, record_start_time, record_start_pulse, record_start_wheel, last_test_result
    # 改为按下 'w' 开始
    if event.key == 'w' and not is_recording:
        is_recording = True
        record_start_time = time.time()
        record_start_pulse = current_pulse_global
        record_start_wheel = current_wheel_global
        last_test_result = "" 
        print("\n" + "🚀"*10)
        print("⏱️  开始 20 秒采样测速...") # 修改点：提示文字改为 20 秒
        print("🚀"*10 + "\n")

fig.canvas.mpl_connect('key_press_event', on_key_press)

def animate(i):
    global frame_count, current_pulse_global, current_wheel_global, is_recording, last_test_result
    
    if ser.in_waiting > 0:
        try:
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            if not line: return

            if "Data:" in line:
                # 稳健的分割逻辑
                payload = line.split("Data:")[1]
                values = payload.split(",")
                
                if len(values) >= 2:
                    pulse_count = int(values[0])
                    # 自动修正红外计数：除以 2 得到真实圈数
                    wheel_count = int(values[1]) // 2 
                    
                    current_pulse_global = pulse_count 
                    current_wheel_global = wheel_count
                    current_time = time.time()
                    
                    # --- 20 秒记录逻辑 ---
                    if is_recording:
                        elapsed_time = current_time - record_start_time
                        if elapsed_time >= 20.0: # 修改点：判定时间改为 20.0 秒
                            is_recording = False
                            delta_pulse = pulse_count - record_start_pulse
                            delta_wheel = wheel_count - record_start_wheel
                            
                            motor_turns = abs(delta_pulse / PPR)
                            # 计算传动比 (Gear Ratio)
                            ratio = motor_turns / delta_wheel if delta_wheel != 0 else 0
                            
                            last_test_result = (f"Result (20s): Motor {motor_turns:.2f} r, "
                                                f"Wheel {delta_wheel} r\n"
                                                f"Ratio = {ratio:.2f} : 1")
                            
                            print(f"\n🛑 20秒测试完成！推算减速比: {ratio:.2f} : 1\n")

                    # --- RPM 计算与绘图更新 ---
                    frame_count += 1
                    time_buffer.append(current_time)
                    pos_buffer.append(pulse_count)
                    
                    current_rpm = 0
                    if len(time_buffer) >= CALC_WINDOW:
                        dt = current_time - time_buffer[0]
                        dp = pulse_count - pos_buffer[0]
                        if dt > 0:
                            current_rpm = (dp / PPR) * (60.0 / dt)
                        time_buffer.pop(0)
                        pos_buffer.pop(0)
                    
                    x_data.append(frame_count)
                    pos_data.append(pulse_count)
                    rpm_data.append(current_rpm) 
                    
                    x_plot = x_data[-max_points:]
                    y_pos_plot = pos_data[-max_points:]
                    y_rpm_plot = rpm_data[-max_points:]
                    
                    ax1.clear()
                    ax1.plot(x_plot, y_pos_plot, color='#1f77b4', linewidth=2)
                    title_text = f'Encoder Pulses (Physical Wheel Turns: {current_wheel_global})'
                    if is_recording:
                        # 额外增加倒计时显示，方便观察
                        time_left = 20.0 - (current_time - record_start_time)
                        title_text += f'  [RECORDING: {time_left:.1f}s]'
                    ax1.set_title(title_text)
                    ax1.set_ylabel('Pulses')
                    ax1.grid(True, linestyle='--', alpha=0.6)
                    
                    if last_test_result and not is_recording:
                        ax1.text(0.5, 0.7, last_test_result, transform=ax1.transAxes, 
                                ha='center', va='center', color='red', fontsize=12, 
                                bbox=dict(facecolor='white', alpha=0.8, edgecolor='red'))

                    ax2.clear()
                    ax2.plot(x_plot, y_rpm_plot, color='#ff7f0e', linewidth=2)
                    ax2.set_title('Motor RPM')
                    ax2.set_xlabel('Samples')
                    ax2.set_ylabel('RPM')
                    ax2.grid(True, linestyle='--', alpha=0.6)
                    
        except Exception as e:
            print(f"解析中... {e}")

ani = animation.FuncAnimation(fig, animate, interval=50, cache_frame_data=False)
plt.tight_layout()
plt.show() 

if ser.is_open:
    ser.close()