"""跨模块共享的常量。

进度信号原来靠裸字符串在 ``workers`` 与 ``account_detail`` 之间约定，
拼错一个字母不报错、只是分支不进，这里统一成常量。
"""


class ProgressEvent:
    """``progress_update`` 信号的 status 取值。"""

    START = "start"
    FINISH = "finish"
    SKIP = "skip"
    COMPLETED = "completed"
    ERROR = "error"
    INFO = "info"
    UNIT_START = "unit_start"
    UNIT_FINISH = "unit_finish"
    PROGRESS = "progress"
    PROGRESS_TOTAL = "progress_total"
    PROGRESS_CURRENT = "progress_current"


class AccountStatus:
    """账号状态文案（同时用于界面着色判断）。"""

    PENDING = "待处理"
    LOGGING_IN = "登录中"
    LOGGED_IN = "已登录"
    RUNNING = "运行中"
    COMPLETED = "已完成"
    STOPPED = "已停止"
    FAILED = "失败"
    LOGIN_FAILED = "登录失败"


class TaskType:
    """任务类型文案，写入 task_progress.json。"""

    HOMEWORK = "刷作业"
    DURATION = "刷时长"


class ErrCode:
    """错误分类码，供界面分支判断，替代对中文错误串做子串匹配。"""

    NETWORK = "network"
    TIMEOUT = "timeout"
    AUTH = "auth"
    SERVER = "server"
    PARSE = "parse"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


__all__ = ["ProgressEvent", "AccountStatus", "TaskType", "ErrCode"]