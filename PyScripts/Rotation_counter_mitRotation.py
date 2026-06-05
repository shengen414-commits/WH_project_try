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

# 请务必确认这两个数值！
MOTOR_PPR = 12       # 传动轴（电机）编码器一圈的脉冲数
WHEEL_PPR = 12       # 车轮编码器一圈的脉冲数 (统一为 12 PPR)

try:
    ser = serial.Serial(PORT, BAUD_RATE, timeout=0.1)
    print(f"✅ 成功连接到 {PORT}")
except Exception as e:
    print(f"❌ 串口打开失败: {e}")
    sys.exit()

# 创建 2x2 的图表布局
fig, axs = plt.subplots(2, 2, figsize=(12, 8))
fig.canvas.manager.set_window_title('ESP32 Dual Encoder Dashboard')

ax_motor_turns = axs[0, 0]
ax_wheel_turns = axs[0, 1]
ax_motor_rpm = axs[1, 0]
ax_wheel_rpm = axs[1, 1]

max_points = 100  
x_data = []
motor_turns_data, wheel_turns_data = [], []
motor_rpm_data, wheel_rpm_data = [], []
frame_count = 0

time_buffer = []
motor_pos_buffer = []
wheel_pos_buffer = []
CALC_WINDOW = 5 

# --- 测试相关的状态变量 ---
is_recording = False
record_start_time = 0
record_start_motor_pulse = 0
record_start_wheel_pulse = 0     
current_motor_pulse = 0 
current_wheel_pulse = 0   
last_test_result = ""    

def on_key_press(event):
    global is_recording, record_start_time, record_start_motor_pulse, record_start_wheel_pulse, last_test_result
    if event.key == 'w' and not is_recording:
        is_recording = True
        record_start_time = time.time()
        record_start_motor_pulse = current_motor_pulse
        record_start_wheel_pulse = current_wheel_pulse
        last_test_result = "" 
        print("\n" + "🚀"*10)
        print("⏱️  开始 20 秒采样测速...") 
        print("🚀"*10 + "\n")

fig.canvas.mpl_connect('key_press_event', on_key_press)

