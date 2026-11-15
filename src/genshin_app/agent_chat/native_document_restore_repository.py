"""Document-owned storage routing for native Agent restore points.

Restore points belong to an App document, not to the conversation that happened
to create them.  Keeping their artifacts below a conversation directory makes a
normal conversation delete an accidental destructive checkpoint delete.  This
repository gives every document a stable, content-derived directory below the
sessions root and performs a conservative one-time copy migration from the
unreleased conversation-local layout.

The migration deliberately leaves the legacy copy in place.  The global copy is
validated before it becomes authoritative and no user artifact is removed as a
side effect of opening a conversation.  Release cleanup may remove proven
unreferenced legacy copies in a separately reviewed maintenance step.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import uuid
from pathlib import Path

from genshin_app.equipment.files import publish_files
from genshin_app.equipment.schema import PlanError
from genshin_db.locking import file_lock

from .native_document_restore_points import (
    MAX_APPLICATION_PAYLOAD_BYTES,
    MAX_DOCUMENT_BYTES,
    MAX_STORE_BYTES,
    STORE_FILE,
    NativeDocumentRestorePointStore,
)
from .sessions import safe_path

CATALOG_DIRECTORY = ".document-restore-points-v1"
CATALOG_LOCK_FILE = ".document-restore-points-v1.lock"
_DOCUMENT_DIRECTORY = re.compile(r"document-[0-9a-f]{64}\Z")
_ARTIFACT_FILE = re.compile(
    r"(?:document-body|application-payload)-[0-9a-f]{64}\.json\Z"
)


def _fail(code: str) -> None:
    raise PlanError("文档恢复点仓库不可用", code)


def _document_identity(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 128
        or any(ord(character) < 0x20 for character in value)
    ):
        _fail("document_restore_repository_invalid")
    return value


def _regular_owned_file(path: Path, maximum: int) -> bytes:
    try:
        info = path.lstat()
        attributes = getattr(info, "st_file_attributes", 0)
        if (
            path.is_symlink()
            or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
            or not stat.S_ISREG(info.st_mode)
            or info.st_nlink > 1
            or info.st_size > maximum
        ):
            _fail("document_restore_repository_invalid")
        with path.open("rb") as stream:
            raw = stream.read(maximum + 1)
    except PlanError:
        raise
    except OSError:
        _fail("document_restore_repository_invalid")
    if len(raw) > maximum:
        _fail("agent_size_limit")
    return raw


class NativeDocumentRestoreRepository:
    """Resolve and conservatively migrate document-owned restore stores."""

    def __init__(self, sessions_root: str | Path) -> None:
        self.sessions_root = Path(sessions_root).absolute()
        self.catalog_root = safe_path(self.sessions_root, CATALOG_DIRECTORY)

    @staticmethod
    def directory_name(document_id: str) -> str:
        checked = _document_identity(document_id)
        return "document-" + hashlib.sha256(checked.encode("utf-8")).hexdigest()

    def document_root(self, document_id: str) -> Path:
        name = self.directory_name(document_id)
        if _DOCUMENT_DIRECTORY.fullmatch(name) is None:  # pragma: no cover
            _fail("document_restore_repository_invalid")
        return safe_path(self.catalog_root, name)

    def _ensure_catalog(self) -> None:
        try:
            safe_path(self.sessions_root, CATALOG_DIRECTORY)
            if self.catalog_root.exists() and (
                self.catalog_root.is_symlink() or not self.catalog_root.is_dir()
            ):
                _fail("document_restore_repository_invalid")
            self.catalog_root.mkdir(parents=True, exist_ok=True)
            safe_path(self.catalog_root, CATALOG_LOCK_FILE)
        except PlanError:
            raise
        except OSError:
            _fail("document_restore_repository_invalid")

    @staticmethod
    def _migration_files(legacy_root: Path) -> list[tuple[str, bytes]]:
        store_path = safe_path(legacy_root, STORE_FILE)
        rows = [(STORE_FILE, _regular_owned_file(store_path, MAX_STORE_BYTES))]
        try:
            entries = list(legacy_root.iterdir())
        except OSError:
            _fail("document_restore_repository_invalid")
        for entry in entries:
            if _ARTIFACT_FILE.fullmatch(entry.name) is None:
                continue
            maximum = (
                MAX_DOCUMENT_BYTES + 4096
                if entry.name.startswith("document-body-")
                else MAX_APPLICATION_PAYLOAD_BYTES + 4096
            )
            rows.append((entry.name, _regular_owned_file(entry, maximum)))
        return rows

    def resolve(
        self,
        document_id: str,
        *,
        legacy_root: str | Path | None = None,
    ) -> Path:
        """Return the authoritative global root, migrating once if necessary.

        A pre-existing global root always wins.  This prevents a stale legacy
        copy from overwriting a store that has advanced since migration.
        """

        checked = _document_identity(document_id)
        self._ensure_catalog()
        target = self.document_root(checked)
        legacy = None if legacy_root is None else Path(legacy_root).absolute()
        lock_path = safe_path(self.catalog_root, CATALOG_LOCK_FILE)
        with file_lock(lock_path):
            if target.exists():
                if target.is_symlink() or not target.is_dir():
                    _fail("document_restore_repository_invalid")
                # Reading validates identity, metadata and all referenced
                # artifacts before callers use the repository.
                NativeDocumentRestorePointStore(
                    target, document_id=checked
                ).validate_integrity()
                return target
            if legacy is None or not safe_path(legacy, STORE_FILE).is_file():
                return target

            # Validate the source before copying any bytes.
            NativeDocumentRestorePointStore(
                legacy, document_id=checked
            ).validate_integrity()
            rows = self._migration_files(legacy)
            temporary = safe_path(
                self.catalog_root,
                f".migrating-{hashlib.sha256(checked.encode()).hexdigest()}-{uuid.uuid4().hex}",
            )
            try:
                temporary.mkdir(parents=False, exist_ok=False)
                publish_files(
                    [(safe_path(temporary, name), raw) for name, raw in rows],
                    overwrite=False,
                )
                NativeDocumentRestorePointStore(
                    temporary, document_id=checked
                ).validate_integrity()
                os.replace(temporary, target)
                NativeDocumentRestorePointStore(
                    target, document_id=checked
                ).validate_integrity()
            except Exception:
                # Only the newly-created, exact temporary directory is ever
                # cleaned.  The legacy source and any authoritative target are
                # intentionally left untouched.
                if temporary.exists() and temporary.is_dir() and not temporary.is_symlink():
                    for child in temporary.iterdir():
                        if child.is_file() and not child.is_symlink():
                            child.unlink(missing_ok=True)
                    temporary.rmdir()
                raise
            return target

    def store(
        self,
        document_id: str,
        *,
        legacy_root: str | Path | None = None,
    ) -> NativeDocumentRestorePointStore:
        return NativeDocumentRestorePointStore(
            self.resolve(document_id, legacy_root=legacy_root),
            document_id=document_id,
        )

    def source_state(self, conversation_id: str | None) -> str:
        """Return a display-only source state without reviving a conversation."""

        if conversation_id is None:
            return "unattributed"
        try:
            source = safe_path(self.sessions_root, conversation_id)
        except PlanError:
            return "tombstone"
        return "available" if source.is_dir() and not source.is_symlink() else "tombstone"


__all__ = [
    "CATALOG_DIRECTORY",
    "NativeDocumentRestoreRepository",
]
