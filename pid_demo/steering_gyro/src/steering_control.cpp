#include "steering_control.h"
#include <ESP32Servo.h>

void SteeringControl::begin(int servoPin, float kp, float ki, float kd, float kg,
                            int pwmMin, int pwmCenter, int pwmMax) {
    servoPin_ = servoPin;
    pwmMin_ = pwmMin;
    pwmCenter_ = pwmCenter;
    pwmMax_ = pwmMax;
    kg_ = kg;

    ESP32PWM::allocateTimer(0);          // 为舵机预留硬件定时器
    servo_ = new Servo();
    servo_->setPeriodHertz(50);          // 标准 50Hz 舵机信号(DS3120 亦可)
    servo_->attach(servoPin_, pwmMin_, pwmMax_);
    servo_->writeMicroseconds(pwmCenter_);

    // PID 输出是"相对中位的偏差"(us),限幅 [min-center, max-center]
    pid_.begin(kp, ki, kd, pwmMin_ - pwmCenter_, pwmMax_ - pwmCenter_);
}

void SteeringControl::setTargetYawRate(float rRefDegPerSec) {
    rRef_ = rRefDegPerSec;
}

void SteeringControl::setGyroGain(float kg) {
    kg_ = kg;
}

void SteeringControl::setTunings(float kp, float ki, float kd) {
    pid_.setTunings(kp, ki, kd);
}

void SteeringControl::update(float yawRateDegPerSec) {
    // error = r_ref - Kg * yaw_rate:让实测横摆角速度跟踪目标值。
    // r_ref=0 时,PID 会强制 yaw_rate -> 0,即"外力扰动下自动回正走直线"。
    float measurement = kg_ * yawRateDegPerSec;
    float out = pid_.compute(rRef_, measurement);

    int pwm = pwmCenter_ + (int)out;
    if (pwm > pwmMax_) pwm = pwmMax_;
    if (pwm < pwmMin_) pwm = pwmMin_;

    servo_->writeMicroseconds(pwm);
    pidOutput_ = out;
    servoPwm_ = pwm;
}

void SteeringControl::disable() {
    servo_->writeMicroseconds(pwmCenter_);
    pid_.reset();
    rRef_ = 0.0f;
    pidOutput_ = 0.0f;
    servoPwm_ = pwmCenter_;
}
