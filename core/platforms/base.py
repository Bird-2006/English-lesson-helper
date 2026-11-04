"""平台适配层：把不同学习平台抽象成同一套接口。

设计目标：上层（界面、线程、任务进度）只认这套接口，不关心背后是
WeLearn 的 HTTP 接口还是 iSmart 的本地客户端页面。新增平台只要实现
:class:`PlatformClient` 并注册到 ``core.platforms`` 即可。

方法名沿用原来 WeLearn 客户端的命名（``get_sco_leaves`` /
``submit_course_progress`` / ``simulate_time``），这样 UI 与 workers 无需改动。
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, FrozenSet, List, Optional, Tuple


class Capability(str, Enum):
    """平台能力标记，界面据此决定显示哪些控件。"""

    HOMEWORK = "homework"  # 支持刷作业
    DURATION = "duration"  # 支持刷时长
    CDP = "cdp"  # 通过 Chrome DevTools Protocol 驱动本地客户端


@dataclass(frozen=True)
class PlatformInfo:
    """平台元信息。"""

    key: str
    name: str
    description: str = ""
    capabilities: FrozenSet[Capability] = field(default_factory=frozenset)

    def supports(self, capability: Capability) -> bool:
        return capability in self.capabilities


class PlatformClient(ABC):
    """单个账号在某平台上的会话。"""

    info: PlatformInfo
    #: 最近一次失败的错误分类（见 ``core.constants.ErrCode``）
    last_error_code: str = ""

    def supports(self, capability: Capability) -> bool:
        return self.info.supports(capability)

    # ---------------------------------------------------------------- 会话

    @abstractmethod
    def login(self, username: str, password: str) -> Tuple[bool, str]:
        """登录（iSmart 这类平台以本地客户端登录态为准）。"""

    @abstractmethod
    def close(self) -> None:
        """释放会话。"""

    # ---------------------------------------------------------------- 课程结构

    @abstractmethod
    def get_courses(self) -> Tuple[bool, List[Dict], str]:
        """课程列表，元素至少含 ``cid`` / ``name`` / ``per``。"""

    @abstractmethod
    def get_course_info(self, cid) -> Tuple[bool, Optional[Dict], str]:
        """课程详情，返回 ``{"uid": str, "classid": str, "units": list}``。"""

    @abstractmethod
    def get_sco_leaves(self, cid, uid, classid, unit_idx) -> Tuple[bool, List[Dict], str]:
        """单元内的学习任务，元素至少含 ``id`` / ``location`` / ``isvisible`` / ``iscomplete``。"""

    # ---------------------------------------------------------------- 任务

    @abstractmethod
    def submit_course_progress(self, cid, uid, classid, scoid, accuracy) -> Tuple[int, int, int, int]:
        """完成一个学习任务。

        Returns:
            ``(方式1成功, 方式1失败, 方式2成功, 方式2失败)``
        """

    @abstractmethod
    def simulate_time(self, cid, uid, scoid, learning_time, stop_event=None) -> bool:
        """挂学习时长；平台不支持时应返回 ``False`` 并记录原因。"""


__all__ = [
    "Capability",
    "PlatformInfo",
    "PlatformClient",
]