#include <Arduino.h>
#include "encoder.h"
#include "esc_control.h"
#include "serial_transport.h"
#include "line_sensor.h"
#include "steering_control.h"

namespace {
constexpr unsigned long SAMPLE_INTERVAL_MS = 10;
constexpr unsigned long COMMAND_TIMEOUT_MS = 100;
constexpr unsigned long LINE_DEBUG_INTERVAL_MS = 200;
enum class DebugMode { Normal, Line, Capture, Steering };
DebugMode debugMode = DebugMode::Normal;
unsigned long lastLineDebugAt = 0;
char command = 0;
char digits[5];
unsigned int digitCount = 0;
unsigned long commandStartedAt = 0;
unsigned long lastSampleAt = 0;
uint32_t sequence = 0;
void resetCommand() { command = 0; digitCount = 0; }
void selectMode(DebugMode mode) {
    resetCommand();
    if (debugMode == DebugMode::Steering) endSteeringDebug();
    debugMode = mode;
    setESCDebugState(mode != DebugMode::Normal, mode == DebugMode::Steering);
    if (mode == DebugMode::Line) lastLineDebugAt = millis() - LINE_DEBUG_INTERVAL_MS;
    if (mode == DebugMode::Steering) beginSteeringDebug();
}
}

void pollSerialCommands() {
    if (command && millis() - commandStartedAt >= COMMAND_TIMEOUT_MS) resetCommand();
    // Bound work so incoming traffic cannot starve control updates.
    for (unsigned int i = 0; i < 64 && Serial.available() > 0; ++i) {
        char value = Serial.read();
        if (value == 'E' || value == 'e') {
            resetCommand();
            if (debugMode == DebugMode::Steering) handleSteeringCommand('E');
            handleESCCommand('E', 1500);
            return;
        }
        // Mode commands also discard any incomplete throttle packet.
        if (value == 'L' || value == 'l') { selectMode(DebugMode::Line); continue; }
        if (value == 'G' || value == 'g') { selectMode(DebugMode::Capture); continue; }
        if (value == 'V' || value == 'v') { selectMode(DebugMode::Steering); continue; }
        if (value == 'R' || value == 'r') { selectMode(DebugMode::Normal); continue; }
        if (value == 'T' || value == 't' || value == 'B' || value == 'b') {
            command = (value == 'T' || value == 't') ? 'T' : 'B';
            digitCount = 0;
            commandStartedAt = millis();
            continue;
        }
        if (!command) {
            if (debugMode == DebugMode::Steering) handleSteeringCommand(value);
            continue;
        }
        if (value >= '0' && value <= '9' && digitCount < 4) {
            digits[digitCount++] = value;
        } else if (value == '\n') {
            if (digitCount == 4) {
                digits[4] = '\0';
                int pwm = atoi(digits);
                if (pwm >= 1000 && pwm <= 2000 && debugMode != DebugMode::Steering)
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
    if (debugMode == DebugMode::Steering) return;
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
