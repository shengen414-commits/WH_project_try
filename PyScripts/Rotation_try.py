import serial
import matplotlib.pyplot as plt
import matplotlib.animation as animation
import time
import sys

# ==========================================
# 1. 核心配置 (请根据实际情况修改)
# ==========================================
PORT = 'COM13'       # 你的 ESP32 端口号
BAUD_RATE = 115200

# 【重要】编码器线数 (PPR)
# 你的磁环写着 STM6-P12，通常转一圈是 12 或 13 个脉冲。
# 如果你发现算出来的转速不对，可以微调这个值。
PPR = 12  

# ==========================================
# 2. 串口初始化
# ==========================================
try:
    ser = serial.Serial(PORT, BAUD_RATE, timeout=0.1)
    print(f"✅ 成功连接到 {PORT}")
except Exception as e:
    print(f"❌ 串口打开失败: {e}")
    sys.exit()

# ==========================================
# 3. 图表与数据初始化 (创建上下两个子图)
# ==========================================
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8))
fig.canvas.manager.set_window_title('ESP32 Encoder Dashboard')

max_points = 100  # 屏幕最多显示的数据点
x_data, pos_data, rpm_data = [], [], []
frame_count = 0

# 用于计算 RPM 的滑动窗口缓存
time_buffer = []
pos_buffer = []
CALC_WINDOW = 5 # 使用过去 5 帧的数据计算转速 (起到平滑滤波的作用)

# ==========================================
# 4. 动画更新函数
# ==========================================
def animate(i):
    global frame_count
    
    if ser.in_waiting > 0:
        try:
            line = ser.readline().decode('utf-8').strip()
            
            # 严格匹配前缀，防止被开机乱码干扰
            if line.startswith("Pos:"):
                pulse_count = int(line.split(":")[1])
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
                        # 核心公式：(脉冲差 / 1圈总脉冲) * (60 / 时间差)
                        current_rpm = (dp / PPR) * (60.0 / dt)
                    
                    # 保持缓冲区大小，踢出最老的数据
                    time_buffer.pop(0)
                    pos_buffer.pop(0)
                
                # --- C. 更新绘图列表 ---
                x_data.append(frame_count)
                pos_data.append(pulse_count)
                rpm_data.append(current_rpm) # 可以用 abs(current_rpm) 强制只显示正转速
                
                x_plot = x_data[-max_points:]
                y_pos_plot = pos_data[-max_points:]
                y_rpm_plot = rpm_data[-max_points:]
                
                # --- D. 绘制上面：位置图 ---
                ax1.clear()
                ax1.plot(x_plot, y_pos_plot, color='#1f77b4', linewidth=2)
                ax1.set_title('Position (Total Pulses)', fontsize=12)
                ax1.set_ylabel('Pulses')
                ax1.grid(True, linestyle='--', alpha=0.6)
                
                # --- E. 绘制下面：转速图 ---
                ax2.clear()
                ax2.plot(x_plot, y_rpm_plot, color='#ff7f0e', linewidth=2)
                ax2.set_title('Speed (RPM)', fontsize=12)
                ax2.set_xlabel('Time (Frames)')
                ax2.set_ylabel('RPM')
                # 动态设置Y轴范围，防止静止时Y轴比例崩塌
                if max(abs(min(y_rpm_plot)), max(y_rpm_plot)) < 10:
                    ax2.set_ylim(-15, 15) 
                ax2.grid(True, linestyle='--', alpha=0.6)
                
                # 防止两个子图的文字重叠
                plt.tight_layout()
                
        except Exception as e:
            pass

# ==========================================
# 5. 启动程序
# ==========================================
print("🚀 正在接收数据，请转动电机...")
ani = animation.FuncAnimation(fig, animate, interval=50, cache_frame_data=False)

plt.show() 
ser.close()
print("🛑 串口已关闭。")