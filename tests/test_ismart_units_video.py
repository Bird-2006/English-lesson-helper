"""章节/课时/作答/播视频：客户端真实链路的单元测试。"""
import pytest

from core.platforms import ismart
from core.platforms.ismart import (
    ANSWER_PAPER_JS,
    BOOK_TASKS_JS,
    CLICK_UNIT_JS,
    OPEN_QUESTION_CARD_JS,
    OPEN_TASK_JS,
    PAPER_KIND_JS,
    PLAY_VIDEO_JS,
    SUBMIT_PAPER_JS,
    UNITS_FROM_BOOK_JS,
    ISmartClient,
    ISmartProfile,
)


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("ELH_ISMART_PROFILE", str(tmp_path / "p.json"))
    client = ISmartClient(ISmartProfile(port=9222))
    monkeypatch.setattr(ismart.time, "sleep", lambda *_: None)
    return client


class _Clock:
    """可控时钟：monotonic 由 sleep 推进，避免用例真的等待。"""

    def __init__(self, monkeypatch):
        self.now = 0.0
        monkeypatch.setattr(ismart.time, "monotonic", lambda: self.now)
        monkeypatch.setattr(ismart.time, "sleep", self._sleep)

    def _sleep(self, seconds):
        self.now += seconds


# ---------------------------------------------------------------- JS 老内核兼容

@pytest.mark.parametrize("script", [UNITS_FROM_BOOK_JS, BOOK_TASKS_JS, ANSWER_PAPER_JS, PAPER_KIND_JS])
def test_scripts_avoid_nodespread(script):
    """Chrome 49 的 NodeList 没有 Symbol.iterator，不能出现 [...document...]。"""
    assert "[...document" not in script
    assert "[...doc." not in script


def test_click_unit_js_targets_nth_study_button():
    rendered = CLICK_UNIT_JS % {"index": 3}
    assert "const index = 3;" in rendered
    assert "学习" in rendered
    assert "%(index)" not in rendered


def test_open_task_js_uses_task_id():
    rendered = OPEN_TASK_JS % {"taskid": '"ABC123"'}
    assert "'node_' + taskId" in rendered
    assert "%(taskid)" not in rendered


def test_submit_paper_js_targets_footer_text():
    assert "提交试卷" in SUBMIT_PAPER_JS
    assert "u3xot-questioncard-footer" in SUBMIT_PAPER_JS


def test_open_question_card_js_expands_body():
    assert "u3xot-questioncard-body" in OPEN_QUESTION_CARD_JS
    assert "display" in OPEN_QUESTION_CARD_JS


def test_answer_paper_js_groups_by_name():
    assert "input[type=radio]" in ANSWER_PAPER_JS
    assert "input[type=checkbox]" in ANSWER_PAPER_JS
    assert "groups" in ANSWER_PAPER_JS


def test_book_tasks_js_reads_task_id_and_learned():
    assert "div.nodeInfo" in BOOK_TASKS_JS
    assert "lesson-span" in BOOK_TASKS_JS
    assert "learned" in BOOK_TASKS_JS
    assert "nodeState" in BOOK_TASKS_JS


# ---------------------------------------------------------------- 课时枚举

