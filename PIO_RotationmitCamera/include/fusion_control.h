#pragma once

#include <cmath>
#include <cstdint>

// Straight-path controller, in rear-axle coordinates (X forward, Y left).
// One owner calls it at the ESP32 control cadence. No allocation or I/O.
class FusionControl {
public:
    static constexpr float maxSpeed() { return .8f; }
    struct Geometry { float pitch, lineX, lineY, metresPerCount, wheelbase; };
    struct Output {
        float angle, speed, targetSpeed;
        bool routeValid, degraded;
    };

    bool configure(Geometry g) {
        if (!between(g.pitch,.001f,.1f) || !between(g.lineX,-2,3) ||
            !between(g.lineY,-1,1) || !between(std::fabs(g.metresPerCount),1e-7f,.02f) ||
            !between(g.wheelbase,.05f,1)) return false;
        geometry = g;
        configured = true;
        return true;
    }

    bool vision(uint32_t now, uint32_t stamp, float lateral, float heading, float confidence) {
        const uint32_t age = now - stamp;
        if (age > 150 || (visionCount && int32_t(stamp - visionAt) <= 0) ||
            !between(lateral,-.5f,.5f) || !between(heading,-.35f,.35f) ||
            !between(confidence,.6f,1.f) || !historySize) return false;
        const Pose* old = nullptr;
        for (unsigned i = 0; i < historySize; ++i) {
            const Pose& candidate = history[(next + historyCapacity - 1 - i) % historyCapacity];
            if (int32_t(stamp - candidate.at) >= 0) { old = &candidate; break; }
        }
        if (!old) return false;
        const float yawToPath = heading - old->yaw;
        const float aligned = lateral + std::sin(yawToPath)*(pose.x-old->x)
                                      + std::cos(yawToPath)*(pose.y-old->y);
        const float alignedHeading = heading + pose.yaw - old->yaw;
        if (visionCount && now-visionAt<=150 &&
            (std::fabs(aligned - errorY) > .15f || std::fabs(alignedHeading - errorHeading) > .3f))
            return false;
        const bool recovering = !visionCount || now-visionAt>150;
        const float weight = recovering ? .65f : .25f + .3f*confidence;
        errorY += weight*(aligned-errorY);
        errorHeading += weight*(alignedHeading-errorHeading);
        visionAt = stamp;
        visionCount = recovering ? 1 : 2;
        return true;
    }

    Output update(uint32_t at, int32_t count, float yawRate, float dt, bool lineValid, float channel,
                  float requestedSpeed) {
        if (!configured) return {};
        if (haveCount) {
            const int32_t delta = int32_t(uint32_t(count) - uint32_t(previousCount));
            const float measured = delta * geometry.metresPerCount / dt;
            velocity += (dt/(.035f+dt))*(measured-velocity);
        }
        previousCount = count;
        haveCount = true;
        const float dyaw = yawRate*dt;
        const float distance = velocity*dt;
        pose.x += distance*std::cos(pose.yaw+dyaw*.5f);
        pose.y += distance*std::sin(pose.yaw+dyaw*.5f);
        pose.yaw += dyaw;
        pose.at = at;
        history[next] = pose;
        next = (next + 1) % historyCapacity;
        if (historySize < historyCapacity) ++historySize;
        errorY += distance*std::sin(errorHeading+dyaw*.5f);
        errorHeading = std::remainder(errorHeading+dyaw,6.2831853f);
        const bool cameraFresh = visionCount>=2 && at-visionAt<=150;
        if (lineValid) {
            const float observedY = (channel-3.5f)*geometry.pitch - geometry.lineY
                                    - geometry.lineX*std::tan(clamp(errorHeading,-.45f,.45f));
            if (cameraFresh && std::fabs(observedY-errorY)>.12f) lineValid = false;
            else errorY += (cameraFresh ? .22f : .45f)*(observedY-errorY);
        }
        const bool valid = (cameraFresh || (everTracked && lineValid && at-visionAt<=500)) &&
                           std::fabs(errorHeading)<.45f && std::fabs(errorY)<.25f;
        everTracked = everTracked || (valid && cameraFresh && lineValid);
        const bool degraded = !(cameraFresh && lineValid);
        const float qualityLimit = degraded ? .25f : maxSpeed();
        const float limit = clamp(qualityLimit - 2.f*std::fabs(errorY) - .5f*std::fabs(errorHeading),0,qualityLimit);
        const float targetSpeed = valid ? clamp(requestedSpeed,0,limit) : 0;
        // Outer loop chooses a yaw demand. The local gyro damps deviation
        // from that demand. Use a bounded speed floor near standstill.
        const float headingForPath = lineValid && !cameraFresh ? 0.f : errorHeading;
        const float yawTarget = clamp(-4.f*errorY-1.7f*headingForPath,-.9f,.9f);
        const float speedFloor = std::fmax(.25f,velocity);
        const float angle = valid ? clamp(std::atan(geometry.wheelbase*yawTarget/speedFloor)
                                      + .12f*(yawTarget-yawRate),-.20f,.20f) : 0.f;
        return {angle,velocity,targetSpeed,valid && everTracked,degraded};
    }

private:
    static float clamp(float v,float lo,float hi) { return v<lo?lo:v>hi?hi:v; }
    static bool between(float v,float lo,float hi) { return std::isfinite(v)&&lo<=v&&v<=hi; }
    static constexpr unsigned historyCapacity = 24;
    struct Pose { uint32_t at; float x,y,yaw; };
    Pose history[historyCapacity] = {}, pose = {};
    Geometry geometry = {};
    unsigned historySize=0,next=0,visionCount=0;
    uint32_t visionAt=0;
    int32_t previousCount=0;
    float velocity=0,errorY=0,errorHeading=0;
    bool configured=false,haveCount=false,everTracked=false;
};
