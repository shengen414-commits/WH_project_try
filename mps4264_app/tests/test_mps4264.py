import csv
import json
import socket
import struct
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from mps4264_app import MPS4264Controller
from mps4264_app.controller import MPSControllerError
from mps4264_app.protocol import FAST_TYPE, NORMAL_TYPE, convert_binary_to_csv, decode_frame
from mps4264_app.web import create_app
from mps4264_app.network import NetworkInitializationError, ensure_ipv4_alias


def make_frame(number=1, packet_type=NORMAL_TYPE, units=23, endian=">", serial=249):
    raw = bytearray(348)
    struct.pack_into(endian + "4I", raw, 0, packet_type, 348, number, serial)
    struct.pack_into(endian + "f", raw, 16, 5.0)
    struct.pack_into(endian + "2I", raw, 20, 0, units)
    struct.pack_into(endian + "f", raw, 28, 6894.759766)
    values = [float(i) for i in range(1, 65)] if units != 27 else list(range(1, 65))
    struct.pack_into(endian + ("64f" if units != 27 else "64i"), raw, 76, *values)
    struct.pack_into(endian + "4I", raw, 332, 12, 500_000_000, 0, 0)
    return bytes(raw)


class FakeDevice:
    def __init__(self, udp_port, endian=">"):
        self.udp_port = udp_port
        self.endian = endian
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(2)
        self.listener.settimeout(0.2)
        self.port = self.listener.getsockname()[1]
        self.stop = threading.Event()
        self.commands = []
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while not self.stop.is_set():
            try:
                client, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with client:
                client.settimeout(0.2)
                client.sendall(b"Mock MPS4264\r\n>")
                buffer = b""
                while not self.stop.is_set():
                    try:
                        chunk = client.recv(4096)
                    except socket.timeout:
                        continue
                    except OSError:
                        break
                    if not chunk:
                        break
                    buffer += chunk
                    while b"\r" in buffer:
                        command, buffer = buffer.split(b"\r", 1)
                        decoded = command.decode("ascii", errors="replace")
                        self.commands.append(decoded)
                        if decoded == "SCAN":
                            threading.Thread(target=self._send_udp, daemon=True).start()
                        elif decoded == "LIST S":
                            client.sendall(b"SET RATE 5.0000\r\nSET FPS 3\r\nSET OPTIONS 0 0 16\r\n>")
                        elif decoded == "LIST UDP":
                            client.sendall(f"SET ENUDP 1\r\nSET IPUDP 127.0.0.1 {self.udp_port}\r\n>".encode())
                        else:
                            try:
                                client.sendall((decoded + "\r\n>").encode())
                            except OSError:
                                break

    def _send_udp(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            for number in (1, 2, 3):
                udp.sendto(make_frame(number, endian=self.endian), ("127.0.0.1", self.udp_port))
                time.sleep(0.02)

    def close(self):
        self.stop.set()
        self.listener.close()
        self.thread.join(timeout=2)


class ProtocolTests(unittest.TestCase):
    def test_actual_ver_210_little_endian_header_and_fields(self):
        raw = make_frame(number=1, serial=2, endian="<")
        self.assertEqual(raw[:28], bytes.fromhex(
            "0a0000005c01000001000000020000000000a0400000000017000000"))
        frame = decode_frame(raw)
        self.assertEqual((frame.packet_type, frame.frame_number, frame.serial_number),
                         (NORMAL_TYPE, 1, 2))
        self.assertEqual((frame.rate_hz, frame.units_index, frame.pressures[63]),
                         (5.0, 23, 64.0))
        self.assertEqual(frame.frame_time_sec, 12.5)
        self.assertEqual(decode_frame(make_frame(units=27, endian="<")).pressures[63], 64)

    def test_normal_and_raw_decoding(self):
        frame = decode_frame(make_frame())
        self.assertEqual(frame.frame_number, 1)
        self.assertEqual(frame.pressures[0], 1.0)
        self.assertEqual(frame.pressures[-1], 64.0)
        self.assertEqual(frame.frame_time_sec, 12.5)
        raw = decode_frame(make_frame(units=27))
        self.assertEqual(raw.units_index, 27)
        self.assertEqual(raw.pressures[63], 64)

    def test_fast_group_masks_other_channels(self):
        frame = decode_frame(make_frame(packet_type=FAST_TYPE))
        values = frame.display_pressures(1)
        self.assertEqual(values[0], 1.0)
        self.assertIsNone(values[1])
        self.assertEqual(values[63], 64.0)
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / "fast.dat", Path(directory) / "fast.csv"
            source.write_bytes(make_frame(packet_type=FAST_TYPE))
            with self.assertRaises(ValueError):
                convert_binary_to_csv(source, target)
            result = convert_binary_to_csv(source, target, fast_group=1)
            self.assertEqual(result["frames"], 1)
            with target.open(encoding="utf-8", newline="") as file:
                row = next(csv.DictReader(file))
            self.assertEqual(row["P01"], "1.0")
            self.assertEqual(row["P02"], "")


class ControllerTests(unittest.TestCase):
    def test_full_capture_and_importable_api(self):
        with tempfile.TemporaryDirectory() as directory:
            reserved = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            reserved.bind(("127.0.0.1", 0))
            udp_port = reserved.getsockname()[1]
            reserved.close()
            fake = FakeDevice(udp_port, endian="<")
            service = MPS4264Controller(directory)
            try:
                service.connect("127.0.0.1", fake.port)
                service.device_info()
                self.assertIn("STATUS", service.send_command("STATUS")["response"])
                service.set_parameters(5, 3, 0)
                service.new_file("trial", udp_port)
                service.scan()
                deadline = time.monotonic() + 3
                while service.status()["frames_received"] < 3 and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertEqual(service.status()["frames_received"], 3)
                self.assertEqual(service.status()["bytes_received"], 3 * 348)
                self.assertEqual(service.status()["bytes_saved"], 3 * 348)
                service.stop()
                service.close_file()
                result = service.convert_file("trial.dat")
                self.assertEqual(result["frames"], 3)
                self.assertEqual((Path(directory) / "trial.dat").stat().st_size, 3 * 348)
                self.assertTrue((Path(directory) / "trial.index.csv").is_file())
                self.assertTrue((Path(directory) / "trial.meta.json").is_file())
                self.assertIn("SCAN", fake.commands)
                self.assertIn("STOP", fake.commands)
                service.disconnect()
            finally:
                fake.close()

    def test_dashboard_without_device(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(MPS4264Controller(directory))
            client = app.test_client()
            self.assertEqual(client.get("/").status_code, 200)
            self.assertIn(b'191.30.90.102', client.get("/").data)
            self.assertIn(b'191.30.90.82', client.get("/").data)
            self.assertIn(b'id="udp-port" type="number" min="1" max="65535" value="50023"', client.get("/").data)
            self.assertEqual(client.get("/api/status").json["udp_port"], 50023)
            self.assertFalse(client.get("/api/status").json["connected"])
            self.assertEqual(client.post("/api/calz", json={}).status_code, 400)
            self.assertEqual(client.post("/api/command", json={"command": "STATUS"}).status_code, 409)

    def test_dashboard_full_workflow(self):
        with tempfile.TemporaryDirectory() as directory:
            reserved = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            reserved.bind(("127.0.0.1", 0))
            udp_port = reserved.getsockname()[1]
            reserved.close()
            fake = FakeDevice(udp_port)
            service = MPS4264Controller(directory)
            app = create_app(service)
            browser = app.test_client()
            try:
                self.assertEqual(browser.post("/api/connect", json={
                    "device_ip": "127.0.0.1", "control_port": fake.port}).status_code, 200)
                self.assertEqual(browser.post("/api/device-info", json={}).status_code, 200)
                command = browser.post("/api/command", json={"command": "LIST S"})
                self.assertEqual(command.status_code, 200)
                self.assertIn("SET FPS 3", command.json["response"])
                configured = browser.post("/api/configure-udp", json={
                    "host_ip": "127.0.0.1", "udp_port": udp_port})
                self.assertTrue(configured.json["reboot_required"])
                self.assertIn("SET FORMAT F B", fake.commands)
                self.assertEqual(browser.post("/api/connect", json={
                    "device_ip": "127.0.0.1", "control_port": fake.port,
                    "confirm_reboot": True}).status_code, 200)
                self.assertEqual(browser.post("/api/parameters", json={
                    "rate": 5, "fps": 3, "fast_group": 0,
                    "read_mode": 0, "subset_size": 16}).status_code, 200)
                self.assertEqual(browser.post("/api/new-file", json={
                    "name": "web_trial", "udp_port": udp_port}).status_code, 200)
                self.assertEqual(browser.post("/api/scan", json={}).status_code, 200)
                deadline = time.monotonic() + 3
                while service.status()["frames_received"] < 3 and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertEqual(service.status()["frames_received"], 3)
                self.assertEqual(browser.get("/api/status").json["bytes_received"], 1044)
                self.assertEqual(browser.post("/api/command", json={"command": "STATUS"}).status_code, 409)
                self.assertEqual(browser.post("/api/stop", json={}).status_code, 200)
                self.assertEqual(browser.post("/api/close-file", json={}).status_code, 200)
                self.assertEqual(browser.post("/api/convert", json={
                    "filename": "web_trial.dat"}).status_code, 202)
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    job = browser.get("/api/convert-status").json
                    if job["state"] != "running":
                        break
                    time.sleep(0.02)
                self.assertEqual(job["state"], "done")
                self.assertEqual(job["result"]["frames"], 3)
                self.assertEqual(browser.post("/api/disconnect", json={}).status_code, 200)
            finally:
                fake.close()


class LowPortTests(unittest.TestCase):
    def test_explicit_low_udp_port_permission_error_is_actionable(self):
        udp = Mock()
        udp.bind.side_effect = PermissionError(13, "Permission denied")
        with tempfile.TemporaryDirectory() as directory, \
                patch("mps4264_app.controller.socket.socket", return_value=udp):
            service = MPS4264Controller(directory)
            with self.assertRaisesRegex(MPSControllerError, "CAP_NET_BIND_SERVICE"):
                service.new_file("low_port", udp_port=23)
            self.assertFalse((Path(directory) / "low_port.dat").exists())
        udp.bind.assert_called_once_with(("0.0.0.0", 23))
        udp.close.assert_called_once()


class NetworkInitializationTests(unittest.TestCase):
    def test_eth0_without_ipv4_is_still_detected(self):
        devices = [
            {"ifname": "wlan0", "flags": ["UP"],
             "addr_info": [{"family": "inet", "local": "192.168.1.5", "prefixlen": 24}]},
            {"ifname": "eth0", "flags": ["BROADCAST", "UP", "LOWER_UP"],
             "addr_info": [{"family": "inet6", "local": "fe80::1234", "prefixlen": 64}]},
        ]
        completed = type("Completed", (), {"returncode": 0, "stdout": json.dumps(devices), "stderr": ""})()
        with patch("mps4264_app.network.platform.system", return_value="Linux"), \
                patch("mps4264_app.network._run_ip", return_value=completed) as run_ip, \
                patch("mps4264_app.network._run_privileged") as privileged:
            result = ensure_ipv4_alias("eth0", "191.30.90.82/16")
        run_ip.assert_called_once_with(["-j", "address", "show"])
        self.assertTrue(result["added"])
        privileged.assert_called_once_with(["address", "add", "191.30.90.82/16", "dev", "eth0"])

    def test_adds_only_missing_address(self):
        devices = [{"ifname": "eth0", "flags": ["UP"],
                    "addr_info": [{"family": "inet", "local": "192.168.1.5", "prefixlen": 24}]}]
        completed = type("Completed", (), {"returncode": 0, "stdout": json.dumps(devices), "stderr": ""})()
        with patch("mps4264_app.network.platform.system", return_value="Linux"), \
                patch("mps4264_app.network._run_ip", return_value=completed), \
                patch("mps4264_app.network._run_privileged") as privileged:
            result = ensure_ipv4_alias("eth0", "191.30.90.82/16")
        self.assertTrue(result["added"])
        privileged.assert_called_once_with(["address", "add", "191.30.90.82/16", "dev", "eth0"])

    def test_existing_address_does_not_reconfigure(self):
        devices = [{"ifname": "eth0", "flags": ["UP"],
                    "addr_info": [{"family": "inet", "local": "191.30.90.82", "prefixlen": 16}]}]
        completed = type("Completed", (), {"returncode": 0, "stdout": json.dumps(devices), "stderr": ""})()
        with patch("mps4264_app.network.platform.system", return_value="Linux"), \
                patch("mps4264_app.network._run_ip", return_value=completed), \
                patch("mps4264_app.network._run_privileged") as privileged:
            result = ensure_ipv4_alias()
        self.assertFalse(result["added"])
        privileged.assert_not_called()

    def test_conflicting_mask_is_not_overwritten(self):
        devices = [{"ifname": "eth0", "flags": ["UP"],
                    "addr_info": [{"family": "inet", "local": "191.30.90.82", "prefixlen": 24}]}]
        completed = type("Completed", (), {"returncode": 0, "stdout": json.dumps(devices), "stderr": ""})()
        with patch("mps4264_app.network.platform.system", return_value="Linux"), \
                patch("mps4264_app.network._run_ip", return_value=completed):
            with self.assertRaises(NetworkInitializationError):
                ensure_ipv4_alias()


if __name__ == "__main__":
    unittest.main()