def animate(i):
    global frame_count, current_motor_pulse, current_wheel_pulse, is_recording, last_test_result
    
    if ser.in_waiting > 0:
        try:
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            if not line: return

            if "Data:" in line:
                payload = line.split("Data:")[1]
                values = payload.split(",")
                
                if len(values) >= 2:
                    m_pulse = int(values[0])
                    w_pulse = int(values[1]) 
                    
                    current_motor_pulse = m_pulse 
                    current_wheel_pulse = w_pulse
                    current_time = time.time()
                    
                    # 当前的物理圈数 (绝对值)
                    m_turns = abs(m_pulse / MOTOR_PPR)
                    w_turns = abs(w_pulse / WHEEL_PPR)

                    # --- 20 秒记录逻辑 ---
                    if is_recording:
                        elapsed_time = current_time - record_start_time
                        if elapsed_time >= 20.0: 
                            is_recording = False
                            delta_m_pulse = m_pulse - record_start_motor_pulse
                            delta_w_pulse = w_pulse - record_start_wheel_pulse
                            
                            motor_total_turns = abs(delta_m_pulse / MOTOR_PPR)
                            wheel_total_turns = abs(delta_w_pulse / WHEEL_PPR)
                            
                            ratio = motor_total_turns / wheel_total_turns if wheel_total_turns != 0 else 0
                            
                            last_test_result = (f"20s Test Result:\n"
                                                f"Motor: {motor_total_turns:.2f} r\n"
                                                f"Wheel: {wheel_total_turns:.2f} r\n"
                                                f"Ratio = {ratio:.2f} : 1")
                            
                            print(f"🛑 20秒测试完成！")
                            print(f"电机转了: {motor_total_turns:.2f} 圈, 车轮转了: {wheel_total_turns:.2f} 圈")
                            print(f"推算减速比: {ratio:.2f} : 1\n")

                    # --- RPM 计算缓冲 ---
                    frame_count += 1
                    time_buffer.append(current_time)
                    motor_pos_buffer.append(m_pulse)
                    wheel_pos_buffer.append(w_pulse)
                    
                    current_motor_rpm = 0
                    current_wheel_rpm = 0
                    
                    if len(time_buffer) >= CALC_WINDOW:
                        dt = current_time - time_buffer[0]
                        dm = m_pulse - motor_pos_buffer[0]
                        dw = w_pulse - wheel_pos_buffer[0]
                        
                        if dt > 0:
                            current_motor_rpm = abs((dm / MOTOR_PPR) * (60.0 / dt))
                            current_wheel_rpm = abs((dw / WHEEL_PPR) * (60.0 / dt))
                            
                        time_buffer.pop(0)
                        motor_pos_buffer.pop(0)
                        wheel_pos_buffer.pop(0)
                    
                    # --- 更新绘图数据 ---
                    x_data.append(frame_count)
                    motor_turns_data.append(m_turns)
                    wheel_turns_data.append(w_turns)
                    motor_rpm_data.append(current_motor_rpm) 
                    wheel_rpm_data.append(current_wheel_rpm)
                    
                    x_plot = x_data[-max_points:]
                    
                    # 1. 绘制电机圈数
                    ax_motor_turns.clear()
                    ax_motor_turns.plot(x_plot, motor_turns_data[-max_points:], color='#1f77b4', linewidth=2)
                    title_m_turns = f'Motor Turns: {m_turns:.1f} r'
                    if is_recording:
                        time_left = 20.0 - (current_time - record_start_time)
                        title_m_turns += f'  [REC: {time_left:.1f}s]'
                    ax_motor_turns.set_title(title_m_turns)
                    ax_motor_turns.set_ylabel('Turns (r)')
                    ax_motor_turns.grid(True, linestyle='--', alpha=0.6)
                    
                    # 显示20秒测试结果浮窗
                    if last_test_result and not is_recording:
                        ax_motor_turns.text(0.5, 0.7, last_test_result, transform=ax_motor_turns.transAxes, 
                                ha='center', va='center', color='red', fontsize=10, 
                                bbox=dict(facecolor='white', alpha=0.9, edgecolor='red'))

                    # 2. 绘制车轮圈数
                    ax_wheel_turns.clear()
                    ax_wheel_turns.plot(x_plot, wheel_turns_data[-max_points:], color='#2ca02c', linewidth=2)
                    ax_wheel_turns.set_title(f'Wheel Turns: {w_turns:.1f} r')
                    ax_wheel_turns.grid(True, linestyle='--', alpha=0.6)

                    # 3. 绘制电机转速 RPM
                    ax_motor_rpm.clear()
                    ax_motor_rpm.plot(x_plot, motor_rpm_data[-max_points:], color='#ff7f0e', linewidth=2)
                    ax_motor_rpm.set_title(f'Motor Speed: {current_motor_rpm:.0f} RPM')
                    ax_motor_rpm.set_ylabel('RPM')
                    ax_motor_rpm.set_xlabel('Samples')
                    ax_motor_rpm.grid(True, linestyle='--', alpha=0.6)
                    
                    # 4. 绘制车轮转速 RPM
                    ax_wheel_rpm.clear()
                    ax_wheel_rpm.plot(x_plot, wheel_rpm_data[-max_points:], color='#d62728', linewidth=2)
                    ax_wheel_rpm.set_title(f'Wheel Speed: {current_wheel_rpm:.0f} RPM')
                    ax_wheel_rpm.set_xlabel('Samples')
                    ax_wheel_rpm.grid(True, linestyle='--', alpha=0.6)
                    
        except Exception as e:
            print(f"解析中... {e}")

ani = animation.FuncAnimation(fig, animate, interval=50, cache_frame_data=False)
plt.tight_layout()
plt.show() 

if ser.is_open:
    ser.close()
