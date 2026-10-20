#pragma once
#include <Arduino.h>

namespace SteeringConfig {
constexpr int PIN = 12;
constexpr int CENTER_US = 1500;
constexpr int LOW_US = 1450;
constexpr int HIGH_US = 1550;
}

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
