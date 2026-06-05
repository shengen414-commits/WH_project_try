import serial
import re
import time
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from collections import deque


# --- 加上这两行 ---
plt.rcParams['font.sans-serif'] = ['SimHei']  # 用来正常显示中文标签
plt.rcParams['axes.unicode_minus'] = False   # 用来正常显示负号
# ----------------

# ================= 配置区 =================
SERIAL_PORT = 'COM14'  # 根据你的实际端口修改
BAUD_RATE = 115200
PPR = 12.0             # 每圈脉冲数 (统一为 12 PPR)
HISTORY_SIZE = 100     # 图表显示的轨迹长度
# ==========================================

# 初始化串口
try:
    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.1)
except Exception as e:
    print(f"无法打开串口: {e}")
    exit()

# 数据缓存
times = deque(maxlen=HISTORY_SIZE)
rpms = deque(maxlen=HISTORY_SIZE)
counts = deque(maxlen=HISTORY_SIZE)

last_count = 0
last_time = time.time()
total_revs = 0

# 设置绘图窗口
plt.style.use('dark_background')  # 使用深色模式，看起来更像仪表盘
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8))
fig.canvas.manager.set_window_title('ESP32 电机编码器实时监控')

# 转速图表设置
line_rpm, = ax1.plot([], [], color='#00ff00', linewidth=2, label='实时转速 (RPM)')
ax1.set_title("Motor Speed Real-time (RPM)")
ax1.set_ylabel("RPM")
ax1.grid(True, alpha=0.3)
ax1.legend(loc='upper left')

# 圈数图表设置
line_rev, = ax2.plot([], [], color='#00bcff', linewidth=2, label='累计圈数 (Revs)')
ax2.set_title("Total Revolutions")
ax2.set_xlabel("Data Points")
ax2.set_ylabel("Turns")
ax2.grid(True, alpha=0.3)
ax2.legend(loc='upper left')


def update(frame):
    global last_count, last_time, total_revs

    while ser.in_waiting > 0:
        try:
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            if line:
                print(f"DEBUG -> 原始数据内容: '{line}'")

            match = re.search(r"当前脉冲计数:\s*(-?\d+)", line)

            if match:
                current_count = int(match.group(1))
                current_time = time.time()

                # 计算逻辑
                dt = current_time - last_time
                dp = current_count - last_count

                if dt > 0:
                    rpm = (dp / PPR) / dt * 60
                    total_revs = current_count / PPR

                    # 更新缓存
                    rpms.append(rpm)
                    counts.append(total_revs)
                    times.append(len(rpms))  # 简单用数据点序号作为X轴

                    last_count = current_count
                    last_time = current_time

                    # 打印到控制台备查
                    print(f"RPM: {rpm:>8.2f} | Total: {total_revs:>8.2f}")
        except:
            pass

    # 更新转速曲线
    if rpms:
        line_rpm.set_data(range(len(rpms)), list(rpms))
        ax1.set_xlim(0, HISTORY_SIZE)
        ax1.set_ylim(min(rpms)-10, max(rpms)+10)  # 动态缩放纵坐标

        # 更新圈数曲线
        line_rev.set_data(range(len(counts)), list(counts))
        ax2.set_xlim(0, HISTORY_SIZE)
        ax2.relim()          # 重新计算数据范围
        ax2.autoscale_view() # 自动缩放视图

    return line_rpm, line_rev


# 使用 FuncAnimation 刷新图表
ani = FuncAnimation(fig, update, interval=50, blit=False)

plt.tight_layout()
plt.show()

# 退出时关闭串口
ser.close()
