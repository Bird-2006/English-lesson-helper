"""WeLearn 学习助手 - 主窗口（多账号管理中心）。

改动要点：

- 原来「启动声明」+「更新公告」两个模态框串行弹出，加上 1 秒后的未完成任务
  提示，启动阶段连弹三个 → 合并为一个对话框；
- 42 处 ``print()`` 在打包后（``console=False``）全部丢弃，改为日志；
- 最小化到托盘时同步执行 ``subprocess.run(powershell … Start-Sleep 6)``，
  主线程被阻塞 6~10 秒 → 删除，改用托盘气泡本身；
- ``closeEvent`` / ``quit_from_tray`` 各有一份近 100 行的重复收尾逻辑 →
  合并为 ``_shutdown()``；
- 每次 ``resizeEvent`` 都把 978KB 背景图重新缩放 → 缓存 + 100ms 去抖；
- 版本号、公告历史改为集中数据，窗口标题走 ``core.version``。
"""
import os
import sys
import threading

from PyQt5.QtCore import QSettings, QTimer
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStatusBar,
    QStyle,
    QSystemTrayIcon,
    QTextEdit,
    QVBoxLayout,
)

from core.logger import get_logger
from core.logger import shutdown as shutdown_logging
from core.paths import resource_path
from core.task_progress import TaskProgress
from core.version import APP_VENDOR, APP_VERSION_TAG
from ui import account_detail, account_view
from ui.helpers import app_icon, apply_background, no_help

logger = get_logger("MainWindow")

TRAY_REMIND_MS = 10 * 60 * 1000
RESIZE_DEBOUNCE_MS = 100

STARTUP_NOTICE = """使用声明：

本软件为WeLearn学习助手{version}版本。

使用条款：
1. 本软件仅供学习交流使用，严禁用于任何商业用途
2. 用户使用本软件所产生的任何后果由用户自行承担
3. 请合理使用，遵守学校相关规定

感谢您的理解与支持！"""

