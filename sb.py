#!/usr/bin/env python3
"""sing-box 1.15+ 管理脚本 (Python 3 标准库实现)"""

from __future__ import annotations

import base64
import ipaddress
import json
import os
import platform
import random
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 常量与路径
# ---------------------------------------------------------------------------

SING_BOX_BIN = Path("/usr/local/bin/sing-box")
CONFIG_DIR = Path("/usr/local/etc/sing-box")
CONFIG_FILE = CONFIG_DIR / "config.json"
OUTPUT_FILE = CONFIG_DIR / "output.txt"
SERVICE_FILE = Path("/etc/systemd/system/sing-box.service")
CERT_DIR = Path("/etc/ssl/private")
SCRIPT_RAW_BASE = "https://raw.githubusercontent.com/myrust/sb/main"

CYAN = "\033[0;36m"
YELLOW = "\033[0;33m"
GREEN = "\033[0;32m"
RED = "\033[0;31m"
NC = "\033[0m"

REALITY_PRESETS = [
    "www.microsoft.com",
    "itunes.apple.com",
    "addons.mozilla.org",
    "dl.google.com",
]

SS_METHODS = [
    ("2022-blake3-aes-128-gcm", 16),
    ("2022-blake3-aes-256-gcm", 32),
    ("2022-blake3-chacha20-poly1305", 32),
    ("aes-128-gcm", 16),
    ("aes-256-gcm", 32),
    ("chacha20-ietf-poly1305", 32),
]

GEOSITE_RULESETS = {
    "1": ("openai", "https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set/geosite-openai.srs"),
    "2": ("netflix", "https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set/geosite-netflix.srs"),
    "3": ("disney", "https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set/geosite-disney.srs"),
    "4": ("youtube", "https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set/geosite-youtube.srs"),
}


# ---------------------------------------------------------------------------
# 基础工具函数
# ---------------------------------------------------------------------------

def info(msg: str) -> None:
    print(f"{CYAN}{msg}{NC}")


def ok(msg: str) -> None:
    print(f"{GREEN}{msg}{NC}")


def warn(msg: str) -> None:
    print(f"{YELLOW}{msg}{NC}")


def err(msg: str) -> None:
    print(f"{RED}{msg}{NC}")


def prompt(text: str, default: str = "") -> str:
    suffix = f" (默认: {default})" if default else ""
    try:
        val = input(f"{text}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)
    return val if val else default


def prompt_yes_no(text: str, default: bool = False) -> bool:
    hint = "Y/n" if default else "y/N"
    while True:
        val = prompt(f"{text} [{hint}]").lower()
        if not val:
            return default
        if val in ("y", "yes"):
            return True
        if val in ("n", "no"):
            return False
        err("请输入 y 或 n")


def prompt_int(text: str, min_val: int, max_val: int, default: int | None = None) -> int:
    while True:
        raw = prompt(text, str(default) if default is not None else "")
        if raw.isdigit() and min_val <= int(raw) <= max_val:
            return int(raw)
        err(f"请输入 {min_val}-{max_val} 之间的整数")


def run_cmd(cmd: list[str], check: bool = True, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        check=check,
        text=True,
        capture_output=capture,
    )


def http_get_json(url: str, timeout: int = 15) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "singbox-manager/2.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_get_text(url: str, timeout: int = 10) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8").strip()


def http_download(url: str, dest: Path, timeout: int = 120) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "singbox-manager/2.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as f:
        shutil.copyfileobj(resp, f)


def get_public_ips() -> tuple[str, str]:
    v4, v6 = "", ""
    for url in ("https://api.ipify.org", "https://ipv4.icanhazip.com"):
        try:
            ip = http_get_text(url, timeout=5)
            if isinstance(ipaddress.ip_address(ip), ipaddress.IPv4Address):
                v4 = ip
                break
        except Exception:
            continue
    for url in ("https://api64.ipify.org", "https://ipv6.icanhazip.com"):
        try:
            ip = http_get_text(url, timeout=5)
            if isinstance(ipaddress.ip_address(ip), ipaddress.IPv6Address):
                v6 = ip
                break
        except Exception:
            continue
    return v4, v6


def format_host_for_uri(host: str) -> str:
    try:
        addr = ipaddress.ip_address(host)
        return f"[{host}]" if isinstance(addr, ipaddress.IPv6Address) else host
    except ValueError:
        return host


def is_port_in_use(port: int) -> bool:
    for family in (socket.AF_INET, socket.AF_INET6):
        for sock_type in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
            try:
                with socket.socket(family, sock_type) as s:
                    s.bind(("::" if family == socket.AF_INET6 else "0.0.0.0", port))
            except OSError as e:
                if e.errno in (98, 48):  # EADDRINUSE
                    return True
    return False


def prompt_listen_port(default: int | None = None) -> int:
    if default is None:
        default = random.randint(10000, 59999)
    while True:
        port = prompt_int("请输入监听端口 (1-65535)", 1, 65535, default=default)
        if is_port_in_use(port):
            err(f"端口 {port} 已被占用，请更换端口")
            continue
        return port


# ---------------------------------------------------------------------------
# 系统环境、BBR、防火墙与安装升级
# ---------------------------------------------------------------------------

def configure_dns64(v4: str, v6: str) -> None:
    if v4 or not v6:
        return
    resolv = Path("/etc/resolv.conf")
    if not resolv.exists():
        return
    content = resolv.read_text(encoding="utf-8", errors="ignore")
    if "2001:67c:2b0::4" in content:
        return
    info("检测到 IPv6 单栈环境，正在配置 DNS64...")
    lines = [
        f"# {line}" if line.startswith("nameserver ") else line
        for line in content.splitlines()
    ]
    lines.extend(["nameserver 2001:67c:2b0::4", "nameserver 2001:67c:2b0::6"])
    resolv.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ok("DNS64 配置完成。")


def enable_bbr() -> None:
    sysctl = Path("/etc/sysctl.conf")
    if sysctl.exists() and "net.core.default_qdisc=fq" in sysctl.read_text(errors="ignore"):
        return
    if prompt_yes_no("是否开启 BBR 拥塞控制", default=False):
        with open(sysctl, "a", encoding="utf-8") as f:
            f.write("\nnet.core.default_qdisc=fq\nnet.ipv4.tcp_congestion_control=bbr\n")
        run_cmd(["sysctl", "-p"], check=False, capture=True)
        ok("BBR 已开启。")


