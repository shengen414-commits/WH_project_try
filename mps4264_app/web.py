"""Browser UI adapter; all hardware logic stays in MPS4264Controller."""

from __future__ import annotations

import threading
import time

from flask import Flask, g, jsonify, render_template, request

from .controller import MPS4264Controller, MPSControllerError
from .plotting import register_plot_routes
from .diagnostics import begin_trace, end_trace, create_timing_logger, request_summary, write_timing


def create_app(controller: MPS4264Controller | None = None,
               default_device_ip: str = "191.30.90.102",
               default_host_ip: str = "191.30.90.82") -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    service = controller or MPS4264Controller()
    app.config["MPS_CONTROLLER"] = service
    register_plot_routes(app, data_dir=service.data_dir)
    timing_logger, timing_path = create_timing_logger(service.data_dir)
    app.config["MPS_TIMING_LOG_PATH"] = str(timing_path)
    app.config["MPS_TIMING_LOGGER"] = timing_logger

    @app.before_request
    def start_request_timing():
        if request.path.startswith("/api/"):
            g.mps_timing_start = time.perf_counter()
            g.mps_timing_trace, g.mps_timing_token = begin_trace(
                request.path, request.headers.get("X-MPS-Trace-ID"))

    @app.after_request
    def finish_request_timing(response):
        if not hasattr(g, "mps_timing_trace"):
            return response
        summary = request_summary(g.mps_timing_trace, g.mps_timing_start)
        summary.update(method=request.method, http_status=response.status_code)
        response.headers["X-MPS-Trace-ID"] = summary["trace_id"]
        response.headers["X-MPS-Server-Ms"] = str(summary["server_ms"])
        response.headers["X-MPS-Controller-Wait-Ms"] = str(summary["controller_lock_wait_ms"])
        response.headers["X-MPS-TCP-Wait-Ms"] = str(summary["tcp_lock_wait_ms"])
        if request.method == "POST":
            payload = response.get_json(silent=True)
            if isinstance(payload, dict):
                payload["_timing"] = summary
                response.set_data(app.json.dumps(payload))
        # Keep routine status polling quiet. Slow GETs expose lock/backlog stalls.
        if request.method == "POST" or summary["server_ms"] >= 500:
            try:
                write_timing(timing_logger, summary)
            except (OSError, ValueError):
                app.logger.exception("命令计时日志写入失败；不改变设备操作结果")
        return response

    @app.teardown_request
    def reset_request_timing(exc):
        token = getattr(g, "mps_timing_token", None)
        if token is not None:
            end_trace(token)
            del g.mps_timing_token
    conversion = {"state": "idle", "result": None, "error": None}
    conversion_lock = threading.Lock()

    def body() -> dict:
        value = request.get_json(silent=True)
        if not isinstance(value, dict):
            raise ValueError("请发送 JSON 对象")
        return value

    @app.errorhandler(ValueError)
    def invalid(exc):
        return jsonify(error=str(exc)), 400

    @app.errorhandler(MPSControllerError)
    def conflict(exc):
        return jsonify(error=str(exc)), 409

    @app.errorhandler(OSError)
    def io_error(exc):
        return jsonify(error=str(exc)), 503

    @app.get("/")
    def index():
        return render_template("index.html", default_device_ip=default_device_ip,
                               default_host_ip=default_host_ip)

    @app.get("/api/status")
    def status():
        state = service.try_status()
        return jsonify(state), (202 if state.get("busy") else 200)

    @app.get("/api/files")
    def files():
        return jsonify(files=service.list_files())

    @app.post("/api/connect")
    def connect():
        data = body()
        return jsonify(service.connect(str(data.get("device_ip", "")),
                                       int(data.get("control_port", 23)),
                                       bool(data.get("confirm_reboot", False))))

    @app.post("/api/disconnect")
    def disconnect():
        return jsonify(service.disconnect())

    @app.post("/api/device-info")
    def device_info():
        return jsonify(info=service.device_info(), status=service.status())

    @app.post("/api/command")
    def command():
        data = body()
        return jsonify(service.send_command(str(data.get("command", "")),
                                            bool(data.get("zero_pressure_confirmed", False))))

    @app.post("/api/configure-udp")
    def configure_udp():
        data = body()
        return jsonify(service.configure_udp(str(data.get("host_ip", "")),
                                              int(data.get("udp_port", 50023))))

    @app.post("/api/parameters")
    def parameters():
        data = body()
        response = service.set_parameters(float(data["rate"]), int(data["fps"]),
                                          int(data.get("fast_group", 0)),
                                          int(data.get("read_mode", 0)),
                                          int(data.get("subset_size", 16)))
        return jsonify(responses=response, status=service.status())

    @app.post("/api/save")
    def save():
        return jsonify(response=service.save_settings())

    @app.post("/api/calz")
    def calz():
        data = body()
        return jsonify(response=service.calz(bool(data.get("zero_pressure_confirmed", False))))

    @app.post("/api/new-file")
    def new_file():
        data = body()
        return jsonify(service.new_file(str(data.get("name", "")),
                                        int(data["udp_port"]) if "udp_port" in data else None))

    @app.post("/api/scan")
    def scan():
        return jsonify(service.scan())

    @app.post("/api/stop")
    def stop():
        return jsonify(service.stop())

    @app.post("/api/close-file")
    def close_file():
        return jsonify(service.close_file())

    @app.get("/api/convert-status")
    def convert_status():
        with conversion_lock:
            return jsonify(dict(conversion))

    @app.post("/api/convert")
    def convert():
        data = body()
        filename = str(data.get("filename", ""))
        group_value = data.get("fast_group")
        fast_group = int(group_value) if group_value not in (None, "") else None
        with conversion_lock:
            if conversion["state"] == "running":
                raise MPSControllerError("已有转换任务在运行")
            conversion.update(state="running", result=None, error=None)

        def worker():
            try:
                result = service.convert_file(filename, fast_group)
                with conversion_lock:
                    conversion.update(state="done", result=result, error=None)
            except Exception as exc:
                with conversion_lock:
                    conversion.update(state="error", result=None, error=str(exc))

        threading.Thread(target=worker, name="mps4264-convert", daemon=True).start()
        return jsonify(state="running"), 202

    return app
