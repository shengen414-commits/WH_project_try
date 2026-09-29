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

## 供其他程序导入

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

运行无硬件模拟测试：

```bash
python3 -m unittest discover -s mps4264_app/tests -v
```

测试覆盖 TCP 命令、UDP 采集、普通/快速帧解析与 CSV 转换。尚需在实物上确认固件版本、UDP 配置、长时间无丢帧、相机并行时的磁盘性能。本模块不做校准系数维护、固件升级或硬件级同步，也不替代 ScanTel 的所有功能。

资料：[MPS4264 Gen1 厂家手册](https://scanivalve.com/wp-content/uploads/2024/11/MPS4264_V306.pdf) · [ScanTel v1.08 手册](https://scanivalve.com/wp-content/uploads/2024/11/ScanTel_V108.pdf)
