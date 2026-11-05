"""章节 → 学习 → 新窗口（paper.html）→ 播视频：客户端里刷课的真实链路。"""
import pytest

from core.platforms import ismart
from core.platforms.ismart import (
    CLICK_UNIT_JS,
    PLAY_VIDEO_JS,
    UNITS_FROM_BOOK_JS,
    ISmartClient,
    ISmartProfile,
)


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("ELH_ISMART_PROFILE", str(tmp_path / "p.json"))
    client = ISmartClient(ISmartProfile(port=9222))
    monkeypatch.setattr(ismart.time, "sleep", lambda *_: None)
    return client


# ---------------------------------------------------------------- 章节脚本

def test_click_unit_js_targets_nth_study_button():
    rendered = CLICK_UNIT_JS % {"index": 3}
    assert "const index = 3;" in rendered
    assert "学习" in rendered
    assert "%(index)" not in rendered


def test_units_from_book_js_reads_chapter_rows():
    assert "章节" in UNITS_FROM_BOOK_JS
    assert "Array.from" in UNITS_FROM_BOOK_JS      # 老内核不支持 [...NodeList]
    assert "..." not in UNITS_FROM_BOOK_JS.split("Array.from")[0]


def test_play_video_js_handles_video_and_button():
    assert "querySelector('video')" in PLAY_VIDEO_JS
    assert "play" in PLAY_VIDEO_JS


# ---------------------------------------------------------------- 章节识别

def test_discover_units_parses_rows(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    units = [
        {"id": "unit:0", "name": "章节：UNIT ONE Song of Freedom 0%", "iscomplete": "未完成"},
        {"id": "unit:1", "name": "章节：UNIT TWO Living with Integrity 0%", "iscomplete": "未完成"},
    ]
    monkeypatch.setattr(client, "_eval_top", lambda expr, await_promise=True: units)
    found = client.discover_units()
    assert [u["id"] for u in found] == ["unit:0", "unit:1"]


def test_discover_units_empty(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(client, "_eval_top", lambda expr, await_promise=True: [])
    assert client.discover_units() == []


# ---------------------------------------------------------------- 跟随新窗口

def test_click_and_follow_attaches_new_page(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    before = [{"webSocketDebuggerUrl": "ws://old", "url": "https://x/bookLearn.html"}]
    after = before + [
        {
            "webSocketDebuggerUrl": "ws://new",
            "url": "https://x/paper.html?paperId=9F46&taskId=66BB",
            "title": "在线作答",
        }
    ]
    states = {"n": 0}

    def page_list(port=None):
        states["n"] += 1
        return before if states["n"] == 1 else after

    attached = {}

    class _Session:
        target_url = ""
        target_title = ""

        def evaluate(self, expr, await_promise=True):
            return None

        def send(self, method, params=None):
            return {}

        def close(self):
            pass

    def fake_attach(page):
        attached["page"] = page
        return _Session()

    monkeypatch.setattr(client, "_page_list", page_list)
    monkeypatch.setattr(client, "_eval_top", lambda expr, await_promise=True: True)
    monkeypatch.setattr(client, "_attach_specific", fake_attach)

    ok, message, session = client.click_and_follow("1", "paper.html", wait_seconds=3)
    assert ok is True
    assert "paper.html" in message
    assert attached["page"]["webSocketDebuggerUrl"] == "ws://new"
    assert session is not None


def test_click_and_follow_reports_missing_element(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(client, "_page_list", lambda port=None: [])
    monkeypatch.setattr(client, "_eval_top", lambda expr, await_promise=True: False)
    ok, message, session = client.click_and_follow("1")
    assert ok is False
    assert session is None
    assert "没找到" in message


def test_click_and_follow_times_out(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(client, "_page_list", lambda port=None: [{"webSocketDebuggerUrl": "ws://old"}])
    monkeypatch.setattr(client, "_eval_top", lambda expr, await_promise=True: True)
    clock = {"t": 0.0}
    monkeypatch.setattr(ismart.time, "monotonic", lambda: clock["t"])

    def fake_sleep(seconds):
        clock["t"] += seconds

    monkeypatch.setattr(ismart.time, "sleep", fake_sleep)
    ok, message, session = client.click_and_follow("1", "paper.html", wait_seconds=2)
    assert ok is False
    assert "没等到" in message


# ---------------------------------------------------------------- 播视频

def test_play_video_loops_and_reads_progress(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    calls = {"play": 0, "progress": 0}

    class _Session:
        def evaluate(self, expr, await_promise=True):
            if expr == PLAY_VIDEO_JS:
                calls["play"] += 1
                return True
            calls["progress"] += 1
            return {"learned": 12, "total": 100, "currentTime": "00:00:12", "hasVideo": True}

    clock = {"t": 0.0}
    monkeypatch.setattr(ismart.time, "monotonic", lambda: clock["t"])

    def fake_sleep(seconds):
        clock["t"] += seconds

    monkeypatch.setattr(ismart.time, "sleep", fake_sleep)

    assert client.play_video(30, session=_Session()) is True
    assert calls["play"] >= 2
    assert calls["progress"] >= 1


def test_play_video_requires_session(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    client._session = None
    assert client.play_video(5) is False


def test_play_video_honours_stop_event(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    class _Stop:
        def is_set(self):
            return True

    class _Session:
        def evaluate(self, expr, await_promise=True):
            return True

    clock = {"t": 0.0}
    monkeypatch.setattr(ismart.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(ismart.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    assert client.play_video(60, stop_event=_Stop(), session=_Session()) is False


# ---------------------------------------------------------------- 遍历章节

def test_study_units_walks_each_unit(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    units = [{"id": "unit:0", "name": "章节：UNIT ONE"}, {"id": "unit:1", "name": "章节：UNIT TWO"}]
    monkeypatch.setattr(client, "discover_units", lambda: units)
    entered, played, closed = [], [], []

    class _Session:
        def close(self):
            closed.append(True)

    def fake_follow(script, url_pattern="paper.html", wait_seconds=15.0):
        entered.append(script)
        return True, "https://x/paper.html?p=1", _Session()

    monkeypatch.setattr(client, "click_and_follow", fake_follow)
    monkeypatch.setattr(client, "play_video", lambda seconds, stop_event=None, session=None: played.append(seconds) or True)

    result = client.study_units(5)
    assert result["done"] == 2
    assert result["total"] == 2
    assert len(entered) == 2
    assert played == [5, 5]
    assert len(closed) == 2


def test_study_units_reports_non_book_page(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(client, "discover_units", lambda: [])
    result = client.study_units(5)
    assert result["done"] == 0
    assert "教材页" in result["message"]


def test_study_units_collects_failures(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(client, "discover_units", lambda: [{"id": "unit:0", "name": "UNIT ONE"}])
    monkeypatch.setattr(client, "click_and_follow", lambda *a, **k: (False, "没等到新窗口", None))
    result = client.study_units(5)
    assert result["done"] == 0
    assert len(result["failed"]) == 1
    assert "没等到新窗口" in result["failed"][0]


@pytest.mark.parametrize("seconds", [1, 30])
def test_play_video_accepts_positive_seconds(seconds, monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    class _Session:
        def evaluate(self, expr, await_promise=True):
            return True

    clock = {"t": 0.0}
    monkeypatch.setattr(ismart.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(ismart.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    assert client.play_video(seconds, session=_Session()) is True