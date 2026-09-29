"""Run with `python3 -m mps4264_app.main` or `python3 mps4264_app/main.py`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mps4264_app.controller import MPS4264Controller
from mps4264_app.network import NetworkInitializationError, ensure_ipv4_alias
from mps4264_app.web import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="MPS4264 Gen1 可视化采集面板")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Web 监听地址；远程访问可用 0.0.0.0，但应只在可信局域网使用")
    parser.add_argument("--port", type=int, default=5055)
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).parent / "recordings")
    parser.add_argument("--interface", default="eth0", help="连接 MPS 的有线网口，默认 eth0")
    parser.add_argument("--local-cidr", default="191.30.90.82/16",
                        help="启动时确保网口具有的地址；只添加，不替换原有地址")
    parser.add_argument("--device-ip", default="191.30.90.102", help="网页设备 IP 默认值")
    parser.add_argument("--skip-network-init", action="store_true",
                        help="仅在网络已由系统配置或本机开发时使用")
    args = parser.parse_args()
    if not args.skip_network_init:
        try:
            network = ensure_ipv4_alias(args.interface, args.local_cidr)
        except NetworkInitializationError as exc:
            parser.exit(2, f"网口初始化失败：{exc}\n")
        if network.get("skipped"):
            print(network["reason"])
        else:
            action = "已添加" if network["added"] else "已存在"
            print(f"网口初始化：{network['interface']} {network['cidr']} {action}")
    controller = MPS4264Controller(args.data_dir)
    default_host_ip = args.local_cidr.split("/", 1)[0]
    app = create_app(controller, default_device_ip=args.device_ip,
                     default_host_ip=default_host_ip)
    if args.host == "0.0.0.0":
        print("注意：当前面板无用户认证，设备控制接口将暴露在所有网卡；仅用于可信隔离网络。")
    print(f"面板地址：http://{args.host}:{args.port}")
    try:
        app.run(host=args.host, port=args.port, threaded=True, use_reloader=False)
    finally:
        controller.disconnect()


if __name__ == "__main__":
    main()
