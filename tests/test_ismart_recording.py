"""录制健壮性 + 地址校验：导航后不丢事件、拒绝非网页地址、截图前切前台。"""
import json

import pytest

from core.platforms import ismart
from core.platforms.ismart import CDPSession, ISmartClient, ISmartProfile, normalize_url
from core.platforms.ismart_flow import RECORDER_BOOTSTRAP_JS, RECORDER_STOP_JS

# ---------------------------------------------------------------- 地址校验

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://ismart.example.com/login", "https://ismart.example.com/login"),
        ("http://127.0.0.1:8080/x", "http://127.0.0.1:8080/x"),
        ("  https://a.b/c  ", "https://a.b/c"),
        ("data:text/html,<b>x</b>", ""),
        ("file:///C:/x.html", ""),
        ("javascript:alert(1)", ""),
        ("", ""),
    ],
)
def test_normalize_url_only_accepts_http(raw, expected):
    assert normalize_url(raw) == expected


def test_open_in_browser_rejects_non_web_url(monkeypatch):
    client = ISmartClient(ISmartProfile(port=9222, browser_url="data:text/html,x"))
    monkeypatch.setattr(ismart, "find_browser", lambda: "C:/fake/msedge.exe")
    called = {}
    monkeypatch.setattr(ismart, "launch_browser", lambda *a, **k: called.setdefault("n", 1))

    ok, message = client.open_in_browser()
    assert ok is False
    assert "不是网页地址" in message
    assert "https://" in message
    assert not called  # 根本不该去启动浏览器
    assert client.profile.browser_url == ""  # 无效值被清掉


# ---------------------------------------------------------------- 录制健壮性

class _FakeWs:
    """回显式替身：把收到的请求 id 原样回送，模拟 CDP 的请求-应答。"""

    def __init__(self, result=None):
        self.sent = []
        self.result = result if result is not None else {}
        self._last = {"id": 0}

    def send(self, raw):
        self.sent.append(raw)
        self._last = json.loads(raw)

    def recv(self):
        return json.dumps({"id": self._last["id"], "result": self.result})

    def close(self):
        pass


def _session(result=None):
    session = CDPSession.__new__(CDPSession)
    session._next_id = 0
    session.session_id = "S"
    session.timeout = 5
    session._ws = _FakeWs(result)
    return session


def test_recorder_bootstrap_uses_localstorage_for_navigation_safety():
    assert "localStorage" in RECORDER_BOOTSTRAP_JS
    assert "__ELH_REC_MEM" in RECORDER_BOOTSTRAP_JS
    # 每次导航都要能自动重新注入 → 引导脚本必须可重复执行且自我收敛
    assert "window.__ELH_REC_READY" in RECORDER_BOOTSTRAP_JS


def test_recorder_stop_merges_storage_and_memory():
    assert "localStorage" in RECORDER_STOP_JS
    assert "concat" in RECORDER_STOP_JS  # 合并两份来源
    assert "removeItem" in RECORDER_STOP_JS  # 结束即清理，避免下次串味


def test_start_recording_registers_navigation_script(monkeypatch):
    client = ISmartClient(ISmartProfile(port=9222))
    calls = []

    class _Session:
        def send(self, method, params=None):
            calls.append((method, params or {}))
            if method == "Page.addScriptToEvaluateOnNewDocument":
                return {"identifier": "SCRIPT-1"}
            return {}

    monkeypatch.setattr(client, "_attach", lambda: _Session())
    monkeypatch.setattr(client, "_eval", lambda expr, await_promise=True: "ready")

    assert client.start_recording() is True
    payload = RECORDER_BOOTSTRAP_JS
    assert ("Page.enable", {}) in calls
    registered = [c for c in calls if c[0] == "Page.addScriptToEvaluateOnNewDocument"]
    assert registered and registered[0][1]["source"] == payload
    assert client._recorder_script_id == "SCRIPT-1"


def test_stop_recording_removes_navigation_script(monkeypatch):
    client = ISmartClient(ISmartProfile(port=9222))
    monkeypatch.setattr(client.profile, "save", lambda *a, **k: None)
    client._recorder_script_id = "SCRIPT-1"
    removed = []

    class _Session:
        def send(self, method, params=None):
            removed.append((method, (params or {}).get("identifier")))
            return {}

    client._session = _Session()
    monkeypatch.setattr(
        client, "_eval", lambda expr, await_promise=True: [{"t": 0, "selector": "[data-scoid='x']"}]
    )

    steps = client.stop_recording()
    assert steps
    assert ("Page.removeScriptToEvaluateOnNewDocument", "SCRIPT-1") in removed
    assert client._recorder_script_id is None


def test_stop_recording_without_events_is_clean(monkeypatch):
    client = ISmartClient(ISmartProfile(port=9222))
    monkeypatch.setattr(client, "_eval", lambda expr, await_promise=True: [])
    assert client.stop_recording() == []


# ---------------------------------------------------------------- 配置隔离

def test_profile_path_can_be_overridden_by_env(monkeypatch, tmp_path):
    target = tmp_path / "p.json"
    monkeypatch.setenv("ELH_ISMART_PROFILE", str(target))
    assert ISmartProfile.path() == str(target)


# ---------------------------------------------------------------- 截图

def test_screenshot_brings_page_to_front_first():
    session = _session({"data": ""})
    assert session.screenshot() == b""
    assert "Page.bringToFront" in session._ws.sent[0]