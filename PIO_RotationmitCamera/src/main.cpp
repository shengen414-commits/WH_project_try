#include <Arduino.h>
#include "encoder.h"
#include "esc_control.h"
#include "serial_transport.h"
#include "line_sensor.h"

void setup() {
    Serial.begin(115200);
    initEncoder();
    initESC();
    if (initLineSensor()) {
        Serial.println("[LINE_READY] Eight-channel digital sensor enabled");
    } else {
        Serial.println("[LINE_ERROR] Invalid pins: AD0/AD1/AD2 require output-capable GPIO; GPIO35 is input-only");
    }
    Serial.println("[READY] Encoder stream v1; storage on Orange Pi");
    Serial.println("[HELP] L: line debug 5 Hz; G: line capture 100 Hz; R: normal (ENC paused in L/G)");
}

void loop() {
    pollSerialCommands();
    updateESC();
    publishEncoder();
    updateLineSensor();
    publishLineSensor();
}
