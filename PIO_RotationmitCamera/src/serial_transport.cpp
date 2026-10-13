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
enum class DebugMode { Normal, Line, Capture, Steering, MPU };
DebugMode debugMode = DebugMode::Normal;
unsigned long lastLineDebugAt = 0;
char command = 0;
char digits[13]; // Four throttle digits, or N<action>,<uint32 request id>.
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
bool parseSteeringRequest(uint32_t& id) {
    id = 0; // Legacy N1/N0 commands remain valid without a request id.
    if (digitCount == 1) return true;
    if (digitCount < 3 || digits[1] != ',') return false;
    for (unsigned int i = 2; i < digitCount; ++i) {
        const uint32_t digit = digits[i] - '0';
        if (digit > 9 || id > (UINT32_MAX - digit) / 10) return false;
        id = id * 10 + digit;
    }
    return true;
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
    debugMode = mode;
    setESCDebugState(mode != DebugMode::Normal, mode == DebugMode::Steering || mode == DebugMode::MPU);
    if (mode == DebugMode::Line) lastLineDebugAt = millis() - LINE_DEBUG_INTERVAL_MS;
    if (mode == DebugMode::Steering) beginSteeringDebug();
    if (mode == DebugMode::MPU) beginMPU6050Debug();
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
            handleESCCommand('E', 1500);
            return;
        }
        if (value == '@') {
            resetCommand(); configPending = true; commandStartedAt = millis(); continue;
        }
        // Mode commands also discard any incomplete throttle packet.
        if (value == 'L' || value == 'l') { selectMode(DebugMode::Line); continue; }
        if (value == 'G' || value == 'g') { selectMode(DebugMode::Capture); continue; }
        if (value == 'V' || value == 'v') { selectMode(DebugMode::Steering); continue; }
        if (value == 'M' || value == 'm') { selectMode(DebugMode::MPU); continue; }
        if (value == 'O' || value == 'o') { selectMode(DebugMode::MPU); handleMPU6050DebugCommand('O'); continue; }
        if (value == 'R' || value == 'r') { selectMode(DebugMode::Normal); continue; }
        if (value == 'N' || value == 'n') {
            resetCommand(); command = 'N'; commandStartedAt = millis(); continue;
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
            continue;
        }
        if (command == 'N' && value == ',' && digitCount == 1) {
            digits[digitCount++] = value;
        } else if (value >= '0' && value <= '9' && digitCount < (command == 'N' ? 12U : 4U)) {
            digits[digitCount++] = value;
        } else if (value == '\n') {
            uint32_t requestId = 0;
            if (command == 'N' && digitCount >= 1 && digits[0] >= '0' && digits[0] <= '2' && parseSteeringRequest(requestId)) {
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
                if (pwm >= 1000 && pwm <= 2000 && debugMode != DebugMode::Steering && debugMode != DebugMode::MPU)
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
