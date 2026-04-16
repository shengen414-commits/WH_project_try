#!/bin/bash
# shutdown_recorder.sh
# 断电/关机时记录最后运行时间，用于测试电池续航
# 使用方法: ./shutdown_recorder.sh install  (安装到系统)
#           ./shutdown_recorder.sh status    (查看状态)

STATE_FILE="/tmp/last_poweroff_timestamp.txt"
INSTALL_DIR="/opt/shutdown_recorder"

install_service() {
    echo "[*] 安装关机记录脚本..."
    
    # 创建安装目录
    sudo mkdir -p "$INSTALL_DIR"
    sudo cp "$(dirname "$0")/shutdown_recorder.sh" "$INSTALL_DIR/"
    sudo chmod +x "$INSTALL_DIR/shutdown_recorder.sh"
    
    # 创建 systemd 服务
    sudo tee /etc/systemd/system/shutdown-recorder.service > /dev/null << 'EOF'
[Unit]
Description=Power Off Timestamp Recorder
RequiresMountsFor=/tmp
Before=shutdown.target reboot.target

[Service]
Type=oneshot
ExecStart=/opt/shutdown_recorder/shutdown_recorder.sh record
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

    # 创建 timer 每分钟更新心跳
    sudo tee /etc/systemd/system/shutdown-recorder.timer > /dev/null << 'EOF'
[Unit]
Description=Update heartbeat timestamp every minute

[Timer]
OnBootSec=10
OnUnitActiveSec=60

[Install]
WantedBy=timers.target
EOF

    sudo systemctl daemon-reload
    sudo systemctl enable shutdown-recorder.timer
    sudo systemctl start shutdown-recorder.timer
    
    echo "[✓] 安装完成！"
    echo "    - 每分钟自动记录心跳时间到 $STATE_FILE"
    echo "    - 关机时记录最后时间到 $STATE_FILE"
    echo ""
    show_status
}

show_status() {
    echo ""
    echo "========== 电池续航记录状态 =========="
    echo ""
    
    # 系统运行时间
    echo "📅 系统启动时间: $(who -b | awk '{print $3, $4}')"
    echo "⏱️  系统运行时长: $(uptime -p)"
    echo ""
    
    # 最后心跳时间
    if [ -f "$STATE_FILE" ]; then
        LAST_TIME=$(cat "$STATE_FILE")
        LAST_HUMAN=$(date -d @"$LAST_TIME" "+%Y-%m-%d %H:%M:%S" 2>/dev/null || echo "$LAST_TIME")
        CURRENT_TIME=$(date +%s)
        SINCE_LAST=$((CURRENT_TIME - LAST_TIME))
        
        echo "💓 最后心跳: $LAST_HUMAN"
        echo "   距今: ${SINCE_LAST}秒"
        echo ""
        
        # 检查上次关机时间
        if [ -f "/tmp/last_shutdown_timestamp.txt" ]; then
            SHUTDOWN_TIME=$(cat /tmp/last_shutdown_timestamp.txt)
            SHUTDOWN_HUMAN=$(date -d @"$SHUTDOWN_TIME" "+%Y-%m-%d %H:%M:%S" 2>/dev/null || echo "$SHUTDOWN_TIME")
            
            # 查找最近的启动时间
            LAST_BOOT=$(last reboot -2 | head -2 | tail -1 | awk '{print $5, $6, $7, $8}')
            
            echo "🔌 最后关机时间: $SHUTDOWN_HUMAN"
            echo "📊 上次运行到关机持续: ${SHUTDOWN_TIME}秒 ($(($SHUTDOWN_TIME/3600))小时$(($SHUTDOWN_TIME%3600/60))分钟)"
        fi
    else
        echo "⚠️  尚未记录心跳数据"
    fi
    
    echo ""
    echo "========================================="
}

record_heartbeat() {
    date +%s > "$STATE_FILE"
}

record_shutdown() {
    date +%s > /tmp/last_shutdown_timestamp.txt
    date +%s > "$STATE_FILE"
}

case "$1" in
    install)
        install_service
        ;;
    status)
        show_status
        ;;
    record)
        record_shutdown
        ;;
    heartbeat)
        record_heartbeat
        ;;
    *)
        echo "用法: $0 {install|status|heartbeat}"
        echo ""
        echo "  install    - 安装为系统服务（开机自启）"
        echo "  status     - 查看当前状态"
        echo "  heartbeat  - 手动记录心跳"
        ;;
esac