$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

pip install -r requirements.txt pyinstaller

# 版本号唯一来源：app/version.py
$VERSION = (python -c "import importlib.util as u; s=u.spec_from_file_location('v','app/version.py'); m=u.module_from_spec(s); s.loader.exec_module(m); print(m.__version__)")

# 目录版（onedir，误报率更低，推荐分发）
python -m PyInstaller --noconfirm --clean PixivNovelScraper-onedir.spec

# 生成安装包（版本号从 version.py 自动传入，不再散落多处）
& "C:\Program Files (x86)\NSIS\makensis.exe" -DVERSION=$VERSION installer-onedir.nsi

Write-Host "打包完成：dist\PixivNovelScraper-Setup-v${VERSION}-onedir.exe"
