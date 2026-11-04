"""Reference data provenance, signed angles and interpolation without control I/O."""
import csv
import tempfile
import unittest
import json
from pathlib import Path

from PyScripts.steering_reference import SteeringReference, load_reference


class SteeringReferenceTests(unittest.TestCase):
    def test_csv_measurements_get_the_confirmed_sign_and_plateau_is_separate(self):
        reference = load_reference()
        data = reference.snapshot()
        original = Path(__file__).resolve().parents[2] / 'steering_calibration.csv'
        with original.open(encoding='utf-8-sig', newline='') as file:
            rows = list(csv.DictReader(file))
        points = {p['pulse_us']: p for p in data['points']}
        for row in rows:
            pulse, magnitude = int(row['pulse_us']), float(row['angle_deg'])
            if pulse == 1870:
                observation = data['saturation_observations'][0]
                self.assertEqual(observation['measured_pulse_us'], pulse)
                self.assertEqual(observation['measured_angle_deg'], magnitude)
                self.assertFalse(observation['included_in_interpolation'])
                self.assertNotIn(pulse, points)
            else:
                self.assertEqual(points[pulse]['angle_deg'], magnitude * (-1 if pulse < 1500 else 1))
                self.assertTrue(points[pulse]['source'].startswith('csv_row_'))
        self.assertEqual(points[1500]['angle_deg'], 0)
        self.assertIn('not_a_csv_measurement', points[1500]['source'])
        self.assertEqual(points[1850]['angle_deg'], 26)
        self.assertIn('user_reports_same_angle_as_1870', points[1850]['source'])

    def test_direction_asymmetry_interpolation_and_reference_limits(self):
        reference = load_reference()
        for angle, pulse in [(-26,1130),(-22,1200),(-9,1400),(0,1500),(9,1600),(17,1700),(26,1850)]:
            self.assertEqual(reference.angle_to_pulse_deg(angle),pulse)
            self.assertAlmostEqual(reference.pulse_to_angle_deg(pulse),angle)
        self.assertEqual(reference.angle_to_pulse_deg(10),1617)
        self.assertEqual(reference.angle_to_pulse_deg(-10),1383)
        self.assertAlmostEqual(reference.pulse_to_angle_deg(1550),4.5)
        for angle in [-27,27,float('nan'),float('inf'),True,'10']:
            with self.assertRaises(ValueError): reference.angle_to_pulse_deg(angle)
        for pulse in [1129,1851,1870,2500,float('nan'),False,'1500']:
            with self.assertRaises(ValueError): reference.pulse_to_angle_deg(pulse)

    def test_duplicate_saturated_angle_wrong_direction_or_changed_neutral_is_rejected(self):
        for mutation in ('plateau','wrong_direction','center','missing_zero','endpoint'):
            data=load_reference().snapshot()
            if mutation=='plateau':
                data['points'].append({'pulse_us':1870,'angle_deg':26})
                data['endpoints']['left_us']=1870
            elif mutation=='wrong_direction':
                for p in data['points']:p['angle_deg']=-p['angle_deg']
            elif mutation=='center':data['center_us']=1540
            elif mutation=='missing_zero':data['points']=[p for p in data['points'] if p['pulse_us']!=1500]
            else:data['endpoints']['left_us']=1870
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): SteeringReference(data)

    def test_explicit_alternative_does_not_modify_original_reference(self):
        original=load_reference()
        data=original.snapshot()
        data['points'][-1]['angle_deg']=25
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'ground_test.json'
            path.write_text(json.dumps(data),encoding='utf-8')
            alternative=load_reference(path)
            self.assertEqual(alternative.angle_to_pulse_deg(25),1850)
            with self.assertRaises(ValueError):alternative.angle_to_pulse_deg(26)
        self.assertEqual(original.angle_to_pulse_deg(26),1850)
        data['points'][0]['angle_deg']=999
        self.assertEqual(original.snapshot()['points'][0]['angle_deg'],-26)


if __name__=='__main__': unittest.main()
