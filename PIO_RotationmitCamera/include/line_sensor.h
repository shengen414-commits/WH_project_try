#pragma once

#include <Arduino.h>

namespace LineSensorConfig {
constexpr uint8_t AD2_PIN = 33;
constexpr uint8_t AD1_PIN = 32;
// AD0 wire moved from input-only GPIO35 to GPIO13.
constexpr uint8_t AD0_PIN = 13;
constexpr uint8_t OUT_PIN = 34;
constexpr uint32_t SETTLE_US = 50;
constexpr uint32_t SCAN_INTERVAL_US = 10000;
// Verify on the real track. Raw values remain available regardless of polarity.
constexpr uint8_t LINE_LEVEL = LOW;
}

struct LineSensorFrame {
    uint32_t sequence;
    uint32_t timeMs;       // Completion time of the sequential eight-channel scan.
    uint8_t rawMask;       // Bit 0 = CH1, bit 7 = CH8; 1 = electrical HIGH.
    uint8_t lineMask;      // Bits set where raw level matches LINE_LEVEL.
};

// Returns false without configuring pins if the pin assignment is invalid.
bool initLineSensor();
// Call frequently in loop(); waits for channel settling without delay().
void updateLineSensor();
// Returns false until a complete frame exists. Copies the latest complete frame.
bool readLineSensorFrame(LineSensorFrame &frame);
