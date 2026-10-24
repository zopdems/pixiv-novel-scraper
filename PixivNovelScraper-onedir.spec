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
import os

# 版本号单一来源：app/version.py
import importlib.util as _ilu
_ver_spec = _ilu.spec_from_file_location("app_version", "app/version.py")
_app_version = _ilu.module_from_spec(_ver_spec)
_ver_spec.loader.exec_module(_app_version)
APP_VERSION = _app_version.__version__

# 生成 Windows 文件版本资源（version 字段需要 .txt 资源文件）
os.makedirs("build", exist_ok=True)
_ver_txt = os.path.join("build", "version_info.txt")
with open(_ver_txt, "w", encoding="utf-8") as _vf:
    _vf.write(
        "VSVersionInfo(\n"
        "  ffi=FixedFileInfo(\n"
        "    filevers=(%s, 0, 0, 0),\n"
        "    prodvers=(%s, 0, 0, 0),\n"
        "    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1,\n"
        "    subtype=0x0, date=(0, 0)\n"
        "  ),\n"
        "  kids=[\n"
        "    StringFileInfo([StringTable('040904B0', [\n"
        "      StringStruct('FileVersion', '%s'),\n"
        "      StringStruct('ProductVersion', '%s'),\n"
        "      StringStruct('ProductName', 'Pixiv Novel Scraper'),\n"
        "      StringStruct('FileDescription', 'Pixiv 小说抓取器'),\n"
        "    ])]),\n"
        "    VarFileInfo([VarStruct('Translation', [1033, 1200])])\n"
        "  ]\n"
        ")\n" % (APP_VERSION.replace('.', ','), APP_VERSION.replace('.', ','),
                APP_VERSION, APP_VERSION)
    )

datas = []
binaries = []
hiddenimports = ['win32crypt', 'PySide6.QtSvg', 'PySide6.QtSvgWidgets']
datas += collect_data_files('certifi')
tmp_ret = collect_all('ebooklib')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('curl_cffi')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# 过滤掉未被使用的重型二进制：Qt Quick/Qml/Pdf/WebEngine 的 DLL、OpenGL 软渲染、
# numpy/PIL（curl_cffi 的 hook 会顺带收集，但 app 代码从不 import）。
_UNUSED_DLL = (
    'Qt6Quick', 'Qt6Qml', 'Qt6QmlModels', 'Qt6Pdf', 'Qt6PdfWidgets',
    'Qt6WebEngineCore', 'Qt6WebEngineWidgets', 'Qt6WebChannel',
    'Qt6OpenGL', 'Qt6OpenGLWidgets', 'opengl32sw',
)
binaries = [b for b in binaries
            if not any(b[0].startswith(n) for n in _UNUSED_DLL)]
# 排除 numpy / PIL 的 Python 模块与数据
excluded_modules = [
    'numpy', 'numpy.*', 'PIL', 'PIL.*',
]


a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 登录走 pywebview（系统 WebView2），不依赖 QtWebEngine；QtPdf/QtQml/Qt3D 也未被引用。
    # 显式排除这些重模块，把打包体积从 ~190MB 压下去。
    excludes=excluded_modules + [
        'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebChannel',
        'PySide6.QtPdf', 'PySide6.QtPdfWidgets',
        'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtQuickWidgets',
        'PySide6.Qt3DCore', 'PySide6.Qt3DRender', 'PySide6.Qt3DInput',
        'PySide6.Qt3DLogic', 'PySide6.Qt3DAnimation', 'PySide6.Qt3DExtras',
    ],
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
    version=os.path.join('build', 'version_info.txt'),
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
