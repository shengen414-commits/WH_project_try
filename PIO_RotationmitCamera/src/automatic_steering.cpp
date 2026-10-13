#include "automatic_steering.h"
#include "feedback_pid.h"
#include "line_sensor.h"
#include "steering_control.h"
#include "esc_control.h"
#include <cmath>
#include <cstdio>
#include <cstring>

namespace {
constexpr uint32_t FEEDBACK_TIMEOUT_MS = 100;
constexpr uint32_t TARGET_TIMEOUT_MS = 250;
constexpr float SLEW_US_PER_SEC = 150;
constexpr float LINE_TO_YAW_DEG_PER_SEC = 60;
constexpr float MAX_YAW_DEG_PER_SEC = 90;
AutomaticSteeringMode mode = AutomaticSteeringMode::Off;
FeedbackPID linePID, yawPID;
bool prepared = false, gyroEnabled = false;
bool yawValid = false, targetValid = false, haveFrame = false;
uint32_t yawAt = 0, targetAt = 0, lastFrameSequence = 0, lastControlAt = 0, lastPublishAt = 0;
float yawRate = 0, targetYawRate = 0, lineError = 0, outputUs = SteeringConfig::CENTER_US;
float lastRequestedUs = SteeringConfig::CENTER_US;
int direction = 1;
uint8_t lastMask = 0;
const char* reason = "disabled";

void prepare() {
    if (prepared) return;
    linePID.tune(35, 0, 0); yawPID.tune(1, 0, 0); prepared = true;
}
void message(const char* text) {
    char line[128];
    const char* name = mode == AutomaticSteeringMode::Line ? "LINE" : mode == AutomaticSteeringMode::ExternalYaw ? "YAW" : mode == AutomaticSteeringMode::Fault ? "FAULT" : "OFF";
    int n = snprintf(line, sizeof(line), "[FOLLOW] mode=%s e=%.3f pwm=%d mask=%u gyro=%d reason=%s\n",
                     name, lineError, (int)std::lround(outputUs), (unsigned int)lastMask, gyroEnabled, text);
    if (n > 0 && n < (int)sizeof(line) && Serial.availableForWrite() >= n)
        Serial.write((const uint8_t*)line, n);
}
bool fresh(uint32_t now, uint32_t at, uint32_t timeout) { return uint32_t(now - at) <= timeout; }
bool yawFresh(uint32_t now) { return yawValid && fresh(now, yawAt, FEEDBACK_TIMEOUT_MS); }
bool targetFresh(uint32_t now) { return targetValid && fresh(now, targetAt, TARGET_TIMEOUT_MS); }
bool linePosition(const LineSensorFrame& frame, float& error) {
    if (!frame.lineMask || frame.lineMask == 0xff) return false;
    int count = 0, segments = 0;
    float sum = 0;
    bool previous = false;
    for (int i = 0; i < 8; ++i) {
        bool active = (frame.lineMask >> i) & 1;
        if (active) { ++count; sum += float(i) - 3.5f; if (!previous) ++segments; }
        previous = active;
    }
    if (segments != 1) return false;  // Crossings/multiple lines need a future route planner.
    error = sum / (float(count) * 3.5f);
    return true;
}
void fault(const char* why) {
    mode = AutomaticSteeringMode::Fault; reason = why;
    linePID.reset(); yawPID.reset();
    outputUs = steeringCenterUs(); centerSteeringOutput();
    lastRequestedUs = outputUs;
    setESCSteeringInhibit(true);
    message(why);
}
bool canArm() {
    const auto drive = escSteeringState();
    if (steeringDebugActive() || drive.debugNeutral || drive.emergency || drive.rcMode || drive.pwm != 1500) {
        reason = "arm_requires_neutral_normal_mode"; message(reason); return false;
    }
    if (!attachSteeringOutput()) { reason = "pwm_attach_failed"; message(reason); return false; }
    return true;
}
void armed(AutomaticSteeringMode next) {
    mode = next; reason = "armed";
    linePID.reset(); yawPID.reset(); haveFrame = false;
    outputUs = steeringCenterUs(); centerSteeringOutput();
    lastRequestedUs = outputUs;
    lastControlAt = millis(); lastPublishAt = millis() - 200;
    setESCSteeringInhibit(false); // Clear old throttle; new T/RC inputs are required.
}
float clamp(float value, float low, float high) { return value < low ? low : value > high ? high : value; }
}

