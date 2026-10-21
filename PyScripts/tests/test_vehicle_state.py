"""Physical semantics, covariance, delayed updates and hardware-free replay."""
import copy
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PyScripts.vehicle_state.clock import DeviceClock
from PyScripts.vehicle_state.contracts import ImuObservation, SpeedObservation, LineObservation, VisionObservation
from PyScripts.vehicle_state.estimator import StateEstimator
from PyScripts.vehicle_state.profile import defaults, validate, ProfileStore, angle_to_pulse
from PyScripts.vehicle_state.service import VehicleLab
from PyScripts.replay_vehicle_state import replay


def vision(t, seq, y=.02, heading=.01):
    return VisionObservation(t, seq, y, heading, (.0001, -.00001, -.00001, .0004), .9)


class VehicleStateTests(unittest.TestCase):
    def test_unknown_geometry_is_not_invented_and_upside_down_is_right_handed(self):
        p = defaults()
        self.assertIsNone(p['geometry']['line_pitch_m'])
        self.assertEqual(p['geometry']['imu_yaw_sign'], -1)
        self.assertIsNone(p['geometry']['imu_axes'])
        p['geometry']['imu_axes'] = [1, -2, -3]
        validate(p)
        p['geometry']['imu_axes'] = [1, 2, -3]
        with self.assertRaises(ValueError): validate(p)
        p = defaults(); p['geometry']['line_pitch_m'] = float('nan')
        with self.assertRaises(ValueError): validate(p)

    def test_actuator_interpolation_direction_envelope_and_changed_center(self):
        p = defaults()
        p['actuator'] = {'center_us': 1540, 'points': [
            {'pulse_us': 1440, 'angle_deg': 10}, {'pulse_us': 1540, 'angle_deg': 0}, {'pulse_us': 1640, 'angle_deg': -12}]}
        self.assertEqual(angle_to_pulse(p, math.radians(5), 1540), 1490)
        self.assertEqual(angle_to_pulse(p, math.radians(-6), 1540), 1590)
        with self.assertRaises(ValueError): angle_to_pulse(p, math.radians(11), 1540)
        with self.assertRaises(ValueError): angle_to_pulse(p, 0, 1500)
        p['actuator']['points'][-1]['angle_deg'] = 15
        with self.assertRaises(ValueError): angle_to_pulse(p, 0, 1540)

    def test_atomic_profile_write_failure_preserves_previous_calibration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'profile.json'
            store = ProfileStore(path)
            p = store.snapshot(); p['geometry']['line_pitch_m'] = .008
            store.save(p)
            before = path.read_bytes()
            p['geometry']['line_pitch_m'] = .012
            with patch('os.replace', side_effect=OSError('disk failure')):
                with self.assertRaises(OSError): store.save(p)
            self.assertEqual(before, path.read_bytes())
            self.assertEqual(store.snapshot()['geometry']['line_pitch_m'], .008)
            self.assertEqual(ProfileStore(path).snapshot()['revision'], 1)

    def test_clock_requires_round_trip_and_handles_rollover_and_reboot(self):
        c = DeviceClock()
        self.assertIsNone(c.stamp(1000, 10))
        packet = c.probe(10)
        request = int(packet[1:])
        self.assertTrue(c.reply(request, 1000, 10.006))
        self.assertAlmostEqual(c.stamp(1010, 10.016), 10.013)
        self.assertFalse(c.reply(request, 1000, 10.01))
        self.assertFalse(c.status(25)['valid'])
        c = DeviceClock()
        self.assertEqual(c.unwrap(0xfffffff0), 0xfffffff0)
        self.assertEqual(c.unwrap(20), 0x100000014)
        c.unwrap(10000); c.unwrap(0)
        self.assertEqual(c.epoch, 1)
        self.assertFalse(c.status(10)['valid'])

    def test_delayed_camera_replays_subsequent_line_measurements_exactly(self):
        events = []
        for i in range(30):
            t = 10+i*.02
            events.extend([ImuObservation(t, i, .015, True), SpeedObservation(t, i, 1.)])
            # z = -ey - sensor_x*heading
            events.append(LineObservation(t, i, -.025, .3, 0, .0001))
        events.append(vision(10.2, 1))
        ordered = StateEstimator()
        for e in sorted(events, key=lambda x:x.time_s): ordered.submit(e)
        delayed = StateEstimator()
        for e in events: delayed.submit(e)
        for a, b in zip(ordered.core.x, delayed.core.x): self.assertAlmostEqual(a,b,places=12)
        for a, b in zip(ordered.core.p, delayed.core.p):
            for x, y in zip(a,b): self.assertAlmostEqual(x,y,places=12)
        self.assertGreater(delayed.replays, 0)
        self.assertTrue(delayed.snapshot(10.58)['valid'])
        self.assertFalse(delayed.snapshot(10.9)['valid'])

    def test_outliers_duplicates_invalid_covariance_and_old_camera_do_not_corrupt_state(self):
        e = StateEstimator(); e.submit(vision(1, 1))
        before = e.core.x[:]
        self.assertFalse(e.submit(vision(1, 1)))
        e.submit(vision(1.01, 2, 2, .3))
        self.assertEqual(e.core.x, before)
        self.assertEqual(e.core.rejected, 1)
        with self.assertRaises(ValueError): e.submit(VisionObservation(1.02,3,0,0,(1,2,2,1),.9))
        with self.assertRaises(ValueError): e.submit(ImuObservation(1.02,2,float('nan'),True))
        e.submit(ImuObservation(2, 3, 0, True))
        self.assertFalse(e.submit(vision(1, 4)))

    def test_gyro_bias_and_unknown_speed_do_not_manufacture_displacement(self):
        e=StateEstimator(); e.submit(vision(1,1,0,0))
        for i in range(1,20): e.submit(ImuObservation(1+i*.02,i,1,False))
        s=e.snapshot(1.38)
        self.assertAlmostEqual(s['lateral_error_m'],0)
        self.assertAlmostEqual(s['heading_error_rad'],0)
        self.assertIsNone(s['speed_m_s'])
        self.assertIsNone(s['yaw_rate_rad_s'])
        self.assertFalse(s['valid'])
        self.assertIsNone(s['lateral_speed_m_s'])
        self.assertFalse(s['control_permitted'])

    def test_long_sequence_keeps_covariance_symmetric_positive_and_history_bounded(self):
        e=StateEstimator()
        for i in range(600):
            t=1+i*.02
            e.submit(ImuObservation(t,i,.001,True));e.submit(SpeedObservation(t,i,1))
            if i%5==0:e.submit(vision(t,i,0,0))
        self.assertLessEqual(len(e.events),160)
        p=e.core.p
        for i in range(3):
            self.assertGreater(p[i][i],0)
            for j in range(3):self.assertAlmostEqual(p[i][j],p[j][i],places=10)
        for v in ([1,2,3],[1,-2,1],[0,1,0]):
            self.assertGreater(sum(v[i]*p[i][j]*v[j] for i in range(3) for j in range(3)),0)

    def test_service_unknown_dimensions_clock_mapping_and_z_down(self):
        with tempfile.TemporaryDirectory() as directory:
            now=[10.]
            lab=VehicleLab(ProfileStore(Path(directory)/'p.json'),Path(directory),lambda _:None,clock=lambda:now[0])
            lab.owned=True
            packet=lab.timer.probe(10); request=int(packet[1:])
            lab.feed(f'[SYNC],{request},1000',10.004)
            lab.feed('[LAB],1000,1,0',10.004)
            lab.feed('[IMU],1010,0,0,-1,0,0,-10,25,-10,OK',10.014)
            lab.feed('[OBS],0,1010,0,24,24,1010,1500,1500,1200,1800',10.014)
            now[0]=10.014
            s=lab.status()
            self.assertGreater(s['state']['yaw_rate_rad_s'],0)
            self.assertIsNone(s['state']['lateral_error_m'])
            self.assertIsNone(s['state']['speed_m_s'])
            self.assertEqual(s['raw']['line']['center_channel'],3.5)
            self.assertFalse(s['state']['valid'])
            lab.feed('[IMU],1020,nan,0,-1,0,0,0,25,0,OK',10.024)
            self.assertEqual(lab.invalid_frames,1)
            lab.invalidate('disconnect')
            self.assertFalse(lab.status()['state']['valid'])

    def test_replay_uses_same_observation_contract_without_hardware(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'trace.jsonl';out=Path(directory)/'out.csv'
            event=vision(1,1)
            from dataclasses import asdict
            source.write_text(json.dumps({'kind':'observation','data':{'type':'VisionObservation',**asdict(event)}})+'\n',encoding='utf-8')
            self.assertEqual(replay(source,out),1)
            self.assertIn('lateral_error_m',out.read_text())

    def test_calibrated_combined_stream_produces_signed_si_state(self):
        with tempfile.TemporaryDirectory() as directory:
            now=[10.]
            store=ProfileStore(Path(directory)/'p.json')
            profile=store.snapshot()
            profile['geometry'].update(line_pitch_m=.008,line_x_m=.3,line_y_m=0.,meters_per_count=.001,
                                       encoder_sign=-1,wheelbase_m=.25,imu_axes=[1,-2,-3])
            store.save(profile)
            lab=VehicleLab(store,Path(directory),lambda _:None,clock=lambda:now[0]);lab.owned=True
            packet=lab.timer.probe(10.)
            lab.feed(f'[SYNC],{int(packet[1:])},1000',10.004)
            for i in range(30):
                ms=1000+i*20;now[0]=10.004+i*.02
                lab.feed(f'[LAB],{ms},1,0',now[0])
                lab.feed(f'[IMU],{ms},0,0,-1,0,0,0,25,0,OK',now[0])
                lab.feed(f'[OBS],{i},{ms},{-i},24,24,{ms},1500,1500,1200,1800',now[0])
            lab.submit_vision(vision(now[0]-.002,100,0,0))
            state=lab.status()['state']
            self.assertAlmostEqual(state['speed_m_s'],.05,places=6)
            self.assertAlmostEqual(state['lateral_error_m'],0,places=6)
            self.assertTrue(state['valid'])
            self.assertFalse(state['control_permitted'])
            self.assertAlmostEqual(lab.raw['imu']['specific_force_body_m_s2'][2],9.80665)


if __name__ == '__main__': unittest.main()
