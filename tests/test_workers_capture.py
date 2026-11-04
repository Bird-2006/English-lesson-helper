"""界面展示与线程联调：进度列不能显示主讲、抓接口线程要用 bool 停止标志。"""
import pytest

pytest.importorskip("PyQt5")

from PyQt5.QtWidgets import QApplication  # noqa: E402

from ui import workers  # noqa: E402


@pytest.fixture(scope="module")
def app():
    existing = QApplication.instance()
    if existing is None:
        existing = QApplication([])
    return existing


class _FakeCourseClient:
    def __init__(self):
        self.calls = []

    def capture_network(self, seconds, should_stop=None):
        self.calls.append((seconds, should_stop()))
        return {"requests": [], "seconds": seconds, "saved_to": "/tmp/api.json"}


def test_capture_thread_passes_bool_stop_flag(app):
    client = _FakeCourseClient()
    thread = workers.LaunchClientThread(client, mode="capture", seconds=5)
    results = []
    thread.result_ready.connect(lambda ok, message: results.append((ok, message)))

    thread.run()  # 同步跑，避免测试里等线程

    assert client.calls and client.calls[0][0] == 5
    assert client.calls[0][1] is False  # stop_requested 是 bool，不是 Event
    assert results and results[0][0] is True


def test_capture_thread_without_capture_support(app):
    class _NoCapture:
        pass

    thread = workers.LaunchClientThread(_NoCapture(), mode="capture", seconds=1)
    results = []
    thread.result_ready.connect(lambda ok, message: results.append((ok, message)))
    thread.run()
    assert results[0][0] is False
    assert "不支持" in results[0][1]


def test_capture_thread_reports_exception(app):
    class _Broken:
        def capture_network(self, seconds, should_stop=None):
            raise RuntimeError("boom")

    thread = workers.LaunchClientThread(_Broken(), mode="capture", seconds=1)
    results = []
    thread.result_ready.connect(lambda ok, message: results.append((ok, message)))
    thread.run()
    assert results[0][0] is False
    assert "boom" in results[0][1]