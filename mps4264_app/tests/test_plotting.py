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

    def test_errors_and_path_restrictions(self):
        for name in ['../outside.csv', '/outside.csv', r'..\outside.csv', 'run.dat']:
            self.assertEqual(self.client.get('/plot/api/data', query_string={'filename':name}).status_code,400)
        self.assertEqual(self.client.post('/plot/api/upload').status_code,400)
        self.assertEqual(self.client.post('/plot/api/upload',data={'file':(io.BytesIO(b'bad'),'bad.csv')}).status_code,400)


if __name__ == '__main__':
    unittest.main()
