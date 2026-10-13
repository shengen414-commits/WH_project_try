"""Exercise exclusive-open policy using mocks, never open a physical UART."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from PyScripts.serial_connection import open_esp32_serial


class SerialConnectionTests(unittest.TestCase):
    def setUp(self):
        self.connection = Mock()
        self.connection.fileno.return_value = 7
        self.serial = SimpleNamespace(Serial=Mock(return_value=self.connection))
        self.ioctl = Mock()
        self.modules = {'serial': self.serial, 'fcntl': SimpleNamespace(ioctl=self.ioctl),
                        'termios': SimpleNamespace(TIOCEXCL=0x540c)}

    def test_linux_advisory_and_kernel_exclusive_before_using_port(self):
        with patch.dict('sys.modules',self.modules), patch('os.name','posix'), patch('sys.platform','linux'):
            self.assertIs(open_esp32_serial('/dev/ttyUSB0'),self.connection)
        self.serial.Serial.assert_called_once_with('/dev/ttyUSB0',115200,timeout=.01,write_timeout=.05,exclusive=True)
        self.ioctl.assert_called_once_with(7,0x540c)
        self.connection.close.assert_not_called()

    def test_failed_kernel_lock_closes_port_without_unprotected_fallback(self):
        self.ioctl.side_effect = OSError('exclusive access failed')
        with patch.dict('sys.modules',self.modules), patch('os.name','posix'), patch('sys.platform','linux'):
            with self.assertRaisesRegex(OSError,'exclusive access failed'):
                open_esp32_serial('/dev/ttyUSB0')
        self.connection.close.assert_called_once()
        self.assertEqual(self.serial.Serial.call_count,1)

    def test_existing_owner_is_reported_without_retry(self):
        self.serial.Serial.side_effect = OSError('port already exclusively locked')
        with patch.dict('sys.modules',self.modules), patch('os.name','posix'), patch('sys.platform','linux'):
            with self.assertRaisesRegex(OSError,'already exclusively locked'):
                open_esp32_serial('/dev/ttyUSB0')
        self.ioctl.assert_not_called()

    def test_windows_does_not_import_posix_modules_or_pass_posix_option(self):
        with patch.dict('sys.modules',self.modules), patch('os.name','nt'), patch('sys.platform','win32'):
            self.assertIs(open_esp32_serial('COM12'),self.connection)
        self.serial.Serial.assert_called_once_with('COM12',115200,timeout=.01,write_timeout=.05)
        self.ioctl.assert_not_called()
