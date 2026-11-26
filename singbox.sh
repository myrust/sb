#!/usr/bin/env bash
set -euo pipefail

RAW_URL="https://raw.githubusercontent.com/myrust/sb/main/singbox.py"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || pwd)"
LOCAL_PY="${SCRIPT_DIR}/singbox.py"
INSTALL_PY="/usr/local/bin/singbox-manage.py"

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
    echo "请使用 root 权限运行此脚本 (sudo bash singbox.sh)"
    exit 1
fi

install_deps() {
    local pkgs=("python3" "curl" "wget" "tar" "openssl")
    if command -v apt-get >/dev/null 2>&1; then
        apt-get update -qq && apt-get install -y -qq "${pkgs[@]}" qrencode
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y -q "${pkgs[@]}"
        dnf install -y -q qrencode || true
    elif command -v yum >/dev/null 2>&1; then
        yum install -y -q "${pkgs[@]}"
        yum install -y -q qrencode || true
    else
        echo "仅支持 Debian/Ubuntu (apt) 与 CentOS/RHEL (yum/dnf)，请手动安装: ${pkgs[*]} qrencode"
        exit 1
    fi
}

if ! command -v python3 >/dev/null 2>&1 || ! command -v openssl >/dev/null 2>&1 || ! command -v curl >/dev/null 2>&1; then
    echo "正在安装基础依赖..."
    install_deps
fi

if [[ -f "${LOCAL_PY}" ]]; then
    exec python3 "${LOCAL_PY}" "$@"
else
    curl -fsSL "${RAW_URL}" -o "${INSTALL_PY}"
    chmod +x "${INSTALL_PY}"
    exec python3 "${INSTALL_PY}" "$@"
fi