AutomaticSteeringState automaticSteeringState() {
    return {mode, lineError, (int)std::lround(outputUs), lastMask, gyroEnabled, reason};
}
bool submitYawRateFeedback(float value, uint32_t at, bool valid) {
    yawValid = valid && std::isfinite(value) && std::fabs(value) <= 500 && fresh(millis(), at, FEEDBACK_TIMEOUT_MS);
    if (yawValid) { yawRate = value; yawAt = at; }
    return yawValid;
}
bool submitTargetYawRate(float value, uint32_t at, bool valid) {
    targetValid = valid && std::isfinite(value) && std::fabs(value) <= MAX_YAW_DEG_PER_SEC && fresh(millis(), at, TARGET_TIMEOUT_MS);
    if (targetValid) { targetYawRate = value; targetAt = at; }
    return targetValid;
}
bool startLineFollowing() {
    prepare();
    LineSensorFrame frame;
    if (!readLineSensorFrame(frame) || !fresh(millis(), frame.timeMs, FEEDBACK_TIMEOUT_MS) || !linePosition(frame, lineError)) {
        reason = "no_valid_line"; message(reason); return false;
    }
    if (gyroEnabled && !yawFresh(millis())) { reason = "no_valid_gyro"; message(reason); return false; }
    if (!canArm()) return false;
    lastMask = frame.lineMask; armed(AutomaticSteeringMode::Line);
    lastControlAt = frame.timeMs - 10; // First cached frame may precede the arm command.
    message(reason);
    return true;
}
bool startExternalYawControl() {
    prepare();
    if (!yawFresh(millis()) || !targetFresh(millis())) { reason = "no_valid_yaw_source"; message(reason); return false; }
    if (!canArm()) return false;
    gyroEnabled = true; armed(AutomaticSteeringMode::ExternalYaw); message(reason); return true;
}
void stopAutomaticSteering(bool resetDrive) {
    if (mode == AutomaticSteeringMode::Off) return;
    mode = AutomaticSteeringMode::Off; reason = "disabled";
    linePID.reset(); yawPID.reset(); outputUs = steeringCenterUs(); centerSteeringOutput();
    if (resetDrive) setESCSteeringInhibit(false);
    message(reason);
}

void updateAutomaticSteering() {
    if (mode != AutomaticSteeringMode::Line && mode != AutomaticSteeringMode::ExternalYaw) return;
    const uint32_t now = millis();
    const auto drive = escSteeringState();
    if (drive.emergency || drive.debugNeutral || steeringDebugActive()) { stopAutomaticSteering(); return; }
    if (drive.rcMode) { stopAutomaticSteering(false); reason = "rc_takeover"; message(reason); return; }
    if (drive.pwm < 1450) { fault("reverse_not_supported"); return; }
    if (gyroEnabled && !yawFresh(now)) { fault("gyro_stale"); return; }
    if (mode == AutomaticSteeringMode::ExternalYaw && !targetFresh(now)) { fault("target_stale"); return; }
    LineSensorFrame frame;
    if (mode == AutomaticSteeringMode::Line) {
        if (!readLineSensorFrame(frame) || !fresh(now, frame.timeMs, FEEDBACK_TIMEOUT_MS)) { fault("line_stale"); return; }
        lastMask = frame.lineMask;
        if (!linePosition(frame, lineError)) { fault(frame.lineMask == 0 ? "line_lost" : "line_ambiguous"); return; }
        if (haveFrame && frame.sequence == lastFrameSequence) return;
    } else if (uint32_t(now - lastControlAt) < 10) return;
    const uint32_t sampleAt = mode == AutomaticSteeringMode::Line ? frame.timeMs : now;
    const uint32_t elapsed = uint32_t(sampleAt - lastControlAt);
    if (elapsed == 0) return;
    if (elapsed > FEEDBACK_TIMEOUT_MS) { fault("control_gap"); return; }
    lastControlAt = sampleAt;
    if (mode == AutomaticSteeringMode::Line) { haveFrame = true; lastFrameSequence = frame.sequence; }
    const float dt = float(elapsed) * 0.001f;
    const float low = steeringLowUs() - steeringCenterUs(), high = steeringHighUs() - steeringCenterUs();
    // Sign belongs to the actuator mapping; asymmetric travel limits follow it.
    const float controlLow = direction > 0 ? low : -high, controlHigh = direction > 0 ? high : -low;
    float correction = 0;
    const bool actuatorReady = std::fabs(lastRequestedUs - outputUs) <= 1;
    if (gyroEnabled) {
        if (drive.pwm <= 1550) {
            yawPID.reset(); outputUs = steeringCenterUs(); centerSteeringOutput(); reason = "waiting_forward_motion"; return;
        }
        float target = mode == AutomaticSteeringMode::Line ? lineError * LINE_TO_YAW_DEG_PER_SEC : targetYawRate;
        correction = yawPID.compute(target, yawRate, dt, controlLow, controlHigh, actuatorReady);
    } else {
        if (drive.pwm <= 1550) linePID.reset(); // Do not carry accumulated correction through a stop.
        correction = linePID.compute(0, -lineError, dt, controlLow, controlHigh, drive.pwm > 1550 && actuatorReady);
    }
    const float requested = steeringCenterUs() + direction * correction;
    lastRequestedUs = requested;
    const float step = SLEW_US_PER_SEC * dt;
    outputUs = clamp(requested, outputUs - step, outputUs + step);
    if (!writeAutomaticSteeringPulse((int)std::lround(outputUs))) { fault("steering_output_failed"); return; }
    reason = "tracking";
}

