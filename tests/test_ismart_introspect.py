"""页面自检与登录页识别：提示要能照做，自检要能落盘。"""
import json

import pytest

from core.platforms import ismart
from core.platforms.ismart import ISmartClient, ISmartProfile


class _StubSession:
    def __init__(self, payload):
        self.payload = payload

    def evaluate(self, expression, await_promise=True):
        return self.payload


def _client(payload, monkeypatch, tmp_path):
    monkeypatch.setenv("ELH_ISMART_PROFILE", str(tmp_path / "p.json"))
    client = ISmartClient(ISmartProfile(port=9222))
    monkeypatch.setattr(client, "_attach", lambda: _StubSession(payload))
    return client


def test_get_courses_on_login_page_says_please_login(monkeypatch, tmp_path):
    client = _client([], monkeypatch, tmp_path)
    monkeypatch.setattr(
        client,
        "login_page_info",
        lambda: {"isLogin": True, "title": "登录", "url": "https://me.example.com/front/login.html"},
    )
    ok, courses, message = client.get_courses()
    assert ok is False and courses == []
    assert "登录页" in message
    assert "浏览器模式" in message and "刷新课程" in message


def test_get_courses_off_list_page_reports_url(monkeypatch, tmp_path):
    client = _client([], monkeypatch, tmp_path)
    monkeypatch.setattr(
        client, "login_page_info", lambda: {"isLogin": False, "url": "https://me.example.com/home"}
    )
    ok, _, message = client.get_courses()
    assert ok is False
    assert "https://me.example.com/home" in message
    assert "页面自检" in message


def test_get_courses_returns_data_when_found(monkeypatch, tmp_path):
    client = _client([{"cid": "c1", "name": "Book", "per": ""}], monkeypatch, tmp_path)
    ok, courses, message = client.get_courses()
    assert ok is True and courses[0]["cid"] == "c1"
    assert message == "获取课程成功"


def test_page_introspect_saves_json(monkeypatch, tmp_path):
    payload = {
        "url": "https://me.example.com/course",
        "title": "我的课程",
        "text": "Book One",
        "dataAttrs": [["data-id", 12], ["data-courseid", 3]],
        "anchors": [{"text": "Book One", "href": "/course/1"}],
        "buttons": ["进入"],
        "inputs": [],
        "xhr": ["https://me.example.com/api/course/list"],
        "likelyLogin": False,
    }
    client = _client(payload, monkeypatch, tmp_path)
    data = client.page_introspect()

    assert data["title"] == "我的课程"
    saved = data["saved_to"]
    stored = json.loads(open(saved, encoding="utf-8").read())
    assert stored["dataAttrs"][0][0] == "data-id"
    assert stored["xhr"] == ["https://me.example.com/api/course/list"]


def test_page_introspect_retries_until_rendered(monkeypatch, tmp_path):
    """页面异步渲染：第一次读到空要重试，不能直接报「没有内容」。"""
    monkeypatch.setenv("ELH_ISMART_PROFILE", str(tmp_path / "p.json"))
    payloads = [
        {"url": "https://x/y", "title": "课程", "text": "", "inputs": [], "anchors": []},
        {"url": "https://x/y", "title": "课程", "text": "Book One", "inputs": [{"type": "text"}], "anchors": []},
    ]
    client = ISmartClient(ISmartProfile(port=9222))
    calls = {"n": 0}

    class _Session:
        def evaluate(self, expression, await_promise=True):
            index = min(calls["n"], len(payloads) - 1)
            calls["n"] += 1
            return payloads[index]

    monkeypatch.setattr(client, "_attach", lambda: _Session())
    monkeypatch.setattr("core.platforms.ismart.time.sleep", lambda *_: None)

    data = client.page_introspect()
    assert calls["n"] >= 2
    assert data["text"] == "Book One"


def test_page_introspect_tolerates_garbage(monkeypatch, tmp_path):
    client = _client("not-a-dict", monkeypatch, tmp_path)
    monkeypatch.setattr("core.platforms.ismart.time.sleep", lambda *_: None)
    data = client.page_introspect()
    assert isinstance(data, dict)


def test_login_page_info_tolerates_failure(monkeypatch, tmp_path):
    client = ISmartClient(ISmartProfile(port=9222))

    def boom():
        raise ismart.ISmartError("没有页面")

    monkeypatch.setattr(client, "_attach", boom)
    assert client.login_page_info() == {}


@pytest.mark.parametrize(
    "payload,expected_login",
    [
        ({"isLogin": True}, "登录页"),
        ({"isLogin": False, "url": "https://x/y"}, "不是课程列表"),
    ],
)
def test_course_failure_message_variants(payload, expected_login, monkeypatch, tmp_path):
    client = _client([], monkeypatch, tmp_path)
    monkeypatch.setattr(client, "login_page_info", lambda: payload)
    _, _, message = client.get_courses()
    assert expected_login in message