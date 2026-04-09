// ---------------------------------------------------------
// 文件名: include/sd_logger.h
// 作用: SD卡与串口指令模块的菜单
// ---------------------------------------------------------

#pragma once 

// 前台只有三个对外开放的功能
void initSDLogger();         // 1. 开机初始化SD卡
void handleSerialCommands(); // 2. 随时接听电脑发来的指令(s, p, r, l)
void handleDataLogging();    // 3. 定时把数据写进SD卡里