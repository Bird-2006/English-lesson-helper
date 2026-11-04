"""平台适配层测试：注册表、WeLearn 契约、iSmart（无客户端时的失败路径）、配置与步骤引擎。"""
import json
import os
import sys

import pytest

from core.platforms import (
    CLIENTS,
    DEFAULT_PLATFORM,
    PLATFORMS,
    create_client,
    get_info,
    key_from_name,
    name_from_key,
    platform_names,
    supports,
)
from core.platforms.base import Capability, PlatformClient
from core.platforms.ismart import (
    ISmartClient,
    ISmartError,
    ISmartProfile,
    discover_pages,
)
from core.platforms.welearn import PLATFORM as WELEARN_INFO

# ---------------------------------------------------------------- 注册表

def test_registry_exposes_both_platforms():
    keys = [info.key for info in PLATFORMS]
    assert keys == ["welearn", "ismart"]
    assert set(CLIENTS) == set(keys)


def test_create_client_returns_platform_contract():
    for key in ("welearn", "ismart"):
        client = create_client(key)
        try:
            assert isinstance(client, PlatformClient)
            assert client.info.key == key
            for method in (
                "login",
                "close",
                "get_courses",
                "get_course_info",
                "get_sco_leaves",
                "submit_course_progress",
                "simulate_time",
            ):
                assert callable(getattr(client, method))
        finally:
            client.close()


def test_create_client_falls_back_to_default():
    assert create_client("no-such-platform").info.key == DEFAULT_PLATFORM


def test_name_and_key_roundtrip():
    for info in PLATFORMS:
        assert key_from_name(info.name) == info.key
        assert name_from_key(info.key) == info.name
    assert name_from_key("unknown") == WELEARN_INFO.name
    assert key_from_name("不存在") == DEFAULT_PLATFORM


def test_capabilities():
    assert get_info("welearn").supports(Capability.HOMEWORK)
    assert get_info("welearn").supports(Capability.DURATION)
    assert not get_info("welearn").supports(Capability.CDP)
    assert get_info("ismart").supports(Capability.HOMEWORK)
    assert get_info("ismart").supports(Capability.CDP)
    # iSmart 的「挂时长」靠保持任务页活跃实现（不依赖 ut 签名），因此也声明 DURATION
    assert get_info("ismart").supports(Capability.DURATION)
    assert supports("ismart", Capability.DURATION) is True


def test_platform_names_are_unique():
    names = platform_names()
    assert len(names) == len(set(names)) == 2


# ---------------------------------------------------------------- iSmart：无客户端

def test_ismart_login_reports_missing_debug_port():
    client = ISmartClient(ISmartProfile(port=1, host_keyword="ismart"))
    ok, message = client.login("user", "pass")
    assert ok is False
    assert "9222" not in message or "端口" in message
    assert "1" in message
    # 独立错误码：界面会把这条可操作提示原样展示，而不是「无法连接服务器」
    assert client.last_error_code == "cdp"
    client.close()


def test_ismart_simulate_time_reports_cdp_error_without_page():
    """没有可附着的页面时，挂时长要干净地失败并标 cdp 错误码。"""
    client = ISmartClient(ISmartProfile(port=1))
    assert client.simulate_time(1, "2", "3", 1) is False
    assert client.last_error_code == "cdp"
    client.close()


def test_ismart_get_courses_fails_cleanly_without_client():
    client = ISmartClient(ISmartProfile(port=1))
    ok, courses, message = client.get_courses()
    assert ok is False
    assert courses == []
    assert message
    client.close()


def test_discover_pages_raises_ismart_error():
    with pytest.raises(ISmartError) as excinfo:
        discover_pages(port=1, timeout=0.2)
    assert "调试端口" in str(excinfo.value)


# ---------------------------------------------------------------- 配置

def test_profile_roundtrip(tmp_path):
    path = tmp_path / "ismart_profile.json"
    profile = ISmartProfile()
    profile.port = 9333
    profile.host_keyword = "example"
    profile.submit_steps = [{"kind": "wait_ms", "ms": 10}]
    profile.save(str(path))

    loaded = ISmartProfile.load(str(path))
    assert loaded.port == 9333
    assert loaded.host_keyword == "example"
    assert loaded.submit_steps == [{"kind": "wait_ms", "ms": 10}]
    assert loaded.courses_js  # 默认脚本仍在


