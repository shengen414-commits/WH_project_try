#include <cassert>
#include <utility>
#include <vector>
#include "Arduino.h"
#include "serial_transport.h"
#include "imu_sensor.h"
#include "line_sensor.h"
#include "automatic_steering.h"

FakeSerial Serial;
unsigned long clockMs = 0;
std::vector<std::pair<char, int>> commands;
bool diagnosticsMuted = false;
bool neutralHeld = false;
bool steeringDebug = false;
int steeringPulse = 1500;
int steeringMiddle = 1500;
bool steeringOutputReady() { return steeringDebug; }
int steeringTargetUs() { return steeringPulse; }
int steeringLowUs() { return steeringMiddle - 300; }
int steeringCenterUs() { return steeringMiddle; }
int steeringHighUs() { return steeringMiddle + 300; }
bool saveCurrentSteeringCenter() {
    if (!steeringDebug || steeringPulse < 1300 || steeringPulse > 1700) return false;
    steeringMiddle = steeringPulse;
    return true;
}
bool setSteeringDebugPulse(int value) {
    if (!steeringDebug || value < steeringLowUs() || value > steeringHighUs()) return false;
    steeringPulse = value;
    return true;
}
std::string steeringCommands;
int autoStarts = 0, yawStarts = 0;
std::string autoConfig;
void stopAutomaticSteering(bool) {}
bool startLineFollowing() { ++autoStarts; return true; }
bool startExternalYawControl() { ++yawStarts; return false; }
bool handleAutomaticSteeringConfig(const char* value) { autoConfig = value; return true; }
AutomaticSteeringState automaticSteeringState() {
    return {AutomaticSteeringMode::Off, 0, 1500, 24, false, "armed", LineControlAlgorithm::Basic, 1, 1500};
}
void setESCSteeringInhibit(bool) {}
void centerSteeringOutput() {}
bool mpuDebug = false;
std::string mpuCommands;
void beginMPU6050Debug() { mpuDebug = true; }
void endMPU6050Debug() { mpuDebug = false; }
void handleMPU6050DebugCommand(char value) { mpuCommands += value; }
void setESCDebugState(bool mute, bool neutral) { diagnosticsMuted = mute; neutralHeld = neutral; }
void beginSteeringDebug() { steeringDebug = true; steeringPulse = steeringMiddle; }
void endSteeringDebug() { steeringDebug = false; steeringPulse = steeringMiddle; }
void handleSteeringCommand(char value) { steeringCommands += value; }
unsigned long millis() { return clockMs; }
long readEncoderPosition() { return -42; }
LineSensorFrame sensorFrame = {0, 130, 24, 24};
bool readLineSensorFrame(LineSensorFrame &frame) { frame = sensorFrame; return true; }
void handleESCCommand(char cmd, int pwm) {
    commands.emplace_back(cmd, pwm);
    if (cmd == 'E') Serial.input.clear();  // Existing ESC emergency handler clears queued commands.
}

void beginMPU6050Sensor() {}
void endMPU6050Sensor() {}
void handleMPU6050SensorCommand(char) {}
bool readMPU6050Sample(MPU6050Sample&) { return false; }
MPU6050SensorStatus mpu6050SensorStatus() { return {false, false, false, 0, "test"}; }
void feed(const std::string& input) {
    Serial.input += input;
    pollSerialCommands();
}

