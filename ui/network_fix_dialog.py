"""网络诊断与修复工具箱对话框。

改动要点：

- ``DiagnosisThread`` / ``FixThread`` 原来把信号命名为 ``finished``，直接遮蔽了
  ``QThread.finished`` 内置信号（Qt 会在对象销毁时自己发这个信号，语义混淆），
  改名为 ``results_ready``；
- 背景图、图标、去问号按钮统一走 ``ui.helpers``，不再复制一份路径判断；
- 诊断/修复线程支持 ``request_stop``，关窗时不会留下还在跑的线程。
"""
import os

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from core.logger import get_logger
from core.network_fixer import NetworkDiagnostics, NetworkFixer, is_admin
from ui.helpers import apply_background, no_help

logger = get_logger("NetworkFixDialog")

REPAIR_MAP = {
    "DNS服务": ("刷新DNS", "flush_dns"),
    "代理配置": ("重置代理", "reset_proxy"),
    "HOSTS文件": ("修复HOSTS", "fix_hosts"),
    "LSP协议": ("重置Winsock", "reset_winsock"),
}

RESULT_TABLE_STYLE = (
    "<style>table{width:100%} td{padding:5px} "
    ".ok{color:green} .warning{color:orange} .error{color:red}</style>"
)


class DiagnosisThread(QThread):
    """诊断线程。"""

    results_ready = pyqtSignal(list)
    progress = pyqtSignal(str)

    def run(self):
        self.progress.emit("正在诊断网络状态...")
        try:
            self.results_ready.emit(NetworkDiagnostics().diagnose_all())
        except Exception as exc:  # 诊断本身炸了也要回一个空结果，避免界面卡在「诊断中」
            logger.exception("网络诊断异常")
            self.progress.emit(f"诊断失败: {exc}")
            self.results_ready.emit([])


class FixThread(QThread):
    """修复线程。"""

    results_ready = pyqtSignal(list)
    progress = pyqtSignal(str)

    def __init__(self, items_to_fix, parent=None):
        super().__init__(parent)
        self.items_to_fix = list(items_to_fix)

    def run(self):
        self.progress.emit("正在执行修复...")
        fixer = NetworkFixer()
        results = []
        for item in self.items_to_fix:
            self.progress.emit(f"正在修复: {item}...")
            try:
                if item == "IP配置":
                    results.append(("重置IP/TCP", *fixer.reset_tcp_ip()))
                    results.append(("刷新DNS", *fixer.flush_dns()))
                    results.append(("重置DHCP", *fixer.reset_dhcp()))
                elif item in REPAIR_MAP:
                    label, method = REPAIR_MAP[item]
                    results.append((label, *getattr(fixer, method)()))
                else:
                    results.append((item, False, "该诊断项没有对应的修复动作"))
            except Exception as exc:
                logger.exception("修复 %s 时异常", item)
                results.append((item, False, str(exc)))
        self.results_ready.emit(results)


