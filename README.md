# VPN/代理 IP 管理器（增强版，Windows 可直接使用）

这是一个轻量的命令行工具，用于管理代理节点，支持：

- 手动更新代理 IP
- 从 API 自动更新代理 IP
- 从订阅源（URL/本地 JSON）批量导入节点
- 节点健康检查（TCP）
- 自动切换到下一个健康节点
- 回滚到上一个活跃节点
- 查看当前活跃节点与历史节点
- 通过当前活跃代理发起真实 HTTP 请求测试（connect-test）
- 搜索网络公开代理并生成初始化 JSON（generate-init-json）
- 备份/修复/恢复网络配置（backup-network / repair-network / restore-network）
- 参考 ip1/ip2 云端脚本更新 IP 并生成 JSON（update-ip-json）

> 说明：本项目是“代理节点管理器”，不包含底层 VPN 隧道协议实现（如 WireGuard/OpenVPN 内核层功能）。

## Windows 快速使用

1. 安装 Python 3.9+（勾选“Add Python to PATH”）
2. 在项目目录双击或命令行运行 `vpn-manager.bat`（不带参数时进入状态面板）
3. 例如：

```bat
vpn-manager.bat manual-update --host 1.2.3.4 --port 1080 --protocol socks5 --region hk
vpn-manager.bat show-active
vpn-manager.bat list
```



## 运行状态面板（持续显示）

为了解决“窗口无法持续显示状态和操作提示”，现在支持交互控制台：

- 直接运行 `vpn-manager.bat`（不带参数）会进入状态面板。
- 或运行 `python vpn_proxy_manager.py console`。
- 面板会持续显示：当前活跃节点、节点总数、上次同步时间、节点健康状态。
- 面板会始终提供操作提示（1/2/3/.../q）。

```bat
vpn-manager.bat
```

```bat
python D:\vpn\vpn_proxy_manager.py console
```

## Windows 常见错误排查

如果你看到这个错误：

```text
python: can't open file 'D:\\vpn\\vpn_proxy_manager.py': [Errno 2] No such file or directory
```

说明你当前目录里没有 `vpn_proxy_manager.py` 文件。

推荐做法（避免路径问题）：

1. 把 `vpn-manager.bat` 和 `vpn_proxy_manager.py` 放在同一个目录。
2. 用 `vpn-manager.bat` 启动，而不是直接在任意目录运行 `python vpn_proxy_manager.py`。
3. 或者使用绝对路径：

```bat
python D:\vpn\vpn_proxy_manager.py show-active
```

`vpn-manager.bat` 已经做了路径修复，会自动定位到 bat 自己所在目录下的 `vpn_proxy_manager.py`。

## 通用命令（Windows / Linux / macOS）

```bash
python3 vpn_proxy_manager.py manual-update --host 1.2.3.4 --port 1080 --protocol socks5 --region hk
python3 vpn_proxy_manager.py auto-update --api-url http://127.0.0.1:8080/latest-proxy
python3 vpn_proxy_manager.py import-sub --source ./subscription.json
python3 vpn_proxy_manager.py health --host 1.2.3.4 --port 1080
python3 vpn_proxy_manager.py rotate
python3 vpn_proxy_manager.py rollback
python3 vpn_proxy_manager.py show-active
python3 vpn_proxy_manager.py list
python3 vpn_proxy_manager.py connect-test --url http://example.com
python3 vpn_proxy_manager.py generate-init-json --output ./initial_proxies.json --max-items 50
python3 vpn_proxy_manager.py backup-network --output ./network_snapshot.json
python3 vpn_proxy_manager.py repair-network
python3 vpn_proxy_manager.py restore-network --snapshot ./network_snapshot.json
python3 vpn_proxy_manager.py update-ip-json --ip-index 1 --output ./initial_proxies.json
```

## 自动更新 API 响应格式（单节点）

```json
{
  "host": "8.8.8.8",
  "port": 1080,
  "protocol": "socks5",
  "region": "sg"
}
```

## 订阅源 JSON 格式（多节点）

```json
[
  {"host": "8.8.8.8", "port": 1080, "protocol": "socks5", "region": "sg"},
  {"host": "1.1.1.1", "port": 8080, "protocol": "http", "region": "us"}
]
```

## 测试

```bash
python3 -m unittest -v
```


## 真正的网络代理连接逻辑说明

`connect-test` 会使用当前活跃代理作为上游代理，实际向目标 URL 发起 HTTP 请求。

- 当活跃节点协议是 `http/https` 时，会通过 `urllib` 的 `ProxyHandler` 走代理链路访问目标站点。
- 当活跃节点协议是 `socks5` 时，标准库默认不内置 SOCKS 支持，会提示需要额外依赖。

在 Windows 下可直接使用：

```bat
vpn-manager.bat connect-test --url http://example.com
```


## 初始化 JSON 说明

你可以运行下面命令来“搜索网络信息并生成初始化 JSON 文件”：

```bash
python3 vpn_proxy_manager.py generate-init-json --output ./initial_proxies.json --max-items 50
python3 vpn_proxy_manager.py backup-network --output ./network_snapshot.json
python3 vpn_proxy_manager.py repair-network
python3 vpn_proxy_manager.py restore-network --snapshot ./network_snapshot.json
python3 vpn_proxy_manager.py update-ip-json --ip-index 1 --output ./initial_proxies.json
```

说明：
- 优先从公开代理源抓取 `ip:port` 信息。
- 若当前网络受限，会自动回退读取系统环境变量中的代理（如 `HTTP_PROXY`/`HTTPS_PROXY`）来生成初始化文件。
- 输出文件是标准数组 JSON，可直接用于 `import-sub --source ./initial_proxies.json`。


## 网络修复与恢复

当程序运行后网络出现问题，可以使用以下流程恢复到运行前状态：

1. 先备份当前网络配置（代理环境快照）：

```bash
python3 vpn_proxy_manager.py backup-network --output ./network_snapshot.json
```

2. 执行修复网络：

```bash
python3 vpn_proxy_manager.py repair-network
```

3. 如需回到运行前配置，执行恢复：

```bash
python3 vpn_proxy_manager.py restore-network --snapshot ./network_snapshot.json
python3 vpn_proxy_manager.py update-ip-json --ip-index 1 --output ./initial_proxies.json
```

说明：
- 当前版本恢复的是代理相关环境变量（HTTP_PROXY/HTTPS_PROXY/NO_PROXY 等）。
- 在 Windows 下会额外给出 `netsh winhttp reset proxy` / `ipconfig /flushdns` 的修复提示。
- 交互面板中也可使用 10/11/12 号操作完成备份、修复、恢复。


## 参考 ip1/ip2 云端更新脚本

已按你提供的 bat 逻辑实现程序化命令：

```bash
python3 vpn_proxy_manager.py update-ip-json --ip-index 1 --output ./initial_proxies.json
python3 vpn_proxy_manager.py update-ip-json --ip-index 2 --output ./initial_proxies.json
```

逻辑说明：
- 优先尝试 `gitlabip.xyz` 地址；
- 失败后自动回退到 `gitlab.com/free9999/ipupdate`；
- 下载 `config.yaml` 后，提取其中 `server` + `port`，生成可导入的 JSON 数组。
