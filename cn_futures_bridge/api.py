"""启动阶段只开放状态和截图，不提供交易或任意桌面输入接口。"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
from urllib.parse import urlsplit

from .runtime import Runtime, RuntimeErrorCode


def create_server(runtime: Runtime) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def reply(self, code: int, payload: dict | bytes, content_type: str = "application/json"):
            body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlsplit(self.path).path
            # healthz 只证明 HTTP 服务存活，不暴露账号或终端状态。
            if path == "/healthz":
                self.reply(200, {"status": "ok"})
                return
            token = runtime.settings.api_token
            authorization = self.headers.get("Authorization", "")
            if token and not secrets.compare_digest(authorization.encode(), f"Bearer {token}".encode()):
                self.reply(401, {"error": {"code": "UNAUTHORIZED", "message": "需要有效的 Bearer token"}})
                return
            if path in ("/v1/status", "/readyz"):
                status = runtime.status()
                ready = status["state"] == "window_visible" and status["terminal_window_visible"]
                self.reply(503 if path == "/readyz" and not ready else 200, status)
            elif path == "/v1/desktop/screenshot":
                try:
                    self.reply(200, runtime.screenshot(), "image/png")
                except RuntimeErrorCode as exc:
                    self.reply(503, {"error": {"code": exc.code, "message": str(exc)}})
            else:
                self.reply(404, {"error": {"code": "NOT_FOUND", "message": "本阶段仅提供启动诊断接口"}})

        def do_POST(self):
            self.reply(405, {"error": {"code": "METHOD_NOT_ALLOWED", "message": "本阶段未开放写操作"}})

        def log_message(self, format, *args):
            # 不把路径、查询串或请求头写入日志，防止调用方误传凭证。
            pass

    server = ThreadingHTTPServer((runtime.settings.api_host, runtime.settings.api_port), Handler)
    server.daemon_threads = True
    return server
