"""CDP 端点兼容性与故障诊断：非标准 JSON、命令行回读、错误文案。"""
import pytest

from core.platforms import ismart
from core.platforms.ismart import (
    CDPSession,
    ISmartError,
    _short_error,
    client_command_line,
    launch_client,
)

# ---------------------------------------------------------------- 错误文案

def test_short_error_key_error_is_readable():
    text = _short_error(KeyError("webSocketDebuggerUrl"))
    assert "缺少字段" in text
    assert "webSocketDebuggerUrl" in text


def test_short_error_timeout():
    assert _short_error(TimeoutError("timed out")) == "响应超时"


def test_short_error_connection_refused():
    assert _short_error(Exception("Connection refused")) == "端口未开放"


# ---------------------------------------------------------------- 端点兼容性

class _Resp:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


@pytest.mark.allow_cdp_attach
def test_attach_without_browser_ws_uses_page_attach(monkeypatch):
    """/json/version 不带 webSocketDebuggerUrl（iSmart 客户端 Chrome 49）→ 走页面级附着。"""
    monkeypatch.setattr(ismart, "local_get", lambda url, timeout=3, **kw: _Resp({
        "Browser": "", "User-Agent": "PC-Up366-iSmart 1.8.0.0 ... Chrome/49.0.2623.110",
    }))

    called = {}

    def fake_attach_page(cls, port, host_keyword=""):
        called["args"] = (port, host_keyword)
        return "PAGE-SESSION"

    monkeypatch.setattr(CDPSession, "attach_page", classmethod(fake_attach_page))
    assert CDPSession.attach_to_browser(9222, "ismartlearning") == "PAGE-SESSION"
    assert called["args"] == (9222, "ismartlearning")


@pytest.mark.allow_cdp_attach
def test_attach_falls_back_when_target_domain_missing(monkeypatch):
    """浏览器级 ws 存在但 Target 域不存在（老内核）时，也退回页面级。"""
    monkeypatch.setattr(ismart, "local_get", lambda url, timeout=3, **kw: _Resp({
        "webSocketDebuggerUrl": "ws://browser/1",
    }))

    def fake_send(self, method, params=None):
        if method == "Target.getTargets":
            raise ISmartError("Target.getTargets 失败: 'Target.getTargets' wasn't found")
        return {}

    monkeypatch.setattr(CDPSession, "__init__", lambda self, ws, timeout=15: setattr(self, "session_id", None) or None)
    monkeypatch.setattr(CDPSession, "send", fake_send)
    monkeypatch.setattr(CDPSession, "close", lambda self: None)

    called = {}
    monkeypatch.setattr(
        CDPSession,
        "attach_page",
        classmethod(lambda cls, port, host_keyword="": called.update(port=port) or "PAGE-SESSION"),
    )
    assert CDPSession.attach_to_browser(9222, "") == "PAGE-SESSION"
    assert called["port"] == 9222


@pytest.mark.allow_cdp_attach
def test_attach_page_reports_no_pages(monkeypatch):
    monkeypatch.setattr(ismart, "discover_pages", lambda *a, **k: [])
    with pytest.raises(ISmartError) as excinfo:
        CDPSession.attach_page(9222, "")
    message = str(excinfo.value)
    assert "没有页面" in message
    assert "页面级" in message


@pytest.mark.allow_cdp_attach
def test_attach_page_prefers_campus_page(monkeypatch):
    pages = [
        {"type": "page", "url": "about:blank", "webSocketDebuggerUrl": "ws://blank", "title": "about:blank"},
        {"type": "page", "url": "http://pc.ismartin.com/", "webSocketDebuggerUrl": "ws://home", "title": "home"},
        {
            "type": "page",
            "url": "https://me.ismartlearning.cn/center/student/courses.html",
            "webSocketDebuggerUrl": "ws://campus",
            "title": "我的课程",
        },
    ]
    monkeypatch.setattr(ismart, "discover_pages", lambda *a, **k: pages)

    created = {}

    def fake_init(self, ws_url, timeout=15):
        created["ws"] = ws_url
        self.session_id = None
        self.legacy = False
        self.target_url = ""
        self.target_title = ""

    monkeypatch.setattr(CDPSession, "__init__", fake_init)
    monkeypatch.setattr(CDPSession, "send", lambda self, method, params=None: {})
    monkeypatch.setattr(CDPSession, "close", lambda self: None)

    session = CDPSession.attach_page(9222, "")
    assert created["ws"] == "ws://campus"
    assert session.legacy is True
    assert session.target_url.endswith("courses.html")


