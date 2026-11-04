"""iSmart 流程层测试：录制转步骤、占位符替换、回放执行、挂时长可中断。"""
import threading

import pytest

from core.platforms import ismart
from core.platforms.ismart import ISmartClient, ISmartProfile
from core.platforms.ismart_flow import FlowError, FlowRunner, build_steps

# ---------------------------------------------------------------- build_steps

def test_build_steps_converts_events_and_waits():
    events = [
        {"t": 0, "selector": "[data-scoid='s1']", "text": "Task 1"},
        {"t": 1500, "selector": "button.submit", "text": "提交"},
    ]
    steps = build_steps(events)
    assert steps[0]["kind"] == "click"
    assert steps[0]["selector"] == '[data-scoid="{scoid}"]'  # 任务 id 被占位符化
    assert {"kind": "wait", "ms": 1500} in steps
    assert steps[-1] == {"kind": "click", "selector": "button.submit", "text": "提交"}


def test_build_steps_drops_duplicates_and_tiny_gaps():
    events = [
        {"t": 0, "selector": "a.first"},
        {"t": 50, "selector": "a.first"},
        {"t": 60, "selector": "a.second"},
    ]
    steps = build_steps(events)
    assert [s["kind"] for s in steps] == ["click", "click"]
    assert all(s["kind"] != "wait" for s in steps)


def test_build_steps_skips_empty_events():
    assert build_steps([{"t": 0, "selector": "", "text": ""}, {}]) == []


# ---------------------------------------------------------------- FlowRunner

class _StubSession:
    def __init__(self, click_results=None, text_present=True):
        self.expressions = []
        self.click_results = list(click_results or [])
        self.text_present = text_present

    def evaluate(self, expression, await_promise=True):
        self.expressions.append(expression)
        if "document.querySelector" in expression:
            return self.click_results.pop(0) if self.click_results else True
        if "indexOf" in expression:
            return self.text_present
        return True


def test_runner_replaces_placeholder_in_selector():
    session = _StubSession(click_results=[True])
    FlowRunner(session).run([{"kind": "click", "selector": '[data-scoid="{scoid}"]'}], {"scoid": "s99"})
    expression = session.expressions[0]
    assert "{scoid}" not in expression  # 占位符必须被替换掉
    assert '[data-scoid=\\"s99\\"]' in expression  # 作为 JS 字符串字面量注入


def test_runner_raises_when_element_missing():
    session = _StubSession(click_results=[False])
    with pytest.raises(FlowError) as excinfo:
        FlowRunner(session).run([{"kind": "click", "selector": "#nope"}])
    assert "没找到" in str(excinfo.value)


def test_runner_falls_back_to_text_click():
    session = _StubSession(click_results=[False, True])
    FlowRunner(session).run([{"kind": "click", "selector": "#nope", "text": "提交"}])
    assert "提交" in session.expressions[-1]


def test_runner_expect_text_failure():
    session = _StubSession(text_present=False)
    with pytest.raises(FlowError):
        FlowRunner(session).run([{"kind": "expect_text", "text": "已完成"}])


def test_runner_unknown_step():
    with pytest.raises(FlowError):
        FlowRunner(_StubSession()).run([{"kind": "teleport"}])


def test_runner_keepalive_uses_dedicated_scripts():
    session = _StubSession()
    runner = FlowRunner(session)
    runner.keepalive_start()
    runner.keepalive_stop()
    assert "setInterval" in session.expressions[0]
    assert "clearInterval" in session.expressions[1]


# ---------------------------------------------------------------- 客户端集成

def test_stop_recording_saves_steps(monkeypatch):
    client = ISmartClient(ISmartProfile(port=9600))
    events = [{"t": 0, "selector": "[data-scoid='a']", "text": "T"}]
    monkeypatch.setattr(client, "_eval", lambda expr, await_promise=True: events)
    monkeypatch.setattr(client.profile, "save", lambda *a, **k: None)

    steps = client.stop_recording()
    assert steps and steps[0]["selector"] == '[data-scoid="{scoid}"]'
    assert client.profile.recorded_steps == steps


