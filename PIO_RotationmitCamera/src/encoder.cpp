// ---------------------------------------------------------
// 文件名: src/encoder.cpp
// 作用: 编码器模块的“源文件（后厨）”，所有的引脚定义、中断逻辑都在这里
// ---------------------------------------------------------

#include "encoder.h" // 必须把自己对应的“菜单”引进来

// 【真正定义变量】这里不需要 extern 了，因为变量就出生在这里
volatile long pulseCount = 0; 

// 【定义引脚】（设为私有，不想让 main.cpp 知道引脚具体是几）
const int encoderPinA = 26; 
const int encoderPinB = 25; 
//3.3Vcc

// 【中断服务函数 (ISR)】
// 注意：这个函数没有写在 .h 菜单里，因为大堂经理不需要知道它，这是后厨的私事
void IRAM_ATTR updateEncoder() {
  // A 相上升沿时，判断 B 相的状态，决定是正转还是反转
  if (digitalRead(encoderPinB) == HIGH) {
    pulseCount++; 
  } else {
    pulseCount--; 
  }
}

// 【初始化函数】（实现在 .h 菜单里承诺过的函数）
void initEncoder() {
  // 1. 设置引脚模式（带内部上拉电阻，防止引脚悬空乱跳）
  pinMode(encoderPinA, INPUT_PULLUP);
  pinMode(encoderPinB, INPUT_PULLUP);

  // 2. 挂载中断：告诉 ESP32，只要 A 相引脚电平上升(RISING)，就立刻去执行 updateEncoder 函数
  attachInterrupt(digitalPinToInterrupt(encoderPinA), updateEncoder, RISING);
}