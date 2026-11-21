"""Linux UART owner. The dashboard exchanges bounded messages over local sockets."""
from pathlib import Path
import select
import socket
import subprocess
import sys
import threading

try:
    from .serial_connection import open_esp32_serial
except ImportError:
    from serial_connection import open_esp32_serial


class SerialWorker:
    def __init__(self, port):
        self.port = port
        self.lock = threading.Lock()
        self.pending = bytearray()
        self.closed = False
        self.commands, child_commands = socket.socketpair(type=socket.SOCK_SEQPACKET)
        self.telemetry, child_telemetry = socket.socketpair()
        self.commands.settimeout(3)
        self.telemetry.settimeout(.01)
        try:
            with child_commands, child_telemetry:
                descriptors = (child_commands.fileno(),child_telemetry.fileno())
                self.process = subprocess.Popen(
                    [sys.executable, str(Path(__file__).resolve()), port, *map(str,descriptors)],
                    pass_fds=descriptors)
            self._expect_ok()
            self.commands.settimeout(.1)
        except (OSError, ValueError):
            self.close()
            raise

    @property
    def is_open(self):
        return not self.closed and self.process.poll() is None

    @property
    def in_waiting(self):
        if not self.pending and select.select([self.telemetry],[],[],0)[0]:
            self._receive()
        return len(self.pending)

    def _receive(self):
        try:
            chunk = self.telemetry.recv(4096)
        except socket.timeout:
            return
        if not chunk:
            raise OSError('ESP32 串口进程已断开')
        self.pending.extend(chunk)

    def read(self, size):
        if not self.pending:
            self._receive()
        data = bytes(self.pending[:size])
        del self.pending[:size]
        return data

    def _expect_ok(self):
        reply = self.commands.recv(4096)
        if reply != b'ok':
            raise OSError(reply.decode('utf-8',errors='replace') or 'ESP32 串口进程未响应')

    def _command(self, payload):
        with self.lock:
            try:
                self.commands.sendall(payload)
                self._expect_ok()
            except OSError:
                # Without a matching reply the state of this write is uncertain.
                # Close the transport so an old reply cannot acknowledge the next command.
                self.close()
                raise

    def write(self, payload):
        if not 0 < len(payload) <= 4096:
            raise ValueError('串口消息长度无效')
        self._command(b'w'+payload)
        return len(payload)

    def reset_input_buffer(self):
        self._command(b'r')
        self.pending.clear()
        while select.select([self.telemetry],[],[],0)[0]:
            if not self.telemetry.recv(4096):
                raise OSError('ESP32 串口进程已断开')

    def reset_output_buffer(self):
        self._command(b'o')

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.commands.close()
        self.telemetry.close()
        process = getattr(self,'process',None)
        if process is not None:
            try:
                process.wait(timeout=.15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def run(port, commands_fd, telemetry_fd):
    with socket.socket(fileno=commands_fd) as commands, socket.socket(fileno=telemetry_fd) as telemetry:
        # Bound the backlog between the UART worker and dashboard. If the
        # consumer stalls, end this session instead of replaying stale bytes.
        telemetry.setsockopt(socket.SOL_SOCKET,socket.SO_SNDBUF,8192)
        telemetry.setblocking(False)
        try:
            with open_esp32_serial(port) as uart:
                commands.sendall(b'ok')
                while True:
                    ready = select.select([commands,uart],[],[])[0]
                    if commands in ready:
                        packet = commands.recv(4097)
                        if not packet:
                            return
                        if packet == b'r':
                            uart.reset_input_buffer()
                            ready = []
                        elif packet == b'o':
                            uart.reset_output_buffer()
                        elif packet[:1] != b'w' or uart.write(packet[1:]) != len(packet)-1:
                            raise OSError('ESP32 指令未完整发送')
                        commands.sendall(b'ok')
                    if uart in ready:
                        data = uart.read(min(max(uart.in_waiting,1),4096))
                        if data:
                            telemetry.sendall(data)
        except OSError as exc:
            try:
                commands.sendall(str(exc).encode('utf-8',errors='replace')[:2048] or b'UART worker error')
            except OSError:
                pass


if __name__ == '__main__':
    run(sys.argv[1],int(sys.argv[2]),int(sys.argv[3]))
