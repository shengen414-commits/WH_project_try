import math
import sys
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from line_debug_plot import FrameTimeline, collect, decode_frame, line_center


class LinePlotTests(unittest.TestCase):
    def test_center_and_polarity(self):
        self.assertEqual(line_center(0b00011000), (0.0, "valid"))
        self.assertEqual(line_center(1), (-3.5, "valid"))
        self.assertEqual(line_center(128), (3.5, "valid"))
        self.assertEqual(line_center(1, reverse=True), (3.5, "valid"))

    def test_undefined_line_is_not_a_fake_center(self):
        for mask, status in ((0, "lost"), (255, "all_black"), (129, "multiple")):
            center, actual = line_center(mask)
            self.assertTrue(math.isnan(center))
            self.assertEqual(actual, status)

    def test_decode_and_validation(self):
        self.assertEqual(decode_frame("[LINE],1,123,231,24\r\n"), (1, 123, 231, 24))
        for text in ("[ENC],1,2,3", "[LINE],1,2,256,0", "[LINE],-1,2,0,0",
                     "[LINE],1,2,3", "[LINE],x,2,3,4"):
            self.assertIsNone(decode_frame(text))

    def test_device_rollover_duplicates_and_loss(self):
        timeline = FrameTimeline()
        self.assertEqual(timeline.add((0xFFFFFFFF, 0xFFFFFFFA, 231, 24))["elapsed_s"], 0)
        sample = timeline.add((1, 14, 231, 24))
        self.assertEqual(sample["elapsed_s"], 0.02)
        self.assertEqual(sample["missing_before"], 1)
        self.assertIsNone(timeline.add((1, 14, 231, 24)))
        with self.assertRaises(RuntimeError):
            timeline.add((0, 0, 231, 24))

    def test_capture_fragmented_uart_and_return_to_debug(self):
        class Port:
            in_waiting = 64
            def __init__(self):
                self.commands = []
                self.chunks = [b"[LINE],0,0,", b"231,24\n[LINE],1,10,254,1\n"]
            def write(self, data):
                self.commands.append(data)
            def flush(self):
                pass
            def reset_input_buffer(self):
                pass
            def read(self, size):
                return self.chunks.pop(0)

        port = Port()
        with patch("line_debug_plot.time.sleep"), patch(
                "line_debug_plot.time.monotonic", side_effect=[0, 0.01, 0.02, 0.03, 0.11]):
            rows = collect(port, 0.1)
        self.assertEqual(port.commands, [b"L\n", b"G\n", b"L\n"])
        self.assertEqual([row["center"] for row in rows], [0, -3.5])
        self.assertEqual(rows[1]["elapsed_s"], 0.01)


if __name__ == "__main__":
    unittest.main()
