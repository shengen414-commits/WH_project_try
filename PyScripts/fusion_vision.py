"""Own the native detector subprocess and publish one immutable ground observation."""
import os
from pathlib import Path
import subprocess
import threading
import time

try:
    from .vehicle_state.vision import ground_observation, validate_camera
except ImportError:
    from vehicle_state.vision import ground_observation, validate_camera


class FusionVision:
    def __init__(self, camera, calibration, settings, clock=time.monotonic):
        self.camera = camera
        self.calibration = validate_camera(calibration)
        if self.calibration['latency_ms'] is None:
            raise ValueError('联合转向需要标定左相机的曝光至读帧延迟')
        self.clock = clock
        self.settings = settings
        self.latest = None
        self.error = None
        self.stop_event = threading.Event()
        suffix = '.exe' if os.name == 'nt' else ''
        default = Path(__file__).resolve().parent / 'native' / ('line_detector'+suffix)
        binary = Path(os.environ.get('CAR_NATIVE_LINE_DETECTOR', default)).resolve()
        if not binary.is_file():
            raise ValueError('没有找到 C++ 中线程序，请先编译 PyScripts/native/line_detector.cpp')
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        self.process = subprocess.Popen([str(binary)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, **options)
        self.thread = threading.Thread(target=self._work, name='fusion-vision', daemon=True)
        self.thread.start()

    def _work(self):
        import cv2

        last = None
        params = f"{self.settings['roi_top']:.4f} {self.settings['max_width']:.4f}\n".encode('ascii')
        try:
            while not self.stop_event.is_set():
                started = self.clock()
                packet = self.camera()
                if packet is not None and packet[0] != last and 0 <= started-packet[1] < .15:
                    last, read_at, frame = packet
                    h,w = frame.shape[:2]
                    image = cv2.resize(frame, (256,192), interpolation=cv2.INTER_AREA)
                    if image.ndim == 3:
                        image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
                    self.process.stdin.write(params+image.tobytes())
                    self.process.stdin.flush()
                    fields = self.process.stdout.readline(1024).split()
                    if not fields:
                        raise OSError('C++ 中线进程已退出')
                    self.latest = None
                    if fields != [b'0']:
                        count, confidence = int(fields[0]), float(fields[1])
                        if not 6 <= count <= 18 or len(fields) != 2+2*count:
                            raise OSError('C++ 中线进程返回了异常数据')
                        points = [(float(fields[i]), float(fields[i+1])) for i in range(2,len(fields),2)]
                        result = dict(points=points, width=256, height=192, valid=True, confidence=confidence)
                        try:
                            self.latest = ground_observation(result, (w,h), self.calibration, read_at, last)
                        except ValueError as exc:
                            self.error = str(exc)
                        else:
                            self.error = None
                    else:
                        self.error = 'C++ 检测未找到可信的直线胶带'
                self.stop_event.wait(max(.003, .04-(self.clock()-started)))
        except (OSError, ValueError, cv2.error) as exc:
            self.latest = None
            self.error = str(exc)

    def close(self):
        self.stop_event.set()
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=1)
        self.thread.join(timeout=1)
        if not self.thread.is_alive():
            self.process.stdin.close()
            self.process.stdout.close()
