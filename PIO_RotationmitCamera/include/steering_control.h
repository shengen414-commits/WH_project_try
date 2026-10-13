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
void publishSteeringDebug();