CHANGELOG = [
    ("V5.0.12", [
        "添加网络诊断与修复工具箱",
        "文件夹模式打包，启动速度更快",
        "默认管理员权限启动",
        "增加网络超时时间和自动重试",
        "添加重复账号检测，支持覆盖更新",
        "添加倒计时功能",
        "关闭窗口时保存任务进度",
        "打开详情页时自动检查并继续未完成任务",
        "修复声明窗口重复提示音",
        "重复账号自动高亮选中",
    ]),
    ("V5.0.11", [
        "优化倒计时计算，添加分隔线，更新公告，修复警告页面任务栏图标",
        "修复声明窗口和更新公告窗口在任务栏不显示的问题",
        "添加弹窗提示音",
        "添加失败自动重试功能（最多3次）",
    ]),
    ("V4.6.6", [
        "修复了暂停选择彻底关闭依旧无响应的问题",
        "继续任务功能存在无法修复的缺陷，已移除该功能",
    ]),
    ("V4.6.5", [
        "修复了点击\"Yes\"后没有自动打开账号详情管理页面的问题",
        "修复了选择后台运行后弹出空白PowerShell弹窗的问题",
        "修复了点击\"完全退出程序\"后程序仍在后台运行的问题",
        "优化了任务恢复逻辑，确保未完成任务能够正确继续",
    ]),
    ("V4.6.4", [
        "修复了点击\"Yes\"后没有自动打开账号详情管理页面的问题",
        "修复了选择后台运行后弹出空白PowerShell弹窗的问题",
        "修复了点击\"完全退出程序\"后程序仍在后台运行的问题",
    ]),
    ("V4.6.2", [
        "修复了暂停任务后退出程序时的线程报错问题",
        "优化了继续任务弹窗，使其与主UI一起出现并在屏幕中央",
        "修复了点击继续任务后没有自动进行任务的问题",
    ]),
    ("V4.6.1", ["修复了弹窗中预计完成时间没有精确到秒的问题"]),
    ("V4.6", [
        "修复了任务暂停后关闭程序时的线程报错问题",
        "改进了任务停止逻辑，确保进度正确保存",
        "优化了多单元并行刷时长的稳定性",
        "修复了任务进度保存功能的潜在问题",
    ]),
    ("V4.5", [
        "实现了真正的多单元并行刷时长功能，充分利用设定的并发数",
        "新增任务进度保存功能，支持中断后恢复未完成的任务",
        "新增启动时检测未完成任务并提示继续的功能",
        "优化了预计完成时间的计算，精确到秒",
    ]),
    ("V4.4", [
        "修复了打包后模块导入错误的问题，提高了程序稳定性",
        "修复了workers.py中的缩进错误，确保后台任务正常运行",
        "新增了任务开始前的弹窗提醒功能，显示选中单元数量",
        "新增了刷时长任务开始前的预计完成时间提醒",
    ]),
    ("V4.3", [
        "修复了刷新按钮无效的问题，现在点击刷新按钮会重新从JSON文件加载数据",
        "新增了程序工作日志输出到本地的功能，所有操作都会记录在logs目录下",
        "优化了账号数据加载逻辑，提高了数据同步的可靠性",
        "改进了错误处理机制，增加了详细的日志记录",
        "修复了手动修改JSON文件后UI不更新的问题",
    ]),
    ("V4.2", [
        "修复了新版本无法读取旧版本账号数据的问题",
        "修复了账号数据在UI界面不显示的问题",
        "新增了刷新账号列表按钮，方便用户手动更新账户状态",
        "优化了账号数据文件路径检测逻辑",
        "改进了打包配置，避免账号数据被覆盖",
        "增强了版本升级时的数据兼容性",
    ]),
    ("V4.1", [
        "实现了进入账号详情管理页面自动登录功能",
        "优化了用户体验，减少了手动操作步骤",
        "改进了登录流程，提高了使用便利性",
    ]),
    ("V4.0", [
        "修复了单元时长分配逻辑，确保每个单元的总时长都是用户输入的时长",
        "优化了刷时长功能，现在每个单元独立处理，不再合并所有课程",
        "改进了日志显示，更清晰地展示每个单元的处理进度",
        "提高了多单元刷时长的准确性和稳定性",
    ]),
    ("V3.9", ["移除了所有弹窗中的无用问号按钮", "优化了用户界面交互体验", "改进了弹窗的视觉一致性"]),
    ("V3.8", [
        "添加了任务完成后的提示音功能",
        "修复了打包过程中Qt5Gui.dll提取失败的问题",
        "优化了打包配置，提高了稳定性",
        "改进了用户体验，增加了音频反馈",
    ]),
    ("V3.7", [
        "修复了公告弹窗背景显示问题",
        "修复了用户未勾选不再显示但第二次打开不显示的问题",
        "修复了公告弹窗没有居中的问题",
        "优化了公告弹窗的布局和样式",
        "改进了版本更新检测机制",
        "增强了设置保存的可靠性",
    ]),
    ("V3.6", [
        "修复了公告弹窗背景显示问题",
        "修复了用户未勾选不再显示但第二次打开不显示的问题",
        "修复了公告弹窗没有居中的问题",
        "优化了公告弹窗的布局和样式",
    ]),
    ("V3.5", [
        "修复了公告弹窗背景显示问题",
        "修复了用户选择不再提示后公告仍然显示的问题",
        "为公告添加了滚动功能，提高用户体验",
        "优化了公告弹窗的布局和样式",
    ]),
    ("V3.4", [
        "修复了公告显示逻辑，现在每个新版本都会显示公告",
        "移除了关闭程序弹窗的背景样式，使用系统默认样式",
        "优化了公告弹窗的背景图片路径处理",
        "改进了版本更新时的用户体验",
    ]),
    ("V3.3", [
        "修复了公告弹窗背景显示问题",
        "优化了确定按钮的样式，使用绿色背景提高可见度",
        "统一了弹窗的背景图片处理逻辑",
        "改进了打包后的资源文件路径处理",
        "修复了弹窗文字可读性问题",
    ]),
    ("V3.1", [
        "修改了 main_window.py 中的导入语句，添加了多种导入方式的尝试",
        "在打包配置文件中添加了隐藏导入模块列表",
        "修复了 account_view.py 中的语法错误（中文引号问题）",
        "修改了 ui/workers.py 中的 calculate_unit_time 方法",
        "修复前：每个小节都获得完整的用户输入时长",
        "修复后：输入的单元时长平均分配给每个小节",
        "确保每个单元的总时长就是用户输入的时长，平均分配给每个小节",
    ]),
    ("V2.1", ["添加了Excel批量导入账号功能", "输入框设置为半透明，以显示背景图片", "修复了软件图标显示问题"]),
    ("V1.1", [
        "添加单元时长单位自选",
        "修复关闭程序导致后台运行",
        "添加后台运行彻底关闭自选",
        "默认单元时长与并行数调整为合适数值",
        "添加账号保存功能",
    ]),
]


