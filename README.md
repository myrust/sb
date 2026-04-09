# singbox

这是一个极简的 `sing-box` 服务端一键安装器，固定生成一套可维护的服务端配置：

- `tuic` 入站
- `shadowsocks` 入站
- 本地 `Cloudflare WARP` SOCKS 代理出站
- 两个入站默认都走 `warp`

主入口是 [`singbox.py`](/Users/kk/Github/singbox/singbox.py)。

## 默认行为

- 安装最新版本 `sing-box`
- 安装并配置 `cloudflare-warp`
- 自动执行：
  - `warp-cli registration new`
  - `warp-cli registration license <LICENSE>`
  - `warp-cli tunnel protocol set MASQUE`
  - `warp-cli mode proxy`
  - `warp-cli proxy port 40000`
  - `warp-cli connect`
- 自动检测本机 `IPv4/IPv6`
- 自动生成 `UUID` 和随机密码
- 自动生成 `bing.com` 自签名证书
- 自动写入 `systemd` 服务
- 自动启用 `BBR`
- 不处理防火墙

默认文件位置：

- `sing-box` 可执行文件：`/usr/local/bin/sing-box`
- 配置文件：`/usr/local/etc/sing-box/config.json`
- 安装信息：`/usr/local/etc/sing-box/install-info.json`
- `systemd` 服务：`/etc/systemd/system/sing-box.service`

## 依赖

脚本依赖：

- `python3`
- `openssl`
- `gpg`
- `systemctl`
- `ss`

Debian / Ubuntu 可以先安装：

```bash
sudo apt-get update
sudo apt-get install -y python3 openssl gpg iproute2
```

RHEL / CentOS 可以先安装：

```bash
sudo yum install -y python3 openssl gnupg2 iproute
```

## 用法

交互式一键安装：

```bash
sudo python3 singbox.py install
```

也可以一次性带参数：

```bash
sudo python3 singbox.py install \
  --license 5m8L0i3q-r4N5P2R7-w8K3E49I \
  --tuic-port 23456 \
  --ss-port 9090 \
  --warp-port 40000 \
  --tls-server-name bing.com
```

## 常用命令

更新 WARP license：

```bash
sudo python3 singbox.py set-license 5m8L0i3q-r4N5P2R7-w8K3E49I --reconnect
```

查看生成的服务端配置：

```bash
python3 singbox.py show-config
```

查看端口、凭据、本机 IP、WARP 状态：

```bash
python3 singbox.py show-info
```

只查看本机 IP：

```bash
python3 singbox.py show-ip
```

校验配置：

```bash
python3 singbox.py validate-config
sudo python3 singbox.py validate-config --strict
```

管理服务直接用 `systemctl`：

```bash
sudo systemctl enable sing-box.service
sudo systemctl restart sing-box.service
sudo systemctl status sing-box.service
```

查看或启用 BBR：

```bash
python3 singbox.py bbr status
sudo python3 singbox.py bbr enable
```

## 配置结构

生成的 [`config.json`](/Users/kk/Github/singbox/config.json) 是固定模板思路，不再做通用协议面板。核心结构是：

- `tuic-in -> warp`
- `ss-in -> warp`
- `warp -> 127.0.0.1:40000`

用户只需要关心：

- `WARP license`
- `TUIC` 端口
- `Shadowsocks` 端口
- `WARP` 本地代理端口

其他如 `UUID`、随机密码、自签证书和基础模板都由脚本自动生成。

## 说明

- 这是一个固定模板的一键安装器，目标是快速部署服务端
- 默认生成自签名 TLS 证书，默认证书名称使用 `bing.com`
- 生成的配置由脚本自动写入 `/usr/local/etc/sing-box/config.json`
