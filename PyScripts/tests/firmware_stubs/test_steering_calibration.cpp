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

int main(int argc, char** argv) {
    if (argc > 1) {
        const bool valid = std::string(argv[1]) == "saved";
        fakePreferences()["steering/center_us"] = valid ? 1575 : 999;
        initSteeringCalibration();
        assert(pwmOutputs.empty());  // Reading calibration at boot must not energize the servo.
        assert(steeringCenterUs() == (valid ? 1575 : 1500));
        beginSteeringDebug();
        assert(pwmOutputs[12] == (valid ? 1575 : 1500));
        return 0;
    }
    initSteeringCalibration();
    assert(steeringLowUs() == 1200 && steeringHighUs() == 1800);
    assert(!saveCurrentSteeringCenter());
    beginSteeringDebug();
    assert(setSteeringDebugPulse(1200) && pwmOutputs[12] == 1200);
    assert(!saveCurrentSteeringCenter());  // Cannot preserve +/-300 around 1200 inside 1000..2000.
    assert(steeringCenterUs() == 1500);
    assert(setSteeringDebugPulse(1640));
    assert(saveCurrentSteeringCenter());
    assert(pwmOutputs[12] == 1640);  // Saving the centre must not jump back to 1500.
    assert(fakePreferences()["steering/center_us"] == 1640);
    assert(steeringLowUs() == 1340 && steeringCenterUs() == 1640 && steeringHighUs() == 1940);
    assert(setSteeringDebugPulse(1900) && pwmOutputs[12] == 1900);  // No old attach-range clipping.
    assert(!setSteeringDebugPulse(1941));
    assert(setSteeringDebugPulse(1600));
    fakePreferenceFailure() = true;
    assert(!saveCurrentSteeringCenter());
    assert(steeringCenterUs() == 1640 && fakePreferences()["steering/center_us"] == 1640);
    centerSteeringOutput();
    assert(pwmOutputs[12] == 1640);
    endSteeringDebug();
    assert(pwmOutputs[12] == 1640);
}
