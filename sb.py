#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import platform
import secrets
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import uuid
from pathlib import Path
from typing import Any


CONFIG_DIR = Path("/usr/local/etc/sing-box")
CONFIG_PATH = CONFIG_DIR / "config.json"
INSTALL_INFO_PATH = CONFIG_DIR / "install-info.json"
BINARY_PATH = Path("/usr/local/bin/sing-box")
SERVICE_PATH = Path("/etc/systemd/system/sing-box.service")
WARP_APT_KEYRING = Path("/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg")
WARP_APT_LIST = Path("/etc/apt/sources.list.d/cloudflare-client.list")
WARP_YUM_REPO = Path("/etc/yum.repos.d/cloudflare-warp.repo")
SYSCTL_CONF = Path("/etc/sysctl.conf")
GITHUB_LATEST = "https://api.github.com/repos/SagerNet/sing-box/releases/latest"
DEFAULT_TUIC_PORT = 23456
DEFAULT_SS_PORT = 9090
DEFAULT_WARP_PORT = 40000
DEFAULT_TLS_SERVER_NAME = "bing.com"
DEFAULT_SS_METHOD = "chacha20-ietf-poly1305"
DEFAULT_WARP_OUTBOUND_TAG = "warp"
DEFAULT_DIRECT_OUTBOUND_TAG = "direct"
BBR_QDISC = "net.core.default_qdisc=fq"
BBR_CC = "net.ipv4.tcp_congestion_control=bbr"


def run(
    cmd: list[str],
    check: bool = True,
    capture_output: bool = False,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        check=check,
        text=True,
        capture_output=capture_output,
        input=input_text,
    )


def run_expect(script: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["expect", "-c", script],
        check=check,
        text=True,
        capture_output=True,
    )


def require_root() -> None:
    if os.geteuid() != 0:
        raise SystemExit("This command must be run as root.")


def prompt_text(label: str, default: str | None = None, required: bool = False) -> str:
    prompt = label
    if default is not None:
        prompt += f" [{default}]"
    prompt += ": "
    while True:
        value = input(prompt).strip()
        if value:
            return value
        if default is not None:
            return default
        if not required:
            return ""


def prompt_int(label: str, default: int) -> int:
    while True:
        raw = prompt_text(label, str(default), required=True)
        try:
            value = int(raw)
        except ValueError:
            print("Please enter a valid integer.", file=sys.stderr)
            continue
        if 1 <= value <= 65535:
            return value
        print("Port must be between 1 and 65535.", file=sys.stderr)


def detect_local_ips() -> dict[str, str | None]:
    ipv4 = None
    ipv6 = None

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ipv4 = sock.getsockname()[0]
    except OSError:
        pass

    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as sock:
            sock.connect(("2001:4860:4860::8888", 80))
            ipv6 = sock.getsockname()[0]
    except OSError:
        pass

    return {"ipv4": ipv4, "ipv6": ipv6}


def is_port_in_use(port: int) -> bool:
    if shutil.which("ss"):
        tcp = run(["ss", "-ltn"], check=False, capture_output=True).stdout
        udp = run(["ss", "-lun"], check=False, capture_output=True).stdout
        needle = f":{port} "
        return needle in tcp or needle in udp or tcp.rstrip().endswith(f":{port}") or udp.rstrip().endswith(f":{port}")
    return False


def ensure_port_available(port: int) -> None:
    if is_port_in_use(port):
        raise SystemExit(f"Port already in use: {port}")


def os_release() -> dict[str, str]:
    values: dict[str, str] = {}
    release_file = Path("/etc/os-release")
    if not release_file.exists():
        raise SystemExit("Unsupported system: /etc/os-release not found.")
    for line in release_file.read_text().splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"')
    return values


def detect_arch() -> str:
    machine = platform.machine().lower()
    mapping = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
        "armv7l": "armv7",
        "s390x": "s390x",
    }
    if machine not in mapping:
        raise SystemExit(f"Unsupported architecture: {machine}")
    return mapping[machine]


def fetch_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "singbox-installer"})
    with urllib.request.urlopen(req) as response:
        return json.load(response)


def download_file(url: str, target: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "singbox-installer"})
    with urllib.request.urlopen(req) as response, target.open("wb") as fp:
        shutil.copyfileobj(response, fp)


def pick_singbox_asset(release: dict[str, Any], arch: str) -> str:
    suffix = f"linux-{arch}.tar.gz"
    for asset in release.get("assets", []):
        url = asset.get("browser_download_url", "")
        if url.endswith(suffix):
            return url
    raise SystemExit(f"Could not find sing-box asset for {suffix}")


