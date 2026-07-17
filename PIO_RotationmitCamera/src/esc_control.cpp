#include "esc_control.h"
#include <ESP32Servo.h>

// --- 模块私有变量 ---
const int escPin = 14; // 设定电调连接的 GPIO 引脚
const int rcPin = 27;  // 🚀 新增：遥控器接收机信号线接入 D27
Servo myESC;           // 实例化 Servo 对象

// --- 遥控器信号读取专用变量 (必须加 volatile 防止被编译器优化) ---
volatile unsigned long rcRiseTime = 0;
volatile int rcPulseWidth = 1500;
volatile unsigned long lastRcValidTime = 0;
volatile bool newRcData = false; // 🚀 新增：标记是否收到了【新】的一帧脉冲
int rcActiveCount = 0;           // 🚀 新增：连续有效动作计数器

unsigned long lastDriveStatusTime = 0; // 🚀 新增：状态播报计时器
const unsigned long WEB_COMMAND_TIMEOUT_MS = 2500;
const unsigned long ESTOP_LATCH_MS = 1000;
const int ESC_NEUTRAL_PWM = 1500;
const int ESC_DIRECTION_DEADBAND_US = 50;
const unsigned long ESC_DIRECTION_CHANGE_NEUTRAL_MS = 200;
const float ESC_BOOST_MULTIPLIER = 1.5f;     // 启动段增速倍率(pwm-1500)*rate+-1500
const unsigned long ESC_BOOST_RAMP_MS = 250; // 启动段增速渐升时间
const unsigned long ESC_BOOST_HOLD_MS = 800; // 启动段增速维持时间
const int ESC_BOOST_FORWARD_LIMIT = 1900;
const int ESC_BOOST_REVERSE_LIMIT = 1100; // 增速段PWM上下限

// --- 控制权状态机 ---
enum ControlMode
{
    WEB_MODE,
    RC_MODE
};
ControlMode currentMode = WEB_MODE; // 默认网页控制
int webThrottle = 1500;             // 记录网页下发的油门值
unsigned long lastWebCommandTime = 0;
unsigned long estopLatchedUntil = 0;
int appliedEscDirection = 0;
bool directionChangePending = false;
int pendingEscPwm = ESC_NEUTRAL_PWM;
unsigned long directionChangeReleaseAt = 0;
bool escBoostActive = false;
int escBoostBasePwm = ESC_NEUTRAL_PWM;
int escBoostPeakPwm = ESC_NEUTRAL_PWM;
unsigned long escBoostStartedAt = 0;

// 🚀 核心硬件中断：精准捕捉 D27 引脚的电平变化，计算 PWM 脉宽
int getEscDirection(int pwmValue)
{
    if (pwmValue > ESC_NEUTRAL_PWM + ESC_DIRECTION_DEADBAND_US)
    {
        return 1;
    }
    if (pwmValue < ESC_NEUTRAL_PWM - ESC_DIRECTION_DEADBAND_US)
    {
        return -1;
    }
    return 0;
}

void forceEscNeutralOutput()
{
    escBoostActive = false;
    directionChangePending = false;
    pendingEscPwm = ESC_NEUTRAL_PWM;
    appliedEscDirection = 0;
    myESC.writeMicroseconds(ESC_NEUTRAL_PWM);
}

void cancelESCBoost()
{
    escBoostActive = false;
    escBoostBasePwm = ESC_NEUTRAL_PWM;
    escBoostPeakPwm = ESC_NEUTRAL_PWM;
    escBoostStartedAt = 0;
}

void requestEscOutput(int pwmValue)
{
    int targetPwm = constrain(pwmValue, 1000, 2000);
    int targetDirection = getEscDirection(targetPwm);
    unsigned long now = millis();

    if (directionChangePending)
    {
        pendingEscPwm = targetPwm;
        if (targetDirection == 0)
        {
            forceEscNeutralOutput();
            return;
        }
        if (now < directionChangeReleaseAt)
        {
            myESC.writeMicroseconds(ESC_NEUTRAL_PWM);
            return;
        }
        directionChangePending = false;
    }

    if (targetDirection != 0 && appliedEscDirection != 0 && targetDirection != appliedEscDirection)
    {
        directionChangePending = true;
        pendingEscPwm = targetPwm;
        directionChangeReleaseAt = now + ESC_DIRECTION_CHANGE_NEUTRAL_MS;
        appliedEscDirection = 0;
        myESC.writeMicroseconds(ESC_NEUTRAL_PWM);
        return;
    }

    myESC.writeMicroseconds(targetPwm);
    appliedEscDirection = targetDirection;
}

