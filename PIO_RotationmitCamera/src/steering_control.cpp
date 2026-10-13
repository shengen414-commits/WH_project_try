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
constexpr unsigned long HOLD_MS = 800;
constexpr unsigned long STATUS_MS = 200;
constexpr unsigned int TEST_STEP_COUNT = 4;
int testPulse(unsigned int index) {
    return index == 0 ? travelCenter - 50 : index == 2 ? travelCenter + 50 : travelCenter;
}

void writeTarget(int pwm) {
    targetUs = constrain(pwm, travelLow, travelHigh);
    if (steering.attached()) steering.writeMicroseconds(targetUs);
    lastStatusAt = millis() - STATUS_MS;
}

void help() {
    Serial.println("[SERVO] V=debug; C=center; J=center-50; K=center+50; A=one sweep; X=cancel/center; ?=help; R=normal; E=emergency stop");
}
}

bool steeringDebugActive() { return debugEnabled; }
int steeringCenterUs() { return travelCenter; }
int steeringLowUs() { return travelLow; }
int steeringHighUs() { return travelHigh; }
bool attachSteeringOutput() {
    if (!steering.attached()) {
        steering.setPeriodHertz(50);
        steering.attach(SteeringConfig::PIN, travelLow, travelHigh);
    }
    return steering.attached();
}
bool writeAutomaticSteeringPulse(int pwm) {
    if (debugEnabled || !steering.attached()) return false;
    writeTarget(pwm);
    return true;
}
void centerSteeringOutput() {
    if (steering.attached()) writeTarget(travelCenter);
}
bool configureSteeringTravel(int low, int center, int high) {
    if (debugEnabled || low < 1000 || high > 2000 || center < 1400 || center > 1600 ||
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

void publishSteeringDebug() {
    if (!debugEnabled || millis() - lastStatusAt < STATUS_MS) return;
    char line[96];
    int length = snprintf(line, sizeof(line), "[SERVO] GPIO%d target=%d us PWM=%s sweep=%s\n",
                          SteeringConfig::PIN, targetUs, steering.attached() ? "ready" : "error",
                          sweeping ? "running" : "idle");
    if (length > 0 && length < (int)sizeof(line) && Serial.availableForWrite() >= length) {
        Serial.write((const uint8_t*)line, length);
        lastStatusAt = millis();
    }
}
