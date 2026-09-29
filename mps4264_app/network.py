"""Idempotent, narrowly scoped Ethernet address setup for the standalone app."""

from __future__ import annotations

import ipaddress
import json
import platform
import subprocess


class NetworkInitializationError(RuntimeError):
    pass


def _run_ip(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(["ip", *arguments], capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise NetworkInitializationError("系统没有 ip 命令；请安装 iproute2") from exc


def _run_privileged(arguments: list[str]) -> None:
    result = _run_ip(arguments)
    if result.returncode == 0:
        return
    detail = (result.stderr or result.stdout).strip()
    if "not permitted" not in detail.lower() and "permission denied" not in detail.lower():
        raise NetworkInitializationError(f"配置网口失败：{detail or result.returncode}")
    try:
        elevated = subprocess.run(["sudo", "-n", "ip", *arguments],
                                  capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise NetworkInitializationError("需要 NET_ADMIN 权限，但系统没有 sudo") from exc
    if elevated.returncode != 0:
        reason = (elevated.stderr or elevated.stdout).strip()
        raise NetworkInitializationError(
            "自动添加网口地址需要管理员权限。可先执行 sudo -v 再启动面板，"
            "或在 systemd 中以特权 ExecStartPre 配置地址、面板本身仍用普通用户运行。"
            f" 原因：{reason}")


def ensure_ipv4_alias(interface: str = "eth0", cidr: str = "191.30.90.82/16") -> dict:
    """Add one address only when absent; preserve all other interface addresses.

    Called only by the executable entry point. Importing the package never
    changes host networking. Non-Linux machines skip this Linux-only setup.
    """
    if platform.system() != "Linux":
        return {"skipped": True, "reason": "仅 Ubuntu/Linux 执行网口初始化"}
    try:
        desired = ipaddress.IPv4Interface(cidr)
    except ValueError as exc:
        raise NetworkInitializationError(f"无效的本机地址：{cidr}") from exc
    if not interface or any(char.isspace() or char in "/\\" for char in interface):
        raise NetworkInitializationError("无效网口名")

    result = _run_ip(["-j", "-4", "address", "show"])
    if result.returncode != 0:
        raise NetworkInitializationError(f"读取网口地址失败：{result.stderr.strip()}")
    try:
        devices = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise NetworkInitializationError("无法解析 ip -j 的网口信息") from exc
    target = next((item for item in devices if item.get("ifname") == interface), None)
    if target is None:
        raise NetworkInitializationError(f"找不到网口 {interface}；请先用 ip -br link 核对接口名")

    already_present = False
    for device in devices:
        for address in device.get("addr_info", []):
            if address.get("family") != "inet" or address.get("local") != str(desired.ip):
                continue
            if device.get("ifname") != interface:
                raise NetworkInitializationError(
                    f"{desired.ip} 已配置在 {device.get('ifname')}，不能再加到 {interface}")
            if int(address.get("prefixlen", -1)) != desired.network.prefixlen:
                raise NetworkInitializationError(
                    f"{interface} 已有 {desired.ip}，但掩码不是 /{desired.network.prefixlen}；请人工核对")
            already_present = True

    if not already_present:
        _run_privileged(["address", "add", str(desired), "dev", interface])
    if "UP" not in target.get("flags", []):
        _run_privileged(["link", "set", "dev", interface, "up"])
    return {"skipped": False, "added": not already_present,
            "interface": interface, "cidr": str(desired)}
