"""Record Yahboom eight-channel frames, then plot black-line center over time.

Run: python PyScripts/line_debug_plot.py --port COM12
Requires pyserial and matplotlib. Firmware must support G/L/R commands.
"""

import argparse
import csv
import math
import time
from datetime import datetime
from pathlib import Path


def line_center(mask, reverse=False):
    """Return (position, status); disconnected groups have no unique center."""
    if not 0 <= mask <= 255:
        raise ValueError("mask must be an 8-bit value")
    hits = [i for i in range(8) if mask & (1 << i)]
    if not hits:
        return math.nan, "lost"
    if len(hits) == 8:
        return math.nan, "all_black"
    if hits[-1] - hits[0] + 1 != len(hits):
        return math.nan, "multiple"
    center = sum(i - 3.5 for i in hits) / len(hits)
    return (-center if reverse else center), "valid"


def decode_frame(text):
    if not text.startswith("[LINE],"):
        return None
    try:
        tag, sequence, ms, raw, mask = text.strip().split(",")
        sequence, ms, raw, mask = map(int, (sequence, ms, raw, mask))
        if not (0 <= sequence <= 0xFFFFFFFF and 0 <= ms <= 0xFFFFFFFF
                and 0 <= raw <= 255 and 0 <= mask <= 255):
            return None
        return sequence, ms, raw, mask
    except ValueError:
        return None


class FrameTimeline:
    """Unwrap device time; preserve gaps instead of inventing missing positions."""
    def __init__(self, reverse=False):
        self.reverse = reverse
        self.previous = None
        self.elapsed_ms = 0
        self.gaps = 0

    def add(self, frame):
        sequence, ms, raw, mask = frame
        gap = 0
        if self.previous is not None:
            old_seq, old_ms = self.previous
            seq_delta = (sequence - old_seq) & 0xFFFFFFFF
            dt = (ms - old_ms) & 0xFFFFFFFF
            if seq_delta == 0:
                return None
            if seq_delta > 0x7FFFFFFF or dt > 0x7FFFFFFF:
                raise RuntimeError("ESP32 restarted or frames arrived out of order; retry recording")
            gap = seq_delta - 1
            self.elapsed_ms += dt
            self.gaps += gap
        self.previous = sequence, ms
        center, status = line_center(mask, self.reverse)
        return {"elapsed_s": self.elapsed_ms / 1000.0, "sequence": sequence,
                "device_time_ms": ms, "raw_mask": raw, "line_mask": mask,
                "center": center, "status": status, "missing_before": gap}


def collect(port, duration, reverse=False):
    timeline = FrameTimeline(reverse)
    rows = []
    # Switch to quiet human debug before draining the old stream. G preserves
    # full-rate LINE packets while suppressing ENC on this dedicated connection.
    port.write(b"L\n")
    port.flush()
    time.sleep(0.1)
    port.reset_input_buffer()
    port.write(b"G\n")
    port.flush()
    started = time.monotonic()
    buffer = bytearray()
    try:
        while time.monotonic() - started < duration:
            chunk = port.read(min(max(port.in_waiting, 1), 4096))
            buffer.extend(chunk)
            while b"\n" in buffer:
                text, _, remaining = buffer.partition(b"\n")
                buffer = bytearray(remaining)
                frame = decode_frame(text.decode("ascii", errors="replace"))
                if frame is not None:
                    row = timeline.add(frame)
                    if row is not None:
                        rows.append(row)
            if len(buffer) > 4096:
                raise RuntimeError("Serial stream has no line terminators")
            if not rows and time.monotonic() - started > min(3.0, duration):
                raise RuntimeError("No LINE frames: check firmware, wiring and selected port")
    finally:
        port.write(b"L\n")
        port.flush()
    if not rows:
        raise RuntimeError("No LINE frames received")
    return rows


