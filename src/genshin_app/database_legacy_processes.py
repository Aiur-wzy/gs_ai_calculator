"""Read-only detection of packaged peers which may predate the writer fence."""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes


def packaged_peer_processes(*, include_updaters: bool = False) -> list[dict]:
    """Conservatively ask users to close other App/CLI instances, never kill.

    Toolhelp enumerates executable names in one OS snapshot; unlike checking
    a new lock, this also sees old binaries which never implemented that lock.
    No command lines, environment, credential or process memory is read.
    """
    if os.name != "nt":
        return []

    class ProcessEntry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.Process32FirstW.restype = wintypes.BOOL
    kernel.Process32NextW.argtypes = kernel.Process32FirstW.argtypes
    kernel.Process32NextW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), "无法检查其他应用实例")
    names = {"genshincalc.exe", "genshincalcgui.exe", "genshinupdater.exe"}
    if include_updaters:
        # Only callers outside the onefile updater may request this name.
        names.add("genshincalcupdater.exe")
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        present = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        found = []
        while present:
            if entry.th32ProcessID != os.getpid() and entry.szExeFile.lower() in names:
                found.append({"pid": int(entry.th32ProcessID), "name": entry.szExeFile})
            present = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        last = ctypes.get_last_error()
        if last not in (0, 18):  # ERROR_NO_MORE_FILES
            raise OSError(last, "无法完整检查其他应用实例")
        return found
    finally:
        kernel.CloseHandle(snapshot)
