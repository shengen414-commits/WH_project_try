import serial
import time

# ================= 配置区 =================
SERIAL_PORT = 'COM12'  # 请替换为你实际的串口号
BAUD_RATE = 115200
# ==========================================

print(f"正在连接 ESP32 (端口 {SERIAL_PORT})...")

try:
    # 1. 打开串口
    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=2)
    time.sleep(2) # 等待 ESP32 重启准备就绪
    print("连接成功！\n")

    # 2. 发送列出文件的指令 'l'
    print("正在请求 SD 卡文件列表...\n")
    ser.write(b'l')

    # 3. 接收并打印数据
    is_receiving = False

    while True:
        if ser.in_waiting > 0:
            # 读取一行串口数据
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            
            # 检测起点标志
            if line == "---FILE_LIST_START---":
                is_receiving = True
                print("================ SD 卡存储内容 ================")
                continue
            
            # 检测终点标志
            if line == "---FILE_LIST_END---":
                print("===============================================\n")
                break
            
            # 打印文件信息
            if is_receiving:
                print(line)

    ser.close()
    print("查询完成，串口已断开。")

except serial.SerialException as e:
    print(f"\n[串口错误]: {e}")
    print("👉 请检查端口号，并确保没有其他软件（如 Arduino IDE）正在占用该串口！")
except Exception as e:
    print(f"\n[发生错误]: {e}")