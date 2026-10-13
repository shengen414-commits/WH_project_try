
目前主程序是 `PIO_RotationmitCamera`（ESP32 固件）和
`PyScripts/Camera_mit_Rotation_dashboard.py`（Orange Pi 网页后台）。

## 车速＋扫描阀同步记录（第一版）

启动入口仍为小车后台，网页仍使用 5000 端口：

```bash
python3 PyScripts/Camera_mit_Rotation_dashboard.py
```

启动本身不会连接扫描阀或修改网口。小车页面新增“车速＋扫描阀同步记录”区，
通过后台的 `SyncRecorder` 使用同一个 `MPS4264Controller`。
不要同时运行 `python3 -m mps4264_app.main` 控制同一设备或占用同一个 UDP 端口。

同步面板与线控底盘驱动台在宽屏左右并列，窄屏自动纵向排列。
连接状态灯、UDP 状态灯、设备命令行和 VER/STATUS/VALVESTATE/LIST 快捷命令常驻显示。
网络设置、扫描参数、实时压力曲线、64 路数值、SAVE/CALZ、压力单独采集、文件转换下载、
事件和历史 CSV 绘图入口放在默认折叠的“更多扫描阀功能”里。
同步记录期间命令行 STOP 会协调双方停止，其他设备命令不允许修改该批采集状态。
没有单独压力文件时，命令行 SCAN 启动同步记录；新建了压力文件时，SCAN/STOP 仅管理压力采集。
压力单独采集保存在 `Car_Records/Sync_Records/Pressure_Only/`，历史图支持浏览同步批次的压力 CSV。

操作顺序：

1. ESP32 使用正常 `esp32dev` 固件，保持串口正常模式（`R`）；舵机 demo 或暂停编码器的巡线调试模式不能用于车速同步。
2. 在同步区域核对扫描阀 IP、Orange Pi 网口 CIDR（默认 `191.30.90.82/16`）和网口名（默认 `eth0`），点击连接。
   默认连接时检查并添加网口地址；普通用户需要先执行 `sudo -v`。已配置网络可取消该复选框。
   无权限或网络初始化失败会明确返回错误，其他小车功能仍可用。
3. 点击“读取设备信息”，核对设备版本、参数和 UDP 目标。
   若设备尚未配置为当前 Orange Pi 的 UDP 二进制输出，点击“设置 UDP 并保存”，
   等待返回后给扫描阀断电重启，再点“已重启，重新连接”，重新读取信息。
   该设置包含 `SET TRIG 0`；本版没有外部硬件触发。
4. 设置 RATE、FPS 和扫描组后点“发送采样参数”。默认 RATE=100 Hz、FPS=0（直到 STOP）、64 通道。
   参数只是页面初始输入值，必须发送或从设备读取，才会成为本次记录的实际配置。
5. 确认编码器就绪，点击“开始同步记录”。先监听压力 UDP，再记录车速，最后发送 SCAN。
   收到双方数据后显示“正在记录”；记录按钮不改变油门、不启动相机，车速原有独立记录可以同时使用。
6. 点“停止并保存”；也可达到时长上限自动结束，有限 FPS 完成后自动收尾。
   STOP 未确认时显示“重试停止并保存”，压力文件仍保持打开，禁止开始新记录或更改设备参数。
   恢复设备通信后重试停止。编码器中断、压力长时间无数据或写盘错误会自动收尾并显示失败，保留已有数据。

每次实验位于 `Car_Records/Sync_Records/唯一实验编号/`：

| 文件 | 内容 |
|---|---|
| `speed.csv`、`speed.csv.meta.json` | 原始脉冲、ESP32 时间/序号、主机单调时间、滤波速度、控制状态和保存统计 |
| `pressure.dat` | 压力原始二进制帧 |
| `pressure.index.csv` | 每帧对应的主机接收时间和设备帧号/时间 |
| `pressure.meta.json`、`pressure.csv` | 扫描参数、接收统计和转换后的压力数据 |
| `session.json` | 共同时间起点、开始/停止事件、第一条数据时间、双方状态、失败原因和匹配统计 |
| `synchronized.csv` | 每个压力帧匹配的车速、相对时间、匹配时间差和有效性标记 |

压力 CSV 与索引按记录顺序关联并校验帧号；不会用字典合并而丢掉重复帧。
同步 CSV 采用主机单调时间的最近邻匹配，默认容差 30 ms，可在页面修改。
超出速度覆盖、超出容差或跨设备重启边界时 `match_valid=0`，速度值留空。
第一版 `speed_rpm` / `speed_kmh` 使用现有滑动窗口及 EMA 的滤波速度，存在滤波延迟；
原始 `Position` / `Time_ms` 保留，可用于后续离线速度计算。
USB、UDP、批量数据和线程调度延迟仍存在，本版不能证明采样时刻精确同步。
时间戳中的 ns 是存储单位，不代表纳秒级测量精度。

Linux/Orange Pi 的 Docker 部署使用单独的 host 网络配置，避免原 bridge 配置收不到扫描阀 UDP：

```bash
# 如果原 Compose 容器正在运行，先停掉它，避免同时占用相机/串口。
docker compose down
docker compose -f docker-compose.sync.yml up -d --build
docker compose -f docker-compose.sync.yml logs -f wh-dashboard
```

该配置给容器 NET_ADMIN 用于添加主机网口地址，镜像已加入 iproute2。
设备路径和网口名须按实际 Orange Pi 核对。Windows Docker Desktop 不适用本机硬件/host 网口部署步骤。
其他部署方式如已在主机配置网络，可以自行去掉 NET_ADMIN 并取消网页的网口初始化复选框。
本功能仍用于现有的 MPS4264 Gen1 协议；新型号需要先确认协议。

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
