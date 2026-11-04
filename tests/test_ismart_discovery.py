"""客户端探测/启动相关测试：端口探测、可执行文件定位、带参数启动、错误信息可读性。"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from core.platforms import ismart
from core.platforms.ismart import (
    ISmartClient,
    ISmartProfile,
    find_cdp_port,
    find_client_executable,
    launch_client,
    probe_cdp_port,
)


class _CDPHandler(BaseHTTPRequestHandler):
    browser = "Chrome/120"

    def do_GET(self):
        if self.path == "/json/version":
            body = json.dumps(
                {"Browser": self.browser, "webSocketDebuggerUrl": "ws://127.0.0.1/devtools/browser/x"}
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


class _PlainHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"hello"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _serve(handler):
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def test_probe_detects_chromium_endpoint():
    server = _serve(_CDPHandler)
    try:
        info = probe_cdp_port(server.server_address[1], timeout=2)
        assert info is not None
        assert "Browser" in info
    finally:
        server.shutdown()
        server.server_close()


def test_probe_ignores_non_cdp_server():
    server = _serve(_PlainHandler)
    try:
        assert probe_cdp_port(server.server_address[1], timeout=2) is None
    finally:
        server.shutdown()
        server.server_close()


def test_probe_ignores_closed_port():
    assert probe_cdp_port(1, timeout=0.3) is None


def test_find_cdp_port_returns_first_live_port():
    server = _serve(_CDPHandler)
    live = server.server_address[1]
    try:
        assert find_cdp_port((1, live), timeout=0.3) == live
        assert find_cdp_port((1,), timeout=0.2) is None
    finally:
        server.shutdown()
        server.server_close()


def test_find_client_executable_from_registry_candidate(tmp_path, monkeypatch):
    exe = tmp_path / "iSmart.exe"
    exe.write_text("stub", encoding="utf-8")
    monkeypatch.setattr(ismart, "_registry_candidates", lambda: iter([str(exe)]))
    monkeypatch.setattr(ismart, "CLIENT_SEARCH_PATTERNS", ())
    assert find_client_executable() == str(exe)


def test_find_client_executable_returns_none_when_absent(monkeypatch):
    monkeypatch.setattr(ismart, "_registry_candidates", lambda: iter(()))
    monkeypatch.setattr(ismart, "CLIENT_SEARCH_PATTERNS", ())
    assert find_client_executable() is None


def test_launch_client_passes_debug_flag(tmp_path, monkeypatch):
    exe = tmp_path / "iSmart.exe"
    exe.write_text("stub", encoding="utf-8")
    captured = {}
    monkeypatch.setattr(
        ismart.subprocess, "Popen", lambda args, **kwargs: captured.setdefault("args", args)
    )
    monkeypatch.setattr(ismart, "probe_cdp_port", lambda port, timeout=1.0: {"Browser": "x"})

    ok, message = launch_client(9333, str(exe), wait_seconds=2)
    assert ok is True
    assert captured["args"] == [str(exe), "--remote-debugging-port=9333"]
    assert "9333" in message


def test_launch_client_without_executable(monkeypatch):
    monkeypatch.setattr(ismart, "find_client_executable", lambda: None)
    ok, message = launch_client(9222, None, wait_seconds=1)
    assert ok is False
    assert "未找到" in message


def test_launch_client_reports_timeout(tmp_path, monkeypatch):
    exe = tmp_path / "iSmart.exe"
    exe.write_text("stub", encoding="utf-8")
    monkeypatch.setattr(ismart.subprocess, "Popen", lambda *a, **k: None)
    monkeypatch.setattr(ismart, "probe_cdp_port", lambda port, timeout=1.0: None)
    ok, message = launch_client(9222, str(exe), wait_seconds=0.3)
    assert ok is False
    assert "未就绪" in message


def test_short_error_hides_urllib_stack():
    text = (
        "HTTPConnectionPool(host='127.0.0.1', port=9222): Max retries exceeded with url: /json/list "
        "(Caused by NewConnectionError('<urllib3.connection.HTTPConnection object>: "
        "Failed to establish a new connection: [WinError 10061] ...'))"
    )
    assert ismart._short_error(Exception(text)) == "端口未开放"


def test_login_message_is_actionable_and_short(monkeypatch):
    """回归：不要再把 200 字符的 urllib3 堆栈甩到界面上。"""
    monkeypatch.setattr(ismart, "find_cdp_port", lambda *a, **k: None)
    monkeypatch.setattr(ismart, "find_client_executable", lambda: None)
    client = ISmartClient(ISmartProfile(port=1))
    ok, message = client.login("u", "p")
    assert ok is False
    assert "已尝试端口" in message
    assert "未在本机找到 iSmart 桌面客户端" in message
    assert "urllib3" not in message and "NewConnectionError" not in message
    assert len(message) < 400
    assert client.last_error_code == "cdp"


def test_login_message_mentions_found_executable(monkeypatch, tmp_path):
    exe = tmp_path / "iSmart.exe"
    exe.write_text("stub", encoding="utf-8")
    monkeypatch.setattr(ismart, "find_cdp_port", lambda *a, **k: None)
    monkeypatch.setattr(ismart, "find_client_executable", lambda: str(exe))
    client = ISmartClient(ISmartProfile(port=1))
    ok, message = client.login("u", "p")
    assert ok is False
    assert "已找到客户端" in message
    assert "启动并连接" in message


def test_launch_and_connect_without_client(monkeypatch):
    monkeypatch.setattr(ismart, "find_client_executable", lambda: None)
    client = ISmartClient(ISmartProfile(port=1))
    ok, message = client.launch_and_connect()
    assert ok is False
    assert "桌面客户端" in message
    assert client.last_error_code == "cdp"


def test_launch_and_connect_success_attaches(monkeypatch, tmp_path):
    exe = tmp_path / "iSmart.exe"
    exe.write_text("stub", encoding="utf-8")
    monkeypatch.setattr(ismart, "find_client_executable", lambda: str(exe))
    monkeypatch.setattr(ismart, "launch_client", lambda port, executable, wait: (True, "已启动"))

    client = ISmartClient(ISmartProfile(port=9555))
    attached = {}
    monkeypatch.setattr(client, "_attach", lambda: attached.setdefault("ok", True))

    ok, message = client.launch_and_connect()
    assert ok is True
    assert attached["ok"] is True
    assert client.profile.port == 9555
    assert "已连接" in message