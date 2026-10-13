#include "line_position.h"

LinePosition estimateLinePosition(const LineSensorFrame& frame) {
    LinePosition position = {LinePositionStatus::Lost, 0, 3.5f, frame.lineMask, 0,
                             frame.sequence, frame.timeMs};
    if (!frame.lineMask) return position;
    if (frame.lineMask == 0xff) {
        position.status = LinePositionStatus::AllActive;
        position.activeCount = 8;
        return position;
    }
    int segments = 0, sum = 0;
    bool previous = false;
    for (uint8_t channel = 0; channel < 8; ++channel) {
        const bool active = (frame.lineMask >> channel) & 1;
        if (active) {
            ++position.activeCount;
            sum += channel;
            if (!previous) ++segments;
        }
        previous = active;
    }
    if (segments != 1) {
        position.status = LinePositionStatus::MultipleSegments;
        return position;
    }
    position.centerChannel = float(sum) / position.activeCount;
    position.normalizedError = (position.centerChannel - 3.5f) / 3.5f;
    position.status = LinePositionStatus::Valid;
    return position;
}
