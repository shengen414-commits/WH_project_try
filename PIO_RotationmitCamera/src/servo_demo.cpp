// Optional isolated test, sharing the normal firmware's steering commands.
#include <Arduino.h>
#include <ESP32Servo.h>
#include "steering_control.h"

namespace { Servo neutralESC; }

void setup() {
    Serial.begin(115200);
    ESP32PWM::allocateTimer(0);
    neutralESC.setPeriodHertz(50);
    neutralESC.attach(14, 1000, 2000);
    if (!neutralESC.attached()) {
        Serial.println("[SERVO] ERROR: ESC PWM attach failed; demo disabled");
        return;
    }
    neutralESC.writeMicroseconds(1500);
    beginSteeringDebug();
}

void loop() {
    if (!neutralESC.attached()) return;
    for (unsigned int i = 0; i < 64 && Serial.available() > 0; ++i) {
        char value = Serial.read();
        if (value >= 'a' && value <= 'z') value -= 'a' - 'A';
        if (value == 'V') beginSteeringDebug();
        else if (value == 'R') endSteeringDebug();
        else handleSteeringCommand(value);
    }
    updateSteering();
    publishSteeringDebug();
}
