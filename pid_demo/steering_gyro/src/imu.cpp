#include "imu.h"
#include <Wire.h>

#define MPU6050_ADDR     0x68
#define REG_PWR_MGMT_1   0x6B
#define REG_GYRO_CONFIG  0x1B
#define REG_ACCEL_CONFIG 0x1C
#define REG_GYRO_ZOUT_H  0x47

bool MPU6050Gyro::begin() {
    Wire.begin();                       // ESP32 默认 SDA=21, SCL=22
    Wire.beginTransmission(MPU6050_ADDR);
    if (Wire.endTransmission() != 0) return false;

    writeReg(REG_PWR_MGMT_1, 0x00);     // 清除 SLEEP 位,唤醒
    delay(50);
    writeReg(REG_GYRO_CONFIG, 0x08);    // FS_SEL=1 => ±500dps, 65.5 LSB/(deg/s)
    writeReg(REG_ACCEL_CONFIG, 0x00);   // ±2g(本模块未用加速度,仅顺手配置)
    return true;
}

void MPU6050Gyro::calibrate(int samples) {
    float sum = 0.0f;
    for (int i = 0; i < samples; i++) {
        sum += readReg16(REG_GYRO_ZOUT_H) / gyroScale_;
        delay(2);
    }
    biasZ_ = sum / samples;
}

void MPU6050Gyro::update() {
    int16_t rawZ = readReg16(REG_GYRO_ZOUT_H);
    rawYaw_ = rawZ / gyroScale_ - biasZ_;
    yawRate_ = alpha_ * rawYaw_ + (1.0f - alpha_) * yawRate_;   // 一阶低通
}

void MPU6050Gyro::writeReg(uint8_t reg, uint8_t val) {
    Wire.beginTransmission(MPU6050_ADDR);
    Wire.write(reg);
    Wire.write(val);
    Wire.endTransmission();
}

int16_t MPU6050Gyro::readReg16(uint8_t reg) {
    Wire.beginTransmission(MPU6050_ADDR);
    Wire.write(reg);
    Wire.endTransmission(false);        // 无停止位(重复起始),准备读
    Wire.requestFrom((uint8_t)MPU6050_ADDR, (uint8_t)2);
    if (Wire.available() < 2) return 0;
    uint8_t hi = Wire.read();
    uint8_t lo = Wire.read();
    return (int16_t)((hi << 8) | lo);
}
