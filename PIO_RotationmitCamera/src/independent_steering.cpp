#include "independent_steering.h"
#include "automatic_steering.h"
#include "steering_control.h"
#include "esc_control.h"
#include "imu_sensor.h"
#include "encoder.h"
#include "line_position.h"
#include "fusion_control.h"
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace {
bool owned=false, failed=false, imuReady=false, zeroStarted=false, targetValid=false;
bool statusPending=false,customTravel=false;
bool fusionConfigured=false, fusionReady=false;
uint32_t visionSequence=0;
uint32_t motionStarted=0;
bool monitoringMotion=false;
float speedRequest=0, speedIntegral=0;
FusionControl fusion;
FusionControl::Output fused = {};
int outputLow=1200,outputHigh=1800;
uint32_t session=0, sequence=0, received=0, started=0, updated=0, published=0, imuPublished=0, imuAt=0, zeroPublished=0;
int source=0, direction=1, amplitude=100, slew=400;
float output=1500, target=0, heading=0, rate=0;
const char* reason="disabled";
char pendingAck[160]; int ackLength=0;
constexpr uint32_t WATCHDOG_MS=250;
bool usesImu() { return source==1 || source==3; }
bool newer(uint32_t next,uint32_t previous) { return int32_t(next-previous)>0; }
float clamp(float v,float low,float high) { return v<low?low:v>high?high:v; }
bool verifiedFrame(const char* packet, char (&body)[96]) {
    const char* suffix=std::strrchr(packet,',');
    if (!suffix || suffix-packet>=int(sizeof(body)) || suffix[1]<'0' || suffix[1]>'9') return false;
    char* end;
    const unsigned long expected=std::strtoul(suffix+1,&end,10);
    if (*end || expected>0xffff) return false;
    uint16_t crc=0xffff;
    for (const char* p=packet;p<suffix;++p) {
        crc ^= uint16_t(uint8_t(*p))<<8;
        for (int i=0;i<8;++i) crc=uint16_t((crc<<1)^((crc&0x8000)?0x1021:0));
    }
    if (crc!=expected) return false;
    std::memcpy(body,packet,suffix-packet);
    body[suffix-packet]='\0';
    return true;
}
void flushAck() {
    if(ackLength>0 && Serial.availableForWrite()>=ackLength) {
        Serial.write((const uint8_t*)pendingAck,ackLength);ackLength=0;
    }
}
void acknowledge(uint32_t id,int action,bool ok,const char* rejection=nullptr) {
    const char* ackReason=rejection?rejection:reason;
    if(customTravel) {
        ackLength=snprintf(pendingAck,sizeof(pendingAck),"[ASSIST_ACK] id=%lu action=%d source=%d ok=%d lim=%d slew=%d dir=%d low=%d high=%d reason=%s\n",
            (unsigned long)id,action,source,ok,amplitude,slew,direction,outputLow,outputHigh,ackReason);
    } else {
        ackLength=snprintf(pendingAck,sizeof(pendingAck),"[ASSIST_ACK] id=%lu action=%d source=%d ok=%d lim=%d slew=%d dir=%d reason=%s\n",
            (unsigned long)id,action,source,ok,amplitude,slew,direction,ackReason);
    }
    if(ackLength>=(int)sizeof(pendingAck))ackLength=0;
    flushAck();
}
void fail(const char* why) {
    failed=true;reason=why;targetValid=false;target=0;
    speedRequest=0;
    output=steeringCenterUs();centerSteeringOutput();setESCSteeringInhibit(true);
}
void publish(uint32_t now) {
    if(statusPending || uint32_t(now-published)>=200) {
        char line[144];
        const bool ready = !failed && (source==3 ? fusionReady : source==2 || imuReady);
        int n=snprintf(line,sizeof(line),"[ASSIST] id=%lu source=%d active=%d fault=%d ready=%d pwm=%d mid=%d reason=%s\n",
            (unsigned long)session,source,owned,failed,ready,(int)std::lround(output),steeringCenterUs(),reason);
        if (source==3 && n>0 && n<110)
            n += snprintf(line+n-1,sizeof(line)-n+1," v=%.2f goal=%.2f esc=%d\n",
                          fused.speed,fused.targetSpeed,escSteeringState().pwm)-1;
        if(n>0 && n<(int)sizeof(line) && Serial.availableForWrite()>=n) {Serial.write((const uint8_t*)line,n);published=now;statusPending=false;}
    }
    if(owned && usesImu() && !imuReady && uint32_t(now-zeroPublished)>=200) {
        const auto zero=mpu6050CalibrationStatus(); char line[128], sensorReason[40];
        const auto sensor=mpu6050SensorStatus();
        const char* detail=zero.reason;
        if(!sensor.ready && sensor.message && std::sscanf(sensor.message,"ERROR=%39[^ ;]",sensorReason)==1)detail=sensorReason;
        const int n=snprintf(line,sizeof(line),"[ASSIST_ZERO] id=%lu n=%u reset=%u bias=%.2f sd=%.3f a=%.3f reason=%s\n",
            (unsigned long)session,zero.samples,zero.restarts,zero.meanZ,zero.gyroStd,zero.acceleration,detail);
        if(n>0 && n<(int)sizeof(line) && Serial.availableForWrite()>=n) {Serial.write((const uint8_t*)line,n);zeroPublished=now;}
    }
    if(owned && !failed && source==1 && imuReady && uint32_t(now-imuPublished)>=50) {
        char line[96];
        int n=snprintf(line,sizeof(line),"[ASSIST_IMU],%lu,%lu,%.3f,%.3f\n",
            (unsigned long)session,(unsigned long)imuAt,heading,rate);
        if(n>0 && n<(int)sizeof(line) && Serial.availableForWrite()>=n) {Serial.write((const uint8_t*)line,n);imuPublished=now;}
    }
}
}

