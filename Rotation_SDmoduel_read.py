import serial
import time
import pandas as pd
import matplotlib.pyplot as plt
import io
import sys

# ================= 配置区 =================
SERIAL_PORT = 'COM12'  
BAUD_RATE = 115200
PPR = 12 # 你的编码器一圈产生的脉冲数，用于计算 RPM
# ==========================================

print(f"尝试连接串口 {SERIAL_PORT}...")

try:
    # 1. 打开串口连接
    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=2)
    time.sleep(2) 
    print("串口连接成功！")

    # 2. 发送读取指令
    print("正在向 ESP32 发送读取指令 'r'...")
    ser.write(b'r')

    # 3. 接收并解析数据
    print("正在接收数据，请稍候...")
    raw_data = []
    is_receiving = False

    while True:
        if ser.in_waiting > 0:
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            
            if line == "---DATA_START---":
                is_receiving = True
                print("成功检测到数据流起点，开始抓取 CSV 数据...")
                continue
            
            if line == "---DATA_END---":
                print("数据接收完毕！")
                break
            
            if is_receiving and "," in line:
                raw_data.append(line)

    # 4. 关闭串口
    ser.close()
    print("串口已安全释放。")

    # 5. 数据处理与可视化
    if len(raw_data) > 1: 
        print("\n开始处理数据并生成图表...")
        
        csv_string = "\n".join(raw_data)
        df = pd.read_csv(io.StringIO(csv_string))
        
        # 清除可能因为断电等原因产生的损坏行，将所有数据转为数字
        df = df.apply(pd.to_numeric, errors='coerce').dropna()

        # 检查是否包含必要的列
        if 'Time_ms' not in df.columns or 'Position' not in df.columns:
             print("错误：CSV 数据缺失 'Time_ms' 或 'Position' 表头！")
             sys.exit()

        # 将毫秒转换为秒
        df['Time_s'] = df['Time_ms'] / 1000.0
        
        # ================== 核心：计算速度 (RPM) ==================
        # 计算每一行与上一行的时间差 (秒) 和 位置差 (脉冲数)
        df['delta_time'] = df['Time_s'].diff()
        df['delta_pos'] = df['Position'].diff()
        
        # 计算速度：(脉冲数 / 一圈的总脉冲数) / 时间差(秒) * 60 = 转/分钟 (RPM)
        # 用 abs() 取绝对值，这样正转反转都会显示为正向的速度大小
        df['RPM'] = abs((df['delta_pos'] / PPR) / df['delta_time'] * 60.0)
        
        # 第一行的 delta 算出来是 NaN（空值），需要填充为 0
        df['RPM'] = df['RPM'].fillna(0)
        # ========================================================

        x_data = df['Time_s']
        y_pos = df['Position']
        y_rpm = df['RPM']

        # 6. 开始画图 (2行1列的子图)
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8), sharex=True)
        fig.canvas.manager.set_window_title('ESP32 Encoder Data Analysis')
        
        # --- 图表 1：位置 (Position) ---
        ax1.plot(x_data, y_pos, color='#1f77b4', linewidth=2, label='Total Pulses')
        ax1.set_title('Drive Shaft Position & Speed Analysis', fontsize=14, fontweight='bold')
        ax1.set_ylabel('Position (Pulses)', fontsize=12)
        ax1.grid(True, linestyle='--', alpha=0.7)
        ax1.legend()
        
        # --- 图表 2：速度 (RPM) ---
        # 考虑到串口传输可能有微小的延迟导致时间差抖动，速度曲线往往会有毛刺
        # 我们用橙色线画出速度，可以明显看清加速、匀速和减速过程
        ax2.plot(x_data, y_rpm, color='#ff7f0e', linewidth=2, label='Motor Speed (RPM)')
        ax2.set_xlabel('Time (Seconds)', fontsize=12)
        ax2.set_ylabel('Speed (RPM)', fontsize=12)
        ax2.grid(True, linestyle='--', alpha=0.7)
        ax2.legend()
        
        # 紧凑布局并显示
        plt.tight_layout()
        print("图表生成完毕！")
        plt.show()
        
    else:
        print("警告：没有接收到足够的数据用于绘图。")

except serial.SerialException as e:
    print(f"\n[串口错误]: {e}")
except Exception as e:
    print(f"\n[发生未知错误]: {e}")