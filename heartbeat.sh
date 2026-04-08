#!/bin/bash
while true
do
    # 将当前时间戳覆盖写入一个文件
    date +%s > last_alive_timestamp.txt
    # 同步磁盘缓存，确保即使断电数据也已写入硬盘
    sync
    echo "saved"
    sleep 120
    
done