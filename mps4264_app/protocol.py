"""MPS4264 Gen1 348-byte scan frames; accept observed LE and documented BE."""

from __future__ import annotations

import csv
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


FRAME_SIZE = 348
NORMAL_TYPE = 0x0A
FAST_TYPE = 0x10
FAST_GROUPS = {
    1: (1, 5, 9, 13, 17, 21, 25, 29, 36, 40, 44, 48, 52, 56, 60, 64),
    2: (2, 6, 10, 14, 18, 22, 26, 30, 35, 39, 43, 47, 51, 55, 59, 63),
    3: (3, 7, 11, 15, 19, 23, 27, 31, 34, 38, 42, 46, 50, 54, 58, 62),
    4: (4, 8, 12, 16, 20, 24, 28, 32, 33, 37, 41, 45, 49, 53, 57, 61),
}


@dataclass(frozen=True)
class PressureFrame:
    packet_type: int
    frame_number: int
    serial_number: int
    rate_hz: float
    valve_state: int
    units_index: int
    psi_to_units: float
    scan_start_sec: int
    scan_start_ns: int
    external_trigger_us: int
    temperatures: tuple[float, ...]
    pressures: tuple[float | int, ...]
    frame_sec: int
    frame_ns: int
    trigger_sec: int
    trigger_ns: int

    @property
    def frame_time_sec(self) -> float:
        return self.frame_sec + self.frame_ns / 1_000_000_000

    def display_pressures(self, fast_group: int | None = None) -> list[float | int | None]:
        if self.packet_type == FAST_TYPE and fast_group in FAST_GROUPS:
            valid = set(FAST_GROUPS[fast_group])
            return [value if number in valid else None
                    for number, value in enumerate(self.pressures, 1)]
        return list(self.pressures)


def decode_frame(raw: bytes) -> PressureFrame:
    if len(raw) != FRAME_SIZE:
        raise ValueError(f"帧长度应为 {FRAME_SIZE} 字节，实际为 {len(raw)}")
    # The manual specifies network byte order, but an actual Ver 2.10 module
    # sent 0a0000005c010000 (type 0x0A, length 348) in little-endian order.
    # Select only from an exact type/length match; never guess from values.
    for endian in (">", "<"):
        packet_type, packet_size = struct.unpack_from(endian + "2I", raw)
        if packet_type in (NORMAL_TYPE, FAST_TYPE) and packet_size == FRAME_SIZE:
            break
    else:
        raise ValueError(f"不是预期的 MPS4264 帧头：{raw[:8].hex()}")
    packet_type, packet_size, frame_number, serial_number = struct.unpack_from(endian + "4I", raw)
    rate_hz = struct.unpack_from(endian + "f", raw, 16)[0]
    valve_state, units_index = struct.unpack_from(endian + "2I", raw, 20)
    psi_to_units = struct.unpack_from(endian + "f", raw, 28)[0]
    scan_start_sec, scan_start_ns, external_trigger_us = struct.unpack_from(endian + "3I", raw, 32)
    temperatures = struct.unpack_from(endian + "8f", raw, 44)
    pressures = struct.unpack_from(endian + ("64i" if units_index == 27 else "64f"), raw, 76)
    frame_sec, frame_ns, trigger_sec, trigger_ns = struct.unpack_from(endian + "4I", raw, 332)
    return PressureFrame(packet_type, frame_number, serial_number, rate_hz,
                         valve_state, units_index, psi_to_units, scan_start_sec,
                         scan_start_ns, external_trigger_us, temperatures,
                         pressures, frame_sec, frame_ns, trigger_sec, trigger_ns)


def decode_datagram(datagram: bytes) -> Iterator[tuple[bytes, PressureFrame]]:
    if not datagram or len(datagram) % FRAME_SIZE:
        raise ValueError(f"UDP 包长度 {len(datagram)} 不是 {FRAME_SIZE} 的整数倍")
    for offset in range(0, len(datagram), FRAME_SIZE):
        raw = datagram[offset:offset + FRAME_SIZE]
        yield raw, decode_frame(raw)


