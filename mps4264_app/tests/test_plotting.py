import io
import tempfile
import unittest
from pathlib import Path

from mps4264_app.plotting import create_plot_app, read_pressure_csv

LINUX = ('frame,frame_time_sec,units_index,P01,P02,P03\n'
         '1,0.002,23,4.065393447875977,-957.0142822265625,-999999\n'
         '2,0.004,23,3.656257152557373,,nan\n')
SCANTEL = ('Rate,500\nUnits,PA\nConversion,6894.76\nDate,2015/01/01\n'
           'Time,00:09:27.371617472\nFrame,Valve,XTime,FTime,01Press,02Press,03Press\n'
           '1,Px,0,0.002,4.065393,-957.0143,-999999\n'
           '2,Px,0,0.004,3.656257,,nan\n')
SYNCHRONIZED = ('session_elapsed_ms,speed_kmh,match_valid,frame,frame_time_sec,units_index,P01\n'
                '125,0,1,1,10.002,23,12.5\n'
                '145,3.25,1,2,10.004,23,13.5\n'
                '165,99,0,3,10.006,23,14.5\n'
                '185,nan,1,4,10.008,23,15.5\n')


class CSVReaderTests(unittest.TestCase):
    def test_both_formats_and_invalid_values(self):
        for text, format_name in [(LINUX, 'Linux'), (SCANTEL, 'ScanTel')]:
            data = read_pressure_csv(io.StringIO(text), name='run.csv')
            self.assertEqual(data.source_format, format_name)
            self.assertEqual(data.unit, 'Pa')
            self.assertEqual(data.times, [0.002, 0.004])
            self.assertEqual(data.frames, [1, 2])
            self.assertEqual(data.channels['P03'], [None, None])
            self.assertIsNone(data.channels['P02'][1])
            self.assertEqual(data.to_dict()['invalid_counts']['P03'], 2)
            self.assertAlmostEqual(data.channels['P01'][0], 4.065393, places=6)

    def test_values_are_not_reconverted_or_rounded(self):
        data = read_pressure_csv(io.StringIO(LINUX))
        self.assertEqual(data.channels['P01'][0], 4.065393447875977)

    def test_invalid_layout_time_units_and_values(self):
        for text in ['', 'frame,host_receive_unix_ns\n1,5\n',
                     LINUX.replace('2,0.004', '2,0.001'),
                     LINUX.replace('2,0.004', '1,0.004'),
                     LINUX.replace('2,0.004,23', '2,0.004,27'),
                     LINUX.replace('3.656257152557373', 'bad'),
                     LINUX.replace('2,0.004', '2,inf')]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                read_pressure_csv(io.StringIO(text))

    def test_raw_unit_bom_and_missing_channels(self):
        data = read_pressure_csv(io.StringIO('\ufeff'+LINUX.replace(',23,', ',27,')))
        self.assertEqual(data.unit, 'RAW')
        self.assertEqual(len(data.channels), 3)

    def test_sync_header_speed_validity_and_host_session_time(self):
        data = read_pressure_csv(io.StringIO(SYNCHRONIZED), name='renamed.csv')
        self.assertEqual(data.source_format, 'Synchronized')
        self.assertEqual(data.times, [.125, .145, .165, .185])
        self.assertEqual(data.speed, [0, 3.25, None, None])
        self.assertEqual(data.channels['P01'], [12.5, 13.5, 14.5, 15.5])
        self.assertEqual(data.to_dict()['speed_unit'], 'km/h')
        self.assertEqual(data.to_dict()['time_label'], '同步记录时间（s）')
        self.assertIsNone(read_pressure_csv(io.StringIO(LINUX)).speed)

    def test_sync_duplicate_frames_and_receive_times_are_preserved(self):
        text = SYNCHRONIZED.replace('165,99,0,3', '145,99,0,2')
        data = read_pressure_csv(io.StringIO(text))
        self.assertEqual(data.times, [.125, .145, .145, .185])
        self.assertEqual(data.frames, [1, 2, 2, 4])
        for bad in (text.replace('185,nan', '100,nan'), text.replace('145,3.25,1', '145,bad,1'),
                    text.replace('145,3.25,1', '145,3.25,2')):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                read_pressure_csv(io.StringIO(bad))

    def test_both_pressure_error_flags_are_gaps_but_raw_counts_are_kept(self):
        text = 'frame,frame_time_sec,units_index,P01,P02\n1,0.002,23,999999,-999999\n2,0.004,23,1001,-1001\n'
        data = read_pressure_csv(io.StringIO(text))
        self.assertEqual(data.channels, {'P01': [None, 1001], 'P02': [None, -1001]})
        self.assertEqual(data.to_dict()['sentinel_counts']['P01'], {'positive': 1, 'negative': 0})
        self.assertEqual(data.to_dict()['sentinel_counts']['P02'], {'positive': 0, 'negative': 1})
        raw = read_pressure_csv(io.StringIO(text.replace(',23,', ',27,')))
        self.assertEqual(raw.channels['P01'], [999999, 1001])
        self.assertEqual(raw.channels['P02'], [-999999, -1001])
        self.assertEqual(raw.to_dict()['sentinel_counts']['P01']['positive'], 0)

    def test_known_bad_channels_and_scan_groups_are_selection_metadata(self):
        columns = ','.join(f'P{n:02d}' for n in range(1, 65))
        text = f'frame,frame_time_sec,units_index,{columns}\n1,0.002,23,' + ','.join(['10'] * 64) + '\n'
        data = read_pressure_csv(io.StringIO(text))
        payload = data.to_dict()
        self.assertEqual(set(payload['known_invalid_channels']), {'P02', 'P03', 'P35'})
        self.assertEqual(data.channels['P02'], [10])  # Preserve source values despite exclusion from plotting.
        groups = payload['channel_groups']
        self.assertEqual(len(groups['1']), 16)
        self.assertEqual(groups['1'][:3], ['P01', 'P05', 'P09'])
        self.assertIn('P35', groups['2'])
        self.assertEqual({channel for group in groups.values() for channel in group}, set(data.channels))


class PlotWebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root/'run.csv').write_text(LINUX, encoding='utf-8')
        (self.root/'official.CSV').write_text(SCANTEL, encoding='utf-8')
        (self.root/'run.index.csv').write_text('frame,host_receive_unix_ns\n', encoding='utf-8')
        self.app = create_plot_app(data_dir=self.root, initial_csv=self.root/'run.csv')
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def test_page_files_data_and_local_assets(self):
        self.assertEqual(self.client.get('/').status_code, 302)
        page = self.client.get('/plot')
        self.assertEqual(page.status_code, 200)
        self.assertNotIn(b'cdn.', page.data)
        self.assertEqual(self.client.get('/plot/api/files').json['files'], ['official.CSV', 'run.csv'])
        self.assertEqual(self.client.get('/plot/api/data').json['frame_count'], 2)
        response = self.client.get('/plot/api/data?filename=official.CSV')
        self.assertEqual(response.json['source_format'], 'ScanTel')
        for name in ['pressure_plot.js', 'pressure_plot.css']:
            with self.client.get('/plot/static/'+name) as response:
                self.assertEqual(response.status_code, 200)

    def test_upload_does_not_save_file(self):
        before = set(self.root.iterdir())
        response = self.client.post('/plot/api/upload', data={'file':(io.BytesIO(SCANTEL.encode()),'upload.csv')})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['channels']['P03'], [None, None])
        self.assertEqual(before, set(self.root.iterdir()))

    def test_sync_server_file_and_upload_return_speed(self):
        (self.root / 'synchronized.csv').write_text(SYNCHRONIZED, encoding='utf-8')
        response = self.client.get('/plot/api/data?filename=synchronized.csv')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['speed'], [0, 3.25, None, None])
        response = self.client.post('/plot/api/upload', data={'file': (io.BytesIO(SYNCHRONIZED.encode()), 'sync.csv')})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['times'][0], .125)

    def test_errors_and_path_restrictions(self):
        for name in ['../outside.csv', '/outside.csv', r'..\outside.csv', 'run.dat']:
            self.assertEqual(self.client.get('/plot/api/data', query_string={'filename':name}).status_code,400)
        self.assertEqual(self.client.post('/plot/api/upload').status_code,400)
        self.assertEqual(self.client.post('/plot/api/upload',data={'file':(io.BytesIO(b'bad'),'bad.csv')}).status_code,400)


if __name__ == '__main__':
    unittest.main()
