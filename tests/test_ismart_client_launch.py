"""客户端启动：UAC 提权回退、已运行检测、参数组装。"""
import pytest

from core.platforms import ismart
from core.platforms.ismart import client_running, launch_client


def _stub(tmp_path, monkeypatch, popen_error=None, port_ok=True, running=False, shell_ok=True):
    exe = tmp_path / "iSmart.exe"
    exe.write_text("stub", encoding="utf-8")

    captured = {"popen": None, "shell": None}

    def fake_popen(args, **kwargs):
        captured["popen"] = args
        if popen_error is not None:
            raise popen_error

    def fake_shell(exe_path, arguments):
        captured["shell"] = (exe_path, arguments)
        return shell_ok

    monkeypatch.setattr(ismart.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(ismart, "_shell_execute_runas", fake_shell)
    monkeypatch.setattr(ismart, "client_running", lambda *a, **k: running)
    monkeypatch.setattr(ismart, "probe_cdp_port", lambda port, timeout=1.0: {"Browser": "x"} if port_ok else None)
    monkeypatch.setattr(ismart.time, "sleep", lambda *_: None)
    return str(exe), captured


def test_launch_client_popen_success(tmp_path, monkeypatch):
    exe, captured = _stub(tmp_path, monkeypatch)
    ok, message = launch_client(9222, exe, wait_seconds=1)
    assert ok is True
    assert captured["popen"][0] == exe
    assert "--remote-debugging-port=9222" in captured["popen"]
    assert captured["shell"] is None
    assert "9222" in message


def test_launch_client_falls_back_to_uac_on_740(tmp_path, monkeypatch):
    """WinError 740 = 目标程序要求提升权限 → 用 ShellExecuteW runas 再试一次。"""
    error = OSError(740, "请求的操作需要提升。")
    exe, captured = _stub(tmp_path, monkeypatch, popen_error=error)
    ok, message = launch_client(9222, exe, wait_seconds=1)
    assert captured["shell"] is not None
    assert captured["shell"][0] == exe
    assert "--remote-debugging-port=9222" in captured["shell"][1]
    assert ok is True
    assert "提权" in message


def test_launch_client_reports_when_uac_refused(tmp_path, monkeypatch):
    error = OSError(740, "请求的操作需要提升。")
    exe, _ = _stub(tmp_path, monkeypatch, popen_error=error, shell_ok=False)
    ok, message = launch_client(9222, exe, wait_seconds=1)
    assert ok is False
    assert "管理员权限" in message
    assert "UAC" in message


def test_launch_client_other_oserror_is_not_retried_as_uac(tmp_path, monkeypatch):
    error = OSError(5, "拒绝访问")
    exe, captured = _stub(tmp_path, monkeypatch, popen_error=error)
    ok, message = launch_client(9222, exe, wait_seconds=1)
    assert ok is False
    assert captured["shell"] is None
    assert "启动客户端失败" in message


def test_launch_client_warns_when_already_running_without_port(tmp_path, monkeypatch):
    exe, captured = _stub(tmp_path, monkeypatch, port_ok=False, running=True)
    ok, message = launch_client(9222, exe, wait_seconds=1)
    assert ok is False
    assert "已经在运行" in message
    assert "退出客户端" in message
    assert captured["popen"] is None  # 不该再启一个实例


def test_launch_client_times_out_without_port(tmp_path, monkeypatch):
    exe, _ = _stub(tmp_path, monkeypatch, port_ok=False)
    ok, message = launch_client(9222, exe, wait_seconds=0.2)
    assert ok is False
    assert "未就绪" in message


def test_launch_client_without_executable(monkeypatch):
    monkeypatch.setattr(ismart, "find_client_executable", lambda: None)
    ok, message = launch_client(9222, None, wait_seconds=1)
    assert ok is False
    assert "未找到" in message


def test_client_running_parses_tasklist(monkeypatch):
    class _Result:
        stdout = "iSmart.exe                   12345 Console                1     45,678 K"

    monkeypatch.setattr(ismart.subprocess, "run", lambda *a, **k: _Result())
    assert client_running() is True


def test_client_running_returns_false_when_absent(monkeypatch):
    class _Result:
        stdout = "信息: 没有运行的任务匹配指定标准。"

    monkeypatch.setattr(ismart.subprocess, "run", lambda *a, **k: _Result())
    assert client_running() is False


def test_client_running_tolerates_failure(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("tasklist 不存在")

    monkeypatch.setattr(ismart.subprocess, "run", boom)
    assert client_running() is False


@pytest.mark.parametrize("port", [9222, 9223])
def test_launch_client_passes_requested_port(port, tmp_path, monkeypatch):
    exe, captured = _stub(tmp_path, monkeypatch)
    launch_client(port, exe, wait_seconds=1)
    assert f"--remote-debugging-port={port}" in captured["popen"]