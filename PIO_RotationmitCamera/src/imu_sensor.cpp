#include "imu_sensor.h"
#include "line_sensor.h"
#include "steering_control.h"
#include <Wire.h>
#include <driver/gpio.h>
#include <cmath>
#include <cstdio>

namespace {
enum class Stage { Off, Probe, ResetWait, Configure, Verify, Warmup, Running, Failed };
Stage stage = Stage::Off;
bool sensorEnabled = false, busOpen = false;
char eventMessage[128] = "not_initialized";
uint32_t eventSequence = 0;
bool wrongIdentitySeen = false;
uint8_t address = 0, probe = 0, configIndex = 0, verifyIndex = 0;
uint32_t stageAt = 0, lastSampleAt = 0, errors = 0;
MPU6050Sample latest = {};
constexpr uint8_t addresses[] = {0x68, 0x69};
constexpr uint8_t config[][2] = {
    {0x6b, 0x01}, // wake, X gyro PLL clock
    {0x19, 9},    // 1 kHz / (9+1) = 100 Hz with DLPF enabled
    {0x1a, 4},    // gyro 20 Hz / accelerometer 21 Hz; filter before 50 Hz telemetry
    {0x1b, 0x08}, // gyro +/-500 deg/s, 65.5 LSB/(deg/s)
    {0x1c, 0x00}, // accelerometer +/-2g, 16384 LSB/g
    {0x6a, 0x00}, // auxiliary I2C master disabled
    {0x38, 0x00}, // interrupts disabled; use polling
    {0x37, 0x00}, // no auxiliary bus bypass
};
bool calibrating = false;
unsigned int calibrationCount = 0;
float calibrationMean = 0, calibrationM2 = 0, gyroBiasZ = 0;
uint32_t calibrationAt = 0;
constexpr unsigned int CALIBRATION_SAMPLES = 200;

void output(const char* text) {
    snprintf(eventMessage, sizeof(eventMessage), "%s", text); ++eventSequence;
}
bool pinsValid() {
    const int pins[] = {MPU6050Config::SDA_PIN, MPU6050Config::SCL_PIN, MPU6050Config::AD0_PIN, MPU6050Config::INT_PIN};
    for (unsigned int i = 0; i < 4; ++i) {
        if (i >= 2 && pins[i] == -1) continue;
        if (!GPIO_IS_VALID_GPIO(pins[i]) || (i < 3 && !GPIO_IS_VALID_OUTPUT_GPIO(pins[i]))) return false;
        if (pins[i] == 1 || pins[i] == 3 || (pins[i] >= 6 && pins[i] <= 11) ||
            pins[i] == 25 || pins[i] == 26 || pins[i] == 14 || pins[i] == 27 || pins[i] == SteeringConfig::PIN ||
            pins[i] == LineSensorConfig::AD0_PIN || pins[i] == LineSensorConfig::AD1_PIN ||
            pins[i] == LineSensorConfig::AD2_PIN || pins[i] == LineSensorConfig::OUT_PIN) return false;
        for (unsigned int j = 0; j < i; ++j) if (pins[i] == pins[j]) return false;
    }
    return true;
}
void fail(const char* why) {
    latest.valid = false; calibrating = false;
    stage = Stage::Failed; ++errors;
    char text[112]; snprintf(text, sizeof(text), "ERROR=%s count=%lu; M=retry R=exit", why, (unsigned long)errors); output(text);
}
bool writeRegister(uint8_t reg, uint8_t value) {
    Wire.beginTransmission(address);
    Wire.write(reg); Wire.write(value);
    return Wire.endTransmission(true) == 0;
}
bool readRegisters(uint8_t reg, uint8_t* data, uint8_t length) {
    Wire.beginTransmission(address); Wire.write(reg);
    if (Wire.endTransmission(false) != 0) return false;
    if (Wire.requestFrom(address, length, (uint8_t)true) != length || Wire.available() < length) {
        while (Wire.available()) Wire.read();
        return false;
    }
    for (uint8_t i = 0; i < length; ++i) data[i] = Wire.read();
    return true;
}
int16_t signedWord(const uint8_t* data) {
    return (int16_t)((uint16_t(data[0]) << 8) | uint16_t(data[1]));
}
void calibrateSample() {
    if (!calibrating) return;
    const float acceleration = std::sqrt(latest.axG * latest.axG + latest.ayG * latest.ayG + latest.azG * latest.azG);
    const float gyro = std::sqrt(latest.gxDps * latest.gxDps + latest.gyDps * latest.gyDps + latest.gzDps * latest.gzDps);
    if (acceleration < 0.85f || acceleration > 1.15f || gyro > 3 ||
        uint32_t(millis() - calibrationAt) > 4000) {
        calibrating = false; output("ZERO rejected: motion/gap detected; previous bias kept"); return;
    }
    ++calibrationCount;
    float delta = latest.gzDps - calibrationMean;
    calibrationMean += delta / calibrationCount;
    calibrationM2 += delta * (latest.gzDps - calibrationMean);
    if (calibrationCount >= CALIBRATION_SAMPLES) {
        calibrating = false;
        if (calibrationM2 / (calibrationCount - 1) > 0.25f) {
            output("ZERO rejected: gyro unstable; previous bias kept"); return;
        }
        gyroBiasZ = calibrationMean; latest.calibrated = true;
        latest.correctedGzDps = latest.gzDps - gyroBiasZ;
        output("ZERO complete: gyro Z bias saved in RAM only");
    }
}
}

