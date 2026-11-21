import os
import select
import sys
import time
import unittest

from PyScripts.serial_worker import SerialWorker


@unittest.skipUnless(sys.platform.startswith('linux'), 'dashboard serial process uses Linux local sockets')
class SerialWorkerTests(unittest.TestCase):
    def test_worker_owns_pseudoterminal_and_moves_data_both_ways(self):
        import pty
        import serial

        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        port = os.ttyname(slave)
        worker = SerialWorker(port)
        self.addCleanup(worker.close)
        worker.reset_input_buffer()
        worker.reset_output_buffer()
        with self.assertRaises(serial.SerialException):
            serial.Serial(port, 115200)
        self.assertEqual(worker.write(b'T1500\n'), 6)
        self.assertTrue(select.select([master], [], [], 1)[0])
        self.assertEqual(os.read(master, 100), b'T1500\n')
        frame = b'[ENC],1,10,3\n'
        os.write(master, frame)
        received = bytearray()
        deadline = time.monotonic() + 1
        while len(received) < len(frame) and time.monotonic() < deadline:
            received.extend(worker.read(len(frame)-len(received)))
        self.assertEqual(received, frame)
