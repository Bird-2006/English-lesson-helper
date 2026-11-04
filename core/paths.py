"""路径解析统一入口。

原来 ``get_background_path`` / ``set_background`` / ``AccountManager`` /
``Logger`` 各自抄了一份「源码运行 vs PyInstaller 打包」的分支判断，
这里收敛成一份，避免四处改动不同步。
"""
import os
import sys
from typing import Optional

_INTERNAL_DIR = "_internal"


def is_frozen() -> bool:
    """是否运行在打包后的可执行文件中。"""
    return bool(getattr(sys, "frozen", False))


def app_dir() -> str:
    """可写数据目录：源码运行=项目根目录；打包=exe 所在目录。"""
    if is_frozen():
        if hasattr(sys, "_MEIPASS"):
            # PyInstaller 打包：数据要写回 exe 旁边，而不是临时解包目录
            return os.path.dirname(sys.executable)
        return os.path.dirname(os.path.abspath(sys.argv[0]))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_dirs() -> list:
    """资源查找目录，按优先级排列。"""
    dirs = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(meipass)
    base = app_dir()
    dirs.append(base)
    dirs.append(os.path.join(base, _INTERNAL_DIR))
    return dirs


def resource_path(name: str) -> Optional[str]:
    """查找只读资源（图标、背景图、样式表），找不到返回 None。"""
    for directory in resource_dirs():
        candidate = os.path.join(directory, name)
        if os.path.exists(candidate):
            return candidate
    return None


def data_path(name: str, use_cwd_fallback: bool = True) -> str:
    """数据文件路径。

    固定锚定到 ``app_dir()``，避免从快捷方式/其他工作目录启动时把
    数据写到意料之外的位置。若历史版本已经把文件写在工作目录里，
    优先返回那个文件以免丢数据。
    """
    primary = os.path.join(app_dir(), name)
    if not os.path.exists(primary) and use_cwd_fallback:
        legacy = os.path.join(os.getcwd(), name)
        if os.path.exists(legacy):
            return legacy
    return primary


def logs_dir() -> str:
    """日志目录，不存在时创建。"""
    directory = os.path.join(app_dir(), "logs")
    os.makedirs(directory, exist_ok=True)
    return directory


__all__ = [
    "is_frozen",
    "app_dir",
    "resource_dirs",
    "resource_path",
    "data_path",
    "logs_dir",
]