import json
import os
import tempfile
import unittest
from pathlib import Path

from unittest import mock

from vpn_proxy_manager import ClashIPUpdater, NetworkBootstrapper, NetworkRepairManager, ProxyConnector, ProxyEndpoint, ProxyHealthChecker, ProxyStore, ProxyUpdater, build_status_text


class ProxyManagerTests(unittest.TestCase):
    def test_manual_update_sets_active(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "db.json"
            store = ProxyStore(path)
            updater = ProxyUpdater(store)

            ep = updater.manual_update("1.2.3.4", 1080, "socks5", "hk")
            self.assertEqual(ep.host, "1.2.3.4")

            reloaded = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(reloaded["active"]["host"], "1.2.3.4")

    def test_import_subscription_from_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "db.json"
            sub_path = Path(tmp) / "sub.json"
            sub_path.write_text(
                json.dumps([
                    {"host": "8.8.8.8", "port": 1080, "protocol": "socks5", "region": "sg"},
                    {"host": "1.1.1.1", "port": 8080, "protocol": "http", "region": "us"},
                ]),
                encoding="utf-8",
            )

            store = ProxyStore(db_path)
            updater = ProxyUpdater(store)
            count = updater.import_subscription(str(sub_path))

            self.assertEqual(count, 2)
            self.assertEqual(len(store.list_endpoints()), 2)

    def test_rollback(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "db.json"
            store = ProxyStore(db_path)
            updater = ProxyUpdater(store)

            updater.manual_update("1.1.1.1", 1080, "socks5", "sg")
            updater.manual_update("2.2.2.2", 1080, "socks5", "jp")

            rolled = store.rollback()
            self.assertIsNotNone(rolled)
            self.assertEqual(store.active().host, "1.1.1.1")

    def test_status_text_contains_operation_hints(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "db.json"
            store = ProxyStore(db_path)
            updater = ProxyUpdater(store)
            updater.manual_update("3.3.3.3", 1080, "socks5", "test")
            text = build_status_text(store, ProxyHealthChecker())
            self.assertIn("可用操作", text)
            self.assertIn("手动更新节点", text)
            self.assertIn("当前活跃", text)


    def test_connect_via_http_proxy_success(self):
        endpoint = ProxyEndpoint(host="127.0.0.1", port=8080, protocol="http", region="test")
        with mock.patch("urllib.request.build_opener", return_value=_FakeOpener()):
            result = ProxyConnector.fetch_via_proxy(endpoint, "http://example.com")
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], 200)


    def test_generate_init_json_from_env_proxy(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "initial.json"
            with mock.patch.dict("os.environ", {"HTTP_PROXY": "http://10.0.0.10:8080"}, clear=False):
                endpoints = NetworkBootstrapper.fetch_public_proxies(max_items=10)
            self.assertTrue(any(ep.host == "10.0.0.10" and ep.port == 8080 for ep in endpoints))
            NetworkBootstrapper.write_init_json(output, endpoints)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertIsInstance(payload, list)
            self.assertGreaterEqual(len(payload), 1)


    def test_network_repair_backup_and_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            snap_path = Path(tmp) / "network_snapshot.json"
            mgr = NetworkRepairManager()

            with mock.patch.dict("os.environ", {"HTTP_PROXY": "http://1.2.3.4:8080", "NO_PROXY": "localhost"}, clear=False):
                snapshot = mgr.capture_snapshot()
                mgr.save_snapshot(snap_path, snapshot)
                mgr.repair_network()
                self.assertEqual("", os.environ.get("HTTP_PROXY", ""))
                loaded = mgr.load_snapshot(snap_path)
                mgr.restore_from_snapshot(loaded)
                self.assertEqual(os.environ.get("HTTP_PROXY"), "http://1.2.3.4:8080")


    def test_extract_endpoints_from_clash_yaml(self):
        yaml_text = """
proxies:
  - name: node1
    type: vmess
    server: 1.2.3.4
    port: 443
  - name: node2
    type: trojan
    server: example.com
    port: 8443
"""
        endpoints = ClashIPUpdater.extract_endpoints_from_yaml(yaml_text)
        self.assertEqual(len(endpoints), 2)
        self.assertEqual(endpoints[0].host, "1.2.3.4")
        self.assertEqual(endpoints[1].port, 8443)

    def test_update_ip_json_with_mocked_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "init.json"
            mock_payload = {
                "ok": True,
                "url": "https://example.com/config.yaml",
                "content": "server: 9.9.9.9\nport: 8080\n",
            }
            with mock.patch.object(ClashIPUpdater, "download_config", return_value=mock_payload):
                result = ClashIPUpdater.update_ip_to_json(index=1, output_path=output)

            self.assertTrue(result["ok"])
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload[0]["host"], "9.9.9.9")

    def test_connect_via_socks_proxy_not_supported_without_deps(self):
        endpoint = ProxyEndpoint(host="127.0.0.1", port=1080, protocol="socks5", region="test")
        result = ProxyConnector.fetch_via_proxy(endpoint, "http://example.com")
        self.assertFalse(result["ok"])
        self.assertIn("HTTP/HTTPS", result["error"])


class _FakeResponse:
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def read(self, _n: int = -1) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeOpener:
    def open(self, _req, timeout=0):
        return _FakeResponse(b"ok-through-proxy", 200)


if __name__ == "__main__":
    unittest.main()
