#include "esc_control.h"
#include <ESP32Servo.h>

// --- 模块私有变量 ---
const int escPin = 14;  // 设定电调连接的 GPIO 引脚
const int rcPin = 27;   // 🚀 新增：遥控器接收机信号线接入 D27
Servo myESC;            // 实例化 Servo 对象

// --- 遥控器信号读取专用变量 (必须加 volatile 防止被编译器优化) ---
volatile unsigned long rcRiseTime = 0;
volatile int rcPulseWidth = 1500;
volatile unsigned long lastRcValidTime = 0;
volatile bool newRcData = false; // 🚀 新增：标记是否收到了【新】的一帧脉冲
int rcActiveCount = 0; // 🚀 新增：连续有效动作计数器

unsigned long lastDriveStatusTime = 0; // 🚀 新增：状态播报计时器

// --- 控制权状态机 ---
enum ControlMode { WEB_MODE, RC_MODE };
ControlMode currentMode = WEB_MODE; // 默认网页控制
int webThrottle = 1500;             // 记录网页下发的油门值

// 🚀 核心硬件中断：精准捕捉 D27 引脚的电平变化，计算 PWM 脉宽
void IRAM_ATTR rcInterrupt() {
    if (digitalRead(rcPin) == HIGH) {
        rcRiseTime = micros(); // 记录上升沿时间
    } else {
        int width = micros() - rcRiseTime; // 下降沿时计算高电平持续时间
        // 过滤掉明显错误的干扰毛刺 (合法遥控器信号通常在 900~2100 us 之间)
        if (width > 900 && width < 2100) {
            rcPulseWidth = width;
            lastRcValidTime = millis(); // 刷新“心跳”时间
            newRcData = true; // 🚀 新增：只有产生了新的有效脉宽，才标记为有新数据

        }
    }
}

// --- 初始化函数 ---
void initESC() {
    // 为 ESP32 分配底层定时器
    ESP32PWM::allocateTimer(0);
    
    // 设置为标准的 50Hz 航模电调/舵机信号频率
    myESC.setPeriodHertz(50);
    
    // 绑定引脚，并限制底层脉宽最小 1000us，最大 2000us
    myESC.attach(escPin, 1000, 2000);
    
    // 启动时强制输出中位信号 (1500us)，让电调安全解锁
    myESC.writeMicroseconds(1500);
    
    // 🚀 初始化遥控器引脚并挂载双边沿中断 (CHANGE)
    pinMode(rcPin, INPUT_PULLDOWN);
    attachInterrupt(digitalPinToInterrupt(rcPin), rcInterrupt, CHANGE);

    Serial.println(">>> [模块加载] 电调(ESC)与遥控接收机(D27)初始化完成");
}

// --- 控制函数 ---
void setESCThrottle(int pwmValue) {
    webThrottle = constrain(pwmValue, 1000, 2000);
    currentMode = WEB_MODE; // 只要网页发来新指令，瞬间抢回控制权
    myESC.writeMicroseconds(webThrottle);
    
    Serial.print(">>> [网页接管] 油门: ");
    Serial.println(webThrottle);
}

// 🚀 新增：电调模块的指令认领中心
void handleESCCommand(char cmd) {
    if (cmd == 'T' || cmd == 't') {
        // 既然确认是 'T'，说明后面的数字是属于我的，我来读取！
        int throttleValue = Serial.parseInt(); 
        setESCThrottle(throttleValue);
    }
}

// 🚀 新增：持续监控与接管逻辑
void updateESC() {
    // 1. 判断接收机是否存活 (如果超过 500ms 没收到脉冲，说明遥控器关机或信号丢失)
    bool isRcActive = (millis() - lastRcValidTime) < 500;

    if (isRcActive) {
        if (newRcData) {
            newRcData = false;

            // 判断这【新的一帧】是不是在死区外
            // 2. 遥控器死区(Deadband)判断：中位通常是1500，手抖或微调会有波动。
            // 如果脉宽越过 1450~1550 这个死区，说明人手确实在推摇杆！
            if (rcPulseWidth < 1450 || rcPulseWidth > 1550) {
                rcActiveCount++; // 每次发现越界，计数器 +1
                if (rcActiveCount > 3) {
                    if (currentMode != RC_MODE) {
                        Serial.println("⚠️ [警告] 检测到持续物理遥控动作，已强制切为【遥控模式】！");
                        currentMode = RC_MODE; 
                    }
                }
            }
            else {
                // 只要有一帧掉回中位，立刻清零！
                // 只要数值掉回 1450~1550 的死区，立刻清零计数器！
                // 这样偶尔一个干扰毛刺根本凑不够 3 次，就会被无视。
                rcActiveCount = 0;
            }
        }
        
        // 3. 如果当前处于遥控模式，底层油门死死咬住遥控器的值
        if (currentMode == RC_MODE) {
            myESC.writeMicroseconds(rcPulseWidth);
        }
        
    } else {
        // 失控保护 (Failsafe)：如果遥控器突然关机
        if (currentMode == RC_MODE) {
            Serial.println("🚨 [失控保护] 遥控器信号丢失！自动切回【网页模式】并刹车！");
            currentMode = WEB_MODE;
            webThrottle = 1500;
            myESC.writeMicroseconds(1500); // 强制归中刹车
        }
        rcActiveCount = 0; // 没信号也要清零
    }

    // 🚀 新增：向 Python 汇报底层状态 (每 100ms 播报一次)
    if (millis() - lastDriveStatusTime > 100) {
        Serial.print("[DRIVE] 模式: ");
        Serial.print(currentMode == RC_MODE ? "RC" : "WEB");
        Serial.print(" | 油门: ");
        Serial.println(currentMode == RC_MODE ? rcPulseWidth : webThrottle);
        lastDriveStatusTime = millis();
    }
}