#include "grayscale.h"
#include <Wire.h>

#define GRAY_REG      0x30
#define GRAY_WAIT_US  10000     // 官方例程 delay(10) 对应的等待时间

void Grayscale::begin(float kLat, float maxYawRate, uint8_t addr) {
    kLat_ = kLat;
    maxYawRate_ = maxYawRate;
    addr_ = addr;
    Wire.begin();
}

// 非阻塞读取:写寄存器 -> 等 10ms -> 读数据,期间不占用 CPU 阻塞内环。
void Grayscale::update() {
    unsigned long now = micros();
    switch (state_) {
        case IDLE: {
            Wire.beginTransmission(addr_);
            Wire.write(GRAY_REG);
            if (Wire.endTransmission() != 0) return;   // 无应答,保持原值,下轮重试
            waitUntilUs_ = now + GRAY_WAIT_US;
            state_ = WAITING;
            break;
        }
        case WAITING: {
            if (now < waitUntilUs_) return;            // 未到时间,非阻塞返回
            Wire.requestFrom((uint8_t)addr_, (uint8_t)1);
            if (Wire.available() < 1) { state_ = IDLE; return; }
            raw_ = Wire.read();
            state_ = IDLE;
            compute();
            break;
        }
    }
}

void Grayscale::compute() {
    float sumW = 0.0f, sumN = 0.0f;
    for (int i = 0; i < 8; i++) {
        bool bit = (raw_ >> (7 - i)) & 0x01;   // x1=bit7 ... x8=bit0
        bool active = onLineIsOne_ ? bit : !bit;
        if (active) {
            sumW += (float)i - 3.5f;           // x1=-3.5 ... x8=+3.5(中心为 0)
            sumN += 1.0f;
        }
    }
    lateralError_ = (sumN > 0.0f) ? (sumW / sumN) : 0.0f;   // 丢线 -> 0(保持直线)
    valid_ = true;

    float r = kLat_ * lateralError_;           // P 控制(横向偏差 -> 横摆角速度)
    rRef_ = constrain(r, -maxYawRate_, maxYawRate_);
}
