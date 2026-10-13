"""Dashboard HTTP adapter; no device commands are run on application startup."""
from flask import Response, jsonify, request, send_file
from mps4264_app.controller import MPSControllerError
from mps4264_app.network import NetworkInitializationError
try:
    from .sync_recording import SyncRecordingError
except ImportError:
    from sync_recording import SyncRecordingError


def register_sync_routes(app, coordinator):
    app.config['SYNC_RECORDER'] = coordinator
    from mps4264_app.plotting import register_plot_routes
    register_plot_routes(app, data_dir=coordinator.root / 'Sync_Records', recursive=True)

    def execute(callback):
        try:
            result = callback()
            return result if isinstance(result, Response) else jsonify(result)
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
        for key in ('init_network', 'confirm_reboot', 'zero_pressure_confirmed', 'overwrite'):
            if key in data and not isinstance(data[key], bool):
                raise ValueError(f'{key} 必须是布尔值')
        return data

    @app.get('/sync_record/status')
    def sync_status():
        return execute(coordinator.status)

    @app.post('/sync_record/device/<action>')
    def sync_device(action):
        if action == 'command':
            return execute(lambda: coordinator.command(body()))
        return execute(lambda: coordinator.device_operation(action, body()))

    @app.get('/sync_record/files')
    def sync_files():
        return execute(lambda: {'files': coordinator.files()})

    @app.post('/sync_record/convert')
    def sync_convert():
        return execute(lambda: coordinator.convert(body()))

    @app.get('/sync_record/convert/status')
    def sync_convert_status():
        return execute(coordinator.conversion_status)

    @app.get('/sync_record/download')
    def sync_download():
        return execute(lambda: send_file(coordinator.library_path(request.args.get('filename', '')), as_attachment=True))

    @app.post('/sync_record/start')
    def sync_start():
        def start():
            data = body()
            return coordinator.start(data.get('duration_sec', 60), data.get('max_delta_ms', 30))
        return execute(start)

    @app.post('/sync_record/stop')
    def sync_stop():
        return execute(coordinator.stop)
