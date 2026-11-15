"""Windows path authorities used across privilege boundaries.

Environment variables are user-controlled process input and therefore cannot
select an executable or a write target for an elevated updater.  This module
resolves the two product roots through ``SHGetKnownFolderPath`` and provides a
small reparse-chain check for callers which cross from an unelevated process
into an administrator-owned directory.
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
from uuid import UUID

FOLDERID_PROGRAM_DATA = "62ab5d82-fdc1-4dc3-a9dd-070d1d495d97"
FOLDERID_LOCAL_APP_DATA = "f1b32785-6fba-4fcf-9d55-7b8e7f157091"


class WindowsPathAuthorityError(RuntimeError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def parse(cls, value: str) -> _GUID:
        raw = UUID(value).bytes_le
        return cls(
            int.from_bytes(raw[0:4], "little"),
            int.from_bytes(raw[4:6], "little"),
            int.from_bytes(raw[6:8], "little"),
            (ctypes.c_ubyte * 8)(*raw[8:16]),
        )


def known_folder_path(folder_id: str) -> Path:
    """Resolve a Windows Known Folder without consulting the environment."""

    if os.name != "nt":
        raise WindowsPathAuthorityError("windows_known_folder_unavailable")
    identifier = _GUID.parse(folder_id)
    value = ctypes.c_wchar_p()
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.SHGetKnownFolderPath.argtypes = [
        ctypes.POINTER(_GUID),
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_wchar_p),
    ]
    shell.SHGetKnownFolderPath.restype = ctypes.c_long
    result = shell.SHGetKnownFolderPath(
        ctypes.byref(identifier),
        0,
        None,
        ctypes.byref(value),
    )
    if result != 0 or not value.value:
        raise WindowsPathAuthorityError("windows_known_folder_unavailable")
    try:
        return Path(value.value)
    finally:
        ole = ctypes.WinDLL("ole32", use_last_error=True)
        ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        ole.CoTaskMemFree.restype = None
        ole.CoTaskMemFree(ctypes.cast(value, ctypes.c_void_p))


def is_windows_elevated() -> bool:
    """Check the token before a current-user operation uses Windows paths."""
    if os.name != "nt":
        raise WindowsPathAuthorityError("windows_known_folder_unavailable")
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    check = shell.IsUserAnAdmin
    check.argtypes = []
    check.restype = ctypes.c_int
    return bool(check())


def is_reparse_point(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        junction = getattr(path, "is_junction", None)
        if callable(junction) and junction():
            return True
        return bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)
    except OSError:
        return True


def reject_reparse_chain(path: Path, *, reason: str) -> Path:
    """Resolve an existing path only if every existing component is direct.

    Checking only the leaf after ``Path.resolve`` is insufficient because the
    resolved spelling has already hidden any junction in a parent component.
    """

    supplied = Path(path).absolute()
    anchor = Path(supplied.anchor)
    current = anchor
    parts = supplied.parts[1:] if supplied.anchor else supplied.parts
    for part in parts:
        current = current / part
        if not current.exists():
            raise WindowsPathAuthorityError(reason)
        if is_reparse_point(current):
            raise WindowsPathAuthorityError(reason)
    try:
        resolved = supplied.resolve(strict=True)
    except OSError as error:
        raise WindowsPathAuthorityError(reason) from error
    return resolved


def reject_effective_write_access(path: Path, *, reason: str) -> None:
    """Fail if the current unelevated token can modify an admin-owned path.

    This is an effective-access backstop in addition to the installer's ACL;
    it is intentionally conservative.  A GUI launched elevated will disable
    online updating rather than weaken the helper trust boundary.
    """

    if os.name != "nt":
        return
    target = Path(path)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.c_bool
    share_all = 0x00000001 | 0x00000002 | 0x00000004
    open_existing = 3
    backup_semantics = 0x02000000 if target.is_dir() else 0
    # A writable file or a directory in which the token can add/delete a file
    # is sufficient to replace the verified updater after the hash check.
    requested = (
        (0x00000002, 0x00000004, 0x00000040, 0x00010000)
        if target.is_dir()
        else (0x00000002, 0x00000004, 0x00010000, 0x00040000)
    )
    invalid = ctypes.c_void_p(-1).value
    for access in requested:
        ctypes.set_last_error(0)
        handle = kernel.CreateFileW(
            str(target),
            access,
            share_all,
            None,
            open_existing,
            backup_semantics,
            None,
        )
        if handle not in (None, invalid):
            kernel.CloseHandle(handle)
            raise WindowsPathAuthorityError(reason)
        if ctypes.get_last_error() != 5:  # ERROR_ACCESS_DENIED is the safe result.
            raise WindowsPathAuthorityError(reason)


__all__ = [
    "FOLDERID_LOCAL_APP_DATA",
    "FOLDERID_PROGRAM_DATA",
    "WindowsPathAuthorityError",
    "is_reparse_point",
    "is_windows_elevated",
    "known_folder_path",
    "reject_effective_write_access",
    "reject_reparse_chain",
]
