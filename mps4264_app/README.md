# MPS4264 Gen1 独立可视化控制台

本模块不改动现有小车代码。它复现当前项目实际需要的 ScanTel v1.08 工作流：TCP 23 配置、UDP 二进制收帧、保存原始 `.dat`、显示实时压力、停止和离线转 CSV；同时提供可由其他 Python 程序导入的 `MPS4264Controller`。适用于手册协议为 MPS4264 **Gen1** 的设备。2026 年后出厂的 Gen2 须先核对协议，不应仅凭外观沿用解析器。

## 在 Orange Pi Ubuntu 上运行

在项目根目录执行（Python 3.10+）：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install flask
sudo ip addr add 191.30.90.82/16 dev eth0
python3 -m mps4264_app.main

```

启动时程序会检查 `eth0`：如果没有 `191.30.90.82/16`，会执行等价于 `sudo ip addr add 191.30.90.82/16 dev eth0` 的初始化；已有该地址就跳过，不删除或替换其他地址。页面的设备 IP 默认是 `191.30.90.102`，第二步 Orange Pi 网口 IP 默认是 `191.30.90.82`。首次用普通用户启动前可先执行 `sudo -v`，让后台的非交互式提权能成功；如果作为服务开机自启，需要为网口初始化提供 NET_ADMIN 权限（例如由 systemd 的特权 `ExecStartPre` 完成地址配置），否则程序会明确报错退出，不会静默跳过。网口名称不为 `eth0` 时，启动参数加 `--interface 实际网口名`；网络已由系统配置时可用 `--skip-network-init`。可用 `ip -br addr show eth0` 和 `ping -c 3 191.30.90.102` 检查连通性。

在 Orange Pi 本机浏览器打开 `http://127.0.0.1:5055`。如果从同一可信局域网的另一台电脑访问：

```bash
python3 -m mps4264_app.main --host 0.0.0.0 --port 5055
```

面板没有账号认证；**不要向公网或不可信网络开放**。`recordings/` 自动创建并被本目录 `.gitignore` 忽略。当前项目的 Docker Compose 未映射 UDP 数据端口，首次联调建议在 Orange Pi 主机上直接运行本模块，不放进原有 `wh-dashboard` 容器。

设备需要独立电源和网线；若为 CPx 气动阀，测量时还须确认控制气路与 `VALVESTATE=PX`。设备实际地址应以现场连通性和 `LIST IP` 为准。启动时的网口地址是临时地址，重启系统后由程序再次按需添加；它不是永久的 Ubuntu 网络配置。

## 面板操作顺序

1. 输入现场真实设备 IP，连接并“读取设备信息”，核对 `VER`、阀位、`LIST IP/S/M/UDP`。页面下方的设备命令窗口可发送单行 ScanTel 命令（如 `STATUS`、`LIST S`），并显示设备原始回显；SCAN/STOP/CALZ/SAVE 仍走采集状态保护。
2. 首次把 Windows 主机换成 Orange Pi 时，输入 **Orange Pi 网口 IP**、UDP 端口（默认 50023），点击“设置二进制 UDP + SAVE”。等待保存完成，给设备断电重启，再点击“已重启，重新连接”。这会发送 `SET FORMAT F B`、`SET TRIG 0`、`SET ENFTP 0`、`SET ENUDP 1`、`SET SVRSEL 3`、`SET IPUDP`、`SAVE`。厂家手册明确要求 UDP 二进制输出使用 `SET FORMAT F B`。TCP 控制端口保持 23；UDP 数据端口改为 50023。设备与面板的 UDP 端口必须一致。

   设备目前若仍显示 `SET IPUDP 191.30.90.82 23`，只更新面板程序不会收到数据；必须通过上一步把设备改为 `191.30.90.82 50023`，等待 SAVE 完成并重启，再用 `LIST UDP` 与 `LIST M` 核对。默认 50023 避开了普通用户监听 UDP 23 的低端口权限限制。
