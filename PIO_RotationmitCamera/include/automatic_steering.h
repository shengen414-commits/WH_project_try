#pragma once
#include <Arduino.h>
#include "line_controller.h"

enum class AutomaticSteeringMode { Off, Line, ExternalYaw, Fault };
struct AutomaticSteeringState {
    AutomaticSteeringMode mode;
    float lineError;  // normalized -1..1, CH1 negative and CH8 positive
    int pwm;
    uint8_t lineMask;
    bool gyroEnabled;
    const char* reason;
    LineControlAlgorithm algorithm;
    int direction;  // PWM calibration sign; +1 is provisional until wheel testing.
    float requestedPwm;
};

bool startLineFollowing();
bool startExternalYawControl();
void stopAutomaticSteering(bool resetDrive = true);
void updateAutomaticSteering();
void publishAutomaticSteering();
bool handleAutomaticSteeringConfig(const char* packet);  // contents after @
AutomaticSteeringState automaticSteeringState();

// Reserved adapters for an MPU6050 driver and camera/RC outer controller.
// These do not initialize hardware or open a second serial connection.
bool submitYawRateFeedback(float degreesPerSecond, uint32_t sampleTimeMs, bool valid);
bool submitTargetYawRate(float degreesPerSecond, uint32_t sampleTimeMs, bool valid);
