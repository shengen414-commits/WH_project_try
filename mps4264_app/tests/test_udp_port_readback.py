import unittest
from unittest.mock import Mock
from mps4264_app.controller import MPS4264Controller


class UdpReadbackTests(unittest.TestCase):
    def test_signed_port_readback_is_normalized_for_capture(self):
        controller = MPS4264Controller()
        controller._client = Mock()
        controller._client.command.return_value = 'SET ENUDP 1\r\nSET IPUDP 191.30.90.82 -15513\r\n>'
        result = controller.send_command('LIST UDP')
        self.assertEqual(result['status']['udp_port'], 50023)
        self.assertEqual(result['status']['udp_reported_port'], -15513)
        self.assertIn('-15513', result['response'])  # Preserve the device's actual reply.

    def test_bulk_device_info_uses_the_same_port_normalization(self):
        controller = MPS4264Controller()
        controller._client = Mock()
        controller._client.command.side_effect = lambda command: 'SET IPUDP 191.30.90.82 -15513\r\n>' if command == 'LIST UDP' else '>'
        controller.device_info()
        self.assertEqual(controller.status()['udp_port'], 50023)

    def test_invalid_and_positive_readbacks(self):
        controller = MPS4264Controller()
        controller._apply_udp_info('SET IPUDP 191.30.90.82 60000')
        self.assertEqual(controller.status()['udp_port'], 60000)
        for port in (0, -32769, 65536):
            with self.assertRaises(ValueError):
                controller._apply_udp_info(f'SET IPUDP 191.30.90.82 {port}')


if __name__ == '__main__':
    unittest.main()
