import serial
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from collections import deque

# ==========================================
# 核心配置区
# ==========================================
SERIAL_PORT = 'COM10'  # 替换成你的 ESP32 端口
BAUD_RATE = 115200
MAX_POINTS = 300      # 图表上最多显示的过去 300 个数据点（也就是过去大约几秒的“滑动窗口”）

# 初始化串口
try:
    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.1)
    print(f"✅ 成功连接到 {SERIAL_PORT}")
except Exception as e:
    print(f"❌ 串口连接失败，请检查端口号或是否被 Arduino 占用！报错：{e}")
    exit()

# 使用 deque (双端队列) 存储数据，超过 MAX_POINTS 会自动挤掉老数据，实现画面向左滚动
t_data = deque(maxlen=MAX_POINTS)
accel_data = deque(maxlen=MAX_POINTS)
vel_data = deque(maxlen=MAX_POINTS)

# ==========================================
# 设置 Matplotlib 图表 (上下两个子图)
# ==========================================
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6))
fig.canvas.manager.set_window_title('小车动力学实时监控面板')

# 设置第一张图：加速度
line_accel, = ax1.plot([], [], lw=2, color='red', label='Horizontal Accel (m/s²)')
ax1.set_ylabel('Acceleration (m/s²)')
ax1.legend(loc='upper left')
ax1.grid(True, linestyle='--', alpha=0.6)

# 设置第二张图：速度
line_vel, = ax2.plot([], [], lw=2, color='blue', label='Horizontal Velocity (m/s)')
ax2.set_xlabel('Time (Seconds)')
ax2.set_ylabel('Velocity (m/s)')
ax2.legend(loc='upper left')
ax2.grid(True, linestyle='--', alpha=0.6)

fig.tight_layout()

# ==========================================
# 实时更新数据的回调函数
# ==========================================
def update(frame):
    # 读取串口所有排队的数据
    while ser.in_waiting > 0:
        try:
            # 读取一行，解码，去除换行符
            line = ser.readline().decode('utf-8').strip()
            if not line:
                continue
            
            # 用逗号切分数据
            parts = line.split(',')
            if len(parts) == 3:
                t = float(parts[0])
                a = float(parts[1])
                v = float(parts[2])
                
                # 追加到队列中
                t_data.append(t)
                accel_data.append(a)
                vel_data.append(v)
        except ValueError:
            # 防止刚好读到一半的数据导致 float() 转换报错
            pass

    # 如果有数据，更新图表
    if len(t_data) > 0:
        # 提取当前的 X 轴范围（最新的时间 往前推一段）
        current_time = t_data[-1]
        start_time = t_data[0]
        
        # 更新线条数据
        line_accel.set_data(t_data, accel_data)
        line_vel.set_data(t_data, vel_data)
        
        # 动态调整 X 轴，实现“走纸记录仪”的滚动效果
        ax1.set_xlim(start_time, current_time + 0.5)
        ax2.set_xlim(start_time, current_time + 0.5)
        
        # 动态调整 Y 轴，保证波峰不被遮挡
        if max(accel_data) > 0.1:
            ax1.set_ylim(-0.5, max(accel_data) * 1.5)
        if max(vel_data) > 0.1:
            ax2.set_ylim(-0.5, max(vel_data) * 1.5)

    return line_accel, line_vel

# ==========================================
# 启动动画循环
# ==========================================
# interval=20 表示每 20 毫秒刷新一次图表画面 (50fps)
ani = animation.FuncAnimation(fig, update, interval=20, blit=False, cache_frame_data=False)

print("🚀 正在绘制实时图表，关闭绘图窗口即可退出程序...")
plt.show()

# 退出时关闭串口
ser.close()