import unittest
from unittest.mock import Mock, patch
from PyScripts.dashboard_runtime import run_dashboard
from mps4264_app.network import NetworkInitializationError


class DashboardRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.app = Mock(config={})
        self.coordinator = Mock(root='/project/Car_Records')
        self.coordinator.shutdown.return_value = True

    def test_default_network_is_checked_before_web_server_starts(self):
        events = []
        def network(interface, cidr):
            events.append('network')
            return {'interface': interface, 'cidr': cidr, 'added': True}
        self.app.run.side_effect = lambda **kwargs: events.append('server')
        with patch('PyScripts.dashboard_runtime.ensure_ipv4_alias', side_effect=network) as configure, patch('builtins.print'):
            run_dashboard(self.app, self.coordinator, [])
        configure.assert_called_once_with('eth0', '191.30.90.82/16')
        self.assertEqual(events, ['network', 'server'])
        self.app.run.assert_called_once_with(host='0.0.0.0', port=5000, threaded=True, use_reloader=False)
        self.coordinator.shutdown.assert_called_once()

    def test_permission_failure_is_visible_and_car_server_still_runs(self):
        with patch('PyScripts.dashboard_runtime.ensure_ipv4_alias', side_effect=NetworkInitializationError('permission denied')), patch('builtins.print'):
            run_dashboard(self.app, self.coordinator, [])
        self.assertIn('permission denied', self.coordinator.network['error'])
        self.app.run.assert_called_once()

    def test_skip_startup_network_does_not_invoke_privileged_commands(self):
        with patch('PyScripts.dashboard_runtime.ensure_ipv4_alias') as configure, patch('builtins.print'):
            run_dashboard(self.app, self.coordinator, ['--skip-network-init'])
        configure.assert_not_called()
        self.assertTrue(self.coordinator.network['skipped'])

    def test_custom_interface_address_and_port_propagate_to_page_and_server(self):
        with patch('PyScripts.dashboard_runtime.ensure_ipv4_alias', return_value={'skipped': True, 'reason': 'test'}) as configure, patch('builtins.print'):
            run_dashboard(self.app, self.coordinator, ['--interface', 'end0', '--local-cidr', '191.30.90.83/16', '--host', '127.0.0.1', '--port', '5001'])
        configure.assert_called_once_with('end0', '191.30.90.83/16')
        self.assertEqual(self.app.config['MPS_LOCAL_CIDR'], '191.30.90.83/16')
        self.assertEqual(self.app.config['MPS_INTERFACE'], 'end0')
        self.app.run.assert_called_once_with(host='127.0.0.1', port=5001, threaded=True, use_reloader=False)

    def test_server_exception_still_closes_sync_recording(self):
        self.app.run.side_effect = OSError('port in use')
        with patch('builtins.print'), self.assertRaises(OSError):
            run_dashboard(self.app, self.coordinator, ['--skip-network-init'])
        self.coordinator.shutdown.assert_called_once()


if __name__ == '__main__':
    unittest.main()
