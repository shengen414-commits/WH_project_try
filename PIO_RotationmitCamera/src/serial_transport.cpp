#include <Arduino.h>
#include "encoder.h"
#include "esc_control.h"
#include "serial_transport.h"
#include "line_sensor.h"

namespace {
constexpr unsigned long SAMPLE_INTERVAL_MS = 10;
constexpr unsigned long COMMAND_TIMEOUT_MS = 100;
constexpr unsigned long LINE_DEBUG_INTERVAL_MS = 200;
bool lineDebugMode = false;
bool lineCaptureMode = false;
unsigned long lastLineDebugAt = 0;
char command = 0;
char digits[5];
unsigned int digitCount = 0;
unsigned long commandStartedAt = 0;
unsigned long lastSampleAt = 0;
uint32_t sequence = 0;
void resetCommand() { command = 0; digitCount = 0; }
}

void pollSerialCommands() {
    if (command && millis() - commandStartedAt >= COMMAND_TIMEOUT_MS) resetCommand();
    // Bound work so incoming traffic cannot starve control updates.
    for (unsigned int i = 0; i < 64 && Serial.available() > 0; ++i) {
        char value = Serial.read();
        if (value == 'E' || value == 'e') {
            resetCommand();
            handleESCCommand('E', 1500);
            return;
        }
        if (value == 'T' || value == 't' || value == 'B' || value == 'b') {
            command = (value == 'T' || value == 't') ? 'T' : 'B';
            digitCount = 0;
            commandStartedAt = millis();
            continue;
        }
        if (!command) {
            if (value == 'L' || value == 'l') {
                lineDebugMode = true;
                lineCaptureMode = false;
                lastLineDebugAt = millis() - LINE_DEBUG_INTERVAL_MS;
            } else if (value == 'G' || value == 'g') {
                lineDebugMode = false;
                lineCaptureMode = true;
            } else if (value == 'R' || value == 'r') {
                lineDebugMode = false;
                lineCaptureMode = false;
            }
            continue;
        }
        if (value >= '0' && value <= '9' && digitCount < 4) {
            digits[digitCount++] = value;
        } else if (value == '\n') {
            if (digitCount == 4) {
                digits[4] = '\0';
                int pwm = atoi(digits);
                if (pwm >= 1000 && pwm <= 2000) handleESCCommand(command, pwm);
            }
            resetCommand();
        } else if (value != '\r') {
            resetCommand();
        }
    }
}

void publishEncoder() {
    if (lineDebugMode || lineCaptureMode) return;
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
    static bool hasSent = false;
    static uint32_t lastSentSequence = 0;
    LineSensorFrame frame;
    if (!readLineSensorFrame(frame) ||
        (hasSent && frame.sequence == lastSentSequence)) return;
    const unsigned long now = millis();
    if (lineDebugMode && now - lastLineDebugAt < LINE_DEBUG_INTERVAL_MS) return;
    char line[64];
    int length;
    if (lineDebugMode) {
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
        if (lineDebugMode) lastLineDebugAt = now;
    }
}
