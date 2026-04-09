// ---------------------------------------------------------
// 文件名: src/sd_logger.cpp
// 作用: 负责SD卡初始化、文件读写、以及接收电脑发来的 s/p/r/l 指令
// ---------------------------------------------------------

#include "sd_logger.h"
#include <Arduino.h>
#include <SPI.h>
#include <SD.h>
#include "encoder.h" // 核心秘诀：把编码器的菜单引进来，这样前台就能直接读到脉冲数(pulseCount)了！

// --- 私有变量 (只在前台内部使用) ---
const int chipSelect = 5;      // SD卡 CS 引脚
//MOSI D23
//MISO D19
//CLK D18

bool isRecording = false;      // 是否正在录制
String currentFileName = "";   // 当前正在写的文件名
long lastPrintTime = 0;        // 上次记录数据的时间

// --- 内部辅助函数：找回最新文件 ---
void recoverLastFileName() {
  currentFileName = ""; 
  for (int i = 1; i < 1000; i++) {
    String checkName = "/data" + String(i) + ".csv";
    if (SD.exists(checkName)) {
      currentFileName = checkName; 
    } else {
      break; 
    }
  }
  if (currentFileName != "") {
    Serial.print(">>> [系统恢复] 扫描到最新历史记录: ");
    Serial.println(currentFileName);
  } else {
    Serial.println(">>> [系统恢复] SD卡是空的，暂无历史数据。");
  }
}

// --- 内部辅助函数：生成新文件名 ---
void createNewFileName() {
  for (int i = 1; i < 1000; i++) {
    String newName = "/data" + String(i) + ".csv";
    if (!SD.exists(newName)) {
      currentFileName = newName; 
      break;
    }
  }
}

// --- 对外功能 1: 初始化 SD 卡 ---
void initSDLogger() {
  Serial.print("正在初始化 SD 卡...");
  if (!SD.begin(chipSelect)) {
    Serial.println("失败！请检查接线或确认已插入 SD 卡。");
  } else {
    Serial.println("成功！");
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

// --- 对外功能 2: 处理电脑指令 ---
void handleSerialCommands() {
  if (Serial.available() > 0) {
    char incomingChar = Serial.read();

    if (incomingChar == 's' || incomingChar == 'S') {
      if (!isRecording) { 
        createNewFileName(); 
        isRecording = true;
        pulseCount = 0; // 开始录制时清零脉冲
        
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
    else if (incomingChar == 'p' || incomingChar == 'P') {
      if (isRecording) { 
        isRecording = false;
        Serial.println("\n>>> 收到指令，已停止记录。可以安全拔出SD卡。");
      }
    }
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
          }
        }
      } else {
        Serial.println("\n警告：正在录制中！请先停止。");
      }
    }
    else if (incomingChar == 'l' || incomingChar == 'L') {
      if (!isRecording) {
        Serial.println("\n>>> 正在扫描 SD 卡文件...");
        File root = SD.open("/");
        if (root) {
          File entry = root.openNextFile();
          while (entry) {
            if (!entry.isDirectory()) {
              Serial.print("[文件]   "); Serial.print(entry.name());
              Serial.print("\t  (大小: "); Serial.print(entry.size()); Serial.println(" 字节)");
            }
            entry.close();
            entry = root.openNextFile();
          }
          root.close();
        }
      }
    }
  }
}

// --- 对外功能 3: 定时打印和记录数据 ---
void handleDataLogging() {
  int currentInterval = isRecording ? 10 : 1000; 

  if (millis() - lastPrintTime > currentInterval) {
    if (isRecording) {
      Serial.print("[● 录制中] ");
    } else {
      Serial.print("[○ 待机中] ");
    }
    Serial.print("时间: "); Serial.print(millis());
    Serial.print(" ms | 位置: "); Serial.println(pulseCount);

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