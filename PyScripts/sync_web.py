"""Dashboard HTTP adapter; no device commands are run on application startup."""
from flask import jsonify, request
from mps4264_app.controller import MPSControllerError
from mps4264_app.network import NetworkInitializationError
try:
    from .sync_recording import SyncRecordingError
except ImportError:
    from sync_recording import SyncRecordingError


def register_sync_routes(app, coordinator):
    app.config['SYNC_RECORDER'] = coordinator

    def execute(callback):
        try:
            return jsonify(callback())
        except (ValueError, KeyError, TypeError) as exc:
            return jsonify(error=str(exc)), 400
        except (SyncRecordingError, MPSControllerError) as exc:
            return jsonify(error=str(exc)), 409
        except (OSError, NetworkInitializationError, TimeoutError, ConnectionError) as exc:
            return jsonify(error=str(exc)), 503

    def body():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError('请发送 JSON 对象')
        for key in ('init_network', 'confirm_reboot'):
            if key in data and not isinstance(data[key], bool):
                raise ValueError(f'{key} 必须是布尔值')
        return data

    @app.get('/sync_record/status')
    def sync_status():
        return execute(coordinator.status)

    @app.post('/sync_record/device/<action>')
    def sync_device(action):
        return execute(lambda: coordinator.device_operation(action, body()))

    @app.post('/sync_record/start')
    def sync_start():
        def start():
            data = body()
            return coordinator.start(data.get('duration_sec', 60), data.get('max_delta_ms', 30))
        return execute(start)

    @app.post('/sync_record/stop')
    def sync_stop():
        return execute(coordinator.stop)
