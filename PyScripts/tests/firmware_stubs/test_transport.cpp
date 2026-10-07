#include <cassert>
#include <utility>
#include <vector>
#include "Arduino.h"
#include "serial_transport.h"
#include "line_sensor.h"

FakeSerial Serial;
unsigned long clockMs = 0;
std::vector<std::pair<char, int>> commands;
unsigned long millis() { return clockMs; }
long readEncoderPosition() { return -42; }
LineSensorFrame sensorFrame = {0, 130, 231, 24};
bool readLineSensorFrame(LineSensorFrame &frame) { frame = sensorFrame; return true; }
void handleESCCommand(char cmd, int pwm) {
    commands.emplace_back(cmd, pwm);
    if (cmd == 'E') Serial.input.clear();  // Existing ESC emergency handler clears queued commands.
}

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
    publishEncoder();
    assert(Serial.output.empty());
    publishLineSensor();
    assert(Serial.output == "[LINE_DBG] raw=11100111 line=00011000\n");
    Serial.output.clear();
    ++sensorFrame.sequence;
    clockMs = 140;
    publishLineSensor();
    assert(Serial.output.empty());  // Human debug output is limited to 5 Hz.
    feed("G\n");
    publishEncoder();
    assert(Serial.output.empty());
    publishLineSensor();
    assert(Serial.output == "[LINE],1,130,231,24\n");
    Serial.output.clear();
    feed("R\n");
    publishEncoder();
    assert(Serial.output == "[ENC],3,140,-42\n");

    Serial.input = std::string(100, '?');
    pollSerialCommands();
    assert(Serial.input.size() == 36);  // Work per loop stays bounded.
}
