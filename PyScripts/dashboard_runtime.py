"""Native dashboard startup; network changes occur only when explicitly run."""
import argparse
from mps4264_app.network import NetworkInitializationError, ensure_ipv4_alias


def run_dashboard(app, coordinator, argv=None):
    parser = argparse.ArgumentParser(description='小车网页：相机、车速、底盘和扫描阀同步采集')
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=5000)
    parser.add_argument('--interface', default='eth0', help='连接扫描阀的有线网口')
    parser.add_argument('--local-cidr', default='191.30.90.82/16', help='扫描阀所在网段的本机地址')
    parser.add_argument('--skip-network-init', action='store_true', help='启动时跳过网口配置')
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('网页端口需为 1–65535')
    app.config['MPS_INTERFACE'] = args.interface
    app.config['MPS_LOCAL_CIDR'] = args.local_cidr
    if args.skip_network_init:
        coordinator.network = {'skipped': True, 'reason': '按启动参数跳过网口初始化'}
        print('网口初始化已跳过；连接扫描阀前请确认本机地址和网口。')
    else:
        try:
            coordinator.network = ensure_ipv4_alias(args.interface, args.local_cidr)
            network = coordinator.network
            if network.get('skipped'):
                print(f"网口初始化跳过：{network['reason']}")
            else:
                action = '已添加' if network['added'] else '已存在，无需重复添加'
                print(f"网口初始化：{network['interface']} {network['cidr']} {action}")
        except NetworkInitializationError as exc:
            coordinator.network = {'error': str(exc), 'interface': args.interface, 'cidr': args.local_cidr}
            print(f'网口初始化失败：{exc}')
            print('小车网页继续启动；扫描阀连接前请修复网口配置，或先执行 sudo -v 后重启程序。')
    print(f'记录目录：{coordinator.root}')
    print(f'网页监听：{args.host}:{args.port}')
    try:
        app.run(host=args.host, port=args.port, threaded=True, use_reloader=False)
    finally:
        if not coordinator.shutdown():
            print('同步记录未能完成退出收尾，请检查扫描阀 STOP 状态及保留的原始文件')
