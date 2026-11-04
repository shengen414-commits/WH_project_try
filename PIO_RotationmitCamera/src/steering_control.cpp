#include "steering_control.h"
#include <ESP32Servo.h>

namespace {
Servo steering;
bool debugEnabled = false;
bool sweeping = false;
unsigned int step = 0;
unsigned long stepAt = 0;
unsigned long lastStatusAt = 0;
int targetUs = SteeringConfig::CENTER_US;
int travelLow = SteeringConfig::LOW_US;
int travelCenter = SteeringConfig::CENTER_US;
int travelHigh = SteeringConfig::HIGH_US;
int debugLow = SteeringConfig::DEBUG_LOW_US, debugHigh = SteeringConfig::DEBUG_HIGH_US;
constexpr unsigned long HOLD_MS = 800;
constexpr unsigned long STATUS_MS = 200;
constexpr unsigned int TEST_STEP_COUNT = 4;
int testPulse(unsigned int index) {
    return index == 0 ? travelCenter - 50 : index == 2 ? travelCenter + 50 : travelCenter;
}

void writeLimitedTarget(int pwm, int low, int high) {
    targetUs = constrain(pwm, low, high);
    if (steering.attached()) steering.writeMicroseconds(targetUs);
    lastStatusAt = millis() - STATUS_MS;
}

void writeTarget(int pwm) {
    writeLimitedTarget(pwm,debugEnabled ? debugLow : travelLow,debugEnabled ? debugHigh : travelHigh);
}

void help() {
    Serial.println("[SERVO] V=debug; C=center; J=center-50; K=center+50; A=one sweep; X=cancel/center; ?=help; R=normal; E=emergency stop");
}
}

bool steeringDebugActive() { return debugEnabled; }
// Midpoint is fixed, including when an older firmware stored another value in NVS.
void initSteeringCalibration() {}
bool saveCurrentSteeringCenter() { return false; }
bool steeringOutputReady() { return steering.attached(); }
int steeringTargetUs() { return targetUs; }
bool setSteeringDebugPulse(int pwm) {
    if (!debugEnabled || !steering.attached() || pwm < debugLow || pwm > debugHigh) return false;
    sweeping = false;
    writeTarget(pwm);
    return true;
}
int steeringCenterUs() { return travelCenter; }
bool expandSteeringDebugRange(bool) { return false; } // Legacy command, superseded by atomic limits.
bool configureSteeringDebugRange(int low, int high) {
    if (!debugEnabled || !steering.attached() || low != SteeringConfig::DEBUG_LOW_US || high != SteeringConfig::DEBUG_HIGH_US ||
        targetUs < low || targetUs > high) return false;
    // Applying limits never writes PWM. Cancel sweeps so a previous demo cannot move it later.
    sweeping = false;
    debugLow = low; debugHigh = high;
    lastStatusAt = millis() - STATUS_MS;
    return true;
}
int steeringLowUs() { return debugEnabled ? debugLow : travelLow; }
int steeringHighUs() { return debugEnabled ? debugHigh : travelHigh; }
bool attachSteeringOutput() {
    initSteeringCalibration();
    if (!steering.attached()) {
        steering.setPeriodHertz(50);
        // Fixed electrical envelope permits changing calibrated limits without reattachment.
        steering.attach(SteeringConfig::PIN, SteeringConfig::ABS_LOW_US, SteeringConfig::ABS_HIGH_US);
    }
    return steering.attached();
}
bool writeAutomaticSteeringPulse(int pwm) {
    if (debugEnabled || !steering.attached()) return false;
    writeTarget(pwm);
    return true;
}
bool writeIndependentSteeringPulse(int pwm, int low, int high) {
    if (debugEnabled || !steering.attached() || low<SteeringConfig::DEBUG_LOW_US ||
        high>SteeringConfig::DEBUG_HIGH_US || low>=travelCenter || high<=travelCenter) return false;
    writeLimitedTarget(pwm,low,high);
    return true;
}
void centerSteeringOutput() {
    if (steering.attached()) writeTarget(travelCenter);
}
bool configureSteeringTravel(int low, int center, int high) {
    if (debugEnabled || low < 1000 || high > 2000 ||
        center < SteeringConfig::CENTER_LOW_US || center > SteeringConfig::CENTER_HIGH_US ||
        low >= center || center >= high) return false;
    // Configure only before first attachment, avoiding PWM detach/restart in motion.
    if (steering.attached() && (low != travelLow || high != travelHigh)) return false;
    travelLow = low; travelCenter = center; travelHigh = high;
    centerSteeringOutput();
    return true;
}

void beginSteeringDebug() {
    debugEnabled = true;
    sweeping = false;
    // Attach on explicit V, so normal startup does not unexpectedly move steering.
    attachSteeringOutput();
    debugLow = SteeringConfig::DEBUG_LOW_US; debugHigh = SteeringConfig::DEBUG_HIGH_US;
    writeTarget(travelCenter);
    if (!steering.attached()) Serial.println("[SERVO] ERROR: GPIO12 PWM attach failed");
    help();
}

void endSteeringDebug() {
    sweeping = false;
    if (debugEnabled) writeTarget(travelCenter);
    debugEnabled = false;
}

void handleSteeringCommand(char command) {
    if (command >= 'a' && command <= 'z') command -= 'a' - 'A';
    if (!debugEnabled) return;
    if (command == '?') { help(); return; }
    if (!steering.attached()) return;
    switch (command) {
    case 'C': case 'X': case 'E':
        sweeping = false;
        writeTarget(travelCenter);
        break;
    case 'J': case 'K':
        sweeping = false;
        writeTarget(command == 'J' ? travelCenter - 50 : travelCenter + 50);
        break;
    case 'A':
        sweeping = true;
        step = 0;
        stepAt = millis();
        writeTarget(testPulse(0));
        break;
    default: break;
    }
}

void updateSteering() {
    if (!debugEnabled || !sweeping || millis() - stepAt < HOLD_MS) return;
    stepAt = millis();
    if (++step < TEST_STEP_COUNT) writeTarget(testPulse(step));
    else {
        sweeping = false;
        Serial.println("[SERVO] Sweep complete; centered");
    }
}

void publishSteeringDebug(bool preciseCommands) {
    if (!debugEnabled || millis() - lastStatusAt < STATUS_MS) return;
    char line[128];
    int length = snprintf(line, sizeof(line), "[SERVO] GPIO%d target=%d us PWM=%s sweep=%s low=%d mid=%d high=%d proto=%d\n",
                          SteeringConfig::PIN, targetUs, steering.attached() ? "ready" : "error",
                          sweeping ? "running" : "idle", steeringLowUs(), travelCenter, steeringHighUs(), preciseCommands ? 4 : 0);
    if (length > 0 && length < (int)sizeof(line) && Serial.availableForWrite() >= length) {
        Serial.write((const uint8_t*)line, length);
        lastStatusAt = millis();
    }
}
