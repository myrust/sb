# sing-box 管理脚本

基于 Python 3（仅使用标准库）重构，适配 **sing-box 1.14 / 1.15+** 配置语法。

- 可执行文件：`/usr/local/bin/sing-box`
- systemd 服务：`/etc/systemd/system/sing-box.service`
- 配置文件：`/usr/local/etc/sing-box/config.json`
- 节点记录：`/usr/local/etc/sing-box/output.txt`

## 系统依赖安装

包含基础工具（`python3`、`curl`、`wget`、`tar`、`openssl`）及终端二维码打印工具（`qrencode`）：

- **Debian / Ubuntu (`apt`)**

```bash
apt update && apt install -y python3 curl wget tar openssl qrencode
```

- **CentOS / RHEL (`yum` / `dnf`)**

```bash
yum update -y && yum install -y python3 curl wget tar openssl epel-release && yum install -y qrencode
```

## 一键运行

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/myrust/sb/main/singbox.sh)
```

或本地直接运行：

```bash
sudo python3 singbox.py
```

## 支持的协议与功能

- **入站协议**：VLESS (Reality + Vision)、Hysteria2、TUIC v5、ShadowTLS v3、NaiveProxy、Shadowsocks、SOCKS5、HTTP (TLS)、Direct 端口转发
- **Endpoints (1.14 / 1.15)**：WireGuard (WARP)、MASQUE Server (1.15)、Tailscale
- **证书签发**：sing-box 原生 `certificate_providers` (ACME 自动签发与续期)、ECDSA 自签名证书、自定义证书路径
- **节点输出**：关键参数 + URI 分享链接（安装 `qrencode` 后可自动打印终端二维码）
- **配置校验**：每次写入配置自动执行 `sing-box check` 校验
