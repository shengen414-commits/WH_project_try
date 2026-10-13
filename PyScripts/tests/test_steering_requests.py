import unittest
from PyScripts.steering_requests import SteeringRequests


class SteeringRequestTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.requests = SteeringRequests(clock=lambda: self.now)

    def ack(self, request_id, action=1, ok=1, reason="armed", raw=24, mask=24):
        return f"[FOLLOW_ACK] id={request_id} action={action} ok={ok} mask={mask} raw={raw} lvl=1 reason={reason}"

    def test_only_matching_ack_confirms_request(self):
        request_id = self.requests.begin(True)
        self.assertFalse(self.requests.feed(self.ack(request_id - 1)))
        self.assertFalse(self.requests.feed(self.ack(request_id, action=0)))
        self.assertEqual(self.requests.snapshot()["status"], "pending")
        self.assertTrue(self.requests.feed(self.ack(request_id)))
        self.assertEqual(self.requests.snapshot()["status"], "confirmed")

    def test_rejection_includes_raw_polarity_diagnostics(self):
        request_id = self.requests.begin(True)
        self.requests.feed(self.ack(request_id, ok=0, reason="line_lost", raw=0, mask=0))
        state = self.requests.snapshot()
        self.assertEqual(state["status"], "rejected")
        self.assertIn("未检测到黑胶带", state["message"])
        self.assertIn("raw=00000000", state["message"])
        self.assertIn("HIGH", state["message"])

    def test_timeout_and_late_actual_confirmation(self):
        request_id = self.requests.begin(True)
        self.now = 3.1
        self.assertEqual(self.requests.snapshot()["status"], "timeout")
        self.assertTrue(self.requests.feed(self.ack(request_id)))
        self.assertEqual(self.requests.snapshot()["status"], "confirmed")

    def test_stop_supersedes_pending_start(self):
        start = self.requests.begin(True)
        stop = self.requests.begin(False)
        self.assertFalse(self.requests.feed(self.ack(start)))
        self.assertTrue(self.requests.feed(self.ack(stop, action=0, reason="disabled")))
        self.assertIn("已关闭并归中", self.requests.snapshot()["message"])

    def test_corrupt_or_legacy_reply_cannot_guess_success(self):
        request_id = self.requests.begin(True)
        for line in ("[FOLLOW] mode=LINE", self.ack(0), self.ack(request_id, ok=2),
                     self.ack(request_id, raw=256), self.ack(request_id).replace("lvl=1", "lvl=x")):
            self.assertFalse(self.requests.feed(line))
        self.assertEqual(self.requests.snapshot()["status"], "pending")

    def test_write_error_stays_an_error(self):
        request_id = self.requests.begin(True)
        self.requests.fail(request_id, "USB disconnected")
        self.assertFalse(self.requests.feed(self.ack(request_id)))
        self.assertEqual(self.requests.snapshot()["status"], "error")


if __name__ == "__main__":
    unittest.main()
