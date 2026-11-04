"""账号管理器：多账号的增删改查、导入导出与持久化。

旧实现的问题与改法：

- ``open(path, "w")`` 直接覆写，写到一半崩溃会留下半截 JSON → 改为
  「临时文件 + ``os.replace``」的原子替换；
- 数据文件路径自己抄了一套 frozen/源码判断 → 统一交给 ``core.paths``；
- DEBUG 日志把整个账号字典（含明文密码）写进日志文件 → 只记录用户名，
  口令改为 DPAPI 加密落盘（``core.secure_store``）。
"""
import csv
import json
import os
from dataclasses import asdict, dataclass
from typing import ClassVar, Dict, List, Optional, Tuple

from core import secure_store
from core.atomic import write_text_atomic
from core.logger import get_logger
from core.paths import data_path

logger = get_logger("AccountManager")

DATA_FILE_NAME = "accounts.json"


@dataclass
class Account:
    """账号数据结构。"""

    username: str
    password: str
    nickname: str = ""
    status: str = "待处理"
    progress: str = ""
    # WeLearn 数字用户 ID，登录并加载单元后回填，供「继续未完成任务」精确匹配
    uid: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "Account":
        """容错构造：忽略旧版本没有的字段，也不因多余字段而报错。"""
        fields = {f for f in Account.__dataclass_fields__}
        return Account(**{k: v for k, v in data.items() if k in fields})


