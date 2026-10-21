#include <Arduino.h>
#include "encoder.h"
#include "esc_control.h"
#include "serial_transport.h"
#include "line_sensor.h"
#include "steering_control.h"
#include "automatic_steering.h"
#include "mpu6050_debug.h"
#include <cstring>

namespace {
constexpr unsigned long SAMPLE_INTERVAL_MS = 10;
constexpr unsigned long COMMAND_TIMEOUT_MS = 100;
constexpr unsigned long LINE_DEBUG_INTERVAL_MS = 200;
enum class DebugMode { Normal, Line, Capture, Steering, MPU, Observe };
DebugMode debugMode = DebugMode::Normal;
unsigned long lastLineDebugAt = 0;
uint32_t observeFrameAt = 0, observeImuAt = 0, observeStatusAt = 0, observeEventAt = 0;
bool observePreferImu = false;
char command = 0;
char digits[16]; // Four pulse digits plus comma and uint32 request id.
unsigned int digitCount = 0;
unsigned long commandStartedAt = 0;
bool configPending = false;
char configPacket[64];
unsigned int configLength = 0;
unsigned long lastSampleAt = 0;
uint32_t sequence = 0;
char pendingSteeringAck[128];
int pendingSteeringAckLength = 0;
void publishSteeringAck() {
    if (pendingSteeringAckLength > 0 && Serial.availableForWrite() >= pendingSteeringAckLength) {
        Serial.write((const uint8_t*)pendingSteeringAck, pendingSteeringAckLength);
        pendingSteeringAckLength = 0;
    }
}
bool parseRequestId(unsigned int start, uint32_t& id) {
    if (digitCount <= start) return false;
    id = 0;
    for (unsigned int i = start; i < digitCount; ++i) {
        const uint32_t digit = digits[i] - '0';
        if (digit > 9 || id > (UINT32_MAX - digit) / 10) return false;
        id = id * 10 + digit;
    }
    return id != 0;
}
bool parseSteeringRequest(uint32_t& id) {
    id = 0; // Legacy N1/N0 commands remain valid without a request id.
    if (digitCount == 1) return true;
    if (digitCount < 3 || digits[1] != ',') return false;
    return parseRequestId(2, id);
}
void acknowledgeSteering(uint32_t id, char action, bool ok) {
    const auto state = automaticSteeringState();
    LineSensorFrame frame;
    const bool haveFrame = readLineSensorFrame(frame);
    pendingSteeringAckLength = snprintf(pendingSteeringAck, sizeof(pendingSteeringAck),
        "[FOLLOW_ACK] id=%lu action=%c ok=%d mask=%d raw=%d lvl=%d reason=%s\n",
        (unsigned long)id, action, ok ? 1 : 0, haveFrame ? (int)frame.lineMask : -1,
        haveFrame ? (int)frame.rawMask : -1, LineSensorConfig::LINE_LEVEL == HIGH ? 1 : 0,
        action == '0' ? "disabled" : state.reason);
    if (pendingSteeringAckLength >= (int)sizeof(pendingSteeringAck)) pendingSteeringAckLength = 0;
    publishSteeringAck(); // If UART is busy, retry at the start of subsequent loops.
}
void resetCommand() { command = 0; digitCount = 0; configPending = false; configLength = 0; }
void selectMode(DebugMode mode) {
    resetCommand();
    stopAutomaticSteering();
    if (debugMode == DebugMode::Steering) endSteeringDebug();
    if (debugMode == DebugMode::MPU) endMPU6050Debug();
    if (debugMode == DebugMode::Observe) endMPU6050Sensor();
    debugMode = mode;
    setESCDebugState(mode != DebugMode::Normal, mode == DebugMode::Steering || mode == DebugMode::MPU || mode == DebugMode::Observe);
    if (mode == DebugMode::Line) lastLineDebugAt = millis() - LINE_DEBUG_INTERVAL_MS;
    if (mode == DebugMode::Steering) beginSteeringDebug();
    if (mode == DebugMode::MPU) beginMPU6050Debug();
    if (mode == DebugMode::Observe) {
        beginMPU6050Sensor();
        const uint32_t now = millis();
        observeFrameAt = now - 20; observeImuAt = now - 10;
        observeStatusAt = now - 200; observeEventAt = now - 50;
        observePreferImu = false;
    }
}
void manualSteeringRequest(int pulse, uint32_t id) {
    bool ok = false;
    const char* reason = "not_manual";
    if (pulse == 1 && (debugMode == DebugMode::Normal || debugMode == DebugMode::Steering)) {
        if (debugMode == DebugMode::Steering) selectMode(DebugMode::Normal);
        ok = true;
    } else if (debugMode == DebugMode::Steering) {
        if (pulse == 2) {
            ok = saveCurrentSteeringCenter();
            reason = steeringTargetUs() < SteeringConfig::CENTER_LOW_US || steeringTargetUs() > SteeringConfig::CENTER_HIGH_US
                     ? "center_range" : "save_failed";
        } else {
            ok = setSteeringDebugPulse(pulse == 0 ? steeringCenterUs() : pulse);
            reason = !steeringOutputReady() ? "pwm_failed" : "out_of_range";
        }
    }
    if (ok) reason = "ok";
    pendingSteeringAckLength = snprintf(pendingSteeringAck, sizeof(pendingSteeringAck),
        "[SERVO_ACK] id=%lu ok=%d target=%d low=%d mid=%d high=%d manual=%d ready=%d proto=2 reason=%s\n",
        (unsigned long)id, ok, steeringTargetUs(), steeringLowUs(), steeringCenterUs(), steeringHighUs(),
        debugMode == DebugMode::Steering, steeringOutputReady(), reason);
    if (pendingSteeringAckLength >= (int)sizeof(pendingSteeringAck)) pendingSteeringAckLength = 0;
    publishSteeringAck();
}
}
bool serialNormalMode() { return debugMode == DebugMode::Normal; }

