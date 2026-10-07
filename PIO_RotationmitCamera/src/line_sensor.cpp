#include "line_sensor.h"
#include <driver/gpio.h>

namespace {
bool enabled = false;
bool scanning = false;
bool frameReady = false;
uint8_t channel = 0;
uint8_t pendingMask = 0;
uint32_t selectedAtUs = 0;
uint32_t scanStartedAtUs = 0;
uint32_t nextSequence = 0;
LineSensorFrame latest = {};

bool pinsValid() {
    const uint8_t pins[] = {LineSensorConfig::AD2_PIN, LineSensorConfig::AD1_PIN,
                            LineSensorConfig::AD0_PIN, LineSensorConfig::OUT_PIN};
    for (uint8_t i = 0; i < 4; ++i) {
        if (!GPIO_IS_VALID_GPIO(pins[i])) return false;
        if (i < 3 && !GPIO_IS_VALID_OUTPUT_GPIO(pins[i])) return false;
        // Current project: UART0, encoder, ESC and RC input are reserved.
        if (pins[i] == 1 || pins[i] == 3 || pins[i] == 25 || pins[i] == 26 ||
            pins[i] == 14 || pins[i] == 27 || (pins[i] >= 6 && pins[i] <= 11))
            return false;
        for (uint8_t j = 0; j < i; ++j)
            if (pins[i] == pins[j]) return false;
    }
    return true;
}

void selectChannel(uint8_t value) {
    digitalWrite(LineSensorConfig::AD0_PIN, (value >> 0) & 1);
    digitalWrite(LineSensorConfig::AD1_PIN, (value >> 1) & 1);
    digitalWrite(LineSensorConfig::AD2_PIN, (value >> 2) & 1);
    selectedAtUs = micros();
}
}

bool initLineSensor() {
    enabled = false;
    scanning = false;
    frameReady = false;
    nextSequence = 0;
    latest = {};
    if (!pinsValid()) return false;

    pinMode(LineSensorConfig::AD0_PIN, OUTPUT);
    pinMode(LineSensorConfig::AD1_PIN, OUTPUT);
    pinMode(LineSensorConfig::AD2_PIN, OUTPUT);
    // GPIO34 has no internal pull-up/down; module must provide a defined level.
    pinMode(LineSensorConfig::OUT_PIN, INPUT);
    pendingMask = 0;
    channel = 0;
    scanStartedAtUs = micros();
    selectChannel(channel);
    scanning = true;
    enabled = true;
    return true;
}

void updateLineSensor() {
    if (!enabled) return;
    const uint32_t now = micros();
    if (!scanning) {
        if (uint32_t(now - scanStartedAtUs) < LineSensorConfig::SCAN_INTERVAL_US)
            return;
        scanStartedAtUs = now;
        pendingMask = 0;
        channel = 0;
        selectChannel(channel);
        scanning = true;
        return;
    }
    if (uint32_t(now - selectedAtUs) < LineSensorConfig::SETTLE_US) return;
    if (digitalRead(LineSensorConfig::OUT_PIN) == HIGH)
        pendingMask |= uint8_t(1U << channel);

    if (++channel < 8) {
        selectChannel(channel);
        return;
    }
    latest.sequence = nextSequence++;
    latest.timeMs = millis();
    latest.rawMask = pendingMask;
    latest.lineMask = LineSensorConfig::LINE_LEVEL == HIGH
                          ? pendingMask : uint8_t(~pendingMask);
    frameReady = true;
    scanning = false;
}

bool readLineSensorFrame(LineSensorFrame &frame) {
    if (!enabled || !frameReady) return false;
    frame = latest;
    return true;
}
