import math
import unittest
try:
    import cv2
    import numpy as np
    AVAILABLE = isinstance(cv2.__version__, str) and isinstance(np.__version__, str)
except ImportError:
    AVAILABLE = False
from PyScripts.calibrate_ground_camera import calibrate
from PyScripts.vehicle_state.vision import ground_observation


@unittest.skipUnless(AVAILABLE, 'OpenCV/numpy required for camera calibration')
class GroundCameraTests(unittest.TestCase):
    def pairs(self):
        def ground(u,v):return [(480-v)/300,(320-u)/400]
        train=[(80,120),(560,120),(80,420),(560,420),(160,200),(480,350)]
        check=[(200,300),(430,280)]
        return {'image_size':[640,480], 'camera_matrix':[[500,0,320],[0,500,240],[0,0,1]],
                'dist_coeffs':[0,0,0,0,0], 'latency_ms':40,
                'pairs':[{'image':[u,v],'ground':ground(u,v),'validation':False} for u,v in train]+
                        [{'image':[u,v],'ground':ground(u,v),'validation':True} for u,v in check]}

    def detection(self):
        xs=np.linspace(.3,1.1,12)
        points=[[(320-400*(.02+.03*x))/2,(480-300*x)/2] for x in xs]
        return {'valid':True,'confidence':.9,'points':points,'width':320}

    def test_ground_units_sign_scaling_and_exposure_delay(self):
        c=calibrate(self.pairs())
        result=ground_observation(self.detection(),[640,480],c,10,1)
        self.assertAlmostEqual(result.lateral_error_m,-.02,places=6)
        self.assertAlmostEqual(result.heading_error_rad,-math.atan(.03),places=6)
        self.assertAlmostEqual(result.time_s,9.96)
        self.assertLess(result.covariance[0]*result.covariance[3]-result.covariance[1]**2,1)
        self.assertGreater(result.covariance[0]*result.covariance[3]-result.covariance[1]**2,0)

    def test_validation_rejects_incorrect_measured_ground_points(self):
        data=self.pairs();data['pairs'][-1]['ground'][1]+=.1
        with self.assertRaises(ValueError):calibrate(data)

    def test_no_guess_when_latency_resolution_or_calibration_region_unknown(self):
        c=calibrate(self.pairs());c['latency_ms']=None
        with self.assertRaises(ValueError):ground_observation(self.detection(),[640,480],c,10,1)
        c['latency_ms']=40
        with self.assertRaises(ValueError):ground_observation(self.detection(),[320,240],c,10,1)
        detection=self.detection();detection['points']=[[0,p[1]]for p in detection['points']]
        with self.assertRaises(ValueError):ground_observation(detection,[640,480],c,10,1)


if __name__=='__main__':unittest.main()
