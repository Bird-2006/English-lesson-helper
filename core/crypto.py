"""登录口令编码。

WeLearn 的 SSO 不传明文口令，而是 ``base64(f"{T1}*" + password_hex)``：

- ``T0`` 为毫秒时间戳；
- ``V`` 从 ``T0`` 的高位字节开始，逐字节异或口令内容；
- ``T1`` 保留 ``T0`` 的前面部分，末两位替换为 ``V % 100``，服务端据此校验。

算法本身没动，只把逐字节格式化换成内建 ``bytes.hex()``。
"""
import base64
import time
from typing import List


def to_hex_byte_array(byte_array: bytes) -> str:
    """将字节数组转换为十六进制字符串。"""
    return byte_array.hex()


def _timestamp_part(password: bytes, now_ms: int) -> int:
    """根据口令内容计算带校验位的时间戳 ``T1``。"""
    checksum = (now_ms >> 16) & 0xFF
    for byte in password:
        checksum ^= byte
    return (now_ms // 100) * 100 + checksum % 100


def generate_cipher_text(password: str, now_ms: int = None) -> List[str]:
    """生成加密后的密码。

    Args:
        password: 明文口令。
        now_ms: 毫秒时间戳，默认取当前时间；测试时可注入固定值。

    Returns:
        ``[加密后的密码, 时间戳]``
    """
    timestamp = int(round(time.time() * 1000)) if now_ms is None else int(now_ms)
    payload = password.encode("utf-8")
    t1 = _timestamp_part(payload, timestamp)
    raw = f"{t1}*{to_hex_byte_array(payload)}"
    return [base64.b64encode(raw.encode("utf-8")).decode("utf-8"), str(t1)]


__all__ = ["to_hex_byte_array", "generate_cipher_text"]