# ---------------------------------------------------------------- 命令行回读

def test_client_command_line_parses_wmic(monkeypatch):
    class _Result:
        stdout = "\r\nCommandLine=\"E:\\iSmart\\client\\iSmart.exe\" --remote-debugging-port=9222\r\n\r\n"

    monkeypatch.setattr(ismart.subprocess, "run", lambda *a, **k: _Result())
    line = client_command_line()
    assert "--remote-debugging-port=9222" in line


def test_client_command_line_falls_back_to_powershell(monkeypatch):
    """wmic 在 Win11 24H2 起被移除：空输出时必须用 PowerShell CIM 兜底。"""
    seen = []

    class _Result:
        def __init__(self, stdout):
            self.stdout = stdout

    def fake_run(command, **kwargs):
        seen.append(command[0])
        if command[0] == "wmic":
            return _Result("")                       # 新系统没有 wmic
        return _Result('"E:\\iSmart\\client\\iSmart.exe" --remote-debugging-port=9222\n')

    monkeypatch.setattr(ismart.subprocess, "run", fake_run)
    line = client_command_line()
    assert seen == ["wmic", "powershell"]
    assert "--remote-debugging-port=9222" in line


def test_client_command_line_returns_empty_on_failure(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("wmic 不存在")

    monkeypatch.setattr(ismart.subprocess, "run", boom)
    assert client_command_line() == ""


# ---------------------------------------------------------------- 启动失败诊断

def _patch_launch(monkeypatch, tmp_path, command_line):
    exe = tmp_path / "iSmart.exe"
    exe.write_text("stub", encoding="utf-8")
    monkeypatch.setattr(ismart.subprocess, "Popen", lambda *a, **k: None)
    monkeypatch.setattr(ismart, "client_running", lambda *a, **k: False)
    monkeypatch.setattr(ismart, "probe_cdp_port", lambda port, timeout=1.0: None)
    monkeypatch.setattr(ismart, "client_command_line", lambda *a, **k: command_line)
    monkeypatch.setattr(ismart.time, "sleep", lambda *_: None)
    clock = {"t": 0.0}
    monkeypatch.setattr(ismart.time, "monotonic", lambda: clock["t"])

    def fake_sleep(seconds):
        clock["t"] += seconds

    monkeypatch.setattr(ismart.time, "sleep", fake_sleep)
    return str(exe)


def test_launch_timeout_points_at_single_instance(monkeypatch, tmp_path):
    exe = _patch_launch(monkeypatch, tmp_path, '"E:\\iSmart\\client\\iSmart.exe"')
    ok, message = launch_client(9222, exe, wait_seconds=1)
    assert ok is False
    assert "命令行里没有" in message
    assert "彻底退出客户端" in message


def test_launch_timeout_when_flag_present_but_port_dead(monkeypatch, tmp_path):
    exe = _patch_launch(
        monkeypatch, tmp_path, '"E:\\iSmart\\client\\iSmart.exe" --remote-debugging-port=9222'
    )
    ok, message = launch_client(9222, exe, wait_seconds=1)
    assert ok is False
    assert "端口仍未就绪" in message
    assert "Chromium" in message


def test_launch_timeout_without_process(monkeypatch, tmp_path):
    exe = _patch_launch(monkeypatch, tmp_path, "")
    ok, message = launch_client(9222, exe, wait_seconds=1)
    assert ok is False
    assert "没找到客户端进程" in message


def test_launch_timeout_when_running_but_cmdline_unreadable(monkeypatch, tmp_path):
    """客户端已以管理员运行时：启动前就被拦下，提示先完全退出（实测本机就是这样）。"""
    exe = _patch_launch(monkeypatch, tmp_path, "")
    monkeypatch.setattr(ismart, "client_running", lambda *a, **k: True)
    ok, message = launch_client(9222, exe, wait_seconds=1)
    assert ok is False
    assert "已经在运行" in message
    assert "完全退出" in message
    assert "单实例" in message