#pragma once
#include <Arduino.h>

// Exclusive, session-tagged actuator gateway for host controllers. Sources are
// 1=IMU heading hold, 2=camera. Fusion will supply its own controller/profile.
bool independentSteeringBlocking();
void stopIndependentSteering(const char* reason = "disabled");
bool handleIndependentSteeringPacket(const char* packet);
void updateIndependentSteering();
