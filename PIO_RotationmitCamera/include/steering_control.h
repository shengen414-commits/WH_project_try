#pragma once
#include <Arduino.h>

namespace SteeringConfig {
constexpr int PIN = 12;
constexpr int CENTER_US = 1500;
constexpr int HALF_RANGE_US = 300;
constexpr int LOW_US = CENTER_US - HALF_RANGE_US;
constexpr int HIGH_US = CENTER_US + HALF_RANGE_US;
constexpr int ABS_LOW_US = 1000;
constexpr int ABS_HIGH_US = 2000;
constexpr int CENTER_LOW_US = ABS_LOW_US + HALF_RANGE_US;
constexpr int CENTER_HIGH_US = ABS_HIGH_US - HALF_RANGE_US;
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
bool attachSteeringOutput();
bool writeAutomaticSteeringPulse(int pulseUs);
void centerSteeringOutput();
bool configureSteeringTravel(int lowUs, int centerUs, int highUs);
int steeringCenterUs();
int steeringLowUs();
int steeringHighUs();
