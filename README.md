
目前主程序是 `PIO_RotationmitCamera`（ESP32 固件）和
`PyScripts/Camera_mit_Rotation_dashboard.py`（Orange Pi 网页后台）。

## 无 ESP32 SD 卡版本

ESP32 负责编码器中断计数、电调 PWM 输出、遥控接管和失联保护。
Orange Pi 负责串口接收、速度计算、相机采集和文件存储。
主固件已移除 SD/SPI 文件存储依赖。`ESP32-Arduino` 和
`PyScripts/Rotation_SD*` 中保留的是早期实验程序，不要用它们烧录或启动新版本。

### 1. 更新并烧录 ESP32

在项目根目录执行（或在 PlatformIO 中选择 `PIO_RotationmitCamera` 构建、上传）：

```powershell
pio run -d PIO_RotationmitCamera
pio run -d PIO_RotationmitCamera -t upload --upload-port COM实际端口号
```

串口波特率仍为 115200。新旧 ESP32/Python 协议不兼容，需要两端一起更新。
验证 ESP32 输出包含持续的 `[ENC],序号,开机毫秒数,累计脉冲数` 行后，关闭串口监视器，
再让 Orange Pi 连接。GPIO 26/25 编码器、GPIO 14 电调、GPIO 27 遥控输入保持原接线。
SD 模块的 CS=5、SCK=18、MISO=19、MOSI=23 接线不再使用；断电后可移除整个外接 SD 模块。
这里移除的是 ESP32 外接存储模块，Orange Pi 自身启动/存储介质仍需保留。

### 2. 更新 Orange Pi 后台

把更新后的项目同步到 Orange Pi，尤其要包括新增的 `PyScripts/telemetry.py`。
在项目根目录运行已有虚拟环境中的 Python：

```bash
python3 PyScripts/Camera_mit_Rotation_dashboard.py
```

已使用 Docker Compose 的环境可执行：

```bash
docker compose up -d --build wh-dashboard
docker compose logs -f wh-dashboard
```

默认连接 `/dev/ttyUSB0`。直接运行时可用环境变量 `ESP32_SERIAL_PORT` 改串口；
Docker 运行时须同时修改设备映射和容器环境变量。
网页端口仍为 5000。若未收到最近 1 秒内的有效新协议数据，开始记录会明确失败，
页面速度显示 `--`，避免将旧状态误当作实时速度。

### 3. 记录文件和验证顺序

- 独立速度记录：`Car_Records/Speed_Records/speed_record_时间.csv`，最长 10 分钟。
- 三秒多源记录：`Car_Records/时间/sensor_data.csv`、左右相机图片目录、
  `sensor_data_enhanced.csv`、`sync_meta.json`、`record_stats.json`。
- 每个原始 CSV 都有 `.csv.meta.json`，保存最终行数、串口缺样数、队列丢样数和停止原因。

保存路径相对于进程的工作目录；使用当前 Compose 挂载时，文件位于宿主机项目的
`Car_Records` 目录。每条有效编码器数据只提交一次；独立速度记录和多源记录可同时进行。
ESP32 每约 10 ms 上报一次，而不是把 Python 的定时读取次数当成采样数。
原始文件保留 `Time_ms`/`Position` 列，并新增 `sequence`、`device_epoch`、主机时间戳、
滤波速度、油门状态和控制模式，同时保留原速度文件的 `position` 列。

先在静止状态确认串口和网页，再手动转动编码器，检查速度方向及脉冲变化；
然后短时间记录，检查 CSV 行数、图片和状态中的丢样统计。
完成后再验证网页油门、物理遥控接管、制动和失联归中。
固件编译与模拟测试不能替代这一步的实车验证。

### 实现细节与边界

- 电调 T/B 指令采用逐字符解析，仅接受 `T1000\n` 到 `T2000\n`、
  `B1000\n` 到 `B2000\n`；半条指令 100 ms 后丢弃。`E` 可立即中断半条命令。
  旧 SD 指令 `s/p/r/l` 不再使用，也不再清零编码器计数。
- 串口发送缓冲不足时跳过该次上报，序号缺口可见；主循环继续处理电调控制。
  Python 不再为记录/制动清空接收缓冲，磁盘写入在独立线程中执行，队列溢出明确计数。
- 相机按实际三秒时间收集帧，帧数随真实帧率变化。图片保存完成后再生成匹配文件；
  保存失败或超时会显示错误，原始 CSV 保留。
- 增强 CSV 的速度由相邻原始脉冲差分计算，可能比网页滤波速度更有波动。
  图片匹配用主机串口处理时间和相机读帧时间，含 USB/缓存延迟；不代表曝光硬件同步。
  旧的 `python_time_ns_est` 字段为兼容保留，值等于新增 `host_received_time_ns`。
  `esp32_elapsed_ms` 在设备重启后重新计时，以 `device_epoch` 区分。
- CSV 定期 flush，正常结束时执行 fsync；程序异常结束或断电仍可能损失未同步数据。
  移除 SD 后，Orange Pi 不在线或串口断开期间没有独立备份。
- 后续巡线采集可接在 `main.cpp` 的非阻塞传感器轮询位置。当前尚未读取巡线硬件：
  接入前需要确定模块型号、数字/模拟/I2C/UART 接口、引脚和电平，随后扩展带类型标识的串口帧。

### 自动检查

```bash
python -X utf8 -B -m unittest discover -s PyScripts/tests -v
node --check PyScripts/static/js/main.js
pio run -d PIO_RotationmitCamera
```

Python 测试使用模拟串口和相机，不访问真实设备；有主机 `g++` 时还会测试实际 C++ 命令解析源码。
