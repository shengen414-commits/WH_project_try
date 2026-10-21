"""Round-trip bounded clock mapping; camera read completion is not exposure."""
from collections import deque
import math
import secrets
import threading
from functools import wraps


def synchronized(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)
    return call


class DeviceClock:
    def __init__(self):
        self.lock = threading.RLock()
        self.latest = None
        self.epoch = 0
        self.samples = deque(maxlen=12)
        self.pending = {}
        # A delayed reply from a previous observation session must not match a new probe.
        self.next_id = secrets.randbelow(0xffffffff)

    @synchronized
    def unwrap(self, raw):
        if type(raw) is not int or not 0 <= raw <= 0xffffffff:
            raise ValueError('无效设备时间')
        if self.latest is None:
            self.latest = raw
            return raw
        delta = ((raw-(self.latest & 0xffffffff)+0x80000000) & 0xffffffff)-0x80000000
        if delta < -1000:
            self.epoch += 1
            self.latest = raw
            self.samples.clear(); self.pending.clear()
            return raw
        stamp = self.latest+delta
        self.latest = max(self.latest, stamp)
        return stamp

    @synchronized
    def probe(self, now):
        self.pending = {k: t for k, t in self.pending.items() if now-t < 1}
        self.next_id = self.next_id % 0xffffffff+1
        self.pending[self.next_id] = now
        return f'!{self.next_id}\n'.encode('ascii')

    @synchronized
    def reply(self, request_id, raw_ms, received):
        sent = self.pending.pop(request_id, None)
        if sent is None or not math.isfinite(received) or not 0 <= received-sent <= .1:
            return False
        device = self.unwrap(raw_ms)/1000
        rtt = received-sent
        self.samples.append((received, (sent+received)/2-device, rtt/2+.001))
        return True

    @synchronized
    def status(self, now):
        recent = [s for s in self.samples if 0 <= now-s[0] < 10]
        if not recent:
            return {'valid': False, 'uncertainty_ms': None, 'epoch': self.epoch}
        best = min(recent, key=lambda s: s[2])
        # Bound ordinary crystal drift between probes; this is not hardware sync.
        error = best[2]+(now-best[0])*.0002
        return {'valid': error <= .02, 'offset_s': best[1],
                'uncertainty_ms': error*1000, 'epoch': self.epoch}

    @synchronized
    def stamp(self, raw_ms, now):
        device = self.unwrap(raw_ms)/1000
        status = self.status(now)
        if not status['valid']:
            return None
        stamp = device+status['offset_s']
        return stamp if -.02 <= now-stamp <= .5 else None
