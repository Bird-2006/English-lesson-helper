"""CDP 会话层单测：sessionId 注入、附着重试（不需要真实浏览器）。"""
import pytest

from core.platforms.ismart import CDPSession, ISmartClient, ISmartError, ISmartProfile


class _FakeWs:
    """替身 WebSocket：记录发出的 payload，按预设回帧。"""

    def __init__(self, replies):
        self.sent = []
        self.replies = list(replies)

    def send(self, raw):
        self.sent.append(raw)

    def recv(self):
        if not self.replies:
            raise AssertionError("没有更多回帧")
        return self.replies.pop(0)

    def close(self):
        pass


def _session_with(replies):
    session = CDPSession.__new__(CDPSession)
    session._next_id = 0
    session.session_id = None
    session.timeout = 5
    session._ws = _FakeWs(replies)
    return session


def test_send_injects_session_id_when_attached():
    session = _session_with(['{"id": 1, "result": {"ok": true}}'])
    session.session_id = "SID-1"
    result = session.send("Runtime.evaluate", {"expression": "1"})
    assert result == {"ok": True}
    assert '"sessionId": "SID-1"' in session._ws.sent[0]


def test_send_without_session_omits_session_id():
    session = _session_with(['{"id": 1, "result": {}}'])
    session.send("Target.getTargets")
    assert "sessionId" not in session._ws.sent[0]


def test_send_raises_on_cdp_error():
    session = _session_with(['{"id": 1, "error": {"message": "boom"}}'])
    with pytest.raises(ISmartError) as excinfo:
        session.send("Runtime.evaluate")
    assert "boom" in str(excinfo.value)


def test_send_ignores_event_frames():
    session = _session_with(
        ['{"method": "Runtime.consoleAPICalled"}', '{"id": 1, "result": {"value": 2}}']
    )
    assert session.send("Runtime.evaluate") == {"value": 2}


def test_attach_retries_once_then_succeeds(monkeypatch):
    client = ISmartClient(ISmartProfile(port=9500))
    calls = {"n": 0}

    class _Ok:
        session_id = "S"

        def send(self, method, params=None):
            return {}

        def close(self):
            pass

    def fake_attach(port, keyword=""):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ISmartError("target 还在切换")
        return _Ok()

    monkeypatch.setattr(CDPSession, "attach_to_browser", staticmethod(fake_attach))
    monkeypatch.setattr("core.platforms.ismart.find_cdp_port", lambda *a, **k: 9500)

    session = client._attach()
    assert calls["n"] == 2
    assert client._session is session
    client.close()


def test_attach_gives_up_after_two_failures(monkeypatch):
    client = ISmartClient(ISmartProfile(port=9501))
    monkeypatch.setattr(
        CDPSession,
        "attach_to_browser",
        staticmethod(lambda port, keyword="": (_ for _ in ()).throw(ISmartError("no page"))),
    )
    monkeypatch.setattr("core.platforms.ismart.find_cdp_port", lambda *a, **k: 9501)

    with pytest.raises(ISmartError):
        client._attach()
    client.close()