def install_singbox_latest() -> None:
    require_root()
    arch = detect_arch()
    release = fetch_json(GITHUB_LATEST)
    download_url = pick_singbox_asset(release, arch)

    with tempfile.TemporaryDirectory() as tmp_dir:
        archive_path = Path(tmp_dir) / "sing-box.tar.gz"
        download_file(download_url, archive_path)
        with tarfile.open(archive_path, "r:gz") as tar:
            binary_member = next((m for m in tar.getmembers() if m.name.endswith("/sing-box") or m.name == "sing-box"), None)
            if binary_member is None:
                raise SystemExit("Downloaded archive does not contain sing-box binary.")
            binary_member.name = "sing-box"
            tar.extract(binary_member, path=tmp_dir, filter="data")
        extracted_binary = Path(tmp_dir) / "sing-box"
        BINARY_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(extracted_binary, BINARY_PATH)
        BINARY_PATH.chmod(0o755)


def install_cloudflare_warp() -> None:
    require_root()
    info = os_release()
    distro_id = info.get("ID", "").lower()

    if distro_id in {"ubuntu", "debian"}:
        WARP_APT_KEYRING.parent.mkdir(parents=True, exist_ok=True)
        key_tmp = Path(tempfile.mkdtemp()) / "pubkey.gpg"
        download_file("https://pkg.cloudflareclient.com/pubkey.gpg", key_tmp)
        run(["gpg", "--yes", "--dearmor", "--output", str(WARP_APT_KEYRING), str(key_tmp)])
        codename = info.get("VERSION_CODENAME")
        if not codename and shutil.which("lsb_release"):
            codename = run(["lsb_release", "-cs"], capture_output=True).stdout.strip()
        if not codename:
            raise SystemExit("Unable to detect Debian/Ubuntu codename.")
        WARP_APT_LIST.write_text(
            f"deb [signed-by={WARP_APT_KEYRING}] https://pkg.cloudflareclient.com/ {codename} main\n"
        )
        run(["apt-get", "update"])
        run(["apt-get", "install", "-y", "cloudflare-warp", "expect"])
        return

    if distro_id in {"centos", "rhel"} or info.get("ID_LIKE", "").lower().find("rhel") != -1:
        run(["rpm", "-e", "gpg-pubkey(4fa1c3ba-61abda35)"], check=False)
        run(["rpm", "--import", "https://pkg.cloudflareclient.com/pubkey.gpg"])
        repo_tmp = Path(tempfile.mkdtemp()) / "cloudflare-warp.repo"
        download_file("https://pkg.cloudflareclient.com/cloudflare-warp-ascii.repo", repo_tmp)
        WARP_YUM_REPO.write_text(repo_tmp.read_text())
        pkg_mgr = "dnf" if shutil.which("dnf") else "yum"
        run([pkg_mgr, "-y", "update"])
        run([pkg_mgr, "-y", "install", "cloudflare-warp", "expect"])
        return

    raise SystemExit(f"Unsupported distro for cloudflare-warp install: {distro_id or 'unknown'}")


def registration_expect_script() -> str:
    return r"""
set timeout 120
spawn warp-cli registration new
expect {
    -re {Accept Terms of Service and Privacy Policy\? \[y/N\]} {
        send "y\r"
        exp_continue
    }
    eof
}
"""


def configure_warp(license_key: str | None, proxy_port: int) -> None:
    require_root()
    run_expect(registration_expect_script(), check=False)
    if license_key:
        run(["warp-cli", "registration", "license", license_key])
    run(["warp-cli", "tunnel", "protocol", "set", "MASQUE"])
    run(["warp-cli", "mode", "proxy"])
    run(["warp-cli", "proxy", "port", str(proxy_port)])
    run(["warp-cli", "connect"])


def update_warp_license(license_key: str) -> None:
    require_root()
    run(["warp-cli", "registration", "license", license_key])


def warp_status() -> dict[str, Any]:
    result = run(["warp-cli", "status"], check=False, capture_output=True)
    return {"exit_code": result.returncode, "status": result.stdout.strip() or result.stderr.strip()}


def ensure_directories() -> None:
    require_root()
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def random_uuid() -> str:
    return str(uuid.uuid4())


def random_password(length: int = 16) -> str:
    return secrets.token_urlsafe(length)[:length]