void pollSerialCommands() {
    publishSteeringAck(); // Command results get an opportunity before sample packets.
    if ((command || configPending) && millis() - commandStartedAt >= COMMAND_TIMEOUT_MS) resetCommand();
    // Bound work so incoming traffic cannot starve control updates.
    for (unsigned int i = 0; i < 64 && Serial.available() > 0; ++i) {
        char value = Serial.read();
        if (value == 'E' || value == 'e') {
            resetCommand();
            stopAutomaticSteering();
            if (debugMode == DebugMode::Steering) handleSteeringCommand('E');
            if (debugMode == DebugMode::MPU) handleMPU6050DebugCommand('E');
            if (debugMode == DebugMode::Observe) handleMPU6050SensorCommand('E');
            handleESCCommand('E', 1500);
            return;
        }
        if (value == '@') {
            resetCommand(); configPending = true; commandStartedAt = millis(); continue;
        }
        // Mode commands also discard any incomplete throttle packet.
        if (value == 'U' || value == 'u') { selectMode(DebugMode::Observe); continue; }
        if (value == '!') { resetCommand(); command = '!'; commandStartedAt = millis(); continue; }
        if (value == 'L' || value == 'l') { selectMode(DebugMode::Line); continue; }
        if (value == 'G' || value == 'g') { selectMode(DebugMode::Capture); continue; }
        if (value == 'V' || value == 'v') { selectMode(DebugMode::Steering); continue; }
        if (value == 'M' || value == 'm') { selectMode(DebugMode::MPU); continue; }
        if (value == 'O' || value == 'o') { selectMode(DebugMode::MPU); handleMPU6050DebugCommand('O'); continue; }
        if (value == 'R' || value == 'r') { selectMode(DebugMode::Normal); continue; }
        if (value == 'N' || value == 'n') {
            resetCommand(); command = 'N'; commandStartedAt = millis(); continue;
        }
        if (!configPending && (value == 'S' || value == 's')) {
            resetCommand(); command = 'S'; commandStartedAt = millis(); continue;
        }
        if (value == 'T' || value == 't' || value == 'B' || value == 'b') {
            command = (value == 'T' || value == 't') ? 'T' : 'B';
            digitCount = 0;
            commandStartedAt = millis();
            continue;
        }
        if (configPending) {
            if (value == '\n') {
                configPacket[configLength] = '\0';
                const bool ok = serialNormalMode() && handleAutomaticSteeringConfig(configPacket);
                if (serialNormalMode()) {
                    const char* result = ok ? "[FOLLOW] config accepted\n" : "[FOLLOW] config rejected\n";
                    int length = strlen(result);
                    if (Serial.availableForWrite() >= length) Serial.write((const uint8_t*)result, length);
                }
                resetCommand();
            } else if (value != '\r') {
                if (configLength + 1 < sizeof(configPacket)) configPacket[configLength++] = value;
                else resetCommand();
            }
            continue;
        }
        if (!command) {
            if (debugMode == DebugMode::Steering) handleSteeringCommand(value);
            if (debugMode == DebugMode::MPU) handleMPU6050DebugCommand(value);
            if (debugMode == DebugMode::Observe) handleMPU6050SensorCommand(value);
            continue;
        }
        if ((command == 'N' && digitCount == 1 && value == ',') ||
            (command == 'S' && digitCount == 4 && value == ',')) {
            digits[digitCount++] = value;
        } else if (value >= '0' && value <= '9' && digitCount < (command == 'S' ? 15U : command == 'N' ? 12U : command == '!' ? 10U : 4U)) {
            digits[digitCount++] = value;
        } else if (value == '\n') {
            uint32_t requestId = 0;
            if (command == '!') {
                if (parseRequestId(0, requestId)) {
                    char reply[48];
                    int n = snprintf(reply, sizeof(reply), "[SYNC],%lu,%lu\n", (unsigned long)requestId, (unsigned long)millis());
                    if (Serial.availableForWrite() >= n) Serial.write((const uint8_t*)reply, n);
                }
            } else if (command == 'S') {
                if (digitCount >= 6 && digits[4] == ',' && parseRequestId(5, requestId)) {
                    digits[4] = '\0';
                    manualSteeringRequest(atoi(digits), requestId);
                }
            } else if (command == 'N' && digitCount >= 1 && digits[0] >= '0' && digits[0] <= '2' && parseSteeringRequest(requestId)) {
                const char action = digits[0];
                selectMode(DebugMode::Normal); // Autonomous control keeps ENC and LINE streams running.
                bool ok = true;
                if (action == '1') ok = startLineFollowing();
                else if (action == '2') ok = startExternalYawControl();
                else {
                    setESCSteeringInhibit(false);
                    centerSteeringOutput();
                    handleAutomaticSteeringConfig("S");
                }
                acknowledgeSteering(requestId, action, ok);
            } else if (command != 'N' && digitCount == 4) {
                digits[4] = '\0';
                int pwm = atoi(digits);
                if (pwm >= 1000 && pwm <= 2000 && debugMode != DebugMode::Steering && debugMode != DebugMode::MPU && debugMode != DebugMode::Observe)
                    handleESCCommand(command, pwm);
            }
            resetCommand();
        } else if (value != '\r') {
            resetCommand();
        }
    }
}

