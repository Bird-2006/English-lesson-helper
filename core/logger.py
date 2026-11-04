"""日志管理器。

修正要点（旧实现的问题）：

- ``get_logger(name)`` 返回的是 ``logging.getLogger(name)``，它是 root 的子
  logger，**不是** ``WeLearnV4`` 的子 logger，因此除了 ``core.logger.logger``
  单例以外，全项目所有 ``get_logger("StudyThread")`` 之类的日志都没进过
  日志文件。现在统一挂在 ``WeLearnV4.<name>`` 下。
- 原来的 ``FileHandler`` 没有轮转，单日日志能到 2.7MB 且永不清理，现改为
  ``RotatingFileHandler``。
"""
import logging
import os
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler

from core.paths import logs_dir
from core.version import APP_NAME, APP_VERSION_TAG

LOGGER_NAME = "WeLearnV4"
_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 5

_configured = False
_log_file = ""


class _SafeRotatingFileHandler(RotatingFileHandler):
    """轮转失败时不要让进程崩、也不要刷一屏 "Logging error"。

    Windows 上另一个实例（比如源码版和便携版同时在跑）会占着同一个日志文件，
    ``rename`` 必然拿到 ``WinError 32``。这种情况跳过本次轮转、继续往原文件追加即可。
    """

    def doRollover(self):  # noqa: N802 - 覆盖标准库命名
        try:
            super().doRollover()
        except OSError as exc:
            print(f"[logging] 跳过日志轮转（文件被占用）: {exc}", file=sys.stderr)


def _safe_stream(stream):
    """让终端输出不会因为编码问题抛异常。

    Windows 中文控制台默认 cp936，日志里带 emoji（🌐 / ⏺）就会
    ``UnicodeEncodeError``，logging 于是刷一屏 "Logging error"。
    这里把流改成 UTF-8 + 替换不可编码字符。
    """
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    return stream


def _configure() -> None:
    """配置 ``WeLearnV4`` 根 logger（只做一次）。"""
    global _configured, _log_file
    if _configured:
        return

    base = logging.getLogger(LOGGER_NAME)
    base.setLevel(logging.DEBUG)
    # 自己带 handler，不再向 root 冒泡，避免第三方库配置 root 时重复输出
    base.propagate = False

    if not base.handlers:
        today = datetime.now().strftime("%Y-%m-%d")
        _log_file = os.path.join(logs_dir(), f"WeLearnV4_{today}.log")

        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        try:
            file_handler = _SafeRotatingFileHandler(
                _log_file,
                maxBytes=_MAX_BYTES,
                backupCount=_BACKUP_COUNT,
                encoding="utf-8",
            )
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(formatter)
            base.addHandler(file_handler)
        except OSError:
            # 日志文件写不了也不能让程序起不来
            pass

        console_handler = logging.StreamHandler(_safe_stream(sys.stderr))
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        base.addHandler(console_handler)

        base.info("=" * 50)
        base.info("%s %s 启动", APP_NAME, APP_VERSION_TAG)
        if _log_file:
            base.info("日志文件路径: %s", _log_file)
        base.info("=" * 50)

    _configured = True


def get_logger(name: str = None) -> logging.Logger:
    """获取日志记录器。

    传名字时返回 ``WeLearnV4.<name>`` 子 logger，输出会进同一个日志文件。
    """
    _configure()
    if name:
        return logging.getLogger(f"{LOGGER_NAME}.{name}")
    return logging.getLogger(LOGGER_NAME)


def log_file_path() -> str:
    """当前日志文件路径（未成功创建时为空串）。"""
    _configure()
    return _log_file


def shutdown() -> None:
    """刷盘并关闭所有日志 handler，退出前调用避免丢最后几行。"""
    logging.shutdown()


# 兼容旧调用方式：`from core.logger import logger`
logger = get_logger()

__all__ = ["LOGGER_NAME", "get_logger", "log_file_path", "shutdown", "logger"]