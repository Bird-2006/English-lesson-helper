"""版本与产品标识。

这里只保留中性标识：不含任何个人署名、联系方式或上游仓库地址，
窗口标题、文件属性、设置目录都从这里取。
"""

APP_NAME = "WeLearn学习助手"
APP_VERSION = "5.0.12"
APP_VERSION_TAG = f"V{APP_VERSION}"
APP_TITLE = f"{APP_NAME} {APP_VERSION_TAG}"
# 设置目录与 exe 文件属性使用的厂商字段
APP_VENDOR = "WeLearnHelper"

__all__ = [
    "APP_NAME",
    "APP_VERSION",
    "APP_VERSION_TAG",
    "APP_TITLE",
    "APP_VENDOR",
]
