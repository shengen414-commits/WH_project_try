"""Open the car UART once, protecting Linux acquisition from other readers."""
import os
import sys


def open_esp32_serial(port):
    import serial
    options = dict(timeout=0.01, write_timeout=0.05)
    if os.name == 'posix':
        # pySerial's advisory lock prevents another cooperating owner.
        options['exclusive'] = True
    connection = serial.Serial(port, 115200, **options)
    try:
        if sys.platform.startswith('linux'):
            import fcntl
            import termios
            # TIOCEXCL also rejects later non-privileged opens by monitors that
            # do not request pySerial's advisory lock. Existing owners are not
            # evicted; stop them before starting the dashboard.
            fcntl.ioctl(connection.fileno(), termios.TIOCEXCL)
        return connection
    except Exception:
        connection.close()
        raise
