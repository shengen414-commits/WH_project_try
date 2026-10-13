# MPU6050 串口读数测试

测试模块已经接入正常 `esp32dev` 固件，不用烧录替换整个小车功能的独立固件。
按 M 后才初始化 I2C；上电正常模式不会驱动 MPU 的相关 GPIO，也不读取 MPU。
XDA/XCL 不由程序使用；GPIO4/GPIO16 现在用于主 I2C 的 SCL/SDA。

## 当前接线与可选引脚

| MPU6050 引脚 | 当前连接 | 测试程序处理 |
|---|---|---|
| SDA | D16 / GPIO16 | I2C 数据，Wire.begin 第一个参数 |
| SCL | D4 / GPIO4 | I2C 时钟，Wire.begin 第二个参数 |
| AD0 | D17 / GPIO17 | 进入 M 后输出 LOW，选择 0x68 |
| INT | D5 / GPIO5 | 只设为 INPUT，不启用中断，轮询即可 |
| XDA | 不接 | 本次不需要，不连接 SDA/SCL 的 ESP32 引脚 |
| XCL | 不接 | 本次不需要，不连接 SDA/SCL 的 ESP32 引脚 |
| VCC/GND | 按模块供电规格/公共地 | SDA/SCL 的高电平及上拉须符合 ESP32 的 3.3V 接口 |

