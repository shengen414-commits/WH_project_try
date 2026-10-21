import unittest
from PyScripts.line_response import LineResponse


class LineResponseTests(unittest.TestCase):
    def setUp(self):
        self.now=[10.]; self.response=LineResponse(clock=lambda:self.now[0])
        self.settings=dict(left_us=180,right_us=120,deadband=.16,slew_us_s=600,direction=1)

    def test_parameters_require_matching_firmware_confirmation(self):
        identifier, packet=self.response.begin(self.settings)
        self.assertEqual(packet,f'@K180,120,0.160,600,1,{identifier}\n'.encode())
        self.assertIsNone(self.response.snapshot()['applied'])
        self.assertFalse(self.response.feed(f'[LINE_CFG] id={identifier+1} ok=1 left=180 right=120 dead=0.160 slew=600 dir=1'))
        self.assertFalse(self.response.feed(f'[LINE_CFG] id={identifier} ok=1 left=181 right=120 dead=0.160 slew=600 dir=1'))
        self.now[0]+=3.1
        self.assertEqual(self.response.snapshot()['request']['status'],'timeout')
        self.assertTrue(self.response.feed(f'[LINE_CFG] id={identifier} ok=1 left=180 right=120 dead=0.160 slew=600 dir=1'))
        self.assertEqual(self.response.snapshot()['applied'],self.settings)
        self.response.invalidate('reboot')
        self.assertIsNone(self.response.snapshot()['applied'])

    def test_all_masks_accept_only_one_or_two_adjacent_probes(self):
        for mask in range(256):
            self.response.feed(f'[LINE],{mask},1000,{mask},{mask}')
            hits=[i for i in range(8) if mask&(1<<i)]
            valid=len(hits)==1 or len(hits)==2 and hits[1]-hits[0]==1
            self.assertEqual(self.response.snapshot()['probes']['valid'],valid)
        self.response.feed('[LINE],1,1000,24,24')
        self.assertEqual(self.response.snapshot()['probes']['side'],'center')
        self.now[0]+=.4
        self.assertFalse(self.response.snapshot()['probes']['valid'])

    def test_invalid_parameters_and_pending_changes_are_rejected(self):
        for key,value in [('left_us',501),('right_us',501),('right_us',True),('deadband',float('nan')),('direction',0),('slew_us_s',0)]:
            with self.assertRaises(ValueError):self.response.begin({**self.settings,key:value})
        self.response.begin(self.settings)
        with self.assertRaises(ValueError):self.response.begin(self.settings)

    def test_500us_boundary_is_accepted(self):
        _,packet=self.response.begin({**self.settings,'left_us':500,'right_us':500})
        self.assertTrue(packet.startswith(b'@K500,500,'))
