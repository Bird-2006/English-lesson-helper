"""pytest 根配置：把项目根目录加入 sys.path，并让 Qt 使用无界面后端。"""
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# 部分 PyQt5 wheel 不会自动定位 platform 插件目录，显式指定一次
if "QT_QPA_PLATFORM_PLUGIN_PATH" not in os.environ:
    try:
        import PyQt5

        candidate = os.path.join(
            os.path.dirname(PyQt5.__file__), "Qt5", "plugins", "platforms"
        )
        if os.path.isdir(candidate):
            os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = candidate
    except Exception:  # PyQt5 未安装时交给 pytest.importorskip 处理
        pass

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@pytest.fixture(autouse=True)
def _never_attach_to_real_browser(monkeypatch):
    """测试期间禁止附着实机浏览器。

    用户可能正开着「浏览器模式」的 Edge（端口 9222，且已登录），
    若测试直接走 ``CDPSession.attach_to_browser`` 就会点到真实页面上去。
    需要真实浏览器的验证脚本请放在 tests/ 之外单独运行。
    """
    try:
        from core.platforms import ismart
    except Exception:  # 依赖缺失时不必阻断其它测试
        return

    def _blocked(*args, **kwargs):
        raise ismart.ISmartError("测试环境禁用真实浏览器附着")

    monkeypatch.setattr(ismart.CDPSession, "attach_to_browser", staticmethod(_blocked), raising=False)

    # 只读接口同理：测试里不允许向外网发请求（本机回环放行，供端口探测类用例使用）
    real_get = ismart.requests.get

    def _guarded_get(url, *args, **kwargs):
        host = str(url).split("/")[2] if "://" in str(url) else ""
        if host == "localhost" or host.startswith("127."):
            return real_get(url, *args, **kwargs)
        raise ConnectionError("测试环境禁用外网请求")

    monkeypatch.setattr(ismart.requests, "get", _guarded_get, raising=False)