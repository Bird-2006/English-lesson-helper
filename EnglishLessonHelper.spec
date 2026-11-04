# -*- mode: python ; coding: utf-8 -*-

# 打包脚本（English Lesson Helper，onedir）。
#
# 说明：
#   - EXE 传入 version='version_info.txt'，文件属性里才有版本号；
#   - datas 只带 style.qss；不带 accounts.json / task_progress.json 等本机数据；
#   - 不把 .py 源码当数据打包（源码不该随包分发）；
#   - 不内置图标与背景图，需要时在仓库根放 app.ico / app_bg.png 后自行加回 datas；
#   - upx 关闭：对 Qt5 DLL 做 UPX 压缩容易触发杀软误报并导致加载失败。

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
    [],
    exclude_binaries=True,
    name='EnglishLessonHelper',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='version_info.txt',
    uac_admin=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='EnglishLessonHelper',
)