"""Match steering requests to firmware acknowledgements, with a bounded wait."""
import re
import secrets
import threading
import time


REASONS = {
    "line_lost": "未检测到黑胶带",
    "line_ambiguous": "巡线读数为全通道命中或多段命中，请检查胶带位置与极性",
    "no_line_frame": "巡线模块尚未产生完整帧，请检查初始化与接线",
    "no_valid_line": "巡线反馈无效",
    "line_stale": "巡线数据已超时",
    "arm_requires_neutral_normal_mode": "油门未归中，或仍处于遥控、调试、急停状态",
    "pwm_attach_failed": "舵机 PWM 初始化失败",
    "no_valid_gyro": "缺少有效陀螺仪反馈",
    "no_valid_yaw_source": "缺少有效外部目标或陀螺仪反馈",
}


class SteeringRequests:
    def __init__(self, timeout=3.0, clock=time.monotonic):
        self.timeout = timeout
        self.clock = clock
        self.lock = threading.Lock()
        self.next_id = secrets.randbelow(0x7FFFFFFF) + 1
        self.state = {"status": "idle"}

    def begin(self, enabled):
        with self.lock:
            self.next_id = (self.next_id + 1) & 0xFFFFFFFF or 1
            self.state = {"id": self.next_id, "enabled": enabled, "status": "pending",
                          "sent_at": self.clock(), "message": "命令已发送，等待 ESP32 确认（最多3秒）"}
            return self.next_id

    def fail(self, request_id, message):
        with self.lock:
            if self.state.get("id") == request_id:
                self.state.update(status="error", message=message)

    def feed(self, line):
        if not line.startswith("[FOLLOW_ACK] "):
            return False
        try:
            fields = dict(re.findall(r"(\w+)=([^\s]+)", line))
            request_id, action, ok = (int(fields[key]) for key in ("id", "action", "ok"))
            raw, mask, level = (int(fields[key]) for key in ("raw", "mask", "lvl"))
            reason = fields["reason"]
            if not (0 < request_id <= 0xFFFFFFFF and action in (0, 1) and ok in (0, 1)
                    and level in (0, 1) and -1 <= raw <= 255 and -1 <= mask <= 255):
                return False
        except (KeyError, ValueError):
            return False
        with self.lock:
            if self.state.get("id") != request_id or action != int(self.state.get("enabled", False)):
                return False  # Delayed replies cannot confirm a newer or opposite command.
            if self.state["status"] in {"confirmed", "rejected", "error"}:
                return False
            if ok:
                message = "ESP32 已确认：巡线转向已开启" if action else "ESP32 已确认：巡线转向已关闭并归中"
            else:
                message = "ESP32 拒绝开启：" + REASONS.get(reason, reason)
            if raw >= 0 and mask >= 0:
                raw_bits = "".join(str((raw >> i) & 1) for i in range(8))
                mask_bits = "".join(str((mask >> i) & 1) for i in range(8))
                message += f"；raw={raw_bits}，line={mask_bits}，黑线电平={'HIGH' if level else 'LOW'}"
            self.state.update(status="confirmed" if ok else "rejected", message=message,
                              reason=reason, raw_mask=raw, line_mask=mask, line_level=level)
            return True

    def snapshot(self):
        with self.lock:
            if self.state["status"] == "pending" and self.clock() - self.state["sent_at"] >= self.timeout:
                self.state.update(status="timeout", message="3秒未收到 ESP32 命令确认：请确认已烧录新版 esp32dev 固件，并检查串口连接和后台接收日志。")
            return dict(self.state)
