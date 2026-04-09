// ---------------------------------------------------------
// 文件名: src/main.cpp
// 作用: 程序的总入口
// ---------------------------------------------------------

#include <Arduino.h>
#include "encoder.h"   // 拿到了编码器的菜单
#include "sd_logger.h" // 拿到了SD卡的菜单

void setup() {
  Serial.begin(115200);
  Serial.println("\n[系统启动] 正在初始化模块...");

  initEncoder();  // 呼叫编码器干活
  initSDLogger(); // 呼叫SD卡干活
  
  Serial.println("[系统启动] 全部就绪！");
}

void loop() {
  // 让系统疯狂地、无阻塞地循环执行这两件事：
  handleSerialCommands(); // 1. 听听看有没有人按了键盘 (s/p/r/l)
  handleDataLogging();    // 2. 根据时间间隔，决定要不要写SD卡
}