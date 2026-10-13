#pragma once
#include "line_sensor.h"

enum class LinePositionStatus { Valid, Lost, AllActive, MultipleSegments };

// Sensor adapter: coordinates follow channel order, CH1=-1 and CH8=+1.
// With CH1 mounted on the vehicle's left, positive means tape on the right.
struct LinePosition {
    LinePositionStatus status;
    float normalizedError;
    float centerChannel;  // 0..7; target center is 3.5 (between CH4 and CH5).
    uint8_t activeMask;
    uint8_t activeCount;
    uint32_t sequence;
    uint32_t sampleTimeMs;
    bool valid() const { return status == LinePositionStatus::Valid; }
};

LinePosition estimateLinePosition(const LineSensorFrame& frame);