def open_firewall_port(port: int) -> None:
    if platform.system() != "Linux":
        return
    if shutil.which("ufw"):
        res = run_cmd(["ufw", "status"], check=False, capture=True)
        if "Status: active" in res.stdout:
            run_cmd(["ufw", "allow", f"{port}/tcp"], check=False, capture=True)
            run_cmd(["ufw", "allow", f"{port}/udp"], check=False, capture=True)
            ok(f"ufw 已放行端口 {port} (TCP/UDP)")
            return
    if shutil.which("firewall-cmd"):
        res = run_cmd(["firewall-cmd", "--state"], check=False, capture=True)
        if "running" in res.stdout:
            run_cmd(["firewall-cmd", "--permanent", f"--add-port={port}/tcp"], check=False, capture=True)
            run_cmd(["firewall-cmd", "--permanent", f"--add-port={port}/udp"], check=False, capture=True)
            run_cmd(["firewall-cmd", "--reload"], check=False, capture=True)
            ok(f"firewalld 已放行端口 {port} (TCP/UDP)")


def get_arch_suffix() -> str:
    machine = platform.machine().lower()
    mapping = {
        "x86_64": "linux-amd64",
        "amd64": "linux-amd64",
        "aarch64": "linux-arm64",
        "arm64": "linux-arm64",
        "armv7l": "linux-armv7",
        "s390x": "linux-s390x",
    }
    if machine not in mapping:
        raise RuntimeError(f"不支持的 CPU 架构: {machine}")
    return mapping[machine]


def install_sing_box_binary(prerelease: bool = False) -> None:
    arch = get_arch_suffix()
    api_url = (
        "https://api.github.com/repos/SagerNet/sing-box/releases"
        if prerelease
        else "https://api.github.com/repos/SagerNet/sing-box/releases/latest"
    )
    info(f"正在获取 sing-box {'测试版' if prerelease else '最新稳定版'} 下载地址...")
    data = http_get_json(api_url)
    release = data[0] if isinstance(data, list) else data
    tag_name = release.get("tag_name", "unknown")
    asset_url = next(
        (
            a["browser_download_url"]
            for a in release.get("assets", [])
            if a.get("name", "").endswith(f"{arch}.tar.gz")
        ),
        None,
    )
    if not asset_url:
        raise RuntimeError(f"未找到适配架构 {arch} 的安装包 ({tag_name})")

    with tempfile.TemporaryDirectory() as tmpdir:
        tar_path = Path(tmpdir) / "sing-box.tar.gz"
        info(f"正在下载 sing-box {tag_name} ...")
        http_download(asset_url, tar_path)
        with tarfile.open(tar_path, "r:gz") as tf:
            for member in tf.getmembers():
                if member.isfile() and member.name.endswith("/sing-box"):
                    member.name = "sing-box"
                    tf.extract(member, path=tmpdir)
                    shutil.move(str(Path(tmpdir) / "sing-box"), str(SING_BOX_BIN))
                    break
    SING_BOX_BIN.chmod(0o755)
    ok(f"sing-box {tag_name} 安装成功。")


