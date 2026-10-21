import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from mps4264_app.controller import MPS4264Controller, MPSControllerError
from mps4264_app.recording import PressureWriter
from mps4264_app.protocol import decode_frame
from mps4264_app.tests.test_mps4264 import make_frame


class PressureStorageIsolationTests(unittest.TestCase):
    def test_slow_raw_write_does_not_stop_udp_reception_status_or_stop_command(self):
        with tempfile.TemporaryDirectory() as directory:
            reserve = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            reserve.bind(('127.0.0.1', 0)); port = reserve.getsockname()[1]; reserve.close()
            service = MPS4264Controller(directory)
            service._client = Mock()
            service._device_ip = '127.0.0.1'
            service.new_file('stall', port)
            entered, release = threading.Event(), threading.Event()
            original = service._storage.raw
            proxy = Mock(wraps=original)
            def write(data):
                entered.set(); release.wait(5)
                return original.write(data)
            proxy.write.side_effect = write
            service._storage.raw = proxy
            try:
                service.scan()
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                    sender.sendto(make_frame(1), ('127.0.0.1', port))
                    self.assertTrue(entered.wait(1))
                    for number in range(2, 12):
                        sender.sendto(make_frame(number), ('127.0.0.1', port))
                    deadline = time.monotonic()+1
                    while service.status()['frames_received'] < 11 and time.monotonic() < deadline:
                        time.sleep(.002)
                state = service.try_status()
                self.assertFalse(state.get('busy', False))
                self.assertEqual(state['frames_received'], 11)
                self.assertEqual(state['bytes_saved'], 0)
                self.assertGreater(state['storage']['queue_depth'], 0)
                # STOP is a device command, independent of the blocked file.write.
                service.stop()
                service._client.stop_scan.assert_called_once()
                self.assertFalse(service.status()['scanning'])
                # A drain timeout preserves ownership and cannot report a completed file.
                with patch.object(service._storage.done, 'wait', return_value=False):
                    with self.assertRaisesRegex(MPSControllerError, '仍在保存'):
                        service.close_file()
                self.assertTrue(service.status()['file_open'])
                self.assertFalse(service.status()['file_closing'])
                with self.assertRaisesRegex(MPSControllerError, '收尾'):
                    service.scan()
            finally:
                release.set(); service.close_file()
            state = service.status()
            self.assertEqual(state['bytes_saved'], 11*348)
            self.assertTrue(state['storage']['finalized'])
            self.assertEqual((Path(directory)/'stall.dat').stat().st_size, 11*348)
            self.assertEqual(len((Path(directory)/'stall.index.csv').read_text().splitlines()), 12)

    def test_overflow_is_bounded_counted_and_writer_drains_on_finish(self):
        entered, release = threading.Event(), threading.Event()
        raw, index, rows, error = Mock(), Mock(), Mock(), Mock()
        def write(_):
            entered.set(); release.wait(3)
        raw.write.side_effect = write
        writer = PressureWriter(raw, index, rows, error, queue_size=1)
        data = make_frame(); decoded = [(data, decode_frame(data))]
        try:
            self.assertTrue(writer.submit(decoded, 1, 1))
            self.assertTrue(entered.wait(1))
            self.assertTrue(writer.submit(decoded, 2, 2))
            self.assertFalse(writer.submit(decoded, 3, 3))
            state = writer.status()
            self.assertEqual((state['queue_depth'], state['dropped_frames']), (1, 1))
            writer.finish()
            self.assertFalse(writer.done.is_set())
        finally:
            release.set(); self.assertTrue(writer.done.wait(2))
        self.assertEqual(writer.status()['frames_saved'], 2)
        error.assert_not_called()

    def test_write_error_is_reported_and_both_files_are_closed(self):
        raw, index, rows, error = Mock(), Mock(), Mock(), Mock()
        raw.write.side_effect = OSError('disk full')
        writer = PressureWriter(raw, index, rows, error)
        data = make_frame()
        writer.submit([(data, decode_frame(data))], 1, 1)
        self.assertTrue(writer.done.wait(1))
        self.assertIn('disk full', writer.status()['error'])
        self.assertEqual(writer.status()['bytes_saved'], 0)
        error.assert_called_once_with('disk full')
        raw.close.assert_called_once(); index.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
