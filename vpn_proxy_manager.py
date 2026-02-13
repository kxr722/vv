#!/usr/bin/env python3
"""VPN/Proxy endpoint manager with Windows-friendly interactive console."""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
import urllib.parse
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

DB_PATH = Path("proxy_store.json")
DEFAULT_INIT_JSON_PATH = Path("initial_proxies.json")
NETWORK_SNAPSHOT_PATH = Path("network_snapshot.json")


@dataclass
class ProxyEndpoint:
    host: str
    port: int
    protocol: str = "socks5"
    region: str = "unknown"
    updated_at: float = 0.0
    source: str = "manual"

    @property
    def url(self) -> str:
        return f"{self.protocol}://{self.host}:{self.port}"


class ProxyStore:
    def __init__(self, path: Path = DB_PATH) -> None:
        self.path = path
        self._data: Dict[str, Any] = {
            "active": None,
            "previous_active": None,
            "endpoints": [],
            "last_sync": 0,
        }
        self.load()

    def load(self) -> None:
        if self.path.exists():
            self._data = json.loads(self.path.read_text(encoding="utf-8"))

    def save(self) -> None:
        self.path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")

    def list_endpoints(self) -> List[ProxyEndpoint]:
        return [ProxyEndpoint(**item) for item in self._data.get("endpoints", [])]

    def upsert(self, endpoint: ProxyEndpoint) -> None:
        endpoints = self._data["endpoints"]
        for i, item in enumerate(endpoints):
            if (
                item["host"] == endpoint.host
                and int(item["port"]) == endpoint.port
                and item.get("protocol", "socks5") == endpoint.protocol
            ):
                endpoints[i] = asdict(endpoint)
                return
        endpoints.append(asdict(endpoint))

    def set_active(self, endpoint: ProxyEndpoint) -> None:
        current = self._data.get("active")
        if current:
            self._data["previous_active"] = current
        self.upsert(endpoint)
        self._data["active"] = asdict(endpoint)

    def active(self) -> Optional[ProxyEndpoint]:
        active = self._data.get("active")
        return ProxyEndpoint(**active) if active else None

    def previous_active(self) -> Optional[ProxyEndpoint]:
        prev = self._data.get("previous_active")
        return ProxyEndpoint(**prev) if prev else None

    def rollback(self) -> Optional[ProxyEndpoint]:
        prev = self.previous_active()
        if not prev:
            return None
        curr = self._data.get("active")
        self._data["active"] = asdict(prev)
        self._data["previous_active"] = curr
        self.upsert(prev)
        self.save()
        return prev

    def mark_sync(self) -> None:
        self._data["last_sync"] = time.time()

    def last_sync(self) -> float:
        return float(self._data.get("last_sync", 0) or 0)


