#include <SPI.h>
#include <SD.h>
#include <Arduino.h>
// --- 引脚定义 ---
const int encoderPinA = 13; 
const int encoderPinB = 12; 
//3.3Vcc


const int chipSelect = 5;      // SD卡 CS(片选) 引脚
//MOSI D23
//MISO D19
//CLK D18
// --- 全局变量 ---
volatile long pulseCount = 0;  
long lastPrintTime = 0;        
bool isRecording = false;      

// 存储当前正在使用的文件名
String currentFileName = ""; 

// --- 中断服务程序 (ISR) ---
void IRAM_ATTR updateEncoder() {
  if (digitalRead(encoderPinB) == HIGH) {
    pulseCount++; 
  } else {
    pulseCount--; 
  }
}

// --- 【新增】开机自动找回最新的历史文件 ---
void recoverLastFileName() {
  currentFileName = ""; // 先清空
  // 从1开始遍历找文件，直到找到一个不存在的文件为止
  for (int i = 1; i < 1000; i++) {
    String checkName = "/data" + String(i) + ".csv";
    if (SD.exists(checkName)) {
      currentFileName = checkName; // 如果存在，就先记下来，继续往下找更大的
    } else {
      break; // 遇到不存在的，说明上一个就是最新的！跳出循环
    }
  }
  
  // 打印扫描结果给电脑
  if (currentFileName != "") {
    Serial.print(">>> [系统恢复] 扫描到最新历史记录: ");
    Serial.println(currentFileName);
  } else {
    Serial.println(">>> [系统恢复] SD卡是空的，暂无历史数据。");
  }
}

// --- 自动生成新文件名的函数 ---
void createNewFileName() {
  for (int i = 1; i < 1000; i++) {
    String newName = "/data" + String(i) + ".csv";
    if (!SD.exists(newName)) {
      currentFileName = newName; 
      break;
    }
  }
}

void setup() {
  Serial.begin(115200); 
  Serial.println("\nESP32 编码器数据采集系统启动...");

  pinMode(encoderPinA, INPUT_PULLUP);
  pinMode(encoderPinB, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(encoderPinA), updateEncoder, RISING);

  Serial.print("正在初始化 SD 卡...");
  if (!SD.begin(chipSelect)) {
    Serial.println("失败！请检查接线或确认已插入 SD 卡。");
  } else {
    Serial.println("成功！");
    
    // 【关键改动】SD卡初始化成功后，立刻执行扫描找回最新文件！
    recoverLastFileName();
    
    Serial.println("=========================================");
    Serial.println("指令说明:");
    Serial.println("  发送 's' : 开始记录数据 (自动生成新文件)");
    Serial.println("  发送 'p' : 停止记录");
    Serial.println("  发送 'r' : 读取最新录制的文件");
    Serial.println("  发送 'l' : 查看所有文件列表");
    Serial.println("=========================================");
  }
}

void loop() {

  // --- 第一部分：监听电脑串口发来的控制指令 ---
  if (Serial.available() > 0) {
    char incomingChar = Serial.read();

    // 收到 's' 或 'S'：开始记录
    if (incomingChar == 's' || incomingChar == 'S') {
      if (!isRecording) { 
        createNewFileName(); 
        isRecording = true;
        pulseCount = 0; // 【可选优化】每次开始录制时，把脉冲清零，这样画图总是从0开始
        
        Serial.print("\n>>> 收到指令，开始记录！创建新文件: ");
        Serial.println(currentFileName);
        
        File dataFile = SD.open(currentFileName, FILE_WRITE); 
        if(dataFile){
          dataFile.println("Time_ms,Position"); 
          dataFile.close();
        } else {
          Serial.println("错误：无法创建新文件！");
        }
      }
    } 
    // 收到 'p' 或 'P'：停止记录
    else if (incomingChar == 'p' || incomingChar == 'P') {
      if (isRecording) { 
        isRecording = false;
        Serial.println("\n>>> 收到指令，已停止记录。可以安全拔出SD卡。");
      }
    }

    // 收到 'r' 或 'R'：读取最新记录
    else if (incomingChar == 'r' || incomingChar == 'R') {
      if (!isRecording) { 
        if (currentFileName == "") {
          Serial.println("\n错误：还没有录制过任何数据，找不到要读取的文件！");
        } else {
          Serial.print("\n>>> 开始从 SD 卡读取文件: ");
          Serial.println(currentFileName);
          Serial.println("---DATA_START---"); 
          
          File dataFile = SD.open(currentFileName, FILE_READ); 
          if (dataFile) {
            uint8_t buf[512];
            while (dataFile.available()) {
              int n = dataFile.read(buf, sizeof(buf));
              Serial.write(buf, n);
            }
            dataFile.close();
            Serial.println("\n---DATA_END---"); 
            Serial.println(">>> 数据传输完毕！");
          } else {
            Serial.println("错误：无法打开文件进行读取！");
          }
        }
      } else {
        Serial.println("\n警告：正在录制中！请先发送 'p' 停止录制，然后再读取。");
      }
    }
    
    // 收到 'l' 或 'L'：查看 SD 卡根目录
    else if (incomingChar == 'l' || incomingChar == 'L') {
      if (!isRecording) {
        Serial.println("\n>>> 收到指令，正在扫描 SD 卡文件...");
        Serial.println("---FILE_LIST_START---");
        File root = SD.open("/");
        if (root) {
          File entry = root.openNextFile();
          while (entry) {
            if (entry.isDirectory()) {
              Serial.print("[文件夹] "); Serial.println(entry.name());
            } else {
              Serial.print("[文件]   "); Serial.print(entry.name());
              Serial.print("\t  (大小: "); Serial.print(entry.size()); Serial.println(" 字节)");
            }
            entry.close();
            entry = root.openNextFile();
          }
          root.close();
          Serial.println("---FILE_LIST_END---");
          Serial.println(">>> 扫描完毕！");
        }
      }
    }
  }

  int currentInterval = isRecording ? 10 : 1000; // 录制时 10ms，待机时 1000ms [cite: 3, 34]
  // --- 第二部分：定时处理和保存数据 (每 200 毫秒执行一次) ---
  if (millis() - lastPrintTime > currentInterval) {
    if (isRecording) {
      Serial.print("[● 录制中] ");
    } else {
      Serial.print("[○ 待机中] ");
    }
    Serial.print("时间: ");
    Serial.print(millis());
    Serial.print(" ms | 位置: ");
    Serial.println(pulseCount);

    if (isRecording) {
      File dataFile = SD.open(currentFileName, FILE_APPEND);
      if (dataFile) {
        dataFile.print(millis());      
        dataFile.print(",");           
        dataFile.println(pulseCount);  
        dataFile.close();              
      }
    }
    
    lastPrintTime = millis(); 
  }
}