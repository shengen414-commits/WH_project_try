#pragma once
#include "feedback_pid.h"

enum class LineControlAlgorithm { Basic, PID, Proportional };

struct LineControlContext {
    float dtSeconds;
    float lowCorrectionUs;
    float highCorrectionUs;
    bool allowIntegral;
};

// A replaceable algorithm has no GPIO, UART, motor state or sensor dependency.
// correctionError is the desired correction sign, separate from measured tape
// position. Output is a PWM delta before actuator direction/rate calibration.
class LineController {
public:
    virtual ~LineController() = default;
    virtual void reset() = 0;
    virtual bool compute(float correctionError, const LineControlContext& context,
                         float& correctionUs) = 0;
};

class BasicLineController : public LineController {
public:
    bool tune(float offsetUs, float enterError, float exitError);
    void reset() override { side = 0; }
    bool compute(float correctionError, const LineControlContext& context,
                 float& correctionUs) override;
    float offsetUs() const { return offset; }
private:
    float offset = 40;
    float enterThreshold = 0.22f;
    float exitThreshold = 0.16f;
    int side = 0;
};

class PIDLineController : public LineController {
public:
    bool tune(float kp, float ki, float kd) { return pid.tune(kp, ki, kd); }
    void reset() override { pid.reset(); }
    bool compute(float correctionError, const LineControlContext& context,
                 float& correctionUs) override;
private:
    FeedbackPID pid;
};

class ProportionalLineController : public LineController {
public:
    bool tune(float left, float right, float deadband, float slew);
    void reset() override {}
    bool compute(float error, const LineControlContext& context, float& output) override;
    float leftUs = 400, rightUs = 400, deadband = .16f, slewUsPerSec = 600;
};