3. 先设置 `RATE=5`、`FPS=100`、`OPTIONS=0 0 16`，点击“发送 SET”。如需断电后保留，点击 SAVE。正式高频采集再改成 64 路最高 850 Hz；`RATE=2500` 时必须选 OPTIONS 组 1–4，且只有 16 路有效。
4. 点击“新建文件”先打开并监听 UDP，再点 SCAN。确认帧数增长及数据单位正确；04 区的 `UDP Byte Counter` 会实时显示本次扫描收到的 UDP 字节数，旁边同时显示有效帧写入字节数。FPS 有限时设备会自行完成采样，但仍应点 STOP 结束本次扫描；然后点“关闭文件”。最后选择 `.dat` 点击“转换二进制为 CSV”。
5. CALZ 只在零压/等压条件确认后执行。不要在测点上施加未知压力时校零。

每个记录有 `<name>.dat`（连续的原始 348 字节帧）、`<name>.index.csv`（主机接收时间、设备帧号/时间）、`<name>.meta.json`（参数及丢包统计），转换后生成 `<name>.csv`。单位索引 `23` 表示 Pa，已无需再乘 6894.759766。快速扫描组号不在二进制帧里；本模块录制的文件可从 `.meta.json` 自动读取，外部来源的快速扫描 `.dat` 需手选组号。厂家手册描述二进制帧为大端序，但现场 `Ver 2.10` 设备的抓包显示为小端序；解析器根据帧头类型和 348 字节长度自动识别两者。先前因解析失败产生的空 `.dat` 没有原始帧，不能补转 CSV，必须更新程序后重新采集。

实时趋势图约每 200 ms 获取最新帧一次，仅用于观察；所有收到的有效原始帧仍直接写盘。UDP 本身不保证不丢帧，请查看帧号缺口并与 FPS 比较。设备时间、主机接收时间和相机曝光时间**并非天然同步**。

## CSV 多通道压力—时间图

原有采集面板首页新增“打开 CSV 压力—时间图”入口，地址为
`http://127.0.0.1:5055/plot`。它只读取历史 CSV，不连接扫描阀，也不发送设备命令。
支持 ScanTel 的 `FTime/01Press` 格式和本程序的 `frame_time_sec/P01` 格式。
可以从浏览器上传 CSV，或者选择服务端 `--data-dir` 目录中的 CSV；上传内容仅在内存中解析，不会保存到服务器。

也可以不启动采集程序，单独运行绘图服务（不需要 sudo，不会配置网口）：

```bash
# 在 WH_project_try 项目根目录、已安装 Flask 的虚拟环境中
python3 -m mps4264_app.plotting
# 自动打开某份 CSV 的数据
python3 -m mps4264_app.plotting --csv /home/orangepi/WH_project_try/mps4264_app/recordings/test008.csv
# 同一可信局域网的其他电脑访问：浏览器打开 http://香橙派地址:5056/plot
python3 -m mps4264_app.plotting --host 0.0.0.0 --port 5056
```

单独运行时本机浏览器打开 `http://127.0.0.1:5056/plot`。默认数据目录是
`mps4264_app/recordings`，可用 `--data-dir` 指定。Windows 示例：

```powershell
python -m mps4264_app.plotting --csv "E:\大创-wh\测压阀\test008-linux.csv"
```

勾选需要叠加的通道，可全选、取消全部、设置显示时间范围，并保存带通道图例的 PNG。
页面和绘图库全部在本地，无需联网加载。后端保留完整帧数据，曲线仅在显示时按像素保留极值；指针读数取最近的真实帧，不做插值。空白、NaN/Inf 和 `-999999` 作为无效值，全部无效的通道不可选；曲线不跨越无效值或帧号缺口连线。不修改 CSV，不重复乘单位转换系数。

当前限制：单个 CSV 不超过 128 MiB；帧号与时间必须递增，多个扫描拼接产生的重置应先拆分。Linux CSV 的单位索引 23 显示 Pa、27 显示 RAW，其他单位索引原样标注，不猜测单位。设备帧时间并非主机 UTC，不能直接代替摄像/速度同步时间。

