#pragma once
#include <Arduino.h>

namespace MPU6050Config {
constexpr int SDA_PIN = 16;
constexpr int SCL_PIN = 4;
constexpr int AD0_PIN = 17; // -1 if AD0 is physically tied to GND/3.3V instead.
constexpr int INT_PIN = 5;  // -1 if disconnected; this demo polls, no interrupt needed.
constexpr uint32_t SAMPLE_MS = 10;
constexpr uint32_t PRINT_MS = 200;
}
struct MPU6050Sample {
    uint32_t timeMs;
    float axG, ayG, azG;
    float gxDps, gyDps, gzDps;
    float temperatureC;
    float correctedGzDps;
    bool valid;
    bool calibrated;
};
// Sensor ownership is independent of diagnostic printing and never arms steering.
void beginMPU6050Sensor();
void endMPU6050Sensor();
void updateMPU6050Sensor();
void handleMPU6050SensorCommand(char command);
bool readMPU6050Sample(MPU6050Sample& sample);
struct MPU6050SensorStatus { bool active; bool ready; bool calibrating; uint32_t event; const char* message; };
MPU6050SensorStatus mpu6050SensorStatus();
