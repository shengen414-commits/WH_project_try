#!/bin/bash

LOG_FILE="boot_log.txt"

echo "正在记录开机时间，每2分钟更新一次... (按 Ctrl+C 停止)"

while true
do
    echo "$(date '+%Y-%m-%d %H:%M:%S') - Boot time: $(uptime -s)" >> "$LOG_FILE"
    sleep 120
done