def generate_self_signed_cert(domain: str) -> dict[str, str]:
    require_root()
    cert = Path(f"/etc/ssl/private/{domain}.crt")
    key = Path(f"/etc/ssl/private/{domain}.key")
    cert.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "openssl",
            "req",
            "-x509",
            "-nodes",
            "-newkey",
            "ec",
            "-pkeyopt",
            "ec_paramgen_curve:prime256v1",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-subj",
            f"/CN={domain}",
            "-days",
            "3650",
        ]
    )
    key.chmod(0o600)
    cert.chmod(0o644)
    return {"certificate_path": str(cert), "key_path": str(key)}


def build_config(*, tuic_port: int, ss_port: int, warp_port: int, tls_server_name: str, cert_path: str, key_path: str, tuic_username: str, tuic_uuid: str, tuic_password: str, ss_method: str, ss_password: str) -> dict[str, Any]:
    return {
        "log": {
            "disabled": False,
            "level": "info",
            "timestamp": True,
        },
        "route": {
            "rules": [
                {
                    "inbound": ["tuic-in"],
                    "outbound": DEFAULT_WARP_OUTBOUND_TAG,
                },
                {
                    "inbound": ["ss-in"],
                    "outbound": DEFAULT_WARP_OUTBOUND_TAG,
                },
            ],
            "auto_detect_interface": True,
        },
        "inbounds": [
            {
                "type": "tuic",
                "tag": "tuic-in",
                "listen": "::",
                "listen_port": tuic_port,
                "users": [
                    {
                        "name": tuic_username,
                        "uuid": tuic_uuid,
                        "password": tuic_password,
                    }
                ],
                "congestion_control": "bbr",
                "auth_timeout": "3s",
                "zero_rtt_handshake": False,
                "heartbeat": "10s",
                "tls": {
                    "enabled": True,
                    "server_name": tls_server_name,
                    "alpn": ["h3"],
                    "certificate_path": cert_path,
                    "key_path": key_path,
                },
            },
            {
                "type": "shadowsocks",
                "tag": "ss-in",
                "listen": "::",
                "listen_port": ss_port,
                "method": ss_method,
                "password": ss_password,
            },
        ],
        "outbounds": [
            {
                "type": "direct",
                "tag": DEFAULT_DIRECT_OUTBOUND_TAG,
            },
            {
                "type": "socks",
                "tag": DEFAULT_WARP_OUTBOUND_TAG,
                "server": "127.0.0.1",
                "server_port": warp_port,
            },
        ],
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def write_config(payload: dict[str, Any]) -> None:
    require_root()
    ensure_directories()
    write_json(CONFIG_PATH, payload)


def write_install_info(payload: dict[str, Any]) -> None:
    require_root()
    ensure_directories()
    write_json(INSTALL_INFO_PATH, payload)


def read_install_info() -> dict[str, Any]:
    if not INSTALL_INFO_PATH.exists():
        raise SystemExit(f"Install info not found: {INSTALL_INFO_PATH}")
    return json.loads(INSTALL_INFO_PATH.read_text())


def service_unit() -> str:
    return """[Unit]
Description=sing-box service
Documentation=https://sing-box.sagernet.org
After=network.target nss-lookup.target

[Service]
CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_BIND_SERVICE CAP_SYS_PTRACE CAP_DAC_READ_SEARCH
AmbientCapabilities=CAP_NET_ADMIN CAP_NET_BIND_SERVICE CAP_SYS_PTRACE CAP_DAC_READ_SEARCH
ExecStart=/usr/local/bin/sing-box run -c /usr/local/etc/sing-box/config.json
ExecReload=/bin/kill -HUP $MAINPID
Restart=on-failure
RestartSec=10s
LimitNOFILE=infinity

[Install]
WantedBy=multi-user.target
"""


def install_service() -> None:
    require_root()
    SERVICE_PATH.write_text(service_unit())
    run(["systemctl", "daemon-reload"])

def bbr_status() -> dict[str, Any]:
    qdisc = run(["sysctl", "-n", "net.core.default_qdisc"], capture_output=True).stdout.strip()
    cc = run(["sysctl", "-n", "net.ipv4.tcp_congestion_control"], capture_output=True).stdout.strip()
    return {"enabled": qdisc == "fq" and cc == "bbr", "default_qdisc": qdisc, "tcp_congestion_control": cc}


def read_sysctl_conf() -> list[str]:
    if not SYSCTL_CONF.exists():
        return []
    return SYSCTL_CONF.read_text().splitlines()


def write_sysctl_conf(lines: list[str]) -> None:
    require_root()
    SYSCTL_CONF.write_text("\n".join(lines).rstrip() + "\n")


def enable_bbr() -> None:
    require_root()
    lines = [line for line in read_sysctl_conf() if line.strip() not in {BBR_QDISC, BBR_CC}]
    lines.extend([BBR_QDISC, BBR_CC])
    write_sysctl_conf(lines)
    run(["sysctl", "-p"])


def bbr_command(args: argparse.Namespace) -> None:
    if args.action == "status":
        print(json.dumps(bbr_status(), indent=2, ensure_ascii=False))
        return
    if args.action == "enable":
        enable_bbr()
        print(json.dumps(bbr_status(), indent=2, ensure_ascii=False))
        return
    raise SystemExit(f"Unsupported BBR action: {args.action}")


def validate_config(strict: bool = False) -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise SystemExit(f"Config not found: {CONFIG_PATH}")
    config = json.loads(CONFIG_PATH.read_text())

    if not isinstance(config, dict):
        raise SystemExit("config.json must be a JSON object.")
    if not isinstance(config.get("inbounds"), list):
        raise SystemExit("config.inbounds must be a list.")
    if not isinstance(config.get("outbounds"), list):
        raise SystemExit("config.outbounds must be a list.")
    if not isinstance(config.get("route"), dict):
        raise SystemExit("config.route must be an object.")

    seen_tags: set[str] = set()
    seen_ports: set[int] = set()
    for inbound in config["inbounds"]:
        tag = inbound.get("tag")
        port = inbound.get("listen_port")
        if not isinstance(tag, str) or not tag:
            raise SystemExit("Each inbound must have a string tag.")
        if tag in seen_tags:
            raise SystemExit(f"Duplicate inbound tag: {tag}")
        seen_tags.add(tag)
        if not isinstance(port, int):
            raise SystemExit(f"Inbound {tag} has invalid listen_port.")
        if port in seen_ports:
            raise SystemExit(f"Duplicate listen_port: {port}")
        seen_ports.add(port)

    if strict and BINARY_PATH.exists():
        run([str(BINARY_PATH), "check", "-c", str(CONFIG_PATH)])

    return {"status": "ok", "inbounds": len(config["inbounds"])}


def validate_config_command(args: argparse.Namespace) -> None:
    print(json.dumps(validate_config(strict=args.strict), indent=2, ensure_ascii=False))


def show_config_command(args: argparse.Namespace) -> None:
    if not CONFIG_PATH.exists():
        raise SystemExit(f"Config not found: {CONFIG_PATH}")
    print(CONFIG_PATH.read_text(), end="")


def systemctl_state(action: str, unit: str) -> str:
    result = run(["systemctl", action, unit], check=False, capture_output=True)
    return (result.stdout or result.stderr).strip() or "unknown"


def render_install_summary(info: dict[str, Any]) -> str:
    ip = info.get("ip", {})
    warp = info.get("warp", {})
    tuic = info.get("tuic", {})
    shadowsocks = info.get("shadowsocks", {})
    warp_state = info.get("warp_status", {})
    warp_message = warp_state.get("status", "unknown")
    first_warp_line = warp_message.splitlines()[0] if isinstance(warp_message, str) and warp_message else "unknown"
    singbox_enabled = systemctl_state("is-enabled", "sing-box.service")
    singbox_active = systemctl_state("is-active", "sing-box.service")

    return "\n".join(
        [
            "安装完成：",
            f"- warp-cli 已连接：{first_warp_line}",
            f"- sing-box 已安装：{BINARY_PATH}",
            f"- sing-box.service 已启用并在运行：{singbox_enabled} / {singbox_active}",
            "- 配置文件已生成：",
            f"  - {CONFIG_PATH}",
            f"  - {INSTALL_INFO_PATH}",
            "",
            "监听情况：",
            f"- TUIC: {tuic.get('listen_port', 'unknown')}/udp",
            f"- Shadowsocks: {shadowsocks.get('listen_port', 'unknown')}",
            f"- WARP SOCKS: 127.0.0.1:{warp.get('proxy_port', 'unknown')}",
            "",
            "本次生成的关键信息：",
            f"- TUIC username: {tuic.get('username', 'unknown')}",
            f"- TUIC uuid: {tuic.get('uuid', 'unknown')}",
            f"- TUIC password: {tuic.get('password', 'unknown')}",
            f"- SS password: {shadowsocks.get('password', 'unknown')}",
            "",
            "服务器 IP：",
            f"- IPv4: {ip.get('ipv4') or 'N/A'}",
            f"- IPv6: {ip.get('ipv6') or 'N/A'}",
        ]
    )


def show_info_command(args: argparse.Namespace) -> None:
    info = read_install_info()
    info["ip"] = detect_local_ips()
    info["warp_status"] = warp_status()
    print(render_install_summary(info))


def show_ip_command(args: argparse.Namespace) -> None:
    print(json.dumps(detect_local_ips(), indent=2, ensure_ascii=False))


def install_command(args: argparse.Namespace) -> None:
    require_root()

    license_key = args.license
    tuic_port = args.tuic_port if args.tuic_port is not None else DEFAULT_TUIC_PORT
    ss_port = args.ss_port if args.ss_port is not None else DEFAULT_SS_PORT
    warp_port = args.warp_port if args.warp_port is not None else DEFAULT_WARP_PORT
    tls_server_name = args.tls_server_name or DEFAULT_TLS_SERVER_NAME

    for port in {tuic_port, ss_port, warp_port}:
        ensure_port_available(port)

    tuic_username = random_uuid().split("-")[0]
    tuic_uuid = random_uuid()
    tuic_password = random_password(12)
    ss_password = random_password(22)

    print("Installing Cloudflare WARP...")
    install_cloudflare_warp()
    configure_warp(license_key, warp_port)

    print("Installing sing-box latest...")
    install_singbox_latest()

    print("Generating self-signed certificate...")
    certs = generate_self_signed_cert(tls_server_name)

    config = build_config(
        tuic_port=tuic_port,
        ss_port=ss_port,
        warp_port=warp_port,
        tls_server_name=tls_server_name,
        cert_path=certs["certificate_path"],
        key_path=certs["key_path"],
        tuic_username=tuic_username,
        tuic_uuid=tuic_uuid,
        tuic_password=tuic_password,
        ss_method=DEFAULT_SS_METHOD,
        ss_password=ss_password,
    )
    write_config(config)
    install_service()
    enable_bbr()
    run(["systemctl", "enable", "sing-box.service"])
    run(["systemctl", "restart", "sing-box.service"])

    install_info = {
        "tuic": {
            "listen_port": tuic_port,
            "username": tuic_username,
            "uuid": tuic_uuid,
            "password": tuic_password,
            "tls_server_name": tls_server_name,
            "certificate_path": certs["certificate_path"],
            "key_path": certs["key_path"],
        },
        "shadowsocks": {
            "listen_port": ss_port,
            "method": DEFAULT_SS_METHOD,
            "password": ss_password,
        },
        "warp": {
            "proxy_port": warp_port,
        },
    }
    write_install_info(install_info)
    show_info_command(args)


def set_license_command(args: argparse.Namespace) -> None:
    update_warp_license(args.license)
    if args.reconnect:
        run(["warp-cli", "connect"])
    print(json.dumps(warp_status(), indent=2, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="One-click sing-box + Cloudflare WARP installer.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    install_parser = subparsers.add_parser("install", help="Install cloudflare-warp, sing-box, config, and systemd service.")
    install_parser.add_argument("--license", help="Optional Cloudflare WARP license key.")
    install_parser.add_argument("--tuic-port", type=int, help="TUIC listen port.")
    install_parser.add_argument("--ss-port", type=int, help="Shadowsocks listen port.")
    install_parser.add_argument("--warp-port", type=int, help="Local WARP proxy port.")
    install_parser.add_argument("--tls-server-name", help="Self-signed TLS server_name.", default=DEFAULT_TLS_SERVER_NAME)

    license_parser = subparsers.add_parser("set-license", help="Update Cloudflare WARP license.")
    license_parser.add_argument("license", help="Cloudflare WARP license key.")
    license_parser.add_argument("--reconnect", action="store_true", help="Reconnect WARP after updating the license.")

    subparsers.add_parser("show-config", help="Print generated config.json.")
    subparsers.add_parser("show-info", help="Print generated ports, credentials, IP, and WARP status.")
    subparsers.add_parser("show-ip", help="Detect and print local IPv4/IPv6.")

    validate_parser = subparsers.add_parser("validate-config", help="Validate config.json.")
    validate_parser.add_argument("--strict", action="store_true", help="Also run 'sing-box check -c config.json'.")

    bbr_parser = subparsers.add_parser("bbr", help="Manage BBR.")
    bbr_parser.add_argument("action", choices=["status", "enable"])

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "install":
        install_command(args)
        return 0
    if args.command == "set-license":
        set_license_command(args)
        return 0
    if args.command == "show-config":
        show_config_command(args)
        return 0
    if args.command == "show-info":
        show_info_command(args)
        return 0
    if args.command == "show-ip":
        show_ip_command(args)
        return 0
    if args.command == "validate-config":
        validate_config_command(args)
        return 0
    if args.command == "bbr":
        bbr_command(args)
        return 0
    parser.error("Unknown command")
    return 2


if __name__ == "__main__":
    sys.exit(main())
