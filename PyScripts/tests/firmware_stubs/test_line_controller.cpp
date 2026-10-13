#include <cassert>
#include <cmath>
#include <limits>
#include "line_position.h"
#include "line_controller.h"

unsigned long clockUs = 0;
uint8_t groundMask = 0;
unsigned long millis() { return clockUs / 1000; }
unsigned long micros() { return clockUs; }
int digitalRead(int pin) {
    assert(pin == LineSensorConfig::OUT_PIN);
    const int address = fakePinLevels()[LineSensorConfig::AD0_PIN] |
                        (fakePinLevels()[LineSensorConfig::AD1_PIN] << 1) |
                        (fakePinLevels()[LineSensorConfig::AD2_PIN] << 2);
    return (groundMask >> address) & 1;
}

LineSensorFrame scan(uint8_t mask, bool first = false) {
    groundMask = mask;
    if (!first) { clockUs += LineSensorConfig::SCAN_INTERVAL_US; updateLineSensor(); }
    for (int i = 0; i < 8; ++i) { clockUs += LineSensorConfig::SETTLE_US; updateLineSensor(); }
    LineSensorFrame frame;
    assert(readLineSensorFrame(frame));
    return frame;
}

int main() {
    auto position = estimateLinePosition({10, 123, 0x18, 0x18});
    assert(position.valid() && position.normalizedError == 0);
    assert(position.sequence == 10 && position.sampleTimeMs == 123);
    assert(position.activeCount == 2 && position.centerChannel == 3.5f);
    assert(estimateLinePosition({0, 0, 1, 1}).normalizedError == -1);
    assert(estimateLinePosition({0, 0, 128, 128}).normalizedError == 1);
    assert(estimateLinePosition({0, 0, 0, 0}).status == LinePositionStatus::Lost);
    assert(estimateLinePosition({0, 0, 255, 255}).status == LinePositionStatus::AllActive);
    assert(estimateLinePosition({0, 0, 0x81, 0x81}).status == LinePositionStatus::MultipleSegments);

    BasicLineController basic;
    LineControlContext context = {.01f, -50, 50, false};
    float output = 999;
    // The estimator is independent of control direction. The coordinator passes
    // -normalizedError to request the user-specified opposite response.
    assert(basic.compute(-estimateLinePosition({0, 0, 1, 1}).normalizedError, context, output));
    assert(output == 40);
    assert(basic.compute(-estimateLinePosition({0, 0, 128, 128}).normalizedError, context, output));
    assert(output == -40);
    assert(basic.compute(0, context, output) && output == 0);
    assert(basic.compute(.20f, context, output) && output == 0); // Entry deadband.
    assert(basic.compute(.3f, context, output) && output == 40);
    assert(basic.compute(.20f, context, output) && output == 40); // Hysteresis avoids chatter.
    assert(basic.compute(.15f, context, output) && output == 0);
    assert(basic.compute(-.20f, context, output) && output == 0);
    assert(basic.compute(-.3f, context, output) && output == -40);
    basic.reset();
    assert(basic.compute(-.20f, context, output) && output == 0);
    assert(basic.compute(1, {.01f, -20, 25, false}, output) && output == 25);
    assert(!basic.tune(40, .1f, .2f));
    assert(!basic.tune(0, .22f, .16f));
    assert(basic.tune(45, .3f, .15f));
    assert(basic.compute(-1, context, output) && output == -45);
    assert(!basic.compute(std::numeric_limits<float>::quiet_NaN(), context, output) && output == 0);
    assert(!basic.compute(1, {0, -50, 50, false}, output));

    PIDLineController pid;
    assert(pid.tune(20, 0, 0));
    LineController* algorithm = &pid;
    assert(algorithm->compute(0.5f, context, output) && std::fabs(output - 10) < .001f);
    assert(algorithm->compute(-0.5f, context, output) && std::fabs(output + 10) < .001f);
    algorithm = &basic; // Same interface, no sensor or actuator changes required.
    algorithm->reset();
    assert(algorithm->compute(0.5f, context, output) && output == 45);

    // Exercise real multiplexing and active-level conversion, not just fabricated masks.
    assert(initLineSensor());
    LineSensorFrame pending;
    assert(!readLineSensorFrame(pending));
    updateLineSensor();
    assert(!readLineSensorFrame(pending)); // No read before the settling interval.
    const auto center = scan(0x18, true);
    assert(center.rawMask == 0x18 && center.lineMask == 0x18);
    assert(estimateLinePosition(center).normalizedError == 0);
    basic.tune(40, .22f, .16f);
    const auto left = scan(1);
    assert(left.sequence == center.sequence + 1 && left.rawMask == 1 && left.lineMask == 1);
    assert(basic.compute(-estimateLinePosition(left).normalizedError, context, output) && output == 40);
    const auto right = scan(128);
    assert(basic.compute(-estimateLinePosition(right).normalizedError, context, output) && output == -40);
    assert(!estimateLinePosition(scan(0)).valid());
}
