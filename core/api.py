"""兼容层：``core.api`` 的历史导入路径仍然可用。

实际实现已迁移到 :mod:`core.platforms.welearn`（WeLearn 只是可插拔平台之一）。
保留本模块是为了让旧的 ``from core.api import WeLearnClient`` 继续工作。
"""
from core.platforms.welearn import (
    DEFAULT_TIMEOUT,
    MAX_LOGIN_RETRY,
    WeLearnClient,
    classify_exception,
    parse_prelogin_url,
)

__all__ = [
    "WeLearnClient",
    "parse_prelogin_url",
    "classify_exception",
    "DEFAULT_TIMEOUT",
    "MAX_LOGIN_RETRY",
]