class ProxyUpdater:
    def __init__(self, store: ProxyStore) -> None:
        self.store = store

    def manual_update(self, host: str, port: int, protocol: str, region: str) -> ProxyEndpoint:
        endpoint = ProxyEndpoint(
            host=host,
            port=port,
            protocol=protocol,
            region=region,
            updated_at=time.time(),
            source="manual",
        )
        self.store.set_active(endpoint)
        self.store.save()
        return endpoint

    def fetch_from_api(self, api_url: str, timeout: float = 5.0) -> ProxyEndpoint:
        req = urllib.request.Request(api_url, headers={"User-Agent": "vpn-proxy-manager/3.0"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                payload = json.loads(res.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as err:
            raise RuntimeError(f"自动更新失败: {err}") from err

        endpoint = self._parse_payload(payload, source=f"api:{api_url}")
        self.store.set_active(endpoint)
        self.store.mark_sync()
        self.store.save()
        return endpoint

    def import_subscription(self, source: str) -> int:
        payload = self._load_json(source)
        if not isinstance(payload, list):
            raise RuntimeError("订阅源必须是 JSON 数组")

        count = 0
        for item in payload:
            endpoint = self._parse_payload(item, source=source)
            self.store.upsert(endpoint)
            count += 1

        self.store.mark_sync()
        self.store.save()
        return count

    def _load_json(self, source: str) -> Any:
        if source.startswith("http://") or source.startswith("https://"):
            req = urllib.request.Request(source, headers={"User-Agent": "vpn-proxy-manager/3.0"})
            with urllib.request.urlopen(req, timeout=8.0) as res:
                return json.loads(res.read().decode("utf-8"))
        return json.loads(Path(source).read_text(encoding="utf-8"))

    @staticmethod
    def _parse_payload(payload: Dict[str, Any], source: str) -> ProxyEndpoint:
        required = ["host", "port"]
        if not all(k in payload for k in required):
            raise RuntimeError(f"节点缺少必填字段: {required}")
        return ProxyEndpoint(
            host=str(payload["host"]),
            port=int(payload["port"]),
            protocol=str(payload.get("protocol", "socks5")),
            region=str(payload.get("region", "unknown")),
            updated_at=time.time(),
            source=source,
        )


class ProxyHealthChecker:
    @staticmethod
    def check(endpoint: ProxyEndpoint, timeout: float = 1.5) -> bool:
        try:
            with socket.create_connection((endpoint.host, endpoint.port), timeout=timeout):
                return True
        except OSError:
            return False




class ProxyConnector:
    """Execute real HTTP requests through the configured proxy endpoint."""

    @staticmethod
    def fetch_via_proxy(endpoint: ProxyEndpoint, url: str, timeout: float = 8.0) -> Dict[str, Any]:
        proxy_url = endpoint.url

        # urllib supports HTTP/HTTPS proxying directly; SOCKS may require extra deps.
        if endpoint.protocol.lower() in {"http", "https"}:
            handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
            opener = urllib.request.build_opener(handler)
            req = urllib.request.Request(url, headers={"User-Agent": "vpn-proxy-manager/4.0"})
            try:
                with opener.open(req, timeout=timeout) as res:
                    body = res.read(512).decode("utf-8", errors="ignore")
                    return {
                        "ok": True,
                        "status": int(getattr(res, "status", 200)),
                        "url": url,
                        "proxy": proxy_url,
                        "preview": body[:200],
                    }
            except Exception as err:  # noqa: BLE001
                return {
                    "ok": False,
                    "status": 0,
                    "url": url,
                    "proxy": proxy_url,
                    "error": str(err),
                }

        return {
            "ok": False,
            "status": 0,
            "url": url,
            "proxy": proxy_url,
            "error": "当前仅内置支持 HTTP/HTTPS 代理连接测试；SOCKS 需额外依赖。",
        }


class NetworkBootstrapper:
    """Fetch public proxy information from web sources and generate init JSON."""

    SOURCES = [
        "https://api.proxyscrape.com/v4/free-proxy-list/get?request=get_proxies&protocol=http&proxy_format=ipport&format=text&timeout=4000",
        "https://api.openproxylist.xyz/http.txt",
    ]

    @staticmethod
    def _parse_ip_port_lines(text: str) -> List[ProxyEndpoint]:
        endpoints: List[ProxyEndpoint] = []
        for raw in text.splitlines():
            line = raw.strip()
            if not line or ":" not in line:
                continue
            host, port_text = line.rsplit(":", 1)
            try:
                port = int(port_text)
            except ValueError:
                continue
            endpoints.append(
                ProxyEndpoint(
                    host=host,
                    port=port,
                    protocol="http",
                    region="unknown",
                    updated_at=time.time(),
                    source="web-search",
                )
            )
        return endpoints


    @staticmethod
    def _extract_proxy_from_env() -> List[ProxyEndpoint]:
        endpoints: List[ProxyEndpoint] = []
        for key in ["HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"]:
            val = os.environ.get(key, "").strip()
            if not val:
                continue
            parsed = urllib.parse.urlparse(val)
            host = parsed.hostname
            port = parsed.port
            if not host or not port:
                continue
            protocol = "http" if (parsed.scheme or "http").startswith("http") else "https"
            endpoints.append(
                ProxyEndpoint(
                    host=host,
                    port=port,
                    protocol=protocol,
                    region="local-env",
                    updated_at=time.time(),
                    source="env-proxy",
                )
            )
        return endpoints

    @classmethod
    def fetch_public_proxies(cls, max_items: int = 50) -> List[ProxyEndpoint]:
        results: List[ProxyEndpoint] = []
        seen = set()

        # 1) search public proxy lists on network
        for source in cls.SOURCES:
            req = urllib.request.Request(source, headers={"User-Agent": "vpn-proxy-manager/4.1"})
            try:
                with urllib.request.urlopen(req, timeout=10.0) as res:
                    body = res.read().decode("utf-8", errors="ignore")
            except Exception:
                continue

            for ep in cls._parse_ip_port_lines(body):
                key = (ep.host, ep.port, ep.protocol)
                if key in seen:
                    continue
                seen.add(key)
                results.append(ep)
                if len(results) >= max_items:
                    return results

        # 2) fallback to environment proxy info when outbound web is restricted
        for ep in cls._extract_proxy_from_env():
            key = (ep.host, ep.port, ep.protocol)
            if key in seen:
                continue
            seen.add(key)
            results.append(ep)
            if len(results) >= max_items:
                break

        return results

    @staticmethod
    def write_init_json(path: Path, endpoints: List[ProxyEndpoint]) -> None:
        payload = [asdict(ep) for ep in endpoints]
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class ClashIPUpdater:
    """Update IP by downloading clash config.yaml from mirror URLs and generate JSON."""

    URL_TEMPLATES = [
        "https://www.gitlabip.xyz/Alvin9999/PAC/refs/heads/master/backup/img/1/2/ipp/clash.meta2/{index}/config.yaml",
        "https://gitlab.com/free9999/ipupdate/-/raw/master/backup/img/1/2/ipp/clash.meta2/{index}/config.yaml",
    ]

    @classmethod
    def download_config(cls, index: int, timeout: float = 10.0) -> Dict[str, Any]:
        errors: List[str] = []
        for t in cls.URL_TEMPLATES:
            url = t.format(index=index)
            req = urllib.request.Request(url, headers={"User-Agent": "vpn-proxy-manager/5.0"})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as res:
                    text = res.read().decode("utf-8", errors="ignore")
                    return {"ok": True, "url": url, "content": text}
            except Exception as err:  # noqa: BLE001
                errors.append(f"{url} -> {err}")
        return {"ok": False, "errors": errors}

    @staticmethod
    def extract_endpoints_from_yaml(yaml_text: str) -> List[ProxyEndpoint]:
        endpoints: List[ProxyEndpoint] = []
        seen = set()

        server_pattern = re.compile(r"^\s*server\s*:\s*([\w\.-]+)\s*$")
        port_pattern = re.compile(r"^\s*port\s*:\s*(\d{1,5})\s*$")

        current_server: Optional[str] = None
        for line in yaml_text.splitlines():
            m_server = server_pattern.match(line)
            if m_server:
                current_server = m_server.group(1).strip()
                continue

            m_port = port_pattern.match(line)
            if m_port and current_server:
                port = int(m_port.group(1))
                key = (current_server, port)
                if key in seen:
                    current_server = None
                    continue
                seen.add(key)
                endpoints.append(
                    ProxyEndpoint(
                        host=current_server,
                        port=port,
                        protocol="http",
                        region="unknown",
                        updated_at=time.time(),
                        source="clash-cloud-update",
                    )
                )
                current_server = None

        return endpoints

    @classmethod
    def update_ip_to_json(cls, index: int, output_path: Path) -> Dict[str, Any]:
        result = cls.download_config(index=index)
        if not result.get("ok"):
            return {"ok": False, "errors": result.get("errors", [])}

        endpoints = cls.extract_endpoints_from_yaml(result["content"])
        NetworkBootstrapper.write_init_json(output_path, endpoints)
        return {"ok": True, "count": len(endpoints), "source_url": result["url"], "output": str(output_path)}


class NetworkRepairManager:
    """Backup/repair/restore runtime network proxy settings."""

    PROXY_ENV_KEYS = ["HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy"]

    @classmethod
    def capture_snapshot(cls) -> Dict[str, Any]:
        env = {k: os.environ.get(k, "") for k in cls.PROXY_ENV_KEYS}
        return {"timestamp": time.time(), "proxy_env": env}

    @staticmethod
    def save_snapshot(path: Path, snapshot: Dict[str, Any]) -> None:
        path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def load_snapshot(path: Path) -> Dict[str, Any]:
        if not path.exists():
            raise RuntimeError(f"快照文件不存在: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    @classmethod
    def repair_network(cls) -> Dict[str, Any]:
        for key in cls.PROXY_ENV_KEYS:
            os.environ.pop(key, None)

        actions = ["已清理当前进程代理环境变量"]
        if os.name == "nt":
            actions.append("可选: netsh winhttp reset proxy")
            actions.append("可选: ipconfig /flushdns")
        else:
            actions.append("可选: 重启网络服务或刷新DNS缓存")
        return {"ok": True, "actions": actions}

    @classmethod
    def restore_from_snapshot(cls, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        env = snapshot.get("proxy_env", {})
        for key in cls.PROXY_ENV_KEYS:
            val = env.get(key, "")
            if val:
                os.environ[key] = val
            else:
                os.environ.pop(key, None)
        return {"ok": True, "restored_keys": list(cls.PROXY_ENV_KEYS)}


class ProxyRotator:
    def __init__(self, store: ProxyStore, checker: ProxyHealthChecker) -> None:
        self.store = store
        self.checker = checker

    def rotate_to_next_healthy(self) -> Optional[ProxyEndpoint]:
        active = self.store.active()
        endpoints = self.store.list_endpoints()
        if not endpoints:
            return None

        start_idx = 0
        if active:
            for i, ep in enumerate(endpoints):
                if ep.host == active.host and ep.port == active.port and ep.protocol == active.protocol:
                    start_idx = (i + 1) % len(endpoints)
                    break

        for offset in range(len(endpoints)):
            candidate = endpoints[(start_idx + offset) % len(endpoints)]
            if self.checker.check(candidate):
                candidate.updated_at = time.time()
                candidate.source = "rotation"
                self.store.set_active(candidate)
                self.store.save()
                return candidate
        return None


def format_ts(ts: float) -> str:
    if not ts:
        return "never"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def build_status_text(store: ProxyStore, checker: ProxyHealthChecker) -> str:
    store.load()
    active = store.active()
    lines = [
        "=== VPN/代理管理器 运行面板 ===",
        f"时间: {format_ts(time.time())}",
        f"节点总数: {len(store.list_endpoints())}",
        f"上次同步: {format_ts(store.last_sync())}",
    ]
    if not active:
        lines.append("当前状态: 无活跃代理")
    else:
        health = "在线" if checker.check(active) else "离线"
        lines.append(f"当前活跃: {active.url} [{active.region}] 来源={active.source} 健康={health}")

    lines.extend(
        [
            "",
            "可用操作:",
            "  1) 手动更新节点",
            "  2) API 自动更新节点",
            "  3) 导入订阅节点(JSON文件或URL)",
            "  4) 轮换到下一个健康节点",
            "  5) 回滚到上一个节点",
            "  6) 显示节点列表",
            "  7) 刷新状态",
            "  8) 通过当前活跃代理做HTTP连接测试",
            "  9) 搜索网络公开代理并生成初始化JSON",
            " 10) 备份当前网络配置快照",
            " 11) 修复网络(清理代理环境)",
            " 12) 恢复运行前网络快照",
            " 13) 云端更新IP并生成JSON(参考ip1/ip2脚本)",
            "  q) 退出",
        ]
    )
    return "\n".join(lines)


def clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def run_console(store: ProxyStore, updater: ProxyUpdater, checker: ProxyHealthChecker, rotator: ProxyRotator, connector: ProxyConnector, repair_manager: NetworkRepairManager) -> int:
    while True:
        clear_screen()
        print(build_status_text(store, checker))
        choice = input("\n请输入操作编号: ").strip().lower()

        try:
            if choice == "1":
                host = input("host: ").strip()
                port = int(input("port: ").strip())
                protocol = input("protocol[socks5]: ").strip() or "socks5"
                region = input("region[unknown]: ").strip() or "unknown"
                ep = updater.manual_update(host, port, protocol, region)
                print(f"\n已更新: {ep.url} ({ep.region})")
            elif choice == "2":
                api_url = input("api-url: ").strip()
                ep = updater.fetch_from_api(api_url)
                print(f"\n自动更新成功: {ep.url} ({ep.region})")
            elif choice == "3":
                source = input("source(URL或本地JSON路径): ").strip()
                n = updater.import_subscription(source)
                print(f"\n导入完成: {n} 个节点")
            elif choice == "4":
                ep = rotator.rotate_to_next_healthy()
                print(f"\n已切换到: {ep.url} ({ep.region})" if ep else "\n未找到可用健康节点")
            elif choice == "5":
                ep = store.rollback()
                print(f"\n已回滚到: {ep.url} ({ep.region})" if ep else "\n无可回滚节点")
            elif choice == "6":
                eps = store.list_endpoints()
                print("\n--- 节点列表 ---")
                if not eps:
                    print("暂无节点")
                for i, e in enumerate(eps, 1):
                    print(f"{i}. {e.url} [{e.region}] 来源={e.source} 更新时间={format_ts(e.updated_at)}")
            elif choice == "7":
                print("\n状态已刷新")
            elif choice == "8":
                url = input("test-url[http://example.com]: ").strip() or "http://example.com"
                active = store.active()
                if not active:
                    print("\n当前无活跃代理")
                else:
                    result = connector.fetch_via_proxy(active, url)
                    if result.get("ok"):
                        print(f"\n代理连接成功: status={result['status']} proxy={result['proxy']}")
                    else:
                        print(f"\n代理连接失败: {result.get('error','unknown')}")
            elif choice == "9":
                output = input("output-json[initial_proxies.json]: ").strip() or str(DEFAULT_INIT_JSON_PATH)
                endpoints = NetworkBootstrapper.fetch_public_proxies(max_items=50)
                if not endpoints:
                    print("\n未从网络检索到可用代理数据")
                else:
                    NetworkBootstrapper.write_init_json(Path(output), endpoints)
                    print(f"\n已生成初始化JSON: {output} (共 {len(endpoints)} 条)")
            elif choice == "10":
                output = input("snapshot-path[network_snapshot.json]: ").strip() or str(NETWORK_SNAPSHOT_PATH)
                snap = repair_manager.capture_snapshot()
                repair_manager.save_snapshot(Path(output), snap)
                print(f"\n网络快照已保存: {output}")
            elif choice == "11":
                result = repair_manager.repair_network()
                print("\n网络修复已执行:")
                for a in result["actions"]:
                    print(f"- {a}")
            elif choice == "12":
                source = input("snapshot-path[network_snapshot.json]: ").strip() or str(NETWORK_SNAPSHOT_PATH)
                snap = repair_manager.load_snapshot(Path(source))
                repair_manager.restore_from_snapshot(snap)
                print(f"\n已从快照恢复网络配置: {source}")
            elif choice == "13":
                idx_text = input("ip-index[1]: ").strip() or "1"
                output = input("output-json[initial_proxies.json]: ").strip() or str(DEFAULT_INIT_JSON_PATH)
                res = ClashIPUpdater.update_ip_to_json(index=int(idx_text), output_path=Path(output))
                if res.get("ok"):
                    print(f"\n更新成功: {res['output']} (节点 {res['count']} 条)")
                    print(f"来源: {res['source_url']}")
                else:
                    print("\n更新失败，尝试源如下:")
                    for e in res.get("errors", []):
                        print(f"- {e}")
            elif choice == "q":
                return 0
            else:
                print("\n无效输入，请按提示输入")
        except Exception as err:  # noqa: BLE001
            print(f"\n操作失败: {err}")

        input("\n按 Enter 继续...")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="VPN/代理 IP 管理器（Windows可运行）")
    sub = parser.add_subparsers(dest="cmd", required=False)

    manual = sub.add_parser("manual-update", help="手动更新代理 IP")
    manual.add_argument("--host", required=True)
    manual.add_argument("--port", required=True, type=int)
    manual.add_argument("--protocol", default="socks5")
    manual.add_argument("--region", default="unknown")

    auto = sub.add_parser("auto-update", help="从 API 自动更新代理 IP")
    auto.add_argument("--api-url", required=True)

    imp = sub.add_parser("import-sub", help="导入订阅节点（URL或本地JSON文件）")
    imp.add_argument("--source", required=True)

    health = sub.add_parser("health", help="检查指定节点是否可连通")
    health.add_argument("--host", required=True)
    health.add_argument("--port", required=True, type=int)
    health.add_argument("--protocol", default="socks5")
    health.add_argument("--region", default="adhoc")

    sub.add_parser("rotate", help="自动切换到下一个健康节点")
    sub.add_parser("rollback", help="回滚到上一个活跃节点")
    sub.add_parser("show-active", help="显示当前活跃代理")
    sub.add_parser("list", help="显示历史代理列表")
    sub.add_parser("console", help="进入交互控制台（持续显示状态）")

    connect_test = sub.add_parser("connect-test", help="通过当前活跃代理发起真实HTTP请求")
    connect_test.add_argument("--url", default="http://example.com")

    init_json = sub.add_parser("generate-init-json", help="搜索网络公开代理并生成初始化JSON文件")
    init_json.add_argument("--output", default=str(DEFAULT_INIT_JSON_PATH))
    init_json.add_argument("--max-items", type=int, default=50)

    backup_net = sub.add_parser("backup-network", help="备份当前网络代理环境快照")
    backup_net.add_argument("--output", default=str(NETWORK_SNAPSHOT_PATH))

    repair_net = sub.add_parser("repair-network", help="修复网络(清理当前代理环境)")

    restore_net = sub.add_parser("restore-network", help="从快照恢复运行前网络")
    restore_net.add_argument("--snapshot", default=str(NETWORK_SNAPSHOT_PATH))

    cloud_update = sub.add_parser("update-ip-json", help="参考ip1/ip2脚本，从云端更新IP并生成JSON")
    cloud_update.add_argument("--ip-index", type=int, default=1, choices=[1, 2])
    cloud_update.add_argument("--output", default=str(DEFAULT_INIT_JSON_PATH))

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    store = ProxyStore()
    updater = ProxyUpdater(store)
    checker = ProxyHealthChecker()
    rotator = ProxyRotator(store, checker)
    connector = ProxyConnector()
    repair_manager = NetworkRepairManager()

    if not args.cmd or args.cmd == "console":
        return run_console(store, updater, checker, rotator, connector, repair_manager)

    if args.cmd == "manual-update":
        endpoint = updater.manual_update(args.host, args.port, args.protocol, args.region)
        print(f"手动更新成功: {endpoint.url} ({endpoint.region})")
        return 0

    if args.cmd == "auto-update":
        endpoint = updater.fetch_from_api(args.api_url)
        print(f"自动更新成功: {endpoint.url} ({endpoint.region})")
        return 0

    if args.cmd == "import-sub":
        count = updater.import_subscription(args.source)
        print(f"订阅导入完成: {count} 个节点")
        return 0

    if args.cmd == "health":
        endpoint = ProxyEndpoint(args.host, args.port, args.protocol, args.region)
        ok = checker.check(endpoint)
        print(f"健康检查 {'通过' if ok else '失败'}: {endpoint.url}")
        return 0 if ok else 2

    if args.cmd == "rotate":
        endpoint = rotator.rotate_to_next_healthy()
        if not endpoint:
            print("未找到可用健康节点")
            return 3
        print(f"已切换到健康节点: {endpoint.url} ({endpoint.region})")
        return 0

    if args.cmd == "rollback":
        endpoint = store.rollback()
        if not endpoint:
            print("无可回滚节点")
            return 4
        print(f"已回滚到: {endpoint.url} ({endpoint.region})")
        return 0

    if args.cmd == "show-active":
        active = store.active()
        if not active:
            print("当前无活跃代理")
            return 1
        print(f"当前活跃代理: {active.url} ({active.region}) 来源={active.source}")
        return 0

    if args.cmd == "list":
        eps = store.list_endpoints()
        if not eps:
            print("暂无代理历史")
            return 0
        for i, e in enumerate(eps, start=1):
            print(f"{i}. {e.url} [{e.region}] 来源={e.source} 更新时间={int(e.updated_at)}")
        return 0


    if args.cmd == "connect-test":
        active = store.active()
        if not active:
            print("当前无活跃代理，无法连接测试")
            return 5
        result = connector.fetch_via_proxy(active, args.url)
        if result.get("ok"):
            print(f"代理连接成功: proxy={result['proxy']} url={result['url']} status={result['status']}")
            if result.get("preview"):
                print(f"响应预览: {result['preview']}")
            return 0
        print(f"代理连接失败: proxy={result['proxy']} url={result['url']} error={result.get('error','unknown')}")
        return 6

    if args.cmd == "generate-init-json":
        endpoints = NetworkBootstrapper.fetch_public_proxies(max_items=args.max_items)
        output_path = Path(args.output)
        NetworkBootstrapper.write_init_json(output_path, endpoints)
        if endpoints:
            print(f"初始化JSON已生成: {output_path} (共 {len(endpoints)} 条)")
            return 0
        print(f"已生成空初始化JSON: {output_path} (当前网络受限且环境变量无可用代理)")
        return 0

    if args.cmd == "backup-network":
        output_path = Path(args.output)
        snapshot = repair_manager.capture_snapshot()
        repair_manager.save_snapshot(output_path, snapshot)
        print(f"网络快照已保存: {output_path}")
        return 0

    if args.cmd == "repair-network":
        result = repair_manager.repair_network()
        print("网络修复已执行:")
        for action in result["actions"]:
            print(f"- {action}")
        return 0

    if args.cmd == "restore-network":
        snapshot = repair_manager.load_snapshot(Path(args.snapshot))
        repair_manager.restore_from_snapshot(snapshot)
        print(f"网络已恢复: {args.snapshot}")
        return 0

    if args.cmd == "update-ip-json":
        result = ClashIPUpdater.update_ip_to_json(index=args.ip_index, output_path=Path(args.output))
        if result.get("ok"):
            print(f"IP更新成功，JSON已生成: {result['output']} (节点 {result['count']} 条)")
            print(f"来源: {result['source_url']}")
            return 0
        print("IP更新失败，请试试其它ip更新")
        for err in result.get("errors", []):
            print(f"- {err}")
        return 8

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
