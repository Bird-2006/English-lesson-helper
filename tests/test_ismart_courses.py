"""学生中心课程卡片闭环：识别 → 进课程 → 提交 → 回列表；缺 DOM 时的退化路径。"""
import pytest

from core.platforms import ismart
from core.platforms.ismart import ISmartClient, ISmartProfile

LOCATION_KEY = "location.href"
CLICK_KEY = "const target = String("
EXPECT_KEY = "提交成功|保存成功"


class _Seq:
    """按调用顺序返回的一组值（用完后重复最后一个）。"""

    def __init__(self, *values):
        self.values = list(values)

    def next(self):
        if not self.values:
            return None
        return self.values.pop(0) if len(self.values) > 1 else self.values[0]


class _Script:
    """按表达式内容返回预设值。"""

    def __init__(self, mapping):
        self.mapping = dict(mapping)
        self.calls = []

    def __call__(self, expression, await_promise=True):
        self.calls.append(expression)
        for key, value in self.mapping.items():
            if key in expression:
                return value.next() if isinstance(value, _Seq) else value
        return None


def _seq(*values):
    return _Seq(*values)


def _client(script, monkeypatch, tmp_path):
    monkeypatch.setenv("ELH_ISMART_PROFILE", str(tmp_path / "p.json"))
    client = ISmartClient(ISmartProfile(port=9222))
    monkeypatch.setattr(client, "_eval", script)
    monkeypatch.setattr(ismart.time, "sleep", lambda *_: None)
    return client


def test_get_courses_records_list_url(monkeypatch, tmp_path):
    script = _Script(
        {
            "const LABELS = ['进入课程'": [
                {"cid": "card:0", "name": "周三5-6节（徐辉 老师）", "per": ""}
            ],
            LOCATION_KEY: "https://me.example.com/center/student/index.html?studentId=1",
        }
    )
    client = _client(script, monkeypatch, tmp_path)
    ok, courses, _ = client.get_courses()
    assert ok and courses[0]["cid"] == "card:0"
    assert courses[0]["per"] == ""
    assert client._list_url.endswith("studentId=1")


def test_course_info_falls_back_to_single_unit(monkeypatch, tmp_path):
    client = _client(_Script({"return { uid": {"uid": "u", "classid": "k", "units": []}}), monkeypatch, tmp_path)
    ok, info, _ = client.get_course_info("card:3")
    assert ok is True
    assert info["units"][0]["id"] == "card:3"
    assert info["units"][0]["name"] == "整门课程"


def test_sco_leaves_falls_back_to_card_as_task(monkeypatch, tmp_path):
    client = _client(_Script({"const out = [], seen": []}), monkeypatch, tmp_path)
    ok, tasks, _ = client.get_sco_leaves("card:2", "uid", "k", 0)
    assert ok is True
    assert tasks[0]["id"] == "card:2"


def test_click_course_waits_for_navigation(monkeypatch, tmp_path):
    script = _Script(
        {
            LOCATION_KEY: _seq("https://x/center", "https://x/course?c=0"),
            CLICK_KEY: True,
        }
    )
    client = _client(script, monkeypatch, tmp_path)
    assert client.click_course("card:0") is True
    assert any(CLICK_KEY in call for call in script.calls)


def test_click_course_reports_missing_card(monkeypatch, tmp_path):
    client = _client(_Script({CLICK_KEY: False}), monkeypatch, tmp_path)
    assert client.click_course("card:9") is False


def test_click_course_non_card_id_just_clicks(monkeypatch, tmp_path):
    client = _client(_Script({CLICK_KEY: True}), monkeypatch, tmp_path)
    assert client.click_course("s123") is True


def test_return_to_list_without_url(monkeypatch, tmp_path):
    client = _client(_Script({}), monkeypatch, tmp_path)
    assert client.return_to_list() is False


def test_return_to_list_navigates_iframe(monkeypatch, tmp_path):
    """内容在 iframe 里：回列表是改 iframe location，不是整页重载。"""
    recorder = _Script({})
    client = _client(recorder, monkeypatch, tmp_path)
    client._list_url = "https://x/center/courses.html"
    client._outer_url = "https://x/center/index.html"
    sent = []

    class _Session:
        def send(self, method, params=None):
            sent.append((method, params))
            return {}

    monkeypatch.setattr(client, "_attach", lambda: _Session())
    assert client.return_to_list() is True
    assert any("courses.html" in call for call in recorder.calls)
    assert sent == []  # 不该调用 Page.navigate


def test_run_steps_card_mode_skips_click_task(monkeypatch, tmp_path):
    client = _client(
        _Script({CLICK_KEY: True, "const want": True, EXPECT_KEY: True}), monkeypatch, tmp_path
    )
    client.profile.recorded_steps = []
    entered = []
    monkeypatch.setattr(client, "click_course", lambda cid, wait_seconds=12.0: entered.append(cid) or True)

    logs = client._run_steps("card:0")
    assert entered == ["card:0"]
    # 默认步骤里的 click_task 应该被跳过（否则会把卡片再点一次）
    assert not any("已点击任务" in line for line in logs)
    assert any("已检测到成功标志" in line for line in logs)


def test_submit_card_mode_returns_to_list(monkeypatch, tmp_path):
    client = _client(
        _Script({CLICK_KEY: True, "const want": True, EXPECT_KEY: True}), monkeypatch, tmp_path
    )
    client.profile.recorded_steps = []
    monkeypatch.setattr(client, "click_course", lambda cid, wait_seconds=12.0: True)
    back = []
    monkeypatch.setattr(client, "return_to_list", lambda: back.append(True) or True)

    way1, way1_fail, _, _ = client.submit_course_progress("card:0", "u", "k", "card:0", 100)
    assert (way1, way1_fail) == (1, 0)
    assert back == [True]


def test_submit_card_mode_returns_even_on_failure(monkeypatch, tmp_path):
    client = _client(_Script({CLICK_KEY: True}), monkeypatch, tmp_path)
    client.profile.recorded_steps = []
    monkeypatch.setattr(client, "click_course", lambda cid, wait_seconds=12.0: False)
    back = []
    monkeypatch.setattr(client, "return_to_list", lambda: back.append(True) or True)

    way1, way1_fail, _, _ = client.submit_course_progress("card:0", "u", "k", "card:0", 100)
    assert (way1, way1_fail) == (0, 1)
    assert back == [True]


@pytest.mark.parametrize("cid", ["card:0", "card:12"])
def test_card_id_shape(cid, monkeypatch, tmp_path):
    client = _client(_Script({"const out = [], seen": []}), monkeypatch, tmp_path)
    ok, tasks, _ = client.get_sco_leaves(cid, "u", "k", 0)
    assert ok and tasks[0]["id"] == cid