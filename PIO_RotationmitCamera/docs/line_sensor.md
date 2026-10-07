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

串口监视器输入 `L`（小写 `l` 也可）进入巡线调试模式：停止发送 `[ENC]`，每 200ms 至多打印一行可读的探头状态，扫描仍按原频率运行，编码器计数和电调控制继续运行。

```text
[LINE_DBG] raw=11100111 line=00011000
```

两组数字都按 **CH1 → CH8** 从左到右排列。`raw` 是原始电平，`line` 的 1 表示符合配置的目标线电平。上例表示 CH4、CH5 命中。固定输入未变化时也定期打印，便于确认采集仍在运行。

输入 `R`（小写 `r` 也可）恢复正常 `[ENC]`、`[LINE]` 数据流。重启也恢复正常模式。调试期间 Orange Pi 收不到编码器上报，速度显示与记录不会更新；连接 Orange Pi 正式运行前恢复正常模式。调试输出限速不会降低传感器扫描频率。

PlatformIO 监视器可使用行输入模式，避免高速输出干扰输入：

```powershell
pio device monitor -b 115200 --filter send_on_enter
```

输入 `L` 后回车；恢复时输入 `R` 后回车。

## G：记录中线并绘图

串口终端本身只能显示文本，绘图由独立的 `PyScripts/line_debug_plot.py` 完成。ESP32 的 `G/g` 命令切换到约 100Hz 的 `[LINE]` 数据流并暂停 `[ENC]`；Python 工具负责计时、保存和绘图。在普通串口监视器中发送 G 只会开始数据输出，不会自动生成图片。

1. 重新编译并烧录当前固件。
2. 关闭 PlatformIO 串口监视器和占用该串口的 Orange Pi 程序。
3. 在仓库根目录运行（COM12 替换为实际 ESP32 串口）：

```powershell
python PyScripts/line_debug_plot.py --port COM12
```

4. 在 Python 工具的 `Command >` 提示符中输入 `G` 后回车，开始默认 10 秒记录。移动黑胶带，观察它在探头阵列下的位置变化。
5. 到时自动停止记录并弹出图窗，同时保存 CSV、PNG 到 `Car_Records/Line_Debug/`。关闭图窗回到输入提示，可再次输入 G 录制。
6. 输入 Q 退出并恢复正常数据流；R 也可以恢复正常数据流。图窗打开期间 ESP32 保持 L 模式，采集继续运行，ENC 上报暂停。

自定义记录时长：

```powershell
python PyScripts/line_debug_plot.py --port COM12 --duration 30
```

无桌面环境时加 `--no-show`，仅保存文件。用 `--reverse` 翻转纵轴通道方向。不指定 `--port` 时工具列出串口并提示选择。依赖 pyserial、matplotlib（仓库 requirements.txt 已包含）。

横轴为收到的首个样本起算的 ESP32 时间（秒），纵轴为黑线中心相对探头阵列中心的位置，单位为探头间距：CH1=-3.5、CH8=+3.5，CH4/CH5 中间为 0。黑线命中的连续通道按等权平均估算中心；二值传感器无法提供连续灰度精度。CH1/CH8 的物理左右方向按实际安装确认。

全无黑线标记为 lost；全黑为 all_black；多个不连续命中组为 multiple。三种情况均无唯一可信中线，图上用标记表示、曲线断开，CSV center 留空。丢帧或超过 50ms 的间隔也断开曲线，CSV 保留帧编号和 missing_before；设备重启/数据倒序则终止此次采集并报错。

仅查看绘图效果（模拟数据，不代表实测）：

```powershell
python PyScripts/line_debug_plot.py --demo
```

1. 修正 AD0 接线与配置，确认 OUT 电平，给模块供电并共地。
2. 在工程目录运行 `pio run`，烧录后打开 115200 波特率串口监视器。
3. 应先看到 `[LINE_READY]`，随后持续出现 `[LINE]` 和 `[ENC]`。
4. 依次将每个探头放到黑线和白底上，检查对应位变化，确认通道顺序和 `LINE_LEVEL`。

本模块实现巡线传感器读取和串口上报。当前 Orange Pi 的 EncoderStream 只解析 `[ENC]`，会忽略 `[LINE]`；网页显示、CSV 保存巡线数据尚未接入。本代码也未加入自动转向或 PID 控制。

资料：
- https://www.yahboom.net/public/upload/upload-html/1763523992/Data%20reading.html
- https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/peripherals/gpio.html
