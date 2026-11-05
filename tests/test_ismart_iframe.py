"""同源 iframe 相关：包装器不能踩 ASI、按钮识别要覆盖 input、回列表要拨 iframe。"""

import pytest

from core.platforms import ismart
from core.platforms.ismart import (
    CLICK_TASK_JS,
    CLICK_TEXT_JS,
    ISmartClient,
    ISmartProfile,
    click_task_script,
    wrap_for_content,
)


def test_wrapper_parenthesises_return_to_avoid_asi():
    """脚本以换行开头，`return` 后面必须带括号，否则 JS 会自动插入分号。"""
    wrapped = wrap_for_content("\n(() => 42)()\n")
    assert "return ((() => 42)());" in wrapped
    assert "return \n" not in wrapped
    assert "return %s" not in wrapped


def test_wrapper_strips_leading_newline():
    wrapped = wrap_for_content("\n\n  (() => 1)()  \n")
    assert wrapped.count("(() => 1)()") == 1


def test_wrapper_prefers_iframe_selectors():
    wrapped = wrap_for_content("1")
    for selector in ("#ifra", "indexIframe", 'iframe[src*="courses"]'):
        assert selector in wrapped


def test_click_task_js_covers_input_buttons():
    js = CLICK_TASK_JS
    assert "input" in js.split("querySelectorAll(")[1][:200]
    assert "el.value" in js          # input 的文案在 value 上
    assert "aria-label" in js


def test_click_text_js_covers_input_and_value():
    assert "input" in CLICK_TEXT_JS
    assert "el.value" in CLICK_TEXT_JS


def test_click_task_js_formats_card_target():
    rendered = click_task_script("card:0")
    assert '"card:0"' in rendered
    assert "%(" not in rendered
    assert "进入课程" in rendered


class _Recorder:
    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on or ()

    def __call__(self, expression, await_promise=True):
        self.calls.append(expression)
        for token in self.fail_on:
            if token in expression:
                raise ismart.ISmartError("注入失败")
        return True


def _client(monkeypatch, tmp_path, recorder):
    monkeypatch.setenv("ELH_ISMART_PROFILE", str(tmp_path / "p.json"))
    client = ISmartClient(ISmartProfile(port=9222))
    monkeypatch.setattr(client, "_eval", recorder)
    monkeypatch.setattr(ismart.time, "sleep", lambda *_: None)
    return client


def test_return_to_list_prefers_iframe_navigation(monkeypatch, tmp_path):
    recorder = _Recorder()
    client = _client(monkeypatch, tmp_path, recorder)
    client._list_url = "https://me.example.com/center/student/courses.html"
    client._outer_url = "https://me.example.com/center/student/index.html"
    navigated = []

    class _Session:
        def send(self, method, params=None):
            navigated.append((method, params))
            return {}

    monkeypatch.setattr(client, "_attach", lambda: _Session())

    assert client.return_to_list() is True
    assert any("courses.html" in call for call in recorder.calls)
    assert not navigated  # 不该整页重载


def test_return_to_list_falls_back_to_page_navigate(monkeypatch, tmp_path):
    recorder = _Recorder(fail_on=("location.href",))
    client = _client(monkeypatch, tmp_path, recorder)
    client._list_url = "https://me.example.com/center/student/courses.html"
    client._outer_url = "https://me.example.com/center/student/index.html"
    navigated = []

    class _Session:
        def send(self, method, params=None):
            navigated.append((method, params))
            return {}

    monkeypatch.setattr(client, "_attach", lambda: _Session())
    assert client.return_to_list() is True
    assert navigated and navigated[0][0] == "Page.navigate"
    assert navigated[0][1]["url"].endswith("index.html")


def test_return_to_list_without_urls(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path, _Recorder())
    assert client.return_to_list() is False


def test_wait_content_retries_then_gives_up(monkeypatch, tmp_path):
    """内容一直空时要按超时返回 False，而不是死循环。"""
    monkeypatch.setenv("ELH_ISMART_PROFILE", str(tmp_path / "p.json"))
    client = ISmartClient(ISmartProfile(port=9222))
    monkeypatch.setattr(client, "_eval", lambda expr, await_promise=True: "")
    monkeypatch.setattr(ismart.time, "sleep", lambda *_: None)
    clock = {"t": 0.0}
    monkeypatch.setattr(ismart.time, "monotonic", lambda: clock["t"])
    orig_sleep = ismart.time.sleep

    def fake_sleep(seconds):
        clock["t"] += seconds

    monkeypatch.setattr(ismart.time, "sleep", fake_sleep)
    assert client._wait_content(timeout=2.0) is False
    assert clock["t"] >= 2.0
    monkeypatch.setattr(ismart.time, "sleep", orig_sleep)


def test_wait_content_returns_true_when_text_appears(monkeypatch, tmp_path):
    monkeypatch.setenv("ELH_ISMART_PROFILE", str(tmp_path / "q.json"))
    client = ISmartClient(ISmartProfile(port=9222))
    values = ["", "", "课程"]
    monkeypatch.setattr(client, "_eval", lambda expr, await_promise=True: values.pop(0))
    monkeypatch.setattr(ismart.time, "sleep", lambda *_: None)
    assert client._wait_content(timeout=3.0) is True


def test_remember_list_url_records_both_documents(monkeypatch, tmp_path):
    monkeypatch.setenv("ELH_ISMART_PROFILE", str(tmp_path / "r.json"))
    client = ISmartClient(ISmartProfile(port=9222))
    monkeypatch.setattr(client, "_eval", lambda expr, await_promise=True: "https://x/courses.html")
    monkeypatch.setattr(client, "_eval_top", lambda expr, await_promise=True: "https://x/center")
    client._remember_list_url()
    assert client._list_url.endswith("courses.html")
    assert client._outer_url.endswith("/center")


@pytest.mark.parametrize("cid", ["card:0", "task:3"])
def test_click_task_targets_are_prefixed_ids(cid):
    assert click_task_script(cid)