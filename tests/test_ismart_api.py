"""接口优先的课程获取 + 抓接口工具。"""
import json

import pytest

from core.platforms import ismart
from core.platforms.ismart import CDPSession, ISmartClient, ISmartProfile, extract_list

# ---------------------------------------------------------------- 列表结构解析

@pytest.mark.parametrize(
    "payload",
    [
        [{"courseId": 1}],
        {"data": [{"courseId": 1}]},
        {"data": {"list": [{"courseId": 1}]}},
        {"rows": [{"courseId": 1}]},
        {"result": {"records": [{"courseId": 1}]}},
        {"code": 0, "data": {"page": {"items": [{"courseId": 1}]}}},
    ],
)
def test_extract_list_handles_common_shapes(payload):
    found = extract_list(payload)
    assert found and found[0]["courseId"] == 1


def test_extract_list_returns_empty_for_scalars():
    assert extract_list({"code": 0, "msg": "ok"}) == []
    assert extract_list("text") == []
    assert extract_list(None) == []


def test_courses_from_records_maps_fields():
    records = [
        {"courseId": 12, "courseName": "周三5-6节", "teacherName": "徐辉"},
        {"id": 34, "name": "周五7-8节", "teacher": "李四"},
        {"noId": True},
    ]
    courses = ISmartClient._courses_from_records(records)
    assert [c["cid"] for c in courses] == ["12", "34"]
    # 主讲并进名称；per 只表示学习进度，不能塞主讲
    assert courses[0]["name"] == "周三5-6节（徐辉）"
    assert courses[0]["per"] == ""


# ---------------------------------------------------------------- 接口优先

def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("ELH_ISMART_PROFILE", str(tmp_path / "p.json"))
    client = ISmartClient(ISmartProfile(port=9222))
    monkeypatch.setattr(ismart.time, "sleep", lambda *_: None)
    return client


