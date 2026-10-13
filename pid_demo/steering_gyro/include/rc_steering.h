#pragma once
#include <Arduino.h>

// RC 接收机转向通道(CH1)读取:中断测脉宽 -> 映射为目标横摆角速度 r_ref。
// 与现有 esc_control.cpp 的油门读取(D27)同理,只是这里是转向通道、输出 r_ref。
// 单例:整个程序只应创建一个 RCSteering 实例。
class RCSteering {
public:
    void begin(int pin, float maxYawRate = 180.0f, int centerUs = 1500);
    void update();                   // 周期性调用:换算 r_ref 并检测断连(非阻塞)
    float targetYawRate() const { return rRef_; }
    bool  connected() const { return connected_; }
    int   pulseWidthUs() const { return pulseWidthUs_; }

private:
    static RCSteering* s_this;       // 供中断回调定位单例
    static void IRAM_ATTR isr();     // 双边沿中断回调

    int   pin_;
    float maxYawRate_;
    int   centerUs_;
    volatile unsigned long riseUs_ = 0;
    volatile int pulseWidthUs_ = 0;
    volatile unsigned long lastEdgeUs_ = 0;
    bool  connected_ = false;
    float rRef_ = 0.0f;
};
