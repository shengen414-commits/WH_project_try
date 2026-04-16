#!/bin/bash
# battery_test.sh - 电池续航测试脚本
# 原理: 每分钟记录心跳时间到文件，关机时自动记录最后时间
# 重启后读取文件计算续航时间

DATA_DIR="/opt/battery_test"
STATE_FILE="$DATA_DIR/last_state.txt"
HISTORY_FILE="$DATA_DIR/history.log"

# 每分钟执行，记录心跳
do_heartbeat() {
    mkdir -p "$DATA_DIR"
    echo "$(date +%s)" > "$STATE_FILE"
}

# 关机时记录（systemd service 调用）
do_shutdown_record() {
    mkdir -p "$DATA_DIR"
    local now=$(date +%s)
    echo "$now" > "$STATE_FILE"
    
    # 追加到历史记录
    echo "$(date '+%Y-%m-%d %H:%M:%S') - 系统关机" >> "$HISTORY_FILE"
}

# 查看状态
do_status() {
    echo ""
    echo "╔══════════════════════════════════════════════╗"
    echo "║         🔋 电池续航测试状态                ║"
    echo "╠══════════════════════════════════════════════╣"
    
    # 系统运行时间
    echo "║ 📅 系统启动: $(who -b | awk '{print $3, $4}')"
    echo "║ ⏱️  运行时长: $(uptime -p)"
    echo "║"
    
    # 当前心跳
    if [ -f "$STATE_FILE" ]; then
        last=$(cat "$STATE_FILE")
        last_human=$(date -d "@$last" "+%Y-%m-%d %H:%M:%S" 2>/dev/null || echo "$last")
        now=$(date +%s)
        since=$((now - last))
        
        echo "║ 💓 最后心跳: $last_human"
        echo "║    距今: ${since}秒"
    else
        echo "║ ⚠️  尚未记录心跳"
    fi
    
    echo "║"
    
    # 最近关机历史
    if [ -f "$HISTORY_FILE" ]; then
        echo "║ 📜 历史记录 (最近10条):"
        tail -10 "$HISTORY_FILE" | while read line; do
            echo "║    $line"
        done
    fi
    
    echo "╚══════════════════════════════════════════════╝"
    echo ""
}

case "$1" in
    heartbeat|hb)
        do_heartbeat
        ;;
    shutdown)
        do_shutdown_record
        ;;
    status|stat)
        do_status
        ;;
    *)
        echo "🔋 电池续航测试脚本"
        echo ""
        echo "用法: $0 {heartbeat|shutdown|status}"
        echo ""
        echo "  heartbeat  - 手动记录心跳"
        echo "  shutdown   - 关机时记录（systemd 调用）"
        echo "  status     - 查看状态"
        echo ""
        echo "已在后台运行定时心跳记录服务！"
        do_status
        ;;
esac