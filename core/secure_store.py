"""账号口令的本地加密存储（Windows DPAPI）。

原来 ``accounts.json`` 里是明文密码，而该文件既没被 .gitignore 忽略、
又被打包脚本塞进了安装包。这里用当前 Windows 用户的 DPAPI 主密钥加密：

- 只有同一台机器、同一个用户能解开；
- 文件被拷到别处只会变成解不开的密文，而不是直接泄漏口令；
- 解密失败时返回空串并记录告警，界面提示用户重新输入，不会崩。

非 Windows 平台（或调用失败）自动退化为明文，行为与旧版本一致。
"""
import base64
import ctypes
import sys
from typing import Optional

PREFIX = "dpapi:"
_CRYPTPROTECT_UI_FORBIDDEN = 0x01
_DESCRIPTION = "WeLearnHelper"

if sys.platform == "win32":  # pragma: no cover - 平台分支
    from ctypes import wintypes

    class _DataBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    _crypt32 = ctypes.windll.crypt32
    _kernel32 = ctypes.windll.kernel32
else:  # pragma: no cover - 平台分支
    _DataBlob = None  # type: ignore[assignment]
    _crypt32 = None
    _kernel32 = None


def is_available() -> bool:
    """当前环境是否可以用 DPAPI。"""
    return sys.platform == "win32" and _crypt32 is not None


def _blob_from(data: bytes):
    buffer = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))


def _release(blob) -> None:
    if blob.pbData:
        _kernel32.LocalFree(blob.pbData)


def protect(text: str) -> str:
    """加密明文；不可用或失败时原样返回明文（保持可用性优先）。"""
    if not text:
        return ""
    if text.startswith(PREFIX):
        return text
    if not is_available():
        return text
    try:
        raw = text.encode("utf-8")
        in_blob = _blob_from(raw)
        out_blob = _DataBlob()
        ok = _crypt32.CryptProtectData(
            ctypes.byref(in_blob),
            _DESCRIPTION,
            None,
            None,
            None,
            _CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(out_blob),
        )
        if not ok:
            return text
        try:
            encrypted = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            _release(out_blob)
        return PREFIX + base64.b64encode(encrypted).decode("ascii")
    except Exception:
        return text


def unprotect(stored: str) -> Optional[str]:
    """解密；返回 None 表示「是密文但解不开」（换了机器/用户）。"""
    if not stored:
        return ""
    if not stored.startswith(PREFIX):
        return stored
    if not is_available():
        return None
    try:
        encrypted = base64.b64decode(stored[len(PREFIX):].encode("ascii"), validate=True)
        in_blob = _blob_from(encrypted)
        out_blob = _DataBlob()
        ok = _crypt32.CryptUnprotectData(
            ctypes.byref(in_blob),
            None,
            None,
            None,
            None,
            _CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(out_blob),
        )
        if not ok:
            return None
        try:
            plain = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            _release(out_blob)
        return plain.decode("utf-8")
    except Exception:
        return None


__all__ = ["PREFIX", "is_available", "protect", "unprotect"]