def test_start_recording_reports_state(monkeypatch):
    client = ISmartClient(ISmartProfile(port=9600))
    monkeypatch.setattr(client, "_eval", lambda expr, await_promise=True: "started")
    assert client.start_recording() is True


def test_run_recorded_flow_without_recording():
    client = ISmartClient(ISmartProfile(port=9600))
    ok, message = client.run_recorded_flow("s1")
    assert ok is False
    assert "录制" in message


def test_run_recorded_flow_substitutes_task_id(monkeypatch):
    client = ISmartClient(ISmartProfile(port=9600))
    client.profile.recorded_steps = [{"kind": "click", "selector": '[data-scoid="{scoid}"]'}]
    seen = {}

    class _Runner:
        def run(self, steps, variables):
            seen["variables"] = variables
            return ["点击"]

    monkeypatch.setattr(client, "_runner", lambda: _Runner())
    ok, _ = client.run_recorded_flow("TASK-7")
    assert ok is True
    assert seen["variables"]["scoid"] == "TASK-7"


def test_submit_uses_recorded_steps_first(monkeypatch):
    client = ISmartClient(ISmartProfile(port=9600))
    client.profile.recorded_steps = [{"kind": "click", "selector": "[data-scoid='{scoid}']"}]
    called = {}

    class _Runner:
        def run(self, steps, variables):
            called["steps"] = steps
            return ["ok"]

    monkeypatch.setattr(client, "_runner", lambda: _Runner())
    way1_ok, way1_fail, _, _ = client.submit_course_progress("c1", "u", "k", "s9", 100)
    assert (way1_ok, way1_fail) == (1, 0)
    assert called["steps"] is client.profile.recorded_steps


def test_submit_falls_back_to_submit_steps(monkeypatch):
    client = ISmartClient(ISmartProfile(port=9600))
    client.profile.recorded_steps = []
    calls = []
    monkeypatch.setattr(client, "_eval", lambda expr, await_promise=True: calls.append(expr) or True)

    way1_ok, way1_fail, _, _ = client.submit_course_progress("c1", "u", "k", "s9", 100)
    assert (way1_ok, way1_fail) == (1, 0)
    assert calls  # 走了默认 submit_steps


# ---------------------------------------------------------------- 挂时长

def test_simulate_time_completes(monkeypatch):
    client = ISmartClient(ISmartProfile(port=9600))
    monkeypatch.setattr(client, "_eval", lambda expr, await_promise=True: True)
    monkeypatch.setattr(client, "_attach", lambda: type("S", (), {"send": lambda self, *a, **k: {}})())
    monkeypatch.setattr("core.platforms.ismart.time.sleep", lambda *_: None)

    class _Runner:
        def keepalive_start(self):
            pass

        def keepalive_stop(self):
            pass

    monkeypatch.setattr(client, "_runner", lambda: _Runner())
    assert client.simulate_time("c1", "u", "s1", 3) is True


def test_simulate_time_stops_on_event(monkeypatch):
    client = ISmartClient(ISmartProfile(port=9600))
    monkeypatch.setattr(client, "_eval", lambda expr, await_promise=True: True)
    monkeypatch.setattr(client, "_attach", lambda: type("S", (), {"send": lambda self, *a, **k: {}})())

    class _Runner:
        def keepalive_start(self):
            pass

        def keepalive_stop(self):
            pass

    monkeypatch.setattr(client, "_runner", lambda: _Runner())
    stop = threading.Event()
    stop.set()
    assert client.simulate_time("c1", "u", "s1", 600, stop_event=stop) is False


def test_ismart_platform_declares_duration_capability():
    assert ismart.PLATFORM.supports(ismart.Capability.DURATION)
    assert ismart.PLATFORM.supports(ismart.Capability.HOMEWORK)