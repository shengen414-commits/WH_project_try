# D12 转向舵机独立测试

源码为 `src/servo_demo.cpp`，PlatformIO 环境名为 `servo_demo`。
正常小车环境仍为 `esp32dev`，默认构建正常小车固件。
测试固件只编译 demo，不运行编码器、巡线或遥控接管功能。
烧录 demo 会替换 ESP32 当前固件，测试后需要重新烧录 `esp32dev` 恢复小车功能。

## 接线

- 舵机信号线：D12 / GPIO12，不能同时连接接收机 S 输出。
- 舵机正负极：保持原接收机/BEC 供电，ESP32 与接收机、电调、舵机共地。
- 电调信号线：保持当前 GPIO14；demo 持续输出 1500 us 中位，遥控器油门不会接管。
- ESP32 自身继续使用现有电源；舵机不能从 GPIO 或 ESP32 3.3V 引脚取电。

先把车轮架空。启动会将舵机置于 1500 us；该值是常见中位，仍需确认你的机械连杆没有顶死。
GPIO12 是经典 ESP32 的启动配置脚：外部电路在复位时拉高它，可能使使用 3.3V Flash 的模块启动失败。
若烧录/启动异常，先断电并拔掉 D12 信号线，再验证能否启动；必要时把信号改接 GPIO18，
同时将源码的 `STEERING_PIN` 改为 18，不要改写 eFuse 来绕过问题。
依据：[Espressif GPIO12 启动配置说明](https://docs.espressif.com/projects/esp-idf/en/release-v5.4/esp32/api-reference/peripherals/sd_pullup_requirements.html)。

## 构建、上传和操作

在项目根目录执行（把 COM12 换成实际串口；Linux 下例如 `/dev/ttyUSB0`）：

```powershell
pio run -d PIO_RotationmitCamera -e servo_demo
pio run -d PIO_RotationmitCamera -e servo_demo -t upload --upload-port COM12
pio device monitor -p COM12 -b 115200
```

也可用 PlatformIO 的 `servo_demo` → Build / Upload / Monitor。
测试前关闭占用该串口的 Orange Pi 网页后台和其他串口监视器。
发送单个字符即可；带 CR/LF 也支持：

| 指令 | 动作 |
|---|---|
| `C` | 中位 1500 us |
| `L` | 1450 us，小幅向一侧转动 |
| `R` | 1550 us，小幅向另一侧转动 |
| `A` | 单次 1450 → 1500 → 1550 → 1500，每步 800 ms |
| `X` 或 `E` | 取消自动测试并回中位 |
| `?` | 打印指令说明 |

大小写均可，上电不会自动左右扫描。
先发送 C，再逐个尝试 L、C、R、C，确认方向和连杆行程后再发送 A。
L/R 是命令标签，实际左右方向由安装和舵机方向决定。
串口的 `[SERVO]` 只证明程序输出了指令，不能证明舵机实际转动。

初始范围仅 1450–1550 us，舵机死区较大时可能看不出明显动作。
若供电正常且没有机械顶死，可逐步把源码的 `LOW_US` / `HIGH_US` 改成 1400 / 1600，
重新构建上传；不要直接扩到全行程。持续嗡鸣、发热或连杆顶死时停止测试并断电。
完全不动时检查电源、共地、信号插针和 3.3V 信号兼容性。

测试完成后关闭串口监视器，恢复正常固件：

```powershell
pio run -d PIO_RotationmitCamera -e esp32dev -t upload --upload-port COM12
```

当前正常小车固件尚未集成转向控制；本 demo 用于先确认舵机硬件能被 D12 控制。
