"""Thin current-user Windows registration. No release or removal authority."""
from __future__ import annotations

import json
import os
import re
import subprocess
from contextlib import contextmanager
from pathlib import Path

PRODUCT_KEY = r"Software\GenshinCalc\Stable"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\GenshinCalcStable"
DISPLAY_NAME = "原神拉表（正式版）"
APP_USER_MODEL_ID = "Aiur.GenshinCalc.Stable"


class WindowsProductRegistration:
    def __init__(self, *, qa_run_id=None):
        # Only a generated run token is injectable. No test caller can choose
        # a production key/name; the OS operations themselves stay identical.
        if qa_run_id is not None and (not isinstance(qa_run_id, str)
                                     or re.fullmatch(r"[a-f0-9]{32}", qa_run_id) is None):
            raise ValueError("product_qa_registration_identity_invalid")
        self.qa_run_id = qa_run_id

    @property
    def product_key(self):
        return rf"Software\GenshinCalc\QA\{self.qa_run_id}" if self.qa_run_id else PRODUCT_KEY

    @property
    def uninstall_key(self):
        return (rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\GenshinCalcQA-{self.qa_run_id}"
                if self.qa_run_id else UNINSTALL_KEY)

    @property
    def display_name(self):
        return f"原神拉表（安装验收 QA {self.qa_run_id[:8]}）" if self.qa_run_id else DISPLAY_NAME

    @property
    def app_user_model_id(self):
        return f"Aiur.GenshinCalc.QA.{self.qa_run_id}" if self.qa_run_id else APP_USER_MODEL_ID

    def process_app_id(self, role="app"):
        if role not in {"app", "setup", "uninstall"}:
            raise ValueError("product_app_role_invalid")
        return self.app_user_model_id + {"app": "", "setup": ".Setup", "uninstall": ".Uninstall"}[role]

    def programs_folder(self, base):
        return Path(base) / f"GenshinCalc-QA-{self.qa_run_id}" if self.qa_run_id else Path(base)

    @contextmanager
    def mutation_lock(self):
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        kernel.CreateMutexW.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel.WaitForSingleObject.restype = ctypes.c_uint32
        kernel.ReleaseMutex.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.CreateMutexW(None, False, f"Local\\{self.app_user_model_id}.Installation")
        if not handle:
            raise OSError(ctypes.get_last_error(), "product_install_registration_lock_failed")
        acquired = False
        try:
            acquired = kernel.WaitForSingleObject(handle, 0) in (0, 0x80)
            if not acquired:
                raise OSError("product_first_install_busy")
            yield
        finally:
            if acquired:
                kernel.ReleaseMutex(handle)
            kernel.CloseHandle(handle)

    def read_location(self):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.product_key) as key:
                value, kind = winreg.QueryValueEx(key, "Location")
                if kind != winreg.REG_SZ:
                    raise ValueError("location kind")
                row = json.loads(value)
                if not isinstance(row, dict) or not isinstance(row.get("root"), str):
                    raise TypeError("location format")
                return row
        except FileNotFoundError:
            return None

    def write_location(self, row):
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, self.product_key, 0, winreg.KEY_WRITE) as key:
            winreg.SetValueEx(key, "Location", 0, winreg.REG_SZ, json.dumps(row, ensure_ascii=False, sort_keys=True))
            winreg.FlushKey(key)

    def remove_location(self, installation_id):
        import winreg
        current = self.read_location()
        if current is None or current.get("installation_id") != installation_id:
            return
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.product_key, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, "Location")

    def register_app(self, *, root, installation_id, version, executable):
        import winreg
        values = {"DisplayName": self.display_name, "DisplayVersion": version, "Publisher": "Aiur-wzy",
                  "InstallLocation": str(root), "DisplayIcon": str(Path(root) / "app" / "GenshinCalcGUI.exe") + ",0",
                  "UninstallString": subprocess.list2cmdline([str(executable), "--uninstall", "--install-root", str(root)]),
                  "URLInfoAbout": "https://github.com/Aiur-wzy/gs_ai_calculator",
                  "InstallationId": installation_id}
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, self.uninstall_key, 0, winreg.KEY_WRITE) as key:
            for name, value in values.items():
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
            for name in ("NoModify", "NoRepair"):
                winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, 1)
            winreg.FlushKey(key)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.uninstall_key) as key:
            if any(winreg.QueryValueEx(key, name)[0] != value for name, value in values.items()):
                raise OSError("product_windows_registration_readback_failed")

    def unregister_app(self, installation_id):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.uninstall_key) as key:
                if winreg.QueryValueEx(key, "InstallationId")[0] != installation_id:
                    return
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, self.uninstall_key)
        except FileNotFoundError:
            pass


def current_build_registration():
    """Production by default; only a dedicated QA build carries this marker."""
    from . import version
    token = getattr(version, "INSTALLATION_QA_RUN_ID", None)
    if token is not None and ("-qa" not in version.APP_RELEASE_ID or version.APP_DIR_NAME != "GenshinCalcQA"):
        raise ValueError("product_qa_registration_build_identity_invalid")
    return WindowsProductRegistration(qa_run_id=token)


def set_process_app_id(*, role="app"):
    if os.name != "nt":
        return
    import ctypes
    set_id = ctypes.WinDLL("shell32").SetCurrentProcessExplicitAppUserModelID
    set_id.argtypes = [ctypes.c_wchar_p]
    set_id.restype = ctypes.c_long
    if set_id(current_build_registration().process_app_id(role)) != 0:
        raise OSError("product_app_user_model_id_failed")
