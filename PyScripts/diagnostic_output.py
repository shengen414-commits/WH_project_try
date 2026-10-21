"""Bounded best-effort console output, isolated from hardware receive threads."""
import queue
import threading


class DiagnosticOutput:
    def __init__(self, capacity=16):
        self.queue = queue.Queue(capacity)
        self.drops = 0
        self.thread = None
        self.lock = threading.Lock()

    def submit(self, message):
        with self.lock:
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, name='serial-diagnostics', daemon=True)
                self.thread.start()
        try:
            self.queue.put_nowait(message)
            return True
        except queue.Full:
            self.drops += 1
            return False

    def _run(self):
        while True:
            message = self.queue.get()
            try:
                print(message)
            except Exception:
                self.drops += 1
