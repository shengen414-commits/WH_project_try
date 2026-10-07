"""Monotonic request/lock/TCP timing; never retries or changes device commands."""
from __future__ import annotations

import contextvars
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
import threading
import time
import uuid
from contextlib import contextmanager

_trace = contextvars.ContextVar("mps_timing_trace", default=None)


def milliseconds(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 3)


def begin_trace(path: str, trace_id: str | None = None):
    if not trace_id or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", trace_id):
        trace_id = uuid.uuid4().hex
    trace = {"trace_id": trace_id, "path": path, "locks": [], "commands": []}
    return trace, _trace.set(trace)


def end_trace(token) -> None:
    _trace.reset(token)


class TimedRLock:
    """Same blocking/reentrant behavior as RLock, with request-local wait times."""
    def __init__(self, name: str):
        self.name = name
        self._lock = threading.RLock()

    def acquire(self, *args, **kwargs):
        start = time.perf_counter()
        acquired = self._lock.acquire(*args, **kwargs)
        trace = _trace.get()
        if trace is not None:
            trace["locks"].append({"name": self.name, "wait_ms": milliseconds(start),
                                   "acquired": acquired})
        return acquired

    def release(self):
        self._lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        self.release()


@contextmanager
def command_timing(command: str, timeout: float | None, *, waits_for_prompt=True):
    start = time.perf_counter()
    record = {"command": command, "timeout_s": timeout,
              "waits_for_prompt": waits_for_prompt, "send_ms": None,
              "response_ms": None, "first_tcp_byte_ms": None,
              "first_text_ms": None, "rx_bytes": 0, "prompt_received": False,
              "outcome": "ok", "error": None}
    try:
        yield record
    except Exception as exc:
        record.update(outcome=type(exc).__name__, error=str(exc))
        raise
    finally:
        record["total_ms"] = milliseconds(start)
        trace = _trace.get()
        if trace is not None:
            trace["commands"].append(record)


def request_summary(trace: dict, start: float) -> dict:
    return {**trace, "server_ms": milliseconds(start),
            "controller_lock_wait_ms": round(sum(x["wait_ms"] for x in trace["locks"]
                                                  if x["name"] == "controller"), 3),
            "tcp_lock_wait_ms": round(sum(x["wait_ms"] for x in trace["locks"]
                                           if x["name"] == "tcp"), 3)}


def create_timing_logger(data_dir: str | Path):
    directory = Path(data_dir) / "diagnostics"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "command_timing.jsonl"
    logger = logging.getLogger("mps4264.timing." + uuid.uuid4().hex)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    file_handler = _ReopeningRotatingFileHandler(path, maxBytes=2*1024*1024, backupCount=3,
                                      encoding="utf-8", delay=True)
    stream_handler = logging.StreamHandler()
    for handler in (file_handler, stream_handler):
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    return logger, path


class _ReopeningRotatingFileHandler(RotatingFileHandler):
    """Release the file after each record (also permits Windows log archival)."""
    def emit(self, record):
        try:
            super().emit(record)
        finally:
            if self.stream is not None:
                self.stream.close()
                self.stream = None


def write_timing(logger, summary: dict) -> None:
    record = {"logged_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **summary}
    logger.info(json.dumps(record, ensure_ascii=False, allow_nan=False))
