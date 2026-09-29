"""Small Telnet-compatible TCP/23 control client for MPS4264 Gen1."""

from __future__ import annotations

import socket
import select
import re
import threading
import time


class MPSControlConnection:
    def __init__(self, host: str, port: int = 23, connect_timeout: float = 5):
        self.host = host
        self.port = port
        self._socket = socket.create_connection((host, port), timeout=connect_timeout)
        self._socket.settimeout(0.3)
        self._lock = threading.RLock()
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

    def _read_until_prompt(self, timeout: float, allow_timeout: bool = False) -> str:
        deadline = time.monotonic() + timeout
        output = bytearray()
        while time.monotonic() < deadline:
            try:
                payload = self._socket.recv(8192)
            except socket.timeout:
                continue
            if not payload:
                raise ConnectionError("设备关闭了 TCP 控制连接")
            output.extend(self._filter_telnet(payload))
            if output.rstrip().endswith(b">"):
                return output.decode("ascii", errors="replace")
        result = output.decode("ascii", errors="replace")
        if allow_timeout:
            return result
        raise TimeoutError(f"未等到设备 > 提示符；收到：{result[-300:]!r}")

    def command(self, command: str, timeout: float = 5) -> str:
        if not command.isascii() or any(c in command for c in "\r\n"):
            raise ValueError("设备命令必须是单行 ASCII")
        with self._lock:
            self._socket.sendall(command.encode("ascii") + b"\r")
            response = self._read_until_prompt(timeout)
            if re.search(r"(?im)^\s*(?:ERROR|ERR\b|INVALID\b|UNKNOWN COMMAND\b)", response):
                raise ValueError(f"设备拒绝命令 {command!r}：{response[-300:]}")
            return response

    def start_scan(self) -> None:
        # In UDP mode SCAN can keep the command channel busy until FPS/STOP.
        with self._lock:
            self._socket.sendall(b"SCAN\r")

    def stop_scan(self) -> str:
        with self._lock:
            self._socket.sendall(b"STOP\r")
            response = self._read_until_prompt(5)
            # SCAN may have left its own prompt unread. Drain the subsequent
            # STOP acknowledgement so it cannot be mistaken for the next query.
            quiet_until = time.monotonic() + 0.35
            while time.monotonic() < quiet_until:
                readable, _, _ = select.select([self._socket], [], [], 0.05)
                if readable:
                    extra = self._socket.recv(8192)
                    if not extra:
                        break
                    response += self._filter_telnet(extra).decode("ascii", errors="replace")
                    quiet_until = time.monotonic() + 0.15
            return response
