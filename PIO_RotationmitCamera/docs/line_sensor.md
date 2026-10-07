# 亚博八路白灯巡线模块

## 接线与配置

当前工程板型为 `esp32dev`（普通 ESP32）。

| 模块 | GPIO | 模式 |
|---|---|---|
| AD2 | D33 / GPIO33 | OUTPUT |
| AD1 | D32 / GPIO32 | OUTPUT |
| AD0 | D13 / GPIO13（由原 D35 改接） | OUTPUT |
| OUT | D34 / GPIO34 | INPUT |
| 5V | 稳定 5V 电源 | 电源 |
| GND | ESP32 GND，外部电源共地 | 地 |

**GPIO35 只能输入，不能用于 AD0。** 根据确认，代码已将 AD0 配置为 GPIO13，实际接线也要从 D35 移到 D13。`initLineSensor()` 会检查引脚能力、重复和当前项目占用；无效时不配置巡线引脚，返回 false，启动打印 `[LINE_ERROR]`。以后更改接线时，必须同时修改 `include/line_sensor.h` 的引脚配置并重新编译、烧录。

OUT 接入前须确认高电平不超过 ESP32 的允许电压；若为 5V，先转换为 3.3V。GPIO34 没有内部上下拉，代码使用 `INPUT`，不要让 OUT 悬空。

## 文件与接口

- `include/line_sensor.h`：引脚、扫描周期、极性配置和数据接口。
- `src/line_sensor.cpp`：地址选择、分步扫描、完整帧缓存。
- `src/main.cpp`：初始化，并在每轮循环调用扫描和上报。
- `src/serial_transport.cpp`：独立 `[LINE]` 数据包；保留原 `[ENC]` 格式。

`updateLineSensor()` 每轮最多读取一个探头，不调用 delay 或 delayMicroseconds。切换地址后以 micros 差值判断至少等待 50μs；完整扫描按约 10ms 周期启动，实际时间取决于主循环负载。八路是依次采集，并非同时采样。无完整帧时 `readLineSensorFrame(frame)` 返回 false；否则复制最近的完整帧。接口供同一主循环使用，若以后跨任务调用须加同步。

## 串口数据

115200 波特率，格式：

```text
[LINE],sequence,time_ms,raw_mask,line_mask
[LINE],0,1234,231,24
```

- `sequence`：完整扫描的编号，从 0 开始；串口拥塞时可能跳号。
- `time_ms`：ESP32 启动后的毫秒数，取扫描完成时刻，uint32 回绕。
- `raw_mask`：0～255 的十进制位掩码，bit0 对应 CH1，bit7 对应 CH8；置位表示 OUT 电平为 HIGH。
- `line_mask`：与 `LINE_LEVEL` 匹配的通道置位。默认 LOW 表示检测到目标线，需要实测确认；若目标线为 HIGH，修改配置。

示例 231 = 二进制 11100111，默认 LOW 极性时 line_mask 为 24 = 00011000，表示 CH4、CH5 命中。地址 AD2 AD1 AD0 的 000～111 对应 CH1～CH8；探头的物理左右方向需按安装方向确认。

TX 缓冲不足时不等待，尝试在后续循环发送最近完整帧，旧帧可能被新帧覆盖。该机制优先保持现有控制和编码器上报的及时性，不保证每一帧传输。

## 调试

1. 修正 AD0 接线与配置，确认 OUT 电平，给模块供电并共地。
2. 在工程目录运行 `pio run`，烧录后打开 115200 波特率串口监视器。
3. 应先看到 `[LINE_READY]`，随后持续出现 `[LINE]` 和 `[ENC]`。
4. 依次将每个探头放到黑线和白底上，检查对应位变化，确认通道顺序和 `LINE_LEVEL`。

本模块实现巡线传感器读取和串口上报。当前 Orange Pi 的 EncoderStream 只解析 `[ENC]`，会忽略 `[LINE]`；网页显示、CSV 保存巡线数据尚未接入。本代码也未加入自动转向或 PID 控制。

资料：
- https://www.yahboom.net/public/upload/upload-html/1763523992/Data%20reading.html
- https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/peripherals/gpio.html
