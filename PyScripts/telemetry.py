"""Hardware-independent encoder decoding and bounded asynchronous CSV storage."""
import csv
import json
import os
import queue
import threading
import time
from collections import deque
from datetime import datetime

FIELDS = ["Time_ms", "Position", "position", "sequence", "device_epoch", "unix_time_ns",
          "iso_time", "elapsed_ms", "speed_pps", "rpm", "kmh", "throttle",
          "mode", "sequence_gaps", "queue_drops"]


class EncoderStream:
    def __init__(self, ppr=12.0, kmh_per_rpm=0.007173):
        self.ppr = ppr
        self.kmh_per_rpm = kmh_per_rpm
        self.previous = None
        self.history = deque()
        self.speed = 0.0
        self.sequence_gaps = 0
        self.device_epoch = 0

    def decode(self, line, received_ns, throttle=1500, mode="WEB"):
        if not line.startswith("[ENC],"):
            return None
        try:
            tag, seq, ms, pos = line.split(",")
            seq, ms, pos = int(seq), int(ms), int(pos)
            if not (0 <= seq <= 0xFFFFFFFF and 0 <= ms <= 0xFFFFFFFF
                    and -0x80000000 <= pos <= 0x7FFFFFFF):
                return None
        except ValueError:
            return None

        if self.previous is not None:
            old_seq, old_ms, old_pos = self.previous
            seq_delta = (seq - old_seq) & 0xFFFFFFFF
            dt = (ms - old_ms) & 0xFFFFFFFF
            if seq_delta == 0:
                return None  # Duplicate packet, never duplicate a recorded sample.
            if seq_delta > 0x7FFFFFFF or dt > 0x7FFFFFFF:
                self.device_epoch += 1
                self.history.clear()
                self.speed = 0.0
            else:
                self.sequence_gaps += seq_delta - 1
                if dt:
                    # Signed 32-bit counter rollover, including reverse travel.
                    dp = ((pos - old_pos + 0x80000000) & 0xFFFFFFFF) - 0x80000000
                    if dt > 500:
                        self.history.clear()
                        self.speed = dp * 1000.0 / dt
                    else:
                        self.history.append((dt, dp))
                        # Keep approximately 100 ms even if samples were lost.
                        while len(self.history) > 1 and sum(x[0] for x in self.history) - self.history[0][0] >= 100:
                            self.history.popleft()
                        window_ms = sum(x[0] for x in self.history)
                        window_speed = sum(x[1] for x in self.history) * 1000.0 / window_ms
                        self.speed = window_speed if window_ms < 100 else 0.3 * window_speed + 0.7 * self.speed
        self.previous = (seq, ms, pos)
        rpm = self.speed / self.ppr * 60.0
        return {"Time_ms": ms, "Position": pos, "position": pos, "sequence": seq,
                "device_epoch": self.device_epoch, "unix_time_ns": received_ns,
                "iso_time": datetime.fromtimestamp(received_ns / 1e9).astimezone().isoformat(timespec="milliseconds"),
                "speed_pps": self.speed, "rpm": rpm, "kmh": abs(rpm) * self.kmh_per_rpm,
                "throttle": throttle, "mode": mode, "sequence_gaps": self.sequence_gaps}


class CsvRecorder:
    """Disk writes never run on the serial reader. Overflow is counted explicitly."""
    def __init__(self, path, duration_sec=600, queue_size=4096):
        self.path = str(path)
        self.started_ns = time.time_ns()
        self.started_mono = time.monotonic()
        self.duration_sec = duration_sec
        self.queue = queue.Queue(maxsize=queue_size)
        self.lock = threading.Lock()
        self.done = threading.Event()
        self.active = True
        self.sample_count = 0
        self.queue_drops = 0
        self.stop_reason = None
        self.stopped_ns = None
        self.first_sequence_gaps = None
        self.last_sequence_gaps = None
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        # Fail synchronously so a request cannot claim a recording started on an unwritable disk.
        self.file = open(self.path, "x", encoding="utf-8", newline="")
        self.writer = csv.DictWriter(self.file, fieldnames=FIELDS)
        try:
            self.writer.writeheader()
            self.file.flush()
        except Exception:
            self.file.close()
            raise
        self.thread = threading.Thread(target=self._write, name="CSV-recorder", daemon=True)
        self.thread.start()

    def submit(self, sample):
        with self.lock:
            if not self.active:
                return
            if time.monotonic() - self.started_mono >= self.duration_sec:
                self._stop_locked("max_duration")
                return
            row = dict(sample)
            if row["unix_time_ns"] < self.started_ns:
                return
            row["elapsed_ms"] = (row["unix_time_ns"] - self.started_ns) / 1e6
            row["queue_drops"] = self.queue_drops
            if self.first_sequence_gaps is None:
                self.first_sequence_gaps = row["sequence_gaps"]
            self.last_sequence_gaps = row["sequence_gaps"]
            try:
                self.queue.put_nowait(row)
            except queue.Full:
                self.queue_drops += 1

    def _stop_locked(self, reason):
        if self.active:
            self.active = False
            self.stop_reason = reason
            self.stopped_ns = time.time_ns()

    def stop(self, reason="manual_stop"):
        with self.lock:
            self._stop_locked(reason)

    def status(self):
        with self.lock:
            return {"active": self.active, "file_path": self.path,
                    "started_at_ns": self.started_ns, "stopped_at_ns": self.stopped_ns,
                    "sample_count": self.sample_count, "queue_drops": self.queue_drops,
                    "sequence_gaps": (self.last_sequence_gaps or 0) - (self.first_sequence_gaps or 0),
                    "stop_reason": self.stop_reason, "finalized": self.done.is_set(),
                    "elapsed_sec": min(time.monotonic() - self.started_mono, self.duration_sec)
                    if self.active else (self.stopped_ns - self.started_ns) / 1e9}

    def _write(self):
        last_flush = time.monotonic()
        try:
            while True:
                with self.lock:
                    if self.active and time.monotonic() - self.started_mono >= self.duration_sec:
                        self._stop_locked("max_duration")
                    if not self.active and self.queue.empty():
                        break
                try:
                    row = self.queue.get(timeout=0.05)
                except queue.Empty:
                    continue
                self.writer.writerow(row)
                with self.lock:
                    self.sample_count += 1
                if time.monotonic() - last_flush >= 0.5:
                    self.file.flush()
                    last_flush = time.monotonic()
            self.file.flush()
            os.fsync(self.file.fileno())
        except Exception as exc:
            with self.lock:
                self._stop_locked(f"error: {exc}")
                self.stop_reason = f"error: {exc}"
        finally:
            try:
                self.file.close()
                state = self.status()
                state["finalized"] = True
                with open(self.path + ".meta.json", "w", encoding="utf-8") as meta:
                    json.dump(state, meta, ensure_ascii=False, indent=2)
            except Exception as exc:
                with self.lock:
                    self.stop_reason = f"error: {exc}"
            finally:
                self.done.set()