def save_results(rows, directory, show=True, demo=False, reverse=False):
    import matplotlib.pyplot as plt

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    suffix = "_SIMULATED" if demo else ""
    stem = datetime.now().strftime("line_%Y%m%d_%H%M%S_%f") + suffix
    csv_path = directory / (stem + ".csv")
    png_path = directory / (stem + ".png")
    with csv_path.open("w", newline="", encoding="utf-8-sig") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            writer.writerow({key: "" if key == "center" and math.isnan(value) else value
                             for key, value in row.items()})

    fig, ax = plt.subplots(figsize=(11, 5.5), layout="constrained")
    x, y = [], []
    for row in rows:
        # Do not connect a line through dropped frames or a long pause.
        if x and (row["missing_before"] or row["elapsed_s"] - x[-1] > 0.05):
            x.append(row["elapsed_s"])
            y.append(math.nan)
        x.append(row["elapsed_s"])
        y.append(row["center"])
    ax.plot(x, y, color="#147d92", linewidth=1.7, label="Black-line center")
    ax.axhline(0, color="#666666", linestyle="--", linewidth=1, label="Sensor center")
    for status, level, color, label in (
        ("lost", -4.2, "#dc5c58", "No black line"),
        ("multiple", 4.2, "#cc8a22", "Multiple black groups"),
        ("all_black", 4.2, "#8c63b8", "All probes black"),
    ):
        times = [r["elapsed_s"] for r in rows if r["status"] == status]
        if times:
            ax.scatter(times, [level] * len(times), s=14, color=color, marker="x", label=label)
    gaps = sum(r["missing_before"] for r in rows)
    valid = sum(r["status"] == "valid" for r in rows)
    title = "Black-line center over time" + (" — SIMULATED DATA" if demo else "")
    ax.set_title(f"{title}\n{len(rows)} frames | {valid} valid centers | {gaps} missing frames")
    ax.set_xlabel("Time since first received frame (s)")
    ax.set_ylabel("Center position (probe spacing units)")
    ticks = [-3.5, -2.5, -1.5, -0.5, 0, 0.5, 1.5, 2.5, 3.5]
    labels = ["CH1", "CH2", "CH3", "CH4", "Center", "CH5", "CH6", "CH7", "CH8"]
    if reverse:
        labels = ["CH8", "CH7", "CH6", "CH5", "Center", "CH4", "CH3", "CH2", "CH1"]
    ax.set_yticks(ticks, labels)
    ax.set_ylim(-4.6, 4.6)
    ax.set_xlim(0, max(rows[-1]["elapsed_s"], 0.1))
    ax.grid(alpha=0.2)
    ax.legend(loc="upper right", fontsize=8)
    fig.savefig(png_path, dpi=170)
    print(f"CSV: {csv_path.resolve()}\nPlot: {png_path.resolve()}")
    if show:
        plt.show()  # Close the figure to return to the G/R/Q command prompt.
    plt.close(fig)
    return csv_path, png_path


def demo_rows(reverse=False):
    timeline = FrameTimeline(reverse)
    rows = []
    for i in range(600):
        channel = round(3.5 + 3.0 * math.sin(i / 60))
        mask = 1 << channel
        if 200 <= i < 230:
            mask = 0
        elif 400 <= i < 420:
            mask = 0b10000001
        rows.append(timeline.add((i, i * 10, 255 ^ mask, mask)))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="e.g. COM12 or /dev/ttyUSB0")
    parser.add_argument("--duration", type=float, default=10.0, help="recording seconds (default 10)")
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).resolve().parents[1] / "Car_Records" / "Line_Debug")
    parser.add_argument("--reverse", action="store_true", help="make CH8 negative and CH1 positive")
    parser.add_argument("--no-show", action="store_true", help="save PNG without opening a plot window")
    parser.add_argument("--demo", action="store_true", help="plot clearly labeled simulated data; no ESP32 needed")
    args = parser.parse_args()
    if not math.isfinite(args.duration) or not 0.1 <= args.duration <= 600:
        parser.error("--duration must be between 0.1 and 600 seconds")
    if args.no_show:
        import matplotlib
        matplotlib.use("Agg")
    if args.demo:
        save_results(demo_rows(args.reverse), args.output, not args.no_show, True, args.reverse)
        return

    import serial
    from serial.tools import list_ports
    if not args.port:
        ports = list(list_ports.comports())
        for item in ports:
            print(f"{item.device}: {item.description}")
        parser.error("Select the ESP32 using --port; close other serial monitors first")
    with serial.Serial(args.port, 115200, timeout=0.05, write_timeout=1) as port:
        time.sleep(2)  # Opening USB serial often resets the ESP32.
        port.write(b"L\n")
        port.flush()
        print("G: record and plot | R: restore normal stream | Q: quit (restores normal)")
        try:
            while True:
                choice = input("Command > ").strip().upper()
                if choice == "Q":
                    break
                if choice == "R":
                    port.write(b"R\n")
                    port.flush()
                    print("Normal streaming restored. Q closes this tool for Orange Pi.")
                elif choice == "G":
                    print(f"Recording {args.duration:g}s; move the black tape under the probes...")
                    rows = collect(port, args.duration, args.reverse)
                    save_results(rows, args.output, not args.no_show, reverse=args.reverse)
                else:
                    print("Enter G, R or Q, then press Enter.")
        finally:
            port.write(b"R\n")
            port.flush()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nRecording interrupted.")
    except (RuntimeError, OSError) as error:
        raise SystemExit(str(error)) from error
