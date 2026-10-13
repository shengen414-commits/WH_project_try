#include "pid.h"
#include <Arduino.h>

void PID::begin(float kp, float ki, float kd, float outMin, float outMax) {
    kp_ = kp; ki_ = ki; kd_ = kd;
    outMin_ = outMin; outMax_ = outMax;
    reset();
}

void PID::setTunings(float kp, float ki, float kd) {
    kp_ = kp; ki_ = ki; kd_ = kd;
}

void PID::setOutputLimits(float outMin, float outMax) {
    outMin_ = outMin; outMax_ = outMax;
    if (integral_ > outMax_) integral_ = outMax_;
    if (integral_ < outMin_) integral_ = outMin_;
}

void PID::reset() {
    integral_ = 0;
    prevError_ = 0;
    first_ = true;
}

float PID::compute(float setpoint, float measurement) {
    unsigned long nowUs = micros();
    float error = setpoint - measurement;

    float p = kp_ * error;

    float dt = 0.0f;
    if (first_) {
        first_ = false;               // 首次调用无历史,跳过 I/D 项
    } else {
        dt = (float)(nowUs - prevTimeUs_) * 1e-6f;
        if (dt <= 0.0f) dt = 1e-6f;   // 防止除零
        if (dt > 0.1f) dt = 0.1f;     // 卡顿/断流保护,避免 dt 突变放大微分
    }
    prevTimeUs_ = nowUs;

    float iTerm = 0.0f;
    float dTerm = 0.0f;
    if (dt > 0.0f) {
        integral_ += ki_ * error * dt;
        if (integral_ > outMax_) integral_ = outMax_;     // 抗积分饱和(clamp)
        else if (integral_ < outMin_) integral_ = outMin_;
        iTerm = integral_;

        dTerm = kd_ * (error - prevError_) / dt;
    }
    prevError_ = error;

    float out = p + iTerm + dTerm;
    if (out > outMax_) out = outMax_;
    else if (out < outMin_) out = outMin_;
    return out;
}
