"""账号详情对话框：单账号的登录、选课、选单元、刷作业 / 刷时长。

合并版改动：客户端不再硬编码 WeLearn，改为按账号所属平台从
``core.platforms`` 创建；界面按平台能力（是否支持刷时长、是否 CDP 驱动）
决定显示哪些控件。

历次修复（保留说明）：

- ``_auto_start_tick`` 里引用了未定义的 ``logger``（日志有实证：刷作业→刷时长
  自动衔接时每秒抛一次 ``NameError``，被全局 excepthook 转成弹窗），并且每
  tick 都把 ``self.study_thread`` 置空 → 已修；
- 停止任务不再 ``QThread.terminate()`` / ``os.kill(threadId)``，改为协作式
  停止，1 秒内收工且状态、进度都能落盘；
- 每条进度消息都会触发主窗口「写 JSON + 读 JSON + 整表重建」，刷时长时每秒
  上百次 → 现在状态信号做 300ms 合并；
- 刷时长完成提示读的 ``completed_units`` 上游根本不发 → 上游已补，这里按新
  字段展示；
- 任务正常结束后调用 ``mark_task_completed``，不再让 task_progress.json 无限
  堆积过期任务；
- 日志框限制最大行数；移除 ``QtMultimedia`` 依赖（只在兜底播放提示音时用到）。
"""
from typing import Dict, List, Optional

from PyQt5.QtCore import QElapsedTimer, Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.account_manager import Account
from core.constants import AccountStatus, ErrCode, ProgressEvent, TaskType
from core.logger import get_logger
from core.platforms import create_client, get_info
from core.platforms.base import Capability
from core.task_progress import TaskProgress
from ui import workers
from ui.helpers import apply_background, no_help

logger = get_logger("AccountDetail")

LOG_MAX_BLOCKS = 5000
STATUS_FLUSH_MS = 300
COUNTDOWN_TICK_MS = 1000
AUTO_CHAIN_SECONDS = 5

MODE_HOMEWORK = TaskType.HOMEWORK
MODE_DURATION = TaskType.DURATION