bool independentSteeringBlocking(){return owned;}
void stopIndependentSteering(const char* why) {
    if(!owned)return;
    if(usesImu())endMPU6050Sensor();
    if(source==3)setESCAutonomous(false);
    owned=false;customTravel=false;failed=false;imuReady=false;targetValid=false;target=0;reason=why;
    output=steeringCenterUs();centerSteeringOutput();if(!escSteeringState().rcMode)setESCSteeringInhibit(false);
    published=millis()-200;statusPending=true;publish(millis());
}
bool handleIndependentSteeringPacket(const char* packet) {
    if(packet[0]!='A')return false;
    unsigned long id=0, seq=0; int action=-1, requestedSource=0, limit=0, speed=0, sign=0; float value=0;char extra;
    if(sscanf(packet+1,"%d,%lu %c",&action,&id,&extra)==2 && (action==0||action==3) && id>0 && id<=UINT32_MAX) {
        const bool ok=id==session && session>0;
        if(ok) {if(action==0)stopIndependentSteering();else if(owned)fail("host_sensor_invalid");}
        acknowledge(id,action,ok);return true;
    }
    int low=0,high=0;
    const bool extended=sscanf(packet+1,"%d,%d,%lu,%d,%d,%d,%d,%d %c",&action,&requestedSource,&id,&limit,&speed,&sign,&low,&high,&extra)==8;
    const bool legacy=!extended && sscanf(packet+1,"%d,%d,%lu,%d,%d,%d %c",&action,&requestedSource,&id,&limit,&speed,&sign,&extra)==6;
    if((extended||legacy) && action==1 && id>0 && id<=UINT32_MAX) {
        const auto drive=escSteeringState();
        const bool supported = requestedSource==1 || requestedSource==2 || (requestedSource==3 && extended);
        bool ok=!owned && supported && limit>=10 && limit<=(extended?400:300) &&
            (!extended || (low>=1100 && low<1500 && high>1500 && high<=1900)) && speed>=50 && speed<=1200 &&
            (sign==1||sign==-1) && !steeringDebugActive() && !drive.debugNeutral && !drive.emergency && !drive.rcMode && drive.pwm==1500 &&
            (automaticSteeringState().mode==AutomaticSteeringMode::Off||automaticSteeringState().mode==AutomaticSteeringMode::Fault);
        if(!ok && !owned) {
            if(!supported)reason="invalid_source";
            else if(limit<10 || limit>(extended?400:300) || speed<50 || speed>1200 || (sign!=1 && sign!=-1) ||
                    (extended && (low<1100 || low>=1500 || high<=1500 || high>1900)))reason="invalid_parameters";
            else if(steeringDebugActive() || drive.debugNeutral)reason="debug_mode_active";
            else if(drive.emergency)reason="emergency_active";
            else if(drive.rcMode)reason="rc_takeover";
            else if(drive.pwm!=1500)reason="throttle_not_neutral";
            else reason="other_controller_active";
        }
        if(ok && !attachSteeringOutput()){ok=false;reason="pwm_attach_failed";}
        if(ok) {
            stopAutomaticSteering();
            customTravel=extended;outputLow=extended?low:steeringLowUs();outputHigh=extended?high:steeringHighUs();
            source=requestedSource;session=id;sequence=0;target=0;targetValid=false;
            amplitude=limit;slew=speed;direction=sign;owned=true;failed=false;imuReady=false;zeroStarted=false;
            started=updated=received=millis();published=started-200;zeroPublished=started-200;imuPublished=started-50;heading=rate=0;
            output=steeringCenterUs();centerSteeringOutput();setESCSteeringInhibit(usesImu());
            reason=usesImu()?"imu_calibrating":"waiting_target";
            if(usesImu())beginMPU6050Sensor();
            if(source==3) {
                fusion = FusionControl{};
                fused = {};
                fusionConfigured=fusionReady=false;
                speedRequest=speedIntegral=0;
                monitoringMotion=false;
                visionSequence=0;
                setESCAutonomous(true);
            }
        }
        acknowledge(id,1,ok,!ok&&owned?"controller_owned":nullptr);return true;
    }
    if(sscanf(packet+1,"%d,%lu,%lu,%f %c",&action,&id,&seq,&value,&extra)==4 && action==2) {
        if(owned && !failed && source!=3 && id==session && seq>0 && seq<=UINT32_MAX && newer(seq,sequence) && std::isfinite(value) && std::fabs(value)<=1 &&
           (source==2||imuReady)) {
            sequence=seq;received=millis();target=value;targetValid=true;
        }
        return true; // Invalid, old, or wrong-session targets never refresh watchdog.
    }
    if (owned && source==3 && !failed) {
        char body[96];
        if (!verifiedFrame(packet,body)) return true;
        FusionControl::Geometry geometry{};
        if (sscanf(body+1,"6,%lu,%f,%f,%f,%f,%f %c",&id,&geometry.pitch,&geometry.lineX,
                   &geometry.lineY,&geometry.metresPerCount,&geometry.wheelbase,&extra)==6 && id==session) {
            if (!fusionConfigured) {
                fusionConfigured=fusion.configure(geometry);
                if (!fusionConfigured) fail("invalid_fusion_geometry");
            }
            return true;
        }
        if (sscanf(body+1,"5,%lu,%lu,%f %c",&id,&seq,&value,&extra)==3 && id==session &&
            seq>0 && seq<=UINT32_MAX && newer(seq,sequence) && std::isfinite(value) &&
            value>=0 && value<=FusionControl::maxSpeed()) {
            sequence=seq;
            received=millis();
            speedRequest=fusionReady ? value : 0;
            return true;
        }
        unsigned long stamp;
        float lateral, angle, confidence;
        if (sscanf(body+1,"4,%lu,%lu,%lu,%f,%f,%f %c",&id,&seq,&stamp,&lateral,&angle,&confidence,&extra)==6 &&
            id==session && seq>0 && seq<=UINT32_MAX && stamp<=UINT32_MAX && newer(seq,visionSequence) &&
            fusionConfigured && imuReady && fusion.vision(millis(),stamp,lateral,angle,confidence))
            visionSequence=seq;
    }
    return true; // Reserved packet family: no fallback to unrelated commands.
}
void updateIndependentSteering() {
    flushAck();const uint32_t now=millis();
    if(!owned){if(statusPending)publish(now);return;}
    const auto drive=escSteeringState();
    if(drive.rcMode){stopIndependentSteering("rc_takeover");return;}
    if(drive.emergency||drive.debugNeutral||steeringDebugActive()){stopIndependentSteering("interrupted");return;}
    if(failed){publish(now);return;}
    if(drive.pwm<1450){fail("reverse_not_supported");publish(now);return;}
    if(source==3 && escAutonomousStopRequested()) {stopIndependentSteering();return;}
    if(source==3 && uint32_t(now-received)>WATCHDOG_MS){fail("host_target_stale");publish(now);return;}
    if(usesImu()) {
        MPU6050Sample sample;const auto status=mpu6050SensorStatus();
        if(!imuReady) {
            if(uint32_t(now-started)>5000){fail("imu_zero_timeout");publish(now);return;}
            if(!status.ready && status.message && std::strncmp(status.message,"ERROR=",6)==0) {
                fail("imu_init_failed");publish(now);return;
            }
            if(readMPU6050Sample(sample)) {
                if(!zeroStarted){handleMPU6050SensorCommand('Z');zeroStarted=true;}
                else if(sample.calibrated && !status.calibrating) {
                    imuReady=true;imuAt=sample.timeMs;heading=rate=0;received=updated=now;published=now-200;
                    reason="waiting_target";
                    if(source!=3)setESCSteeringInhibit(false);
                } else if(!status.calibrating){fail(mpu6050CalibrationStatus().reason);publish(now);return;}
            }
            if(!imuReady){publish(now);return;}
        }
        if(!readMPU6050Sample(sample)){fail("imu_stale");publish(now);return;}
        if(sample.azG>-.7f||std::fabs(sample.axG)>.5f||std::fabs(sample.ayG)>.5f){fail("imu_mount_or_tilt");publish(now);return;}
        const uint32_t dt=uint32_t(sample.timeMs-imuAt);
        if(dt>100){fail("imu_gap");publish(now);return;}
        if(dt>0) {
            // Confirmed installation: sensor X forward, Y right, Z down.
            // Shared vehicle frame is X forward, Y left, Z up: [1,-2,-3].
            rate=-sample.correctedGzDps;heading+=rate*dt*.001f;imuAt=sample.timeMs;
            while(heading>180)heading-=360;
            while(heading<-180)heading+=360;
        }
    }
    if(uint32_t(now-received)>WATCHDOG_MS){fail("host_target_stale");publish(now);return;}
    const uint32_t dt=uint32_t(now-updated);
    if(dt>=10) {
        if(dt>100){fail("control_gap");publish(now);return;}
        updated=now;
        if(source==3) {
            LineSensorFrame frame;
            LinePosition line{};
            const bool lineFresh=readLineSensorFrame(frame) && now-frame.timeMs<=40;
            if(lineFresh) line=estimateLinePosition(frame);
            const float elapsed=dt*.001f;
            fused=fusion.update(now,readEncoderPosition(),rate*.0174532925f,elapsed,
                                lineFresh && line.valid(),line.centerChannel,speedRequest);
            if (!fused.routeValid) {
                if (fusionReady) fail("fusion_route_lost");
                else if (now-started>8000) fail("fusion_observation_timeout");
                else reason="waiting_fusion_observations";
                publish(now);
                return;
            }
            if (!fusionReady) {
                fusionReady=true;
                setESCSteeringInhibit(false);
                speedRequest=0;
            }
            if (fused.speed<-.15f || fused.speed>1.1f) {fail("fusion_speed_out_of_range");publish(now);return;}
            if (fused.targetSpeed<=.02f) {
                monitoringMotion=false;
                speedIntegral=0;
                writeESCAutonomous(1500);
            } else {
                const float error=fused.targetSpeed-fused.speed;
                speedIntegral=clamp(speedIntegral+18*error*elapsed,0,28);
                const int drivePwm=fused.speed>fused.targetSpeed+.08f ? 1500 :
                    (int)std::lround(clamp(1551+85*error+speedIntegral,1500,1640));
                if (drivePwm>1550 && fused.speed<.03f) {
                    if (!monitoringMotion) {monitoringMotion=true;motionStarted=now;}
                    if (now-motionStarted>1000) {fail("fusion_no_motion");publish(now);return;}
                } else monitoringMotion=false;
                writeESCAutonomous(drivePwm);
            }
            const float degrees=std::fabs(fused.angle)*57.2957795f;
            // Manual reference near centre: about 100 us at 9 degrees,
            // then another 50 us at 12 degrees, on either side.
            const float pulse=degrees <= 9 ? degrees * (100.f/9) : 100+(degrees-9)*(50.f/3);
            target=clamp(std::copysign(pulse,fused.angle)/amplitude,-1,1);
            targetValid=true;
        }
        const float desired=clamp(steeringCenterUs()+direction*target*amplitude,outputLow,outputHigh);
        output=clamp(desired,output-slew*dt*.001f,output+slew*dt*.001f);
        if(!(customTravel?writeIndependentSteeringPulse((int)std::lround(output),outputLow,outputHigh):writeAutomaticSteeringPulse((int)std::lround(output)))){fail("steering_output_failed");publish(now);return;}
        reason=source==3 && fused.degraded ? "fusion_limited" : targetValid?"tracking":"waiting_target";
    }
    publish(now);
}
