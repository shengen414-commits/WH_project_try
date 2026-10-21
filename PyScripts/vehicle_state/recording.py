"""Bounded asynchronous JSONL traces. All filesystem operations run on the writer."""
import copy
import json
import os
import queue
import threading
import time


class TraceWriter:
    def __init__(self, path, profile, queue_size=1024, max_bytes=2*1024**2):
        self.path, self.profile = path, copy.deepcopy(profile)
        self.queue = queue.Queue(queue_size)
        self.max_bytes = max_bytes
        self.pending_bytes = 0
        self.lock = threading.Lock()
        self.stop, self.done, self.ready = threading.Event(), threading.Event(), threading.Event()
        self.drops = self.written = 0
        self.error = None
        self.write_started = None
        self.last_write_ms = self.max_write_ms = 0.
        self.oldest_pending = None
        self.thread = threading.Thread(target=self._run, daemon=True, name='state-trace-writer')
        self.thread.start()

    def submit(self, kind, data):
        if self.stop.is_set() or self.error:
            return False
        try:
            encoded = (json.dumps({'kind': kind, 'data': data}, allow_nan=False)+'\n').encode('utf-8')
        except (ValueError, TypeError):
            self.drops += 1
            return False
        with self.lock:
            if self.stop.is_set() or self.error:
                return False
            if self.pending_bytes+len(encoded) > self.max_bytes:
                self.drops += 1
                return False
            stamp = time.monotonic()
            try:
                self.queue.put_nowait((stamp, encoded))
            except queue.Full:
                self.drops += 1
                return False
            self.pending_bytes += len(encoded)
            if self.oldest_pending is None:
                self.oldest_pending = stamp
            return True

    def close(self):
        self.stop.set()

    def status(self):
        now = time.monotonic()
        with self.lock:
            return {'path': str(self.path), 'drops': self.drops, 'error': self.error,
                    'done': self.done.is_set(), 'ready': self.ready.is_set(),
                    'queue_depth': self.queue.qsize(), 'queue_bytes': self.pending_bytes,
                    'queue_capacity': self.queue.maxsize, 'max_queue_bytes': self.max_bytes,
                    'pending_age_ms': (now-self.oldest_pending)*1000 if self.oldest_pending is not None else 0,
                    'write_in_progress_ms': (now-self.write_started)*1000 if self.write_started is not None else 0,
                    'last_write_ms': self.last_write_ms, 'max_write_ms': self.max_write_ms,
                    'written_events': self.written}

    def _io(self, operation):
        self.write_started = time.monotonic()
        try:
            return operation()
        finally:
            self.last_write_ms = (time.monotonic()-self.write_started)*1000
            self.max_write_ms = max(self.max_write_ms, self.last_write_ms)
            self.write_started = None

    def _open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return self.path.open('xb')

    def _run(self):
        file = None
        try:
            file = self._io(self._open)
            header = (json.dumps({'kind': 'profile', 'data': self.profile}, allow_nan=False)+'\n').encode('utf-8')
            self._io(lambda: file.write(header))
            self._io(file.flush)
            self.ready.set()
            last_flush = time.monotonic()
            while not self.stop.is_set() or not self.queue.empty():
                try:
                    _, encoded = self.queue.get(timeout=.05)
                except queue.Empty:
                    encoded = None
                if encoded is not None:
                    with self.lock:
                        self.pending_bytes -= len(encoded)
                        with self.queue.mutex:
                            self.oldest_pending = self.queue.queue[0][0] if self.queue.queue else None
                    self._io(lambda: file.write(encoded))
                    self.written += 1
                if time.monotonic()-last_flush >= .5:
                    self._io(file.flush)
                    last_flush = time.monotonic()
            self._io(file.flush)
            self._io(lambda: os.fsync(file.fileno()))
        except Exception as exc:
            self.error = str(exc)
            with self.lock:
                while True:
                    try:
                        self.queue.get_nowait(); self.drops += 1
                    except queue.Empty:
                        break
                self.pending_bytes = 0; self.oldest_pending = None
        finally:
            try:
                if file is not None:
                    self._io(file.close)
            except Exception as exc:
                self.error = str(exc)
            finally:
                self.done.set()