def test_get_courses_prefers_api(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    payload = {"code": 0, "data": {"list": [{"courseId": 7, "courseName": "周三5-6节", "teacherName": "徐辉"}]}}
    monkeypatch.setattr(client, "api_get", lambda path, params=None, timeout=10.0: (True, payload, ""))
    monkeypatch.setattr(client, "_eval", lambda expr, await_promise=True: "https://me.example.com/center")

    ok, courses, message = client.get_courses()
    assert ok is True
    assert courses[0]["cid"] == "7"
    assert "接口" in message
    assert client._list_url.endswith("/center")


def test_get_courses_falls_back_to_dom_when_api_empty(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(client, "api_get", lambda *a, **k: (True, {"code": 0, "data": {"list": []}}, ""))
    monkeypatch.setattr(
        client,
        "_eval",
        lambda expr, await_promise=True: (
            [{"cid": "card:0", "name": "周三5-6节", "per": ""}] if "进入课程" in expr else "https://x/center"
        ),
    )
    ok, courses, _ = client.get_courses()
    assert ok and courses[0]["cid"] == "card:0"


def test_get_courses_reports_when_api_and_dom_both_fail(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(client, "api_get", lambda *a, **k: (False, None, "接口返回 HTTP 401"))
    monkeypatch.setattr(client, "_eval", lambda expr, await_promise=True: ([] if "进入课程" in expr else "https://x/login"))
    monkeypatch.setattr(client, "login_page_info", lambda: {"isLogin": True, "title": "登录"})
    ok, courses, message = client.get_courses()
    assert ok is False and courses == []
    assert "登录页" in message


# ---------------------------------------------------------------- Cookie 与请求

def test_cookies_from_cdp(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    class _Session:
        def send(self, method, params=None):
            assert method == "Network.getAllCookies"
            return {"cookies": [{"name": "SESSION", "value": "abc"}, {"name": "uid", "value": "1"}]}

    monkeypatch.setattr(client, "_attach", lambda: _Session())
    assert client.cookies() == {"SESSION": "abc", "uid": "1"}


def test_cookies_fall_back_to_document_cookie(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    class _Session:
        def send(self, method, params=None):
            raise ismart.ISmartError("unsupported")

    monkeypatch.setattr(client, "_attach", lambda: _Session())
    monkeypatch.setattr(client, "_eval", lambda expr, await_promise=True: "a=1; b=2")
    assert client.cookies() == {"a": "1", "b": "2"}


def test_api_get_requires_cookies(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(client, "cookies", lambda: {})
    ok, payload, message = client.api_get("/client/course/list-of-student")
    assert ok is False
    assert "登录" in message


def test_api_get_returns_json(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(client, "cookies", lambda: {"SESSION": "abc"})
    seen = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"code": 0, "data": {"list": [{"courseId": 1}]}}

    def fake_get(url, params=None, cookies=None, headers=None, timeout=10):
        seen.update({"url": url, "params": params, "cookies": cookies})
        return _Resp()

    monkeypatch.setattr(ismart.requests, "get", fake_get)
    ok, payload, message = client.api_get("/client/course/list-of-student", {"status": 1})
    assert ok is True
    assert payload["code"] == 0
    assert seen["url"].startswith("https://school.ismartlearning.cn")
    assert seen["cookies"] == {"SESSION": "abc"}


def test_api_get_handles_http_error(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(client, "cookies", lambda: {"SESSION": "abc"})

    class _Resp:
        status_code = 401

    monkeypatch.setattr(ismart.requests, "get", lambda *a, **k: _Resp())
    ok, payload, message = client.api_get("/x")
    assert ok is False
    assert "401" in message


# ---------------------------------------------------------------- 抓接口

class _FakeWs:
    def __init__(self, frames):
        self.frames = list(frames)
        self.sent = []
        self.timeouts = []

    def send(self, raw):
        self.sent.append(raw)

    def recv(self):
        if not self.frames:
            raise TimeoutError("no more frames")
        return json.dumps(self.frames.pop(0))

    def settimeout(self, value):
        self.timeouts.append(value)

    def close(self):
        pass


def test_drain_collects_event_frames_only():
    session = CDPSession.__new__(CDPSession)
    session._next_id = 0
    session.session_id = "S"
    session.timeout = 5
    session._ws = _FakeWs(
        [
            {"method": "Network.requestWillBeSent", "params": {}},
            {"id": 9, "result": {}},  # 应答帧不算事件
        ]
    )
    events = session.drain(0.05)
    assert [e["method"] for e in events] == ["Network.requestWillBeSent"]


def test_capture_network_aggregates_and_saves(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    frames = [
        {
            "method": "Network.requestWillBeSent",
            "params": {
                "request": {
                    "url": "https://school.ismartlearning.cn/client/course/list-of-student?status=1",
                    "method": "GET",
                }
            },
        },
        {
            "method": "Network.requestWillBeSent",
            "params": {
                "request": {
                    "url": "https://school.ismartlearning.cn/client/course/list-of-student?status=1",
                    "method": "GET",
                }
            },
        },
        {
            "method": "Network.requestWillBeSent",
            "params": {
                "request": {
                    "url": "https://school.ismartlearning.cn/client/study/save",
                    "method": "POST",
                    "postData": '{"ut":"abc123"}',
                }
            },
        },
        {"method": "Network.requestWillBeSent", "params": {"request": {"url": "data:text/html,x"}}},
    ]

    class _Session:
        def __init__(self):
            self.enabled = False

        def send(self, method, params=None):
            if method == "Network.enable":
                self.enabled = True
            return {}

        def drain(self, seconds, should_stop=None):
            return frames

    session = _Session()
    monkeypatch.setattr(client, "_attach", lambda: session)

    result = client.capture_network(30)
    assert session.enabled is True
    assert result["seconds"] == 30
    urls = [item["url"] for item in result["requests"]]
    assert all(not url.startswith("data:") for url in urls)
    assert len(urls) == 2  # 同一 URL 聚合
    post = [item for item in result["requests"] if item["method"] == "POST"][0]
    assert '"ut":"abc123"' in post["postData"]
    saved = json.loads(open(result["saved_to"], encoding="utf-8").read())
    assert saved["requests"]