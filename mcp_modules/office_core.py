"""
Общий core для Microsoft Office через COM (win32com).

Synglet `Officer` удерживает ссылки на приложения Excel/Word/PowerPoint/Outlook,
переиспользует уже запущенные экземпляры через GetActiveObject и лениво
поднимает новые через Dispatch.

Порт из D:\\Desktop\\OfficeMCP (Officer.py), урезанный до 4 приложений.
"""
from __future__ import annotations

import winreg

import pywintypes
import win32com.client


SUPPORTED_APPS = ("Word", "Excel", "PowerPoint", "Outlook")


class _Officer:
    def __init__(self) -> None:
        self._apps: dict[str, object] = {}

    # ── low-level ─────────────────────────────────────────────────────────────

    def is_available(self, app_name: str) -> bool:
        prog_id = app_name if app_name.endswith(".Application") else f"{app_name}.Application"
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, prog_id):
                pass
            return True
        except OSError:
            return False

    def available_apps(self) -> list[str]:
        return [a for a in SUPPORTED_APPS if self.is_available(a)]

    def running_apps(self) -> list[str]:
        result = []
        for name in SUPPORTED_APPS:
            try:
                win32com.client.GetActiveObject(f"{name}.Application")
                result.append(name)
            except (pywintypes.com_error, FileNotFoundError):
                continue
        return result

    def application(self, app_name: str, as_new: bool = False) -> object | None:
        if app_name not in SUPPORTED_APPS:
            return None
        cached = self._apps.get(app_name)
        if cached is not None:
            try:
                _ = cached.Name  # ping
                return cached
            except Exception:
                self._apps.pop(app_name, None)

        if not self.is_available(app_name):
            return None

        full = f"{app_name}.Application"
        if as_new:
            app = win32com.client.Dispatch(full)
        else:
            try:
                app = win32com.client.GetActiveObject(full)
            except pywintypes.com_error:
                app = win32com.client.Dispatch(full)
        self._apps[app_name] = app
        return app

    def visible(self, app_name: str, value: bool | None = None) -> bool:
        app = self.application(app_name)
        if app is None:
            return False
        try:
            if value is not None:
                app.Visible = value
            return bool(app.Visible)
        except Exception:
            # Outlook не поддерживает .Visible — считаем, что GUI видим, если процесс есть
            return True

    def quit(self, app_name: str, force: bool = False) -> bool:
        app = self._apps.get(app_name)
        if app is None:
            try:
                app = win32com.client.GetActiveObject(f"{app_name}.Application")
            except Exception:
                return False
        try:
            if not force:
                app.Quit()
            else:
                import win32api, win32con, win32process
                hwnd = app.Hwnd
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                handle = win32api.OpenProcess(win32con.PROCESS_TERMINATE, 0, pid)
                if handle:
                    win32api.TerminateProcess(handle, 0)
                    win32api.CloseHandle(handle)
            self._apps.pop(app_name, None)
            return True
        except Exception:
            return False

    # ── properties-ярлыки для RunPython namespace ─────────────────────────────

    @property
    def Excel(self):       return self.application("Excel")
    @property
    def Word(self):        return self.application("Word")
    @property
    def PowerPoint(self):  return self.application("PowerPoint")
    @property
    def Outlook(self):     return self.application("Outlook")


Officer = _Officer()
