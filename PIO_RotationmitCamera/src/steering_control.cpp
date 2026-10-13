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
constexpr unsigned long HOLD_MS = 800;
constexpr unsigned long STATUS_MS = 200;
const int steps[] = {SteeringConfig::LOW_US, SteeringConfig::CENTER_US,
                     SteeringConfig::HIGH_US, SteeringConfig::CENTER_US};

void writeTarget(int pwm) {
    targetUs = pwm;
    if (steering.attached()) steering.writeMicroseconds(targetUs);
    lastStatusAt = millis() - STATUS_MS;
}

void help() {
    Serial.println("[SERVO] V=debug; C=center; J=1450; K=1550; A=one sweep; X=cancel/center; ?=help; R=normal; E=emergency stop");
}
}

void beginSteeringDebug() {
    debugEnabled = true;
    sweeping = false;
    // Attach on explicit V, so normal startup does not unexpectedly move steering.
    if (!steering.attached()) {
        steering.setPeriodHertz(50);
        steering.attach(SteeringConfig::PIN, SteeringConfig::LOW_US, SteeringConfig::HIGH_US);
    }
    writeTarget(SteeringConfig::CENTER_US);
    if (!steering.attached()) Serial.println("[SERVO] ERROR: GPIO12 PWM attach failed");
    help();
}

void endSteeringDebug() {
    sweeping = false;
    if (debugEnabled) writeTarget(SteeringConfig::CENTER_US);
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
        writeTarget(SteeringConfig::CENTER_US);
        break;
    case 'J': case 'K':
        sweeping = false;
        writeTarget(command == 'J' ? SteeringConfig::LOW_US : SteeringConfig::HIGH_US);
        break;
    case 'A':
        sweeping = true;
        step = 0;
        stepAt = millis();
        writeTarget(steps[0]);
        break;
    default: break;
    }
}

void updateSteering() {
    if (!debugEnabled || !sweeping || millis() - stepAt < HOLD_MS) return;
    stepAt = millis();
    if (++step < sizeof(steps) / sizeof(steps[0])) writeTarget(steps[step]);
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
