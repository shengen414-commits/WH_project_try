#pragma once
#ifndef ESC_CONTROL_H
#define ESC_CONTROL_H

#include <Arduino.h>

// 初始化电调
void initESC();

// 设置电调油门值 (输入范围 1000 - 2000)
void setESCThrottle(int pwmValue);
// Start a short boost profile for numeric PWM control.
void startESCBoost(int pwmValue);
// 🚀 新增：让电调模块自己处理传入的字符
void handleESCCommand(char incomingChar);
// 🚀 新增：放在 loop 中持续执行，负责监控遥控器信号并处理控制权抢占
void updateESC();
#endif
