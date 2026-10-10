import sys
import winreg

from .config import APP_NAME

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def set_autostart(enabled: bool) -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                exe = sys.executable
                if not exe.lower().endswith(".exe"):
                    return False
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, f'"{exe}" --silent')
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


def clear_autostart():
    set_autostart(False)