void publishEncoder() {
    if (debugMode != DebugMode::Normal) return;
    unsigned long now = millis();
    if (now - lastSampleAt < SAMPLE_INTERVAL_MS) return;
    lastSampleAt = now;
    long position = readEncoderPosition();
    char line[64];
    int length = snprintf(line, sizeof(line), "[ENC],%lu,%lu,%ld\n",
                          (unsigned long)sequence++, now, position);
    // Skip instead of waiting on full TX; sequence gaps expose the loss.
    if (length > 0 && length < (int)sizeof(line) && Serial.availableForWrite() >= length)
        Serial.write((const uint8_t*)line, length);
}

void publishLineSensor() {
    if (debugMode == DebugMode::Observe) {
        static uint32_t observationSequence = 0, lastEvent = 0;
        const uint32_t now = millis();
        char packet[128]; int n = 0;
        bool frameDue = uint32_t(now - observeFrameAt) >= 20;
        bool imuDue = uint32_t(now - observeImuAt) >= 20;
        if (frameDue && imuDue) {
            // Under sustained slow loops, alternate priority so neither stream starves.
            if (observePreferImu) { frameDue = false; observeFrameAt = now - 10; }
            else { imuDue = false; observeImuAt = now - 10; }
            observePreferImu = !observePreferImu;
        }
        if (frameDue) {
            // Skip a full-TX slot instead of retrying formatting every control loop.
            observeFrameAt = now;
            LineSensorFrame frame;
            const bool valid = readLineSensorFrame(frame) && uint32_t(now - frame.timeMs) <= 100;
            n = snprintf(packet, sizeof(packet), "[OBS],%lu,%lu,%ld,%d,%d,%lu,%d,%d,%d,%d\n",
                         (unsigned long)observationSequence++, (unsigned long)now, readEncoderPosition(),
                         valid ? frame.rawMask : -1, valid ? frame.lineMask : -1,
                         (unsigned long)(valid ? frame.timeMs : now), steeringTargetUs(), steeringCenterUs(), steeringLowUs(), steeringHighUs());
            if (n > 0 && n < (int)sizeof(packet) && Serial.availableForWrite() >= n) {
                Serial.write((const uint8_t*)packet, n);
            }
        }
        MPU6050Sample sample;
        if (imuDue) {
            observeImuAt = now;
            if (readMPU6050Sample(sample)) {
                // Same units/schema as O mode; only observed data, no steering feedback submission.
                n = snprintf(packet, sizeof(packet), "[IMU],%lu,%.4f,%.4f,%.4f,%.3f,%.3f,%.3f,%.2f,%.3f,%s\n",
                             (unsigned long)sample.timeMs, sample.axG, sample.ayG, sample.azG,
                             sample.gxDps, sample.gyDps, sample.gzDps, sample.temperatureC, sample.correctedGzDps,
                             mpu6050SensorStatus().calibrating ? "RUN" : sample.calibrated ? "OK" : "NO");
                if (n > 0 && n < (int)sizeof(packet) && Serial.availableForWrite() >= n) {
                    Serial.write((const uint8_t*)packet, n);
                }
            }
        }
        if (uint32_t(now - observeStatusAt) >= 200) {
            observeStatusAt = now;
            const auto status = mpu6050SensorStatus();
            n = snprintf(packet, sizeof(packet), "[LAB],%lu,%d,%d\n", (unsigned long)now, status.ready, status.calibrating);
            if (Serial.availableForWrite() >= n) Serial.write((const uint8_t*)packet, n);
        }
        const auto status = mpu6050SensorStatus();
        if (status.event != lastEvent && uint32_t(now - observeEventAt) >= 50) {
            observeEventAt = now;
            n = snprintf(packet, sizeof(packet), "[LAB_MSG] %s\n", status.message);
            if (n > 0 && n < (int)sizeof(packet) && Serial.availableForWrite() >= n) {
                Serial.write((const uint8_t*)packet, n); lastEvent = status.event;
            }
        }
        return;
    }
    if (debugMode == DebugMode::Steering || debugMode == DebugMode::MPU) return;
    static bool hasSent = false;
    static uint32_t lastSentSequence = 0;
    LineSensorFrame frame;
    if (!readLineSensorFrame(frame) ||
        (hasSent && frame.sequence == lastSentSequence)) return;
    const unsigned long now = millis();
    if (debugMode == DebugMode::Line && now - lastLineDebugAt < LINE_DEBUG_INTERVAL_MS) return;
    char line[64];
    int length;
    if (debugMode == DebugMode::Line) {
        char raw[9], detected[9];
        for (uint8_t i = 0; i < 8; ++i) {
            raw[i] = ((frame.rawMask >> i) & 1) ? '1' : '0';
            detected[i] = ((frame.lineMask >> i) & 1) ? '1' : '0';
        }
        raw[8] = detected[8] = '\0';
        // Printed from CH1 to CH8, unlike conventional binary mask notation.
        length = snprintf(line, sizeof(line), "[LINE_DBG] raw=%s line=%s\n", raw, detected);
    } else {
        length = snprintf(line, sizeof(line), "[LINE],%lu,%lu,%u,%u\n",
                                (unsigned long)frame.sequence,
                                (unsigned long)frame.timeMs,
                                (unsigned int)frame.rawMask,
                                (unsigned int)frame.lineMask);
    }
    // No waiting on UART: encoder/control keep their existing priority.
    if (length > 0 && length < (int)sizeof(line) && Serial.availableForWrite() >= length) {
        Serial.write((const uint8_t *)line, length);
        hasSent = true;
        lastSentSequence = frame.sequence;
        if (debugMode == DebugMode::Line) lastLineDebugAt = now;
    }
}
