import serial
import time
import matplotlib.pyplot as plt
import csv
import os

# ==========================================
# 1. 配置区
# ==========================================
SERIAL_PORT = 'COM8'  # 你的 ESP32 端口
BAUD_RATE = 115200
RECORD_DURATION = 3.0  # 录制时长

def run_experiment():
    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.1)
        print(f"✅ 成功连接 {SERIAL_PORT}")
    except Exception as e:
        print(f"❌ 串口无法打开：{e}")
        return

    print("\n--- 动力学数据采集系统 ---")
    print("操作指令：")
    print("  按下 [s]键: 进入 3-2-1 倒数并开始录制")
    print("  按下 [q]键: 退出程序")

    while True:
        cmd = input("\n等待指令 >> ").lower()
        
        if cmd == 's':
            # --- 倒数计时逻辑 ---
            print("\n准备好了吗？")
            for i in range(3, 0, -1):
                print(f"[{i}] ...")
                time.sleep(1)
            print("🚀 开始！开始录制！")

            # 清空缓存，确保数据是最新的
            ser.reset_input_buffer() 
            
            t_data, a_data, v_data = [], [], []
            s_data = [0]
            raw_rows = [] # 用于保存到 CSV 的原始数据列表
            
            start_time = time.time()
            t_offset = None 
            
            while (time.time() - start_time) < RECORD_DURATION:
                if ser.in_waiting > 0:
                    try:
                        line = ser.readline().decode('utf-8', errors='ignore').strip()
                        parts = line.split(',')
                        
                        if len(parts) == 3:
                            curr_t = float(parts[0])
                            curr_a = float(parts[1])
                            curr_v = float(parts[2])
                            
                            if t_offset is None: t_offset = curr_t
                            
                            t_rel = curr_t - t_offset
                            t_data.append(t_rel)
                            a_data.append(curr_a)
                            v_data.append(curr_v)
                            
                            # 计算位移
                            if len(t_data) > 1:
                                dt = t_data[-1] - t_data[-2]
                                new_dist = s_data[-1] + (curr_v * dt)
                                s_data.append(new_dist)
                            
                            # 存入原始数据行 (相对时间, 加速度, 速度, 计算位移)
                            raw_rows.append([f"{t_rel:.3f}", f"{curr_a:.3f}", f"{curr_v:.3f}", f"{s_data[-1]:.3f}"])
                    except:
                        continue
            
            # 补齐位移长度
            if len(s_data) > len(t_data): s_data = s_data[:len(t_data)]
            
            print(f"✅ 录制结束。采集点数: {len(t_data)}")
            
            if len(t_data) > 0:
                # --- 保存数据到 CSV 文件 ---
                file_id = int(time.time())
                csv_filename = f"Data_{file_id}.csv"
                with open(csv_filename, 'w', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow(["Time(s)", "Accel(m/s2)", "Velocity(m/s)", "Displacement(m)"])
                    writer.writerows(raw_rows)
                print(f"💾 原始数据已保存至: {csv_filename}")
                
                # --- 生成分析图表 ---
                generate_analysis_plot(t_data, a_data, v_data, s_data, file_id)
            
        elif cmd == 'q':
            break

    ser.close()

def generate_analysis_plot(t, a, v, s, file_id):
    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(10, 12), sharex=True)
    
    # 子图 1: 加速度
    ax1.plot(t, a, color='red', label='Accel (m/s²)')
    ax1.set_ylabel('Acceleration')
    ax1.grid(True, alpha=0.3)
    ax1.legend(loc='upper right')
    ax1.set_title(f'Vehicle Dynamics Analysis (Experiment ID: {file_id})')

    # 子图 2: 速度
    ax2.plot(t, v, color='blue', label='Velocity (m/s)', linewidth=2)
    ax2.set_ylabel('Velocity')
    ax2.grid(True, alpha=0.3)
    ax2.legend(loc='upper right')

    # 子图 3: 位移
    ax3.plot(t, s, color='green', label='Displacement (m)', linewidth=2)
    ax3.set_xlabel('Time (s)')
    ax3.set_ylabel('Displacement')
    ax3.grid(True, alpha=0.3)
    ax3.legend(loc='upper right')

    plt.tight_layout()
    plt.savefig(f"Plot_{file_id}.png")
    print(f"📊 分析图表已保存为: Plot_{file_id}.png")
    plt.show()

if __name__ == "__main__":
    run_experiment()