void publishAutomaticSteering() {
    if (mode == AutomaticSteeringMode::Off || uint32_t(millis() - lastPublishAt) < 200) return;
    const char* name = mode == AutomaticSteeringMode::Line ? "LINE" : mode == AutomaticSteeringMode::ExternalYaw ? "YAW" : "FAULT";
    char line[128];
    int n = snprintf(line, sizeof(line), "[FOLLOW] mode=%s e=%.3f pwm=%d mask=%u gyro=%d reason=%s\n",
                     name, lineError, (int)std::lround(outputUs), (unsigned int)lastMask, gyroEnabled, reason);
    if (n > 0 && n < (int)sizeof(line) && Serial.availableForWrite() >= n) {
        Serial.write((const uint8_t*)line, n); lastPublishAt = millis();
    }
}

bool handleAutomaticSteeringConfig(const char* packet) {
    prepare();
    float a, b, c; int x, y, z; char extra;
    if (strcmp(packet, "S") == 0 || strcmp(packet, "s") == 0) {
        message(reason); return true;
    }
    if ((packet[0] == 'Y' || packet[0] == 'y') && sscanf(packet + 1, "%f %c", &a, &extra) == 1)
        return submitTargetYawRate(a, millis(), true);
    if (mode != AutomaticSteeringMode::Off && mode != AutomaticSteeringMode::Fault) return false;
    bool result = false;
    switch (packet[0]) {
    case 'P': case 'p':
        if (sscanf(packet + 1, "%f,%f,%f %c", &a, &b, &c, &extra) == 3) result = linePID.tune(a, b, c);
        break;
    case 'Q': case 'q':
        if (sscanf(packet + 1, "%f,%f,%f %c", &a, &b, &c, &extra) == 3) result = yawPID.tune(a, b, c);
        break;
    case 'D': case 'd':
        if (sscanf(packet + 1, "%d %c", &x, &extra) == 1 && (x == 1 || x == -1)) { direction = x; result = true; }
        break;
    case 'W': case 'w':
        if (sscanf(packet + 1, "%d,%d,%d %c", &x, &y, &z, &extra) == 3) result = configureSteeringTravel(x, y, z);
        break;
    case 'H': case 'h':
        if (sscanf(packet + 1, "%d %c", &x, &extra) == 1 && (x == 0 || (x == 1 && yawFresh(millis())))) {
            gyroEnabled = x; yawPID.reset(); result = true;
        }
        break;
    default: break;
    }
    return result;
}
