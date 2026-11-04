# -*- mode: python ; coding: utf-8 -*-

# 打包脚本。
# 相对旧的 spec 做了这些修正：
#   - EXE 传入 version='version_info.txt'，文件属性里才有版本号（原来该文件是死文件）；
#   - datas 不再包含 accounts.json（会把开发者的账号口令打进安装包）；
#   - datas 不再把 .py 源码当数据打包（源码不该随包分发）；
#   - upx 关闭：对 Qt5 DLL 做 UPX 压缩容易触发杀软误报并导致加载失败；
#   - 不再内置图标与背景图，需要在仓库根放 app.ico / app_bg.png 后自行加回 datas。

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('style.qss', '.'),
    ],
    hiddenimports=[],
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
    name='WeLearn学习助手V5.0.12',
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
    name='WeLearn学习助手V5.0.12',
)