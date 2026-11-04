"""HTTP test boundary; hardware ownership/recording locks are supplied by dashboard."""
import math
from flask import jsonify, render_template, request, send_file, Response
from .contracts import VisionObservation
from .profile import finite, angle_to_pulse, calibration_status


def register_vehicle_lab(app, lab, operation, manual_status):
    def public_status():
        manual = manual_status()
        return lab.status(manual.get('center_us') if manual.get('fresh') and manual.get('ready') else None)

    def body():
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or request.content_length and request.content_length > 32768:
            raise ValueError('需要有效 JSON，大小不超过32KiB')
        return data

    @app.get('/vehicle-lab')
    def lab_page():
        return render_template('vehicle_lab.html')

    @app.get('/vehicle-lab/status')
    def lab_status():
        return jsonify(public_status())

    @app.get('/vehicle-lab/image')
    def lab_image():
        jpeg = lab.jpeg
        return Response(jpeg, mimetype='image/jpeg', headers={'Cache-Control': 'no-store'}) if jpeg else ('', 204)

    @app.post('/vehicle-lab/channels')
    def lab_channels():
        try:
            lab.set_channels(body())
            return jsonify(public_status())
        except (ValueError,TypeError) as exc:
            return jsonify(error=str(exc)),409

    @app.post('/vehicle-lab/command')
    def lab_command():
        try:
            action = body().get('action')
            if action not in ('start', 'stop', 'zero'):
                raise ValueError('无效观察操作')
            with operation(action):
                lab.command(action)
            return jsonify(public_status())
        except (ValueError, OSError) as exc:
            return jsonify(error=str(exc)), 409

    @app.post('/vehicle-lab/profile')
    def lab_profile():
        try:
            data = body()
            if set(data) - {'geometry', 'camera'} or not data:
                raise ValueError('仅允许修改几何参数和相机标定；转角点必须来自已确认的手动脉宽')
            with operation('profile'):
                if lab.blocking():
                    raise ValueError('先退出联合观察再修改标定，避免新旧参数混用')
                profile = lab.store.snapshot()
                for key in data:
                    profile[key] = data[key]
                lab.store.save(profile)
                with lab.lock:
                    lab.estimator.reset()
            return jsonify(public_status())
        except (ValueError, TypeError, OSError) as exc:
            return jsonify(error=str(exc)), 409

    @app.post('/vehicle-lab/actuator/point')
    @app.post('/steering/manual/calibration/point')
    def lab_actuator_point():
        try:
            data = body()
            angle = data.get('angle_deg')
            if not finite(angle) or abs(angle) > 45 or set(data) - {'angle_deg', 'pulse_us', 'request_id'}:
                raise ValueError('填写实测前轮角度，左正右负；不是舵机轴角度')
            with operation('point'):
                status = manual_status()
                if not all(status.get(k) for k in ('active', 'fresh', 'ready', 'precise')) or status.get('request', {}).get('status') != 'confirmed':
                    raise ValueError('请进入手动舵机测试，等待命令确认后再记录')
                if ('pulse_us' in data and data['pulse_us'] != status['target_us']) or ('request_id' in data and data['request_id'] != status['request'].get('id')):
                    raise ValueError('舵机输出已变化，请重新停稳测量后记录')
                if lab.clock()-status['request'].get('confirmed_at',status['request'].get('sent_at', lab.clock())) < .4:
                    raise ValueError('请等待机构停稳，再测量并记录')
                profile = lab.store.snapshot()
                a = profile['actuator']
                if a['center_us'] != status['center_us']:
                    a['points'] = []
                    a['endpoints'] = {}
                    a['center_us'] = status['center_us']
                p = status['target_us']
                a['points'] = [v for v in a['points'] if v['pulse_us'] != p]
                a['points'].append({'pulse_us': p, 'angle_deg': float(angle)})
                for endpoint in a.get('endpoints', {}).values():
                    if endpoint['pulse_us'] == p:
                        endpoint['angle_deg'] = float(angle)
                lab.store.save(profile)
            if request.path.startswith('/steering/manual/'):
                return jsonify(calibration_status(lab.store.snapshot(), manual_status()))
            return jsonify(public_status())
        except (ValueError, OSError) as exc:
            return jsonify(error=str(exc)), 409

    @app.get('/steering/manual/calibration')
    def manual_calibration_status():
        return jsonify(calibration_status(lab.store.snapshot(), manual_status()))

    @app.post('/steering/manual/calibration/endpoint')
    def manual_calibration_endpoint():
        try:
            data = body()
            side, angle = data.get('side'), data.get('angle_deg')
            if side not in ('left', 'right') or set(data) - {'side', 'angle_deg', 'pulse_us', 'request_id'}:
                raise ValueError('端点应为 left 或 right')
            with operation('point'):
                status = manual_status()
                if not all(status.get(k) for k in ('active', 'fresh', 'ready', 'precise')) or status.get('request', {}).get('status') != 'confirmed':
                    raise ValueError('请等待手动舵机命令确认后再记录端点')
                if ('pulse_us' in data and data['pulse_us'] != status['target_us']) or ('request_id' in data and data['request_id'] != status['request'].get('id')):
                    raise ValueError('舵机输出已变化，请重新停稳测量后记录')
                if lab.clock()-status['request'].get('confirmed_at',status['request'].get('sent_at', lab.clock())) < .4:
                    raise ValueError('请等待机构停稳再记录端点')
                profile = lab.store.snapshot()
                a = profile['actuator']
                if a['center_us'] != status['center_us']:
                    a.update(center_us=status['center_us'], points=[], endpoints={})
                point = {'pulse_us': status['target_us'], 'angle_deg': angle}
                a.setdefault('endpoints', {})[side] = point
                if angle is not None:
                    a['points'] = [p for p in a['points'] if p['pulse_us'] != point['pulse_us']]+[dict(point)]
                lab.store.save(profile)
            return jsonify(calibration_status(lab.store.snapshot(), manual_status()))
        except (ValueError, OSError) as exc:
            return jsonify(error=str(exc)), 409

    @app.post('/steering/manual/calibration/remove')
    def manual_calibration_remove():
        try:
            data = body()
            pulse = data.get('pulse_us')
            if set(data) != {'pulse_us'} or type(pulse) is not int:
                raise ValueError('需要删除记录的脉宽')
            with operation('profile'):
                if lab.blocking():
                    raise ValueError('请先退出联合观察')
                profile = lab.store.snapshot()
                a = profile['actuator']
                a['points'] = [p for p in a['points'] if p['pulse_us'] != pulse]
                a['endpoints'] = {k:v for k,v in a.get('endpoints', {}).items() if v['pulse_us'] != pulse}
                lab.store.save(profile)
            return jsonify(calibration_status(lab.store.snapshot(), manual_status()))
        except (ValueError, OSError) as exc:
            return jsonify(error=str(exc)), 409

    @app.get('/steering/manual/calibration/export')
    def manual_calibration_export():
        import csv
        import io
        a = lab.store.snapshot()['actuator']
        rows = {p['pulse_us']:dict(p) for p in a['points']}
        for point in a.get('endpoints', {}).values():
            rows.setdefault(point['pulse_us'], dict(point))
        output = io.StringIO(newline='')
        writer = csv.writer(output)
        writer.writerow(['pulse_us','angle_deg','endpoint','center_us'])
        for pulse, point in sorted(rows.items()):
            side = next((k for k,v in a.get('endpoints',{}).items() if v['pulse_us'] == pulse),'')
            writer.writerow([pulse,point['angle_deg'],side,a['center_us']])
        return Response(output.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename="steering_calibration.csv"'})

    @app.post('/vehicle-lab/actuator/clear')
    def lab_actuator_clear():
        try:
            with operation('profile'):
                if lab.blocking():
                    raise ValueError('请先退出联合观察')
                profile = lab.store.snapshot()
                profile['actuator'] = {'center_us': None, 'points': [], 'endpoints': {}}
                lab.store.save(profile)
            return jsonify(public_status())
        except (ValueError, OSError) as exc:
            return jsonify(error=str(exc)), 409

    @app.post('/vehicle-lab/actuator/preview')
    def lab_actuator_preview():
        try:
            angle = body().get('angle_deg')
            if not finite(angle):
                raise ValueError('角度无效')
            manual = manual_status()
            center = manual['center_us'] if manual.get('fresh') else lab.status()['raw'].get('servo', {}).get('center_us') if lab.status()['fresh'] else None
            return jsonify(pulse_us=angle_to_pulse(lab.store.snapshot(), math.radians(angle), center, allow_probe=True),
                           estimated_angle_deg=angle, measured_angle_deg=None, sent_to_hardware=False)
        except ValueError as exc:
            return jsonify(error=str(exc)), 409

    @app.post('/vehicle-lab/vision')
    def lab_vision():
        try:
            data = body()
            if set(data) != {'time_s', 'sequence', 'lateral_error_m', 'heading_error_rad', 'covariance', 'confidence'}:
                raise ValueError('视觉接口需要时间、序号、米/弧度误差、2×2协方差和置信度')
            queued = lab.queue_vision(VisionObservation(**data))
            return jsonify(accepted=queued, queued=queued, control_permitted=False)
        except (ValueError, TypeError) as exc:
            return jsonify(error=str(exc)), 409

    @app.get('/vehicle-lab/trace')
    def lab_trace():
        trace = lab.trace
        if trace is None or not trace.done.is_set():
            return jsonify(error='请先退出观察，等待记录完成'), 409
        return send_file(trace.path, as_attachment=True)
