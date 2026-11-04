import unittest
from PyScripts.manual_steering import ManualSteering


class ManualSteeringTests(unittest.TestCase):
    def setUp(self):
        self.now = [100.0]
        self.manual = ManualSteering(clock=lambda: self.now[0])

    def ack(self, request_id, target=1500, manual=1, ok=1, ready=1, center=1500, proto=4):
        return self.manual.feed(f'[SERVO_ACK] id={request_id} ok={ok} target={target} low={center-400} mid={center} high={center+400} manual={manual} ready={ready} proto={proto} reason={"ok" if ok else "pwm_failed"}')

    def test_commands_wait_for_matching_firmware_ack(self):
        request_id, packet = self.manual.begin('start')
        self.assertEqual(packet, f'V\nS0000,{request_id}\n'.encode())
        self.assertTrue(self.manual.blocking())
        self.assertFalse(self.manual.snapshot()['active'])
        self.manual.normal_frame()  # A queued ENC sample cannot confirm V.
        self.assertEqual(self.manual.snapshot()['request']['status'], 'pending')
        self.assertFalse(self.ack(request_id + 1))
        self.assertTrue(self.ack(request_id))
        self.assertTrue(self.manual.snapshot()['fresh'])
        move, packet = self.manual.begin('set', 1505)
        self.assertEqual(packet, f'S1505,{move}\n'.encode())
        self.assertFalse(self.ack(move, target=1510))
        self.assertTrue(self.ack(move, target=1505))
        self.assertEqual(self.manual.snapshot()['target_us'], 1505)
        with self.assertRaises(ValueError):
            self.manual.begin('set', 2000)
        stop, packet = self.manual.begin('stop')
        self.assertEqual(packet, f'C\nR\nS0001,{stop}\n'.encode())
        self.assertFalse(self.ack(move))  # Old move cannot undo a later exit.
        self.assertTrue(self.ack(stop, manual=0))
        self.assertFalse(self.manual.blocking())

    def test_timeout_and_disconnect_keep_control_locked_until_exit(self):
        request_id, _ = self.manual.begin('start')
        self.now[0] += 3.1
        self.assertEqual(self.manual.snapshot()['request']['status'], 'timeout')
        self.assertTrue(self.manual.blocking())
        self.manual.invalidate('串口已断开')
        self.assertEqual(self.manual.snapshot()['request']['message'], '串口已断开')
        stop, _ = self.manual.begin('stop')
        self.manual.normal_frame()
        self.assertFalse(self.manual.blocking())
        self.assertEqual(self.manual.snapshot()['request']['status'], 'confirmed')

    def test_fixed_center_and_old_protocol_cannot_adjust(self):
        rid, _ = self.manual.begin('start')
        self.ack(rid, proto=3)
        for action, pulse in [('calibrate', None), ('set', 1505), ('center', None)]:
            with self.assertRaises(ValueError):
                self.manual.begin(action, pulse)
        self.assertFalse(self.manual.snapshot()['range_supported'])
        self.manual.begin('stop')
        self.manual.normal_frame()
        self.assertFalse(self.manual.blocking())

    def test_old_firmware_can_be_detected_and_exited_but_not_fine_adjusted(self):
        self.manual.feed('[SERVO] GPIO12 target=1500 us PWM=ready sweep=idle')
        self.assertTrue(self.manual.snapshot()['active'])
        self.assertFalse(self.manual.snapshot()['precise'])
        with self.assertRaises(ValueError):
            self.manual.begin('set', 1505)
        self.manual.begin('stop')
        self.manual.normal_frame()
        self.assertFalse(self.manual.blocking())

    def test_stale_invalid_and_failed_pwm_samples_cannot_enable_adjustment(self):
        request_id, _ = self.manual.begin('start')
        self.assertTrue(self.ack(request_id, ok=0, ready=0))
        self.assertEqual(self.manual.snapshot()['request']['status'], 'rejected')
        with self.assertRaises(ValueError):
            self.manual.begin('set', 1505)
        self.assertFalse(self.manual.feed('[SERVO] GPIO12 target=900 us PWM=ready'))
        self.assertFalse(self.manual.feed('[SERVO] GPIO12 target=1500 us PWM=ready low=1500 mid=1500 high=1550 proto=1'))
        self.manual.feed('[SERVO] GPIO12 target=1500 us PWM=ready low=1450 mid=1500 high=1550 proto=1')
        self.now[0] += 1.1
        with self.assertRaises(ValueError):
            self.manual.begin('set', 1505)

    def test_fixed_range_exact_ack_edges_and_no_expansion(self):
        rid,_=self.manual.begin('start');self.ack(rid)
        self.assertEqual((self.manual.low,self.manual.center,self.manual.high),(1100,1500,1900))
        for invalid in [(500,2500),(1200,1800),(1000,1900),(1100,2000),(1100.0,1900),(True,1900)]:
            with self.assertRaises(ValueError):self.manual.begin('range',limits=invalid)
        rid,packet=self.manual.begin('range',limits=(1100,1900))
        self.assertEqual(packet,f'S1100:1900,{rid}\n'.encode())
        self.assertFalse(self.ack(rid,target=1600))
        self.assertTrue(self.ack(rid))
        for pulse in (1100,1900):
            rid,_=self.manual.begin('set',pulse)
            self.assertTrue(self.ack(rid,target=pulse))
        for pulse in (1099,1901,500,2500):
            with self.assertRaises(ValueError):self.manual.begin('set',pulse)
        self.assertTrue(self.manual.feed('[SERVO] GPIO12 target=1500 us PWM=ready low=1200 mid=1500 high=1800 proto=4'))
        self.assertFalse(self.manual.snapshot()['range_supported'])
        with self.assertRaises(ValueError):self.manual.begin('set',1505)


if __name__ == '__main__':
    unittest.main()
