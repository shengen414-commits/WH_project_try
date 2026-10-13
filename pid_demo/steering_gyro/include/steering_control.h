#pragma once
#include <Arduino.h>
#include "pid.h"

class Servo;  // 前置声明,实现在 ESP32Servo.h 中

// 转向内环:横摆角速度反馈 PID -> 舵机 PWM。
// 核心公式: error = r_ref - Kg * yaw_rate,输出叠加到舵机中位 1500us 上。
class SteeringControl {
public:
    void begin(int servoPin, float kp, float ki, float kd, float kg,
               int pwmMin = 1000, int pwmCenter = 1500, int pwmMax = 2000);

    void setTargetYawRate(float rRefDegPerSec);   // 目标横摆角速度(外环/遥控给定)
    void setGyroGain(float kg);                   // 陀螺增益(正负可反方向)
    void setTunings(float kp, float ki, float kd);

    // 按控制频率(如 200Hz)调用。yawRateDegPerSec 为去偏+低通后的横摆角速度。
    void update(float yawRateDegPerSec);
    void disable();                               // 回中并清零 PID

    float targetYawRate() const { return rRef_; }
    float pidOutput() const { return pidOutput_; }
    int   servoPwm() const { return servoPwm_; }

private:
    Servo* servo_ = nullptr;
    PID    pid_;
    int    servoPin_ = 0;
    int    pwmMin_ = 1000, pwmCenter_ = 1500, pwmMax_ = 2000;
    float  kg_ = 1.0f;
    float  rRef_ = 0.0f;
    float  pidOutput_ = 0.0f;
    int    servoPwm_ = 1500;
};
