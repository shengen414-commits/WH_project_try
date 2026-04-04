import serial
import time

# ================= 配置区 =================
SERIAL_PORT = '/dev/ttyUSB0'  # 请替换为你实际的串口号
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

    # ... 前面代码不变 ...
    while True:
        if ser.in_waiting > 0:
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            
            if "---FILE_LIST_START---" in line:
                is_receiving = True
                print("\n================ SD 卡存储内容 ================")
                continue
            
            if "---FILE_LIST_END---" in line:
                is_receiving = False
                print("===============================================\n")
                break
            
            if is_receiving:
                # 🚀 关键：只有包含 [文件] 或 [文件夹] 的行才准进入显示区
                if "[文件]" in line or "[文件夹]" in line:
                    print(line)
                    found_any_file = True
                # 如果是“待机中”的干扰，直接丢弃，不打印
    ser.close()
    print("查询完成，串口已断开。")

except serial.SerialException as e:
    print(f"\n[串口错误]: {e}")
    print("👉 请检查端口号，并确保没有其他软件（如 Arduino IDE）正在占用该串口！")
except Exception as e:
    print(f"\n[发生错误]: {e}")