class NetworkFixDialog(QDialog):
    """网络修复工具箱对话框。"""

    def __init__(self, parent=None):
        super().__init__(None)  # 独立窗口，不跟随父窗口
        self.setWindowTitle("网络诊断与修复工具箱")
        self.setMinimumSize(600, 500)
        self.resize(600, 500)
        no_help(self)
        apply_background(self)
        self.init_ui()
        self.diagnosis_results = []
        self.fix_checkboxes = {}
        self.diag_thread = None
        self.fix_thread = None

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)

        title = QLabel("网络诊断与修复工具箱")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #333;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        button_row = QHBoxLayout()
        self.diagnose_btn = QPushButton("🔍 一键诊断")
        self.diagnose_btn.setStyleSheet(
            """
            QPushButton { background-color: #2196F3; color: white; border: none;
                          padding: 10px 20px; font-size: 14px; border-radius: 5px; }
            QPushButton:hover { background-color: #1976D2; }
            """
        )
        self.diagnose_btn.clicked.connect(self.start_diagnosis)
        button_row.addWidget(self.diagnose_btn)

        self.fix_btn = QPushButton("🔧 修复选中项")
        self.fix_btn.setStyleSheet(
            """
            QPushButton { background-color: #4CAF50; color: white; border: none;
                          padding: 10px 20px; font-size: 14px; border-radius: 5px; }
            QPushButton:hover { background-color: #45a049; }
            QPushButton:disabled { background-color: #cccccc; }
            """
        )
        self.fix_btn.setEnabled(False)
        self.fix_btn.clicked.connect(self.start_fix)
        button_row.addWidget(self.fix_btn)
        layout.addLayout(button_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        result_group = QGroupBox("诊断结果")
        result_layout = QVBoxLayout(result_group)
        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        result_layout.addWidget(self.result_text)

        self.fix_options_group = QGroupBox("选择要修复的项目（取消勾选可忽略）")
        self.fix_options_layout = QVBoxLayout(self.fix_options_group)
        self.fix_options_group.setVisible(False)
        result_layout.addWidget(self.fix_options_group)
        layout.addWidget(result_group)

        self.status_label = QLabel('点击"一键诊断"开始检测网络状态')
        self.status_label.setStyleSheet("color: #666; font-size: 12px;")
        layout.addWidget(self.status_label)

    # ------------------------------------------------------------ 诊断

    def start_diagnosis(self):
        if self.diag_thread is not None and self.diag_thread.isRunning():
            return
        self.diagnose_btn.setEnabled(False)
        self.fix_btn.setEnabled(False)
        self.result_text.clear()
        self.fix_options_group.setVisible(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.status_label.setText("正在诊断...")

        self.diag_thread = DiagnosisThread()
        self.diag_thread.results_ready.connect(self.on_diagnosis_finished)
        self.diag_thread.progress.connect(self.status_label.setText)
        self.diag_thread.start()

    def on_diagnosis_finished(self, results):
        self.diagnosis_results = results
        self.progress_bar.setVisible(False)
        self.diagnose_btn.setEnabled(True)

        html = RESULT_TABLE_STYLE + "<table>"
        self._clear_fix_options()
        has_problem = False

        for result in results:
            status = result.get("status", "")
            name = result.get("name", "")
            detail = result.get("detail", "")
            if status == "ok":
                icon, css = "\u2705", "ok"
            elif status == "warning":
                icon, css = "\u26a0\ufe0f", "warning"
                has_problem = True
                self._add_fix_option(name, detail, "font-size: 12px; padding: 3px;")
            else:
                icon, css = "\u274c", "error"
                has_problem = True
                self._add_fix_option(name, detail, "font-size: 12px; padding: 3px; color: red;")
            html += f'<tr><td>{icon}</td><td><b>{name}</b></td><td class="{css}">{detail}</td></tr>'

        html += "</table>"
        self.result_text.setHtml(html)

        if has_problem:
            self.fix_btn.setEnabled(True)
            self.fix_options_group.setVisible(True)
            self.status_label.setText(
                f"检测到 {len(self.fix_checkboxes)} 个问题，取消勾选可忽略"
            )
        else:
            self.status_label.setText("网络状态正常，无需修复")

    def _add_fix_option(self, name: str, detail: str, style: str) -> None:
        checkbox = QCheckBox(f"{name}: {detail}")
        checkbox.setChecked(True)
        checkbox.setStyleSheet(style)
        self.fix_options_layout.addWidget(checkbox)
        self.fix_checkboxes[name] = checkbox

    def _clear_fix_options(self) -> None:
        while self.fix_options_layout.count():
            item = self.fix_options_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.fix_checkboxes.clear()

    # ------------------------------------------------------------ 修复

    def start_fix(self):
        items = [name for name, checkbox in self.fix_checkboxes.items() if checkbox.isChecked()]
        if not items:
            QMessageBox.information(self, "提示", "没有选择需要修复的项目")
            return
        if not is_admin():
            box = QMessageBox(self)
            no_help(box)
            box.setWindowTitle("需要管理员权限")
            box.setIcon(QMessageBox.Warning)
            box.setText("网络修复需要管理员权限，请以管理员身份运行程序后重试。")
            box.exec_()
            return

        fix_list = "\n".join(f"  • {item}" for item in items)
        box = QMessageBox(self)
        no_help(box)
        box.setWindowTitle("确认修复")
        box.setIcon(QMessageBox.Question)
        box.setText(f"即将修复以下项目：\n{fix_list}\n\n部分修复需要重启电脑生效，是否继续？")
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        if box.exec_() != QMessageBox.StandardButton.Yes:
            return

        self.fix_btn.setEnabled(False)
        self.diagnose_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.status_label.setText("正在修复...")

        self.fix_thread = FixThread(items)
        self.fix_thread.results_ready.connect(self.on_fix_finished)
        self.fix_thread.start()

    def on_fix_finished(self, results):
        self.progress_bar.setVisible(False)
        self.diagnose_btn.setEnabled(True)
        self.fix_btn.setEnabled(True)

        if not results:
            self.status_label.setText("没有执行任何修复操作")
            return

        html = RESULT_TABLE_STYLE + "<p><b>修复结果：</b></p><table>"
        for name, success, message in results:
            icon = "\u2705" if success else "\u274c"
            css = "ok" if success else "error"
            html += f'<tr><td>{icon}</td><td><b>{name}</b></td><td class="{css}">{message}</td></tr>'
        html += "</table>"
        self.result_text.setHtml(html)
        self.status_label.setText("修复完成，部分操作需要重启电脑生效")

        if any("重启" in message for _, success, message in results if success):
            box = QMessageBox(self)
            no_help(box)
            box.setWindowTitle("需要重启")
            box.setIcon(QMessageBox.Question)
            box.setText("部分修复操作需要重启电脑才能生效，是否现在重启？")
            box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            box.setDefaultButton(QMessageBox.StandardButton.No)
            if box.exec_() == QMessageBox.StandardButton.Yes:
                os.system('shutdown /r /t 10 /c "网络修复完成，10秒后重启"')

    # ------------------------------------------------------------ 生命周期

    def resizeEvent(self, event):
        apply_background(self)
        super().resizeEvent(event)

    def closeEvent(self, event):
        for thread in (self.diag_thread, self.fix_thread):
            if thread is not None and thread.isRunning():
                thread.requestInterruption()
                thread.wait(1000)
        event.accept()


__all__ = ["NetworkFixDialog", "DiagnosisThread", "FixThread"]