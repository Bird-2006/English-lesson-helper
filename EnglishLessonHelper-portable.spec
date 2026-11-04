# -*- mode: python ; coding: utf-8 -*-

# 便携版（单文件 onefile）打包脚本：产物是一个可直接双击的 exe。
#
# 与 EnglishLessonHelper.spec（onedir）的区别：
#   - onefile：所有依赖打进 exe，运行时解包到临时目录，产物只有一个文件；
#   - uac_admin=False：双击即开，不弹 UAC（「网络工具箱」的修复动作仍会检测权限，
#     需要时用「以管理员身份运行」启动即可）；
#   - 数据（accounts.json / task_progress.json / ismart_profile.json / logs/）
#     写在 exe 所在目录，符合便携使用习惯（放 U 盘/文件夹里即用）。
#
# 注意：PyInstaller 构建要求解释器与源码都在纯 ASCII 路径下，
# 否则 Qt 会把路径前缀转成 "?"，报 "Qt plugin directory ... does not exist"。

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('style.qss', '.'),
    ],
    hiddenimports=['websocket'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pandas', 'matplotlib', 'numpy', 'scipy', 'tkinter', 'test'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='EnglishLessonHelper-portable',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='version_info.txt',
    uac_admin=False,
)