void IRAM_ATTR rcInterrupt()
{
    if (digitalRead(rcPin) == HIGH)
    {
        rcRiseTime = micros(); // 记录上升沿时间
    }
    else
    {
        int width = micros() - rcRiseTime; // 下降沿时计算高电平持续时间
        // 过滤掉明显错误的干扰毛刺 (合法遥控器信号通常在 900~2100 us 之间)
        if (width > 900 && width < 2100)
        {
            rcPulseWidth = width;
            lastRcValidTime = millis(); // 刷新“心跳”时间
            newRcData = true;           // 🚀 新增：只有产生了新的有效脉宽，才标记为有新数据
        }
    }
}

void handleESCCommand(char cmd)
{
    // 🚀 新增：最高优先级急停指令
    if (cmd == 'E' || cmd == 'e')
    {
        // 1. 第一时间切断动力，物理电调归中
        webThrottle = 1500;
        currentMode = WEB_MODE;
        estopLatchedUntil = millis() + ESTOP_LATCH_MS;
        lastWebCommandTime = 0;
        forceEscNeutralOutput();

        // 2. 核心魔法：清空单片机硬件串口缓冲区里的所有积压旧指令！
        // 就像把下水道彻底疏通，那些还没执行的 T1800, T1900 全部被丢弃。
        while (Serial.available() > 0)
        {
            Serial.read();
        }

        Serial.println("\n[E-STOP] 🚨 收到急停指令，动力已切断，接收队列已清空！");
    }
    // 普通油门指令保持不变
    else if (cmd == 'T' || cmd == 't')
    {
        int throttleValue = Serial.parseInt();
        if (millis() < estopLatchedUntil)
        {
            Serial.println("[E-STOP] 忽略急停保护窗口内的普通油门指令");
            return;
        }
        setESCThrottle(throttleValue);
    }
    else if (cmd == 'B' || cmd == 'b')
    {
        int throttleValue = Serial.parseInt();
        if (millis() < estopLatchedUntil)
        {
            Serial.println("[E-STOP] Ignored boost command during protection window");
            return;
        }
        startESCBoost(throttleValue);
    }
}

// --- 初始化函数 ---
void initESC()
{
    Serial.setTimeout(20);

    // 为 ESP32 分配底层定时器
    ESP32PWM::allocateTimer(0);

    // 设置为标准的 50Hz 航模电调/舵机信号频率
    myESC.setPeriodHertz(50);

    // 绑定引脚，并限制底层脉宽最小 1000us，最大 2000us
    myESC.attach(escPin, 1000, 2000);

    // 启动时强制输出中位信号 (1500us)，让电调安全解锁
    forceEscNeutralOutput();

    // 🚀 初始化遥控器引脚并挂载双边沿中断 (CHANGE)
    pinMode(rcPin, INPUT_PULLDOWN);
    attachInterrupt(digitalPinToInterrupt(rcPin), rcInterrupt, CHANGE);

    Serial.println(">>> [模块加载] 电调(ESC)与遥控接收机(D27)初始化完成");
}

// --- 控制函数 ---
void setESCThrottle(int pwmValue)
{
    int requestedPwm = constrain(pwmValue, 1000, 2000);
    bool keepCurrentBoost = escBoostActive && requestedPwm == escBoostBasePwm;
    webThrottle = requestedPwm;
    lastWebCommandTime = millis();
    currentMode = WEB_MODE; // 只要网页发来新指令，瞬间抢回控制权
    if (keepCurrentBoost)
    {
        return;
    }
    cancelESCBoost();
    requestEscOutput(webThrottle);

    Serial.print(">>> [网页接管] 油门: ");
    Serial.println(webThrottle);
}

void startESCBoost(int pwmValue)
{
    webThrottle = constrain(pwmValue, 1000, 2000);
    lastWebCommandTime = millis();
    currentMode = WEB_MODE;

    int delta = webThrottle - ESC_NEUTRAL_PWM;
    if (abs(delta) <= ESC_DIRECTION_DEADBAND_US)
    {
        cancelESCBoost();
        requestEscOutput(webThrottle);
        return;
    }

    int boostedDelta = (int)roundf(delta * ESC_BOOST_MULTIPLIER);
    escBoostBasePwm = webThrottle;
    escBoostPeakPwm = ESC_NEUTRAL_PWM + boostedDelta;
    if (delta > 0)
    {
        escBoostPeakPwm = min(escBoostPeakPwm, ESC_BOOST_FORWARD_LIMIT);
    }
    else
    {
        escBoostPeakPwm = max(escBoostPeakPwm, ESC_BOOST_REVERSE_LIMIT);
    }
    escBoostStartedAt = millis();
    escBoostActive = escBoostPeakPwm != escBoostBasePwm;
    requestEscOutput(escBoostBasePwm);

    Serial.print(">>> [NUMERIC BOOST] base: ");
    Serial.print(escBoostBasePwm);
    Serial.print(" | peak: ");
    Serial.println(escBoostPeakPwm);
}

