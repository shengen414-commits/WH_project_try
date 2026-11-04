"""Standalone source controllers sharing the UART, camera and output gateway."""
import copy
import json
import math
import os
from pathlib import Path
import re
import secrets
import threading
import time
try:
    from .steering_control_pipeline import (SteeringObservation, camera_observation,
        standalone_proposal, arbitrate, serialize, IMU_MOUNT_AXES, observation_timeout)
    from .vehicle_state.clock import DeviceClock
except ImportError:
    from steering_control_pipeline import (SteeringObservation, camera_observation,
        standalone_proposal, arbitrate, serialize, IMU_MOUNT_AXES, observation_timeout)
    from vehicle_state.clock import DeviceClock


DEFAULTS={
    'imu':{'kp':4.,'kd':.5,'deadband':1.,'max_us':370,'slew_us_s':400,'direction':1,'low_us':1130,'high_us':1850},
    'camera':{'kp':200.,'kd':.5,'deadband':.03,'max_us':370,'low_us':1130,'high_us':1850,'slew_us_s':400,'direction':1,
              'max_width':.32,'confidence_min':.65,'target_x':.5,'roi_top':.35,'lookahead':.4,'threshold_mode':'otsu','threshold':80},
    # Reserved deliberately separate from individual test gains. No fusion mode
    # can be armed until a joint policy and its validation are implemented.
    'fusion':{'enabled':False,'policy':None}}


def validate_settings(source,data):
    if source not in ('imu','camera') or not isinstance(data,dict) or set(data)!=set(DEFAULTS[source]):
        raise ValueError('参数字段不完整或控制源不支持')
    for key,lo,hi in [('kp',0,50 if source=='imu' else 1000),('kd',0,10 if source=='imu' else 100),('deadband',0,10 if source=='imu' else .3)]:
        value=data[key]
        if type(value) not in (int,float) or not math.isfinite(value) or not lo<=value<=hi:
            raise ValueError(f'{key} 需为 {lo}～{hi} 的有限数')
    for key,lo,hi in [('max_us',10,400),('slew_us_s',50,1200)]:
        if type(data[key]) is not int or not lo<=data[key]<=hi:raise ValueError(f'{key} 需为 {lo}～{hi} 的整数')
    if type(data['direction']) is not int or data['direction'] not in (-1,1):raise ValueError('PWM方向应为±1')
    if source in ('imu','camera'):
        if (type(data['low_us']) is not int or type(data['high_us']) is not int or
                not 1100<=data['low_us']<1500<data['high_us']<=1900):
            raise ValueError('独立转向输出范围需位于1100～1900μs内，且下限<1500<上限')
    if source=='camera':
        for key,lo,hi in [('max_width',.03,.35),('confidence_min',.3,.99),('target_x',.1,.9),('roi_top',.1,.6),('lookahead',.1,.8)]:
            if type(data[key]) not in (int,float) or not math.isfinite(data[key]) or not lo<=data[key]<=hi:
                raise ValueError(f'{key} 超出 {lo}～{hi}')
        if data['threshold_mode'] not in ('otsu','fixed','adaptive') or type(data['threshold']) is not int or not 0<=data['threshold']<=255:
            raise ValueError('相机阈值设置无效')
    return copy.deepcopy(data)


