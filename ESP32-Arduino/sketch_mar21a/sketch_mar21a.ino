#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>
#include <Wire.h>
#include "BluetoothSerial.h" // 引入蓝牙库

Adafruit_MPU6050 mpu;
BluetoothSerial SerialBT;    // 创建蓝牙串口对象

float velocityX = 0.0; 
unsigned long previousTime = 0;
float baseAccelX = 0.0;

void setup() {
  // 依然保留有线串口，方便你插着线调试
  Serial.begin(115200);
  
  // 开启蓝牙，并给你的小车起个霸气的名字
  SerialBT.begin("ESP32_TestCart"); 
  Serial.println("蓝牙已启动！请在电脑的蓝牙设置中配对 'ESP32_TestCart'");

  while (!Serial) delay(10);
  if (!mpu.begin()) {
    while (1) { delay(10); } 
  }

  mpu.setAccelerometerRange(MPU6050_RANGE_4_G);
  mpu.setFilterBandwidth(MPU6050_BAND_21_HZ);

  calibrateSensor();
  previousTime = millis();
}

void loop() {
  sensors_event_t a, g, temp;
  mpu.getEvent(&a, &g, &temp);

  unsigned long currentTime = millis();
  float dt = (currentTime - previousTime) / 1000.0; 
  previousTime = currentTime;
  float runningSeconds = currentTime / 1000.0;

  float realAccelX = a.acceleration.x - baseAccelX;

  // 智能状态机 (ZUPT)
  if (abs(realAccelX) > 0.3) {
    velocityX += realAccelX * dt;
  } else {
    if (abs(velocityX) <= 1.0) {
      velocityX *= 0.8; 
      if (abs(velocityX) < 0.05) velocityX = 0; 
    }
  }

  // 构建纯净的数据字符串
  String dataString = String(runningSeconds, 3) + "," + 
                      String(realAccelX, 3) + "," + 
                      String(velocityX, 3);

  // 1. 发送给有线串口（如果你还插着线的话）
  Serial.println(dataString);
  
  // 2. 发送给无线蓝牙（核心！）
  SerialBT.println(dataString); 

  delay(10); 
}

void calibrateSensor() {
  int numSamples = 200;
  float sumX = 0;
  for (int i = 0; i < numSamples; i++) {
    sensors_event_t a, g, temp;
    mpu.getEvent(&a, &g, &temp);
    sumX += a.acceleration.x;
    delay(5);
  }
  baseAccelX = sumX / numSamples;
}