其他 Python 程序可以复用解析器或网页服务：

```python
from mps4264_app.plotting import read_pressure_csv, create_plot_app, register_plot_routes

data = read_pressure_csv("run.csv")
time_seconds = data.times
pressure_channel_1 = data.channels["P01"]  # 无效值为 None
payload = data.to_dict()                   # 完整分辨率，可 JSON 序列化

# 可选：创建独立绘图服务；调用 create_plot_app 不会自动运行服务器
app = create_plot_app(data_dir="Car_Records/mps_run_001")
# 或向已有 Flask app 注册 /plot 页面，不要在同一个 app 重复注册
# register_plot_routes(existing_app, data_dir="Car_Records/mps_run_001")
```

前端绘图类为 `window.PressureTimeChart`（`static/pressure_plot.js`）。可在其他页面用
`new PressureTimeChart(canvas, {tooltip})` 创建实例，再调用 `setData(payload)`、
`setChannels(["P01", "P04"])`、`setRange(0, 2)`、`exportPNG()`；不用时调用 `destroy()`。
此功能是离线文件查看，不是实时采集或同步控制模块。

## 采集控制接口的导入示例

在项目根目录的其他 Python 程序中：

```python
from mps4264_app import MPS4264Controller

mps = MPS4264Controller("Car_Records/mps_run_001")
mps.connect("191.30.90.102")
mps.device_info()                       # 只在未扫描时查询
mps.set_parameters(rate=5, fps=100, fast_group=0)
mps.new_file("run_001", udp_port=50023) # 必须先监听，再 SCAN
mps.scan()
# 其他程序可调用 mps.status() 查看帧数及最新压力
mps.stop()
mps.close_file()
mps.convert_file("run_001.dat")
mps.disconnect()
```

如设备的 UDP 目标还是端口 23 或旧 Windows 主机，先调用 `mps.configure_udp("191.30.90.82", 50023)`，**等待 SAVE 完成、重启设备**，随后 `mps.connect("191.30.90.102", confirm_reboot=True)`。不要在扫描过程中调用 `device_info()` 或重复连接。`set_parameters`、`calz`、`save_settings`、`new_file`、`scan`、`stop`、`close_file`、`convert_file` 都可单独调用；导入模块不会自动连接设备或启动网页。

## 验证和限制

### 命令分段耗时日志

每次网页 POST 操作（命令窗口、SET、SCAN、STOP、SAVE 等），命令窗口都会新增
“耗时 [ui-…]”记录。同一 ID 出现在 HTTP 返回和后台 JSONL 日志中，可对应查找。
后台日志在当前 `--data-dir` 下的 `diagnostics/command_timing.jsonl`，默认就是
`mps4264_app/recordings/diagnostics/command_timing.jsonl`；启动服务的终端也会输出。
每个日志文件最多约 2 MiB，保留 3 个轮转备份。正常状态轮询不落日志，超过 500 ms
的慢 GET 会记录，以便发现状态查询排队。日志本身不改变重连或设备回复超时。

状态/转换进度/文件列表请求均采用单请求在途保护，状态轮询在上一请求完成后等待
200 ms 再发下一次。`/api/status` 在控制锁忙时返回 202 + `busy=true`，不排队等锁，
网页保留上一次状态并显示忙碌说明。控制操作直接用返回的状态更新事件与计数，
不再为了辅助状态/文件列表刷新延长按钮的等待。`status_epoch/status_revision` 避免
较旧响应覆盖新的状态。STOP 超时会保留未确认的扫描状态和打开的文件；恢复通信后
可以重试 STOP，不会记录虚假的“已停止”或继续关闭文件。

SCAN 按钮结束等待只表示 SCAN 命令发送完成，不是采集完成。2500 Hz × 10000 帧
理论采集时长为 4 秒；UDP 字节为零说明尚未收到数据，不能仅凭按钮状态判定扫描成功。
当前不会自动重发 SCAN、STOP、SET 或自动重连。

```bash
tail -n 20 mps4264_app/recordings/diagnostics/command_timing.jsonl
```

