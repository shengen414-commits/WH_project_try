#include "mpu6050_debug.h"
#include <cstdio>
namespace {
bool enabled = false, stream3D = false;
uint32_t lastPrintAt = 0, lastEvent = 0;
void output(const char* text) {
    char line[160];
    int n = snprintf(line, sizeof(line), "[MPU] %s\n", text);
    if (n > 0 && n < (int)sizeof(line) && Serial.availableForWrite() >= n)
        Serial.write((const uint8_t*)line, n);
}
void events() {
    auto status = mpu6050SensorStatus();
    if (lastEvent != status.event) { output(status.message); lastEvent = status.event; }
}
void help() { output("M=enter/retry; Z=gyro zero (keep still 2s); R=normal; E=stop; a=g,w=deg/s"); }
}
void beginMPU6050Debug() {
    enabled = true; stream3D = false; lastPrintAt = millis();
    beginMPU6050Sensor(); events();
    help();
}
void endMPU6050Debug() { enabled = stream3D = false; endMPU6050Sensor(); }
void updateMPU6050Debug() { updateMPU6050Sensor(); if (enabled) events(); }
void handleMPU6050DebugCommand(char command) {
    if (!enabled) return;
    if (command >= 'a' && command <= 'z') command -= 'a' - 'A';
    if (command == '?') { help(); return; }
    if (command == 'O') { stream3D = true; output("3D stream 50Hz; Z=gyro zero; R=exit"); }
    else { handleMPU6050SensorCommand(command); events(); }
}
void publishMPU6050Debug() {
    if (!enabled || uint32_t(millis() - lastPrintAt) < (stream3D ? 20u : MPU6050Config::PRINT_MS)) return;
    MPU6050Sample latest;
    if (!readMPU6050Sample(latest)) { output(mpu6050SensorStatus().message); lastPrintAt = millis(); return; }
    bool calibrating = mpu6050SensorStatus().calibrating;
    char line[128];
    int n;
    if (stream3D) n = snprintf(line, sizeof(line), "[IMU],%lu,%.4f,%.4f,%.4f,%.3f,%.3f,%.3f,%.2f,%.3f,%s\n",
                     (unsigned long)latest.timeMs, latest.axG, latest.ayG, latest.azG,
                     latest.gxDps, latest.gyDps, latest.gzDps, latest.temperatureC, latest.correctedGzDps,
                     calibrating ? "RUN" : latest.calibrated ? "OK" : "NO");
    else n = snprintf(line, sizeof(line), "[MPU] a=%.2f,%.2f,%.2f w=%.1f,%.1f,%.1f T=%.1f z=%.1f cal=%s\n",
                     latest.axG, latest.ayG, latest.azG, latest.gxDps, latest.gyDps, latest.gzDps,
                     latest.temperatureC, latest.correctedGzDps, calibrating ? "RUN" : latest.calibrated ? "OK" : "NO");
    if (n > 0 && n < (int)sizeof(line) && Serial.availableForWrite() >= n) {
        Serial.write((const uint8_t*)line, n); lastPrintAt = millis();
    }
}