class AccountManager:
    """账号管理器。"""

    def __init__(self, data_file: str = None):
        self.accounts: List[Account] = []
        self.data_file = data_file or data_path(DATA_FILE_NAME)
        self._locked_accounts: set = set()
        logger.info("账号数据文件路径: %s", self.data_file)
        self.load_accounts()

    # ---------------------------------------------------------------- 查询

    def get_account(self, username: str) -> Optional[Account]:
        for acc in self.accounts:
            if acc.username == username:
                return acc
        return None

    def get_all_accounts(self) -> List[Account]:
        return self.accounts.copy()

    def get_account_count(self) -> int:
        return len(self.accounts)

    def is_password_locked(self, username: str) -> bool:
        """该账号的口令是否因换了机器而无法解密。"""
        return username in self._locked_accounts

    # ---------------------------------------------------------------- 增删改

    def add_account(self, username: str, password: str, nickname: str = "") -> bool:
        if not username:
            return False
        if any(acc.username == username for acc in self.accounts):
            logger.warning("账号已存在: %s", username)
            return False
        self.accounts.append(Account(username, password, nickname))
        self._locked_accounts.discard(username)
        self.save_accounts()
        logger.info("已添加账号: %s", username)
        return True

    def remove_account(self, username: str) -> bool:
        for index, acc in enumerate(self.accounts):
            if acc.username == username:
                self.accounts.pop(index)
                self._locked_accounts.discard(username)
                self.save_accounts()
                logger.info("已删除账号: %s", username)
                return True
        logger.warning("未找到要删除的账号: %s", username)
        return False

    def clear_accounts(self) -> None:
        self.accounts.clear()
        self._locked_accounts.clear()
        self.save_accounts()

    def update_account_password(self, username: str, new_password: str) -> bool:
        acc = self.get_account(username)
        if not acc:
            return False
        acc.password = new_password
        self._locked_accounts.discard(username)
        self.save_accounts()
        logger.info("已更新账号密码: %s", username)
        return True

    def update_account_nickname(self, username: str, new_nickname: str) -> bool:
        acc = self.get_account(username)
        if not acc:
            return False
        acc.nickname = new_nickname
        self.save_accounts()
        logger.info("已更新账号昵称: %s", username)
        return True

    def update_status(self, username: str, status: str, progress: str = "", save: bool = True) -> None:
        """更新账号状态。

        Args:
            save: 是否立刻落盘。高频进度回调传 False，由任务结束/退出时统一保存，
                避免每条进度消息都整份重写 JSON。
        """
        acc = self.get_account(username)
        if not acc:
            return
        acc.status = status
        acc.progress = progress
        if save:
            self.save_accounts()

    def set_uid(self, username: str, uid: str, save: bool = True) -> None:
        acc = self.get_account(username)
        if not acc or not uid:
            return
        acc.uid = str(uid)
        if save:
            self.save_accounts()

    def reset_all_status(self) -> None:
        for acc in self.accounts:
            acc.status = "待处理"
            acc.progress = ""

    # ---------------------------------------------------------------- 持久化

    def save_accounts(self) -> bool:
        """原子写入账号文件。"""
        try:
            payload = {"accounts": [self._to_stored(acc) for acc in self.accounts]}
            write_text_atomic(
                self.data_file,
                json.dumps(payload, ensure_ascii=False, indent=2),
                prefix=".tmp_accounts_",
            )
            logger.debug("已保存 %d 个账号到: %s", len(self.accounts), self.data_file)
            return True
        except Exception as exc:  # pragma: no cover - 磁盘异常
            logger.error("保存账号信息失败: %s", exc)
            logger.exception("保存账号信息异常详情")
            return False

    def _to_stored(self, acc: Account) -> Dict:
        data = acc.to_dict()
        data["password"] = secure_store.protect(acc.password)
        return data

    def load_accounts(self) -> None:
        """从文件加载账号，兼容明文与密文两种历史格式。"""
        if not os.path.exists(self.data_file):
            logger.warning("账号数据文件不存在: %s", self.data_file)
            self.accounts = []
            return
        try:
            with open(self.data_file, "r", encoding="utf-8") as stream:
                data = json.load(stream)
        except Exception as exc:
            logger.error("加载账号信息失败: %s", exc)
            logger.exception("加载账号信息异常详情")
            self.accounts = []
            return

        raw_accounts = data.get("accounts") if isinstance(data, dict) else None
        if not isinstance(raw_accounts, list):
            logger.error("账号数据文件格式错误：缺少 'accounts' 数组")
            self.accounts = []
            return

        self.accounts = []
        self._locked_accounts.clear()
        for item in raw_accounts:
            if not isinstance(item, dict):
                continue
            acc = Account.from_dict(item)
            stored = item.get("password", "")
            plain = secure_store.unprotect(stored)
            if plain is None:
                acc.password = ""
                self._locked_accounts.add(acc.username)
                logger.warning("账号 %s 的口令无法在本机解密（换机器/换用户），需重新输入", acc.username)
            else:
                acc.password = plain
            self.accounts.append(acc)

        logger.info("成功加载 %d 个账号", len(self.accounts))
        logger.debug("账号列表: %s", [acc.username for acc in self.accounts])

    # ---------------------------------------------------------------- 导入导出

    USERNAME_HEADERS: ClassVar[set] = {"用户名", "账号", "username", "user", "account"}
    PASSWORD_HEADERS: ClassVar[set] = {"密码", "口令", "password", "pwd", "pass"}

    @classmethod
    def _looks_like_header(cls, row) -> bool:
        """识别表头行，避免把「用户名,密码」当成一个账号导进去。"""
        if not row or len(row) < 2:
            return False
        first = str(row[0]).strip().lower()
        second = str(row[1]).strip().lower()
        return first in cls.USERNAME_HEADERS and second in cls.PASSWORD_HEADERS

    def import_from_file(self, filepath: str) -> Tuple[int, str]:
        """从 CSV/TXT 导入账号（用户名,密码[,昵称]），自动跳过表头行。"""
        try:
            imported = 0
            if filepath.lower().endswith(".csv"):
                with open(filepath, "r", encoding="utf-8-sig", newline="") as stream:
                    for row in csv.reader(stream):
                        if self._looks_like_header(row):
                            continue
                        imported += self._import_row(row)
            elif filepath.lower().endswith(".txt"):
                with open(filepath, "r", encoding="utf-8-sig") as stream:
                    for line in stream:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        parts = line.split(",")
                        if self._looks_like_header(parts):
                            continue
                        imported += self._import_row(parts)
            else:
                return 0, "不支持的文件格式，请使用 .csv 或 .txt"
            return imported, ""
        except Exception as exc:
            return 0, f"导入失败: {exc}"

    def _import_row(self, row) -> int:
        if not row or len(row) < 2:
            return 0
        username = str(row[0]).strip()
        password = str(row[1]).strip()
        nickname = str(row[2]).strip() if len(row) > 2 else ""
        if not username or not password:
            return 0
        return 1 if self.add_account(username, password, nickname) else 0

    def export_to_file(self, filepath: str) -> Tuple[bool, str]:
        """导出账号到 CSV/TXT（明文，供用户自行备份）。"""
        try:
            if filepath.lower().endswith(".csv"):
                with open(filepath, "w", encoding="utf-8-sig", newline="") as stream:
                    writer = csv.writer(stream)
                    writer.writerow(["用户名", "密码", "昵称"])
                    for acc in self.accounts:
                        writer.writerow([acc.username, acc.password, acc.nickname])
            elif filepath.lower().endswith(".txt"):
                with open(filepath, "w", encoding="utf-8") as stream:
                    for acc in self.accounts:
                        stream.write(f"{acc.username},{acc.password},{acc.nickname}\n")
            else:
                return False, "不支持的文件格式，请使用 .csv 或 .txt"
            return True, ""
        except Exception as exc:
            return False, f"导出失败: {exc}"


__all__ = ["Account", "AccountManager", "DATA_FILE_NAME"]