void beginMPU6050Sensor() {
    endMPU6050Sensor(); sensorEnabled = true;
    latest = {}; gyroBiasZ = 0; errors = 0; probe = 0; configIndex = verifyIndex = 0; wrongIdentitySeen = false;
    if (!pinsValid()) { fail("invalid_or_conflicting_pins"); return; }
    if (MPU6050Config::AD0_PIN >= 0) {
        // Set output latch before changing direction; select address 0x68.
        digitalWrite(MPU6050Config::AD0_PIN, LOW); pinMode(MPU6050Config::AD0_PIN, OUTPUT);
    }
    if (MPU6050Config::INT_PIN >= 0) pinMode(MPU6050Config::INT_PIN, INPUT);
    if (!Wire.begin(MPU6050Config::SDA_PIN, MPU6050Config::SCL_PIN, 100000)) { fail("i2c_begin_failed"); return; }
    busOpen = true; Wire.setTimeOut(3);
    stage = Stage::Probe; stageAt = millis();
    char text[112];
    snprintf(text, sizeof(text), "init SDA=%d SCL=%d AD0=%d INT=%d; XDA/XCL unused",
             MPU6050Config::SDA_PIN, MPU6050Config::SCL_PIN, MPU6050Config::AD0_PIN, MPU6050Config::INT_PIN);
    output(text);
}
void endMPU6050Sensor() {
    sensorEnabled = false; calibrating = false; latest.valid = false; stage = Stage::Off;
    if (busOpen) Wire.end();
    busOpen = false;
}
bool readMPU6050Sample(MPU6050Sample& sample) {
    if (stage != Stage::Running || !latest.valid || uint32_t(millis() - latest.timeMs) > 100) return false;
    sample = latest; return true;
}
void handleMPU6050SensorCommand(char command) {
    if (!sensorEnabled) return;
    if (command >= 'a' && command <= 'z') command -= 'a' - 'A';
    if (command == 'E') { calibrating = false; output("ZERO canceled; ESC remains neutral"); }
    if (command != 'Z') return;
    MPU6050Sample sample;
    if (!readMPU6050Sample(sample)) { output("ZERO unavailable: wait for valid MPU readings"); return; }
    calibrating = true; calibrationCount = 0; calibrationMean = calibrationM2 = 0; calibrationAt = millis();
    output("ZERO started: keep car STILL for 2 seconds; raw w readings remain unchanged");
}
void updateMPU6050Sensor() {
    if (!sensorEnabled) return;
    switch (stage) {
    case Stage::Probe: {
        address = addresses[probe]; uint8_t id = 0;
        if (!readRegisters(0x75, &id, 1)) {
            if (++probe < 2) return;
            fail(wrongIdentitySeen ? "WHO_AM_I_mismatch_check_sensor_model" : "no_response_at_0x68_or_0x69"); return;
        }
        char text[80]; snprintf(text, sizeof(text), "address=0x%02X WHO_AM_I=0x%02X (expected 0x68)", address, id); output(text);
        if (id != 0x68) {
            wrongIdentitySeen = true;
            if (++probe < 2) return;
            fail("WHO_AM_I_mismatch_check_sensor_model"); return;
        }
        if (!writeRegister(0x6b, 0x80)) { fail("reset_write_failed"); return; }
        stage = Stage::ResetWait; stageAt = millis(); return;
    }
    case Stage::ResetWait:
        if (uint32_t(millis() - stageAt) >= 100) stage = Stage::Configure;
        return;
    case Stage::Configure:
        if (!writeRegister(config[configIndex][0], config[configIndex][1])) { fail("configuration_write_failed"); return; }
        if (++configIndex == sizeof(config) / sizeof(config[0])) stage = Stage::Verify;
        return;
    case Stage::Verify: {
        uint8_t value;
        if (!readRegisters(config[verifyIndex][0], &value, 1) || value != config[verifyIndex][1]) {
            fail("configuration_verify_failed"); return;
        }
        if (++verifyIndex == sizeof(config) / sizeof(config[0])) { stage = Stage::Warmup; stageAt = millis(); }
        return;
    }
    case Stage::Warmup:
        if (uint32_t(millis() - stageAt) >= 50) {
            stage = Stage::Running; lastSampleAt = millis() - MPU6050Config::SAMPLE_MS;
            output("READY: +/-2g, +/-500deg/s, 100Hz DLPF20Hz; Z=zero when still; yaw PID is NOT connected");
        }
        return;
    case Stage::Running: {
        if (uint32_t(millis() - lastSampleAt) < MPU6050Config::SAMPLE_MS) return;
        lastSampleAt = millis(); uint8_t data[14];
        if (!readRegisters(0x3b, data, sizeof(data))) { fail("sample_read_failed_press_M_to_retry"); return; }
        latest.timeMs = millis(); latest.valid = true;
        latest.axG = signedWord(data) / 16384.0f;
        latest.ayG = signedWord(data + 2) / 16384.0f;
        latest.azG = signedWord(data + 4) / 16384.0f;
        latest.temperatureC = signedWord(data + 6) / 340.0f + 36.53f;
        latest.gxDps = signedWord(data + 8) / 65.5f;
        latest.gyDps = signedWord(data + 10) / 65.5f;
        latest.gzDps = signedWord(data + 12) / 65.5f;
        latest.correctedGzDps = latest.gzDps - gyroBiasZ;
        calibrateSample(); return;
    }
    default: return;
    }
}

MPU6050SensorStatus mpu6050SensorStatus() {
    return {sensorEnabled, stage == Stage::Running, calibrating, eventSequence, eventMessage};
}
