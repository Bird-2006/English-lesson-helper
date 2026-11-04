"""pytest 根配置：把项目根目录加入 sys.path，并让 Qt 使用无界面后端。"""
import os
import sys

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