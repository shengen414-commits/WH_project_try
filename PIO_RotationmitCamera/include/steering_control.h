#pragma once
#include <Arduino.h>

namespace SteeringConfig {
constexpr int PIN = 12;
constexpr int CENTER_US = 1500;
constexpr int HALF_RANGE_US = 300;
constexpr int LOW_US = CENTER_US - HALF_RANGE_US;
constexpr int HIGH_US = CENTER_US + HALF_RANGE_US;
constexpr int DEBUG_LOW_US = 1100;
constexpr int DEBUG_HIGH_US = 1900;
constexpr int ABS_LOW_US = 500;
constexpr int ABS_HIGH_US = 2500;
constexpr int CENTER_LOW_US = CENTER_US;
constexpr int CENTER_HIGH_US = CENTER_US;
}

void initSteeringCalibration();
bool saveCurrentSteeringCenter();
void beginSteeringDebug();
void endSteeringDebug();
void handleSteeringCommand(char command);
void updateSteering();
void publishSteeringDebug(bool preciseCommands = false);
bool steeringDebugActive();
bool steeringOutputReady();
int steeringTargetUs();
bool setSteeringDebugPulse(int pulseUs);
bool expandSteeringDebugRange(bool lower);
bool configureSteeringDebugRange(int lowUs, int highUs);
bool attachSteeringOutput();
bool writeAutomaticSteeringPulse(int pulseUs);
// Only the session-checked independent gateway supplies these separate limits.
bool writeIndependentSteeringPulse(int pulseUs, int lowUs, int highUs);
void centerSteeringOutput();
bool configureSteeringTravel(int lowUs, int centerUs, int highUs);
int steeringCenterUs();
int steeringLowUs();
int steeringHighUs();