XDA/XCL 是 MPU 用于连接外部传感器的辅助 I2C 总线，不是读取自身六轴数据必须接的线。
AD0 是地址选择脚：LOW 对应 0x68，HIGH 对应 0x69；WHO_AM_I 在两种地址下都应为 0x68。
依据：[TDK 引脚定义](https://invensense.tdk.com/wp-content/uploads/2015/02/MPU-6000-Datasheet.pdf)、
[寄存器手册](https://invensense.tdk.com/wp-content/uploads/2015/02/MPU-6000-Register-Map1.pdf)。

当前 SDA16/SCL4 软件可用，没有占用编码器、巡线、ESC 或舵机 GPIO。
GPIO4/16 现在只接 SDA/SCL，请先断电移除原来的 XDA/XCL 连接，不能把两个接口并接到这两个 GPIO。
INT 所接 GPIO5 是经典 ESP32 的启动配置脚，本次轮询无需 INT，可以不接。
GPIO16/17 在带 PSRAM 的部分模组上被占用（如 WROVER）；当前接法适用于这两个 GPIO 可用的模组。
[Espressif GPIO 说明](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/peripherals/gpio.html)。

本项目优先推荐 SDA21/SCL22，也可以用 SDA18/SCL19 或 SDA19/SCL23。
它们在当前正常固件中空闲；GPIO18/19/23 原来可能用于 SD，本项目已移除 SD。
两条 I2C 线都要选能输出的 GPIO，不能使用 GPIO34～39。
不要使用 Flash GPIO6～11、串口 GPIO1/3，或本项目已占用的 12/13/14/25/26/27/32/33/34。
GPIO16/17 在部分带 PSRAM 的 ESP32 模组上被占用（如 WROVER），须核对具体模组，不能一概视为空闲。

更简洁的接线是 SDA21、SCL22、VCC、GND，再将 AD0 接 GND，INT/XDA/XCL 都不接。
这样还可释放当前 D4/D5/D16/D17。改线后修改
`include/mpu6050_debug.h` 的 SDA_PIN/SCL_PIN，AD0_PIN 与 INT_PIN 改成 -1，并重新烧录。
只有改代码没有改线，或者只改线没改代码，都无法正常读取。
[Arduino ESP32 I2C 自定义引脚 API](https://docs.espressif.com/projects/arduino-esp32/en/latest/api/i2c.html)。

## 烧录及按键

在项目根目录执行，替换实际串口号。先关闭占用串口的 Orange Pi 后台/其他监视器：

```powershell
pio run -d PIO_RotationmitCamera -e esp32dev -t upload --upload-port COM12
pio device monitor -p COM12 -b 115200 --filter send_on_enter
```

| 按键 | 功能 |
|---|---|
| `M` / `m` | 切入 MPU 读数；再次输入会重新探测/初始化，清除之前的零偏标定 |
| `Z` / `z` | 仅 MPU 模式有效：静止采集 200 次，约 2 秒标定陀螺 Z 零偏 |
| `?` | MPU 模式下显示帮助 |
| `R` / `r` | 退出 MPU 测试，恢复正常编码器和巡线数据上报 |
| `E` / `e` | 保留原最高优先级急停，取消进行中的标定，继续显示读数 |
| `L/G/V/N0/N1` | 切换原功能；离开 MPU 模式后关闭测试 I2C 总线 |

普通字符无需参数，send_on_enter 模式需按回车发送。
M 模式先退出自动转向，电调固定 1500 us；T/B 油门和遥控接管不会驱动电机。
串口过滤 ENC、LINE、DRIVE、FOLLOW 的日常输出，只显示 MPU 测试信息；E 急停确认仍保留。
编码器计数和巡线扫描继续运行，但编码器暂停上报，所以同步记录前必须 R。
退出 M 后恢复正常油门控制，进入前的旧油门/增速不会自动恢复。
模式切换时可能出现一次原自动转向退出确认，后续不混入日常转向日志。

## 读数含义

设备探测成功时显示地址和身份，例如：

```text
[MPU] address=0x68 WHO_AM_I=0x68 (expected 0x68)
[MPU] READY: +/-2g, +/-500deg/s, 100Hz; Z=zero when still; yaw PID is NOT connected
[MPU] a=0.01,-0.02,1.00 w=0.2,-0.1,0.4 T=28.5 z=0.4 cal=NO
```

- a：X/Y/Z 加速度，单位 g，包含重力；静止时三轴合成值应约 1g，水平放置且 Z 朝上时 Z 约 +1。
- w：X/Y/Z 原始角速度，单位 deg/s，未扣除零偏。
- T：芯片内部温度（摄氏度），不是环境温度计。
- z：Z 轴角速度减去标定零偏，未标定时与 w 的第三个值相同。
- cal：NO 未标定、RUN 正在标定、OK 本次成功。

倾斜模块时加速度各轴随重力方向变化；绕 Z 轴转动时 w 第三项和 z 应出现正负变化。
Z 标定只修正陀螺零偏，不会把重力置零，不输出绝对航向角，也不校准加速度。
过程中检测到明显运动、加速度合成值偏离 1g、角速度波动过大或采样拖得太久，
会拒绝标定并保留之前有效零偏。缓慢匀速旋转可能无法仅凭六轴静止检测辨别，
因此仍必须真正保持模块静止。
标定只保存在本次测试的 RAM 中，重新 M 或重启会清除。

## 读取与故障

100kHz I2C，总线超时设置为 3ms；初始化、复位等待和寄存器配置采用分步状态机，
没有 delay()，运行中每约 10ms 读取一个 14 字节数据块，每 200ms 至多打印一次。
I2C 调用仍是有超时的同步事务，并非完全异步。
配置并回读确认 +/-2g、+/-500deg/s、100Hz 和片内低通滤波。
同时关闭辅助 I2C 主机、旁路和 INT 中断，保持本次测试只使用 SDA/SCL。

程序验证 WHO_AM_I=0x68；其他身份不会假定为同一器件并套用 MPU6050 换算系数。
无应答、短读、配置写入失败、回读不符都会报 `[MPU] ERROR=...`，立即标记数据无效，
不会补 0，也不会继续把上次值当成新值。修复接线/供电后输入 M 重试。
测试数据**没有**送入 `submitYawRateFeedback()`，因此不会自动激活陀螺仪舵机 PID。
后续完成轴向、零偏、采样时间和实物验证后，再把真实有效采样接入自动转向反馈接口。
