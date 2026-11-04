"""账号管理视图：账号表格、增删、Excel 批量导入、导出。

改动要点：

- 顶层 ``import pandas`` 只为了读一个 Excel，却把 50MB 级依赖带进启动
  路径，缺包直接 ``ImportError`` 起不来 → 改用 ``openpyxl`` 直读；
- ``export_accounts()`` 原来定义了却从没接到按钮上（死代码），现在补上
  「导出」按钮；
- ``update_account_status`` 原来每次都「整份读 JSON + 整表重建」，而它被
  进度回调高频调用 → 改为原地更新单元格，只有显式刷新/增删才整表重建。
"""
from typing import Dict, Tuple

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.account_manager import Account, AccountManager
from core.constants import AccountStatus
from core.logger import get_logger
from ui.helpers import apply_background, message, no_help

logger = get_logger("AccountView")

COLUMNS = ["用户名", "昵称", "状态", "目标课程", "进度", "操作"]

STATUS_COLORS = {
    AccountStatus.RUNNING: Qt.GlobalColor.blue,
    AccountStatus.COMPLETED: Qt.GlobalColor.darkGreen,
    AccountStatus.FAILED: Qt.GlobalColor.red,
    AccountStatus.LOGIN_FAILED: Qt.GlobalColor.red,
}


