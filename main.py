"""WeLearn 学习助手 - 程序入口。

只做三件事：装全局异常钩子、拉起主窗口、退出前把日志刷盘。
"""
import sys
import threading
import traceback

from PyQt5.QtWidgets import QApplication, QMessageBox

from core.logger import get_logger
from core.logger import shutdown as shutdown_logging
from core.version import APP_TITLE
from ui.main_window import WeLearnUI

logger = get_logger("Main")


def exception_hook(exctype, value, traceback_obj):
    """全局异常钩子：记录、提示，但不让程序闪退。"""
    message = "".join(traceback.format_exception(exctype, value, traceback_obj))
    print(f"未捕获的异常:\n{message}", file=sys.stderr)
    logger.critical("未捕获的异常:\n%s", message)

    box = QMessageBox()
    box.setIcon(QMessageBox.Icon.Critical)
    box.setWindowTitle("程序错误")
    box.setText("程序发生错误，但不会退出")
    box.setDetailedText(message)
    box.exec_()


def _wait_background_threads(timeout: float = 2.0) -> None:
    """退出前给后台线程一点收尾时间，并记录残留。"""
    current = threading.current_thread()
    stragglers = [
        thread
        for thread in threading.enumerate()
        if thread is not current and thread.is_alive() and not isinstance(thread, threading._DummyThread)
    ]
    for thread in stragglers:
        logger.info("等待线程结束: %s (ID: %s)", thread.name, thread.ident)
        thread.join(timeout=timeout)
    alive = [thread.name for thread in stragglers if thread.is_alive()]
    if alive:
        logger.warning("退出时仍有线程未结束: %s", ", ".join(alive))


def main() -> int:
    sys.excepthook = exception_hook
    logger.info("启动 %s", APP_TITLE)

    app = QApplication(sys.argv)
    window = WeLearnUI()
    window.show()
    logger.info("主窗口已显示")

    exit_code = app.exec_()
    logger.info("程序退出，退出代码: %s", exit_code)
    _wait_background_threads()
    shutdown_logging()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())