def test_profile_load_creates_default_file(tmp_path):
    path = tmp_path / "sub" / "ismart_profile.json"
    profile = ISmartProfile.load(str(path))
    assert profile.port == 9222
    assert os.path.exists(str(path))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["port"] == 9222


def test_profile_ignores_unknown_keys(tmp_path):
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"port": 1234, "unknown": 1}), encoding="utf-8")
    profile = ISmartProfile.load(str(path))
    assert profile.port == 1234
    assert not hasattr(profile, "unknown")


# ---------------------------------------------------------------- 步骤引擎

class _StubSession:
    """替身 CDP 会话：按顺序返回预设结果，并记录执行过的脚本。"""

    def __init__(self, results):
        self.results = list(results)
        self.scripts = []

    def evaluate(self, expression, await_promise=True):
        self.scripts.append(expression)
        return self.results.pop(0) if self.results else True

    def close(self):
        pass


def _client_with_stub(profile, results) -> ISmartClient:
    client = ISmartClient(profile)
    client._session = _StubSession(results)
    return client


def test_submit_steps_success_path():
    profile = ISmartProfile(
        submit_steps=[
            {"kind": "click_task", "scoid": "{scoid}"},
            {"kind": "wait_ms", "ms": 1},
            {"kind": "click_text", "text": "提交"},
            {"kind": "expect_ok"},
        ]
    )
    client = _client_with_stub(profile, [True, True, True])
    counts = client.submit_course_progress(1, "2", "3", "sco-9", 90)
    assert counts[0] == 1 and counts[1] == 0
    assert any("sco-9" in script for script in client._session.scripts)


def test_submit_steps_failure_is_reported():
    profile = ISmartProfile(
        submit_steps=[{"kind": "click_task", "scoid": "{scoid}"}]
    )
    client = _client_with_stub(profile, [False])
    counts = client.submit_course_progress(1, "2", "3", "sco-9", 90)
    assert counts[1] == 1 and counts[0] == 0
    assert client.last_error_code == "parse"


def test_unknown_step_kind_is_rejected():
    profile = ISmartProfile(submit_steps=[{"kind": "teleport"}])
    client = _client_with_stub(profile, [])
    counts = client.submit_course_progress(1, "2", "3", "sco", 90)
    assert counts[1] == 1


def test_get_courses_normalizes_stub_output():
    profile = ISmartProfile()
    client = _client_with_stub(profile, [[{"cid": "11", "name": "Book A", "per": ""}]])
    ok, courses, message = client.get_courses()
    assert ok is True
    assert courses[0]["cid"] == "11"
    assert message


def test_get_courses_empty_result_gives_actionable_message():
    profile = ISmartProfile()
    client = _client_with_stub(profile, [[]])
    ok, courses, message = client.get_courses()
    assert ok is False
    # 现在的提示会区分「登录页」与「不在列表页」，并指向刷新课程 / 页面自检
    assert "刷新课程" in message
    assert "页面自检" in message


def test_get_course_info_falls_back_to_placeholder_ids():
    profile = ISmartProfile()
    client = _client_with_stub(profile, [{"units": [{"id": "u1", "name": "Unit 1"}]}])
    ok, data, _ = client.get_course_info(1)
    assert ok is True
    assert data["uid"] == "cdp"
    assert data["classid"] == "cdp"
    assert data["units"][0]["name"] == "Unit 1"


def test_dump_page_writes_html(tmp_path, monkeypatch):
    profile = ISmartProfile()

    class _DumpSession(_StubSession):
        def screenshot(self):
            return b"\x89PNG"

    client = ISmartClient(profile)
    client._session = _DumpSession(["<html></html>"])
    monkeypatch.setattr(
        "core.platforms.ismart.ISmartProfile.path", staticmethod(lambda: str(tmp_path / "p.json"))
    )
    path = client.dump_page("tester")
    assert path.endswith(".html")
    assert os.path.exists(path)
    assert "<html>" in open(path, encoding="utf-8").read()


# ---------------------------------------------------------------- 兼容层

def test_core_api_shim_still_works():
    from core.api import WeLearnClient as ShimClient
    from core.api import parse_prelogin_url

    assert ShimClient is not None
    assert parse_prelogin_url(
        "https://x/authorize?code_challenge=c&code_challenge_method=S256&state=s"
    ) == ("c", "s")
    assert sys.modules["core.api"].__doc__