"""Reusable, read-only pressure CSV reader and offline web plotter.

Run: python3 -m mps4264_app.plotting [--csv /path/to/run.csv]
Import: read_pressure_csv(path), create_plot_app(), register_plot_routes(app).
No device connections or network-interface initialization happen here.
"""
from __future__ import annotations

import argparse
import csv
import io
import math
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

MAX_CSV_BYTES = 128 * 1024 * 1024


@dataclass
class PressureCSV:
    name: str
    source_format: str
    unit: str
    times: list[float]
    frames: list[int]
    channels: dict[str, list[float | None]]
    metadata: dict[str, str]

    def to_dict(self) -> dict:
        """Full-resolution data, JSON-safe; invalid readings are None/null."""
        return {"name": self.name, "source_format": self.source_format,
                "unit": self.unit, "times": self.times, "frames": self.frames,
                "channels": self.channels, "metadata": self.metadata,
                "frame_count": len(self.frames),
                "invalid_counts": {key: sum(v is None for v in values)
                                   for key, values in self.channels.items()}}


def read_pressure_csv(source: str | Path | TextIO, *, name: str | None = None,
                      invalid_value: float = -999999.0) -> PressureCSV:
    """Read ScanTel or mps4264_app CSV without changing the source.

    Frames, time order and units are validated. Blank, nonfinite and sentinel
    pressures become None; other malformed values raise a row-specific error.
    Pressure values are already in the selected unit; no conversion is applied.
    Separate scans with time/frame resets must be plotted as separate files.
    """
    if hasattr(source, "read"):
        text = source.read(MAX_CSV_BYTES + 1)
        filename = name or Path(str(getattr(source, "name", "uploaded.csv"))).name
    else:
        path = Path(source)
        if path.stat().st_size > MAX_CSV_BYTES:
            raise ValueError("CSV 超过 128 MiB，请按扫描拆分文件")
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("gb18030")
        filename = name or path.name
    if not isinstance(text, str):
        raise ValueError("请传入文件路径或文本流（不是二进制流）")
    if len(text) > MAX_CSV_BYTES:
        raise ValueError("CSV 超过大小限制")
    rows = csv.reader(io.StringIO(text.lstrip("\ufeff")))
    metadata: dict[str, str] = {}
    for line_number, row in enumerate(rows, 1):
        row = [v.strip() for v in row]
        if not row or not any(row):
            continue
        if row[0].lower() == "frame":
            header = row
            break
        if len(row) >= 2:
            metadata[row[0]] = row[1]
        if line_number > 32:
            raise ValueError("找不到压力 CSV 表头")
    else:
        raise ValueError("CSV 为空或找不到 Frame/frame 表头")
    if len(set(header)) != len(header):
        raise ValueError("CSV 表头包含重复列名")
    columns = {key: index for index, key in enumerate(header)}
    linux = "frame_time_sec" in columns
    time_key = "frame_time_sec" if linux else "FTime"
    frame_key = "frame" if linux else "Frame"
    if time_key not in columns or frame_key not in columns:
        raise ValueError("缺少时间列；需要 frame_time_sec 或 FTime（不支持 .index.csv）")
    channel_columns = {f"P{n:02d}": columns[key] for n in range(1, 65)
                       if (key := (f"P{n:02d}" if linux else f"{n:02d}Press")) in columns}
    if not channel_columns:
        raise ValueError("没有找到 P01～P64 或 01Press～64Press 压力列")
    times: list[float] = []
    frames: list[int] = []
    values: dict[str, list[float | None]] = {key: [] for key in channel_columns}
    unit = metadata.get("Units", "未知单位")
    unit_index: int | None = None
    for line_number, row in enumerate(rows, line_number + 1):
        if not row or not any(v.strip() for v in row):
            continue
        if len(row) != len(header):
            raise ValueError(f"第 {line_number} 行列数不正确")
        try:
            t = float(row[columns[time_key]])
            frame = int(row[columns[frame_key]])
            if not math.isfinite(t):
                raise ValueError("时间必须为有限数值")
            if times and (t <= times[-1] or frame <= frames[-1]):
                raise ValueError("帧号或时间不递增；请将重复记录或多次扫描拆分后绘图")
            if linux and "units_index" in columns:
                current_unit = int(row[columns["units_index"]])
                if unit_index is not None and unit_index != current_unit:
                    raise ValueError("文件中途改变了压力单位，请拆分后绘图")
                unit_index = current_unit
            parsed = {}
            for key, index in channel_columns.items():
                cell = row[index].strip()
                v = float(cell) if cell else None
                parsed[key] = (v if v is not None and math.isfinite(v)
                               and v != invalid_value else None)
        except ValueError as exc:
            raise ValueError(f"第 {line_number} 行：{exc}") from exc
        times.append(t)
        frames.append(frame)
        for key, v in parsed.items():
            values[key].append(v)
    if not times:
        raise ValueError("CSV 没有数据帧")
    if unit_index is not None:
        unit = {23: "Pa", 27: "RAW"}.get(unit_index, f"单位索引 {unit_index}")
    elif unit.upper() == "PA":
        unit = "Pa"
    return PressureCSV(filename, "Linux" if linux else "ScanTel", unit,
                       times, frames, values, metadata)


