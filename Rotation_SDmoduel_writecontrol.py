import serial
import time

# 1. 连接 ESP32 的串口 (注意：请把 'COM3' 换成你实际的端口号，Mac系统一般是 '/dev/ttyUSB0')
# 此时需要确保 Arduino IDE 的串口监视器处于关闭状态，否则端口会被占用
ser = serial.Serial('COM12', 115200, timeout=1)

# 给 ESP32 一点时间重启和初始化 SD 卡
time.sleep(2)

print("正在发送开始记录指令 (s)...")
ser.write(b's')  # b 代表发送字节流

# 假设让它记录 10 秒钟的数据
print("等待 10 秒...")
time.sleep(10)

print("正在发送停止记录指令 (p)...")
ser.write(b'p')

ser.close()  # 释放串口
print("记录完成！现在你可以拔出 SD 卡了。")
