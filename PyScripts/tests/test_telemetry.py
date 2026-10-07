import csv
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyScripts.telemetry import CsvRecorder, EncoderStream


class StreamTests(unittest.TestCase):
    def test_forward_reverse_and_sequence_loss(self):
        stream = EncoderStream()
        stream.decode('[ENC],0,10,0', time.time_ns())
        row = stream.decode('[ENC],1,20,2', time.time_ns())
        self.assertAlmostEqual(row['rpm'], 1000)
        row = stream.decode('[ENC],4,50,-8', time.time_ns())
        self.assertEqual(row['sequence_gaps'], 2)
        self.assertLess(row['rpm'], 0)
        self.assertGreater(row['kmh'], 0)

    def test_reboot_does_not_create_speed_spike(self):
        stream = EncoderStream()
        stream.decode('[ENC],100,5000,500', time.time_ns())
        row = stream.decode('[ENC],0,10,0', time.time_ns())
        self.assertEqual((row['device_epoch'], row['rpm']), (1, 0))
        self.assertAlmostEqual(stream.decode('[ENC],1,20,1', time.time_ns())['rpm'], 500)

    def test_uint_time_sequence_and_signed_position_rollover(self):
        stream = EncoderStream()
        stream.decode('[ENC],4294967295,4294967290,2147483647', time.time_ns())
        row = stream.decode('[ENC],0,4,-2147483648', time.time_ns())
        self.assertAlmostEqual(row['rpm'], 500)
        self.assertEqual((row['sequence_gaps'], row['device_epoch']), (0, 0))

    def test_malformed_and_duplicate_packets(self):
        stream = EncoderStream()
        for line in ('[DRIVE] 模式: WEB | 油门: 1500', '[ENC],a,1,2', '[ENC],1,2',
                     '[ENC],-1,2,3', '[ENC],1,2,2147483648'):
            self.assertIsNone(stream.decode(line, time.time_ns()))
        stream.decode('[ENC],1,20,2', time.time_ns())
        self.assertIsNone(stream.decode('[ENC],1,20,2', time.time_ns()))


class RecorderTests(unittest.TestCase):
    def sample(self, seq):
        return EncoderStream().decode(f'[ENC],{seq},10,1', time.time_ns())

    def test_two_recordings_share_samples_without_duplicate_polling(self):
        with tempfile.TemporaryDirectory() as directory:
            recorders = [CsvRecorder(Path(directory) / name) for name in ('speed.csv', 'sensor.csv')]
            for seq in range(50):
                sample = self.sample(seq)
                for recorder in recorders:
                    recorder.submit(sample)
            recorders[0].stop()
            recorders[1].submit(self.sample(50))
            recorders[1].stop()
            for recorder, count in zip(recorders, (50, 51)):
                self.assertTrue(recorder.done.wait(3))
                with open(recorder.path, newline='', encoding='utf-8') as file:
                    rows = list(csv.DictReader(file))
                self.assertEqual(len(rows), count)
                self.assertEqual(len({row['sequence'] for row in rows}), count)
                meta = json.loads(Path(recorder.path + '.meta.json').read_text(encoding='utf-8'))
                self.assertEqual((meta['sample_count'], meta['queue_drops'], meta['finalized']), (count, 0, True))

    def test_duration_stops_even_without_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = CsvRecorder(Path(directory) / 'empty.csv', duration_sec=0.05)
            self.assertTrue(recorder.done.wait(2))
            self.assertEqual(recorder.status()['stop_reason'], 'max_duration')
            self.assertEqual(recorder.status()['sample_count'], 0)

    def test_queue_overflow_is_counted_and_does_not_block(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch('threading.Thread.start'):
                recorder = CsvRecorder(Path(directory) / 'full.csv', queue_size=1)
            recorder.submit(self.sample(0))
            recorder.submit(self.sample(1))
            self.assertEqual(recorder.status()['queue_drops'], 1)
            recorder.stop()
            recorder._write()
            self.assertEqual(recorder.status()['sample_count'], 1)
            meta = json.loads(Path(recorder.path + '.meta.json').read_text(encoding='utf-8'))
            self.assertEqual(meta['queue_drops'], 1)

    def test_write_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch('threading.Thread.start'):
                recorder = CsvRecorder(Path(directory) / 'error.csv')
            recorder.submit(self.sample(0))
            recorder.stop()
            with patch.object(recorder, 'writer') as writer:
                writer.writerow.side_effect = OSError('disk full')
                recorder._write()
            self.assertTrue(recorder.done.is_set())
            self.assertIn('disk full', recorder.status()['stop_reason'])

    def test_existing_file_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'existing.csv'
            path.write_text('old data', encoding='utf-8')
            with self.assertRaises(FileExistsError):
                CsvRecorder(path)
            self.assertEqual(path.read_text(encoding='utf-8'), 'old data')


if __name__ == '__main__':
    unittest.main()
