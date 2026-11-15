"""Test-only synthetic Windows credential smoke; no launcher or runtime owner."""
from __future__ import annotations

import ctypes
import json
import os
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from genshin_app.agent_chat.credential_store import (
    CredentialStoreError,
    WindowsCredentialStore,
)

TEST_CREDENTIAL_PREFIX = "GenshinCalc/TestOnly/NativeAgent/"
CREDENTIAL_SMOKE_TARGET_PREFIX = TEST_CREDENTIAL_PREFIX + "CredentialSmoke/"
_CRED_TYPE_GENERIC = 1
_ERROR_NOT_FOUND = 1168
_DUMP_SUFFIXES = frozenset({".dmp", ".dump", ".mdmp", ".core"})


class CredentialSmokeError(RuntimeError):
    """Stable, secret-free failure returned by the development gate."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise CredentialSmokeError(code)


def _physical_delete_target(store: WindowsCredentialStore) -> None:
    """Remove the TestOnly record itself after validating product tombstones."""

    try:
        deleted = bool(store._delete(store.target, _CRED_TYPE_GENERIC, 0))
        error = ctypes.get_last_error()
    except Exception:  # noqa: BLE001 - sanitize the private Win32 deletion boundary
        _fail("development_acceptance_credential_physical_cleanup_failed")
    if not deleted and error != _ERROR_NOT_FOUND:
        _fail("development_acceptance_credential_physical_cleanup_failed")


def _contains_token(value: object, tokens: Sequence[str]) -> bool:
    try:
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(type(value))
    return any(token and token in text for token in tokens)


def _scan_file(path: Path, token_bytes: Sequence[bytes]) -> bool:
    try:
        with path.open("rb") as stream:
            tail = b""
            longest = max(map(len, token_bytes), default=1)
            while chunk := stream.read(1024 * 1024):
                haystack = tail + chunk
                if any(token in haystack for token in token_bytes):
                    return True
                tail = haystack[-longest:]
    except OSError:
        _fail("development_acceptance_leak_scan_failed")
    return False


def _dump_candidates(project_root: Path) -> list[Path]:
    candidates: list[Path] = []
    excluded = {".git", "node_modules", "data", "dist", "archives", "backups"}
    for root, directories, files in os.walk(project_root):
        directories[:] = [name for name in directories if name not in excluded]
        for filename in files:
            path = Path(root) / filename
            if path.suffix.lower() in _DUMP_SUFFIXES or filename.lower().startswith("core."):
                candidates.append(path)
    return candidates


def assert_tokens_absent(
    *,
    tokens: Sequence[str],
    argv: Sequence[str],
    environment: Mapping[str, str],
    captured_output: Sequence[str],
    profile: Path,
    project_root: Path,
) -> None:
    """Scan all required public and on-disk surfaces without printing a token."""

    if _contains_token(list(argv), tokens):
        _fail("development_acceptance_token_in_argv")
    if _contains_token(dict(environment), tokens):
        _fail("development_acceptance_token_in_environment")
    if _contains_token(list(captured_output), tokens):
        _fail("development_acceptance_token_in_output")
    # Native Windows components and crash artefacts commonly persist strings as
    # UTF-16.  Search both byte orders as well as the UTF-8 form; a BOM, when
    # present, does not prevent the encoded token itself from matching.
    encoded = list(dict.fromkeys(
        token.encode(encoding)
        for token in tokens
        if token
        for encoding in ("utf-8", "utf-16-le", "utf-16-be")
    ))
    files = [path for path in profile.rglob("*") if path.is_file()]
    files.extend(_dump_candidates(project_root))
    seen: set[Path] = set()
    for path in files:
        try:
            canonical = path.resolve(strict=True)
        except OSError:
            _fail("development_acceptance_leak_scan_failed")
        if canonical in seen:
            continue
        seen.add(canonical)
        if _scan_file(canonical, encoded):
            _fail("development_acceptance_token_on_disk")


def run_windows_credential_smoke(
    *,
    profile: Path,
    project_root: Path,
    argv: Sequence[str],
    environment: Mapping[str, str],
    captured_output: Sequence[str],
    store_factory: Callable[[str], WindowsCredentialStore] = WindowsCredentialStore,
    physical_delete: Callable[[WindowsCredentialStore], None] = _physical_delete_target,
) -> dict[str, Any]:
    """Exercise write/replace/read/tombstone and always physically delete."""

    target = f"{CREDENTIAL_SMOKE_TARGET_PREFIX}{uuid.uuid4()}"
    first = "TEST_ONLY_NOT_A_REAL_KEY_" + str(uuid.uuid4())
    second = "TEST_ONLY_NOT_A_REAL_KEY_REPLACEMENT_" + str(uuid.uuid4())
    store: WindowsCredentialStore | None = None
    value_one: str | None = None
    value_two: str | None = None
    operation_error_code: str | None = None
    try:
        if os.name != "nt" and store_factory is WindowsCredentialStore:
            _fail("development_acceptance_windows_required")
        try:
            store = store_factory(target)
            if not target.startswith(CREDENTIAL_SMOKE_TARGET_PREFIX):
                _fail("development_acceptance_credential_target_invalid")
            if store.read_versioned() != (0, None):
                _fail("development_acceptance_credential_target_not_empty")
            store.write(first)
            generation_one, value_one = store.read_versioned()
            if generation_one != 1 or value_one != first:
                _fail("development_acceptance_credential_write_failed")
            store.write(second)
            generation_two, value_two = store.read_versioned()
            if generation_two != 2 or value_two != second:
                _fail("development_acceptance_credential_replace_failed")
            if store.delete() is not True or store.read_versioned() != (3, None):
                _fail("development_acceptance_credential_tombstone_failed")
            assert_tokens_absent(
                tokens=(first, second),
                argv=argv,
                environment=environment,
                captured_output=captured_output,
                profile=profile,
                project_root=project_root,
            )
        except CredentialSmokeError as error:
            operation_error_code = error.code
        except CredentialStoreError:
            operation_error_code = "development_acceptance_credential_smoke_failed"
        except Exception:  # noqa: BLE001 - never export a secret-bearing backend error
            operation_error_code = "development_acceptance_credential_smoke_failed"
    except CredentialSmokeError as error:
        operation_error_code = error.code
    finally:
        if store is not None:
            try:
                physical_delete(store)
                if store.read_versioned() != (0, None):
                    _fail("development_acceptance_credential_physical_cleanup_failed")
            except CredentialSmokeError as cleanup_error:
                operation_error_code = cleanup_error.code
            except Exception:  # noqa: BLE001 - cleanup must replace any raw backend error
                operation_error_code = (
                    "development_acceptance_credential_physical_cleanup_failed"
                )
        first = second = ""
        value_one = value_two = None
        store = None
    if operation_error_code is not None:
        # Construct the public error only after every backend exception handler has
        # exited.  ``from None`` prevents a secret-bearing backend exception from
        # remaining reachable through ``__context__`` or ``__cause__``.
        raise CredentialSmokeError(operation_error_code) from None
    return {
        "status": "passed",
        "target_scope": "TestOnly/unique",
        "write": True,
        "replace": True,
        "read": True,
        "tombstone": True,
        "physical_cleanup": True,
        "token_scan": True,
    }
