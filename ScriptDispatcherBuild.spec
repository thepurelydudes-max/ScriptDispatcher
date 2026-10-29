# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = []  # Never bundle local cookies or other credentials
binaries = []
hiddenimports = [
    'openpyxl',
    'bs4',
    'mutagen',
    'requests',
    'dispatcher.modules.broadcast',
    'dispatcher.modules.music',
    'dispatcher.modules.reports',
    'dispatcher.modules.web_tools',
]
hiddenimports += collect_submodules('dispatcher.modules')
hiddenimports = sorted(set(hiddenimports))

tmp_ret = collect_all('langdetect')
datas += tmp_ret[0]
binaries += tmp_ret[1]
hiddenimports += tmp_ret[2]

tmp_ret = collect_all('pydub')
datas += tmp_ret[0]
binaries += tmp_ret[1]
hiddenimports += tmp_ret[2]

a = Analysis(
    ['app.py'],
    pathex=['.'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ScriptDispatcher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon=['ScriptDispatcher.ico'],
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ScriptDispatcher',
)