def changelog_text() -> str:
    """把公告数据渲染成可读文本。"""
    lines = [f"WeLearn学习助手 {APP_VERSION_TAG}", ""]
    for version, items in CHANGELOG:
        lines.append(version)
        lines.extend(f"- {item}" for item in items)
        lines.append("")
    return "\n".join(lines)


class WeLearnUI(QMainWindow):
    """主窗口 = 多账号管理中心。"""

    def __init__(self):
        super().__init__()
        self.detail_dialogs = {}
        self.tray_icon = None
        self.tray_reminder_timer = None
        self.version = APP_VERSION_TAG
        self._resize_timer = None

        self.init_ui()
        self.init_tray()
        self.show_startup_dialogs()
        QTimer.singleShot(1000, self.check_incomplete_tasks)

    # ================================================================ 界面

    def init_ui(self):
        self.setWindowTitle(
            f"WeLearn学习助手 {APP_VERSION_TAG}    致力于把大学生的时间还给大学生"
        )
        self.setGeometry(100, 100, 900, 600)
        self.setMinimumSize(800, 500)

        icon = app_icon()
        if not icon.isNull():
            self.setWindowIcon(icon)
        self._load_style_sheet()
        apply_background(self)

        self.account_view = account_view.AccountView()
        self.account_view.open_detail_requested.connect(self.open_account_detail)
        self.setCentralWidget(self.account_view)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("就绪 - 添加账号开始使用")

        disclaimer = QLabel("本软件仅供学术交流，禁止用于商业途径")
        disclaimer.setStyleSheet("color: red; font-size: 14px; font-weight: bold; padding: 2px;")
        self.status_bar.addPermanentWidget(disclaimer)

        self.network_fix_btn = QPushButton("🔧 网络工具箱")
        self.network_fix_btn.setStyleSheet(
            """
            QPushButton { background-color: #2196F3; color: white; border: none;
                          padding: 4px 12px; font-size: 12px; border-radius: 3px; }
            QPushButton:hover { background-color: #1976D2; }
            """
        )
        self.network_fix_btn.clicked.connect(self.open_network_fix)
        self.status_bar.addPermanentWidget(self.network_fix_btn)


    def _load_style_sheet(self):
        path = resource_path("style.qss")
        if not path:
            logger.warning("样式表文件不存在，使用 Qt 默认外观")
            return
        try:
            with open(path, "r", encoding="utf-8") as stream:
                self.setStyleSheet(stream.read())
            logger.debug("已加载样式表: %s", path)
        except OSError as exc:
            logger.warning("读取样式表失败: %s", exc)

    def center_window(self):
        screen = QApplication.desktop().screenGeometry()
        frame = self.frameGeometry()
        self.move(
            (screen.width() - frame.width()) // 2,
            (screen.height() - frame.height()) // 2,
        )

    def show(self):
        super().show()
        QTimer.singleShot(100, self.center_window)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._resize_timer is None:
            self._resize_timer = QTimer(self)
            self._resize_timer.setSingleShot(True)
            self._resize_timer.timeout.connect(lambda: apply_background(self))
        self._resize_timer.start(RESIZE_DEBOUNCE_MS)

    # ================================================================ 设置

    @staticmethod
    def _settings() -> QSettings:
        settings_dir = os.path.join(
            os.path.expanduser("~"), "AppData", "Local", APP_VENDOR, "WeLearn学习助手"
        )
        os.makedirs(settings_dir, exist_ok=True)
        return QSettings(os.path.join(settings_dir, "settings.ini"), QSettings.IniFormat)

    # ================================================================ 启动弹窗

    def show_startup_dialogs(self):
        """声明 + 更新公告合并成一个对话框。"""
        import uuid

        settings = self._settings()
        if not settings.value("General/installation_id", "", type=str):
            settings.setValue("General/installation_id", str(uuid.uuid4()))
            settings.sync()

        dont_show = settings.value("General/dont_show_update_announcement", False, type=bool)
        announcement_shown = settings.value("General/announcement_shown", False, type=bool)
        last_version = settings.value("General/last_version", "", type=str)

        show_announcement = not (dont_show and announcement_shown and last_version == APP_VERSION_TAG)
        if dont_show and last_version != APP_VERSION_TAG:
            # 版本升级后重新询问是否忽略公告
            settings.setValue("General/dont_show_update_announcement", False)
            settings.sync()

        try:
            import winsound

            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception:
            pass

        dialog = QDialog()
        no_help(dialog)
        dialog.setWindowTitle("使用声明")
        dialog.setMinimumSize(620, 520)
        dialog.resize(620, 520)
        icon = app_icon()
        if not icon.isNull():
            dialog.setWindowIcon(icon)

        layout = QVBoxLayout(dialog)
        text = QTextEdit()
        text.setReadOnly(True)
        body = STARTUP_NOTICE.format(version=APP_VERSION_TAG)
        if show_announcement:
            body += "\n\n" + "=" * 30 + "\n更新公告\n" + "=" * 30 + "\n\n" + changelog_text()
        text.setText(body)
        layout.addWidget(text)

        checkbox = QCheckBox("不再提醒版本公告")
        checkbox.setVisible(show_announcement)
        layout.addWidget(checkbox)

        buttons = QHBoxLayout()
        buttons.addStretch()
        ok_btn = QPushButton("我已了解")
        ok_btn.setDefault(True)
        ok_btn.setMinimumWidth(96)
        ok_btn.clicked.connect(dialog.accept)
        buttons.addWidget(ok_btn)
        layout.addLayout(buttons)

        dialog.exec_()

        settings.setValue("General/announcement_shown", True)
        settings.setValue("General/last_version", APP_VERSION_TAG)
        if show_announcement and checkbox.isChecked():
            settings.setValue("General/dont_show_update_announcement", True)
        settings.sync()

    # ================================================================ 账号详情

    def open_account_detail(self, account):
        username = account.username
        existing = self.detail_dialogs.get(username)
        if existing is not None:
            if existing.isVisible():
                existing.raise_()
                existing.activateWindow()
                return
            self.detail_dialogs.pop(username, None)

        dialog = account_detail.AccountDetailDialog(account)
        dialog.status_updated.connect(self.on_account_status_updated)
        dialog.uid_resolved.connect(self.on_uid_resolved)
        dialog.finished.connect(lambda _result, name=username: self.on_detail_closed(name))
        self.detail_dialogs[username] = dialog
        dialog.show()
        self.status_bar.showMessage(f"已打开账号详情: {username}")

    def on_account_status_updated(self, username: str, status: str, progress: str):
        self.account_view.update_account_status(username, status, progress)

    def on_uid_resolved(self, username: str, uid: str):
        self.account_view.account_manager.set_uid(username, uid)

    def on_detail_closed(self, username: str):
        self.detail_dialogs.pop(username, None)
        self.account_view.refresh_table()

    def check_incomplete_tasks(self):
        """启动时检查未完成任务。

        旧实现拿 ``acc.username`` 去比数字 uid，永远匹配不上；现在按
        ``account.uid`` 匹配，未解析过 uid 的账号交给详情窗登录后再续跑。
        """
        task_progress = TaskProgress()
        task_progress.prune()
        incomplete = task_progress.get_incomplete_tasks()
        if not incomplete:
            return

        accounts = self.account_view.account_manager.get_all_accounts()
        by_uid = {str(acc.uid): acc for acc in accounts if getattr(acc, "uid", "")}
        targets = []
        for task in incomplete:
            account = by_uid.get(str(task.get("uid", "")))
            if account is not None:
                targets.append(account)
            else:
                logger.info(
                    "未找到 uid=%s 对应的已加载账号，登录后可在详情页继续",
                    task.get("uid", ""),
                )

        if not targets:
            return

        task_list = "\n".join(
            f"- {task.get('task_type', '未知')}: {task.get('task_config', {}).get('course_name', '未知课程')}"
            for task in incomplete
        )
        box = QMessageBox(QMessageBox.Question, "未完成任务",
                          f"检测到 {len(incomplete)} 个未完成的任务：\n{task_list}\n\n是否继续这些任务？")
        no_help(box)
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.Yes)

        if box.exec_() == QMessageBox.StandardButton.Yes:
            for account in targets:
                self.open_account_detail(account)
        else:
            for task in incomplete:
                task_progress.clear_task_progress(task.get("task_id", ""))
            self.status_bar.showMessage("已清除未完成任务记录")

    # ================================================================ 附加窗口

    def open_network_fix(self):
        from ui.network_fix_dialog import NetworkFixDialog

        dialog = NetworkFixDialog()
        dialog.setWindowTitle("网络诊断与修复工具箱")
        dialog.show()
        self._network_dialog = dialog  # 保住引用，避免被 GC 掉

    # ================================================================ 托盘

    def init_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            logger.warning("系统不支持托盘功能")
            return
        self.tray_icon = self.create_tray_icon()
        menu = QMenu()
        menu.addAction("显示主窗口").triggered.connect(self.show_from_tray)
        menu.addSeparator()
        quit_action = menu.addAction("完全退出程序")
        quit_action.triggered.connect(self._shutdown)
        font = quit_action.font()
        font.setBold(True)
        quit_action.setFont(font)
        self.tray_icon.setContextMenu(menu)
        self.tray_icon.setToolTip("WeLearn学习助手 - 右键退出程序")
        self.tray_icon.activated.connect(self.on_tray_icon_activated)
        self.tray_icon.show()

    def create_tray_icon(self) -> QSystemTrayIcon:
        icon = app_icon()
        if icon.isNull():
            icon = self.style().standardIcon(QStyle.SP_ComputerIcon)
        return QSystemTrayIcon(icon, self)

    def show_from_tray(self):
        self.show()
        self.raise_()
        self.activateWindow()
        self.stop_tray_reminder()

    def start_tray_reminder(self):
        self.stop_tray_reminder()
        self.tray_reminder_timer = QTimer(self)
        self.tray_reminder_timer.timeout.connect(self.show_tray_reminder)
        self.tray_reminder_timer.start(TRAY_REMIND_MS)
        logger.info("已启动托盘提醒定时器")

    def stop_tray_reminder(self):
        if self.tray_reminder_timer and self.tray_reminder_timer.isActive():
            self.tray_reminder_timer.stop()
            self.tray_reminder_timer = None

    def show_tray_reminder(self):
        if self.tray_icon and not self.isVisible():
            self.tray_icon.showMessage(
                "WeLearn学习助手仍在后台运行",
                "双击托盘图标显示主窗口，或右键选择'完全退出程序'",
                QSystemTrayIcon.Information,
                8000,
            )

    def on_tray_icon_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            if self.isVisible():
                self.hide()
            else:
                self.show_from_tray()

    # ================================================================ 关闭

    def closeEvent(self, event):
        box = QMessageBox(QMessageBox.Question, "关闭程序", "您希望如何关闭程序？")
        no_help(box)
        background_btn = box.addButton("后台运行", QMessageBox.YesRole)
        close_btn = box.addButton("完全关闭", QMessageBox.NoRole)
        cancel_btn = box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(close_btn)

        center = self.frameGeometry().center()
        size = box.sizeHint()
        box.move(center.x() - size.width() // 2, center.y() - size.height() // 2)
        box.exec_()

        clicked = box.clickedButton()
        if clicked is cancel_btn:
            logger.info("用户取消关闭程序")
            event.ignore()
            return
        if clicked is background_btn:
            logger.info("用户选择后台运行")
            self.hide()
            for username, dialog in self.detail_dialogs.items():
                logger.info("隐藏账号详情对话框: %s", username)
                dialog.hide()
            if self.tray_icon:
                self.tray_icon.showMessage(
                    "WeLearn学习助手已最小化到系统托盘",
                    "程序仍在后台运行！\n右键点击托盘图标选择'退出程序'可完全关闭",
                    QSystemTrayIcon.Information,
                    5000,
                )
                self.start_tray_reminder()
            event.ignore()
            return

        event.accept()
        self._shutdown()

    def _shutdown(self):
        """统一的退出收尾：停任务、关窗、收托盘、刷日志、强退。"""
        logger.info("开始退出程序，活动线程数: %d", threading.active_count())
        self.stop_tray_reminder()

        for username, dialog in list(self.detail_dialogs.items()):
            try:
                logger.info("关闭账号详情对话框: %s", username)
                dialog.closeEvent(None)
                dialog.close()
            except Exception as exc:
                logger.error("关闭账号详情对话框时出错 %s: %s", username, exc)
        self.detail_dialogs.clear()

        if self.tray_icon:
            self.tray_icon.hide()

        # 退出前把内存里的账号状态落盘
        try:
            self.account_view.account_manager.save_accounts()
        except Exception as exc:
            logger.error("退出时保存账号信息失败: %s", exc)

        app = QApplication.instance()
        if app is not None:
            app.quit()

        shutdown_logging()  # 刷日志，避免丢最后几行

        if sys.platform == "win32":
            os._exit(0)
        sys.exit(0)


__all__ = ["WeLearnUI", "CHANGELOG", "changelog_text", "STARTUP_NOTICE"]