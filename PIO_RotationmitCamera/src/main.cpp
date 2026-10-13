#include <Arduino.h>
#include "encoder.h"
#include "esc_control.h"
#include "serial_transport.h"
#include "line_sensor.h"
#include "steering_control.h"
#include "automatic_steering.h"
#include "mpu6050_debug.h"

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
    Serial.println("[HELP] L: line debug; G: line capture; V: servo debug; M: MPU6050 debug; O: IMU 3D; R: normal; E: emergency stop");
    Serial.println("[FOLLOW_HELP] N1: line steering ON; N0: OFF; @P kp,ki,kd; @D +/-1; @S status (newline required)");
}

void loop() {
    pollSerialCommands();
    updateESC();
    updateSteering();
    publishEncoder();
    updateLineSensor();
    updateMPU6050Debug();
    updateAutomaticSteering();
    publishLineSensor();
    publishSteeringDebug();
    publishMPU6050Debug();
    if (serialNormalMode()) publishAutomaticSteering();
}
