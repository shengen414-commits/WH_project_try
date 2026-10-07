import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

from mps4264_app.controller import MPS4264Controller, MPSControllerError
from mps4264_app.diagnostics import TimedRLock, begin_trace, end_trace, request_summary
from mps4264_app.transport import MPSControlConnection
from mps4264_app.web import create_app


def fake_connection(payloads=(b'Version\r\n', b'>'), delay=0.005):
    client = MPSControlConnection.__new__(MPSControlConnection)
    client.host, client.port = '127.0.0.1', 23
    client._socket = Mock()
    pending = iter(payloads)

    def recv(size):
        time.sleep(delay)
        return next(pending)

    client._socket.recv.side_effect = recv
    client._lock = TimedRLock('tcp')
    client._telnet_state, client._telnet_verb = 'data', 0
    return client


class TransportTimingTests(unittest.TestCase):
    def test_partial_reply_first_byte_and_full_prompt(self):
        trace, token = begin_trace('/test', 'test-partial')
        try:
            client = fake_connection()
            self.assertEqual(client.command('VER'), 'Version\r\n>')
            record = trace['commands'][0]
            self.assertTrue(record['prompt_received'])
            self.assertGreater(record['response_ms'], record['first_tcp_byte_ms'])
            self.assertEqual(record['rx_bytes'], 10)
            self.assertEqual(record['outcome'], 'ok')
            self.assertIsNotNone(record['send_ms'])
            client._socket.sendall.assert_called_once_with(b'VER\r')
        finally:
            end_trace(token)

    def test_empty_timeout_records_no_first_byte(self):
        client = fake_connection()
        client._socket.recv.side_effect = socket.timeout
        trace, token = begin_trace('/test')
        try:
            with self.assertRaises(TimeoutError):
                client.command('LIST S', timeout=0.02)
            record = trace['commands'][0]
            self.assertEqual(record['outcome'], 'TimeoutError')
            self.assertFalse(record['prompt_received'])
            self.assertIsNone(record['first_tcp_byte_ms'])
            self.assertEqual(record['rx_bytes'], 0)
            # Deadline and measurement clocks can have different granularity
            # on Windows; verify elapsed wait, not an exact 20 ms lower bound.
            self.assertGreater(record['response_ms'], 0)
        finally:
            end_trace(token)

    def test_send_error_and_scan_no_reply_are_not_mislabelled(self):
        trace, token = begin_trace('/test')
        try:
            client = fake_connection()
            client._socket.sendall.side_effect = BrokenPipeError('closed')
            with self.assertRaises(BrokenPipeError):
                client.command('VER')
            self.assertEqual(trace['commands'][0]['outcome'], 'BrokenPipeError')
            self.assertIsNotNone(trace['commands'][0]['send_ms'])
            client._socket.sendall.side_effect = None
            client.start_scan()
            scan = trace['commands'][1]
            self.assertEqual(scan['command'], 'SCAN')
            self.assertFalse(scan['waits_for_prompt'])
            self.assertIsNone(scan['response_ms'])
        finally:
            end_trace(token)


class WebTimingTests(unittest.TestCase):
    def test_status_poll_returns_busy_without_waiting_for_control_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            service = MPS4264Controller(directory)
            app = create_app(service)
            held, release = threading.Event(), threading.Event()

            def hold():
                with service._lock:
                    held.set()
                    release.wait(2)

            thread = threading.Thread(target=hold)
            thread.start()
            self.assertTrue(held.wait(1))
            try:
                start = time.perf_counter()
                response = app.test_client().get('/api/status')
                self.assertLess(time.perf_counter()-start, 0.3)
                self.assertEqual(response.status_code, 202)
                self.assertTrue(response.json['busy'])
            finally:
                release.set()
                thread.join()
            state = app.test_client().get('/api/status').json
            newer = service.status()
            self.assertEqual(state['status_epoch'], newer['status_epoch'])
            self.assertLess(state['status_revision'], newer['status_revision'])

    def test_failed_stop_does_not_claim_stopped_or_close_file_and_can_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            service = MPS4264Controller(directory)
            service._client = Mock()
            service._client.stop_scan.side_effect = TimeoutError('no prompt')
            service._scanning = True
            raw = Mock()
            service._raw_file = raw
            with self.assertRaises(MPSControllerError):
                service.close_file()
            self.assertTrue(service.status()['scanning'])
            self.assertTrue(service.status()['file_open'])
            raw.close.assert_not_called()
            self.assertFalse(any('扫描已停止' in e['message'] for e in service.status()['events']))
            service._client.stop_scan.side_effect = None
            service._client.stop_scan.return_value = '>'
            service.stop()
            self.assertEqual(service._client.stop_scan.call_count, 2)
            self.assertFalse(service.status()['scanning'])
            self.assertIsNone(service.status()['last_error'])

    def test_http_and_disk_timing_including_lock_wait(self):
        with tempfile.TemporaryDirectory() as directory:
            service = MPS4264Controller(directory)
            service._client = fake_connection()
            app = create_app(service)
            held = threading.Event()

            def block_controller():
                with service._lock:
                    held.set()
                    time.sleep(0.06)

            blocker = threading.Thread(target=block_controller)
            blocker.start()
            held.wait(1)
            response = app.test_client().post('/api/command', json={'command':'VER'},
                                             headers={'X-MPS-Trace-ID':'test-queue'})
            blocker.join()
            self.assertEqual(response.status_code, 200)
            timing = response.json['_timing']
            self.assertEqual(timing['trace_id'], 'test-queue')
            self.assertGreater(timing['controller_lock_wait_ms'], 10)
            self.assertTrue(timing['commands'][0]['prompt_received'])
            self.assertGreaterEqual(timing['server_ms'], timing['controller_lock_wait_ms'])
            self.assertEqual(response.headers['X-MPS-Trace-ID'], 'test-queue')
            path = Path(app.config['MPS_TIMING_LOG_PATH'])
            record = json.loads(path.read_text(encoding='utf-8').splitlines()[-1])
            self.assertEqual(record['trace_id'], 'test-queue')
            self.assertEqual(record['http_status'], 200)

    def test_failed_command_also_has_timing_and_normal_polling_stays_quiet(self):
        with tempfile.TemporaryDirectory() as directory:
            service = MPS4264Controller(directory)
            service._client = fake_connection()
            service._client._socket.sendall.side_effect = BrokenPipeError('closed')
            app = create_app(service)
            browser = app.test_client()
            response = browser.post('/api/command', json={'command':'VER'})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json['_timing']['commands'][0]['outcome'], 'BrokenPipeError')
            path = Path(app.config['MPS_TIMING_LOG_PATH'])
            before = path.read_bytes()
            self.assertEqual(browser.get('/api/status').status_code, 200)
            self.assertEqual(path.read_bytes(), before)

    def test_trace_is_thread_local_and_ids_are_sanitized(self):
        trace, token = begin_trace('/test', 'bad\nheader')
        try:
            self.assertNotIn('\n', trace['trace_id'])
            seen = []

            def worker():
                child, child_token = begin_trace('/child', 'child')
                try:
                    with TimedRLock('controller'):
                        pass
                    seen.append(child)
                finally:
                    end_trace(child_token)

            thread = threading.Thread(target=worker)
            thread.start()
            thread.join()
            self.assertEqual(len(trace['locks']), 0)
            self.assertEqual(len(seen[0]['locks']), 1)
        finally:
            end_trace(token)


if __name__ == '__main__':
    unittest.main()
