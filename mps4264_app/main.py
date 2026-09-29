"""Run with `python3 -m mps4264_app.main` or `python3 mps4264_app/main.py`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mps4264_app.controller import MPS4264Controller
from mps4264_app.web import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="MPS4264 Gen1 可视化采集面板")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Web 监听地址；远程访问可用 0.0.0.0，但应只在可信局域网使用")
    parser.add_argument("--port", type=int, default=5055)
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).parent / "recordings")
    args = parser.parse_args()
    controller = MPS4264Controller(args.data_dir)
    app = create_app(controller)
    if args.host == "0.0.0.0":
        print("注意：当前面板无用户认证，设备控制接口将暴露在所有网卡；仅用于可信隔离网络。")
    print(f"面板地址：http://{args.host}:{args.port}")
    try:
        app.run(host=args.host, port=args.port, threaded=True, use_reloader=False)
    finally:
        controller.disconnect()


if __name__ == "__main__":
    main()
