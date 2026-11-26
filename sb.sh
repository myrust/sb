#!/usr/bin/env bash
set -euo pipefail

RAW_URL="https://raw.githubusercontent.com/myrust/sb/main/sb.py"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || pwd)"
LOCAL_PY="${SCRIPT_DIR}/sb.py"
SB_BIN="/usr/local/bin/sb"

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
    echo "请使用 root 权限运行此脚本 (sudo bash sb.sh)"
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
    cp -f "${LOCAL_PY}" "${SB_BIN}"
else
    curl -fsSL "${RAW_URL}" -o "${SB_BIN}"
fi
chmod +x "${SB_BIN}"
echo "已注册全局命令：之后可直接输入 sb 打开管理菜单。"

exec "${SB_BIN}" "$@"
