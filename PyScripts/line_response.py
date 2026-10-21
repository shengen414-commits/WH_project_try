"""Firmware-confirmed line response settings and timestamped raw probe diagnostics."""
import math
import re
import secrets
import threading
import time


class LineResponse:
    def __init__(self, clock=time.monotonic):
        self.clock, self.lock = clock, threading.RLock()
        self.next_id = secrets.randbelow(0x7fffffff)+1
        self.epoch = secrets.token_hex(8)
        self.request, self.applied = {'status': 'idle'}, None
        self.probes = {'fresh': False, 'reason': 'waiting', 'mask': None}
        self.received = None

    def begin(self, data):
        if not isinstance(data, dict) or set(data) != {'left_us','right_us','deadband','slew_us_s','direction'}:
            raise ValueError('需要左右幅度、居中区、变化速度和方向五个参数')
        for key, low, high in [('left_us',0,500),('right_us',0,500),('slew_us_s',50,1200)]:
            if type(data[key]) is not int or not low <= data[key] <= high:
                raise ValueError(f'{key} 需为 {low}～{high} 的整数')
        if type(data['direction']) is not int or data['direction'] not in (-1,1):
            raise ValueError('方向必须为+1或-1')
        if type(data['deadband']) not in (float,int) or not math.isfinite(data['deadband']) or not 0 <= data['deadband'] <= .3:
            raise ValueError('居中区需为0～0.3')
        data = {**data, 'deadband': round(data['deadband'],3)}
        with self.lock:
            if self.snapshot()['request']['status'] == 'pending':
                raise ValueError('上一份参数尚未确认')
            self.next_id = self.next_id % 0xffffffff+1
            self.request = {'id':self.next_id, 'status':'pending', 'sent_at':self.clock(), 'settings':data,
                            'message':'参数已发送，等待新版固件确认；尚未开启转向'}
            return self.next_id, (f"@K{data['left_us']},{data['right_us']},{data['deadband']:.3f},"
                                  f"{data['slew_us_s']},{data['direction']},{self.next_id}\n").encode('ascii')

    def fail(self, identifier, error):
        with self.lock:
            if self.request.get('id') == identifier:
                self.request.update(status='error', message=error)

    def invalidate(self, error):
        with self.lock:
            self.received = None
            self.applied = None
            if self.request['status'] != 'idle': self.request.update(status='error',message=error)

    def feed(self, line, received=None):
        now = self.clock() if received is None else received
        if line.startswith('[LINE_CFG] '):
            fields = dict(re.findall(r'(\w+)=([^\s]+)',line))
            try:
                identifier, ok = int(fields['id']), int(fields['ok'])
                actual = {'left_us':int(fields['left']), 'right_us':int(fields['right']),
                          'deadband':float(fields['dead']), 'slew_us_s':int(fields['slew']), 'direction':int(fields['dir'])}
            except (ValueError,KeyError):
                return False
            with self.lock:
                if identifier != self.request.get('id') or self.request['status'] not in ('pending','timeout') or ok not in (0,1):
                    return False
                if ok and actual != self.request['settings']:
                    return False
                if ok: self.applied = actual
                self.request.update(status='confirmed' if ok else 'rejected',
                                    message='固件已确认比例参数；可开启巡线转向' if ok else '固件拒绝参数，请先退出转向及调试模式')
                return True
        if line.startswith('[LINE],'):
            try:
                tag, sequence, ms, raw, mask = line.split(',')
                sequence, ms, raw, mask = map(int,(sequence,ms,raw,mask))
                if not 0 <= raw <= 255 or not 0 <= mask <= 255: return False
            except ValueError:
                return False
            hits = [i for i in range(8) if mask & (1<<i)]
            valid = len(hits) in (1,2,3) and hits[-1]-hits[0]+1 == len(hits)
            center = sum(hits)/len(hits) if valid else None
            contiguous = bool(hits) and hits[-1]-hits[0]+1 == len(hits)
            reason = 'valid' if valid else 'waiting' if not hits else 'all_active' if len(hits)==8 else 'multiple_segments' if not contiguous else 'too_wide'
            with self.lock:
                self.probes = {'mask':mask,'raw_mask':raw,'center_channel':center,'valid':valid,'compliant':valid or not hits,'reason':reason,
                               'sequence':sequence,'device_ms':ms,
                               'side':'center' if valid and mask == 0x18 else 'left' if valid and center<3.5 else 'right' if valid else None}
                self.received = now
            return True
        return False

    def snapshot(self):
        with self.lock:
            if self.request['status']=='pending' and self.clock()-self.request['sent_at']>3:
                self.request.update(status='timeout',message='未收到参数确认，请烧录支持比例模式的新版esp32dev；参数未确认')
            fresh = self.received is not None and 0 <= self.clock()-self.received < .3
            return {'epoch':self.epoch,'request':dict(self.request), 'applied':self.applied,
                    'probes':{**self.probes,'fresh':fresh,'valid':fresh and self.probes.get('valid',False),
                              'compliant':fresh and self.probes.get('compliant',False)}}
