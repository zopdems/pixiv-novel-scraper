# -*- mode: python ; coding: utf-8 -*-
"""onedir（目录版）打包配置。

与 PixivNovelScraper.spec 的区别：不把依赖压进单个 exe，
而是输出一个文件夹（内含 exe + _internal）。
少了单文件的自解压 stub，杀毒软件启发式误报率通常更低。
用法：pyinstaller --noconfirm PixivNovelScraper-onedir.spec
输出：dist\PixivNovelScraper-OneDir\PixivNovelScraper.exe
"""
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = ['win32crypt', 'PySide6.QtSvg', 'PySide6.QtSvgWidgets']
datas += collect_data_files('certifi')
tmp_ret = collect_all('ebooklib')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('curl_cffi')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['run.py'],
    pathex=[],
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
    name='PixivNovelScraper',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='PixivNovelScraper-OneDir',
)
