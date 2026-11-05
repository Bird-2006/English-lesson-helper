"""识别任务 + 提交按钮文案兜底。"""
import json

from core.platforms import ismart
from core.platforms.ismart import (
    SUBMIT_LABELS,
    ISmartClient,
    ISmartProfile,
)


class _Script:
    def __init__(self, mapping):
        self.mapping = dict(mapping)
        self.calls = []

    def __call__(self, expression, await_promise=True):
        self.calls.append(expression)
        for key, value in self.mapping.items():
            if key in expression:
                return value
        return False


def _client(monkeypatch, tmp_path, script):
    monkeypatch.setenv("ELH_ISMART_PROFILE", str(tmp_path / "p.json"))
    client = ISmartClient(ISmartProfile(port=9222))
    monkeypatch.setattr(client, "_eval", script)
    monkeypatch.setattr(client, "_wait_content", lambda *a, **k: True)
    monkeypatch.setattr(ismart.time, "sleep", lambda *_: None)
    return client


# ---------------------------------------------------------------- 识别任务

def test_discover_tasks_saves_list(monkeypatch, tmp_path):
    tasks = [
        {"id": "task:0", "location": "Unit 1 测验", "selector": "#u1", "isvisible": "true", "iscomplete": "未完成"},
        {"id": "task:1", "location": "Unit 2 视频", "selector": "#u2", "isvisible": "true", "iscomplete": "已完成"},
    ]
    client = _client(monkeypatch, tmp_path, _Script({"const out = [], seen": tasks}))

    data = client.discover_tasks()
    assert data["count"] == 2
    assert data["tasks"][0]["selector"] == "#u1"
    stored = json.loads(open(data["saved_to"], encoding="utf-8").read())
    assert stored["count"] == 2


def test_discover_tasks_handles_empty(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path, _Script({"const out = [], seen": []}))
    data = client.discover_tasks()
    assert data["count"] == 0
    assert data["tasks"] == []


def test_discover_tasks_reports_page_error(monkeypatch, tmp_path):
    monkeypatch.setenv("ELH_ISMART_PROFILE", str(tmp_path / "p.json"))
    client = ISmartClient(ISmartProfile(port=9222))

    def boom(expr):
        raise ismart.ISmartError("没有页面")

    monkeypatch.setattr(client, "_eval", boom)
    monkeypatch.setattr(client, "_wait_content", lambda *a, **k: True)
    data = client.discover_tasks()
    assert data["count"] == 0
    assert "没有页面" in data["error"]


# ---------------------------------------------------------------- 提交文案兜底

def test_click_submit_tries_labels_in_order(monkeypatch, tmp_path):
    """第一选择点不到时，继续试后续文案（很多页面叫「保存」或「完成」）。"""
    monkeypatch.setenv("ELH_ISMART_PROFILE", str(tmp_path / "p.json"))
    client = ISmartClient(ISmartProfile(port=9222))
    tried = []

    def script(expression, await_promise=True):
        if "const want" not in expression:
            return True
        for label in SUBMIT_LABELS:
            if json.dumps(label) in expression:
                tried.append(label)
                return label == "保存"  # 只有「保存」能点
        return False

    monkeypatch.setattr(client, "_eval", script)
    monkeypatch.setattr(ismart.time, "sleep", lambda *_: None)
    client.profile.recorded_steps = []
    client.profile.submit_steps = [{"kind": "click_submit"}, {"kind": "expect_ok"}]

    way1, way1_fail, _, _ = client.submit_course_progress("c1", "u", "k", "task:0", 100)
    assert (way1, way1_fail) == (1, 0)
    assert tried[0] == SUBMIT_LABELS[0]
    assert "保存" in tried


def test_click_submit_failure_lists_candidates(monkeypatch, tmp_path):
    monkeypatch.setenv("ELH_ISMART_PROFILE", str(tmp_path / "q.json"))
    client = ISmartClient(ISmartProfile(port=9222))
    monkeypatch.setattr(client, "_eval", lambda expr, await_promise=True: False)
    monkeypatch.setattr(ismart.time, "sleep", lambda *_: None)
    client.profile.recorded_steps = []
    client.profile.submit_steps = [{"kind": "click_submit"}]

    way1, way1_fail, _, _ = client.submit_course_progress("c1", "u", "k", "task:0", 100)
    assert (way1, way1_fail) == (0, 1)


def test_submit_labels_include_common_words():
    for word in ("提交", "保存", "完成"):
        assert word in SUBMIT_LABELS


def test_default_submit_steps_use_click_submit():
    from core.platforms.ismart import DEFAULT_SUBMIT_STEPS

    kinds = [step["kind"] for step in DEFAULT_SUBMIT_STEPS]
    assert "click_submit" in kinds
    assert "click_text" not in kinds


# ---------------------------------------------------------------- 任务识别 JS

def test_tasks_js_covers_video_and_input():
    from core.platforms.ismart import TASK_LABELS, TASKS_JS, click_task_script

    assert "视频" in TASK_LABELS          # 标签表统一放 TASK_LABELS
    assert "%(labels)s" in TASKS_JS       # 运行时注入，避免与 CLICK_TASK_JS 漂移
    assert "input" in TASKS_JS
    assert "selector" in TASKS_JS
    assert "aria-label" in TASKS_JS
    # 点击脚本里也要有同一份标签，保证序号对齐
    script = click_task_script("task:2")
    assert "视频" in script and "task:2" in script