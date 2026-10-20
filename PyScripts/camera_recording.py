"""Bounded camera recording and a disk-backed last-two-minutes replay.

Capture timestamps are host read times, not exposure times. AVI is MJPEG with
an average playback rate; the accompanying CSV is the authoritative timeline.
"""
import csv
from dataclasses import dataclass
import json
import math
from pathlib import Path
import queue
import shutil
import struct
import threading
import time
import uuid

import cv2


INDEX_FIELDS = ["frame_id", "host_receive_monotonic_ns", "host_receive_unix_ns",
                "jpeg_offset", "jpeg_bytes"]


def encode_jpeg(frame, quality):
    ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise OSError("相机 JPEG 编码失败")
    return encoded.tobytes()


def _chunk(tag, data):
    return tag + struct.pack("<I", len(data)) + data + (b"\0" if len(data) % 2 else b"")


class MjpegAviWriter:
    """Mux already compressed JPEG frames without a second encode or FFmpeg CLI."""
    def __init__(self, path, width, height, fps=60):
        self.path = Path(path)
        self.width, self.height, self.fps = width, height, fps
        self.entries = []
        self.maximum = 0
        self.file = self.path.open("xb")
        try:
            header = self._header(0, 4)
            self.movi_tag_at = len(header) - 4
            self.file.write(header)
        except Exception:
            self.file.close()
            raise

    def _header(self, riff_size, movi_size):
        scale, rate = 1000, max(1, round(self.fps * 1000))
        avih = struct.pack("<14I", round(1e6 / self.fps), 0, 0, 0x10, len(self.entries),
                           0, 1, self.maximum, self.width, self.height, 0, 0, 0, 0)
        strh = struct.pack("<4s4sIHHIIIIIIIIhhhh", b"vids", b"MJPG", 0, 0, 0, 0,
                           scale, rate, 0, len(self.entries), self.maximum, 0xFFFFFFFF, 0,
                           0, 0, self.width, self.height)
        strf = struct.pack("<IiiHH4sIiiII", 40, self.width, self.height, 1, 24, b"MJPG",
                           self.width * self.height * 3, 0, 0, 0, 0)
        stream = _chunk(b"LIST", b"strl" + _chunk(b"strh", strh) + _chunk(b"strf", strf))
        header = _chunk(b"LIST", b"hdrl" + _chunk(b"avih", avih) + stream)
        return b"RIFF" + struct.pack("<I", riff_size) + b"AVI " + header + b"LIST" + struct.pack("<I", movi_size) + b"movi"

    def write(self, jpeg):
        if self.file.tell() + len(jpeg) + len(self.entries) * 16 > 0xFFFFFF00:
            raise OSError("AVI 达到4GiB限制，请降低回放帧率或图像质量")
        at = self.file.tell()
        self.file.write(_chunk(b"00dc", jpeg))
        self.entries.append((at - self.movi_tag_at, len(jpeg)))
        self.maximum = max(self.maximum, len(jpeg))
        return at + 8

    def close(self, fps=None):
        if self.file.closed:
            return
        try:
            if fps is not None and math.isfinite(fps) and fps > 0:
                self.fps = fps
            end_movi = self.file.tell()
            index = b"".join(struct.pack("<4sIII", b"00dc", 0x10, at, size) for at, size in self.entries)
            self.file.write(_chunk(b"idx1", index))
            size = self.file.tell()
            self.file.seek(0)
            self.file.write(self._header(size - 8, end_movi - self.movi_tag_at))
            self.file.flush()
        finally:
            self.file.close()


