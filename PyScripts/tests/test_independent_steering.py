import copy
import tempfile
import unittest
from pathlib import Path
from PyScripts.independent_steering import IndependentSteering,DEFAULTS,validate_settings
from PyScripts.steering_control_pipeline import (SteeringObservation,camera_observation,
    standalone_proposal,arbitrate,line_observation)


class IndependentSteeringTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.now=10.;self.packets=[]
        self.service=IndependentSteering(Path(self.temp.name)/'independent.json',self.packets.append,
            lambda:(1,self.now,object()),clock=lambda:self.now,autostart=False)

    def arm(self,source):
        self.service.start(source);s=self.service.identifier;p=self.service.settings[source]
        self.service.feed(f"[ASSIST_ACK] id={s} action=1 source={1 if source=='imu' else 2} ok=1 lim={p['max_us']} slew={p['slew_us_s']} dir={p['direction']} reason=ok")
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
        self.arm('camera');self.service.step()
        self.assertIsNotNone(self.service.proposal)
        self.assertLess(self.service.proposal.correction,0)
        self.assertIsNotNone(self.service.jpeg)
        blank=np.full_like(image,210)
        self.service.camera=lambda:(2,self.now,blank)
        self.service.step()
        self.assertEqual(self.service.phase,'fault')
        self.assertTrue(self.packets[-1].startswith(b'@A3,'))


if __name__=='__main__':unittest.main()
