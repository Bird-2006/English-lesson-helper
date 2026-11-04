"""日志健壮性：轮转被占用不崩、终端不能因 emoji 抛编码错误。"""
import logging

from core.logger import _safe_stream, _SafeRotatingFileHandler


class _Reconfigurable:
    def __init__(self):
        self.kwargs = None

    def reconfigure(self, **kwargs):
        self.kwargs = kwargs


def test_safe_stream_sets_utf8_replace():
    stream = _Reconfigurable()
    assert _safe_stream(stream) is stream
    assert stream.kwargs == {"encoding": "utf-8", "errors": "replace"}


def test_safe_stream_tolerates_objects_without_reconfigure():
    class _Bare:
        pass

    bare = _Bare()
    assert _safe_stream(bare) is bare


def test_rollover_survives_locked_file(tmp_path, capsys, monkeypatch):
    target = tmp_path / "a.log"
    handler = _SafeRotatingFileHandler(str(target), maxBytes=1, backupCount=1, encoding="utf-8")

    def boom(*args, **kwargs):
        raise PermissionError(32, "文件被占用")

    monkeypatch.setattr(logging.handlers.RotatingFileHandler, "doRollover", boom)
    handler.doRollover()  # 不应抛出
    err = capsys.readouterr().err
    assert "跳过日志轮转" in err
    handler.close()


def test_rollover_works_normally(tmp_path):
    target = tmp_path / "b.log"
    handler = _SafeRotatingFileHandler(str(target), maxBytes=10, backupCount=1, encoding="utf-8")
    logger = logging.getLogger("rot-test")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    for i in range(30):
        logger.info("line %s", i)
    handler.close()
    backups = list(tmp_path.glob("b.log*"))
    assert len(backups) >= 2  # 原文件 + 至少一个 .1