class AccountDetailDialog(QDialog):
    """单个账号的完整控制面板。"""

    status_updated = pyqtSignal(str, str, str)  # username, status, progress
    uid_resolved = pyqtSignal(str, str)  # username, uid

    def __init__(self, account: Account, parent=None):
        super().__init__(parent)
        self.account = account
        self.platform_key = getattr(account, "platform", "welearn") or "welearn"
        self.platform_info = get_info(self.platform_key)
        self.client = create_client(self.platform_key)

        self.is_logged_in = False
        self.courses: List[Dict] = []
        self.current_course: Optional[Dict] = None
        self.current_units: List[Dict] = []
        self.uid = getattr(account, "uid", "") or ""
        self.classid = ""
        self._closing = False

        self.login_thread = None
        self.course_thread = None
        self.units_thread = None
        self.study_thread = None
        self.launch_thread = None

        self.task_progress = TaskProgress()
        self.current_task_id: Optional[str] = None
        self._pending_continue_task: Optional[Dict] = None
        self._auto_start_mode = False
        self._auto_start_timer: Optional[QTimer] = None
        self._countdown_seconds = 0
        self._pending_status = (account.status, "")
        self._estimated_total_seconds = 0

        self.init_ui()
        self.setWindowTitle(
            f"{self.platform_info.name} - {account.nickname or account.username}"
        )
        self.setMinimumSize(700, 500)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        apply_background(self)

    # ================================================================ 界面

    def init_ui(self):
        layout = QVBoxLayout(self)

        info_row = QHBoxLayout()
        info_row.addWidget(QLabel(f"<b>用户名:</b> {self.account.username}"))
        info_row.addWidget(QLabel(f"<b>昵称:</b> {self.account.nickname or '无'}"))
        self.status_label = QLabel(f"<b>状态:</b> {self.account.status}")
        info_row.addWidget(self.status_label)
        info_row.addStretch()
        self.login_btn = QPushButton("🔐 登录")
        self.login_btn.clicked.connect(self.do_login)
        info_row.addWidget(self.login_btn)
        layout.addLayout(info_row)

        self.auto_login_attempted = False

        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        # ---------------- 课程列表 ----------------
        course_group = QGroupBox("课程列表")
        course_layout = QVBoxLayout(course_group)
        self.refresh_courses_btn = QPushButton("刷新课程")
        self.refresh_courses_btn.setEnabled(False)
        self.refresh_courses_btn.clicked.connect(self.refresh_courses)
        course_layout.addWidget(self.refresh_courses_btn)
        self.courses_list = QListWidget()
        self.courses_list.itemClicked.connect(self.on_course_selected)
        course_layout.addWidget(self.courses_list)
        left_layout.addWidget(course_group)

        # ---------------- 任务设置 ----------------
        settings = QGroupBox("任务设置")
        settings_layout = QVBoxLayout(settings)

        course_row = QHBoxLayout()
        course_row.addWidget(QLabel("目标课程:"))
        self.current_course_label = QLabel("未选择")
        self.current_course_label.setStyleSheet("color: #666; font-style: italic;")
        course_row.addWidget(self.current_course_label)
        course_row.addStretch()
        settings_layout.addLayout(course_row)

        unit_group = QGroupBox("选择单元")
        unit_layout = QVBoxLayout(unit_group)
        select_row = QHBoxLayout()
        self.select_all_btn = QPushButton("全选")
        self.select_none_btn = QPushButton("取消全选")
        self.select_all_btn.clicked.connect(self.select_all_units)
        self.select_none_btn.clicked.connect(self.select_none_units)
        select_row.addWidget(self.select_all_btn)
        select_row.addWidget(self.select_none_btn)
        select_row.addStretch()
        unit_layout.addLayout(select_row)
        self.unit_list = QListWidget()
        self.unit_list.setMaximumHeight(120)
        unit_layout.addWidget(self.unit_list)
        settings_layout.addWidget(unit_group)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("模式:"))
        self.mode_combo = QComboBox()
        modes = [MODE_HOMEWORK]
        if self.client.supports(Capability.DURATION):
            modes.append(MODE_DURATION)
        self.mode_combo.addItems(modes)
        self.mode_combo.currentTextChanged.connect(self.on_mode_changed)
        mode_row.addWidget(self.mode_combo)
        if self.client.supports(Capability.CDP):
            self.launch_btn = QPushButton("🚀 启动并连接")
            self.launch_btn.setToolTip("带 --remote-debugging-port 启动本地客户端并自动连接")
            self.launch_btn.clicked.connect(self.launch_client)
            mode_row.addWidget(self.launch_btn)

            self.dump_btn = QPushButton("🧭 抓取页面结构")
            self.dump_btn.setToolTip("导出当前客户端页面的 HTML 与截图，用于校准选择器")
            self.dump_btn.clicked.connect(self.dump_page)
            mode_row.addWidget(self.dump_btn)
        mode_row.addStretch()
        settings_layout.addLayout(mode_row)

        self.platform_hint = QLabel("")
        self.platform_hint.setWordWrap(True)
        self.platform_hint.setStyleSheet("color: #666; font-size: 12px;")
        settings_layout.addWidget(self.platform_hint)
        self._update_platform_hint()

        # 刷作业参数
        self.homework_widget = QWidget()
        homework_layout = QVBoxLayout(self.homework_widget)
        homework_layout.setContentsMargins(0, 0, 0, 0)

        accuracy_row = QHBoxLayout()
        accuracy_row.addWidget(QLabel("正确率:"))
        self.accuracy_spin = QSpinBox()
        self.accuracy_spin.setRange(0, 100)
        self.accuracy_spin.setValue(100)
        self.accuracy_spin.setSuffix("%")
        accuracy_row.addWidget(self.accuracy_spin)
        accuracy_row.addStretch()
        homework_layout.addLayout(accuracy_row)

        homework_concurrent_row = QHBoxLayout()
        homework_concurrent_row.addWidget(QLabel("并发数:"))
        self.homework_concurrent_spin = QSpinBox()
        self.homework_concurrent_spin.setRange(1, workers.MAX_WORKERS)
        self.homework_concurrent_spin.setValue(5)
        self.homework_concurrent_spin.setToolTip("同时处理多少门课程")
        homework_concurrent_row.addWidget(self.homework_concurrent_spin)
        homework_concurrent_row.addStretch()
        homework_layout.addLayout(homework_concurrent_row)
        settings_layout.addWidget(self.homework_widget)

        # 刷时长参数
        self.time_widget = QWidget()
        time_layout = QVBoxLayout(self.time_widget)
        time_layout.setContentsMargins(0, 0, 0, 0)

        time_row = QHBoxLayout()
        time_row.addWidget(QLabel("单元时长:"))
        self.time_spin = QSpinBox()
        self.time_spin.setRange(1, 240)
        self.time_spin.setValue(3)
        self.time_spin.setToolTip("每个单元的总学习时长")
        time_row.addWidget(self.time_spin)
        self.time_unit_combo = QComboBox()
        self.time_unit_combo.addItems(["小时", "分钟"])
        self.time_unit_combo.setCurrentText("小时")
        self.time_unit_combo.currentTextChanged.connect(self.on_time_unit_changed)
        time_row.addWidget(self.time_unit_combo)
        time_row.addWidget(QLabel("  随机扰动:"))
        self.time_random_spin = QSpinBox()
        self.time_random_spin.setRange(0, 30)
        self.time_random_spin.setValue(5)
        self.time_random_spin.setSuffix(" 分钟")
        self.time_random_spin.setToolTip("随机增减范围，如设5则实际时长为 55~65 分钟")
        time_row.addWidget(self.time_random_spin)
        time_row.addStretch()
        time_layout.addLayout(time_row)

        concurrent_row = QHBoxLayout()
        concurrent_row.addWidget(QLabel("并发数:"))
        self.concurrent_spin = QSpinBox()
        self.concurrent_spin.setRange(1, workers.MAX_WORKERS)
        self.concurrent_spin.setValue(8)
        self.concurrent_spin.setToolTip("同时挂多少门课程（过高只会加剧上游限流，不会更快）")
        concurrent_row.addWidget(self.concurrent_spin)
        concurrent_row.addStretch()
        time_layout.addLayout(concurrent_row)
        settings_layout.addWidget(self.time_widget)
        self.time_widget.hide()

        left_layout.addWidget(settings)

        control_row = QHBoxLayout()
        self.start_btn = QPushButton("▶️ 开始刷作业")
        self.start_btn.setEnabled(False)
        self.start_btn.clicked.connect(self.start_study)
        self.stop_btn = QPushButton("⏹️ 停止")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_study)
        control_row.addWidget(self.start_btn)
        control_row.addWidget(self.stop_btn)
        left_layout.addLayout(control_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        left_layout.addWidget(self.progress_bar)
        splitter.addWidget(left)

        # ---------------- 日志 ----------------
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        log_group = QGroupBox("运行日志")
        log_layout = QVBoxLayout(log_group)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.document().setMaximumBlockCount(LOG_MAX_BLOCKS)
        log_layout.addWidget(self.log_text)
        clear_btn = QPushButton("清空日志")
        clear_btn.clicked.connect(self.log_text.clear)
        log_layout.addWidget(clear_btn)
        right_layout.addWidget(log_group)
        splitter.addWidget(right)

        splitter.setSizes([350, 350])
        layout.addWidget(splitter)

        # ---------------- 倒计时 ----------------
        self.countdown_label = QLabel("⏱ 预计剩余: --:--:--")
        self.countdown_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.countdown_label.setStyleSheet(
            """
            QLabel {
                background-color: rgba(0, 0, 0, 0.7);
                color: #00FF88;
                font-size: 16px;
                font-weight: bold;
                font-family: Consolas, monospace;
                padding: 8px;
                border-radius: 4px;
            }
            """
        )
        self.countdown_label.setVisible(False)
        layout.addWidget(self.countdown_label)

        self.progress_total = 0
        self.progress_current = 0
        self._task_start_time: Optional[QElapsedTimer] = None
        self._countdown_timer = QTimer(self)
        self._countdown_timer.timeout.connect(self._update_countdown)

        self._status_flush_timer = QTimer(self)
        self._status_flush_timer.setSingleShot(True)
        self._status_flush_timer.timeout.connect(self._flush_status)

    # ================================================================ 状态

    def log(self, text: str) -> None:
        self.log_text.append(text)
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        logger.info(text)

    def update_status(self, status: str, progress: str = "", immediate: bool = False) -> None:
        """更新状态。默认合并 300ms 再通知主界面，避免高频信号拖垮表格刷新。"""
        self.account.status = status
        self.account.progress = progress
        self.status_label.setText(f"<b>状态:</b> {status}")
        self._pending_status = (status, progress)
        if immediate:
            self._flush_status()
        elif not self._status_flush_timer.isActive():
            self._status_flush_timer.start(STATUS_FLUSH_MS)

    def _flush_status(self) -> None:
        status, progress = self._pending_status
        self.status_updated.emit(self.account.username, status, progress)

    # ================================================================ 登录

    def showEvent(self, event):
        super().showEvent(event)
        if not self.auto_login_attempted and not self.is_logged_in:
            self.auto_login_attempted = True
            QTimer.singleShot(500, self.do_login)

    def do_login(self):
        if self.login_thread is not None and self.login_thread.isRunning():
            return
        logger.info("开始登录 - 账号: %s", self.account.username)
        self.login_btn.setEnabled(False)
        self.login_btn.setText("登录中...")
        self.log("正在登录...")
        self.update_status(AccountStatus.LOGGING_IN, immediate=True)

        self.login_thread = workers.LoginThread(
            self.client, self.account.username, self.account.password
        )
        self.login_thread.login_result.connect(self.on_login_result)
        self.login_thread.start()

    def on_login_result(self, success: bool, message: str):
        self.login_btn.setEnabled(True)
        if success:
            self.is_logged_in = True
            self.login_btn.setText("✅ 已登录")
            self.login_btn.setEnabled(False)
            self.refresh_courses_btn.setEnabled(True)
            self.log("✅ 登录成功")
            self.update_status(AccountStatus.LOGGED_IN, immediate=True)
            self.refresh_courses()
            QTimer.singleShot(500, self.check_incomplete_task)
            return

        self.login_btn.setText("🔐 登录")
        hint = message
        code = self.client.last_error_code
        if code == ErrCode.TIMEOUT:
            hint = "网络超时，请检查网络连接后重试"
        elif code == ErrCode.NETWORK:
            hint = "无法连接服务器，请检查网络"
        elif code == ErrCode.PARSE:
            hint = "服务器响应异常，请稍后重试"
        elif code in (ErrCode.AUTH, ErrCode.CDP, ErrCode.UNSUPPORTED):
            # 这几类是明确可操作的提示（密码错 / 未开调试端口 / 不支持），原样展示
            hint = message
        self.log(f"❌ 登录失败: {hint}")
        logger.error("登录失败 - 账号: %s, 错误: %s", self.account.username, message)
        if not self.account.password:
            self.log("⚠️ 本机无法解密该账号口令，请重新添加账号并输入密码")
        self.update_status(AccountStatus.LOGIN_FAILED, message, immediate=True)

    # ================================================================ 课程与单元

    def refresh_courses(self):
        if self.course_thread is not None and self.course_thread.isRunning():
            return
        logger.info("开始获取课程列表 - 账号: %s", self.account.username)
        self.refresh_courses_btn.setEnabled(False)
        self.refresh_courses_btn.setText("获取中...")
        self.log("正在获取课程列表...")
        self.course_thread = workers.CourseThread(self.client)
        self.course_thread.course_result.connect(self.on_courses_result)
        self.course_thread.start()

    def on_courses_result(self, success: bool, courses: list, message: str):
        self.refresh_courses_btn.setEnabled(True)
        self.refresh_courses_btn.setText("刷新课程")
        if not success:
            self.log(f"❌ 获取课程失败: {message}")
            logger.error("课程获取失败 - 账号: %s, 错误: %s", self.account.username, message)
            QMessageBox.warning(self, "失败", message)
            return

        self.courses = courses
        self.courses_list.clear()
        for course in courses:
            item = QListWidgetItem(f"{course['name']} (进度: {course['per']}%)")
            item.setData(Qt.ItemDataRole.UserRole, course)
            self.courses_list.addItem(item)
        self.log(f"✅ 获取到 {len(courses)} 门课程")
        logger.info("课程列表获取成功 - 账号: %s, 数量: %d", self.account.username, len(courses))

    def on_course_selected(self, item: QListWidgetItem):
        course = item.data(Qt.ItemDataRole.UserRole)
        if not course:
            return
        self.current_course = course
        self.current_course_label.setText(course["name"])
        self.log(f"选择课程: {course['name']}")
        logger.info(
            "选择课程 - 账号: %s, 课程: %s (ID: %s)",
            self.account.username,
            course["name"],
            course["cid"],
        )
        self.get_units()

    def get_units(self):
        if not self.current_course:
            return
        self.unit_list.clear()
        self.start_btn.setEnabled(False)
        self.log("正在获取单元信息...")
        self.units_thread = workers.UnitsThread(self.client, self.current_course["cid"])
        self.units_thread.units_result.connect(self.on_units_result)
        self.units_thread.start()

    def on_units_result(self, success: bool, units_data: list, message: str):
        if not success or not units_data:
            self.log(f"❌ 获取单元失败: {message}")
            logger.error(
                "单元获取失败 - 账号: %s, 错误: %s", self.account.username, message
            )
            return

        data = units_data[0]
        self.uid = str(data["uid"])
        self.classid = data["classid"]
        self.current_units = data["units"]
        self.account.uid = self.uid
        self.uid_resolved.emit(self.account.username, self.uid)

        self.unit_list.clear()
        for index, unit in enumerate(self.current_units):
            item = QListWidgetItem(f"单元 {index + 1}: {unit.get('name', f'单元 {index + 1}')}")
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, index)
            self.unit_list.addItem(item)

        self.start_btn.setEnabled(True)
        self.log(f"✅ 获取到 {len(self.current_units)} 个单元")
        logger.info(
            "单元获取成功 - 账号: %s, 课程: %s, 单元数: %d",
            self.account.username,
            self.current_course["name"] if self.current_course else "-",
            len(self.current_units),
        )

        if self._pending_continue_task:
            task = self._pending_continue_task
            self._pending_continue_task = None
            self._execute_continue_task(task)

    def select_all_units(self):
        for index in range(self.unit_list.count()):
            self.unit_list.item(index).setCheckState(Qt.CheckState.Checked)

    def select_none_units(self):
        for index in range(self.unit_list.count()):
            self.unit_list.item(index).setCheckState(Qt.CheckState.Unchecked)

    # ================================================================ 模式切换

    def _update_platform_hint(self) -> None:
        """按平台能力给出提示文案。"""
        hints = [self.platform_info.description]
        if not self.client.supports(Capability.DURATION):
            hints.append("该平台暂不支持刷时长（需要 ut 签名直连接口），只能刷作业。")
        if self.client.supports(Capability.CDP):
            hints.append(
                "使用前请给 iSmart 客户端加启动参数 --remote-debugging-port=9222 并保持登录；"
                "页面元素需要校准，可点「抓取页面结构」后修改 ismart_profile.json。"
            )
        self.platform_hint.setText(" ".join(h for h in hints if h))

    def on_mode_changed(self, mode: str):
        is_homework = mode == MODE_HOMEWORK
        self.homework_widget.setVisible(is_homework)
        self.time_widget.setVisible(not is_homework)
        self.start_btn.setText("▶️ 开始刷作业" if is_homework else "▶️ 开始刷时长")

    def launch_client(self):
        """带调试端口启动本地客户端（后台线程，避免 25 秒探测卡界面）。"""
        launcher = getattr(self.client, "launch_and_connect", None)
        if launcher is None:
            self.log("当前平台不支持自动启动客户端")
            return
        if self.launch_thread is not None and self.launch_thread.isRunning():
            return
        if hasattr(self, "launch_btn"):
            self.launch_btn.setEnabled(False)
        self.log("正在启动客户端（带调试端口）...")
        self.launch_thread = workers.LaunchClientThread(self.client)
        self.launch_thread.result_ready.connect(self.on_launch_result)
        self.launch_thread.start()

    def on_launch_result(self, ok: bool, message: str):
        if hasattr(self, "launch_btn"):
            self.launch_btn.setEnabled(True)
        self.log(("✅ " if ok else "❌ ") + message)
        if not ok:
            QMessageBox.warning(self, "无法连接客户端", message)
            return
        self.is_logged_in = True
        self.login_btn.setText("✅ 已登录")
        self.login_btn.setEnabled(False)
        self.refresh_courses_btn.setEnabled(True)
        self.update_status(AccountStatus.LOGGED_IN, immediate=True)
        self.refresh_courses()

    def dump_page(self):
        """导出当前客户端页面结构，便于校准 iSmart 选择器。"""
        dumper = getattr(self.client, "dump_page", None)
        if dumper is None:
            self.log("当前平台不支持页面抓取")
            return
        try:
            path = dumper(self.account.username)
        except Exception as exc:
            self.log(f"❌ 抓取页面失败: {exc}")
            logger.error("抓取页面失败: %s", exc)
            return
        self.log(f"✅ 页面结构已导出: {path}")
        QMessageBox.information(self, "已导出", f"页面已保存到：\n{path}")

    def on_time_unit_changed(self, unit: str):
        current = self.time_spin.value()
        if unit == "小时":
            self.time_spin.setRange(1, 240)
            self.time_spin.setValue(max(1, current // 60))
        else:
            self.time_spin.setRange(1, 14400)
            self.time_spin.setValue(max(1, current * 60))
        self.time_random_spin.setSuffix(" 分钟")

    # ================================================================ 启动任务

    def _selected_units(self) -> List[int]:
        selected = []
        for index in range(self.unit_list.count()):
            item = self.unit_list.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                unit_index = item.data(Qt.ItemDataRole.UserRole)
                if isinstance(unit_index, int):
                    selected.append(unit_index)
        return selected

    def start_study(self):
        if not self.current_course:
            QMessageBox.warning(self, "警告", "请先选择课程")
            return
        if not self.uid:
            QMessageBox.warning(self, "警告", "单元信息尚未加载完成，请稍候")
            return

        units_to_process = self._selected_units()
        if not units_to_process:
            QMessageBox.warning(self, "警告", "请至少选择一个单元")
            return

        mode = self.mode_combo.currentText()
        auto_start = self._auto_start_mode
        self._auto_start_mode = False

        if mode == MODE_HOMEWORK:
            if not auto_start:
                box = QMessageBox(self)
                no_help(box)
                box.setWindowTitle("任务提醒")
                box.setIcon(QMessageBox.Information)
                box.setText(
                    f"即将开始刷作业任务\n\n课程: {self.current_course['name']}\n"
                    f"选中单元数: {len(units_to_process)} 个\n\n确认要开始吗？"
                )
                box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
                box.setDefaultButton(QMessageBox.Yes)
                if box.exec_() != QMessageBox.Yes:
                    return
            homework_concurrent = self.homework_concurrent_spin.value()
            accuracy_config = self.accuracy_spin.value()
            total_minutes = 0
            random_range = 0
        else:
            time_value = self.time_spin.value()
            total_minutes = time_value * 60 if self.time_unit_combo.currentText() == "小时" else time_value
            random_range = self.time_random_spin.value()
            homework_concurrent = 0
            accuracy_config = 0

            estimated = total_minutes * len(units_to_process) / max(1, self.concurrent_spin.value())
            if not auto_start:
                box = QMessageBox(self)
                no_help(box)
                box.setWindowTitle("任务提醒")
                box.setIcon(QMessageBox.Information)
                box.setText(
                    f"即将开始刷时长任务\n\n课程: {self.current_course['name']}\n"
                    f"选中单元数: {len(units_to_process)} 个\n"
                    f"每单元时长: {total_minutes} 分钟\n"
                    f"预计完成时间: {workers.format_duration(int(estimated * 60))}\n\n确认要开始吗？"
                )
                box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
                box.setDefaultButton(QMessageBox.Yes)
                if box.exec_() != QMessageBox.Yes:
                    return

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.progress_total = 0
        self.progress_current = 0

        self._task_start_time = QElapsedTimer()
        self._task_start_time.start()

        if mode == MODE_HOMEWORK:
            self._estimated_total_seconds = len(units_to_process) * 5 / max(1, homework_concurrent)
            self.log(f"开始刷作业 (已选 {len(units_to_process)} 个单元, {homework_concurrent} 并发)...")
            logger.info(
                "刷作业配置 - 正确率: %s%%, 并发: %d", accuracy_config, homework_concurrent
            )
            self.study_thread = workers.StudyThread(
                self.client,
                self.current_course["cid"],
                self.uid,
                self.classid,
                units_to_process,
                accuracy_config,
                self.current_units,
                max_concurrent=homework_concurrent,
            )
            task_config = {
                "cid": self.current_course["cid"],
                "accuracy": accuracy_config,
                "concurrent": homework_concurrent,
                "course_name": self.current_course["name"],
            }
        else:
            concurrent = self.concurrent_spin.value()
            self._estimated_total_seconds = (
                total_minutes * 60 / max(1, concurrent) * len(units_to_process)
            )
            self.log(
                f"开始刷时长 (已选 {len(units_to_process)} 个单元, "
                f"每单元 {total_minutes}±{random_range} 分钟, {concurrent} 并发)..."
            )
            logger.info(
                "刷时长配置 - 每单元: %s 分钟, 扰动: ±%s, 并发: %d",
                total_minutes,
                random_range,
                concurrent,
            )
            self.study_thread = workers.TimeStudyThread(
                self.client,
                self.current_course["cid"],
                self.uid,
                self.classid,
                units_to_process,
                total_minutes,
                random_range,
                self.current_units,
                max_concurrent=concurrent,
            )
            task_config = {
                "cid": self.current_course["cid"],
                "total_minutes": total_minutes,
                "random_range": random_range,
                "concurrent": concurrent,
                "course_name": self.current_course["name"],
            }

        self.countdown_label.setStyleSheet(self._countdown_style())
        self.countdown_label.setText(f"⏱ 预计剩余: {self._format_hms(self._estimated_total_seconds)}")
        self.countdown_label.setVisible(True)
        self._countdown_timer.start(COUNTDOWN_TICK_MS)

        self.update_status(AccountStatus.RUNNING, immediate=True)

        self.current_task_id = self.task_progress.generate_task_id(
            self.current_course["cid"], self.uid, mode
        )
        self.task_progress.save_task_progress(
            task_id=self.current_task_id,
            task_type=mode,
            cid=self.current_course["cid"],
            uid=self.uid,
            classid=self.classid,
            unit_indices=units_to_process,
            current_units=self.current_units,
            completed_units=[],
            completed_courses={},
            task_config=task_config,
            platform=self.platform_key,
        )
        logger.info("任务进度已保存 - 任务ID: %s", self.current_task_id)

        self.study_thread.progress_update.connect(self.on_progress_update)
        self.study_thread.study_finished.connect(self.on_study_finished)
        self.study_thread.finished.connect(self._on_study_thread_finished)
        self.study_thread.start()

    def _on_study_thread_finished(self):
        """线程自然结束后释放引用（不在倒计时里提前置空）。"""
        thread = self.study_thread
        self.study_thread = None
        if thread is not None:
            thread.deleteLater()

    # ================================================================ 停止任务

    def stop_study(self):
        thread = self.study_thread
        course_name = self.current_course["name"] if self.current_course else "未选择"
        logger.info("用户请求停止任务 - 账号: %s, 课程: %s", self.account.username, course_name)

        if thread is not None and thread.isRunning():
            self.log("正在停止任务...")
            thread.request_stop()
            if not thread.wait(5000):
                logger.warning("任务线程 5 秒内未停止，将在后台自行收尾")
                self.log("任务正在收尾，稍后自动结束")
        else:
            self.log("⏹️ 任务已停止")

        if self.current_task_id:
            logger.info("任务已暂停: %s", self.current_task_id)
            self.current_task_id = None

        self._reset_run_ui(AccountStatus.STOPPED)
        self.log("⏹️ 任务已停止")

    def _reset_run_ui(self, status: str) -> None:
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.progress_bar.setVisible(False)
        self.countdown_label.setVisible(False)
        self._countdown_timer.stop()
        self.update_status(status, immediate=True)

    # ================================================================ 进度

    def on_progress_update(self, status: str, message: str):
        if status == ProgressEvent.PROGRESS_TOTAL:
            try:
                self.progress_total = int(message)
                self.progress_bar.setRange(0, self.progress_total)
                self.progress_bar.setValue(0)
            except ValueError:
                pass
            return
        if status == ProgressEvent.PROGRESS_CURRENT:
            try:
                self.progress_current = int(message)
                self.progress_bar.setValue(self.progress_current)
                self._update_countdown()
            except ValueError:
                pass
            return

        self.log(message)
        if status == ProgressEvent.ERROR:
            self.update_status(AccountStatus.RUNNING, message)
        else:
            self.update_status(AccountStatus.RUNNING, status)

    def _format_hms(self, seconds: float) -> str:
        seconds = max(0, int(seconds))
        hours, remainder = divmod(seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    @staticmethod
    def _countdown_style() -> str:
        return """
            QLabel {
                background-color: rgba(0, 0, 0, 0.7);
                color: #00FF88;
                font-size: 16px;
                font-weight: bold;
                font-family: Consolas, monospace;
                padding: 8px;
                border-radius: 4px;
            }
        """

    def _update_countdown(self):
        if self._task_start_time is None:
            return
        elapsed = self._task_start_time.elapsed() / 1000.0
        if self.progress_total > 0 and self.progress_current > 0:
            remaining = elapsed / self.progress_current * max(0, self.progress_total - self.progress_current)
        else:
            remaining = max(0.0, self._estimated_total_seconds - elapsed)

        suffix = f" ({self.progress_current}/{self.progress_total})" if self.progress_total > 0 else ""
        self.countdown_label.setText(f"⏱ 预计剩余: {self._format_hms(remaining)}{suffix}")

    # ================================================================ 完成

    def on_study_finished(self, result: dict):
        elapsed = 0
        if self._task_start_time and self._task_start_time.isValid():
            elapsed = int(self._task_start_time.elapsed() / 1000)

        self._countdown_timer.stop()
        self.progress_bar.setVisible(False)
        self.stop_btn.setEnabled(False)

        if self.current_task_id:
            self.task_progress.mark_task_completed(self.current_task_id)
            self.current_task_id = None

        mode = self.mode_combo.currentText()
        self.countdown_label.setStyleSheet(
            """
            QLabel {
                background-color: rgba(76, 175, 80, 0.85);
                color: white;
                font-size: 16px;
                font-weight: bold;
                font-family: Consolas, monospace;
                padding: 8px;
                border-radius: 4px;
            }
            """
        )
        self.countdown_label.setText(f"✅ 任务完成! 总用时: {self._format_hms(elapsed)}")
        self.countdown_label.setVisible(True)

        if mode == MODE_HOMEWORK:
            summary = (
                f"步骤1成功: {result.get('way1_succeed', 0)}, 失败: {result.get('way1_failed', 0)}\n"
                f"步骤2成功: {result.get('way2_succeed', 0)}, 失败: {result.get('way2_failed', 0)}"
            )
            if result.get("retry_count"):
                summary += f"\n重试次数: {result['retry_count']}"
            self.log(f"✅ 刷作业完成！\n{summary}")
            logger.info("刷作业完成 - 账号: %s, 结果: %s", self.account.username, summary)
            self._ask_auto_duration(summary)
        else:
            completed = result.get("completed_units", result.get("way1_succeed", 0))
            total = result.get("total_units", 0)
            summary = f"✅ 刷时长完成！已完成 {completed}/{total} 门课程"
            if result.get("retry_count"):
                summary += f"\n重试次数: {result['retry_count']}"
            self.log(summary)
            logger.info("刷时长完成 - 账号: %s, %s", self.account.username, summary)

        self._play_finish_sound()
        self.start_btn.setEnabled(True)
        self.update_status(AccountStatus.COMPLETED, immediate=True)
        QMessageBox.information(self, "完成", "任务已完成！")

    def _play_finish_sound(self) -> None:
        try:
            import winsound

            winsound.MessageBeep(winsound.MB_OK)
        except Exception as exc:
            logger.debug("播放系统提示音失败: %s", exc)

    def _ask_auto_duration(self, summary: str) -> None:
        """刷作业完成后询问是否接着刷时长（默认 5 秒后自动开始）。"""
        if not self.client.supports(Capability.DURATION):
            self.log("当前平台不支持刷时长，已跳过自动衔接")
            return
        box = QMessageBox(self)
        no_help(box)
        box.setWindowTitle("刷作业完成")
        box.setIcon(QMessageBox.Information)
        box.setText(f"刷作业已完成！\n{summary}\n\n5秒后自动开始刷时长...")
        ok_btn = box.addButton("立即开始", QMessageBox.AcceptRole)
        cancel_btn = box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(ok_btn)

        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(box.accept)
        timer.start(AUTO_CHAIN_SECONDS * 1000)

        box.exec_()
        timer.stop()
        if box.clickedButton() is cancel_btn:
            self.log("已取消自动刷时长")
            return
        self.auto_start_time_study()

    def auto_start_time_study(self):
        if not self.client.supports(Capability.DURATION):
            self.log("当前平台不支持刷时长")
            return
        logger.info("刷作业完成，自动切换到刷时长模式")
        self.mode_combo.setCurrentText(MODE_DURATION)
        self.time_spin.setValue(3)
        self.time_unit_combo.setCurrentText("小时")
        self.concurrent_spin.setValue(8)
        self.time_random_spin.setValue(5)
        self._auto_start_mode = True

        self._countdown_seconds = AUTO_CHAIN_SECONDS
        self.countdown_label.setStyleSheet(self._countdown_style())
        self.countdown_label.setText(f"⏱ {self._countdown_seconds}秒后自动开始刷时长...")
        self.countdown_label.setVisible(True)

        self._auto_start_timer = QTimer(self)
        self._auto_start_timer.timeout.connect(self._auto_start_tick)
        self._auto_start_timer.start(COUNTDOWN_TICK_MS)

    def _auto_start_tick(self):
        self._countdown_seconds -= 1
        if self._countdown_seconds > 0:
            self.countdown_label.setText(f"⏱ {self._countdown_seconds}秒后自动开始刷时长...")
            return
        if self._auto_start_timer is not None:
            self._auto_start_timer.stop()
        self.countdown_label.setVisible(False)
        logger.debug("自动开始刷时长倒计时结束，启动任务")
        self.start_study()

    # ================================================================ 任务续跑

    def check_incomplete_task(self):
        task = self.task_progress.find_incomplete_by_uid(self.uid, self.platform_key)
        if not task:
            return
        task_type = task.get("task_type", "未知")
        course_name = task.get("task_config", {}).get("course_name", "未知课程")

        box = QMessageBox(self)
        no_help(box)
        box.setWindowTitle("未完成任务")
        box.setIcon(QMessageBox.Question)
        box.setText(f"检测到未完成的{task_type}任务：\n课程: {course_name}\n\n是否继续该任务？")
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.Yes)
        if box.exec_() == QMessageBox.Yes:
            self.continue_task(task)
        else:
            self.task_progress.clear_task_progress(task.get("task_id", ""))

    def continue_task(self, task: Dict):
        """先按 cid 选中课程，等单元加载完再恢复参数并开跑。"""
        task_type = task.get("task_type", "")
        cid = task.get("task_config", {}).get("cid", "")
        logger.info("继续任务: %s, 课程CID: %s", task_type, cid)
        self.log(f"准备继续未完成的{task_type}任务...")
        self._pending_continue_task = task

        if not cid:
            return
        for index in range(self.courses_list.count()):
            item = self.courses_list.item(index)
            course = item.data(Qt.ItemDataRole.UserRole)
            if course and str(course.get("cid")) == str(cid):
                self.courses_list.setCurrentItem(item)
                self.on_course_selected(item)
                return
        self.log("❌ 未找到对应的课程，无法继续任务")
        self._pending_continue_task = None

    def _execute_continue_task(self, task: Dict):
        task_type = task.get("task_type", "")
        config = task.get("task_config", {})
        unit_indices = task.get("unit_indices", [])
        logger.info("执行继续任务: %s, 单元: %s", task_type, unit_indices)

        if task_type == MODE_DURATION:
            self.mode_combo.setCurrentText(MODE_DURATION)
            total_minutes = int(config.get("total_minutes", 60))
            if total_minutes >= 60:
                self.time_unit_combo.setCurrentText("小时")
                self.time_spin.setValue(max(1, total_minutes // 60))
            else:
                self.time_unit_combo.setCurrentText("分钟")
                self.time_spin.setValue(max(1, total_minutes))
            self.concurrent_spin.setValue(min(workers.MAX_WORKERS, int(config.get("concurrent", 8))))
            self.time_random_spin.setValue(int(config.get("random_range", 5)))
        else:
            self.mode_combo.setCurrentText(MODE_HOMEWORK)
            self.accuracy_spin.setValue(int(config.get("accuracy", 100)))
            self.homework_concurrent_spin.setValue(
                min(workers.MAX_WORKERS, int(config.get("concurrent", 5)))
            )

        for index in range(self.unit_list.count()):
            item = self.unit_list.item(index)
            checked = index in unit_indices
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)

        self.task_progress.clear_task_progress(task.get("task_id", ""))
        self.log(f"已恢复{task_type}任务参数，自动开始执行...")
        self._auto_start_mode = True
        QTimer.singleShot(500, self.start_study)

    # ================================================================ 关闭

    def closeEvent(self, event):
        # 主窗口关闭时会显式调一次 closeEvent(None) 再 close()，Qt 又会发一次事件；
        # 用标记保证清理只做一遍（否则会重复关闭线程/客户端并重复写日志）。
        if self._closing:
            if event is not None:
                event.accept()
            return
        self._closing = True
        thread = self.study_thread
        if thread is not None and thread.isRunning():
            box = QMessageBox(self)
            no_help(box)
            box.setWindowTitle("任务正在运行")
            box.setIcon(QMessageBox.Question)
            box.setText("当前有任务正在运行，关闭窗口会停止任务。\n\n是否继续关闭？")
            box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            box.setDefaultButton(QMessageBox.No)
            if box.exec_() != QMessageBox.Yes:
                if event is not None:
                    event.ignore()
                return

        self._countdown_timer.stop()
        if self._auto_start_timer is not None:
            self._auto_start_timer.stop()
        self._status_flush_timer.stop()

        if thread is not None:
            logger.info("关闭窗口，请求停止任务线程")
            thread.request_stop()
            if not thread.wait(3000):
                logger.warning("任务线程未在 3 秒内结束，将在后台自行收尾")
            self.study_thread = None

        if self.current_task_id:
            logger.info("关闭窗口，任务保持暂停状态: %s", self.current_task_id)
            self.current_task_id = None

        try:
            self.client.close()
        except Exception as exc:
            logger.error("关闭客户端连接时出错: %s", exc)

        logger.info("账号详情窗口已关闭 - 账号: %s", self.account.username)
        if event is not None:
            event.accept()

    def set_background(self):
        apply_background(self)

    def resizeEvent(self, event):
        apply_background(self)
        super().resizeEvent(event)


__all__ = ["AccountDetailDialog", "MODE_HOMEWORK", "MODE_DURATION"]