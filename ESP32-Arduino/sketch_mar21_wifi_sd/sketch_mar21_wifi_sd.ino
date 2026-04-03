#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>
#include <Wire.h>
#include "FS.h"
#include "SD.h"
#include "SPI.h"
#include <WiFi.h>
#include <WebServer.h>

// --- 引脚定义 ---
#define SD_CS 5
Adafruit_MPU6050 mpu;
WebServer server(80);

// --- 变量定义 ---
float velocityX = 0.0, velocityY = 0.0;
unsigned long previousTime = 0;
float baseAccelX = 0.0, baseAccelY = 0.0;
String fileName = "/data_01.csv";
File dataFile;

// --- 1. WiFi 网页服务器逻辑：列出并下载文件 ---
void handleRoot() {
  File root = SD.open("/");
  String html = "<html><head><meta charset='UTF-8'></head><body>";
  html += "<h1>🚗 小车实验数据下载站</h1><ul>";
  
  File file = root.openNextFile();
  while (file) {
    String fName = String(file.name());
    html += "<li><a href='" + fName + "'>" + fName + " (" + String(file.size()) + " bytes)</a></li>";
    file = root.openNextFile();
  }
  html += "</ul><p>刷新网页可更新文件列表</p></body></html>";
  server.send(200, "text/html", html);
}

void handleFileDownload() {
  String path = server.uri();
  if (SD.exists(path)) {
    File file = SD.open(path);
    server.streamFile(file, "application/octet-stream");
    file.close();
  } else {
    server.send(404, "text/plain", "File Not Found");
  }
}

// --- 2. 传感器校准 ---
void calibrateSensor() {
  int numSamples = 200;
  float sumX = 0, sumY = 0;
  for (int i = 0; i < numSamples; i++) {
    sensors_event_t a, g, temp;
    mpu.getEvent(&a, &g, &temp);
    sumX += a.acceleration.x; sumY += a.acceleration.y;
    delay(5);
  }
  baseAccelX = sumX / numSamples;
  baseAccelY = sumY / numSamples;
}

void setup() {
  Serial.begin(115200);
  
  // 初始化 SD 卡
  if (!SD.begin(SD_CS)) {
    Serial.println("❌ SD 卡挂载失败！检查接线或格式。");
    while (1);
  }
  Serial.println("✅ SD 卡就绪");

  // 初始化 MPU6050
  if (!mpu.begin()) {
    Serial.println("❌ 找不到 MPU6050");
    while (1);
  }
  mpu.setAccelerometerRange(MPU6050_RANGE_4_G);
  calibrateSensor();

  // 寻找一个不重复的文件名
  int n = 1;
  while (SD.exists("/data_" + String(n) + ".csv")) { n++; }
  fileName = "/data_" + String(n) + ".csv";
  
  // 写入 CSV 表头
  dataFile = SD.open(fileName, FILE_WRITE);
  if (dataFile) {
    dataFile.println("Time(s),Accel(m/s2),Vel(m/s)");
    dataFile.close();
    Serial.println("📝 开始记录到: " + fileName);
  }

  // 初始化 WiFi AP 模式
  WiFi.softAP("ESP32_Data_Lab", "12345678");
  Serial.println("🌐 WiFi 热点已启动: 192.168.4.1");
  
  server.on("/", handleRoot);
  server.onNotFound(handleFileDownload);
  server.begin();

  previousTime = millis();
}

void loop() {
  // 处理网页下载请求
  server.handleClient();

  // 传感器数据采集
  sensors_event_t a, g, temp;
  mpu.getEvent(&a, &g, &temp);

  unsigned long currentTime = millis();
  float dt = (currentTime - previousTime) / 1000.0;
  previousTime = currentTime;

  float runSec = currentTime / 1000.0;
  float rAX = a.acceleration.x - baseAccelX;
  float rAY = a.acceleration.y - baseAccelY;

  if (abs(rAX) < 0.25) rAX = 0;
  if (abs(rAY) < 0.25) rAY = 0;

  velocityX = (velocityX + rAX * dt) * 0.98;
  velocityY = (velocityY + rAY * dt) * 0.98;

  float hA = sqrt(pow(rAX, 2) + pow(rAY, 2));
  float hV = sqrt(pow(velocityX, 2) + pow(velocityY, 2));

  // --- 高频写入 SD 卡 ---
  static unsigned long lastWrite = 0;
  if (currentTime - lastWrite > 20) { // 每 20ms 记录一次数据 (50Hz)
    dataFile = SD.open(fileName, FILE_APPEND);
    if (dataFile) {
      dataFile.printf("%.3f,%.3f,%.3f\n", runSec, hA, hV);
      dataFile.close(); // 每次写入都关闭文件，确保断电不丢数据
    }
    lastWrite = currentTime;
  }

  // 串口依然输出，方便你插着线时调试
  if (currentTime % 500 < 20) {
    Serial.printf("Time:%.2f, Accel:%.2f, Vel:%.2f\n", runSec, hA, hV);
  }
}