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

    def test_bad_frame_reports_exact_reason_and_keeps_reference_on_recovery(self):
        self.imu.feed(frame(100),10)
        self.imu.feed(frame(120,w=(0,0,90)),10.02)
        with patch('PyScripts.imu_visualization.time.monotonic',return_value=10.02):
            self.assertTrue(self.imu.zero())
        raw = '[IMU],140,0,0,1,0,0,90,25,90'
        self.imu.feed(raw,10.04)
        diagnostics = self.imu.status()['diagnostics']
        self.assertEqual(diagnostics['invalid_frames'],1)
        self.assertIn('字段数量',diagnostics['last_invalid']['reason'])
        self.assertEqual(diagnostics['last_invalid']['raw'],raw)
        self.assertTrue(self.imu.status()['relative'])
        # Module acceleration during the next valid frame must not force a new
        # gravity initialization; keep pose and skip rotation over the bad gap.
        self.imu.feed(frame(160,a=(1.5,0,1),w=(0,0,90)),10.06)
        with patch('PyScripts.imu_visualization.time.monotonic',return_value=10.06):
            state = self.imu.status()
            self.assertTrue(state['fresh'])
            self.assertTrue(state['relative'])
            self.assertAlmostEqual(state['angles'][2],0)
        self.assertEqual(state['diagnostics']['consecutive_invalid'],0)
        self.assertEqual(state['diagnostics']['last_invalid']['raw'],raw)
        self.imu.feed(frame(180,w=(0,0,90)),10.08)
        self.assertAlmostEqual(self.imu.status()['angles'][2],1.8,delta=.001)

    def test_malformed_categories_and_whitespace(self):
        cases = [(frame(10).replace(',NO',',BROKEN'),'标定状态'),
                 (frame(10).replace(',10,',',oops,',1),'时间戳不是整数'),
                 (frame(0x100000000),'时间戳越界'),
                 (frame(10,a=(float('inf'),0,1)),'ax 为非有限'),
                 (frame(10,a=(3,0,1)),'ax=3 g'),
                 (frame(10,w=(0,502,0)),'gy=502'),
                 (frame(10,z=511),'校正 gz=511'),
                 (frame(10).replace(',0,',',0�,',1),'ax 不是数字')]
        for raw, expected in cases:
            with self.subTest(expected=expected):
                self.imu.feed(raw,10)
                self.assertIn(expected,self.imu.status()['diagnostics']['last_invalid']['reason'])
        self.imu.feed(frame(20).replace(',',', '),10.02)
        with patch('PyScripts.imu_visualization.time.monotonic',return_value=10.02):
            self.assertTrue(self.imu.status()['fresh'])
            self.assertEqual(self.imu.status()['diagnostics']['invalid_frames'],len(cases))

    def test_full_scale_firmware_values_are_legal_not_bad_frames(self):
        # Exercise UART rounding at the signed raw-register extremes for the
        # configured ±2g / ±500deg/s scaling (including -32768).
        for raw in (-32768,-32767,-1,0,1,32766,32767):
            text = '[IMU],4294967295,{0:.4f},{0:.4f},{0:.4f},{1:.3f},{1:.3f},{1:.3f},{2:.2f},{1:.3f},NO'.format(
                raw/16384,raw/65.5,raw/340+36.53)
            ms, values, calibration = self.imu._decode(text)
            self.assertEqual(ms,0xffffffff)
            self.assertEqual(calibration,'NO')

    def test_many_bad_frames_stale_and_real_mode_error_still_invalidate(self):
        self.imu.feed(frame(100),10)
        for i in range(10):
            self.imu.feed('[IMU],broken',10.02+i*.02)
        with patch('PyScripts.imu_visualization.time.monotonic',return_value=10.3):
            self.assertFalse(self.imu.status()['fresh'])
            self.assertFalse(self.imu.zero())
            self.assertEqual(self.imu.status()['diagnostics']['consecutive_invalid'],10)
        self.imu.feed(frame(500,w=(0,0,90)),10.4)
        self.assertAlmostEqual(self.imu.status()['angles'][2],0)
        self.imu.feed('[MPU] ERROR=sample_read_failed',10.42)
        self.assertFalse(self.imu.status()['fresh'])
        self.imu.feed('[ENC],0,600,0',10.44)
        self.assertIn('离开',self.imu.status()['message'])
