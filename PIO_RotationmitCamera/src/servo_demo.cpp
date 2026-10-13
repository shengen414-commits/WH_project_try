// Standalone D12 steering test. Select the PlatformIO "servo_demo" environment.
// Servo power remains on receiver/BEC; ESP32, receiver, ESC and servo share GND.
#include <Arduino.h>
#include <ESP32Servo.h>

namespace {
constexpr int STEERING_PIN = 12;  // User's actual wiring: D12 = GPIO12.
constexpr int ESC_PIN = 14;       // Existing ESC signal wiring.
constexpr int CENTER_US = 1500;
constexpr int LOW_US = 1450;      // Small initial travel; physical direction depends on installation.
constexpr int HIGH_US = 1550;
constexpr unsigned long HOLD_MS = 800;
Servo steering;
Servo neutralESC;
bool ready = false;
bool autoRunning = false;
unsigned int autoStep = 0;
unsigned long stepStartedAt = 0;
const int testSteps[] = {LOW_US, CENTER_US, HIGH_US, CENTER_US};

void setSteering(int pulseUs) {
    steering.writeMicroseconds(pulseUs);
    Serial.printf("[SERVO] GPIO%d: %d us\n", STEERING_PIN, pulseUs);
}

void help() {
    Serial.println("[HELP] C=center 1500; L=1450; R=1550; A=one small sweep; X/E=cancel and center; ?=help");
    Serial.println("[NOTE] L/R labels are nominal; verify actual wheel direction. ESC stays at 1500 us.");
}

void handleCommand(char command) {
    if (command >= 'a' && command <= 'z') command -= 'a' - 'A';
    if (command == '?') { help(); return; }
    if (!ready) return;
    switch (command) {
    case 'C': case 'X': case 'E':
        autoRunning = false;
        setSteering(CENTER_US);
        break;
    case 'L': case 'R':
        autoRunning = false;
        setSteering(command == 'L' ? LOW_US : HIGH_US);
        break;
    case 'A':
        autoRunning = true;
        autoStep = 0;
        stepStartedAt = millis();
        setSteering(testSteps[0]);
        break;
    default:
        break;  // Ignore line endings and throttle commands; this demo never drives.
    }
}
}

void setup() {
    Serial.begin(115200);
    ESP32PWM::allocateTimer(0);
    // Establish neutral ESC output first, including while testing steering.
    neutralESC.setPeriodHertz(50);
    neutralESC.attach(ESC_PIN, 1000, 2000);
    if (neutralESC.attached()) neutralESC.writeMicroseconds(1500);
    steering.setPeriodHertz(50);
    steering.attach(STEERING_PIN, LOW_US, HIGH_US);
    ready = neutralESC.attached() && steering.attached();
    if (!ready) {
        Serial.println("[ERROR] PWM attachment failed; steering demo disabled.");
        if (steering.attached()) steering.writeMicroseconds(CENTER_US);
        return;
    }
    setSteering(CENTER_US);  // No automatic sweep on power-up.
    Serial.println("[READY] Standalone steering demo; D14 ESC held neutral, D12 steering enabled.");
    help();
}

void loop() {
    for (unsigned int i = 0; i < 64 && Serial.available() > 0; ++i)
        handleCommand(Serial.read());
    if (ready && autoRunning && millis() - stepStartedAt >= HOLD_MS) {
        stepStartedAt = millis();
        if (++autoStep < sizeof(testSteps) / sizeof(testSteps[0])) {
            setSteering(testSteps[autoStep]);
        } else {
            autoRunning = false;
            Serial.println("[DONE] Sweep complete; steering centered.");
        }
    }
}