def test_discover_book_tasks_parses(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    rows = [
        {"id": "AAA", "location": "Overview", "iscomplete": "未完成"},
        {"id": "BBB", "location": "Reading Comprehension", "iscomplete": "已完成"},
    ]
    monkeypatch.setattr(client, "_eval_top", lambda expr, await_promise=True: rows)
    tasks = client.discover_book_tasks()
    assert [t["id"] for t in tasks] == ["AAA", "BBB"]


def test_discover_book_tasks_handles_garbage(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(client, "_eval_top", lambda expr, await_promise=True: "not-a-list")
    assert client.discover_book_tasks() == []


def test_current_stage_by_url(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    cases = {
        "https://x/center/student/courses.html": "courses",
        "https://x/center/student/course/main.html?courseId=1": "course",
        "https://x/center/student/course/bookLearn.html?bookId=1": "book",
        "https://x/center/teacher/course/paper.html?paperId=1": "paper",
        "https://x/front/login.html": "login",
        "https://x/whatever": "unknown",
    }
    for url, stage in cases.items():
        monkeypatch.setattr(client, "_eval_top", lambda expr, await_promise=True, u=url: u)
        assert client.current_stage() == stage


# ---------------------------------------------------------------- 作答 / 交卷

def test_answer_paper_reports_counts(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    class _Session:
        def evaluate(self, expr, await_promise=True):
            return {"groups": 5, "clicked": 5, "finished": "5", "total": "5"}

    info = client.answer_paper(_Session())
    assert info["groups"] == 5
    assert info["finished"] == "5"


def test_submit_paper_requires_button(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _Clock(monkeypatch)

    class _Session:
        def evaluate(self, expr, await_promise=True):
            if expr == OPEN_QUESTION_CARD_JS:
                return "expanded"
            if expr == SUBMIT_PAPER_JS:
                return "no-submit"
            return {}

    result = client.submit_paper(_Session())
    assert result["submitted"] is False
    assert "提交试卷" in result["reason"]


def test_submit_paper_returns_result(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _Clock(monkeypatch)

    class _Session:
        def evaluate(self, expr, await_promise=True):
            if expr == OPEN_QUESTION_CARD_JS:
                return "expanded"
            if expr == SUBMIT_PAPER_JS:
                return "clicked"
            if expr == ismart.CONFIRM_SUBMIT_JS:
                return ""
            return {"percent": 25, "right": 2, "wrong": 6, "total": 8, "submitted": True}

    result = client.submit_paper(_Session())
    assert result["submitted"] is True
    assert result["percent"] == 25


# ---------------------------------------------------------------- study_paper

def test_study_paper_answers_and_submits(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _Clock(monkeypatch)

    class _Session:
        def evaluate(self, expr, await_promise=True):
            if expr == PAPER_KIND_JS:
                return {"kind": "choice", "radios": 4, "checks": 0, "videos": 0, "blanks": 0, "text": "题目"}
            return {}

    monkeypatch.setattr(client, "answer_paper", lambda session=None: {"groups": 1, "clicked": 1})
    monkeypatch.setattr(client, "submit_paper", lambda session=None, wait_seconds=3.0: {"submitted": True, "percent": 50})
    monkeypatch.setattr(ismart, "_paper_progress", lambda session: {"finished": "1", "total": "1", "radios": 4})

    outcome = client.study_paper(_Session())
    assert outcome["done"] is True
    assert outcome["answer"]["groups"] == 1
    assert outcome["result"]["percent"] == 50


def test_study_paper_plays_video_then_submits(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _Clock(monkeypatch)
    played = {}

    class _Session:
        def evaluate(self, expr, await_promise=True):
            if expr == PAPER_KIND_JS:
                return {"kind": "video", "radios": 0, "checks": 0, "videos": 1, "blanks": 0, "text": "watch"}
            return {}

    def fake_play(seconds=0, stop_event=None, session=None):
        played["seconds"] = seconds
        return 30

    monkeypatch.setattr(client, "play_video", fake_play)
    monkeypatch.setattr(client, "submit_paper", lambda session=None, wait_seconds=3.0: {"submitted": True})
    monkeypatch.setattr(ismart, "_paper_progress", lambda session: {"finished": "", "total": "", "radios": 0})

    outcome = client.study_paper(_Session(), video_seconds=0)
    assert played["seconds"] == 0          # 0 = 播到结束
    assert outcome["video"] == 30
    assert outcome["done"] is True


def test_study_paper_retries_submit_once(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _Clock(monkeypatch)
    attempts = {"n": 0}

    class _Session:
        def evaluate(self, expr, await_promise=True):
            if expr == PAPER_KIND_JS:
                return {"kind": "other", "radios": 0, "checks": 0, "videos": 0, "blanks": 0, "text": "x"}
            return {}

    def fake_submit(session=None, wait_seconds=3.0):
        attempts["n"] += 1
        return {"submitted": attempts["n"] > 1}

    monkeypatch.setattr(client, "submit_paper", fake_submit)
    monkeypatch.setattr(ismart, "_paper_progress", lambda session: {"finished": "", "total": "", "radios": 0})
    outcome = client.study_paper(_Session())
    assert attempts["n"] == 2
    assert outcome["done"] is True


# ---------------------------------------------------------------- run_tasks

def test_run_tasks_reports_non_book_page(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(client, "ensure_book_page", lambda: (False, "客户端停在登录页"))
    result = client.run_tasks(limit=1)
    assert result["done"] == 0
    assert "登录页" in result["message"]


def test_run_tasks_walks_and_counts(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _Clock(monkeypatch)
    tasks = [
        {"id": "T1", "location": "Reading Comprehension", "iscomplete": "未完成"},
        {"id": "T2", "location": "Reading Comprehension", "iscomplete": "未完成"},
        {"id": "T3", "location": "Overview", "iscomplete": "已完成"},
    ]
    monkeypatch.setattr(client, "ensure_book_page", lambda: (True, "ok"))
    monkeypatch.setattr(client, "discover_book_tasks", lambda: tasks)

    class _Session:
        target_url = "https://x/paper.html"

        def evaluate(self, expr, await_promise=True):
            return {}

        def close(self):
            pass

    opened = []

    def fake_open(task_id, wait_seconds=20.0):
        opened.append(task_id)
        return True, "https://x/paper.html?paperId=1", _Session()

    monkeypatch.setattr(client, "open_task", fake_open)
    monkeypatch.setattr(client, "study_paper", lambda session=None, **kw: {"kind": "choice", "done": True})

    result = client.run_tasks(limit=0)
    assert opened == ["T1", "T2"]          # 已完成的不再跑
    assert result["done"] == 2
    assert result["total"] == 2


def test_run_tasks_filters_by_title(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _Clock(monkeypatch)
    tasks = [
        {"id": "T1", "location": "Overview", "iscomplete": "未完成"},
        {"id": "T2", "location": "Reading Comprehension", "iscomplete": "未完成"},
    ]
    monkeypatch.setattr(client, "ensure_book_page", lambda: (True, "ok"))
    monkeypatch.setattr(client, "discover_book_tasks", lambda: tasks)
    monkeypatch.setattr(client, "open_task", lambda task_id, wait_seconds=20.0: (True, "u", object()))
    monkeypatch.setattr(client, "study_paper", lambda session=None, **kw: {"done": True})

    result = client.run_tasks(titles=["Overview"], video_seconds=30)
    assert result["total"] == 1


def test_run_tasks_records_failure(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _Clock(monkeypatch)
    tasks = [{"id": "T1", "location": "Overview", "iscomplete": "未完成"}]
    monkeypatch.setattr(client, "ensure_book_page", lambda: (True, "ok"))
    monkeypatch.setattr(client, "discover_book_tasks", lambda: tasks)
    monkeypatch.setattr(client, "open_task", lambda task_id, wait_seconds=20.0: (False, "没等到新窗口", None))

    result = client.run_tasks()
    assert result["done"] == 0
    assert "没等到新窗口" in result["failed"][0]


def test_run_tasks_stops_on_event(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _Clock(monkeypatch)

    class _Stop:
        def is_set(self):
            return True

    tasks = [{"id": "T1", "location": "Overview", "iscomplete": "未完成"}]
    monkeypatch.setattr(client, "ensure_book_page", lambda: (True, "ok"))
    monkeypatch.setattr(client, "discover_book_tasks", lambda: tasks)
    opened = []
    monkeypatch.setattr(
        client, "open_task",
        lambda task_id, wait_seconds=20.0: opened.append(task_id) or (True, "u", object()),
    )

    result = client.run_tasks(stop_event=_Stop())
    assert opened == []
    assert result["done"] == 0


# ---------------------------------------------------------------- 播视频

def test_play_video_returns_int(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _Clock(monkeypatch)

    class _Session:
        def evaluate(self, expr, await_promise=True):
            if expr == PLAY_VIDEO_JS:
                return {"hasVideo": True, "duration": 100, "currentTime": 12, "ended": False}
            return {}

    assert isinstance(client.play_video(5, session=_Session()), (int, float))


def test_play_video_requires_session(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    client._session = None
    assert client.play_video(5) == 0


def test_play_video_until_end_exits_on_ended(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _Clock(monkeypatch)
    state = {"ended": False, "calls": 0}

    class _Session:
        def evaluate(self, expr, await_promise=True):
            if expr == PLAY_VIDEO_JS:
                state["calls"] += 1
                if state["calls"] >= 2:
                    state["ended"] = True
                return {"hasVideo": True, "duration": 60, "currentTime": 60, "ended": state["ended"]}
            return {}

    client.play_video(0, session=_Session())
    assert state["ended"] is True


def test_play_video_honours_stop_event(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _Clock(monkeypatch)

    class _Stop:
        def is_set(self):
            return True

    class _Session:
        def evaluate(self, expr, await_promise=True):
            return {"hasVideo": True}

    assert client.play_video(60, stop_event=_Stop(), session=_Session()) == 0