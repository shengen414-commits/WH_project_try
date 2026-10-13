#include "rc_steering.h"

RCSteering* RCSteering::s_this = nullptr;

void IRAM_ATTR RCSteering::isr() {
    RCSteering* self = s_this;
    if (!self) return;
    if (digitalRead(self->pin_) == HIGH) {
        self->riseUs_ = micros();
    } else {
        long w = (long)(micros() - self->riseUs_);
        if (w > 900 && w < 2100) {          // 滤除毛刺,合法脉宽 900~2100us
            self->pulseWidthUs_ = (int)w;
            self->lastEdgeUs_ = micros();
        }
    }
}

void RCSteering::begin(int pin, float maxYawRate, int centerUs) {
    pin_ = pin;
    maxYawRate_ = maxYawRate;
    centerUs_ = centerUs;
    s_this = this;
    pinMode(pin_, INPUT_PULLDOWN);
    attachInterrupt(digitalPinToInterrupt(pin_), isr, CHANGE);
}

void RCSteering::update() {
    unsigned long now = micros();
    // 超过 100ms 无有效脉冲 -> 判为断连,回中(安全,防止失控)
    connected_ = (now - lastEdgeUs_) < 100000UL && pulseWidthUs_ != 0;
    if (!connected_) { rRef_ = 0.0f; return; }

    float dev = (float)(pulseWidthUs_ - centerUs_);
    rRef_ = dev * (maxYawRate_ / 500.0f);   // 满舵 ±500us -> ±maxYawRate
}
