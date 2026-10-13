#pragma once
#include <Arduino.h>

// 亚博 8 路灰度巡线模块(I2C)。
// 协议:地址 0x12,寄存器 0x30,读 1 字节(bit7=x1 ... bit0=x8,1=压线)。
// 由 8 通道横向重心(加权平均)经 P 控制得到目标横摆角速度 r_ref。
class Grayscale {
public:
    void begin(float kLat, float maxYawRate = 120.0f, uint8_t addr = 0x12);
    void update();                       // 非阻塞状态机,可高频调用(建议与内环同频)
    void setGain(float kLat) { kLat_ = kLat; }
    void setActiveHigh(bool onLineIsOne) { onLineIsOne_ = onLineIsOne; }

    float   targetYawRate() const { return rRef_; }
    float   lateralError() const { return lateralError_; }
    uint8_t raw() const { return raw_; }
    bool    valid() const { return valid_; }

private:
    void compute();                      // 由 raw_ 计算横向偏差与 r_ref

    enum State { IDLE, WAITING };
    State state_ = IDLE;
    unsigned long waitUntilUs_ = 0;

    uint8_t addr_;
    float   kLat_;
    float   maxYawRate_;
    bool    onLineIsOne_ = true;         // true: 1 表示压线;若反了改 false
    uint8_t raw_ = 0;
    bool    valid_ = false;
    float   lateralError_ = 0.0f;
    float   rRef_ = 0.0f;
};
