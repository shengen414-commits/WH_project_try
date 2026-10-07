"""Small Telnet-compatible TCP/23 control client for MPS4264 Gen1."""

from __future__ import annotations

import socket
import select
import re
import time
from .diagnostics import TimedRLock, command_timing, milliseconds


class MPSControlConnection:
    def __init__(self, host: str, port: int = 23, connect_timeout: float = 5):
        self.host = host
        self.port = port
        self._socket = socket.create_connection((host, port), timeout=connect_timeout)
        self._socket.settimeout(0.3)
        self._lock = TimedRLock("tcp")
        self._telnet_state = "data"
        self._telnet_verb = 0
        self.banner = self._read_until_prompt(3, allow_timeout=True)

    def close(self) -> None:
        with self._lock:
            self._socket.close()

    def _filter_telnet(self, payload: bytes) -> bytes:
        output = bytearray()
        for byte in payload:
            state = self._telnet_state
            if state == "data":
                if byte == 255:
                    self._telnet_state = "iac"
                else:
                    output.append(byte)
            elif state == "iac":
                if byte == 255:
                    output.append(byte)
                    self._telnet_state = "data"
                elif byte in (251, 252, 253, 254):
                    self._telnet_verb = byte
                    self._telnet_state = "option"
                elif byte == 250:
                    self._telnet_state = "subneg"
                else:
                    self._telnet_state = "data"
            elif state == "option":
                reply = 254 if self._telnet_verb in (251, 252) else 252
                self._socket.sendall(bytes((255, reply, byte)))
                self._telnet_state = "data"
            elif state == "subneg":
                if byte == 255:
                    self._telnet_state = "subneg_iac"
            elif state == "subneg_iac":
                self._telnet_state = "data" if byte == 240 else "subneg"
        return bytes(output)

    def _read_until_prompt(self, timeout: float, allow_timeout: bool = False,
                           timing: dict | None = None) -> str:
        started = time.perf_counter()
        try:
            return self._receive_until_prompt(timeout, allow_timeout, timing, started)
        finally:
            if timing is not None:
                timing["response_ms"] = milliseconds(started)

    def _receive_until_prompt(self, timeout: float, allow_timeout: bool,
                              timing: dict | None, started: float) -> str:
        deadline = time.monotonic() + timeout
        output = bytearray()
        while time.monotonic() < deadline:
            try:
                payload = self._socket.recv(8192)
            except socket.timeout:
                continue
            if not payload:
                raise ConnectionError("设备关闭了 TCP 控制连接")
            if timing is not None:
                timing["rx_bytes"] += len(payload)
                if timing["first_tcp_byte_ms"] is None:
                    timing["first_tcp_byte_ms"] = milliseconds(started)
            text = self._filter_telnet(payload)
            if text and timing is not None and timing["first_text_ms"] is None:
                timing["first_text_ms"] = milliseconds(started)
            output.extend(text)
            if output.rstrip().endswith(b">"):
                if timing is not None:
                    timing["prompt_received"] = True
                return output.decode("ascii", errors="replace")
        result = output.decode("ascii", errors="replace")
        if allow_timeout:
            return result
        raise TimeoutError(f"未等到设备 > 提示符；收到：{result[-300:]!r}")

    def command(self, command: str, timeout: float = 5) -> str:
        if not command.isascii() or any(c in command for c in "\r\n"):
            raise ValueError("设备命令必须是单行 ASCII")
        with command_timing(command, timeout) as timing, self._lock:
            self._timed_send(command.encode("ascii") + b"\r", timing)
            response = self._read_until_prompt(timeout, timing=timing)
            if re.search(r"(?im)^\s*(?:ERROR|ERR\b|INVALID\b|UNKNOWN COMMAND\b)", response):
                raise ValueError(f"设备拒绝命令 {command!r}：{response[-300:]}")
            return response

    def _timed_send(self, payload: bytes, timing: dict) -> None:
        started = time.perf_counter()
        try:
            self._socket.sendall(payload)
        finally:
            timing["send_ms"] = milliseconds(started)

    def start_scan(self) -> None:
        # In UDP mode SCAN can keep the command channel busy until FPS/STOP.
        with command_timing("SCAN", None, waits_for_prompt=False) as timing, self._lock:
            self._timed_send(b"SCAN\r", timing)

    def stop_scan(self) -> str:
        with command_timing("STOP", 5) as timing, self._lock:
            self._timed_send(b"STOP\r", timing)
            response = self._read_until_prompt(5, timing=timing)
            # SCAN may have left its own prompt unread. Drain the subsequent
            # STOP acknowledgement so it cannot be mistaken for the next query.
            drain_started = time.perf_counter()
            quiet_until = time.monotonic() + 0.35
            while time.monotonic() < quiet_until:
                readable, _, _ = select.select([self._socket], [], [], 0.05)
                if readable:
                    extra = self._socket.recv(8192)
                    if not extra:
                        break
                    response += self._filter_telnet(extra).decode("ascii", errors="replace")
                    quiet_until = time.monotonic() + 0.15
            timing["stop_drain_ms"] = milliseconds(drain_started)
            return response