def configure_systemd_service() -> None:
    unit = """[Unit]
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
    SERVICE_FILE.write_text(unit, encoding="utf-8")


def ensure_sing_box_installed() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    if not SING_BOX_BIN.exists():
        v4, v6 = get_public_ips()
        configure_dns64(v4, v6)
        enable_bbr()
        print("请选择 sing-box 安装版本：")
        print("  1). 最新稳定版 (Latest，推荐)")
        print("  2). 最新测试版 / 1.15 Pre-release (需体验 MASQUE 等新特性选此项)")
        choice = prompt("请选择 [1-2]", "1")
        install_sing_box_binary(prerelease=(choice == "2"))
    if not SERVICE_FILE.exists():
        configure_systemd_service()
    if not CONFIG_FILE.exists():
        save_config(default_config(), check=False)


# ---------------------------------------------------------------------------
# sing-box 1.14 / 1.15 配置读写与校验
# ---------------------------------------------------------------------------

def default_config() -> dict[str, Any]:
    return {
        "log": {"disabled": False, "level": "info", "timestamp": True},
        "certificate_providers": [],
        "endpoints": [],
        "inbounds": [],
        "outbounds": [{"type": "direct", "tag": "direct"}],
        "http_clients": [],
        "route": {
            "rules": [{"action": "sniff"}],
            "rule_set": [],
        },
    }


def load_config() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        return default_config()
    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    for key, val in default_config().items():
        cfg.setdefault(key, val)
    return cfg


def save_config(cfg: dict[str, Any], check: bool = True) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    # 清理空列表，保持配置整洁
    cleaned = json.loads(json.dumps(cfg))
    for opt_key in ("certificate_providers", "endpoints", "http_clients"):
        if not cleaned.get(opt_key):
            cleaned.pop(opt_key, None)
    if not cleaned.get("route", {}).get("rule_set"):
        cleaned.get("route", {}).pop("rule_set", None)

    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cleaned, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if check and SING_BOX_BIN.exists():
        res = run_cmd([str(SING_BOX_BIN), "check", "-c", str(tmp)], check=False, capture=True)
        if res.returncode != 0:
            err("sing-box 配置校验失败，已保留原配置：")
            print(res.stderr or res.stdout)
            tmp.unlink(missing_ok=True)
            sys.exit(1)
    tmp.replace(CONFIG_FILE)


def restart_sing_box() -> None:
    run_cmd(["systemctl", "daemon-reload"], check=False)
    run_cmd(["systemctl", "enable", "sing-box"], check=False, capture=True)
    res = run_cmd(["systemctl", "restart", "sing-box"], check=False, capture=True)
    if res.returncode == 0:
        ok("sing-box 服务已启动/重启。")
    else:
        err("sing-box 启动失败，请检查日志：journalctl -u sing-box -n 30")


def unique_tag(cfg: dict[str, Any], prefix: str) -> str:
    existing = (
        {i.get("tag") for i in cfg.get("inbounds", [])}
        | {e.get("tag") for e in cfg.get("endpoints", [])}
        | {o.get("tag") for o in cfg.get("outbounds", [])}
        | {c.get("tag") for c in cfg.get("certificate_providers", [])}
    )
    while True:
        tag = f"{prefix}-{secrets.token_hex(2)}"
        if tag not in existing:
            return tag


def add_inbound_route_rule(cfg: dict[str, Any], inbound_tag: str, outbound_tag: str = "direct") -> None:
    rules = cfg.setdefault("route", {}).setdefault("rules", [])
    if not any(r.get("action") == "sniff" for r in rules):
        rules.insert(0, {"action": "sniff"})
    rule = {"inbound": [inbound_tag], "outbound": outbound_tag}
    if rule not in rules:
        rules.append(rule)


def generate_rand_base64(length: int) -> str:
    if SING_BOX_BIN.exists():
        res = run_cmd([str(SING_BOX_BIN), "generate", "rand", "--base64", str(length)], check=False, capture=True)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    return base64.b64encode(secrets.token_bytes(length)).decode("ascii")


def generate_reality_keypair() -> tuple[str, str]:
    res = run_cmd([str(SING_BOX_BIN), "generate", "reality-keypair"], capture=True)
    priv = pub = ""
    for line in res.stdout.splitlines():
        if line.startswith("PrivateKey:"):
            priv = line.split(":", 1)[1].strip()
        elif line.startswith("PublicKey:"):
            pub = line.split(":", 1)[1].strip()
    if not priv or not pub:
        raise RuntimeError("生成 Reality 密钥对失败")
    return priv, pub


def generate_wg_keypair() -> tuple[str, str]:
    res = run_cmd([str(SING_BOX_BIN), "generate", "wg-keypair"], capture=True)
    priv = pub = ""
    for line in res.stdout.splitlines():
        if line.startswith("PrivateKey:"):
            priv = line.split(":", 1)[1].strip()
        elif line.startswith("PublicKey:"):
            pub = line.split(":", 1)[1].strip()
    return priv, pub


def generate_ech_keypair() -> tuple[list[str], list[str]]:
    res = run_cmd([str(SING_BOX_BIN), "generate", "ech-keypair"], capture=True)
    keys, configs = [], []
    target = None
    for line in res.stdout.splitlines():
        if "BEGIN ECH KEYS" in line:
            target = keys
        elif "BEGIN ECH CONFIGS" in line:
            target = configs
        if target is not None:
            target.append(line)
        if "END ECH" in line:
            target = None
    return keys, configs


# ---------------------------------------------------------------------------
# 证书与 Reality 目标站检测
# ---------------------------------------------------------------------------

def get_cert_sha256(cert_path: Path) -> str:
    res = run_cmd(
        ["openssl", "x509", "-in", str(cert_path), "-noout", "-fingerprint", "-sha256"],
        check=False,
        capture=True,
    )
    if res.returncode == 0 and "=" in res.stdout:
        return res.stdout.split("=", 1)[1].strip().replace(":", "").lower()
    return ""


def create_self_signed_cert(domain: str) -> tuple[Path, Path, str]:
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    cert_path = CERT_DIR / f"{domain}.crt"
    key_path = CERT_DIR / f"{domain}.key"
    if not (cert_path.exists() and key_path.exists()):
        info(f"正在为 {domain} 生成 ECDSA P-256 自签名证书...")
        run_cmd([
            "openssl", "req", "-x509", "-newkey", "ec",
            "-pkeyopt", "ec_paramgen_curve:prime256v1",
            "-nodes", "-days", "3650",
            "-subj", f"/CN={domain}",
            "-addext", f"subjectAltName=DNS:{domain}",
            "-keyout", str(key_path),
            "-out", str(cert_path),
        ], capture=True)
    sha256 = get_cert_sha256(cert_path)
    return cert_path, key_path, sha256


def verify_domain_points_here(domain: str, v4: str, v6: str) -> bool:
    try:
        infos = socket.getaddrinfo(domain, None)
        resolved = {item[4][0] for item in infos}
        if (v4 and v4 in resolved) or (v6 and v6 in resolved):
            return True
        warn(f"域名 {domain} 解析为 {resolved}，与本机公网 IP ({v4 or '-'} / {v6 or '-'}) 不一致。")
        return prompt_yes_no("是否仍继续使用该域名", default=False)
    except socket.gaierror:
        warn(f"无法解析域名 {domain}。")
        return prompt_yes_no("是否仍继续使用该域名", default=False)


def setup_tls(
    cfg: dict[str, Any],
    require_real_domain: bool = False,
    alpn: list[str] | None = None,
    allow_ech: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """配置 1.14+ TLS（原生 ACME certificate_providers / 自签名 / 自定义路径）。
    返回 (tls_block, meta_info)
    """
    v4, v6 = get_public_ips()
    print("请选择 TLS 证书方式：")
    print("  1). sing-box 原生 ACME 自动签发（需真实域名，自动续期）")
    if not require_real_domain:
        print("  2). 自动生成自签名证书（无需域名，客户端需跳过验证或固定 SHA-256）")
        print("  3). 手动指定已有证书路径")
        max_opt = 3
    else:
        print("  2). 手动指定已有证书路径")
        max_opt = 2

    choice = prompt_int(f"请选择 [1-{max_opt}]", 1, max_opt, default=1)
    tls_block: dict[str, Any] = {"enabled": True}
    meta: dict[str, Any] = {"insecure": False, "sha256": "", "server_name": ""}

    if alpn:
        tls_block["alpn"] = alpn

    if choice == 1:
        domain = prompt("请输入已解析到本机的域名")
        verify_domain_points_here(domain, v4, v6)
        email = prompt("请输入用于 ACME 通知的邮箱", f"admin@{domain}")
        ca_choice = prompt_int("选择 CA: 1). Let's Encrypt  2). ZeroSSL", 1, 2, default=1)
        ca = "letsencrypt" if ca_choice == 1 else "zerossl"

        provider_tag = unique_tag(cfg, "acme")
        provider: dict[str, Any] = {
            "type": "acme",
            "tag": provider_tag,
            "domain": [domain],
            "email": email,
            "provider": ca,
            "data_directory": str(CONFIG_DIR / "acme"),
        }
        print("选择 ACME 验证方式：")
        print("  1). HTTP-01 / TLS-ALPN-01 自动验证（需 80 或 443 端口不被其他程序占用）")
        print("  2). Cloudflare DNS-01 验证（无需开放 80/443，适合套 CDN 或端口受限）")
        chal = prompt_int("请选择 [1-2]", 1, 2, default=1)
        if chal == 2:
            cf_token = prompt("请输入 Cloudflare API Token (Zone:DNS:Edit)")
            provider["dns01_challenge"] = {
                "provider": "cloudflare",
                "api_token": cf_token,
            }

        cfg.setdefault("certificate_providers", []).append(provider)
        tls_block["server_name"] = domain
        tls_block["certificate_provider"] = provider_tag
        meta["server_name"] = domain

    elif not require_real_domain and choice == 2:
        domain = prompt("请输入自签名证书伪装域名", "bing.com")
        cert_path, key_path, sha256 = create_self_signed_cert(domain)
        tls_block["server_name"] = domain
        tls_block["certificate_path"] = str(cert_path)
        tls_block["key_path"] = str(key_path)
        meta["insecure"] = True
        meta["sha256"] = sha256
        meta["server_name"] = domain

    else:
        domain = prompt("请输入证书对应的域名 (SNI)")
        cert_path = prompt("请输入证书公钥路径 (.crt/.pem)")
        key_path = prompt("请输入证书私钥路径 (.key)")
        tls_block["server_name"] = domain
        tls_block["certificate_path"] = cert_path
        tls_block["key_path"] = key_path
        meta["server_name"] = domain
        if Path(cert_path).exists():
            meta["sha256"] = get_cert_sha256(Path(cert_path))

    if allow_ech and prompt_yes_no("是否启用 ECH (Encrypted Client Hello)", default=False):
        keys, _ = generate_ech_keypair()
        if keys:
            tls_block["ech"] = {"enabled": True, "key": keys}

    return tls_block, meta


def check_reality_target(domain: str) -> bool:
    """检测目标站点是否支持 TLS 1.3 与 HTTP/2"""
    try:
        ctx = ssl.create_default_context()
        ctx.minimum_version = ssl.TLSVersion.TLSv1_3
        ctx.set_alpn_protocols(["h2"])
        with socket.create_connection((domain, 443), timeout=5) as sock:
            with ctx.wrap_socket(sock, server_hostname=domain) as ssock:
                return ssock.version() == "TLSv1.3" and ssock.selected_alpn_protocol() == "h2"
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 通用多用户录入
# ---------------------------------------------------------------------------

def prompt_users_userpass(default_prefix: str = "user") -> list[dict[str, str]]:
    count = prompt_int("请输入用户数量 (1-20)", 1, 20, default=1)
    users = []
    for i in range(1, count + 1):
        uname = prompt(f"第 {i} 个用户名", f"{default_prefix}{i}")
        pwd = prompt(f"第 {i} 个密码 (留空自动生成)") or generate_rand_base64(12)
        users.append({"username": uname, "password": pwd})
    return users


def prompt_users_uuid(with_password: bool = False, flow: str = "") -> list[dict[str, Any]]:
    count = prompt_int("请输入用户数量 (1-20)", 1, 20, default=1)
    users = []
    for i in range(1, count + 1):
        name = prompt(f"第 {i} 个用户名称", f"user{i}")
        uid = prompt(f"第 {i} 个 UUID (留空自动生成)") or str(uuid.uuid4())
        entry: dict[str, Any] = {"name": name, "uuid": uid}
        if with_password:
            entry["password"] = prompt(f"第 {i} 个密码 (留空自动生成)") or generate_rand_base64(12)
        if flow:
            entry["flow"] = flow
        users.append(entry)
    return users


# ---------------------------------------------------------------------------
# 节点输出与 URI / 二维码
# ---------------------------------------------------------------------------

def print_qr_if_available(uri: str) -> None:
    if shutil.which("qrencode"):
        print()
        run_cmd(["qrencode", "-t", "ANSIUTF8", "-m", "1", uri], check=False)


def record_and_display(title: str, lines: list[str], uris: list[str] | None = None) -> None:
    block = [
        f"=== {title} ===",
        *lines,
    ]
    if uris:
        block.append("分享链接:")
        block.extend(uris)
    block.append("")

    text = "\n".join(block) + "\n"
    print(f"\n{CYAN}{text}{NC}")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "a", encoding="utf-8") as f:
        f.write(text)
    ok(f"节点信息已追加保存至 {OUTPUT_FILE}")

    if uris and shutil.which("qrencode") and prompt_yes_no("检测到 qrencode，是否打印二维码", default=True):
        for uri in uris:
            print_qr_if_available(uri)


# ---------------------------------------------------------------------------
# 各协议配置实现 (Inbounds)
# ---------------------------------------------------------------------------

def install_socks() -> None:
    ensure_sing_box_installed()
    cfg = load_config()
    tag = unique_tag(cfg, "socks")
    port = prompt_listen_port()
    users = prompt_users_userpass("socks")

    cfg["inbounds"].append({
        "type": "socks",
        "tag": tag,
        "listen": "::",
        "listen_port": port,
        "users": users,
    })
    add_inbound_route_rule(cfg, tag)
    save_config(cfg)
    open_firewall_port(port)
    restart_sing_box()

    v4, v6 = get_public_ips()
    host = format_host_for_uri(v4 or v6 or "YOUR_IP")
    uris = [
        f"socks://{urllib.parse.quote(u['username'])}:{urllib.parse.quote(u['password'])}@{host}:{port}#{urllib.parse.quote(tag)}"
        for u in users
    ]
    record_and_display("SOCKS5 节点", [f"地址: {v4 or v6}", f"端口: {port}", f"用户: {users}"], uris)


def install_direct() -> None:
    ensure_sing_box_installed()
    cfg = load_config()
    tag = unique_tag(cfg, "direct")
    port = prompt_listen_port()
    target = prompt("请输入转发目标地址 (IP 或域名)")
    override_port = prompt_int("请输入转发目标端口 (1-65535)", 1, 65535)

    cfg["inbounds"].append({
        "type": "direct",
        "tag": tag,
        "listen": "::",
        "listen_port": port,
        "override_address": target,
        "override_port": override_port,
    })
    add_inbound_route_rule(cfg, tag)
    save_config(cfg)
    open_firewall_port(port)
    restart_sing_box()

    v4, v6 = get_public_ips()
    record_and_display(
        "Direct 端口转发",
        [f"入口: {v4 or v6}:{port}", f"转发目标: {target}:{override_port}"],
    )


def install_http() -> None:
    ensure_sing_box_installed()
    cfg = load_config()
    tag = unique_tag(cfg, "http")
    port = prompt_listen_port()
    users = prompt_users_userpass("http")
    tls_block, meta = setup_tls(cfg, require_real_domain=False)

    cfg["inbounds"].append({
        "type": "http",
        "tag": tag,
        "listen": "::",
        "listen_port": port,
        "users": users,
        "tls": tls_block,
    })
    add_inbound_route_rule(cfg, tag)
    save_config(cfg)
    open_firewall_port(port)
    restart_sing_box()

    host = meta["server_name"] or format_host_for_uri((get_public_ips()[0] or get_public_ips()[1] or "YOUR_IP"))
    uris = [
        f"https://{urllib.parse.quote(u['username'])}:{urllib.parse.quote(u['password'])}@{host}:{port}#{urllib.parse.quote(tag)}"
        for u in users
    ]
    record_and_display("HTTPS 代理节点", [f"域名/地址: {host}", f"端口: {port}", f"用户: {users}"], uris)


def install_vless_reality() -> None:
    ensure_sing_box_installed()
    cfg = load_config()
    tag = unique_tag(cfg, "vless-reality")
    port = prompt_listen_port(443 if not is_port_in_use(443) else None)
    users = prompt_users_uuid(flow="xtls-rprx-vision")

    print("请选择 Reality 伪装目标站点（需支持 TLS 1.3 + H2）：")
    for idx, d in enumerate(REALITY_PRESETS, 1):
        print(f"  {idx}). {d}")
    print(f"  {len(REALITY_PRESETS) + 1}). 自定义输入")
    sel = prompt_int("请选择", 1, len(REALITY_PRESETS) + 1, default=1)
    if sel <= len(REALITY_PRESETS):
        sni = REALITY_PRESETS[sel - 1]
    else:
        sni = prompt("请输入伪装域名", "www.microsoft.com")

    info(f"正在检测 {sni}:443 TLS 1.3 + H2 支持情况...")
    if check_reality_target(sni):
        ok(f"{sni} 检测通过 (TLS 1.3 + H2)。")
    else:
        warn(f"{sni} 未通过 TLS 1.3 + H2 检测，可能影响连通性。")

    priv_key, pub_key = generate_reality_keypair()
    short_id = secrets.token_hex(4)

    cfg["inbounds"].append({
        "type": "vless",
        "tag": tag,
        "listen": "::",
        "listen_port": port,
        "users": users,
        "tls": {
            "enabled": True,
            "server_name": sni,
            "reality": {
                "enabled": True,
                "handshake": {"server": sni, "server_port": 443},
                "private_key": priv_key,
                "short_id": [short_id],
            },
        },
    })
    add_inbound_route_rule(cfg, tag)
    save_config(cfg)
    open_firewall_port(port)
    restart_sing_box()

    v4, v6 = get_public_ips()
    host = format_host_for_uri(v4 or v6 or "YOUR_IP")
    uris = []
    for u in users:
        qs = urllib.parse.urlencode({
            "encryption": "none",
            "flow": "xtls-rprx-vision",
            "security": "reality",
            "sni": sni,
            "fp": "chrome",
            "pbk": pub_key,
            "sid": short_id,
            "type": "tcp",
        })
        uris.append(f"vless://{u['uuid']}@{host}:{port}?{qs}#{urllib.parse.quote(u['name'])}")

    record_and_display(
        "VLESS + Reality + Vision 节点",
        [
            f"服务器: {v4 or v6}",
            f"端口: {port}",
            f"SNI/伪装域名: {sni}",
            f"PublicKey: {pub_key}",
            f"ShortID: {short_id}",
            f"UUID: {[u['uuid'] for u in users]}",
        ],
        uris,
    )


def install_tuic() -> None:
    ensure_sing_box_installed()
    cfg = load_config()
    tag = unique_tag(cfg, "tuic")
    port = prompt_listen_port()
    users = prompt_users_uuid(with_password=True)
    cc_choice = prompt_int("拥塞控制算法: 1). bbr (推荐)  2). cubic  3). new_reno", 1, 3, default=1)
    cc = {1: "bbr", 2: "cubic", 3: "new_reno"}[cc_choice]
    tls_block, meta = setup_tls(cfg, require_real_domain=False, alpn=["h3"], allow_ech=True)

    cfg["inbounds"].append({
        "type": "tuic",
        "tag": tag,
        "listen": "::",
        "listen_port": port,
        "users": users,
        "congestion_control": cc,
        "auth_timeout": "3s",
        "zero_rtt_handshake": False,
        "heartbeat": "10s",
        "tls": tls_block,
    })
    add_inbound_route_rule(cfg, tag)
    save_config(cfg)
    open_firewall_port(port)
    restart_sing_box()

    v4, v6 = get_public_ips()
    host = format_host_for_uri(v4 or v6 or "YOUR_IP") if meta["insecure"] else meta["server_name"]
    uris = []
    for u in users:
        params = {
            "congestion_control": cc,
            "alpn": "h3",
            "sni": meta["server_name"],
            "udp_relay_mode": "native",
            "allow_insecure": "1" if meta["insecure"] else "0",
        }
        qs = urllib.parse.urlencode(params)
        uris.append(f"tuic://{u['uuid']}:{urllib.parse.quote(u['password'])}@{host}:{port}?{qs}#{urllib.parse.quote(u['name'])}")

    lines = [f"服务器: {host}", f"端口: {port}", f"SNI: {meta['server_name']}", f"拥塞控制: {cc}"]
    if meta["sha256"]:
        lines.append(f"证书 SHA-256: {meta['sha256']}")
    record_and_display("TUIC v5 节点", lines, uris)


def install_hysteria2() -> None:
    ensure_sing_box_installed()
    cfg = load_config()
    tag = unique_tag(cfg, "hy2")
    port = prompt_listen_port()
    users = prompt_users_userpass("hy2")
    up_mbps = prompt_int("服务端上行带宽 Mbps，填 0 不限速走 BBR", 0, 10000, default=0)
    down_mbps = prompt_int("服务端下行带宽 Mbps，填 0 不限速走 BBR", 0, 10000, default=0)
    tls_block, meta = setup_tls(cfg, require_real_domain=False, alpn=["h3"], allow_ech=True)

    inbound: dict[str, Any] = {
        "type": "hysteria2",
        "tag": tag,
        "listen": "::",
        "listen_port": port,
        "users": [{"name": u["username"], "password": u["password"]} for u in users],
        "tls": tls_block,
    }
    if up_mbps > 0:
        inbound["up_mbps"] = up_mbps
    if down_mbps > 0:
        inbound["down_mbps"] = down_mbps
    obfs_pwd = ""
    if prompt_yes_no("是否启用 Salamander 混淆 (仅限特殊阻断环境，常规建议关闭)", default=False):
        obfs_pwd = prompt("混淆密码 (留空自动生成)") or generate_rand_base64(16)
        inbound["obfs"] = {"type": "salamander", "password": obfs_pwd}

    cfg["inbounds"].append(inbound)
    add_inbound_route_rule(cfg, tag)
    save_config(cfg)
    open_firewall_port(port)
    restart_sing_box()

    v4, v6 = get_public_ips()
    host = format_host_for_uri(v4 or v6 or "YOUR_IP") if meta["insecure"] else meta["server_name"]
    uris = []
    for u in users:
        params: dict[str, str] = {"sni": meta["server_name"], "insecure": "1" if meta["insecure"] else "0"}
        if obfs_pwd:
            params["obfs"] = "salamander"
            params["obfs-password"] = obfs_pwd
        if meta["sha256"]:
            params["pinSHA256"] = meta["sha256"]
        qs = urllib.parse.urlencode(params)
        uris.append(f"hy2://{urllib.parse.quote(u['password'])}@{host}:{port}?{qs}#{urllib.parse.quote(u['username'])}")

    lines = [f"服务器: {host}", f"端口: {port}", f"SNI: {meta['server_name']}"]
    if meta["sha256"]:
        lines.append(f"证书 SHA-256: {meta['sha256']}")
    record_and_display("Hysteria2 节点", lines, uris)


def select_ss_method_and_password() -> tuple[str, str]:
    print("请选择 Shadowsocks 加密方式：")
    for idx, (m, _) in enumerate(SS_METHODS, 1):
        print(f"  {idx}). {m}")
    idx = prompt_int(f"请选择 [1-{len(SS_METHODS)}]", 1, len(SS_METHODS), default=1)
    method, key_len = SS_METHODS[idx - 1]
    pwd = prompt("请输入密码 (2022 协议建议留空自动生成)") or generate_rand_base64(key_len)
    return method, pwd


def install_shadowsocks() -> None:
    ensure_sing_box_installed()
    cfg = load_config()
    tag = unique_tag(cfg, "ss")
    port = prompt_listen_port()
    method, pwd = select_ss_method_and_password()

    cfg["inbounds"].append({
        "type": "shadowsocks",
        "tag": tag,
        "listen": "::",
        "listen_port": port,
        "method": method,
        "password": pwd,
    })
    add_inbound_route_rule(cfg, tag)
    save_config(cfg)
    open_firewall_port(port)
    restart_sing_box()

    v4, v6 = get_public_ips()
    host = format_host_for_uri(v4 or v6 or "YOUR_IP")
    userinfo = base64.urlsafe_b64encode(f"{method}:{pwd}".encode()).decode().rstrip("=")
    uri = f"ss://{userinfo}@{host}:{port}#{urllib.parse.quote(tag)}"
    record_and_display("Shadowsocks 节点", [f"服务器: {v4 or v6}", f"端口: {port}", f"加密: {method}", f"密码: {pwd}"], [uri])


def install_shadowtls() -> None:
    ensure_sing_box_installed()
    cfg = load_config()
    stls_tag = unique_tag(cfg, "shadowtls")
    ss_tag = unique_tag(cfg, "ss-detour")
    port = prompt_listen_port()
    handshake_server = prompt("请输入 TLS 握手伪装域名", "www.microsoft.com")
    stls_users = [{"name": u["username"], "password": u["password"]} for u in prompt_users_userpass("stls")]
    ss_method, ss_pwd = select_ss_method_and_password()

    cfg["inbounds"].extend([
        {
            "type": "shadowtls",
            "tag": stls_tag,
            "listen": "::",
            "listen_port": port,
            "version": 3,
            "users": stls_users,
            "handshake": {"server": handshake_server, "server_port": 443},
            "strict_mode": True,
            "detour": ss_tag,
        },
        {
            "type": "shadowsocks",
            "tag": ss_tag,
            "listen": "127.0.0.1",
            "method": ss_method,
            "password": ss_pwd,
        },
    ])
    add_inbound_route_rule(cfg, stls_tag)
    save_config(cfg)
    open_firewall_port(port)
    restart_sing_box()

    v4, v6 = get_public_ips()
    record_and_display(
        "ShadowTLS v3 + Shadowsocks 节点",
        [
            f"服务器: {v4 or v6}",
            f"端口: {port}",
            f"握手域名: {handshake_server}",
            f"ShadowTLS 用户: {stls_users}",
            f"SS 加密: {ss_method}",
            f"SS 密码: {ss_pwd}",
        ],
    )


def install_naive() -> None:
    ensure_sing_box_installed()
    cfg = load_config()
    tag = unique_tag(cfg, "naive")
    port = prompt_listen_port(443 if not is_port_in_use(443) else None)
    users = prompt_users_userpass("naive")
    tls_block, meta = setup_tls(cfg, require_real_domain=True)

    cfg["inbounds"].append({
        "type": "naive",
        "tag": tag,
        "listen": "::",
        "listen_port": port,
        "users": users,
        "tls": tls_block,
    })
    add_inbound_route_rule(cfg, tag)
    save_config(cfg)
    open_firewall_port(port)
    restart_sing_box()

    host = meta["server_name"]
    uris = [
        f"https://{urllib.parse.quote(u['username'])}:{urllib.parse.quote(u['password'])}@{host}:{port}#{urllib.parse.quote(tag)}"
        for u in users
    ]
    record_and_display("NaiveProxy 节点", [f"域名: {host}", f"端口: {port}", f"用户: {users}"], uris)


# ---------------------------------------------------------------------------
# Endpoints 实现 (1.14 / 1.15: WireGuard WARP, MASQUE Server, Tailscale)
# ---------------------------------------------------------------------------

def ensure_direct_http_client(cfg: dict[str, Any]) -> str:
    """1.14+ 远程 rule_set 需显式使用 http_client"""
    clients = cfg.setdefault("http_clients", [])
    for c in clients:
        if c.get("tag") == "http-direct":
            return "http-direct"
    clients.append({"tag": "http-direct", "detour": "direct"})
    cfg.setdefault("route", {})["default_http_client"] = "http-direct"
    return "http-direct"


def install_wireguard_warp() -> None:
    ensure_sing_box_installed()
    cfg = load_config()
    if any(e.get("type") == "wireguard" for e in cfg.get("endpoints", [])):
        err("已存在 WireGuard Endpoint，请先在节点管理中删除后再重新配置。")
        return

    info("正在向 WARP 接口申请免费账户密钥...")
    wg_data: dict[str, Any] | None = None
    try:
        wg_data = http_get_json("https://api.zeroteam.top/warp?format=sing-box", timeout=15)
    except Exception as e:
        warn(f"自动获取 WARP 失败 ({e})，可手动输入 WireGuard 参数。")

    if wg_data and "private_key" in wg_data:
        priv_key = wg_data["private_key"]
        peer_pub = wg_data["peer_public_key"]
        server = wg_data["server"]
        server_port = int(wg_data["server_port"])
        local_addr = wg_data["local_address"]
        reserved = wg_data.get("reserved", [0, 0, 0])
        mtu = int(wg_data.get("mtu", 1280))
    else:
        priv_key = prompt("WireGuard PrivateKey (留空生成新密钥)") or generate_wg_keypair()[0]
        peer_pub = prompt("Peer PublicKey")
        server = prompt("Peer Server IP", "162.159.192.1")
        server_port = prompt_int("Peer Port", 1, 65535, default=2408)
        local_addr = [prompt("本地 IPv4 CIDR", "172.16.0.2/32"), prompt("本地 IPv6 CIDR", "2606:4700:110::2/128")]
        reserved = [0, 0, 0]
        mtu = 1280

    ep_tag = "warp-wg"
    cfg.setdefault("endpoints", []).append({
        "type": "wireguard",
        "tag": ep_tag,
        "system": False,
        "mtu": mtu,
        "address": local_addr,
        "private_key": priv_key,
        "peers": [{
            "address": server,
            "port": server_port,
            "public_key": peer_pub,
            "allowed_ips": ["0.0.0.0/0", "::/0"],
            "reserved": reserved,
        }],
    })

    # 添加直连/分流选择
    print("请选择要通过 WARP 分流的规则集 (空格分隔编号，如 `1 2`，留空跳过仅添加 Endpoint)：")
    for k, (name, _) in GEOSITE_RULESETS.items():
        print(f"  {k}). {name}")
    picks = prompt("选择规则集").split()
    selected_tags = []
    if picks:
        client_tag = ensure_direct_http_client(cfg)
        rsets = cfg.setdefault("route", {}).setdefault("rule_set", [])
        existing_rs = {r.get("tag") for r in rsets}
        for k in picks:
            if k in GEOSITE_RULESETS:
                name, url = GEOSITE_RULESETS[k]
                rs_tag = f"geosite-{name}"
                selected_tags.append(rs_tag)
                if rs_tag not in existing_rs:
                    rsets.append({
                        "type": "remote",
                        "tag": rs_tag,
                        "format": "binary",
                        "url": url,
                        "http_client": client_tag,
                    })
                    existing_rs.add(rs_tag)

    if selected_tags:
        rules = cfg.setdefault("route", {}).setdefault("rules", [])
        rules.insert(1 if rules and rules[0].get("action") == "sniff" else 0, {
            "rule_set": selected_tags,
            "outbound": ep_tag,
        })

    save_config(cfg)
    restart_sing_box()
    record_and_display(
        "WireGuard (WARP) Endpoint (1.14/1.15 语法)",
        [f"Endpoint Tag: {ep_tag}", f"本地地址: {local_addr}", f"Peer: {server}:{server_port}", f"分流规则: {selected_tags or '无'}"],
    )


def install_masque_server() -> None:
    ensure_sing_box_installed()
    cfg = load_config()
    tag = unique_tag(cfg, "masque")
    port = prompt_listen_port(443 if not is_port_in_use(443) else None)
    users = prompt_users_userpass("masque")
    v4_cidr = prompt("隧道 IPv4 网段", "172.16.10.1/24")
    v6_cidr = prompt("隧道 IPv6 网段", "fd00:10::1/64")
    tls_block, meta = setup_tls(cfg, require_real_domain=True)

    cfg.setdefault("endpoints", []).append({
        "type": "masque-server",
        "tag": tag,
        "listen": "::",
        "listen_port": port,
        "version": [1, 2, 3],
        "users": users,
        "tls": tls_block,
        "address": [v4_cidr, v6_cidr],
        "mtu": 1280,
    })
    add_inbound_route_rule(cfg, tag)
    save_config(cfg)
    open_firewall_port(port)
    restart_sing_box()

    record_and_display(
        "MASQUE Server Endpoint (sing-box 1.15)",
        [
            f"Tag: {tag}",
            f"域名: {meta['server_name']}",
            f"端口: {port} (TCP+UDP/QUIC)",
            f"隧道网段: {v4_cidr}, {v6_cidr}",
            f"用户: {users}",
        ],
    )


def install_tailscale() -> None:
    ensure_sing_box_installed()
    cfg = load_config()
    tag = unique_tag(cfg, "tailscale")
    auth_key = prompt("请输入 Tailscale Auth Key (tskey-auth-...)")
    hostname = prompt("节点显示名称 (hostname)", socket.gethostname())
    control_url = prompt("协调服务器 URL (自建 Headscale 可修改)", "https://controlplane.tailscale.com")
    adv_exit = prompt_yes_no("是否作为 Exit Node 出口节点对外提供代理", default=True)
    exit_node = ""
    if not adv_exit:
        exit_node = prompt("指定上游 Exit Node (留空不使用)")

    ep: dict[str, Any] = {
        "type": "tailscale",
        "tag": tag,
        "auth_key": auth_key,
        "hostname": hostname,
        "state_directory": str(CONFIG_DIR / "tailscale" / tag),
        "advertise_exit_node": adv_exit,
    }
    if control_url != "https://controlplane.tailscale.com":
        ep["control_url"] = control_url
    if exit_node:
        ep["exit_node"] = exit_node

    cfg.setdefault("endpoints", []).append(ep)
    save_config(cfg)
    restart_sing_box()

    record_and_display(
        "Tailscale Endpoint (1.14/1.15)",
        [
            f"Tag: {tag}",
            f"Hostname: {hostname}",
            f"Exit Node 对外出口: {adv_exit}",
            f"Control URL: {control_url}",
        ],
    )


# ---------------------------------------------------------------------------
# 管理操作：查看、删除、更新、重启、卸载
# ---------------------------------------------------------------------------

def view_saved_nodes() -> None:
    if OUTPUT_FILE.exists():
        print(OUTPUT_FILE.read_text(encoding="utf-8"))
    else:
        warn("尚未生成任何节点记录 (output.txt 不存在)。")


def manage_nodes() -> None:
    cfg = load_config()
    items: list[tuple[str, str, str]] = []
    detour_tags = {i.get("detour") for i in cfg.get("inbounds", []) if i.get("type") == "shadowtls" and i.get("detour")}

    for ib in cfg.get("inbounds", []):
        if ib.get("tag") not in detour_tags:
            items.append(("inbound", ib.get("type", ""), ib.get("tag", "")))
    for ep in cfg.get("endpoints", []):
        items.append(("endpoint", ep.get("type", ""), ep.get("tag", "")))

    if not items:
        warn("当前没有任何可管理的入站或 Endpoint 节点。")
        return

    print("当前已配置的节点：")
    for idx, (kind, ptype, tag) in enumerate(items, 1):
        print(f"  {idx}). [{kind}] {ptype:<15} tag={tag}")

    sel = prompt_int(f"请输入要删除的编号 (0 取消)", 0, len(items), default=0)
    if sel == 0:
        return

    kind, ptype, tag = items[sel - 1]
    if kind == "inbound":
        detour = next((i.get("detour") for i in cfg["inbounds"] if i.get("tag") == tag), None)
        remove_tags = {tag} | ({detour} if detour else set())
        cfg["inbounds"] = [i for i in cfg["inbounds"] if i.get("tag") not in remove_tags]
        cfg["route"]["rules"] = [
            r for r in cfg.get("route", {}).get("rules", [])
            if not set(r.get("inbound", [])).intersection(remove_tags)
        ]
    else:
        cfg["endpoints"] = [e for e in cfg["endpoints"] if e.get("tag") != tag]
        cfg["route"]["rules"] = [
            r for r in cfg.get("route", {}).get("rules", [])
            if r.get("outbound") != tag
        ]

    # 清理不再被引用的 certificate_providers
    used_providers = (
        {i.get("tls", {}).get("certificate_provider") for i in cfg.get("inbounds", [])}
        | {e.get("tls", {}).get("certificate_provider") for e in cfg.get("endpoints", [])}
    )
    cfg["certificate_providers"] = [
        p for p in cfg.get("certificate_providers", []) if p.get("tag") in used_providers
    ]

    save_config(cfg)
    restart_sing_box()
    ok(f"已删除 {ptype} ({tag})。")


def update_core() -> None:
    print("请选择更新版本：")
    print("  1). 最新稳定版 (Latest)")
    print("  2). 最新测试版 (Pre-release / 1.15)")
    choice = prompt("请选择 [1-2]", "1")
    install_sing_box_binary(prerelease=(choice == "2"))
    restart_sing_box()


def update_script() -> None:
    target = Path(__file__).resolve()
    info(f"正在从 GitHub 更新脚本至 {target} ...")
    http_download(f"{SCRIPT_RAW_BASE}/sb.py", target)
    target.chmod(0o755)
    sb_bin = Path("/usr/local/bin/sb")
    if target != sb_bin:
        shutil.copy2(target, sb_bin)
        sb_bin.chmod(0o755)
    ok("脚本已更新，输入 sb 即可再次运行。")


def restart_and_status() -> None:
    if CONFIG_FILE.exists():
        res = run_cmd([str(SING_BOX_BIN), "check", "-c", str(CONFIG_FILE)], check=False, capture=True)
        if res.returncode != 0:
            err("配置校验失败：")
            print(res.stderr or res.stdout)
            return
    restart_sing_box()
    run_cmd(["systemctl", "status", "--no-pager", "sing-box"], check=False)


def uninstall_all() -> None:
    if not prompt_yes_no("确定要彻底卸载 sing-box 及所有配置吗", default=False):
        return
    run_cmd(["systemctl", "stop", "sing-box"], check=False, capture=True)
    run_cmd(["systemctl", "disable", "sing-box"], check=False, capture=True)
    SERVICE_FILE.unlink(missing_ok=True)
    SING_BOX_BIN.unlink(missing_ok=True)
    Path("/usr/local/bin/sb").unlink(missing_ok=True)
    shutil.rmtree(CONFIG_DIR, ignore_errors=True)
    run_cmd(["systemctl", "daemon-reload"], check=False)
    ok("sing-box 已彻底卸载。")


# ---------------------------------------------------------------------------
# 主菜单
# ---------------------------------------------------------------------------

def main_menu() -> None:
    actions: dict[str, tuple[str, Any]] = {
        "1": ("VLESS + Reality + Vision", install_vless_reality),
        "2": ("Hysteria2", install_hysteria2),
        "3": ("TUIC v5", install_tuic),
        "4": ("ShadowTLS v3", install_shadowtls),
        "5": ("NaiveProxy", install_naive),
        "6": ("Shadowsocks", install_shadowsocks),
        "7": ("SOCKS5", install_socks),
        "8": ("HTTP (TLS)", install_http),
        "9": ("Direct 端口转发", install_direct),
        "10": ("WireGuard (WARP) Endpoint", install_wireguard_warp),
        "11": ("MASQUE Server Endpoint (1.15)", install_masque_server),
        "12": ("Tailscale Endpoint", install_tailscale),
        "13": ("查看已保存节点", view_saved_nodes),
        "14": ("节点管理 (删除)", manage_nodes),
        "15": ("更新 sing-box 内核", update_core),
        "16": ("更新本脚本", update_script),
        "17": ("校验并重启服务", restart_and_status),
        "18": ("卸载 sing-box", uninstall_all),
    }

    while True:
        print("\n" + "=" * 54)
        print(f"  {CYAN}sing-box 管理脚本 (1.14 / 1.15+){NC}")
        print("=" * 54)
        for k, (label, _) in actions.items():
            print(f"  {CYAN}[{k:>2}]{NC} {label}")
        print(f"  {CYAN}[ 0]{NC} 退出")
        print("=" * 54)

        choice = prompt("请选择 [0-18]", "0")
        if choice == "0":
            break
        if choice in actions:
            try:
                actions[choice][1]()
            except Exception as exc:
                err(f"执行出错: {exc}")
        else:
            err("无效选择，请重新输入。")


if __name__ == "__main__":
    if os.geteuid() != 0 and "--allow-non-root" not in sys.argv:
        warn("建议使用 root 权限运行此脚本 (sudo sb)")
    main_menu()
