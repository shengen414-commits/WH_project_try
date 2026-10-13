// ---------------------------------------------------------
// steering_gyro —— 陀螺仪横摆角速度内环 + 三种 r_ref 来源
//
// 内环:读 MPU6050 陀螺 Z -> 低通 -> yaw-rate PID -> 舵机 PWM(200Hz)
// 外环来源(r_ref,由模式决定):
//   MODE_MANUAL(0): 串口手动 'R <deg/s>' 或 PC 摄像头流式下发 R(默认)
//   MODE_RC(1)    : RC 接收机转向通道 CH1 -> 目标横摆角速度(手动模式)
//   MODE_GRAY(2)  : 亚博 8 路灰度 -> 横向偏差 -> 目标横摆角速度(自动)
//
// 串口命令(115200):
//   R <deg/s>  设定手动目标横摆角速度(0=直线,静默下发,可被摄像头高频调用)
//   M <0/1/2>  切换模式(0 手动/摄像头, 1 RC, 2 灰度)
//   P/I/D <值> PID 增益;  G <值> 陀螺增益;  L <值> 灰度横向增益
//   S          使能/失能转向;  C 重新标定陀螺零偏;  ? 帮助
//
// 接线(请核对):
//   舵机信号 -> GPIO13     RC 转向 CH1 -> GPIO26
//   MPU6050  -> SDA=21, SCL=22(AD0 接地)    灰度 -> I2C 同一条总线(地址 0x12)
// ---------------------------------------------------------

#include <Arduino.h>
#include "imu.h"
#include "steering_control.h"
#include "rc_steering.h"
#include "grayscale.h"

#define SERVO_PIN      13        // 舵机信号(DS3120)
#define RC_STEER_PIN   26        // RC 接收机转向通道信号
#define LOOP_US        5000      // 内环周期 5ms = 200Hz
#define TELEMETRY_US   50000     // 遥测周期 50ms = 20Hz

MPU6050Gyro     imu;
SteeringControl steering;
RCSteering      rcSteering;
Grayscale       grayscale;

// 增益(保守起步)
float kp = 3.0f, ki = 0.0f, kd = 0.0f, kg = 1.0f;
float kLat = 30.0f;              // 灰度横向偏差 -> 横摆角速度 (deg/s per channel)

enum Mode { MODE_MANUAL = 0, MODE_RC = 1, MODE_GRAY = 2 };
Mode  mode = MODE_MANUAL;
bool  enabled = false;           // 默认失能,发 'S' 才上舵机
float manualRRef = 0.0f;

void printHelp() {
    Serial.println(F("Commands:"));
    Serial.println(F("  R <deg/s>  manual target yaw rate (0=straight)"));
    Serial.println(F("  M <0/1/2>  mode: 0 manual/camera, 1 RC, 2 grayscale"));
    Serial.println(F("  P <kp>  I <ki>  D <kd>  G <kg>  L <kLat>"));
    Serial.println(F("  S  toggle enable/disable"));
    Serial.println(F("  C  re-calibrate gyro bias"));
    Serial.println(F("  ?  help"));
}

void handleSerial() {
    if (Serial.available() <= 0) return;
    char cmd = Serial.read();
    switch (cmd) {
        case 'R': case 'r':
            manualRRef = Serial.parseFloat();           // 静默,遥测已显示 r_ref
            break;
        case 'M': case 'm': {
            int m = Serial.parseInt();
            if (m >= MODE_MANUAL && m <= MODE_GRAY) mode = (Mode)m;
            const char* s = mode == MODE_RC ? "RC" : mode == MODE_GRAY ? "GRAY" : "MANUAL/CAMERA";
            Serial.printf("mode = %d (%s)\n", (int)mode, s);
            break;
        }
        case 'P': case 'p': kp = Serial.parseFloat(); steering.setTunings(kp, ki, kd);
            Serial.printf("Kp=%.3f\n", kp); break;
        case 'I': case 'i': ki = Serial.parseFloat(); steering.setTunings(kp, ki, kd);
            Serial.printf("Ki=%.3f\n", ki); break;
        case 'D': case 'd': kd = Serial.parseFloat(); steering.setTunings(kp, ki, kd);
            Serial.printf("Kd=%.3f\n", kd); break;
        case 'G': case 'g': kg = Serial.parseFloat(); steering.setGyroGain(kg);
            Serial.printf("Kg=%.3f\n", kg); break;
        case 'L': case 'l': kLat = Serial.parseFloat(); grayscale.setGain(kLat);
            Serial.printf("kLat=%.3f\n", kLat); break;
        case 'S': case 's':
            enabled = !enabled;
            Serial.printf("steering %s\n", enabled ? "ENABLED" : "DISABLED (servo at center)");
            break;
        case 'C': case 'c':
            Serial.println(F("calibrating gyro bias (keep the car still)..."));
            imu.calibrate();
            Serial.println(F("done"));
            break;
        case '?': printHelp(); break;
        default: break;
    }
}

float currentTargetYawRate() {
    switch (mode) {
        case MODE_RC:   return rcSteering.targetYawRate();
        case MODE_GRAY: return grayscale.targetYawRate();
        default:        return manualRRef;              // 手动 / 摄像头流式
    }
}

void setup() {
    Serial.begin(115200);
    Serial.setTimeout(20);
    delay(300);
    Serial.println(F("\n[steering_gyro] boot"));

    if (!imu.begin())
        Serial.println(F("[FATAL] MPU6050 not found (check SDA=21, SCL=22, AD0=GND)"));
    Serial.println(F("calibrating gyro bias (keep the car still)..."));
    imu.calibrate();

    steering.begin(SERVO_PIN, kp, ki, kd, kg);
    rcSteering.begin(RC_STEER_PIN, 180.0f, 1500);
    grayscale.begin(kLat, 120.0f, 0x12);

    printHelp();
}

unsigned long lastLoopUs = 0;
unsigned long lastTelemetryUs = 0;

void loop() {
    handleSerial();

    unsigned long nowUs = micros();

    if (nowUs - lastLoopUs >= LOOP_US) {
        lastLoopUs = nowUs;
        imu.update();                 // 读陀螺(非阻塞)
        rcSteering.update();          // 更新 RC 脉宽/断连状态(非阻塞)
        grayscale.update();           // 非阻塞状态机读取灰度

        steering.setTargetYawRate(currentTargetYawRate());
        if (enabled) steering.update(imu.yawRate());
        else         steering.disable();
    }

    if (nowUs - lastTelemetryUs >= TELEMETRY_US) {
        lastTelemetryUs = nowUs;
        // CSV: mode, r_ref, r_meas, lateral_error, servo_pwm
        Serial.printf("%d, %.1f, %.1f, %.2f, %d\n",
                      (int)mode, steering.targetYawRate(), imu.yawRate(),
                      grayscale.lateralError(), steering.servoPwm());
    }
}
