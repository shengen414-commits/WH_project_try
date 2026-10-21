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

    @unittest.skipUnless(shutil.which('g++'), 'host g++ is optional; ESP32 build uses PlatformIO')
    def test_automatic_steering_and_pid_against_real_firmware_modules(self):
        root = Path(__file__).resolve().parents[2]
        stubs = Path(__file__).resolve().parent / 'firmware_stubs'
        sources = ['esc_control.cpp', 'steering_control.cpp', 'automatic_steering.cpp',
                   'line_position.cpp', 'line_controller.cpp', 'serial_transport.cpp']
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / 'automatic_test.exe'
            result = subprocess.run(['g++', '-std=c++11', '-Wall', '-Wextra', '-Werror',
                                     '-I', str(stubs), '-I', str(root / 'PIO_RotationmitCamera/include'),
                                     *(str(root / 'PIO_RotationmitCamera/src' / name) for name in sources),
                                     str(stubs / 'test_automatic_steering.cpp'), '-o', str(executable)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            subprocess.run([str(executable)], check=True, timeout=5)

    @unittest.skipUnless(shutil.which('g++'), 'host g++ is optional; ESP32 build uses PlatformIO')
    def test_actual_steering_and_esc_debug_interlock(self):
        root = Path(__file__).resolve().parents[2]
        stubs = Path(__file__).resolve().parent / 'firmware_stubs'
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / 'controls_test.exe'
            subprocess.run(['g++', '-std=c++11', '-Wall', '-Wextra', '-Werror',
                            '-I', str(stubs), '-I', str(root / 'PIO_RotationmitCamera/include'),
                            str(root / 'PIO_RotationmitCamera/src/esc_control.cpp'),
                            str(root / 'PIO_RotationmitCamera/src/steering_control.cpp'),
                            str(stubs / 'test_controls.cpp'), '-o', str(executable)], check=True, capture_output=True)
            subprocess.run([str(executable)], check=True, timeout=5)

    @unittest.skipUnless(shutil.which('g++'), 'host g++ is optional; ESP32 build uses PlatformIO')
    def test_mpu6050_driver_and_serial_debug_against_fake_i2c(self):
        root = Path(__file__).resolve().parents[2]
        stubs = Path(__file__).resolve().parent / 'firmware_stubs'
        sources = ['esc_control.cpp', 'steering_control.cpp', 'automatic_steering.cpp',
                   'line_position.cpp', 'line_controller.cpp', 'serial_transport.cpp', 'mpu6050_debug.cpp', 'imu_sensor.cpp']
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / 'mpu_test.exe'
            result = subprocess.run(['g++', '-std=c++11', '-Wall', '-Wextra', '-Werror',
                                     '-I', str(stubs), '-I', str(root / 'PIO_RotationmitCamera/include'),
                                     *(str(root / 'PIO_RotationmitCamera/src' / name) for name in sources),
                                     str(stubs / 'test_mpu6050.cpp'), '-o', str(executable)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            subprocess.run([str(executable)], check=True, timeout=5)

    @unittest.skipUnless(shutil.which('g++'), 'host g++ is optional; ESP32 build uses PlatformIO')
    def test_line_position_and_replaceable_controllers(self):
        root = Path(__file__).resolve().parents[2]
        stubs = Path(__file__).resolve().parent / 'firmware_stubs'
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / 'line_controller_test.exe'
            result = subprocess.run(['g++', '-std=c++11', '-Wall', '-Wextra', '-Werror',
                                     '-I', str(stubs), '-I', str(root / 'PIO_RotationmitCamera/include'),
                                     str(root / 'PIO_RotationmitCamera/src/line_position.cpp'),
                                     str(root / 'PIO_RotationmitCamera/src/line_controller.cpp'),
                                     str(root / 'PIO_RotationmitCamera/src/line_sensor.cpp'),
                                     str(stubs / 'test_line_controller.cpp'), '-o', str(executable)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            subprocess.run([str(executable)], check=True, timeout=5)

    @unittest.skipUnless(shutil.which('g++'), 'host g++ is optional; ESP32 build uses PlatformIO')
    def test_servo_calibration_persistence_limits_and_no_jump(self):
        root = Path(__file__).resolve().parents[2]
        stubs = Path(__file__).resolve().parent / 'firmware_stubs'
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / 'calibration_test.exe'
            subprocess.run(['g++', '-std=c++11', '-Wall', '-Wextra', '-Werror',
                            '-I', str(stubs), '-I', str(root / 'PIO_RotationmitCamera/include'),
                            str(root / 'PIO_RotationmitCamera/src/steering_control.cpp'),
                            str(stubs / 'test_steering_calibration.cpp'), '-o', str(executable)], check=True, capture_output=True)
            for args in ([], ['saved'], ['invalid']):
                subprocess.run([str(executable), *args], check=True, timeout=5)


if __name__ == '__main__':
    unittest.main()
