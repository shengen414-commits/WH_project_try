#pragma once
#include <cmath>

// Explicit sample time; derivative on measurement with filtering, conditional I.
class FeedbackPID {
public:
    bool tune(float p, float i, float d) {
        if (!std::isfinite(p) || !std::isfinite(i) || !std::isfinite(d) ||
            p < 0 || i < 0 || d < 0 || p > 500 || i > 500 || d > 100) return false;
        kp = p; ki = i; kd = d; reset(); return true;
    }
    void reset() { integral = 0; derivative = 0; first = true; }
    float compute(float target, float measurement, float dt, float low, float high, bool integrate) {
        if (!std::isfinite(target) || !std::isfinite(measurement) || !std::isfinite(dt) ||
            dt <= 0 || dt > 0.1f || low >= high) { reset(); return 0; }
        const float error = target - measurement;
        if (!first) {
            const float raw = -(measurement - previous) / dt;
            const float alpha = dt / (0.03f + dt);
            derivative += alpha * (raw - derivative);
        }
        previous = measurement; first = false;
        const float base = kp * error + kd * derivative;
        if (integrate) {
            const float candidate = integral + ki * error * dt;
            const float sum = base + candidate;
            if ((sum >= low && sum <= high) || (sum > high && error < 0) || (sum < low && error > 0))
                integral = clamp(candidate, low, high);
        }
        return clamp(base + integral, low, high);
    }
private:
    static float clamp(float v, float low, float high) { return v < low ? low : v > high ? high : v; }
    float kp = 35, ki = 0, kd = 0, integral = 0, derivative = 0, previous = 0;
    bool first = true;
};
