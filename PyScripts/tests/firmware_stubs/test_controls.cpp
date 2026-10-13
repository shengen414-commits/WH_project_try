#include <cassert>
#include <map>
#include "Arduino.h"
#include "esc_control.h"
#include "steering_control.h"

FakeSerial Serial;
unsigned long clockMs = 10000;
unsigned long clockUs = 10000000;
bool rcHigh = false;
bool servoAttachSucceeds = true;
std::map<int, int> pwmOutputs;
unsigned long millis() { return clockMs; }
unsigned long micros() { return clockUs; }
int digitalRead(int) { return rcHigh ? HIGH : LOW; }
extern void rcInterrupt();
void rcFrame(int width) {
    rcHigh = true;
    rcInterrupt();
    clockUs += width;
    rcHigh = false;
    rcInterrupt();
    clockUs += 20000 - width;
    clockMs += 20;
    updateESC();
}

int main() {
    initESC();
    assert(pwmOutputs[14] == 1500);
    assert(pwmOutputs.count(12) == 0);  // No startup steering move.
    handleSteeringCommand('A');
    assert(pwmOutputs.count(12) == 0);
    servoAttachSucceeds = false;
    beginSteeringDebug();
    handleSteeringCommand('A');
    assert(pwmOutputs.count(12) == 0);
    assert(Serial.output.find("PWM attach failed") != std::string::npos);
    endSteeringDebug();
    servoAttachSucceeds = true;
    setESCThrottle(1600);
    assert(pwmOutputs[14] == 1600);
    setESCDebugState(true, true);
    beginSteeringDebug();
    assert(pwmOutputs[14] == 1500 && pwmOutputs[12] == 1500);
    handleSteeringCommand('j');
    assert(pwmOutputs[12] == 1450);
    handleSteeringCommand('K');
    assert(pwmOutputs[12] == 1550);
    Serial.output.clear();
    setESCThrottle(2000);
    startESCBoost(1900);
    handleESCCommand('T', 1800);
    for (int i = 0; i < 10; ++i) rcFrame(1800);
    assert(pwmOutputs[14] == 1500 && Serial.output.empty());
    publishSteeringDebug();
    assert(Serial.output.find("[SERVO] GPIO12 target=1550 us PWM=ready") == 0);
    handleSteeringCommand('A');
    assert(pwmOutputs[12] == 1450);
    clockMs += 799;
    updateSteering();
    assert(pwmOutputs[12] == 1450);
    clockMs += 1;
    updateSteering();
    assert(pwmOutputs[12] == 1500);
    clockMs += 800;
    updateSteering();
    assert(pwmOutputs[12] == 1550);
    handleSteeringCommand('E');
    handleESCCommand('E', 1500);
    clockMs += 1600;
    updateSteering();
    updateESC();
    assert(pwmOutputs[12] == 1500 && pwmOutputs[14] == 1500);
    handleSteeringCommand('A');
    endSteeringDebug();
    setESCDebugState(false, false);
    clockMs += 800;
    updateSteering();
    updateESC();
    assert(pwmOutputs[12] == 1500 && pwmOutputs[14] == 1500);
    setESCDebugState(true, false);  // Line debug filters ESC logs without disabling control.
    Serial.output.clear();
    setESCThrottle(1650);
    clockMs += 120;
    updateESC();
    assert(pwmOutputs[14] == 1650 && Serial.output.empty());
    clockMs += 2600;
    updateESC();
    assert(pwmOutputs[14] == 1500 && Serial.output.empty());  // Failsafe still runs.
    setESCDebugState(false, false);
    for (int i = 0; i < 4; ++i) rcFrame(1800);
    assert(pwmOutputs[14] == 1800);
}
