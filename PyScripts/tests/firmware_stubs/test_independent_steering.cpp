#include <cassert>
#include <cmath>
#include <map>
#include <string>
#include "Arduino.h"
#include "esc_control.h"
#include "steering_control.h"
#include "automatic_steering.h"
#include "independent_steering.h"
#include "imu_sensor.h"
#include "line_sensor.h"

FakeSerial Serial;
bool servoAttachSucceeds=true;
std::map<int,int> pwmOutputs;
unsigned long clockMs=10000;
unsigned long millis(){return clockMs;}
unsigned long micros(){return clockMs*1000;}
bool rcHigh=false;
int digitalRead(int){return rcHigh?HIGH:LOW;}
extern void rcInterrupt();
bool readLineSensorFrame(LineSensorFrame&){return false;}
bool imuActive=false,imuValid=true,calibrating=false;
MPU6050Sample sample={};
void beginMPU6050Sensor(){imuActive=true;sample.calibrated=false;calibrating=false;}
void endMPU6050Sensor(){imuActive=false;}
void handleMPU6050SensorCommand(char c){if(c=='Z')calibrating=true;}
bool readMPU6050Sample(MPU6050Sample& out){out=sample;return imuActive&&imuValid&&uint32_t(clockMs-sample.timeMs)<=100;}
MPU6050SensorStatus mpu6050SensorStatus(){return {imuActive,imuValid,calibrating,0,"test"};}
void step(int count=1){for(int i=0;i<count;++i){clockMs+=10;sample.timeMs=clockMs;updateESC();updateAutomaticSteering();updateIndependentSteering();}}
void packet(const char* p){assert(handleIndependentSteeringPacket(p));}

int main(){
    initESC();sample.azG=-1;sample.valid=true;
    Serial.capacity=0;packet("A1,2,100,80,400,1");
    assert(independentSteeringBlocking()&&pwmOutputs[12]==1500);
    assert(Serial.output.find("ASSIST_ACK")==std::string::npos);
    Serial.capacity=128;step();
    assert(Serial.output.find("[ASSIST_ACK] id=100 action=1 source=2 ok=1 lim=80 slew=400 dir=1")!=std::string::npos);
    packet("A2,100,1,0.5");step(10);
    assert(pwmOutputs[12]==1540); // Shared gateway slew, limit and direction.
    packet("A2,99,2,-1");packet("A2,100,1,-1");packet("A2,100,2,nan");
    step(16);assert(pwmOutputs[12]==1500); // Wrong session, duplicate, NaN never refresh watchdog.
    setESCThrottle(1800);updateESC();assert(pwmOutputs[14]==1500);
    packet("A2,100,3,1");step();assert(pwmOutputs[12]==1500); // Fault is latched.
    packet("A0,99");assert(independentSteeringBlocking());
    packet("A0,100");assert(!independentSteeringBlocking());
    packet("A1,2,101,80,400,-1");packet("A2,101,1,1");step(10);
    assert(pwmOutputs[12]==1460);
    packet("A3,101");assert(pwmOutputs[12]==1500);
    stopAutomaticSteering();assert(!independentSteeringBlocking());
    packet("A1,1,102,80,400,1");assert(imuActive);
    sample.correctedGzDps=0;step();assert(calibrating);
    sample.calibrated=true;calibrating=false;step();
    packet("A2,102,1,.5");sample.correctedGzDps=10;Serial.output.clear();step(10);
    assert(pwmOutputs[12]==1540);
    assert(Serial.output.find(",-10.000")!=std::string::npos); // Z down: positive sensor rate means negative left-positive rate.
    sample.azG=1;step();assert(pwmOutputs[12]==1500);
    setESCThrottle(1800);updateESC();assert(pwmOutputs[14]==1500);
    packet("A0,102");assert(!imuActive);
    sample.azG=-1;packet("A1,1,103,80,400,1");
    step();assert(calibrating);calibrating=false;sample.calibrated=false;step();
    setESCThrottle(1800);updateESC();assert(pwmOutputs[14]==1500); // Rejected calibration never arms propulsion.
    packet("A0,103");
    packet("A1,2,104,80,400,1");packet("A2,104,1,1");step(4);
    stopAutomaticSteering();assert(!independentSteeringBlocking()&&pwmOutputs[12]==1500);
    packet("A1,2,105,80,400,1");packet("A2,105,1,1");step(3);
    for(int i=0;i<4;++i){rcHigh=true;rcInterrupt();clockMs+=2;rcHigh=false;rcInterrupt();clockMs+=18;updateESC();updateIndependentSteering();}
    assert(!independentSteeringBlocking()&&pwmOutputs[12]==1500&&pwmOutputs[14]==2000);
}

MPU6050CalibrationStatus mpu6050CalibrationStatus(){return {0,0,0,0,1,"zero_timeout"};}
