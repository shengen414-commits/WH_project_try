#include <cassert>
#include <cmath>
#include <map>
#include <limits>
#include "Arduino.h"
#include "feedback_pid.h"
#include "automatic_steering.h"
#include "serial_transport.h"
#include "imu_sensor.h"
#include "line_sensor.h"
#include "esc_control.h"
#include "steering_control.h"

FakeSerial Serial;
unsigned long clockMs = 10000;
bool rcHigh = false, servoAttachSucceeds = true, frameReady = true;
std::map<int, int> pwmOutputs;
LineSensorFrame frame = {1, 10000, 0x18, 0x18};
unsigned long clockUs = 10000000;
unsigned long millis() { return clockMs; }
unsigned long micros() { return clockUs; }
int digitalRead(int) { return rcHigh ? HIGH : LOW; }
long readEncoderPosition() { return 42; }
bool readLineSensorFrame(LineSensorFrame& out) { out = frame; return frameReady; }
extern void rcInterrupt();
void beginMPU6050Debug() {}
void endMPU6050Debug() {}
void handleMPU6050DebugCommand(char) {}
void beginMPU6050Sensor() {}
void endMPU6050Sensor() {}
void handleMPU6050SensorCommand(char) {}
bool readMPU6050Sample(MPU6050Sample&) { return false; }
MPU6050SensorStatus mpu6050SensorStatus() { return {false, false, false, 0, "test"}; }
void feed(const char* value) { Serial.input += value; pollSerialCommands(); }
void cycle(uint8_t mask, unsigned long dt = 10) {
    clockMs += dt; clockUs += dt * 1000;
    frame.timeMs = clockMs; ++frame.sequence; frame.rawMask = mask; frame.lineMask = mask;
    updateESC(); updateAutomaticSteering();
}
void rcFrame(int width) {
    rcHigh = true; rcInterrupt(); clockUs += width;
    rcHigh = false; rcInterrupt(); clockUs += 20000 - width;
    clockMs += 20; updateESC(); updateAutomaticSteering();
}

