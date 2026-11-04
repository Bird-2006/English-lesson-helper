"""平台注册表：按 key 创建客户端，界面从这里取可用平台列表。"""
from typing import Dict, List, Type

from core.platforms.base import Capability, PlatformClient, PlatformInfo
from core.platforms.ismart import PLATFORM as ISMART_INFO
from core.platforms.ismart import ISmartClient
from core.platforms.welearn import PLATFORM as WELEARN_INFO
from core.platforms.welearn import WeLearnClient

DEFAULT_PLATFORM = WELEARN_INFO.key

#: key -> 客户端类型
CLIENTS: Dict[str, Type[PlatformClient]] = {
    WELEARN_INFO.key: WeLearnClient,
    ISMART_INFO.key: ISmartClient,
}

#: 展示顺序即界面下拉框顺序
PLATFORMS: List[PlatformInfo] = [WELEARN_INFO, ISMART_INFO]


def platform_names() -> List[str]:
    """界面用的 ``["WeLearn（…）", "iSmart（…）"]`` 列表。"""
    return [info.name for info in PLATFORMS]


def get_info(key: str) -> PlatformInfo:
    for info in PLATFORMS:
        if info.key == key:
            return info
    return WELEARN_INFO


def key_from_name(name: str) -> str:
    """按展示名反查 key，找不到时退回默认平台。"""
    for info in PLATFORMS:
        if info.name == name:
            return info.key
    return DEFAULT_PLATFORM


def name_from_key(key: str) -> str:
    return get_info(key).name


def create_client(key: str = DEFAULT_PLATFORM) -> PlatformClient:
    """创建客户端；未知平台退回默认平台。"""
    factory = CLIENTS.get(key) or CLIENTS[DEFAULT_PLATFORM]
    return factory()


def supports(key: str, capability: Capability) -> bool:
    return get_info(key).supports(capability)


__all__ = [
    "DEFAULT_PLATFORM",
    "PLATFORMS",
    "CLIENTS",
    "platform_names",
    "get_info",
    "key_from_name",
    "name_from_key",
    "create_client",
    "supports",
]