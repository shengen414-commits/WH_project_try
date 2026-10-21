import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch
from flask import Flask
from PyScripts.vehicle_state.profile import ProfileStore
from PyScripts.vehicle_state.service import VehicleLab
from PyScripts.vehicle_state.web import register_vehicle_lab


class VehicleLabWebTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.root=Path(self.directory.name)
        self.now=[10.]
        self.writes=[]
        self.lab=VehicleLab(ProfileStore(self.root/'profile.json'),self.root,self.writes.append,clock=lambda:self.now[0])
        self.manual={'active':False,'fresh':False,'ready':False,'precise':False}
        @contextmanager
        def operation(_): yield
        project=Path(__file__).resolve().parents[1]
        app=Flask(__name__,template_folder=str(project/'templates'),static_folder=str(project/'static'))
        register_vehicle_lab(app,self.lab,operation,lambda:self.manual)
        self.client=app.test_client()

    def tearDown(self):
        self.lab.shutdown(2)
        if self.lab.trace:
            self.lab.trace.close(); self.assertTrue(self.lab.trace.done.wait(2))
        self.directory.cleanup()

    def test_observation_requires_firmware_confirmation_zero_and_exit_confirmation(self):
        response=self.client.post('/vehicle-lab/command',json={'action':'start'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json['request']['status'],'pending')
        self.assertFalse(response.json['active'])
        self.assertTrue(self.writes[0].startswith(b'U\n!'))
        self.lab.feed('[ENC],0,1000,0',10.)
        self.assertTrue(self.lab.owned) # Queued normal data cannot confirm entry/exit.
        self.lab.feed('[LAB],1000,1,0',10.)
        self.assertTrue(self.lab.status()['active'])
        self.assertEqual(self.client.post('/vehicle-lab/command',json={'action':'zero'}).status_code,200)
        self.lab.feed('[LAB],1020,1,0',10.02)
        self.assertEqual(self.lab.request['status'],'pending')
        self.lab.feed('[LAB],1040,1,1',10.04)
        self.lab.feed('[LAB],3000,1,0',12.)
        self.assertEqual(self.lab.request['status'],'confirmed')
        self.client.post('/vehicle-lab/command',json={'action':'stop'})
        self.assertTrue(self.lab.blocking())
        self.lab.feed('[ENC],1,3001,0',12.001)
        self.assertFalse(self.lab.blocking())
        self.assertEqual(self.writes[-1],b'R\n')

    def test_profile_rejects_unconfirmed_actuator_points_and_changes_during_observation(self):
        self.assertEqual(self.client.post('/vehicle-lab/profile',json={'actuator':{}}).status_code,409)
        self.assertEqual(self.client.post('/vehicle-lab/actuator/point',json={'angle_deg':5}).status_code,409)
        profile=self.lab.store.snapshot()
        self.assertEqual(self.client.post('/vehicle-lab/profile',json={'geometry':profile['geometry']}).status_code,200)
        self.lab.owned=True
        self.assertEqual(self.client.post('/vehicle-lab/profile',json={'geometry':profile['geometry']}).status_code,409)

    def test_manual_point_uses_acknowledged_target_and_preview_never_sends_command(self):
        self.manual.update(active=True,fresh=True,ready=True,precise=True,center_us=1540,target_us=1440,
                           request={'status':'pending','sent_at':9})
        self.assertEqual(self.client.post('/vehicle-lab/actuator/point',json={'angle_deg':-10}).status_code,409)
        self.manual['request']['status']='confirmed'
        for pulse,angle in [(1440,-10),(1540,0),(1640,10)]:
            self.manual['target_us']=pulse
            self.assertEqual(self.client.post('/vehicle-lab/actuator/point',json={'angle_deg':angle}).status_code,200)
        response=self.client.post('/vehicle-lab/actuator/preview',json={'angle_deg':5})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json['pulse_us'],1590)
        self.assertFalse(response.json['sent_to_hardware'])
        self.assertEqual(self.writes,[])
        self.assertTrue(self.client.get('/vehicle-lab/status').json['actuator_ready'])
        self.manual['center_us']=1500
        self.assertFalse(self.client.get('/vehicle-lab/status').json['actuator_ready'])

    def test_vision_rejects_invalid_times_and_covariances_and_download_requires_complete_trace(self):
        data={'time_s':10.,'sequence':1,'lateral_error_m':0.,'heading_error_rad':0.,'covariance':[.001,0,0,.001],'confidence':.9}
        self.assertEqual(self.client.post('/vehicle-lab/vision',json=data).status_code,409)
        self.lab.active=self.lab.owned=True
        packet=self.lab.timer.probe(10.)
        self.lab.timer.reply(int(packet[1:]),1000,10.004)
        self.now[0]=10.004
        self.assertEqual(self.client.post('/vehicle-lab/vision',json=data).status_code,200)
        data['time_s']=9.
        self.assertEqual(self.client.post('/vehicle-lab/vision',json=data).status_code,409)
        self.assertEqual(self.client.get('/vehicle-lab/trace').status_code,409)
        self.assertEqual(self.client.get('/vehicle-lab').status_code,200)


if __name__=='__main__':unittest.main()
