// ---------------------------------------------------------
// 文件名: src/main.cpp
// 作用: 程序的总入口
// ---------------------------------------------------------

#include <Arduino.h>
#include "encoder.h"     // 拿到了编码器的菜单
#include "sd_logger.h"   // 拿到了SD卡的菜单
#include "esc_control.h" //电调控制

void setup()
{
  Serial.begin(115200);
  Serial.println("\n[系统启动] 正在初始化模块...");

  initEncoder();  // 呼叫编码器干活
  initSDLogger(); // 呼叫SD卡干活
  initESC();      // 🚀 2. 呼叫电调模块初始化（默认会输出1500us中位信号）

  Serial.println("[系统启动] 全部就绪！");
}

void loop()
{
  // 让系统疯狂地、无阻塞地循环执行这两件事：
  // 1. 串口指令分发
  if (Serial.available() > 0)
  {
    char incomingCmd = Serial.read(); // ⚠️ 关键：从缓冲区读出字符（只读一次！）

    // 邮递员把信件复印分发给各个部门，部门自己看是不是自己的事：
    // 如果 incomingCmd 是 's'，SD卡部门就会工作，电调部门不理睬。
    // 如果 incomingCmd 是 'T'，SD卡部门不理睬，电调部门就会工作。
    handleSerialCommands(incomingCmd);
    handleESCCommand(incomingCmd);
  }

  handleDataLogging(); // 2. 根据时间间隔，决定要不要写SD卡
  
  // 🚀 新增：让电调模块持续检查遥控器有没有抢控制权
  updateESC();
}