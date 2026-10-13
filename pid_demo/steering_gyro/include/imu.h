#pragma once
#include <Arduino.h>

// 极简 MPU6050 驱动:只读内环需要的陀螺仪 Z 轴(横摆角速度)。
// 单位统一为度每秒(deg/s)。
class MPU6050Gyro {
public:
    bool begin();                       // 初始化 I2C + 唤醒 + 量程配置;找不到返回 false
    void calibrate(int samples = 500);  // 静止采集 Z 轴零偏(标定时保持小车不动)
    void update();                      // 读原始值 -> 去零偏 -> 一阶低通
    float yawRate() const { return yawRate_; }     // 滤波后横摆角速度(deg/s)
    float rawYawRate() const { return rawYaw_; }   // 仅去零偏,未滤波(deg/s)
    void  setFilterAlpha(float a) { alpha_ = a; }  // 低通系数(0~1,越小越平滑)

private:
    void writeReg(uint8_t reg, uint8_t val);
    int16_t readReg16(uint8_t reg);

    const float gyroScale_ = 65.5f;  // ±500dps 时 LSB/(deg/s)
    float biasZ_ = 0.0f;
    float rawYaw_ = 0.0f;
    float yawRate_ = 0.0f;
    float alpha_ = 0.15f;            // 一阶低通系数
};
