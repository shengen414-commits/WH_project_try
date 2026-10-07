import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class FirmwareTransportTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('g++'), 'host g++ is optional; ESP32 build uses PlatformIO')
    def test_actual_firmware_parser_with_fake_uart(self):
        root = Path(__file__).resolve().parents[2]
        stubs = Path(__file__).resolve().parent / 'firmware_stubs'
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / 'transport_test.exe'
            subprocess.run(['g++', '-std=c++11', '-Wall', '-Wextra', '-Werror',
                            '-I', str(stubs), '-I', str(root / 'PIO_RotationmitCamera/include'),
                            str(root / 'PIO_RotationmitCamera/src/serial_transport.cpp'),
                            str(stubs / 'test_transport.cpp'), '-o', str(executable)], check=True, capture_output=True)
            subprocess.run([str(executable)], check=True, timeout=5)


if __name__ == '__main__':
    unittest.main()
