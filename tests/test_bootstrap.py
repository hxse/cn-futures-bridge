from dataclasses import replace
import http.client
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from cn_futures_bridge.api import create_server
from cn_futures_bridge.config import ConfigError, load_settings
from cn_futures_bridge.runtime import Runtime, RuntimeErrorCode

ROOT = Path(__file__).resolve().parents[1]


class ConfigurationTests(unittest.TestCase):
    def parse(self, content):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(content)
            return load_settings(path)

    def test_empty_accounts_startup_configuration(self):
        settings = load_settings(ROOT / "config.example.toml")
        self.assertEqual(settings.environment, "simnow")
        self.assertEqual(settings.account.username, "")
        status = Runtime(settings).status()
        self.assertFalse(status["account_configured"])
        self.assertFalse(status["trading_ready"])
        self.assertEqual(status["login_state"], "unverified")

    def test_credentials_do_not_enable_login_or_appear_in_status(self):
        content = (ROOT / "config.example.toml").read_text()
        content = content.replace('username = ""', 'username = "private-user"')
        content = content.replace('password = ""', 'password = "private-secret"')
        settings = self.parse(content)
        status = Runtime(settings).status()
        self.assertTrue(status["account_configured"])
        self.assertFalse(status["automation_enabled"])
        self.assertFalse(status["trading_ready"])
        self.assertNotIn("private-user", repr(status) + repr(settings))
        self.assertNotIn("private-secret", repr(status) + repr(settings))

    def test_unsupported_environment_and_configuration_errors(self):
        content = (ROOT / "config.example.toml").read_text()
        cases = [
            (content.replace('environment = "simnow"', 'environment = "live"'), "只支持 simnow"),
            (content.replace("port = 8000", "port = true"), "api.port"),
            (content.replace("port = 8000", "port = 5900"), "互不相同"),
            (content.replace('data_dir = "/data"', 'data_dir = "."'), "绝对目录"),
            (content.replace('[account]', '[accounts]'), "五节"),
            (content + '\nauto_login = true\n', "未知字段"),
        ]
        for candidate, expected in cases:
            with self.subTest(expected=expected), self.assertRaisesRegex(ConfigError, expected):
                self.parse(candidate)

    def test_invalid_toml_does_not_echo_secret(self):
        with self.assertRaises(ConfigError) as error:
            self.parse('[account]\npassword = "private-secret')
        self.assertNotIn("private-secret", str(error.exception))


class ApiTests(unittest.TestCase):
    def setUp(self):
        settings = load_settings(ROOT / "config.example.toml")
        self.runtime = Runtime(replace(settings, api_host="127.0.0.1", api_port=0, api_token="local-secret"))
        self.server = create_server(self.runtime)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path, method="GET", authenticated=True):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        headers = {"Authorization": "Bearer local-secret"} if authenticated else {}
        try:
            connection.request(method, path, headers=headers)
            response = connection.getresponse()
            return response.status, response.read(), response.getheader("Cache-Control")
        finally:
            connection.close()

    def test_health_does_not_imply_desktop_ready(self):
        self.assertEqual(self.request("/healthz", authenticated=False)[0], 200)
        self.assertEqual(self.request("/readyz")[0], 503)
        code, body, cache = self.request("/v1/status")
        self.assertEqual(code, 200)
        self.assertIn(b'"trading_ready": false', body)
        self.assertEqual(cache, "no-store")

    def test_status_and_screenshot_require_token(self):
        for path in ("/readyz", "/v1/status", "/v1/desktop/screenshot"):
            with self.subTest(path=path):
                self.assertEqual(self.request(path, authenticated=False)[0], 401)

    def test_ready_only_reports_bootstrap(self):
        self.runtime.state = "window_visible"
        self.runtime.window_visible = True
        code, body, _ = self.request("/readyz")
        self.assertEqual(code, 200)
        self.assertIn(b'"login_state": "unverified"', body)
        self.assertIn(b'"trading_ready": false', body)
        self.runtime.state = "failed"
        self.assertEqual(self.request("/readyz")[0], 503)

    def test_no_trading_placeholder(self):
        self.assertEqual(self.request("/v1/orders")[0], 404)
        code, body, _ = self.request("/v1/orders", method="POST")
        self.assertEqual(code, 405)
        self.assertIn(b"METHOD_NOT_ALLOWED", body)


class RuntimeFilesTests(unittest.TestCase):
    def test_copy_then_reuse_and_reject_modified_vendor_file(self):
        settings = load_settings(ROOT / "config.example.toml")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seed = root / "seed"
            data = root / "data"
            seed.mkdir()
            data.mkdir()
            # 这里只测试文件完整性，不把 fixture 当作可运行的快期客户端。
            (seed / "asset.bin").write_bytes(b"locked-content")
            manifest = {"files": {"asset.bin": hashlib.sha256(b"locked-content").hexdigest()}}
            (seed / "bridge-manifest.json").write_text(json.dumps(manifest))
            runtime = Runtime(replace(settings, data_dir=data))
            target = runtime._prepare_files(seed)
            (target / "user-state.txt").write_text("keep")
            self.assertEqual(runtime._prepare_files(seed), target)
            self.assertEqual((target / "user-state.txt").read_text(), "keep")
            (target / "asset.bin").write_bytes(b"changed")
            with self.assertRaises(RuntimeErrorCode) as error:
                runtime._prepare_files(seed)
            self.assertEqual(error.exception.code, "TERMINAL_CHANGED")
            self.assertEqual((target / "asset.bin").read_bytes(), b"changed")

    def test_data_directory_has_one_owner_and_can_be_released(self):
        settings = load_settings(ROOT / "config.example.toml")
        with tempfile.TemporaryDirectory() as temporary, patch.object(Runtime, "_run"):
            settings = replace(settings, data_dir=Path(temporary))
            first, second = Runtime(settings), Runtime(settings)
            first.start()
            try:
                with self.assertRaises(RuntimeErrorCode) as error:
                    second.start()
                self.assertEqual(error.exception.code, "SESSION_IN_USE")
            finally:
                first.stop()
            second.start()
            second.stop()


if __name__ == "__main__":
    unittest.main()
