#include "line_controller.h"
#include <cmath>

namespace {
float clamp(float value, float low, float high) {
    return value < low ? low : value > high ? high : value;
}
bool validInput(float error, const LineControlContext& context) {
    return std::isfinite(error) && std::fabs(error) <= 1.01f &&
           std::isfinite(context.dtSeconds) && context.dtSeconds > 0 && context.dtSeconds <= 0.1f &&
           std::isfinite(context.lowCorrectionUs) && std::isfinite(context.highCorrectionUs) &&
           context.lowCorrectionUs < context.highCorrectionUs;
}
}

bool BasicLineController::tune(float offsetUs, float enterError, float exitError) {
    if (!std::isfinite(offsetUs) || !std::isfinite(enterError) || !std::isfinite(exitError) ||
        offsetUs <= 0 || offsetUs > 200 || exitError < 0 || exitError >= enterError || enterError >= 1)
        return false;
    offset = offsetUs;
    enterThreshold = enterError;
    exitThreshold = exitError;
    reset();
    return true;
}

bool BasicLineController::compute(float error, const LineControlContext& context, float& correctionUs) {
    correctionUs = 0;
    if (!validInput(error, context)) { reset(); return false; }
    if (std::fabs(error) <= exitThreshold) side = 0;
    else if (error >= enterThreshold) side = 1;
    else if (error <= -enterThreshold) side = -1;
    correctionUs = clamp(side * offset, context.lowCorrectionUs, context.highCorrectionUs);
    return true;
}

bool PIDLineController::compute(float error, const LineControlContext& context, float& correctionUs) {
    correctionUs = 0;
    if (!validInput(error, context)) { reset(); return false; }
    correctionUs = pid.compute(0, -error, context.dtSeconds, context.lowCorrectionUs,
                               context.highCorrectionUs, context.allowIntegral);
    return std::isfinite(correctionUs);
}

bool ProportionalLineController::tune(float left, float right, float dead, float slew) {
    if (!std::isfinite(left) || !std::isfinite(right) || !std::isfinite(dead) || !std::isfinite(slew) ||
        left < 0 || left > 300 || right < 0 || right > 300 || dead < 0 || dead > .3f || slew < 50 || slew > 1200) return false;
    leftUs = left; rightUs = right; deadband = dead; slewUsPerSec = slew;
    return true;
}
bool ProportionalLineController::compute(float error, const LineControlContext& context, float& output) {
    output = 0;
    if (!validInput(error, context)) return false;
    const float magnitude = std::fabs(error);
    if (magnitude <= deadband) return true;
    const float scale = clamp((magnitude-deadband)/(1-deadband), 0, 1);
    output = clamp((error > 0 ? leftUs : -rightUs)*scale, context.lowCorrectionUs, context.highCorrectionUs);
    return true;
}