class IndependentSteering:
    def __init__(self,path,write,camera,clock=time.monotonic,probe=None,autostart=True,camera_capture=None):
        self.path=Path(path);self.write=write;self.camera=camera;self.clock=clock;self.probe=probe;self.autostart=autostart
        self.camera_capture=camera_capture or (lambda enabled:None)
        self.camera_enabled=False;self.camera_worker=None;self.camera_shutdown=threading.Event();self.camera_wakeup=threading.Event()
        self.camera_generation=0;self.camera_observation=None;self.camera_error=None;self.jpeg_at=None;self.jpeg_source_at=None;self.camera_processing_ms=None;self.camera_timings={}
        self.zero_status={};self.fault_detail=None;self.last_target_at=None;self.output_limits=None;self.detector_signature=None;self.camera_toggle_lock=threading.Lock()
        self.lock=threading.RLock();self.settings=copy.deepcopy(DEFAULTS)
        self.owned=False;self.source=None;self.phase='off';self.message='独立转向未开启'
        self.identifier=0;self.sequence=0;self.started=0.;self.received=None
        self.observation=self.proposal=None;self.firmware={};self.error=None;self.jpeg=None;self.jpeg_sequence=0
        self.stop_event=threading.Event();self.worker=None;self.timer=DeviceClock();self.last_probe=-100.;self.last_imu_ms=None
        self.last_frame=None;self.detector=None;self.last_preview=-100.;self.request={'status':'idle'}
        self.camera_invalid_since=None;self.camera_invalid_reason=None
        if self.path.exists():
            try:
                saved=json.loads(self.path.read_text(encoding='utf-8'))
                if saved.get('schema') not in (1,2,3,4):raise ValueError('配置版本不支持')
                profiles=copy.deepcopy(saved['profiles'])
                if saved['schema']==1:
                    # One-time migration: adopt the explicitly requested measured IMU travel,
                    # retaining gains/rate/direction; camera migration follows below.
                    profiles['imu'].update(max_us=370,low_us=1130,high_us=1850)
                if saved['schema']<3:
                    profiles['camera'].update(max_us=370,low_us=1130,high_us=1850)
                profiles['camera'].setdefault('max_width',.32)
                for source in ('imu','camera'):self.settings[source]=validate_settings(source,profiles[source])
            except (ValueError,TypeError,KeyError,OSError) as exc:
                self.error=f'独立转向参数未载入：{exc}；请检查并保存参数后再开启'

    def set_camera_enabled(self, enabled):
        if type(enabled) is not bool:raise ValueError('enabled需为布尔值')
        with self.camera_toggle_lock:
            if not enabled and self.owned and self.source=='camera':self.stop()
            self.camera_capture(enabled)
            with self.lock:
                if enabled==self.camera_enabled:return
                self.camera_enabled=enabled;self.camera_generation+=1
                self.camera_observation=None;self.camera_error=None;self.jpeg=None;self.jpeg_at=None;self.jpeg_source_at=None
                if enabled:
                    self.last_frame=None
                    if self.autostart and (self.camera_worker is None or not self.camera_worker.is_alive()):
                        self.camera_worker=threading.Thread(target=self._camera_work,name='steering-camera-preview',daemon=True)
                        self.camera_worker.start()
            self.camera_wakeup.set()

    def preview_jpeg(self):
        with self.lock:
            stamp=self.jpeg_source_at if self.jpeg_source_at is not None else self.jpeg_at
            if (not self.camera_enabled or stamp is None or self.jpeg_at is None or
                    not 0<=self.clock()-stamp<=1. or not 0<=self.clock()-self.jpeg_at<=1.):return None
            return self.jpeg

    def camera_step(self):
        with self.lock:
            if not self.camera_enabled:return
            generation=self.camera_generation
        started=self.clock()
        try:
            def publish(observation):
                with self.lock:
                    if generation!=self.camera_generation:return
                    self.camera_observation=observation;self.camera_error=None
                    self.camera_processing_ms=(self.clock()-started)*1000
                if self.owned and self.source=='camera':self.step()
            result=self._detect_camera(started,publish=publish)
            with self.lock:
                if generation!=self.camera_generation:return
                if result is not None:
                    observation,jpeg=result
                    self.camera_observation=observation
                    self.camera_processing_ms=(self.clock()-started)*1000
                    if jpeg is not None:
                        self.jpeg=jpeg;self.jpeg_sequence+=1;self.jpeg_at=self.last_preview=self.clock();self.jpeg_source_at=observation.observed_at
                self.camera_error=None
        except Exception as exc:
            with self.lock:
                if generation!=self.camera_generation:return
                self.camera_error=str(exc) # Keep the last image for at most 1s, explicitly marked stale.
        # A bad frame only ends control. The preview worker keeps observing/recovering.

    def _camera_work(self):
        while not self.camera_shutdown.is_set():
            started=self.clock()
            self.camera_step()
            # Submit a newly computed camera result without waiting another host
            # control period. CV and serial feed never share the receiver lock.
            if self.owned and self.source=='camera':
                try:self.step()
                except Exception as exc:self._fail(str(exc))
            self.camera_wakeup.wait(max(.005,.04-(self.clock()-started)) if self.camera_enabled else .2)
            self.camera_wakeup.clear()

    def blocking(self):return self.owned

    def can_drive(self):
        with self.lock:
            return self.owned and self.phase=='running' and self.camera_invalid_since is None and self.firmware.get('ready')=='1' and self.proposal is not None and self.proposal.valid and 0<=self.clock()-self.proposal.observed_at<=observation_timeout(self.source)

    def configure(self,source,data):
        candidate=validate_settings(source,data)
        with self.lock:
            if self.owned:raise ValueError('先关闭独立转向，再修改参数')
            profiles=copy.deepcopy(self.settings);profiles[source]=candidate
            self.path.parent.mkdir(parents=True,exist_ok=True)
            temporary=self.path.with_suffix('.tmp')
            try:
                with temporary.open('w',encoding='utf-8') as f:
                    json.dump({'schema':4,'profiles':profiles},f,ensure_ascii=False,allow_nan=False,indent=2)
                    f.flush();os.fsync(f.fileno())
                os.replace(temporary,self.path)
            finally:
                if temporary.exists():temporary.unlink()
            changed=source=='camera' and candidate!=self.settings['camera']
            self.settings=profiles;self.error=None
            if changed:
                self.camera_generation+=1;self.camera_observation=None;self.camera_error=None
                self.camera_wakeup.set()

    def start(self,source):
        with self.lock:
            if source not in ('imu','camera'):raise ValueError('只允许 IMU 或相机独立模式；联合决策尚未启用')
            if self.owned or self.worker is not None and self.worker.is_alive():raise ValueError('先关闭上一种独立模式')
            if self.error:raise ValueError(self.error)
            if source=='camera':
                if not self.camera_enabled:raise ValueError('请先开启下方相机检测预览，等待新鲜图像后再开启转向')
                frame=self.camera()
                if frame is None or not 0<=self.clock()-frame[1]<=observation_timeout('camera'):raise ValueError('检测相机没有新鲜图像，请检查下方检测预览状态与相机连接')
            if source=='camera':
                self.camera_observation=None;self.camera_error=None
                self.camera_wakeup.set()
            self.identifier=secrets.randbelow(0x7fffffff)+1
            self.source=source;self.started=self.clock();self.received=None;self.owned=True;self.phase='arming'
            self.observation=self.proposal=None;self.firmware={};self.sequence=0;self.last_imu_ms=None
            self.timer=DeviceClock();self.last_probe=-100.;self.zero_status={};self.output_limits=None;self.fault_detail=None;self.last_target_at=None
            self.camera_invalid_since=None;self.camera_invalid_reason=None
            self.stop_event=threading.Event();self.request={'action':'start','status':'pending','id':self.identifier}
            p=self.settings[source];number=1 if source=='imu' else 2
            self.message='等待固件确认；IMU需保持车身静止完成零偏标定' if source=='imu' else '等待固件确认相机独立模式'
            bounds=f",{p['low_us']},{p['high_us']}"
            try:self.write(f"@A1,{number},{self.identifier},{p['max_us']},{p['slew_us_s']},{p['direction']}{bounds}\n".encode('ascii'))
            except Exception as exc:
                self.phase='fault';self.message=str(exc);self.request['status']='error';raise
            if self.autostart:
                self.worker=threading.Thread(target=self._work,name='independent-steering',daemon=True);self.worker.start()

    def stop(self):
        with self.lock:
            if not self.owned:return
            self.phase='stopping';self.stop_event.set();self.message='已请求归中停动力，等待固件退出确认'
            self.stop_at=self.clock()
            self.request={'action':'stop','status':'pending','id':self.identifier}
            # Tagged gateway stop plus old firmware's tagged global OFF fallback.
            self.write(f'@A0,{self.identifier}\nN0,{self.identifier}\n'.encode('ascii'))

    def invalidate(self,message):
        with self.lock:
            if self.owned:
                self.phase='fault';self.message=message;self.stop_event.set();self.request['status']='error'

    def _fail(self,message,observation=None):
        with self.lock:
            if not self.owned or self.phase in ('stopping','fault'):return
            if observation is None:observation=self.camera_observation if self.source=='camera' else self.observation
            now=self.clock()
            self.fault_detail={'reason':message,'at':now,
                'observation_age_ms':(now-observation.observed_at)*1000 if observation else None,
                'confidence':observation.confidence if observation else None,
                'confidence_min':self.settings['camera']['confidence_min'] if self.source=='camera' else None,
                'processing_ms':self.camera_processing_ms,'timings':dict(self.camera_timings)}
            self.phase='fault';self.message=message;self.stop_event.set();self.request['status']='error'
            try:self.write(f'@A3,{self.identifier}\n'.encode('ascii'))
            except OSError:pass # Firmware's 250 ms target watchdog remains the final guard.

    def feed(self,line,received=None):
        now=self.clock() if received is None else received
        if not self.owned:return False
        with self.lock:
            try:
                if line.startswith('[ASSIST_ZERO] '):
                    # Optional diagnostics cannot arm control or invalidate valid control feedback.
                    try:
                        f=dict(re.findall(r'(\w+)=([^\s]+)',line))
                        if int(f['id'])!=self.identifier or self.source!='imu':return False
                        values={key:float(f[key]) for key in ('bias','sd','a')}
                        samples,restarts=int(f['n']),int(f['reset'])
                        if (not all(math.isfinite(v) for v in values.values()) or
                            not 0<=samples<=200 or not 0<=restarts<=100000 or values['sd']<0 or values['a']<0):return False
                        self.zero_status={**values,'samples':samples,'restarts':restarts,'reason':f['reason']}
                        return True
                    except (KeyError,ValueError,TypeError,OverflowError):return False
                if line.startswith('[SYNC],'):
                    _,identifier,ms=line.split(',');self.timer.reply(int(identifier),int(ms),now);return True
                if line.startswith('[ASSIST_IMU],'):
                    _,identifier,ms,heading,rate=line.split(',');identifier,ms=int(identifier),int(ms)
                    heading,rate=float(heading),float(rate)
                    if identifier!=self.identifier or self.source!='imu' or self.phase not in ('calibrating','running'):return False
                    if not 0<=ms<=0xffffffff or not all(math.isfinite(v) for v in (heading,rate)) or abs(heading)>180.01 or abs(rate)>501:
                        raise ValueError('IMU反馈范围错误')
                    if self.last_imu_ms is not None:
                        delta=(ms-self.last_imu_ms)&0xffffffff
                        if delta==0:return False
                        if delta>200:raise ValueError('IMU设备时间倒退、重启或采样中断')
                    self.last_imu_ms=ms
                    stamp=self.timer.stamp(ms,now)
                    if stamp is None:return False
                    stamp-=self.timer.status(now)['uncertainty_ms']/1000
                    self.observation=SteeringObservation('imu',ms,stamp,True,1.,'ok',relative_heading_deg=heading,yaw_rate_deg_s=rate,device_time_ms=ms)
                    if self.phase=='calibrating':self.phase='running';self.message='IMU相对航向保持已确认'
                    return True
                if line.startswith(('[ASSIST_ACK] ','[ASSIST] ','[FOLLOW_ACK] ')):
                    f=dict(re.findall(r'(\w+)=([^\s]+)',line))
                    if int(f['id'])!=self.identifier:return False
                    if line.startswith('[FOLLOW_ACK] '):
                        if self.phase=='stopping' and f.get('action')=='0' and f.get('ok')=='1':self._stopped()
                        return True
                    if line.startswith('[ASSIST_ACK] '):
                        action,ok=int(f['action']),int(f['ok'])
                        if self.phase=='stopping' and action==0 and ok==1:self._stopped();return True
                        if self.phase!='arming' or action!=1:return False
                        p=self.settings[self.source]
                        expected={'source':1 if self.source=='imu' else 2,'lim':p['max_us'],'slew':p['slew_us_s'],
                                  'dir':p['direction'],'low':p['low_us'],'high':p['high_us']}
                        self.request['expected']=dict(expected);self.request['ack']=dict(f)
                        differences=[f"{key}:请求{value}/回包{f.get(key,'缺失')}" for key,value in expected.items() if f.get(key)!=str(value)]
                        if ok!=1 or differences:
                            cause=f.get('reason','未提供原因')
                            explanation={'controller_owned':'固件仍被已有控制会话占用','invalid_parameters':'参数超出固件允许范围',
                                'debug_mode_active':'固件仍在串口调试模式','emergency_active':'急停保护仍生效',
                                'rc_takeover':'遥控器正在接管','throttle_not_neutral':'固件油门尚未归中1500',
                                'other_controller_active':'其他自动转向仍在运行','pwm_attach_failed':'舵机PWM初始化失败',
                                'arm_rejected':'固件拒绝，当前版本未提供细分原因'}.get(cause,cause)
                            cause=f'{explanation} [{cause}]'
                            self.phase='fault';self.message=(f'固件拒绝开启（{cause}）' if ok!=1 else '固件参数确认不一致')
                            if differences:self.message+='；'+'，'.join(differences)
                            if 'low' not in f or 'high' not in f:self.message+='；回包缺少专用行程，请确认已烧录支持相机1130～1850μs行程的新版esp32dev'
                            self.request['status']='rejected';self.stop_event.set();return True
                        self.output_limits={'low_us':p['low_us'],'center_us':1500,'high_us':p['high_us']}
                        self.phase='calibrating' if self.source=='imu' else 'running';self.request['status']='confirmed'
                        self.message='IMU静止标定中，请保持车身不动' if self.source=='imu' else '相机独立转向已确认'
                        self.received=now;return True
                    if self.phase in ('arming','stopping'):return False
                    self.received=now;self.firmware=f
                    if f.get('active')=='0':self._stopped();return True
                    if f.get('fault')=='1':
                        if self.phase!='fault':self.message=f.get('reason','firmware_fault')
                        self.phase='fault';self.stop_event.set();return True
                    if self.phase=='calibrating' and f.get('ready')=='1':self.phase='running';self.message='IMU相对航向保持已确认，当前朝向为0°'
                    return True
                if line.startswith(('[SERVO]','[MPU]','[LAB],')) or line.startswith('[FOLLOW] mode=LINE'):
                    self._stopped();self.message='固件已切换到其他模式';return True
            except (KeyError,ValueError,TypeError,OverflowError):
                # Parsing only: reader never blocks on UART writes or CV work.
                self.phase='fault';self.message='独立转向反馈无效';self.stop_event.set();self.request['status']='error'
            return False

    def _stopped(self):
        self.owned=False;self.phase='off';self.stop_event.set();self.request['status']='confirmed';self.message='已归中停动力并退出独立模式'

    def _detect_camera(self,now,publish=None):
        try:from .camera_line_detector import BlackTapeDetector,DetectorConfig,render_overlay
        except ImportError:from camera_line_detector import BlackTapeDetector,DetectorConfig,render_overlay
        import cv2
        import time as timing
        total_start=timing.perf_counter();cpu_start=timing.thread_time()
        frame=self.camera()
        # Capture can publish a newer frame after step() took its loop timestamp.
        # Test age after reading the immutable frame tuple, never against that
        # earlier timestamp (which would incorrectly label a new frame future).
        if frame is None or not 0<=self.clock()-frame[1]<=1.:raise ValueError('相机超过1秒未出图或已关闭')
        sequence,read_at,image=frame
        if sequence==self.last_frame:return None
        self.last_frame=sequence;p=self.settings['camera']
        signature=tuple(p[key] for key in ('roi_top','target_x','lookahead','threshold_mode','threshold','max_width'))
        if self.detector is None or signature!=self.detector_signature:
            self.detector_signature=signature
            self.detector=BlackTapeDetector(DetectorConfig(roi_top=p['roi_top'],target_x=p['target_x'],lookahead=p['lookahead'],threshold_mode=p['threshold_mode'],threshold=p['threshold'],max_width=p['max_width']))
        input_age=max(0,(self.clock()-read_at)*1000)
        resize_start=timing.perf_counter()
        image=cv2.resize(image,(256,192))
        detect_start=timing.perf_counter()
        result,_,_=self.detector.detect(image)
        detect_end=timing.perf_counter()
        observation=camera_observation(result,sequence,read_at,p['confidence_min'])
        self.camera_timings={'input_age_ms':input_age,'resize_ms':(detect_start-resize_start)*1000,
            'detect_ms':(detect_end-detect_start)*1000,'preview_ms':0.,
            'total_ms':(detect_end-total_start)*1000,'thread_cpu_ms':(timing.thread_time()-cpu_start)*1000,
            'cv_threads':cv2.getNumThreads(),'work_width':256}
        if publish is not None:publish(observation)
        # Publish the overlay together with its observation only if this enable generation still matches.
        jpeg=None
        if self.clock()-self.last_preview>=.2:
            fresh=0<=self.clock()-read_at<=observation_timeout('camera')
            preview_result={**result,'control_valid':observation.valid and fresh}
            gate='STALE' if not fresh else 'OK' if observation.valid else f"{observation.reason} {observation.confidence:.2f}<{p['confidence_min']:.2f}"
            overlay=render_overlay(image,preview_result,self.detector.config,control_label=f'PREVIEW gate={gate}')
            overlay=cv2.resize(overlay,(320,240));ok,encoded=cv2.imencode('.jpg',overlay,[cv2.IMWRITE_JPEG_QUALITY,65])
            if ok:jpeg=encoded.tobytes()
        end=timing.perf_counter()
        self.camera_timings={'input_age_ms':input_age,'resize_ms':(detect_start-resize_start)*1000,
            'detect_ms':(detect_end-detect_start)*1000,'preview_ms':(end-detect_end)*1000,
            'total_ms':(end-total_start)*1000,'thread_cpu_ms':(timing.thread_time()-cpu_start)*1000,
            'cv_threads':cv2.getNumThreads(),'work_width':256}
        return observation,jpeg

    def _camera_observation(self, now):
        # Preview has its own worker; the controller consumes its latest immutable result.
        if self.camera_error:raise ValueError(self.camera_error)
        return self.camera_observation

    def _camera_gap(self,reason,identifier,observation=None):
        """Keep the session alive with neutral targets, never replay old steering."""
        with self.lock:
            if not self.owned or self.identifier!=identifier or self.phase!='running':return
            now=self.clock()
            if self.camera_invalid_since is None:self.camera_invalid_since=now
            self.camera_invalid_reason=reason
            if now-self.camera_invalid_since>=1.:
                self._fail(f'相机观测连续无效超过1秒：{reason}',observation);return
            if self.last_target_at is None or now-self.last_target_at>=.035:
                self.sequence=self.sequence%0xffffffff+1
                self.write(f'@A2,{identifier},{self.sequence},0.0000\n'.encode('ascii'))
                self.last_target_at=now

    def step(self):
        """One bounded control iteration, also callable with fake hardware in tests."""
        with self.lock:
            if not self.owned or self.phase in ('off','stopping','fault'):return
            now=self.clock();phase=self.phase;source=self.source;identifier=self.identifier
            if phase=='arming':
                if now-self.started>3:self._fail('3秒内未收到独立转向确认，请烧录新版固件并退出重试')
                return
            if phase=='calibrating':
                if now-self.started>6:self._fail('IMU标定超时，请静止后重新开启')
            elif self.received is not None and now-self.received>.7:
                self._fail('固件状态超时');return
            if source=='imu' and now-self.last_probe>=.5:
                if self.probe:self.probe(self.timer)
                else:self.write(self.timer.probe(now))
                self.last_probe=now
        # Never hold the receiver lock during CV, JPEG encoding, or image fetch.
        if phase=='calibrating':return
        if source=='camera':
            try:observation=self._camera_observation(now)
            except Exception as exc:
                self._camera_gap(str(exc),identifier);return
            if observation is None:
                self._camera_gap('等待相机观测',identifier);return
            if not observation.valid or not 0<=self.clock()-observation.observed_at<=observation_timeout('camera'):
                self._camera_gap(observation.reason if not observation.valid else 'observation_stale',identifier,observation);return
        else:observation=self.observation
        if observation is None:
            # Initial IMU data may await the first clock probe. Gateway watchdog
            # will still stop motion if this wait lasts more than 250 ms.
            return
        if source=='camera':
            with self.lock:
                if self.proposal is not None and observation.sequence==self.proposal.sequence:
                    # The same already-used result ages while the next frame is
                    # being computed. Never replay that steering demand; enter
                    # the neutral recovery window before the UART watchdog.
                    if self.camera_invalid_since is not None or self.last_target_at is not None and self.clock()-self.last_target_at>=.15:
                        self._camera_gap('等待新的相机观测',identifier,observation)
                    return
        proposal=standalone_proposal(observation,self.settings[source],self.clock())
        if not proposal.valid:
            if source=='camera':
                self._camera_gap(proposal.reason,identifier,observation);return
            with self.lock:
                if identifier==self.identifier:self.observation=observation;self.proposal=proposal
            self._fail(f'观测无效：{proposal.reason}',observation);return
        with self.lock:
            if not self.owned or identifier!=self.identifier or self.phase!='running':return
            if self.proposal is not None and proposal.sequence==self.proposal.sequence:return
            output_at=self.clock()
            if source=='camera' and not 0<=output_at-proposal.observed_at<=observation_timeout('camera'):
                self._camera_gap('observation_stale',identifier,observation);return
            demand=arbitrate(source,proposal,output_at)
            if source=='camera':self.camera_invalid_since=None;self.camera_invalid_reason=None
            self.observation=observation;self.proposal=proposal
            self.sequence=self.sequence%0xffffffff+1
            self.write(f'@A2,{identifier},{self.sequence},{demand:.4f}\n'.encode('ascii'))
            self.last_target_at=self.clock()

    def _work(self):
        while not self.stop_event.is_set():
            start=self.clock()
            try:self.step()
            except Exception as exc:self._fail(str(exc))
            self.stop_event.wait(max(.005,.04-(self.clock()-start)))

    def snapshot(self):
        with self.lock:
            now=self.clock()
            if self.phase=='stopping' and self.request['status']=='pending' and now-self.stop_at>3:
                self.request['status']='timeout';self.message='未收到退出确认，请检查新版固件和串口，不能确认已退出'
            return {'source':self.source,'blocking':self.owned,'phase':self.phase,'message':self.message,
                    'request':dict(self.request),'firmware':dict(self.firmware),'fault_detail':copy.deepcopy(self.fault_detail),
                    'fresh':self.received is not None and 0<=now-self.received<.7,
                    'settings':copy.deepcopy(self.settings),'settings_error':self.error,
                    'observation':serialize(self.observation),'proposal':serialize(self.proposal),
                    'observation_age_ms':max(0,(now-self.observation.observed_at)*1000) if self.observation else None,
                    'jpeg_sequence':self.jpeg_sequence,'imu_mount_axes':list(IMU_MOUNT_AXES),
                    'imu_calibration':dict(self.zero_status),'output_limits':copy.deepcopy(self.output_limits),
                    'camera_recovery':{'active':self.camera_invalid_since is not None and self.phase=='running',
                        'elapsed_ms':max(0,(now-self.camera_invalid_since)*1000) if self.camera_invalid_since is not None else 0,
                        'reason':self.camera_invalid_reason,'grace_ms':1000,'observation_timeout_ms':500},
                    'camera':{'enabled':self.camera_enabled,'error':self.camera_error,
                        'observation':serialize(self.camera_observation),
                        'observation_age_ms':max(0,(now-self.camera_observation.observed_at)*1000) if self.camera_observation else None,
                        'processing_ms':self.camera_processing_ms,'timings':dict(self.camera_timings),'jpeg_sequence':self.jpeg_sequence,
                        'preview_age_ms':max(0,(now-self.jpeg_source_at)*1000) if self.jpeg_source_at is not None else None,
                        'preview_stale':self.jpeg_source_at is None or not 0<=now-self.jpeg_source_at<=observation_timeout('camera')},
                    'clock':self.timer.status(now)}

    def shutdown(self):
        self.camera_shutdown.set();self.camera_wakeup.set()
        self.camera_capture(False)
        with self.lock:
            self.camera_enabled=False;self.camera_generation+=1;self.jpeg=None;self.jpeg_at=None;self.jpeg_source_at=None
        if self.camera_worker and self.camera_worker is not threading.current_thread():self.camera_worker.join(1)
        if self.owned:
            try:self.stop()
            except OSError:self.stop_event.set()
        if self.worker and self.worker is not threading.current_thread():self.worker.join(1)
