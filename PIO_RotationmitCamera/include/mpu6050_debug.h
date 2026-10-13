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
void beginMPU6050Debug();
void endMPU6050Debug();
void updateMPU6050Debug();
void publishMPU6050Debug();
void handleMPU6050DebugCommand(char command);
bool readMPU6050Sample(MPU6050Sample& sample);