class AsyncImageRecorder:
    """Incremental JPEG saving for ordinary multi-source sessions; bounded RAM."""
    def __init__(self, directory, done=None, on_done=None, queue_size=16, quality=90):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.queue = queue.Queue(queue_size)
        self.quality = quality
        self.done = done if done is not None else threading.Event()
        self.done.clear()
        self.on_done = on_done
        self.lock = threading.Lock()
        self.active = True
        self.frames_saved = self.queue_drops = 0
        self.error = None
        self.thread = threading.Thread(target=self._run, name="camera-images", daemon=True)
        self.thread.start()

    def submit(self, frame, wall_ns, mono_ns, frame_id):
        with self.lock:
            if not self.active:
                return False
            try:
                self.queue.put_nowait((frame, wall_ns, mono_ns, frame_id))
                return True
            except queue.Full:
                self.queue_drops += 1
                return False

    def finish(self):
        with self.lock:
            self.active = False

    def status(self):
        with self.lock:
            return {"active": self.active, "frames_saved": self.frames_saved,
                    "queue_drops": self.queue_drops, "queue_depth": self.queue.qsize(),
                    "error": self.error, "finalized": self.done.is_set()}

    def _run(self):
        try:
            with (self.directory / "frames.index.csv").open("x", newline="", encoding="utf-8") as file:
                writer = csv.writer(file)
                writer.writerow(["frame_id", "host_receive_monotonic_ns", "host_receive_unix_ns", "image_path"])
                while True:
                    with self.lock:
                        finished = not self.active and self.queue.empty()
                    if finished:
                        break
                    try:
                        frame, wall_ns, mono_ns, frame_id = self.queue.get(timeout=0.05)
                    except queue.Empty:
                        continue
                    jpeg = encode_jpeg(frame, self.quality)
                    filename = f"{wall_ns}.jpg"
                    (self.directory / filename).write_bytes(jpeg)
                    writer.writerow([frame_id, mono_ns, wall_ns, filename])
                    with self.lock:
                        self.frames_saved += 1
                file.flush()
            if not self.frames_saved:
                self.error = "录制期间没有保存相机帧"
        except Exception as error:
            self.error = str(error)
        finally:
            with self.lock:
                self.active = False
            # Release queued image references promptly on a write failure.
            while True:
                try:
                    self.queue.get_nowait()
                except queue.Empty:
                    break
            try:
                state = self.status()
                state["finalized"] = True
                (self.directory / "recording.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as error:
                self.error = str(error)
            try:
                if self.on_done:
                    self.on_done(self.error)
            finally:
                self.done.set()


@dataclass
class Segment:
    video: Path
    index: Path
    first_ns: int
    last_ns: int
    width: int
    height: int
    bytes: int = 0
    frames: int = 0
    pins: int = 0


class ReplaySnapshot:
    def __init__(self, start_ns, end_ns):
        self.start_ns, self.end_ns = start_ns, end_ns
        self.ready = threading.Event()
        self.segments = []
        self.error = None
        self.cancelled = False


class DiskReplayBuffer:
    def __init__(self, cache_root, history_sec=120, segment_sec=2, max_bytes=1024**3,
                 queue_size=16, quality=80, min_free_bytes=128*1024**2):
        self.cache_root = Path(cache_root)
        self.cache_dir = None
        self.history_ns = int(history_sec * 1e9)
        self.segment_ns = int(segment_sec * 1e9)
        self.max_bytes, self.quality, self.min_free_bytes = max_bytes, quality, min_free_bytes
        self.queue = queue.Queue(queue_size)
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.enabled = False
        self.fps = 60.0
        self.next_due_ns = None
        self.input_frames = self.rate_skips = self.queue_drops = self.frames_saved = 0
        self.error = None
        self.segments = []
        self.current = self.writer = self.index_file = self.index_writer = None
        self.thread = None
        self.limited_by_bytes = False
        self.busy_drops = 0
        self.last_status = {"enabled": False, "target_fps": self.fps, "buffered_sec": 0,
                            "history_sec": history_sec, "frames_saved": 0, "cached_frames": 0,
                            "cache_bytes": 0, "queue_drops": 0, "busy_drops": 0,
                            "rate_skips": 0, "queue_depth": 0, "error": None}

    def enable(self, enabled, fps=60):
        if not math.isfinite(fps) or not 1 <= fps <= 240:
            raise ValueError("回放录制帧率需为1～240")
        with self.lock:
            if enabled and self.stop_event.is_set():
                raise RuntimeError("相机回放已关闭，请重启后台")
            if enabled and self.error:
                raise RuntimeError(f"回放存储已失败，请重启后台：{self.error}")
            if enabled and self.thread is None:
                self.cache_root.mkdir(parents=True, exist_ok=True)
                self.cache_dir = self.cache_root / uuid.uuid4().hex
                self.cache_dir.mkdir()
                self.thread = threading.Thread(target=self._run, name="camera-replay", daemon=True)
                self.thread.start()
            if not self.enabled or fps != self.fps:
                self.next_due_ns = None
            self.fps = fps
            self.enabled = enabled

    def submit(self, frame, wall_ns, mono_ns, frame_id):
        # A slow disk operation must not hold the camera's capture thread.
        if not self.enabled:
            return False
        if not self.lock.acquire(blocking=False):
            self.busy_drops += 1
            return False
        try:
            if not self.enabled or self.error or self.stop_event.is_set():
                return False
            self.input_frames += 1
            if self.next_due_ns is not None and mono_ns < self.next_due_ns:
                self.rate_skips += 1
                return False
            period = round(1e9 / self.fps)
            if self.next_due_ns is None:
                self.next_due_ns = mono_ns + period
            else:
                self.next_due_ns += max(1, (mono_ns - self.next_due_ns) // period + 1) * period
            try:
                self.queue.put_nowait(("frame", (frame, wall_ns, mono_ns, frame_id)))
                return True
            except queue.Full:
                self.queue_drops += 1
                return False
        finally:
            self.lock.release()

    def status(self):
        if not self.lock.acquire(blocking=False):
            return {**self.last_status, "busy": True, "busy_drops": self.busy_drops}
        try:
            parts = self.segments + ([self.current] if self.current else [])
            cutoff = time.monotonic_ns() - self.history_ns
            valid = [part for part in parts if part.last_ns >= cutoff and part.frames]
            first = max(cutoff, valid[0].first_ns) if valid else None
            last = valid[-1].last_ns if valid else None
            span = max(0, (last - first) / 1e9) if first is not None else 0
            result = {"enabled": self.enabled, "target_fps": self.fps,
                    "buffered_sec": span, "history_sec": self.history_ns / 1e9,
                    "frames_saved": self.frames_saved, "cached_frames": sum(p.frames for p in valid),
                    "cache_bytes": sum(p.bytes for p in parts), "max_cache_bytes": self.max_bytes,
                    "queue_depth": self.queue.qsize(), "queue_capacity": self.queue.maxsize,
                    "queue_drops": self.queue_drops, "rate_skips": self.rate_skips,
                    "limited_by_bytes": self.limited_by_bytes, "error": self.error,
                    "busy_drops": self.busy_drops}
            self.last_status = result
            return dict(result)
        finally:
            self.lock.release()

    def snapshot(self, end_ns):
        request = ReplaySnapshot(end_ns - self.history_ns, end_ns)
        with self.lock:
            if self.thread is None or self.error or self.stop_event.is_set():
                raise RuntimeError(self.error or "尚未开启相机回放缓冲")
            try:
                self.queue.put_nowait(("snapshot", request))
            except queue.Full:
                raise RuntimeError("回放写盘队列繁忙，请稍后再次保存") from None
        if not request.ready.wait(15):
            with self.lock:
                request.cancelled = True
                for part in request.segments:
                    part.pins -= 1
                request.segments = []
            raise RuntimeError("等待回放片段完成超时")
        if request.error:
            raise RuntimeError(request.error)
        return request

    def release(self, snapshot):
        with self.lock:
            for part in snapshot.segments:
                part.pins -= 1
            self._prune(time.monotonic_ns())
            if self.stop_event.is_set() and self.writer is None:
                self._discard_closed()

    def _delete_segment(self, part):
        # Each buffer owns one UUID directory; never touch saved exports or other runs.
        for path in (part.video, part.index):
            if path.resolve().parent != self.cache_dir.resolve():
                raise RuntimeError("拒绝删除缓存目录之外的文件")
            path.unlink(missing_ok=True)
        self.segments.remove(part)

    def _discard_closed(self):
        for part in list(self.segments):
            if not part.pins:
                self._delete_segment(part)
        if self.cache_dir and self.cache_dir.exists() and not any(self.cache_dir.iterdir()):
            self.cache_dir.rmdir()

    def _prune(self, now_ns):
        total = sum(part.bytes for part in self.segments) + (self.current.bytes if self.current else 0)
        for part in list(self.segments):
            if part.pins:
                continue
            too_old = part.last_ns < now_ns - self.history_ns
            over_budget = total > self.max_bytes
            if not too_old and not over_budget:
                continue
            if over_budget and not too_old:
                self.limited_by_bytes = True
            self._delete_segment(part)
            total -= part.bytes

    def _open_segment(self, frame, mono_ns):
        if shutil.disk_usage(self.cache_dir).free < self.min_free_bytes:
            raise OSError("磁盘剩余空间不足，回放已停止；请清理已保存录像")
        height, width = frame.shape[:2]
        stem = uuid.uuid4().hex
        self.current = Segment(self.cache_dir / (stem + ".avi"), self.cache_dir / (stem + ".csv"),
                               mono_ns, mono_ns, width, height)
        self.writer = MjpegAviWriter(self.current.video, width, height, self.fps)
        self.index_file = self.current.index.open("x", newline="", encoding="utf-8")
        self.index_writer = csv.writer(self.index_file)
        self.index_writer.writerow(INDEX_FIELDS)

    def _close_segment(self):
        if self.writer is None:
            return
        part = self.current
        span = (part.last_ns - part.first_ns) / 1e9
        try:
            self.writer.close((part.frames - 1) / span if part.frames > 1 and span else self.fps)
        finally:
            if self.index_file:
                self.index_file.close()
        part.bytes = part.video.stat().st_size + part.index.stat().st_size
        self.segments.append(part)
        self.current = self.writer = self.index_file = self.index_writer = None

    def _run(self):
        try:
            while not self.stop_event.is_set() or not self.queue.empty():
                try:
                    kind, item = self.queue.get(timeout=0.1)
                except queue.Empty:
                    with self.lock:
                        if not self.enabled:
                            self._close_segment()
                        self._prune(time.monotonic_ns())
                    continue
                if kind == "snapshot":
                    try:
                        with self.lock:
                            if item.cancelled:
                                continue
                            self._close_segment()
                            item.segments = [part for part in self.segments
                                             if part.last_ns >= item.start_ns and part.first_ns <= item.end_ns]
                            for part in item.segments:
                                part.pins += 1
                        if not item.segments:
                            item.error = "最近两分钟没有相机帧；请先开启回放并等待画面"
                    except Exception as error:
                        item.error = str(error)
                        raise
                    finally:
                        item.ready.set()
                    continue
                frame, wall_ns, mono_ns, frame_id = item
                jpeg = encode_jpeg(frame, self.quality)
                with self.lock:
                    if self.current and (mono_ns - self.current.first_ns >= self.segment_ns or
                                         (frame.shape[1], frame.shape[0]) != (self.current.width, self.current.height)):
                        self._close_segment()
                    if not self.current:
                        self._open_segment(frame, mono_ns)
                    offset = self.writer.write(jpeg)
                    self.index_writer.writerow([frame_id, mono_ns, wall_ns, offset, len(jpeg)])
                    self.current.last_ns = mono_ns
                    self.current.frames += 1
                    self.current.bytes += len(jpeg) + 100
                    self.frames_saved += 1
                    self._prune(mono_ns)
        except Exception as error:
            with self.lock:
                self.error = str(error)
                self.enabled = False
            while True:
                try:
                    kind, item = self.queue.get_nowait()
                    if kind == "snapshot":
                        item.error = self.error
                        item.ready.set()
                except queue.Empty:
                    break
        finally:
            with self.lock:
                try:
                    self._close_segment()
                    if self.stop_event.is_set():
                        self._discard_closed()
                except Exception as error:
                    self.error = str(error)

    def close(self):
        with self.lock:
            self.enabled = False
            self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=3)
            if not self.thread.is_alive():
                with self.lock:
                    self._discard_closed()


def export_replay(buffer, snapshot, directory, name):
    """Copy JPEG packets inside the requested time window to a standalone AVI."""
    directory = Path(directory)
    video_path, index_path = directory / f"{name}.avi", directory / f"{name}.index.csv"
    partial = directory / f"{name}.avi.part"
    muxer = None
    first = last = None
    count = 0
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with index_path.open("x", newline="", encoding="utf-8") as output:
            writer = csv.writer(output)
            writer.writerow(["video_frame", "frame_id", "host_receive_monotonic_ns", "host_receive_unix_ns", "elapsed_sec"])
            for part in snapshot.segments:
                with part.index.open(newline="", encoding="utf-8") as index, part.video.open("rb") as source:
                    for row in csv.DictReader(index):
                        stamp = int(row["host_receive_monotonic_ns"])
                        if not snapshot.start_ns <= stamp <= snapshot.end_ns:
                            continue
                        if muxer is None:
                            muxer = MjpegAviWriter(partial, part.width, part.height, buffer.fps)
                            first = stamp
                        if (part.width, part.height) != (muxer.width, muxer.height):
                            raise RuntimeError("回放期间图像尺寸改变，请分开保存")
                        source.seek(int(row["jpeg_offset"]))
                        jpeg = source.read(int(row["jpeg_bytes"]))
                        if len(jpeg) != int(row["jpeg_bytes"]):
                            raise OSError("缓存片段不完整")
                        muxer.write(jpeg)
                        writer.writerow([count, row["frame_id"], stamp, row["host_receive_unix_ns"], (stamp - first) / 1e9])
                        count += 1
                        last = stamp
        if not count:
            raise RuntimeError("请求的时间范围内没有相机帧")
        span = (last - first) / 1e9
        fps = (count - 1) / span if count > 1 and span else buffer.fps
        muxer.close(fps)
        partial.replace(video_path)
        return {"video": video_path.name, "index": index_path.name, "frames": count,
                "coverage_sec": span, "first_monotonic_ns": first, "last_monotonic_ns": last,
                "playback_fps": fps, "requested_window_sec": buffer.history_ns / 1e9,
                "playback_timing": "constant average FPS; use index CSV for exact host read timestamps",
                "buffer_stats": buffer.status()}
    finally:
        try:
            if muxer is not None:
                muxer.close()
        finally:
            buffer.release(snapshot)