int main() {
    feed("T16");
    assert(commands.empty());
    feed("00\nB1800\r\n");
    assert(commands.size() == 2);
    assert(commands[0] == std::make_pair('T', 1600));
    assert(commands[1] == std::make_pair('B', 1800));
    feed("T999\nT2001\nT16000\nT-100\nT15x0\n");
    assert(commands.size() == 2);  // Invalid values cannot move the motor.
    feed("T17");
    clockMs = 101;
    feed("00\n");
    assert(commands.size() == 2);  // Partial commands expire.
    feed("T19E\nT2000\n");
    assert(commands.size() == 3 && commands.back().first == 'E');
    feed("t1500\n");
    assert(commands.back() == std::make_pair('T', 1500));

    clockMs = 110;
    publishEncoder();
    assert(Serial.output == "[ENC],0,110,-42\n");
    publishEncoder();
    assert(Serial.output == "[ENC],0,110,-42\n");
    Serial.capacity = 0;
    clockMs = 120;
    publishEncoder();  // Full UART skips one sample, not a blocking write.
    Serial.capacity = 128;
    clockMs = 130;
    publishEncoder();
    assert(Serial.output == "[ENC],0,110,-42\n[ENC],2,130,-42\n");

    Serial.output.clear();
    feed("L\n");
    assert(diagnosticsMuted && !neutralHeld);
    publishEncoder();
    assert(Serial.output.empty());
    publishLineSensor();
    assert(Serial.output == "[LINE_DBG] raw=00011000 line=00011000\n");
    Serial.output.clear();
    ++sensorFrame.sequence;
    clockMs = 140;
    publishLineSensor();
    assert(Serial.output.empty());  // Human debug output is limited to 5 Hz.
    feed("G\n");
    publishEncoder();
    assert(Serial.output.empty());
    publishLineSensor();
    assert(Serial.output == "[LINE],1,130,24,24\n");
    Serial.output.clear();
    feed("R\n");
    publishEncoder();
    assert(Serial.output == "[ENC],3,140,-42\n");

    Serial.output.clear();
    feed("v\ncjkaX\nT1900\nB1800\n");
    assert(diagnosticsMuted && neutralHeld && steeringDebug);
    const auto commandsBeforeDebug = commands.size();
    assert(commandsBeforeDebug == 4);  // No throttle/boost reaches ESC in steering mode.
    assert(steeringCommands == "\ncjkaX\n");
    clockMs = 150;
    ++sensorFrame.sequence;
    publishEncoder();
    publishLineSensor();
    assert(Serial.output.empty());
    feed("E");
    assert(steeringCommands.back() == 'E' && neutralHeld);
    feed("l");
    assert(!steeringDebug && diagnosticsMuted && !neutralHeld);
    feed("r");
    assert(!diagnosticsMuted && !neutralHeld);
    steeringCommands.clear();
    feed("CJKAX?");
    assert(steeringCommands.empty());  // Steering actions cannot run outside V mode.
    feed("T19V\n");
    assert(steeringDebug && neutralHeld);  // Mode change discards partial throttle.
    feed("R\nT1600\n");
    assert(commands.back() == std::make_pair('T', 1600));

    Serial.output.clear();
    const auto beforeMPU = commands.size();
    feed("m\nz\nT1900\nB1800\n");
    assert(mpuDebug && neutralHeld && diagnosticsMuted);
    assert(commands.size() == beforeMPU);
    clockMs += 10;
    ++sensorFrame.sequence;
    publishEncoder(); publishLineSensor();
    assert(Serial.output.empty());
    assert(mpuCommands.find('z') != std::string::npos);
    feed("E");
    assert(mpuCommands.back() == 'E' && mpuDebug);
    feed("R\n");
    assert(!mpuDebug && !neutralHeld && serialNormalMode());

    Serial.output.clear();
    Serial.capacity = 0;
    const int beforeTaggedStart = autoStarts;
    feed("N1,4294967295\n");
    assert(autoStarts == beforeTaggedStart + 1);
    assert(Serial.output.empty()); // Busy UART must not lose the command result.
    Serial.capacity = 128;
    pollSerialCommands();
    assert(Serial.output.find("[FOLLOW_ACK] id=4294967295 action=1 ok=1") != std::string::npos);
    Serial.output.clear();
    feed("N1,4294967296\nN1,,12\nN1,\n");
    assert(autoStarts == beforeTaggedStart + 1); // Reject overflow and malformed ids.
    assert(Serial.output.empty());

    Serial.output.clear();
    feed("S1505,30\n");
    assert(steeringPulse == 1500 && !steeringDebug);
    assert(Serial.output.find("id=30 ok=0") != std::string::npos);
    Serial.output.clear();
    feed("V\nS0000,4294967295\n");
    assert(steeringDebug && neutralHeld);
    assert(Serial.output.find("id=4294967295 ok=1 target=1500") != std::string::npos);
    feed("S15");
    feed("05,31\n");
    assert(steeringPulse == 1505);
    feed("S2000,32\n");
    assert(steeringPulse == 1505 && Serial.output.find("id=32 ok=0") != std::string::npos);
    Serial.output.clear();
    feed("S1500,4294967296\nS1500,0\nS1500,,3\n");
    assert(Serial.output.empty() && steeringPulse == 1505);
    Serial.capacity = 0;
    feed("S1510,33\n");
    assert(steeringPulse == 1510 && Serial.output.empty());
    Serial.capacity = 128;
    pollSerialCommands();
    assert(Serial.output.find("id=33 ok=1 target=1510") != std::string::npos);
    feed("S0000,34\n");
    assert(steeringPulse == 1500);
    feed("S0001,35\n");
    assert(!steeringDebug && !neutralHeld && serialNormalMode());
    feed("@S\n");
    assert(autoConfig == "S");  // The manual packet prefix must not steal @S configuration.

    feed("V\nS1540,36\nS0002,37\n");
    assert(steeringMiddle == 1540 && steeringPulse == 1540);
    assert(Serial.output.find("id=37 ok=1 target=1540 low=1240 mid=1540 high=1840") != std::string::npos);
    feed("S1800,38\nS0002,39\n");
    assert(steeringMiddle == 1540 && steeringPulse == 1800);
    assert(Serial.output.find("id=39 ok=0") != std::string::npos);
    feed("S0000,40\nS0001,41\n");
    assert(steeringPulse == 1540 && serialNormalMode());

    Serial.input = std::string(100, '?');
    pollSerialCommands();
    assert(Serial.input.size() == 36);  // Work per loop stays bounded.
}
