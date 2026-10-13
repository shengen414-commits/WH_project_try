import math
import unittest
from unittest.mock import patch
from PyScripts.imu_visualization import ImuVisualization


def frame(ms, a=(0, 0, 1), w=(0, 0, 0), z=None):
    return '[IMU],' + ','.join(map(str, (ms, *a, *w, 25, w[2] if z is None else z, 'NO')))


class ImuTests(unittest.TestCase):
    def setUp(self):
        self.imu = ImuVisualization()

    def test_gravity_initializes_tilt_and_yaw_integrates_device_time(self):
        r = math.radians(30)
        self.imu.feed(frame(0, a=(0, math.sin(r), math.cos(r))), 10)
        self.assertAlmostEqual(self.imu.status()['angles'][0], 30)
        self.imu.reset()
        self.imu.feed(frame(1000), 10)
        for i in range(1, 51):
            # Host jitter must not alter integration of a 90 degree/s turn.
            self.imu.feed(frame(1000+i*20, w=(0,0,90)), 10+i*.018)
        self.assertAlmostEqual(self.imu.status()['angles'][2], 90, delta=.03)

    def test_gravity_correction_converges_without_flipping_sign(self):
        self.imu.feed(frame(0), 10)
        r = math.radians(30)
        for i in range(1, 201):
            self.imu.feed(frame(i*20, a=(0,math.sin(r),math.cos(r))), 10+i*.02)
        self.assertAlmostEqual(self.imu.status()['angles'][0], 30, delta=.1)

    def test_corrected_z_is_used_and_duplicates_ignored(self):
        self.imu.feed(frame(0), 10)
        for i in range(1, 51):
            self.imu.feed(frame(i*20, w=(0,0,2), z=0), 10+i*.02)
        self.assertAlmostEqual(self.imu.status()['angles'][2], 0)
        count = self.imu.status()['count']
        self.imu.feed(frame(1000, w=(0,0,90)), 11)
        self.assertEqual(self.imu.status()['count'], count)

    def test_gap_restart_invalid_and_staleness_do_not_fake_rotation(self):
        self.imu.feed(frame(1000), 10)
        self.imu.feed(frame(1100, w=(0,0,90)), 10.1)
        self.imu.feed(frame(3000, w=(0,0,90)), 12)
        self.assertAlmostEqual(self.imu.status()['angles'][2], 0)
        self.imu.feed(frame(10), 12.02)  # Device restart.
        self.assertAlmostEqual(self.imu.status()['angles'][2], 0)
        with patch('PyScripts.imu_visualization.time.monotonic', return_value=12.6):
            self.assertFalse(self.imu.status()['fresh'])
            self.assertFalse(self.imu.zero())
        for text in ('[IMU],bad', frame(30,a=(float('nan'),0,1)), frame(30,w=(0,0,999))):
            self.imu.feed(text, 12.04)
            self.assertFalse(self.imu.status()['fresh'])

    def test_zero_is_display_reference_and_normal_stream_invalidates(self):
        self.imu.feed(frame(0), 10)
        self.imu.feed(frame(100,w=(0,0,90)), 10.1)
        with patch('PyScripts.imu_visualization.time.monotonic', return_value=10.1):
            self.assertTrue(self.imu.zero())
            self.assertAlmostEqual(self.imu.status()['angles'][2], 0)
            self.assertTrue(self.imu.status()['relative'])
        self.imu.feed(frame(200,w=(0,0,90)),10.2)
        self.assertAlmostEqual(self.imu.status()['angles'][2], 9, delta=.03)
        self.imu.feed('[MPU] ERROR=sample_read_failed',10.3)
        self.assertFalse(self.imu.status()['fresh'])
        self.imu.feed(frame(300),10.4)
        self.imu.feed('[ENC],1,400,0',10.5)
        self.assertFalse(self.imu.status()['fresh'])

    def test_device_uint32_wrap_is_small_delta(self):
        self.imu.feed(frame(0xfffffff0),10)
        self.imu.feed(frame(4,w=(0,0,90)),10.02)
        self.assertAlmostEqual(self.imu.status()['angles'][2],1.8,delta=.001)
