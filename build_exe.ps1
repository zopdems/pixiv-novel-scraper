$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

pip install -r requirements.txt pyinstaller

python -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name PixivNovelScraper `
    --collect-all ebooklib `
    --collect-all curl_cffi `
    --hidden-import win32crypt `
    run.py

Write-Host "打包完成：dist\PixivNovelScraper.exe"