CSV_HEADER = (
    "frame", "serial", "packet_type", "rate_hz", "valve_state", "units_index",
    "frame_sec", "frame_ns", "frame_time_sec", "scan_start_sec", "scan_start_ns",
    "trigger_sec", "trigger_ns",
    *(f"T{i:02d}" for i in range(1, 9)),
    *(f"P{i:02d}" for i in range(1, 65)),
)


def convert_binary_to_csv(source: str | Path, target: str | Path,
                          fast_group: int | None = None, overwrite: bool = False) -> dict:
    """Convert one raw ScanTel-style .dat; original is never modified.

    Fast-scan group is not encoded in the frame and must be supplied explicitly.
    """
    source, target = Path(source), Path(target)
    if source.resolve() == target.resolve():
        raise ValueError("输入和输出文件不能相同")
    if fast_group is not None and fast_group not in FAST_GROUPS:
        raise ValueError("fast_group 只能是 1、2、3 或 4")
    total_bytes = source.stat().st_size
    if not total_bytes or total_bytes % FRAME_SIZE:
        raise ValueError(f"原始文件为空或长度不是 {FRAME_SIZE} 字节的整数倍")
    if target.exists() and not overwrite:
        raise FileExistsError(f"CSV 已存在：{target}")
    with source.open("rb") as src:
        first = decode_frame(src.read(FRAME_SIZE))
    if first.packet_type == FAST_TYPE and fast_group is None:
        raise ValueError("快速扫描文件需指定 OPTIONS 组号 fast_group=1..4；其余 48 路无效")
    if first.packet_type == NORMAL_TYPE and fast_group is not None:
        raise ValueError("普通 64 路文件不应指定 fast_group")

    # Write to a sibling temporary file, then atomically publish on success.
    temporary = target.with_name(target.name + ".part")
    if temporary.exists():
        raise FileExistsError(f"临时文件已存在，请先检查：{temporary}")
    frames = gaps = 0
    last_number: int | None = None
    target_created = False
    try:
        with source.open("rb") as src, temporary.open("x", newline="", encoding="utf-8") as dst:
            writer = csv.writer(dst)
            writer.writerow(CSV_HEADER)
            while raw := src.read(FRAME_SIZE):
                frame = decode_frame(raw)
                if frame.packet_type != first.packet_type:
                    raise ValueError("文件中混有普通/快速扫描帧，无法确定统一通道映射")
                if last_number is not None and frame.frame_number > last_number + 1:
                    gaps += frame.frame_number - last_number - 1
                last_number = frame.frame_number
                writer.writerow((frame.frame_number, frame.serial_number,
                                 f"0x{frame.packet_type:02X}", frame.rate_hz,
                                 frame.valve_state, frame.units_index,
                                 frame.frame_sec, frame.frame_ns, frame.frame_time_sec,
                                 frame.scan_start_sec, frame.scan_start_ns,
                                 frame.trigger_sec, frame.trigger_ns,
                                 *frame.temperatures,
                                 *("" if x is None else x for x in frame.display_pressures(fast_group))))
                frames += 1
        if overwrite:
            temporary.replace(target)
        else:
            # Exclusive output creation prevents silently replacing someone else's CSV.
            with target.open("x", encoding="utf-8") as final, temporary.open("r", encoding="utf-8") as src:
                target_created = True
                for chunk in iter(lambda: src.read(1024 * 1024), ""):
                    final.write(chunk)
            temporary.unlink()
    except Exception:
        temporary.unlink(missing_ok=True)
        if target_created:
            target.unlink(missing_ok=True)
        raise
    return {"frames": frames, "frame_gaps": gaps, "csv_file": str(target),
            "units_index": first.units_index, "fast_group": fast_group}
