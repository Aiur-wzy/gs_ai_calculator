"""Current-user installation locations, independent of the system drive.

Location records select a product; signed release metadata still authorizes
its payload. No caller may derive deletion authority from this module alone.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .product_install_contract import (
    FIRST_INSTALL_CONTRACT,
    REGISTRATION_FILENAME,
    FirstInstallError,
)
from .windows_known_folders import is_reparse_point, reject_reparse_chain

LAYOUT_VERSION = 2
ACTIVE_STATES = {"installed", "registration_pending"}


def checked_path(path: Path | str, *, reason="product_install_path_invalid") -> Path:
    """Reject links before resolving; permit missing leaves for a new install."""
    supplied = Path(path).absolute()
    if supplied == Path(supplied.anchor) or ".." in supplied.parts:
        raise FirstInstallError(reason)
    existing = supplied
    while not os.path.lexists(existing):
        if existing == existing.parent:
            raise FirstInstallError(reason)
        existing = existing.parent
    try:
        ancestor = reject_reparse_chain(existing, reason=reason)
    except RuntimeError as error:
        raise FirstInstallError(reason) from error
    if not ancestor.is_dir() and existing != supplied:
        raise FirstInstallError(reason)
    return ancestor.joinpath(*supplied.relative_to(existing).parts)


def path_identity(path: Path | str) -> dict:
    target = checked_path(path)
    ancestor = target
    while not ancestor.exists():
        ancestor = ancestor.parent
    stat = ancestor.stat()
    return {"path": str(target), "ancestor": str(ancestor), "device": stat.st_dev, "inode": stat.st_ino}


def recheck_identity(identity: dict) -> Path:
    path = checked_path(identity["path"])
    ancestor = checked_path(identity["ancestor"])
    info = ancestor.stat()
    if (str(path) != identity["path"] or info.st_dev != identity["device"]
            or info.st_ino != identity["inode"]):
        raise FirstInstallError("product_install_location_changed")
    return path


def disjoint_roots(source: Path | str, target: Path | str) -> tuple[Path, Path]:
    source, target = checked_path(source), checked_path(target)
    if source == target or source in target.parents or target in source.parents:
        raise FirstInstallError("product_install_roots_overlap")
    return source, target


def validate_selected_root(root):
    root = checked_path(root)
    for key in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)"):
        value = os.environ.get(key)
        if value:
            protected = Path(value).absolute()
            if root == protected or protected in root.parents:
                raise FirstInstallError("product_install_protected_directory")
    return root


def read_registration(root: Path | str) -> dict:
    root = checked_path(root)
    file = checked_path(root / REGISTRATION_FILENAME)
    try:
        if not file.is_file() or file.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("registration size")
        row = json.loads(file.read_text(encoding="utf-8"))
        if (not isinstance(row, dict) or row.get("contract") != FIRST_INSTALL_CONTRACT
                or row.get("scope") != "current_user" or row.get("app_directory") != "app"
                or row.get("data_directory") != "data"):
            raise ValueError("registration identity")
        if row.get("layout_version", 1) not in (1, LAYOUT_VERSION):
            raise ValueError("layout version")
        if row.get("layout_version") == LAYOUT_VERSION:
            identity = row.get("installation_id")
            if not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9]{32}", identity):
                raise ValueError("installation id")
    except (OSError, ValueError) as error:
        raise FirstInstallError("product_install_registration_invalid") from error
    return row


@dataclass(frozen=True)
class InstallationLayout:
    root: Path

    @classmethod
    def at(cls, root):
        return cls(checked_path(root))

    @property
    def app(self):
        return self.root / "app"

    @property
    def data(self):
        return self.root / "data"

    @property
    def cache(self):
        return self.root / "cache"

    @property
    def maintenance(self):
        return self.root / "maintenance"

    @property
    def workspace(self):
        return self.root / "update-workspace"

    def public(self):
        return {key: str(getattr(self, key)) for key in ("root", "app", "data", "cache", "maintenance", "workspace")}


def resolve_runtime_root(executable: Path | str, *, allow_maintenance=False) -> Path | None:
    """Derive from this executable, never a request's claimed installation."""
    executable = Path(executable).absolute()
    parent = executable.parent
    if parent.name not in {"app", "update-workspace", "maintenance"}:
        return None
    root = checked_path(parent.parent)
    if not (root / REGISTRATION_FILENAME).exists():
        if (root / "maintenance/proof/signed-release.json").exists():
            raise FirstInstallError("product_install_registration_missing")
        return None
    row = read_registration(root)
    state = row.get("installation_state", "installed")
    if state not in ACTIVE_STATES and not (allow_maintenance and parent.name == "maintenance"):
        raise FirstInstallError("product_installation_not_active")
    return root


def safe_files(root: Path | str) -> list[Path]:
    """Inventory ordinary files without following a junction anywhere below root."""
    root = checked_path(root)
    if not root.exists():
        return []
    files = []
    for directory, names, leaves in os.walk(root, followlinks=False):
        current = Path(directory)
        for name in names + leaves:
            path = current / name
            if is_reparse_point(path):
                raise FirstInstallError("product_install_link_unsupported")
        files.extend(current / name for name in leaves if (current / name).is_file())
    return sorted(files)
