"""Importable MPS4264 Gen1 control and loss-aware UDP recorder.

No socket or device command is executed during import or construction.
"""

from __future__ import annotations

import csv
import ipaddress
import json
import re
import socket
import threading
import time
from collections import deque
from pathlib import Path
from typing import BinaryIO

from .protocol import FAST_TYPE, PressureFrame, convert_binary_to_csv, decode_datagram
from .transport import MPSControlConnection


class MPSControllerError(RuntimeError):
    pass


class MPS4264Controller:
    """One device, one open capture file, one UDP receiver.

    Typical external integration: connect -> configure_udp (if needed) ->
    power-cycle/reconnect -> set_parameters -> new_file -> scan -> stop ->
    close_file -> convert_file -> disconnect.
    """

    def __init__(self, data_dir: str | Path | None = None):
        self.data_dir = Path(data_dir or Path(__file__).parent / "recordings").resolve()
        self._lock = threading.RLock()
        self._client: MPSControlConnection | None = None
        self._device_ip: str | None = None
        self._device_port = 23
        self._udp_target_ip: str | None = None
        self._udp_port = 50023
        self._udp_socket: socket.socket | None = None
        self._capture_thread: threading.Thread | None = None
        self._capture_stop = threading.Event()
        self._raw_file: BinaryIO | None = None
        self._index_file = None
        self._index_writer: csv.writer | None = None
        self._file_stem: str | None = None
        self._scanning = False
        self._scan_started_monotonic_ns: int | None = None
        self._reboot_required = False
        self._rate: float | None = None
        self._fps: int | None = None
        self._fast_group: int | None = None
        self._frames = 0
        self._frame_gaps = 0
        self._bad_datagrams = 0
        self._foreign_datagrams = 0
        self._last_frame_number: int | None = None
        self._last_frame: PressureFrame | None = None
        self._last_receive_unix_ns: int | None = None
        self._last_error: str | None = None
        self._events: deque[dict] = deque(maxlen=80)
        self._log("面板就绪；尚未连接设备")

    def _log(self, message: str) -> None:
        self._events.append({"time": time.strftime("%H:%M:%S"), "message": message})

    def _require_client(self) -> MPSControlConnection:
        if self._client is None:
            raise MPSControllerError("尚未连接设备")
        return self._client

    def _require_idle(self) -> MPSControlConnection:
        client = self._require_client()
        if self._scanning:
            raise MPSControllerError("扫描进行中；请先 STOP，不能同时修改设备设置")
        return client

    def connect(self, device_ip: str, control_port: int = 23,
                confirm_reboot: bool = False) -> dict:
        ipaddress.IPv4Address(device_ip)
        if not 1 <= control_port <= 65535:
            raise ValueError("TCP 端口范围应为 1–65535")
        with self._lock:
            if self._raw_file is not None:
                raise MPSControllerError("请先关闭采集文件再切换连接")
            if self._client is not None:
                self._client.close()
                self._client = None
            self._client = MPSControlConnection(device_ip, control_port)
            self._device_ip, self._device_port = device_ip, control_port
            if confirm_reboot:
                self._reboot_required = False
            self._last_error = None
            self._log(f"已连接设备 {device_ip}:{control_port}")
            return self.status()

    def disconnect(self) -> dict:
        if self._raw_file is not None:
            self.close_file()
        with self._lock:
            if self._client is not None:
                self._client.close()
                self._client = None
            self._device_ip = None
            self._log("设备连接已断开")
            return self.status()

    def device_info(self) -> dict[str, str]:
        with self._lock:
            client = self._require_idle()
            results = {}
            for command in ("VER", "STATUS", "VALVESTATE", "LIST ID", "LIST IP",
                            "LIST S", "LIST M", "LIST UDP"):
                results[command] = client.command(command)
            scan_settings = results["LIST S"]
            match = re.search(r"SET OPTIONS\s+([0-4])\s+\d+\s+\d+", scan_settings)
            if match:
                self._fast_group = int(match.group(1)) or None
            match = re.search(r"SET RATE\s+([\d.]+)", scan_settings)
            if match:
                self._rate = float(match.group(1))
            match = re.search(r"SET FPS\s+(\d+)", scan_settings)
            if match:
                self._fps = int(match.group(1))
            match = re.search(r"SET IPUDP\s+([\d.]+)(?:\s+(\d+))?", results["LIST UDP"])
            if match:
                self._udp_target_ip = match.group(1)
                if match.group(2):
                    self._udp_port = int(match.group(2))
            self._log("已读取设备版本、阀位和参数")
            return results

    def set_parameters(self, rate: float, fps: int, fast_group: int = 0,
                       read_mode: int = 0, subset_size: int = 16) -> dict:
        rate = float(rate)
        fps = int(fps)
        fast_group, read_mode, subset_size = int(fast_group), int(read_mode), int(subset_size)
        if fast_group not in range(5) or read_mode not in (0, 1) or not 2 <= subset_size <= 256:
            raise ValueError("OPTIONS 需为：组号 0–4、读取模式 0/1、子集 2–256")
        maximum = 850 if fast_group == 0 else 2500
        if not 0.25 <= rate <= maximum:
            raise ValueError(f"当前通道模式的 RATE 范围应为 0.25–{maximum} Hz")
        if not 0 <= fps <= 4_294_967_295:
            raise ValueError("FPS 范围应为 0–4294967295；0 表示直到 STOP")
        with self._lock:
            client = self._require_idle()
            if self._raw_file is not None:
                raise MPSControllerError("请先关闭文件，再更改扫描参数")
            options = f"SET OPTIONS {fast_group} {read_mode} {subset_size}"
            # Switching back to 64 channels must first lower a possible fast RATE.
            commands = ([f"SET RATE {rate:g}", options] if fast_group == 0
                        else [options, f"SET RATE {rate:g}"])
            commands.append(f"SET FPS {fps}")
            response = {}
            for command in commands:
                response[command] = client.command(command)
            self._rate, self._fps = rate, fps
            self._fast_group = fast_group or None
            self._log(f"已 SET RATE={rate:g}, FPS={fps}, OPTIONS={fast_group} {read_mode} {subset_size}")
            return response

    def configure_udp(self, host_ip: str, udp_port: int = 50023) -> dict:
        address = ipaddress.IPv4Address(host_ip)
        if address.is_unspecified or address.is_multicast:
            raise ValueError("UDP 目标必须是 Orange Pi 的单播 IPv4 地址")
        if not 1 <= udp_port <= 65535:
            raise ValueError("UDP 端口范围应为 1–65535")
        with self._lock:
            client = self._require_idle()
            if self._raw_file is not None:
                raise MPSControllerError("请先关闭文件再修改 UDP 目标")
            commands = ("SET FORMAT F B", "SET TRIG 0", "SET ENFTP 0",
                        "SET ENUDP 1", "SET SVRSEL 3",
                        f"SET IPUDP {host_ip} {udp_port}", "SAVE")
            responses = {command: client.command(command, 120 if command == "SAVE" else 10)
                         for command in commands}
            self._udp_target_ip, self._udp_port = host_ip, udp_port
            self._reboot_required = True
            self._log(f"UDP 目标已保存为 {host_ip}:{udp_port}；须等待保存完成并重启设备")
            return {"responses": responses, "reboot_required": True}

    def save_settings(self) -> str:
        with self._lock:
            response = self._require_idle().command("SAVE", 120)
            self._log("设备已返回 SAVE 完成提示符")
            return response

    def calz(self, zero_pressure_confirmed: bool = False) -> str:
        if not zero_pressure_confirmed:
            raise ValueError("请先确认 CAL/REF 等压，或在 PX 状态下确认无风/无测点压差")
        with self._lock:
            response = self._require_idle().command("CALZ", 120)
            self._log("CALZ 已完成；请核查设备返回信息")
            return response

    def new_file(self, name: str, udp_port: int | None = None) -> dict:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name):
            raise ValueError("文件名仅允许英文字母、数字、下划线和连字符，长度 1–64")
        port = self._udp_port if udp_port is None else int(udp_port)
        if not 1 <= port <= 65535:
            raise ValueError("UDP 端口范围应为 1–65535")
        with self._lock:
            if self._raw_file is not None:
                raise MPSControllerError("已有文件打开；请先关闭")
            self.data_dir.mkdir(parents=True, exist_ok=True)
            raw_path = self.data_dir / f"{name}.dat"
            index_path = self.data_dir / f"{name}.index.csv"
            if raw_path.exists() or index_path.exists():
                raise FileExistsError("同名采集文件或索引文件已存在")
            udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            udp.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 1024 * 1024)
            udp.settimeout(0.2)
            try:
                udp.bind(("0.0.0.0", port))
                raw = raw_path.open("xb", buffering=4 * 1024 * 1024)
                try:
                    index = index_path.open("x", newline="", encoding="utf-8", buffering=1024 * 1024)
                except Exception:
                    raw.close()
                    raw_path.unlink()
                    raise
            except Exception:
                udp.close()
                raise
            self._raw_file, self._index_file = raw, index
            self._index_writer = csv.writer(index)
            self._index_writer.writerow(("frame", "host_receive_monotonic_ns",
                                         "host_receive_unix_ns", "device_frame_sec",
                                         "device_frame_ns", "packet_type", "units_index"))
            self._udp_socket, self._udp_port, self._file_stem = udp, port, name
            self._frames = self._frame_gaps = self._bad_datagrams = self._foreign_datagrams = 0
            self._last_frame_number = None
            self._last_frame = None
            self._last_receive_unix_ns = None
            self._last_error = None
            self._capture_stop.clear()
            self._capture_thread = threading.Thread(target=self._receive_loop,
                                                    name="mps4264-udp", daemon=True)
            self._capture_thread.start()
            self._log(f"已新建 {name}.dat，并监听 UDP {port}；等待 SCAN")
            return self.status()

    def _receive_loop(self) -> None:
        assert self._udp_socket is not None
        udp = self._udp_socket
        write_failed = False
        while not self._capture_stop.is_set():
            try:
                datagram, address = udp.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError as exc:
                if not self._capture_stop.is_set():
                    with self._lock:
                        self._last_error = f"UDP 接收中断：{exc}"
                        self._scanning = False
                        self._log(self._last_error)
                    write_failed = True
                break
            monotonic_ns, unix_ns = time.monotonic_ns(), time.time_ns()
            with self._lock:
                if not self._scanning or self._raw_file is None:
                    continue
                if self._device_ip and address[0] != self._device_ip:
                    self._foreign_datagrams += 1
                    continue
                try:
                    decoded = list(decode_datagram(datagram))
                except ValueError:
                    self._bad_datagrams += 1
                    continue
                try:
                    for raw, frame in decoded:
                        self._raw_file.write(raw)
                        self._index_writer.writerow((frame.frame_number, monotonic_ns,
                                                     unix_ns, frame.frame_sec, frame.frame_ns,
                                                     frame.packet_type, frame.units_index))
                        if (self._last_frame_number is not None and
                                frame.frame_number > self._last_frame_number + 1):
                            self._frame_gaps += frame.frame_number - self._last_frame_number - 1
                        self._frames += 1
                        self._last_frame_number = frame.frame_number
                        self._last_frame = frame
                        self._last_receive_unix_ns = unix_ns
                except (OSError, ValueError) as exc:
                    self._last_error = f"写入采集文件失败：{exc}"
                    self._scanning = False
                    self._capture_stop.set()
                    self._log(self._last_error)
                    write_failed = True
                    break
        if write_failed and self._client is not None:
            try:
                self._client.stop_scan()
            except (OSError, TimeoutError, ConnectionError):
                pass
        # Do not close files here: close_file owns the final flush and metadata.

    def scan(self) -> dict:
        with self._lock:
            client = self._require_client()
            if self._reboot_required:
                raise MPSControllerError("UDP 目标已改变；请重启设备并使用“已重启，重新连接”")
            if self._raw_file is None:
                raise MPSControllerError("请先新建采集文件并监听 UDP")
            if self._capture_thread is None or not self._capture_thread.is_alive():
                raise MPSControllerError("UDP 接收线程未运行；请关闭当前文件并重新新建文件")
            if self._scanning:
                raise MPSControllerError("设备已在扫描")
            self._last_error = None
            self._scanning = True
            self._scan_started_monotonic_ns = time.monotonic_ns()
            try:
                client.start_scan()
            except Exception:
                self._scanning = False
                self._scan_started_monotonic_ns = None
                raise
            self._log("已发送 SCAN；开始接收并写入原始二进制帧")
            return self.status()

    def stop(self) -> dict:
        with self._lock:
            was_scanning = self._scanning
            client = self._client
        stop_error = None
        if was_scanning and client is not None:
            try:
                client.stop_scan()
            except (OSError, TimeoutError, ConnectionError) as exc:
                with self._lock:
                    self._last_error = f"STOP 命令未确认：{exc}"
                stop_error = self._last_error
        # Give in-flight UDP datagrams time to reach the already-open file.
        if was_scanning:
            time.sleep(0.2)
        with self._lock:
            self._scanning = False
            self._scan_started_monotonic_ns = None
            if was_scanning:
                self._log("扫描已停止；文件仍打开，可继续 SCAN 或关闭文件")
            result = self.status()
        if stop_error:
            raise MPSControllerError(stop_error)
        return result

    def close_file(self) -> dict:
        self.stop()
        with self._lock:
            if self._raw_file is None:
                return self.status()
            self._capture_stop.set()
            thread = self._capture_thread
            udp = self._udp_socket
        if udp is not None:
            udp.close()
        if thread is not None:
            thread.join(timeout=2)
            if thread.is_alive():
                raise MPSControllerError("UDP 接收线程尚未退出，未关闭文件；请重试")
        with self._lock:
            self._raw_file.flush()
            self._raw_file.close()
            self._index_file.flush()
            self._index_file.close()
            stem = self._file_stem
            metadata = {
                "device_ip": self._device_ip,
                "udp_target_ip": self._udp_target_ip,
                "udp_port": self._udp_port,
                "rate_hz": self._rate,
                "fps": self._fps,
                "fast_group": self._fast_group,
                "frames_received": self._frames,
                "frame_gaps": self._frame_gaps,
                "bad_datagrams": self._bad_datagrams,
                "foreign_datagrams": self._foreign_datagrams,
                "last_error": self._last_error,
                "note": "device frame time is not necessarily synchronized to host UTC",
            }
            self._raw_file = self._index_file = self._index_writer = None
            self._udp_socket = self._capture_thread = None
            self._file_stem = None
            try:
                (self.data_dir / f"{stem}.meta.json").write_text(
                    json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError as exc:
                self._last_error = f"原始文件已关闭，但元数据写入失败：{exc}"
                self._log(self._last_error)
                raise
            self._log(f"已关闭 {stem}.dat：{self._frames} 帧，估计缺口 {self._frame_gaps} 帧")
            return self.status()

    def list_files(self) -> list[dict]:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return [{"name": path.name, "bytes": path.stat().st_size}
                for path in sorted(self.data_dir.glob("*.dat"), reverse=True)
                if path.is_file()]

    def convert_file(self, filename: str, fast_group: int | None = None,
                     overwrite: bool = False) -> dict:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\.dat", filename):
            raise ValueError("只能转换本模块 recordings 目录中的 .dat 文件")
        with self._lock:
            if self._scanning or self._file_stem == filename[:-4]:
                raise MPSControllerError("请先 STOP 并关闭该采集文件再转换")
        source = self.data_dir / filename
        if not source.is_file():
            raise FileNotFoundError(filename)
        if fast_group is None:
            meta = self.data_dir / f"{source.stem}.meta.json"
            if meta.is_file():
                saved = json.loads(meta.read_text(encoding="utf-8"))
                fast_group = saved.get("fast_group")
        target = source.with_suffix(".csv")
        result = convert_binary_to_csv(source, target, fast_group, overwrite)
        with self._lock:
            self._log(f"已转换 {filename} → {target.name}，共 {result['frames']} 帧")
        return result

    def status(self) -> dict:
        with self._lock:
            frame = self._last_frame
            no_udp_warning = bool(self._scanning and self._frames == 0 and
                                  self._scan_started_monotonic_ns is not None and
                                  time.monotonic_ns() - self._scan_started_monotonic_ns > 3_000_000_000)
            return {
                "connected": self._client is not None,
                "device_ip": self._device_ip,
                "device_port": self._device_port,
                "udp_target_ip": self._udp_target_ip,
                "udp_port": self._udp_port,
                "reboot_required": self._reboot_required,
                "file_open": self._raw_file is not None,
                "file_name": f"{self._file_stem}.dat" if self._file_stem else None,
                "scanning": self._scanning,
                "rate_hz": self._rate,
                "fps": self._fps,
                "fast_group": self._fast_group,
                "frames_received": self._frames,
                "frame_gaps": self._frame_gaps,
                "bad_datagrams": self._bad_datagrams,
                "foreign_datagrams": self._foreign_datagrams,
                "last_receive_unix_ns": self._last_receive_unix_ns,
                "last_error": self._last_error,
                "no_udp_warning": no_udp_warning,
                "last_frame": ({
                    "number": frame.frame_number,
                    "serial": frame.serial_number,
                    "rate_hz": frame.rate_hz,
                    "packet_type": f"0x{frame.packet_type:02X}",
                    "units_index": frame.units_index,
                    "unit_label": "Pa" if frame.units_index == 23 else ("RAW" if frame.units_index == 27 else f"index {frame.units_index}"),
                    "fast_group_unknown": frame.packet_type == FAST_TYPE and self._fast_group is None,
                    "pressures": frame.display_pressures(self._fast_group),
                    "temperatures": list(frame.temperatures),
                } if frame else None),
                "events": list(self._events)[-12:],
            }
