#pragma once
#include <map>
#include "Arduino.h"
extern std::map<int, int> pwmOutputs;
extern bool servoAttachSucceeds;
class Servo {
    int pin = -1;
    int low = 1000;
    int high = 2000;
public:
    void setPeriodHertz(int) {}
    void attach(int gpio, int minUs, int maxUs) {
        if (servoAttachSucceeds) pin = gpio;
        low = minUs;
        high = maxUs;
    }
    bool attached() const { return pin >= 0; }
    void writeMicroseconds(int us) { if (attached()) pwmOutputs[pin] = constrain(us, low, high); }
};
struct ESP32PWM { static void allocateTimer(int) {} };