class AddAccountDialog(QDialog):
    """添加账号对话框。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("添加账号")
        self.setModal(True)
        self.setMinimumWidth(300)
        no_help(self)
        self.init_ui()
        apply_background(self)

    def init_ui(self):
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("用户名:"))
        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("用户名")
        layout.addWidget(self.username_input)

        layout.addWidget(QLabel("密码:"))
        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("密码")
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self.password_input)

        layout.addWidget(QLabel("昵称:"))
        self.nickname_input = QLineEdit()
        self.nickname_input.setPlaceholderText("昵称（可选，方便识别）")
        layout.addWidget(self.nickname_input)

        buttons = QHBoxLayout()
        ok_btn = QPushButton("确定")
        cancel_btn = QPushButton("取消")
        ok_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(ok_btn)
        buttons.addWidget(cancel_btn)
        layout.addLayout(buttons)

    def get_values(self) -> Tuple[str, str, str]:
        return (
            self.username_input.text().strip(),
            self.password_input.text().strip(),
            self.nickname_input.text().strip(),
        )

    def resizeEvent(self, event):
        apply_background(self)
        super().resizeEvent(event)


class AccountView(QWidget):
    """账号列表视图。"""

    open_detail_requested = pyqtSignal(Account)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.account_manager = AccountManager()
        self._row_by_username: Dict[str, int] = {}
        self.init_ui()
        apply_background(self)
        self.refresh_table()

    # ------------------------------------------------------------ 界面

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        toolbar = QHBoxLayout()
        self.add_btn = QPushButton("➕ 添加账号")
        self.delete_btn = QPushButton("🗑️ 删除选中")
        self.refresh_btn = QPushButton("🔄 刷新列表")
        self.excel_import_btn = QPushButton("📥 批量导入")
        self.export_btn = QPushButton("📤 导出账号")

        self.add_btn.clicked.connect(self.add_account)
        self.delete_btn.clicked.connect(self.delete_selected)
        self.refresh_btn.clicked.connect(self.refresh_table)
        self.excel_import_btn.clicked.connect(self.import_accounts)
        self.export_btn.clicked.connect(self.export_accounts)

        for button in (
            self.add_btn,
            self.delete_btn,
            self.refresh_btn,
            self.excel_import_btn,
            self.export_btn,
        ):
            toolbar.addWidget(button)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        layout.addWidget(self._separator())

        self.account_table = QTableWidget()
        self.account_table.setColumnCount(len(COLUMNS))
        self.account_table.setHorizontalHeaderLabels(COLUMNS)
        header = self.account_table.horizontalHeader()
        for column in range(len(COLUMNS)):
            header.setSectionResizeMode(column, header.ResizeMode.Interactive)
        header.setDefaultSectionSize(100)
        header.resizeSection(0, 150)
        header.resizeSection(3, 180)
        header.setStretchLastSection(True)

        self.account_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.account_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.account_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.account_table.itemDoubleClicked.connect(self.open_detail)
        layout.addWidget(self.account_table)
        layout.addWidget(self._separator())

        status_row = QHBoxLayout()
        self.status_label = QLabel("账号数: 0")
        self.running_label = QLabel("运行中: 0")
        status_row.addWidget(self.status_label)
        status_row.addWidget(self.running_label)
        status_row.addStretch()
        layout.addLayout(status_row)

    @staticmethod
    def _separator() -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        line.setStyleSheet("background-color: #999; max-height: 2px; margin: 4px 0;")
        return line

    def resizeEvent(self, event):
        apply_background(self)
        super().resizeEvent(event)

    # ------------------------------------------------------------ 增删

    def add_account(self):
        dialog = AddAccountDialog(self)
        if dialog.exec_() != QDialog.DialogCode.Accepted:
            return
        username, password, nickname = dialog.get_values()
        if not username or not password:
            message(self, "警告", "用户名和密码不能为空")
            return

        existing = self.account_manager.get_account(username)
        if existing:
            self.highlight_account(username)
            nickname_text = f"（昵称: {existing.nickname}）" if existing.nickname else ""
            box = QMessageBox(self)
            no_help(box)
            box.setWindowTitle("重复账号")
            box.setIcon(QMessageBox.Warning)
            box.setText(f"用户名 '{username}' 已存在 {nickname_text}\n\n是否要用新密码覆盖现有账号？")
            box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            box.setDefaultButton(QMessageBox.StandardButton.No)
            if box.exec_() == QMessageBox.StandardButton.Yes:
                self.account_manager.update_account_password(username, password)
                if nickname:
                    self.account_manager.update_account_nickname(username, nickname)
                self.refresh_table()
                message(self, "成功", "账号信息已更新", QMessageBox.Information)
            return

        if self.account_manager.add_account(username, password, nickname):
            self.refresh_table()
            message(self, "成功", "账号添加成功", QMessageBox.Information)
        else:
            message(self, "警告", "该账号已存在")

    def delete_selected(self):
        rows = self.account_table.selectionModel().selectedRows()
        if not rows:
            message(self, "警告", "请先选择要删除的账号")
            return
        box = QMessageBox(self)
        no_help(box)
        box.setWindowTitle("确认")
        box.setIcon(QMessageBox.Question)
        box.setText(f"确定要删除选中的 {len(rows)} 个账号吗？")
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if box.exec_() != QMessageBox.StandardButton.Yes:
            return
        for index in sorted(rows, reverse=True):
            item = self.account_table.item(index.row(), 0)
            if item:
                self.account_manager.remove_account(item.text())
        self.refresh_table()

    def highlight_account(self, username: str) -> None:
        row = self._row_by_username.get(username)
        if row is None:
            return
        self.account_table.selectRow(row)
        item = self.account_table.item(row, 0)
        if item:
            self.account_table.scrollToItem(item)

    # ------------------------------------------------------------ 导入导出

    def import_accounts(self):
        message(
            self,
            "批量导入",
            "请选择Excel文件进行批量导入\n\n"
            "Excel文件格式要求：\n"
            "• 必须包含\"用户名\"和\"密码\"列\n"
            "• 可选包含\"昵称\"列\n"
            "• 支持中英文列名（用户名/username，密码/password，昵称/nickname）",
            QMessageBox.Information,
        )
        filepath, _ = QFileDialog.getOpenFileName(
            self, "选择Excel文件", "", "Excel文件 (*.xlsx *.xlsm)"
        )
        if not filepath:
            return
        count, error = self.import_from_excel(filepath)
        if error:
            message(self, "导入失败", error)
            return
        self.refresh_table()
        message(self, "导入成功", f"成功导入 {count} 个账号", QMessageBox.Information)

    def import_from_excel(self, filepath: str) -> Tuple[int, str]:
        """用 openpyxl 直接读表，避免引入 pandas。"""
        if not filepath.lower().endswith((".xlsx", ".xlsm")):
            return 0, "仅支持 .xlsx / .xlsm，请另存为后重试"
        try:
            from openpyxl import load_workbook

            workbook = load_workbook(filepath, read_only=True, data_only=True)
            try:
                sheet = workbook.active
                rows = sheet.iter_rows(values_only=True)
                header = next(rows, None)
                if not header:
                    return 0, "Excel 文件为空"

                names = [str(cell).strip() if cell is not None else "" for cell in header]
                username_col = self._pick_column(names, "用户名", "username")
                password_col = self._pick_column(names, "密码", "password")
                if username_col is None or password_col is None:
                    return 0, "Excel文件中缺少必要的列: 用户名/密码（或 username/password）"
                nickname_col = self._pick_column(names, "昵称", "nickname")

                count = 0
                for row in rows:
                    if not row or len(row) <= max(username_col, password_col):
                        continue
                    username = "" if row[username_col] is None else str(row[username_col]).strip()
                    password = "" if row[password_col] is None else str(row[password_col]).strip()
                    nickname = ""
                    if nickname_col is not None and len(row) > nickname_col and row[nickname_col] is not None:
                        nickname = str(row[nickname_col]).strip()
                    if username and password:
                        count += 1 if self.account_manager.add_account(username, password, nickname) else 0
                return count, ""
            finally:
                workbook.close()
        except Exception as exc:
            return 0, f"读取Excel文件时出错: {exc}"

    @staticmethod
    def _pick_column(names, *candidates):
        for candidate in candidates:
            if candidate in names:
                return names.index(candidate)
        return None

    def export_accounts(self):
        if self.account_manager.get_account_count() == 0:
            message(self, "警告", "没有账号可导出")
            return
        filepath, _ = QFileDialog.getSaveFileName(
            self, "保存账号文件", "accounts.txt", "文本文件 (*.txt);;CSV文件 (*.csv)"
        )
        if not filepath:
            return
        success, error = self.account_manager.export_to_file(filepath)
        if success:
            message(self, "成功", "账号导出成功", QMessageBox.Information)
        else:
            message(self, "导出失败", error)

    # ------------------------------------------------------------ 表格

    def refresh_table(self):
        """整表重建（显式刷新、增删之后调用）。"""
        self.account_manager.load_accounts()
        accounts = self.account_manager.get_all_accounts()
        self.account_table.setRowCount(len(accounts))
        self._row_by_username.clear()

        running = 0
        for row, acc in enumerate(accounts):
            self._row_by_username[acc.username] = row
            self.account_table.setItem(row, 0, QTableWidgetItem(acc.username))
            self.account_table.setItem(row, 1, QTableWidgetItem(acc.nickname or "-"))
            self.account_table.setItem(row, 2, self._status_item(acc.status))
            target = getattr(acc, "target_course_name", None) or "自动"
            self.account_table.setItem(row, 3, QTableWidgetItem(target))
            self.account_table.setItem(row, 4, QTableWidgetItem(acc.progress or "-"))

            manage_btn = QPushButton("管理")
            manage_btn.setProperty("username", acc.username)
            manage_btn.clicked.connect(self.on_manage_clicked)
            self.account_table.setCellWidget(row, 5, manage_btn)

            if acc.status == AccountStatus.RUNNING:
                running += 1

        self.status_label.setText(f"账号数: {len(accounts)}")
        self.running_label.setText(f"运行中: {running}")
        logger.info("账号列表刷新完成，共 %d 个账号，其中 %d 个运行中", len(accounts), running)

    @staticmethod
    def _status_item(status: str) -> QTableWidgetItem:
        item = QTableWidgetItem(status)
        color = STATUS_COLORS.get(status)
        if color is not None:
            item.setForeground(color)
        return item

    def update_account_status(self, username: str, status: str, progress: str = "", persist: bool = True):
        """只更新对应行，不重建整张表（进度回调高频调用）。"""
        self.account_manager.update_status(username, status, progress, save=persist)
        row = self._row_by_username.get(username)
        if row is None:
            return
        self.account_table.setItem(row, 2, self._status_item(status))
        self.account_table.setItem(row, 4, QTableWidgetItem(progress or "-"))

    # ------------------------------------------------------------ 打开详情

    def on_manage_clicked(self):
        button = self.sender()
        if button is None:
            return
        account = self.account_manager.get_account(button.property("username"))
        if account:
            self.open_detail_requested.emit(account)

    def open_detail(self, item):
        username_item = self.account_table.item(item.row(), 0)
        if not username_item:
            return
        account = self.account_manager.get_account(username_item.text())
        if account:
            self.open_detail_requested.emit(account)


__all__ = ["AccountView", "AddAccountDialog", "COLUMNS"]