def register_plot_routes(app, *, data_dir: str | Path | None = None,
                         initial_csv: str | Path | None = None) -> None:
    """Add /plot and read-only CSV endpoints to an existing Flask application.

    Server file browsing is restricted to data_dir. Uploaded files are parsed
    in memory, returned to that browser, and never saved on the server.
    """
    from flask import Blueprint, jsonify, render_template, request

    root = Path(data_dir or Path(__file__).parent / "recordings").resolve()
    initial = Path(initial_csv).resolve() if initial_csv else None
    bp = Blueprint("pressure_plot", __name__, template_folder="templates",
                   static_folder="static", static_url_path="/plot/static")

    @bp.errorhandler(ValueError)
    @bp.errorhandler(UnicodeError)
    def invalid(exc):
        return jsonify(error=str(exc)), 400

    @bp.errorhandler(OSError)
    def io_error(exc):
        return jsonify(error=str(exc)), 400

    @bp.get("/plot")
    def page():
        return render_template("pressure_plot.html", initial_csv=bool(initial))

    @bp.get("/plot/api/files")
    def files():
        candidates = (root.iterdir() if root.exists() else [])
        names = sorted(p.name for p in candidates if p.is_file()
                       and p.suffix.lower() == ".csv"
                       and not p.name.lower().endswith(".index.csv")
                       and p.resolve().parent == root)
        return jsonify(files=names)

    @bp.get("/plot/api/data")
    def data():
        filename = request.args.get("filename")
        if filename is None and initial is not None:
            path = initial
        else:
            if not filename or Path(filename).name != filename or "\\" in filename:
                raise ValueError("请选择数据目录中的 CSV 文件")
            path = (root / filename).resolve()
            if path.parent != root or path.suffix.lower() != ".csv":
                raise ValueError("不能读取数据目录以外的文件")
        return jsonify(read_pressure_csv(path).to_dict())

    @bp.post("/plot/api/upload")
    def upload():
        # Per-request limit keeps this independent of device-control routes.
        request.max_content_length = MAX_CSV_BYTES + 1024 * 1024
        file = request.files.get("file")
        if file is None or not file.filename or not file.filename.lower().endswith(".csv"):
            raise ValueError("请选择 .csv 文件")
        raw = file.stream.read(MAX_CSV_BYTES + 1)
        if len(raw) > MAX_CSV_BYTES:
            raise ValueError("上传 CSV 超过 128 MiB")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("gb18030")
        name = file.filename.replace("\\", "/").split("/")[-1]
        return jsonify(read_pressure_csv(io.StringIO(text), name=name).to_dict())

    app.register_blueprint(bp)


def create_plot_app(*, data_dir: str | Path | None = None,
                    initial_csv: str | Path | None = None):
    """Create an independent plotter; importing/calling never starts a server."""
    from flask import Flask, redirect
    app = Flask(__name__)
    register_plot_routes(app, data_dir=data_dir, initial_csv=initial_csv)

    @app.get("/")
    def index():
        return redirect("/plot")

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="MPS4264 CSV 多通道压力—时间图（离线可用）")
    parser.add_argument("--csv", type=Path, help="启动后自动读取的 CSV 文件")
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).parent / "recordings")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5056)
    args = parser.parse_args()
    if args.csv:
        try:
            read_pressure_csv(args.csv)
        except (OSError, ValueError) as exc:
            parser.exit(2, f"CSV 读取失败：{exc}\n")
    if args.host == "0.0.0.0":
        print("注意：无用户认证，请仅在可信局域网使用，不要开放到公网。")
    print(f"绘图地址：http://{'127.0.0.1' if args.host == '0.0.0.0' else args.host}:{args.port}/plot")
    create_plot_app(data_dir=args.data_dir, initial_csv=args.csv).run(
        host=args.host, port=args.port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
