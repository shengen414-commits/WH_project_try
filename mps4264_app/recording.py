"""Bounded pressure storage worker; never perform disk I/O on the UDP receiver."""
import queue
import threading
import time


class PressureWriter:
    def __init__(self, raw, index, index_writer, on_error, queue_size=128, max_bytes=4*1024**2):
        self.raw, self.index, self.index_writer, self.on_error = raw, index, index_writer, on_error
        self.queue = queue.Queue(queue_size)
        self.lock = threading.Lock()
        self.stop, self.done = threading.Event(), threading.Event()
        self.max_bytes, self.pending_bytes = max_bytes, 0
        self.dropped_frames = self.frames_saved = self.bytes_saved = 0
        self.error = None
        self.io_started = None
        self.max_io_ms = 0.
        self.thread = threading.Thread(target=self._run, name='pressure-storage', daemon=True)
        self.thread.start()

    def submit(self, decoded, mono_ns, wall_ns):
        data = b''.join(raw for raw, _ in decoded)
        rows = [(f.frame_number, mono_ns, wall_ns, f.frame_sec, f.frame_ns, f.packet_type, f.units_index)
                for _, f in decoded]
        size = len(data)+len(rows)*128
        with self.lock:
            if self.stop.is_set() or self.error:
                return False
            if self.pending_bytes+size > self.max_bytes or self.queue.full():
                self.dropped_frames += len(rows)
                return False
            self.queue.put_nowait((time.monotonic(), data, rows, size))
            self.pending_bytes += size
            return True

    def finish(self):
        self.stop.set()

    def status(self):
        with self.lock:
            return {'queue_depth': self.queue.qsize(), 'queue_capacity': self.queue.maxsize,
                    'queue_bytes': self.pending_bytes, 'max_queue_bytes': self.max_bytes,
                    'frames_saved': self.frames_saved, 'bytes_saved': self.bytes_saved,
                    'dropped_frames': self.dropped_frames, 'error': self.error,
                    'finalized': self.done.is_set(), 'max_io_ms': self.max_io_ms,
                    'write_in_progress_ms': (time.monotonic()-self.io_started)*1000 if self.io_started is not None else 0}

    def _io(self, operation):
        self.io_started = time.monotonic()
        try:
            return operation()
        finally:
            self.max_io_ms = max(self.max_io_ms, (time.monotonic()-self.io_started)*1000)
            self.io_started = None

    def _run(self):
        inflight_frames = 0
        try:
            last_flush = time.monotonic()
            while not self.stop.is_set() or not self.queue.empty():
                try:
                    _, data, rows, size = self.queue.get(timeout=.05)
                except queue.Empty:
                    data = None
                if data is not None:
                    inflight_frames = len(rows)
                    with self.lock:
                        self.pending_bytes -= size
                    self._io(lambda: self.raw.write(data))
                    self._io(lambda: self.index_writer.writerows(rows))
                    with self.lock:
                        self.bytes_saved += len(data); self.frames_saved += len(rows)
                    inflight_frames = 0
                if time.monotonic()-last_flush >= .5:
                    self._io(self.raw.flush); self._io(self.index.flush)
                    last_flush = time.monotonic()
            self._io(self.raw.flush); self._io(self.index.flush)
        except Exception as exc:
            self.dropped_frames += inflight_frames
            self._report_error(exc)
        finally:
            self.stop.set()
            with self.lock:
                while True:
                    try:
                        _, _, rows, _ = self.queue.get_nowait()
                        self.dropped_frames += len(rows)
                    except queue.Empty:
                        break
                self.pending_bytes = 0
            for file in (self.raw, self.index):
                try:
                    self._io(file.close)
                except Exception as exc:
                    self._report_error(exc)
            self.done.set()

    def _report_error(self, error):
        self.error = str(error)
        try:
            self.on_error(self.error)
        except Exception as exc:
            self.error += f'; error notification failed: {exc}'