void updateESCBoost()
{
    if (!escBoostActive || currentMode != WEB_MODE)
    {
        return;
    }

    unsigned long elapsed = millis() - escBoostStartedAt;
    if (elapsed < ESC_BOOST_RAMP_MS)
    {
        long peakOffset = escBoostPeakPwm - escBoostBasePwm;
        int rampPwm = escBoostBasePwm + (int)(peakOffset * elapsed / ESC_BOOST_RAMP_MS);
        requestEscOutput(rampPwm);
        return;
    }

    if (elapsed < ESC_BOOST_RAMP_MS + ESC_BOOST_HOLD_MS)
    {
        requestEscOutput(escBoostPeakPwm);
        return;
    }

    escBoostActive = false;
    requestEscOutput(escBoostBasePwm);
    Serial.println(">>> [NUMERIC BOOST] complete, restored base PWM");
}

// 🚀 新增：持续监控与接管逻辑
void updateESC()
{
    // 1. 判断接收机是否存活 (如果超过 500ms 没收到脉冲，说明遥控器关机或信号丢失)
    bool isRcActive = (millis() - lastRcValidTime) < 500;

    if (millis() < estopLatchedUntil)
    {
        forceEscNeutralOutput();
        rcActiveCount = 0;

        if (millis() - lastDriveStatusTime > 100)
        {
            Serial.println("[DRIVE] 模式: WEB | 油门: 1500");
            lastDriveStatusTime = millis();
        }
        return;
    }

    if (isRcActive)
    {
        if (newRcData)
        {
            newRcData = false;

            // 判断这【新的一帧】是不是在死区外
            // 2. 遥控器死区(Deadband)判断：中位通常是1500，手抖或微调会有波动。
            // 如果脉宽越过 1450~1550 这个死区，说明人手确实在推摇杆！
            if (rcPulseWidth < 1450 || rcPulseWidth > 1550)
            {
                rcActiveCount++; // 每次发现越界，计数器 +1
                if (rcActiveCount > 3)
                {
                    if (currentMode != RC_MODE)
                    {
                        Serial.println("⚠️ [警告] 检测到持续物理遥控动作，已强制切为【遥控模式】！");
                        currentMode = RC_MODE;
                        cancelESCBoost();
                    }
                }
            }
            else
            {
                // 只要有一帧掉回中位，立刻清零！
                // 只要数值掉回 1450~1550 的死区，立刻清零计数器！
                // 这样偶尔一个干扰毛刺根本凑不够 3 次，就会被无视。
                rcActiveCount = 0;
            }
        }

        // 3. 如果当前处于遥控模式，底层油门死死咬住遥控器的值
        if (currentMode == RC_MODE)
        {
            requestEscOutput(rcPulseWidth);
        }
    }
    else
    {
        // 失控保护 (Failsafe)：如果遥控器突然关机
        if (currentMode == RC_MODE)
        {
            Serial.println("🚨 [失控保护] 遥控器信号丢失！自动切回【网页模式】并刹车！");
            currentMode = WEB_MODE;
            webThrottle = 1500;
            forceEscNeutralOutput(); // 强制归中刹车
        }
        rcActiveCount = 0; // 没信号也要清零
    }

    if (currentMode == WEB_MODE && escBoostActive)
    {
        updateESCBoost();
    }
    else if (currentMode == WEB_MODE && directionChangePending)
    {
        requestEscOutput(webThrottle);
    }

    if (currentMode == WEB_MODE && webThrottle != 1500 && lastWebCommandTime > 0 &&
        millis() - lastWebCommandTime > WEB_COMMAND_TIMEOUT_MS)
    {
        Serial.println("🚨 [网页失联保护] 超过 2500ms 未收到网页油门心跳，自动归中！");
        webThrottle = 1500;
        forceEscNeutralOutput();
        lastWebCommandTime = 0;
    }

    // 🚀 新增：向 Python 汇报底层状态 (每 100ms 播报一次)
    if (millis() - lastDriveStatusTime > 100)
    {
        Serial.print("[DRIVE] 模式: ");
        Serial.print(currentMode == RC_MODE ? "RC" : "WEB");
        Serial.print(" | 油门: ");
        Serial.println(currentMode == RC_MODE ? rcPulseWidth : webThrottle);
        lastDriveStatusTime = millis();
    }
}
