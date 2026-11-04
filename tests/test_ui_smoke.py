"""无界面（offscreen）冒烟测试：确保窗口能构建、关键信号链路不抛异常。

重点回归两处旧问题：

- ``_auto_start_tick`` 里引用未定义的 ``logger``，每次都抛 ``NameError``；
- 进度回调每条消息都触发「写 JSON + 读 JSON + 整表重建」，这里验证状态信号
  已经被合并。
"""
import os

import pytest

pytest.importorskip("PyQt5")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QSize  # noqa: E402  (必须在设置环境变量之后导入)
from PyQt5.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="session")
def app():
    instance = QApplication.instance()
    if instance is None:
        instance = QApplication([])
    yield instance


@pytest.fixture()
def manager(tmp_path):
    from core.account_manager import AccountManager

    instance = AccountManager(data_file=str(tmp_path / "accounts.json"))
    instance.add_account("tester", "secret", "测试号")
    return instance


class _FakeEvent:
    def __init__(self):
        self.accepted = False
        self.ignored = False

    def accept(self):
        self.accepted = True

    def ignore(self):
        self.ignored = True


class _FakeBox:
    """替身 QMessageBox：不弹模态框，直接返回预设按钮。"""

    Question = Warning = Information = 0
    YesRole = 1
    NoRole = 2
    RejectRole = 3
    pick = 0

    def __init__(self, *args, **kwargs):
        self.buttons = []

    def addButton(self, text, role):
        button = (text, role)
        self.buttons.append(button)
        return button

    def setDefaultButton(self, button):
        pass

    def windowFlags(self):
        return 0

    def setWindowFlags(self, flags):
        pass

    def sizeHint(self):
        return QSize(200, 100)

    def move(self, *args):
        pass

    def exec_(self):
        return 0

    def clickedButton(self):
        return self.buttons[_FakeBox.pick]


def _make_window(monkeypatch, manager):
    import ui.account_view as account_view
    import ui.main_window as main_window

    monkeypatch.setattr(account_view, "AccountManager", lambda *a, **k: manager)
    monkeypatch.setattr(main_window.WeLearnUI, "show_startup_dialogs", lambda self: None)
    monkeypatch.setattr(main_window.WeLearnUI, "check_incomplete_tasks", lambda self: None)
    window = main_window.WeLearnUI()
    window.show()
    QApplication.processEvents()
    return window, main_window


def test_account_view_builds_and_lists_accounts(app, monkeypatch, manager):
    import ui.account_view as account_view

    monkeypatch.setattr(account_view, "AccountManager", lambda *a, **k: manager)
    view = account_view.AccountView()
    assert view.account_table.rowCount() == 1
    assert view.account_table.item(0, account_view.COL_USERNAME).text() == "tester"
    assert view.account_table.item(0, account_view.COL_PLATFORM).text().startswith("WeLearn")

    view.update_account_status("tester", "运行中", "进度 50%", persist=False)
    assert view.account_table.item(0, account_view.COL_STATUS).text() == "运行中"
    assert view.account_table.item(0, account_view.COL_PROGRESS).text() == "进度 50%"


def test_excel_import_rejects_unsupported_suffix(app, monkeypatch, manager, tmp_path):
    import ui.account_view as account_view

    monkeypatch.setattr(account_view, "AccountManager", lambda *a, **k: manager)
    view = account_view.AccountView()
    bad = tmp_path / "accounts.xls"
    bad.write_bytes(b"not really excel")
    count, error = view.import_from_excel(str(bad))
    assert count == 0
    assert "xlsx" in error


def test_excel_import_reads_accounts(app, monkeypatch, manager, tmp_path):
    from openpyxl import Workbook

    import ui.account_view as account_view

    monkeypatch.setattr(account_view, "AccountManager", lambda *a, **k: manager)
    view = account_view.AccountView()

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["用户名", "密码", "昵称", "平台"])
    sheet.append(["excel_user", "excel_pass", "表格号", "welearn"])
    sheet.append(["ismart_user", "ismart_pass", "智能号", "iSmart"])
    path = tmp_path / "accounts.xlsx"
    workbook.save(path)

    count, error = view.import_from_excel(str(path))
    assert (count, error) == (2, "")
    assert manager.get_account("excel_user").password == "excel_pass"
    assert manager.get_account("ismart_user").platform == "ismart"


def test_detail_dialog_welearn_shows_both_modes(app, monkeypatch, manager):
    import ui.account_detail as account_detail
    from core.constants import TaskType

    monkeypatch.setattr(account_detail.AccountDetailDialog, "do_login", lambda self: None)
    dialog = account_detail.AccountDetailDialog(manager.get_account("tester"))
    dialog.show()
    QApplication.processEvents()
    assert dialog.start_btn.isEnabled() is False
    assert dialog.mode_combo.currentText() == TaskType.HOMEWORK
    assert dialog.mode_combo.count() == 2
    assert dialog.client.info.key == "welearn"
    dialog.close()


