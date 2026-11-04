import copy
import tempfile
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from PyScripts.independent_steering import IndependentSteering,DEFAULTS,validate_settings
from PyScripts.steering_control_pipeline import (SteeringObservation,camera_observation,
    standalone_proposal,arbitrate,line_observation)


class IndependentSteeringTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.now=10.;self.packets=[]
        self.service=IndependentSteering(Path(self.temp.name)/'independent.json',self.packets.append,
            lambda:(1,self.now,object()),clock=lambda:self.now,autostart=False)
        self.service.set_camera_enabled(True)

    def arm(self,source):
        self.service.start(source);s=self.service.identifier;p=self.service.settings[source]
        bounds=f"low={p['low_us']} high={p['high_us']}"
        self.service.feed(f"[ASSIST_ACK] id={s} action=1 source={1 if source=='imu' else 2} ok=1 lim={p['max_us']} slew={p['slew_us_s']} dir={p['direction']} {bounds} reason=ok")
        return s

    def test_pure_policies_are_opposite_to_error_and_use_separate_gains(self):
        for source,o in [('imu',SteeringObservation('imu',1,10,True,1.,'ok',relative_heading_deg=10,yaw_rate_deg_s=0)),
                         ('camera',camera_observation({'valid':True,'confidence':.9,'lookahead_error_norm':.3,'image_angle_deg':0},1,10))]:
            p=standalone_proposal(o,DEFAULTS[source],10.01)
            self.assertLess(arbitrate(source,p,10.01),0)
            with self.assertRaises(ValueError):arbitrate('fusion',p,10.01)
            self.assertFalse(standalone_proposal(o,DEFAULTS[source],10.2).valid)
        self.assertIsNone(o.relative_heading_deg)
        line=line_observation({'valid':True,'fresh':True,'center_channel':0,'sequence':1},10)
        self.assertEqual(line.line_offset_norm,-1)

    def test_configuration_validation_and_persistence_never_arm(self):
        settings=copy.deepcopy(DEFAULTS['imu']);settings['kp']=7
        self.service.configure('imu',settings)
        restored=IndependentSteering(self.service.path,self.packets.append,lambda:None,autostart=False)
        self.assertEqual(restored.settings['imu']['kp'],7)
        self.assertFalse(restored.blocking());self.assertEqual(self.packets,[])
        for key,value in [('max_us',500),('direction',0),('kp',float('nan')),('slew_us_s',True)]:
            bad={**settings,key:value}
            with self.assertRaises(ValueError):validate_settings('imu',bad)
        with self.assertRaises(ValueError):self.service.start('fusion')

    def test_ack_is_source_session_and_configuration_specific(self):
        self.service.start('camera');sid=self.service.identifier
        self.assertEqual(self.service.phase,'arming')
        self.service.feed(f'[ASSIST_ACK] id={sid+1} action=1 source=2 ok=1 lim=80 slew=400 dir=1 reason=ok')
        self.assertEqual(self.service.phase,'arming')
        self.service.feed(f'[ASSIST_ACK] id={sid} action=1 source=1 ok=1 lim=80 slew=400 dir=1 reason=ok')
        self.assertEqual(self.service.phase,'fault');self.assertTrue(self.service.blocking())

    def test_camera_uses_latest_observation_and_latches_loss_without_auto_resume(self):
        sid=self.arm('camera')
        observation=camera_observation({'valid':True,'confidence':.95,'lookahead_error_norm':.3,'image_angle_deg':0},1,10)
        self.service._camera_observation=lambda now:observation
        self.service.step();self.assertTrue(self.packets[-1].startswith(f'@A2,{sid},1,-'.encode()))
        before=len(self.packets);self.service.step();self.assertEqual(len(self.packets),before)
        self.now=10.2;self.service.step()
        self.assertEqual(self.service.phase,'fault');self.assertEqual(self.packets[-1],f'@A3,{sid}\n'.encode())
        observation=camera_observation({'valid':True,'confidence':1.,'lookahead_error_norm':0,'image_angle_deg':0},2,10.2)
        self.service.step();self.assertEqual(self.packets[-1],f'@A3,{sid}\n'.encode())
        self.service.stop();self.assertTrue(self.service.blocking())
        self.service.feed(f'[FOLLOW_ACK] id={sid} action=0 ok=1')
        self.assertFalse(self.service.blocking())

    def test_imu_waits_for_clock_mapping_and_does_not_invent_lateral_error(self):
        sid=self.arm('imu')
        self.assertEqual(self.service.phase,'calibrating')
        probe=self.service.timer.probe(10.);pid=int(probe[1:])
        self.now=10.004;self.service.feed(f'[SYNC],{pid},1000')
        self.now=10.014;self.service.feed(f'[ASSIST_IMU],{sid},1010,10.0,0.0')
        self.assertEqual(self.service.phase,'running')
        o=self.service.observation
        self.assertIsNone(o.image_offset_norm);self.assertIsNone(o.line_offset_norm)
        self.assertEqual(o.relative_heading_deg,10)
        self.service.step()
        self.assertTrue(any(p.startswith(f'@A2,{sid},1,-'.encode()) for p in self.packets))
        self.service.feed(f'[ASSIST_IMU],{sid},1010,20.0,0.0')
        self.assertEqual(self.service.observation.relative_heading_deg,10)
        self.service.feed(f'[ASSIST_IMU],{sid},10,0.0,0.0')
        self.assertEqual(self.service.phase,'fault')

    def test_old_firmware_timeout_and_missing_camera_never_claim_success(self):
        self.service.camera=lambda:None
        with self.assertRaises(ValueError):self.service.start('camera')
        self.service.start('imu');self.now=13.1;self.service.step()
        self.assertEqual(self.service.phase,'fault');self.assertFalse(self.service.can_drive())

    def test_real_camera_detector_controls_only_from_fresh_detected_tape(self):
        import cv2
        import numpy as np
        from PyScripts.camera_line_detector import BlackTapeDetector
        image=np.full((240,320),210,dtype=np.uint8)
        cv2.rectangle(image,(210,0),(224,239),20,-1)
        BlackTapeDetector().detect(image)
        def fresh_frame():
            # Camera publication races just after the control loop timestamp.
            self.now+=.001
            return (1,self.now,image)
        self.service.camera=fresh_frame
        self.arm('camera');self.service.camera_step();self.service.step()
        self.assertIsNotNone(self.service.proposal)
        self.assertLess(self.service.proposal.correction,0)
        self.assertIsNotNone(self.service.jpeg)
        blank=np.full_like(image,210)
        self.service.camera=lambda:(2,self.now,blank)
        self.service.camera_step();self.service.step()
        self.assertEqual(self.service.phase,'fault')
        self.assertTrue(self.packets[-1].startswith(b'@A3,'))

    def test_camera_preview_keeps_processing_after_control_fault_and_recovers_from_read_loss(self):
        import numpy as np
        image=np.full((240,320),210,dtype=np.uint8)
        self.service.camera=lambda:(1,self.now,image)
        sid=self.arm('camera')
        self.service.camera_step();self.service.step()
        self.assertEqual(self.service.phase,'fault')  # No line: stop control only.
        self.assertTrue(self.service.camera_enabled)
        self.assertIsNotNone(self.service.preview_jpeg())
        self.assertTrue(self.packets[-1].startswith(f'@A3,{sid}'.encode()))
        packets=list(self.packets)
        self.now+=.25
        image[:,150:163]=20
        self.service.camera=lambda:(2,self.now,image)
        self.service.camera_step();self.service.step()
        self.assertEqual(self.service.phase,'fault')
        self.assertTrue(self.service.snapshot()['camera']['observation']['valid'])
        self.assertEqual(self.packets,packets)  # New valid preview cannot re-arm steering.
        self.service.camera=lambda:None
        self.service.camera_step()
        self.assertIsNone(self.service.preview_jpeg())
        self.assertIn('超时',self.service.snapshot()['camera']['error'])
        self.now+=.25
        self.service.camera=lambda:(3,self.now,image)
        self.service.camera_step()
        self.assertIsNotNone(self.service.preview_jpeg())
        self.assertEqual(self.service.phase,'fault')
        self.service.set_camera_enabled(False)
        self.assertEqual(self.service.phase,'stopping')
        self.assertIsNone(self.service.preview_jpeg())

    def test_camera_capture_switch_and_controllers_have_separate_lifetimes(self):
        owners=[]
        self.service.camera_capture=owners.append
        self.service.set_camera_enabled(False)
        self.assertEqual(owners,[False])
        with self.assertRaisesRegex(ValueError,'下方'):self.service.start('camera')
        self.service.set_camera_enabled(True)
        self.assertEqual(owners,[False,True])
        sid=self.arm('camera')
        self.service.stop()
        self.service.feed(f'[FOLLOW_ACK] id={sid} action=0 ok=1')
        self.assertTrue(self.service.camera_enabled)
        self.assertEqual(owners,[False,True])

    def test_imu_calibration_diagnostics_use_matching_session_and_survive_fault(self):
        sid=self.arm('imu')
        line=f'[ASSIST_ZERO] id={sid} n=140 reset=1 bias=8.20 sd=0.040 a=1.001 reason=collecting'
        self.assertTrue(self.service.feed(line))
        self.assertEqual(self.service.snapshot()['imu_calibration']['samples'],140)
        self.assertFalse(self.service.feed(line.replace(f'id={sid}',f'id={sid+1}')))
        self.service.feed(f'[ASSIST] id={sid} active=1 fault=1 ready=0 reason=gyro_unstable')
        self.assertEqual(self.service.phase,'fault')
        self.assertEqual(self.service.snapshot()['imu_calibration']['bias'],8.2)

    def test_frame_from_previous_enable_generation_cannot_reappear_after_toggle(self):
        observation=camera_observation({'valid':True,'confidence':.95,'lookahead_error_norm':0,'image_angle_deg':0},1,self.now)
        def finish_old_detection(now):
            self.service.set_camera_enabled(False)
            self.service.set_camera_enabled(True)
            return observation,b'old-jpeg'
        with patch.object(self.service,'_detect_camera',side_effect=finish_old_detection):
            self.service.camera_step()
        self.assertIsNone(self.service.jpeg)
        self.assertIsNone(self.service.camera_observation)

    def test_optional_zero_diagnostics_cannot_turn_invalid_metrics_into_control_fault(self):
        sid=self.arm('imu')
        for line in [f'[ASSIST_ZERO] id={sid} n=-1 reset=0 bias=0 sd=0 a=1 reason=x',
                     f'[ASSIST_ZERO] id={sid} n=1 reset=0 bias=nan sd=0 a=1 reason=x',
                     f'[ASSIST_ZERO] id={sid} incomplete']:
            self.assertFalse(self.service.feed(line))
            self.assertEqual(self.service.phase,'calibrating')

    def test_legacy_imu_profile_migrates_travel_and_retains_gains_and_camera(self):
        old=copy.deepcopy(DEFAULTS)
        old['imu'].pop('low_us');old['imu'].pop('high_us')
        old['imu'].update(kp=7.,max_us=80,direction=-1)
        old['camera']['max_us']=120
        self.service.path.write_text(json.dumps({'schema':1,'profiles':old}),encoding='utf-8')
        restored=IndependentSteering(self.service.path,self.packets.append,lambda:None,autostart=False)
        self.assertIsNone(restored.error)
        self.assertEqual((restored.settings['imu']['low_us'],restored.settings['imu']['high_us'],restored.settings['imu']['max_us']),(1130,1850,370))
        self.assertEqual((restored.settings['imu']['kp'],restored.settings['imu']['direction']),(7.,-1))
        self.assertEqual(restored.settings['camera'],{**old['camera'],'max_us':370,'low_us':1130,'high_us':1850})
        restored.configure('imu',restored.settings['imu'])
        self.assertEqual(json.loads(self.service.path.read_text())['schema'],3)
        restored.settings['imu']['max_us']=200
        restored.configure('imu',restored.settings['imu'])
        again=IndependentSteering(self.service.path,self.packets.append,lambda:None,autostart=False)
        self.assertEqual(again.settings['imu']['max_us'],200) # Migration only once, explicit later choices persist.

    def test_imu_travel_is_transmitted_and_must_be_acknowledged(self):
        self.service.start('imu');sid=self.service.identifier
        self.assertEqual(self.packets[-1],f'@A1,1,{sid},370,400,1,1130,1850\n'.encode())
        self.service.feed(f'[ASSIST_ACK] id={sid} action=1 source=1 ok=1 lim=370 slew=400 dir=1 low=1200 high=1800 reason=ok')
        self.assertEqual(self.service.phase,'fault')
        self.assertIsNone(self.service.snapshot()['output_limits'])
        for low,high in [(1099,1850),(1130,1901),(1500,1850),(1130,1500),(True,1850)]:
            with self.assertRaises(ValueError):validate_settings('imu',{**DEFAULTS['imu'],'low_us':low,'high_us':high})

    def test_schema_two_migrates_only_camera_and_schema_three_retains_custom_values(self):
        saved=copy.deepcopy(DEFAULTS)
        saved['imu'].update(max_us=200,kp=8,low_us=1200,high_us=1800)
        saved['camera'].pop('low_us');saved['camera'].pop('high_us')
        saved['camera'].update(max_us=80,kp=250)
        self.service.path.write_text(json.dumps({'schema':2,'profiles':saved}),encoding='utf-8')
        restored=IndependentSteering(self.service.path,self.packets.append,lambda:None,autostart=False)
        self.assertIsNone(restored.error)
        self.assertEqual(restored.settings['imu'],saved['imu'])
        self.assertEqual(restored.settings['camera']['max_us'],370)
        self.assertEqual(restored.settings['camera']['kp'],250)
        restored.configure('camera',{**restored.settings['camera'],'max_us':220})
        again=IndependentSteering(self.service.path,self.packets.append,lambda:None,autostart=False)
        self.assertEqual(again.settings['camera']['max_us'],220)
        self.service.start('camera');sid=self.service.identifier
        self.assertEqual(self.packets[-1],f'@A1,2,{sid},370,400,1,1130,1850\n'.encode())


if __name__=='__main__':unittest.main()
