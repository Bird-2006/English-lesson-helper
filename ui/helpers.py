"""界面公共工具：消息框、图标、背景图缓存。

解决的问题：

- 全项目 32 处重复写 ``setWindowFlags(... & ~Qt.WindowContextHelpButtonHint)``；
- 4 个类各自实现了一遍「源码/打包」资源路径判断；
- 每次 ``resizeEvent`` 都把上 megabytes 的背景图重新缩放一次，拖窗口明显掉帧。
"""
from typing import Dict, Optional, Tuple

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QBrush, QIcon, QPixmap
from PyQt5.QtWidgets import QMessageBox, QWidget

from core.paths import resource_path

# 资源文件名保持中性，替换成自己的图标/背景图时直接覆盖同名文件即可
ICON_NAME = "app.ico"
BACKGROUND_NAME = "app_bg.png"

_SOURCE_CACHE: Dict[str, QPixmap] = {}
_SCALED_CACHE: Dict[Tuple[str, int, int], QPixmap] = {}
_SCALED_CACHE_LIMIT = 8


def no_help(widget: QWidget) -> QWidget:
    """去掉右上角无用的问号按钮。"""
    widget.setWindowFlags(widget.windowFlags() & ~Qt.WindowContextHelpButtonHint)
    return widget


def message(
    parent: Optional[QWidget],
    title: str,
    text: str,
    icon: QMessageBox.Icon = QMessageBox.Warning,
    buttons=None,
    default=None,
) -> int:
    """统一的提示框工厂（自动去掉问号按钮）。"""
    box = QMessageBox(parent)
    no_help(box)
    box.setWindowTitle(title)
    box.setIcon(icon)
    box.setText(text)
    if buttons is not None:
        box.setStandardButtons(buttons)
    if default is not None:
        box.setDefaultButton(default)
    return box.exec_()


def app_icon() -> QIcon:
    """应用图标；找不到时返回空图标（Qt 会退回默认）。"""
    path = resource_path(ICON_NAME)
    return QIcon(path) if path else QIcon()


def background_pixmap(size: Optional[Tuple[int, int]] = None) -> Optional[QPixmap]:
    """按尺寸缓存缩放后的背景图，避免每次 resize 都重新解码缩放。"""
    path = resource_path(BACKGROUND_NAME)
    if not path:
        return None

    source = _SOURCE_CACHE.get(path)
    if source is None:
        loaded = QPixmap(path)
        if loaded.isNull():
            return None
        _SOURCE_CACHE[path] = loaded
        source = loaded

    if size is None:
        return source
    width, height = int(size[0]), int(size[1])
    if width <= 0 or height <= 0:
        return source

    key = (path, width, height)
    cached = _SCALED_CACHE.get(key)
    if cached is not None:
        return cached

    if len(_SCALED_CACHE) >= _SCALED_CACHE_LIMIT:
        _SCALED_CACHE.pop(next(iter(_SCALED_CACHE)))
    scaled = source.scaled(width, height, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    _SCALED_CACHE[key] = scaled
    return scaled


def apply_background(widget: QWidget) -> bool:
    """给控件铺背景图，成功返回 True。"""
    pixmap = background_pixmap((widget.width(), widget.height()))
    if pixmap is None:
        return False
    palette = widget.palette()
    palette.setBrush(widget.backgroundRole(), QBrush(pixmap))
    widget.setPalette(palette)
    return True


__all__ = [
    "ICON_NAME",
    "BACKGROUND_NAME",
    "no_help",
    "message",
    "app_icon",
    "background_pixmap",
    "apply_background",
]