"""原子文件写入。

账号文件与任务进度文件都在运行期被反复覆写，直接 ``open(path, "w")`` 一旦
中途崩溃/断电就会留下半截 JSON，下次启动直接读不出来。这里统一成
「同目录临时文件 → fsync → os.replace」的替换语义：

- 同目录保证 ``os.replace`` 不跨卷，是原子操作；
- Windows 上必须先关闭临时文件句柄再 replace，否则会撞共享冲突。
"""
import os
import tempfile


def write_text_atomic(path: str, text: str, prefix: str = ".tmp_") -> None:
    """把 ``text`` 原子地写入 ``path``。"""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    if not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)

    temp_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=directory, prefix=prefix, delete=False
        ) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
            temp_name = handle.name
        os.replace(temp_name, path)
        temp_name = ""
    finally:
        if temp_name and os.path.exists(temp_name):
            try:
                os.unlink(temp_name)
            except OSError:
                pass


__all__ = ["write_text_atomic"]