字段说明：

- `server_ms`：从 Flask 接收请求到生成计时摘要的后台耗时，含业务处理和等锁；
  不含请求抵达 Flask 之前的排队、网络传输和随后日志写入/追加计时字段的开销。
- `controller_lock_wait_ms` / `tcp_lock_wait_ms`：本次请求累计等待控制器/TCP锁的时间。
- `commands`：实际发送的每条 TCP 命令，包含 `send_ms`、`first_tcp_byte_ms`（收到首个 TCP
  数据字节）、`first_text_ms`（过滤 Telnet 协商后首段文字）、`response_ms`（读取完整回复
  或直到出错的时间）、`rx_bytes`、`prompt_received`、`outcome`、`error`。
- 没收到字节时，首字节/首文字时间为 `null`，不是零。失败请求也有计时。
  SCAN 仍只发送，不等提示符，`waits_for_prompt=false`，不会错误标为已获设备确认。
  STOP 额外记录 `stop_drain_ms`（清理残留回复的耗时）。
- 浏览器窗口显示的“网页总”包含请求等待、传输、后台处理和 JSON 解析。浏览器控制台
  `[MPS timing]` 还包含 Resource Timing 指标。“浏览器发请求前”可能包含排队、DNS/TCP
  建连等，不能全部归为排队；网页总减后台耗时也不能全部归为网络延迟。

例如控制锁等待很长而 TCP 回复很快，应检查后台并发；网页总很长而后台很快，
应检查浏览器排队、网络/代理和前端；首字节很久或直到超时一直为 null，才指向
设备回复、旧 TCP 会话或通信问题。计时使用单调时钟，不跨电脑比较绝对时间。

### 快速组与 DAT 帧头不一致

手册将普通帧标为 `0x0A`、快速帧标为 `0x10`。转换器不再仅凭 `0x0A`
拒绝指定的快速组：选择组 1–4，或“自动”从同名 `.meta.json` 读取组号后，
会按该组保留 16 个实际通道，其余 48 列留空；实时预览也按已记录组号屏蔽非组内通道。
出现 `0x0A` 与快速组的冲突时，页面和日志会明确提醒，结果包含 `packet_type`、
`fast_group_source` 与 `warnings`，不会将转换成功当作设备已启用快速扫描的证据。
必须核对采集时设备 `LIST S` 返回的 `SET OPTIONS` 和 `SET RATE`；转换时的当前设备参数
不能证明历史文件采集时的参数。原始 DAT 保持不变，不必为转换报错删除文件；
是否需要重新采集，应根据实际参数和数据质量决定。

“普通 64 路（忽略元数据组号）”可用于确认元数据组号残留的普通文件；若 DAT 的
RATE 超过 850 Hz 却没有快速组号，程序仍会拒绝猜测组号。真正的 `0x10` 快速帧也必须
提供组号。外部 DAT 没有 `.meta.json` 时，“自动”无法从帧头推断组 1–4，须手选。

运行无硬件模拟测试：

```bash
python3 -m unittest discover -s mps4264_app/tests -v
```

前端绘图回归测试（如本机安装了 Node.js；运行网页本身不需要 Node.js）：

```bash
node mps4264_app/tests/test_pressure_plot.js
```

覆盖 1～64 路选择、P61～P64 线型、鼠标读数、取消后重新选择，避免线型越界
造成绘制中断、鼠标移动恢复旧缓存图。

测试覆盖 TCP 命令、UDP 采集、普通/快速帧解析与 CSV 转换。尚需在实物上确认固件版本、UDP 配置、长时间无丢帧、相机并行时的磁盘性能。本模块不做校准系数维护、固件升级或硬件级同步，也不替代 ScanTel 的所有功能。

资料：[MPS4264 Gen1 厂家手册](https://scanivalve.com/wp-content/uploads/2024/11/MPS4264_V306.pdf) · [ScanTel v1.08 手册](https://scanivalve.com/wp-content/uploads/2024/11/ScanTel_V108.pdf)
