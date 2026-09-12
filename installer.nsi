!include "MUI2.nsh"

Name "Pixiv 小说自动抓取器"
OutFile "dist\PixivNovelScraper-Setup-v1.0.0.exe"
Unicode true
RequestExecutionLevel user
InstallDir "$LOCALAPPDATA\Programs\PixivNovelScraper"
ShowInstDetails show

!define MUI_ABORTWARNING

!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "SimpChinese"

Section "主程序（必需）" SecMain
    SectionIn RO
    SetOutPath "$INSTDIR"
    File "dist\PixivNovelScraper.exe"
    WriteUninstaller "$INSTDIR\Uninstall.exe"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\PixivNovelScraper" "DisplayName" "Pixiv 小说自动抓取器"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\PixivNovelScraper" "UninstallString" "$\"$INSTDIR\Uninstall.exe$\""
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\PixivNovelScraper" "DisplayVersion" "1.0.0"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\PixivNovelScraper" "Publisher" "Tix"
    WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\PixivNovelScraper" "NoModify" 1
    WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\PixivNovelScraper" "NoRepair" 1
SectionEnd

Section "桌面快捷方式" SecDesktop
    CreateShortcut "$DESKTOP\Pixiv 小说自动抓取器.lnk" "$INSTDIR\PixivNovelScraper.exe"
SectionEnd

LangString DESC_SecMain ${LANG_SIMPCHINESE} "程序本体。数据（配置、索引库、日志）将保存在安装目录下的 data 文件夹中，不写 C 盘用户目录。"
LangString DESC_SecDesktop ${LANG_SIMPCHINESE} "在桌面创建快捷方式。"

!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
    !insertmacro MUI_DESCRIPTION_TEXT ${SecMain} $(DESC_SecMain)
    !insertmacro MUI_DESCRIPTION_TEXT ${SecDesktop} $(DESC_SecDesktop)
!insertmacro MUI_FUNCTION_DESCRIPTION_END

Section "Uninstall"
    Delete "$INSTDIR\PixivNovelScraper.exe"
    Delete "$INSTDIR\Uninstall.exe"
    Delete "$DESKTOP\Pixiv 小说自动抓取器.lnk"
    DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\PixivNovelScraper"
    MessageBox MB_YESNO|MB_ICONQUESTION "是否同时删除 data 数据文件夹（配置、索引库、日志）？" /SD IDNO IDNO skip_data
    RMDir /r "$INSTDIR\data"
skip_data:
    RMDir "$INSTDIR"
SectionEnd