def test_detail_dialog_ismart_hides_duration_and_adds_dump(app, monkeypatch, tmp_path):
    import ui.account_detail as account_detail
    from core.account_manager import AccountManager
    from core.constants import TaskType

    manager = AccountManager(data_file=str(tmp_path / "accounts.json"))
    manager.add_account("smart", "pass", "智能号", "ismart")
    monkeypatch.setattr(account_detail.AccountDetailDialog, "do_login", lambda self: None)
    dialog = account_detail.AccountDetailDialog(manager.get_account("smart"))
    dialog.show()
    QApplication.processEvents()

    assert dialog.client.info.key == "ismart"
    assert dialog.mode_combo.count() == 1
    assert dialog.mode_combo.currentText() == TaskType.HOMEWORK
    assert hasattr(dialog, "dump_btn")
    assert hasattr(dialog, "launch_btn")
    assert "iSmart" in dialog.windowTitle()
    dialog.close()


def test_auto_start_tick_does_not_raise_name_error(app, monkeypatch, manager):
    import ui.account_detail as account_detail

    monkeypatch.setattr(account_detail.AccountDetailDialog, "do_login", lambda self: None)
    dialog = account_detail.AccountDetailDialog(manager.get_account("tester"))

    calls = {"started": 0}
    monkeypatch.setattr(
        dialog, "start_study", lambda: calls.__setitem__("started", calls["started"] + 1)
    )

    dialog._countdown_seconds = 2
    dialog._auto_start_tick()  # 2 -> 1，不应抛异常
    assert calls["started"] == 0
    dialog._auto_start_tick()  # 1 -> 0，触发启动
    assert calls["started"] == 1


def test_status_updates_are_coalesced(app, monkeypatch, manager):
    import ui.account_detail as account_detail

    monkeypatch.setattr(account_detail.AccountDetailDialog, "do_login", lambda self: None)
    dialog = account_detail.AccountDetailDialog(manager.get_account("tester"))

    received = []
    dialog.status_updated.connect(lambda *args: received.append(args))
    for _ in range(5):
        dialog.update_status("运行中", "start")
    dialog._flush_status()
    assert len(received) == 1


def test_progress_events_update_progress_bar(app, monkeypatch, manager):
    import ui.account_detail as account_detail
    from core.constants import ProgressEvent

    monkeypatch.setattr(account_detail.AccountDetailDialog, "do_login", lambda self: None)
    dialog = account_detail.AccountDetailDialog(manager.get_account("tester"))

    dialog.on_progress_update(ProgressEvent.PROGRESS_TOTAL, "4")
    dialog.on_progress_update(ProgressEvent.PROGRESS_CURRENT, "2")
    assert dialog.progress_total == 4
    assert dialog.progress_current == 2
    assert dialog.progress_bar.value() == 2

    dialog.on_progress_update(ProgressEvent.INFO, "普通日志")
    assert dialog.log_text.toPlainText().endswith("普通日志")


def test_main_window_builds(app, monkeypatch, manager):
    from PyQt5.QtWidgets import QSystemTrayIcon

    window, _ = _make_window(monkeypatch, manager)
    assert window.account_view.account_table.rowCount() == 1
    if QSystemTrayIcon.isSystemTrayAvailable():
        assert window.tray_icon is not None
    window.hide()


def test_close_event_cancel_keeps_running(app, monkeypatch, manager):
    window, main_window = _make_window(monkeypatch, manager)
    monkeypatch.setattr(main_window, "QMessageBox", _FakeBox)
    _FakeBox.pick = 2  # 取消

    event = _FakeEvent()
    window.closeEvent(event)
    assert event.ignored is True
    assert event.accepted is False
    window.hide()


def test_close_event_background_hides_window(app, monkeypatch, manager):
    window, main_window = _make_window(monkeypatch, manager)
    monkeypatch.setattr(main_window, "QMessageBox", _FakeBox)
    _FakeBox.pick = 0  # 后台运行

    event = _FakeEvent()
    window.closeEvent(event)
    assert event.ignored is True
    assert window.isVisible() is False


def test_close_event_quit_runs_shutdown(app, monkeypatch, manager):
    window, main_window = _make_window(monkeypatch, manager)
    monkeypatch.setattr(main_window, "QMessageBox", _FakeBox)
    calls = []
    monkeypatch.setattr(window, "_shutdown", lambda: calls.append("shutdown"))
    _FakeBox.pick = 1  # 完全关闭

    event = _FakeEvent()
    window.closeEvent(event)
    assert event.accepted is True
    assert calls == ["shutdown"]
    window.hide()


def test_network_fix_dialog_builds(app):
    import ui.network_fix_dialog as dialog_module

    dialog = dialog_module.NetworkFixDialog()
    assert dialog.fix_btn.isEnabled() is False
    assert dialog.diagnose_btn.isEnabled() is True
    dialog.close()


def test_changelog_renders(app):
    from ui.main_window import changelog_text

    text = changelog_text()
    assert "V5.0.12" in text
    assert "添加网络诊断与修复工具箱" in text