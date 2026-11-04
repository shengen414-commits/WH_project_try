#include <cassert>
#include <map>
#include <string>
#include "Arduino.h"
#include "Preferences.h"
#include "steering_control.h"

FakeSerial Serial;
bool servoAttachSucceeds = true;
std::map<int, int> pwmOutputs;
unsigned long millis() { return 10000; }

int main(int argc, char**) {
    fakePreferences()["steering/center_us"] = argc > 1 ? 1575 : 999;
    initSteeringCalibration();
    assert(pwmOutputs.empty() && steeringCenterUs() == 1500);
    assert(!configureSteeringTravel(1200, 1575, 1800));
    assert(!saveCurrentSteeringCenter());
    assert(!configureSteeringDebugRange(500,2500));
    beginSteeringDebug();
    assert(pwmOutputs[12] == 1500);
    assert(steeringLowUs()==1100 && steeringHighUs()==1900);
    assert(configureSteeringDebugRange(1100,1900));
    assert(pwmOutputs[12]==1500);
    assert(!configureSteeringDebugRange(500,2500));
    assert(!configureSteeringDebugRange(1200,1800));
    for (int pulse : {1100,1900}) {
        assert(setSteeringDebugPulse(pulse) && pwmOutputs[12]==pulse);
        assert(!saveCurrentSteeringCenter());
        assert(!configureSteeringDebugRange(500,2500));
        assert(pwmOutputs[12]==pulse);
    }
    assert(!setSteeringDebugPulse(1099) && !setSteeringDebugPulse(1901));
    endSteeringDebug();
    assert(pwmOutputs[12] == 1500 && steeringLowUs() == 1200 && steeringHighUs() == 1800);
    beginSteeringDebug();
    assert(steeringLowUs() == 1100 && steeringHighUs() == 1900);
    assert(!setSteeringDebugPulse(500));
}