int main() {
    FeedbackPID pid;
    assert(pid.tune(10, 5, 0));
    for (int i = 0; i < 100; ++i) assert(pid.compute(1, 0, .01f, -2, 2, true) == 2);
    assert(pid.compute(0, 0, .01f, -2, 2, true) == 0);  // No windup at saturation.
    assert(pid.tune(0, 0, 1));
    assert(pid.compute(0, 0, .01f, -10, 10, true) == 0);
    assert(pid.compute(50, 0, .01f, -10, 10, true) == 0);  // No setpoint derivative kick.
    assert(pid.compute(0, 2, .01f, -10, 10, false) < 0);
    assert(pid.compute(0, 2, 0, -10, 10, false) == 0);
    assert(pid.compute(0, 2, .2f, -10, 10, false) == 0);
    assert(!pid.tune(-1, 0, 0));
    assert(!pid.tune(std::numeric_limits<float>::quiet_NaN(), 0, 0));

    initESC(); updateAutomaticSteering();
    assert(automaticSteeringState().mode == AutomaticSteeringMode::Off);
    assert(pwmOutputs.count(12) == 0);
    assert(!handleAutomaticSteeringConfig("H1")); // No phantom MPU6050 feedback.
    assert(!startExternalYawControl());
    frameReady = false; assert(!startLineFollowing()); frameReady = true;
    servoAttachSucceeds = false; assert(!startLineFollowing()); servoAttachSucceeds = true;
    assert(handleAutomaticSteeringConfig("W1450,1500,1550"));
    assert(!handleAutomaticSteeringConfig("W1600,1500,1550"));
    feed("N1\n");
    assert(automaticSteeringState().mode == AutomaticSteeringMode::Line);
    assert(automaticSteeringState().algorithm == LineControlAlgorithm::Basic);
    updateAutomaticSteering(); // A cached frame earlier than arming remains usable.
    assert(pwmOutputs[12] == 1500);
    feed("T1600\n");
    const int before = pwmOutputs[12]; cycle(0x80);
    assert(pwmOutputs[12] < before && before - pwmOutputs[12] <= 2); // Opposite response, slew limit.
    const int stable = pwmOutputs[12]; updateAutomaticSteering();
    assert(pwmOutputs[12] == stable); // Do not recompute cached samples.
    for (int i = 0; i < 40; ++i) cycle(0x80);
    assert(pwmOutputs[12] == 1460); // Basic mode uses a fixed 40us correction.
    for (int i = 0; i < 40; ++i) cycle(0x08); // CH4 alone is in the center zone.
    assert(pwmOutputs[12] == 1500);
    for (int i = 0; i < 40; ++i) cycle(0x10); // CH5 alone is also in the center zone.
    assert(pwmOutputs[12] == 1500);
    Serial.output.clear(); publishEncoder(); publishLineSensor();
    assert(Serial.output.find("[ENC]") != std::string::npos && Serial.output.find("[LINE]") != std::string::npos);
    assert(!handleAutomaticSteeringConfig("P70,0,0")); // No gain changes in motion.
    assert(!handleAutomaticSteeringConfig("C1"));
    assert(!handleAutomaticSteeringConfig("F45,0.25,0.16"));
    cycle(0);
    assert(automaticSteeringState().mode == AutomaticSteeringMode::Line);
    assert(std::string(automaticSteeringState().reason)=="line_waiting");
    feed("T2000\n");
    for(int i=0;i<200;++i) cycle(0);
    assert(pwmOutputs[14]==1500 && pwmOutputs[12]==1500);
    cycle(0x18);
    assert(automaticSteeringState().mode == AutomaticSteeringMode::Line && pwmOutputs[14]==1500);
    cycle(0x0f);
    assert(automaticSteeringState().mode == AutomaticSteeringMode::Fault);
    assert(pwmOutputs[12] == 1500 && pwmOutputs[14] == 1500);
    feed("T2000\nB1900\n");
    for (int i = 0; i < 10; ++i) rcFrame(1800);
    cycle(0x18);
    assert(pwmOutputs[14] == 1500 && automaticSteeringState().mode == AutomaticSteeringMode::Fault);
    feed("N0\n");
    assert(automaticSteeringState().mode == AutomaticSteeringMode::Off && pwmOutputs[14] == 1500);
    feed("@D -1\n@P 35,0,0\nN1\n");
    cycle(0x80);
    assert(pwmOutputs[12] > 1500);
    feed("L\n");
    assert(automaticSteeringState().mode == AutomaticSteeringMode::Off && !serialNormalMode());
    feed("V\nN1\n");
    assert(serialNormalMode() && !steeringDebugActive());
    assert(automaticSteeringState().mode == AutomaticSteeringMode::Line);
    clockMs += 101; updateAutomaticSteering();
    assert(automaticSteeringState().mode == AutomaticSteeringMode::Fault);
    assert(std::string(automaticSteeringState().reason) == "line_stale");
    feed("R\n"); cycle(0xff); assert(!startLineFollowing());
    cycle(0x81); assert(!startLineFollowing()); // Two separate lines are ambiguous.
    cycle(0x18); assert(startLineFollowing());
    feed("T1400\n"); updateAutomaticSteering();
    assert(std::string(automaticSteeringState().reason) == "reverse_not_supported" && pwmOutputs[14] == 1500);
    feed("N0\n"); cycle(0x18); assert(startLineFollowing());
    for (int i = 0; i < 4; ++i) rcFrame(1800);
    assert(automaticSteeringState().mode == AutomaticSteeringMode::Off && pwmOutputs[14] == 1800);
    feed("T1500\n"); cycle(0x18); assert(startLineFollowing());
    feed("@P35,E\nT1900\n");
    assert(automaticSteeringState().mode == AutomaticSteeringMode::Off && pwmOutputs[14] == 1500);
    assert(Serial.input.empty()); // E interrupts config packets and flushes queued motion.
    assert(!startLineFollowing());
    cycle(0x18, 1100); // Let E protection expire, then rearm explicitly.
    assert(submitYawRateFeedback(0, clockMs, true));
    assert(!submitYawRateFeedback(std::numeric_limits<float>::infinity(), clockMs, true));
    assert(submitYawRateFeedback(0, clockMs, true));
    assert(handleAutomaticSteeringConfig("H1"));
    assert(submitTargetYawRate(10, clockMs, true));
    assert(startExternalYawControl());
    cycle(0x18);
    assert(pwmOutputs[12] == 1500); // Gyro loop waits for forward throttle.
    feed("T1600\n"); cycle(0x18);
    clockMs += 101; updateAutomaticSteering();
    assert(std::string(automaticSteeringState().reason) == "gyro_stale" && pwmOutputs[14] == 1500);
    feed("N0\n");
    assert(submitYawRateFeedback(0, clockMs, true)); assert(submitTargetYawRate(10, clockMs, true));
    assert(startExternalYawControl());
    for (int i = 0; i < 30; ++i) { submitYawRateFeedback(0, clockMs, true); cycle(0x18); }
    assert(std::string(automaticSteeringState().reason) == "target_stale");
    feed("N0\n");
    clockMs = 0xfffffff0UL;
    assert(submitYawRateFeedback(0, uint32_t(clockMs), true));
    clockMs = 20;
    assert(submitTargetYawRate(0, 0xfffffff0UL, true)); // Timestamp freshness works across wrap.
    assert(!submitYawRateFeedback(0, uint32_t(clockMs - 101), true));
    // New parameter packets remain separate from V-mode C and existing T/B/N commands.
    feed("N0\nR\n@C 1\n@P 20,0,0\n");
    assert(automaticSteeringState().algorithm == LineControlAlgorithm::PID);
    feed("@C 0\n@F 45,0.25,0.16\n");
    assert(automaticSteeringState().algorithm == LineControlAlgorithm::Basic);
    assert(!handleAutomaticSteeringConfig("C2"));
    assert(!handleAutomaticSteeringConfig("F-10,0.22,0.16"));
    cycle(0x18,20000); // Finish the preceding rollover/old emergency-latch scenario.
    feed("V\nS0002,499\nR\n"); // Explicit calibration restores center +/-300 travel.
    feed("@K180,120,0.100,600,1,500\n");
    assert(Serial.output.find("[LINE_CFG] id=500 ok=1") != std::string::npos);
    assert(automaticSteeringState().algorithm == LineControlAlgorithm::Proportional);
    assert(automaticSteeringState().mode == AutomaticSteeringMode::Off);
    assert(!handleAutomaticSteeringConfig("K999,100,.16,600,1,501"));
    cycle(0x18); feed("N1\n");
    assert(automaticSteeringState().mode == AutomaticSteeringMode::Line);
    assert(!handleAutomaticSteeringConfig("K100,100,.16,600,1,502"));
    for(int i=0;i<40;++i)cycle(0x01);
    assert(pwmOutputs[12]==steeringCenterUs()+180);
    for(int i=0;i<60;++i)cycle(0xc0);
    assert(pwmOutputs[12]<steeringCenterUs());
    for(int i=0;i<40;++i)cycle(0x18);
    assert(pwmOutputs[12]==steeringCenterUs());
    for(int i=0;i<40;++i)cycle(0x08);
    assert(pwmOutputs[12]>steeringCenterUs());
    for(int i=0;i<40;++i)cycle(0x10);
    assert(pwmOutputs[12]<steeringCenterUs());
    for(int i=0;i<40;++i)cycle(0x18);
    assert(pwmOutputs[12]==steeringCenterUs());
    cycle(0x38); // Three adjacent hits remain valid.
    assert(automaticSteeringState().mode==AutomaticSteeringMode::Line);
    cycle(0x78);
    assert(automaticSteeringState().mode==AutomaticSteeringMode::Fault && pwmOutputs[12]==steeringCenterUs());
    assert(std::string(automaticSteeringState().reason)=="line_too_wide");
    feed("N0\n@K180,120,.160,600,-1,503\n"); cycle(0x18);feed("N1\n");
    for(int i=0;i<40;++i)cycle(0x01);
    assert(pwmOutputs[12]==steeringCenterUs()-180);
    feed("N0\n");
    cycle(0); feed("N1\n");
    assert(automaticSteeringState().mode==AutomaticSteeringMode::Line);
    assert(std::string(automaticSteeringState().reason)=="line_waiting");
    clockMs+=101; updateAutomaticSteering();
    assert(std::string(automaticSteeringState().reason)=="line_stale");
    feed("N0\n");
    // Reproduce rejected arming while UART is full: Off status must be retried.
    Serial.output.clear(); Serial.capacity = 0;
    cycle(0x0f); // Four hits reject arming, remains Off.
    Serial.output.clear(); // Ignore unrelated DRIVE diagnostics from the preceding control tick.
    feed("N1,42\n");
    assert(automaticSteeringState().mode == AutomaticSteeringMode::Off);
    assert(std::string(automaticSteeringState().reason) == "line_too_wide");
    assert(Serial.output.empty());
    Serial.capacity = 128;
    pollSerialCommands(); publishAutomaticSteering();
    assert(Serial.output.find("[FOLLOW_ACK] id=42 action=1 ok=0") != std::string::npos);
    assert(Serial.output.find("mode=OFF") != std::string::npos);
    assert(Serial.output.find("reason=line_too_wide") != std::string::npos);
}
