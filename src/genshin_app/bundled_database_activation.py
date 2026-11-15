"""Explicitly activate the database carried by an installed App package.

This is an installation/repair boundary, not a general database writer.  The
caller cannot supply a candidate path: the only admissible candidate is the
database inside the running frozen package, bound to that package by the
reviewed database and ReleasePair manifests.

The user confirmation belongs to the caller (the installer or the explicit
repair command).  Once confirmed, the existing database release migrator owns
locking, staging, rollback and active-path verification.  This module only
adds package/pair verification and publishes the pair marker after both the
App artifact and installed database have passed their checks.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from genshin_app.database_bootstrap import (
    LIVE_MANIFEST_FILENAME,
    LIVE_PAIR_MANIFEST_FILENAME,
    prepare_database_startup,
)
from genshin_app.database_legacy_processes import packaged_peer_processes
from genshin_app.database_release_reuse import (
    rebind_database_sidecars,
    verified_reusable_database,
)
from genshin_app.database_transaction import (
    ActivationJournalError,
    activation_owner,
    journal_pending,
    profile_writer,
    read_activation_journal,
    write_activation_journal,
)
from genshin_app.package_integrity import (
    PackageIntegrityError,
    verify_package_artifact,
)
from genshin_app.paths import is_frozen, resource_dir
from genshin_app.version import (
    APP_RELEASE_ID,
    APP_VERSION,
    DATABASE_DATA_CONTRACT_VERSION,
    DATABASE_LOGIC_PROFILE,
    DATABASE_PAIR_POLICY,
    DATABASE_RELEASE_CHANNEL,
    DATABASE_RELEASE_ID,
    RELEASE_PAIR_ID,
)
from genshin_db.locking import LockBusyError
from genshin_db.release_migration import (
    abandon_unpublished_database_attempt,
    install_database_release,
)
from genshin_db.release_profile import (
    LIVE_RECEIPT_FILENAME,
    MANIFEST_FILENAME,
    PAIR_MANIFEST_FILENAME,
    DatabaseReleaseError,
    database_live_receipt_matches,
    database_release_semantically_equal,
    load_database_release_manifest,
    load_release_pair_manifest,
    read_database_identity,
    release_pair_matches_database_manifest,
    validate_database_live_receipt,
    validate_database_release_manifest,
    validate_release_pair_manifest,
)

ACTIVATION_CONTRACT = "genshin-bundled-database-activation-v1"
ACTIVATION_REPORT_FILENAME = "database-activation-last.json"
_CONSENT_SOURCES = frozenset({"installer", "user_repair"})


class BundledDatabaseActivationError(RuntimeError):
    """Stable refusal at the packaged database activation boundary."""

    def __init__(
        self,
        reason: str,
        message: str | None = None,
        details: Mapping[str, Any] | None = None,
    ):
        self.reason = reason
        self.details = copy.deepcopy(dict(details)) if details is not None else None
        super().__init__(message or reason)


def _fail(
    reason: str,
    message: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> None:
    raise BundledDatabaseActivationError(reason, message, details)


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        raw = (
            json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8")
        with temporary.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _validate_target_pair(manifest: Mapping[str, Any], pair: Mapping[str, Any]) -> tuple[dict, dict]:
    try:
        checked_manifest = validate_database_release_manifest(dict(manifest))
        checked_pair = validate_release_pair_manifest(dict(pair))
        pair_matches = release_pair_matches_database_manifest(
            checked_pair, checked_manifest
        )
    except DatabaseReleaseError as error:
        raise BundledDatabaseActivationError(error.reason) from error
    expected_policy = "exact" if DATABASE_RELEASE_CHANNEL == "stable" else "current_only"
    expected_app = {
        "app_release_id": APP_RELEASE_ID,
        "app_version": APP_VERSION,
        "required_database_release_id": DATABASE_RELEASE_ID,
        "logic_profile": DATABASE_LOGIC_PROFILE,
        "data_contract_version": DATABASE_DATA_CONTRACT_VERSION,
    }
    actual_app = {field: checked_pair["app"].get(field) for field in expected_app}
    if (
        not pair_matches
        or DATABASE_PAIR_POLICY != expected_policy
        or checked_manifest["channel"] != DATABASE_RELEASE_CHANNEL
        or checked_pair["channel"] != DATABASE_RELEASE_CHANNEL
        or checked_pair["pair_policy"] != DATABASE_PAIR_POLICY
        or checked_manifest["database_release_id"] != DATABASE_RELEASE_ID
        or checked_pair["release_pair_id"] != RELEASE_PAIR_ID
        or actual_app != expected_app
    ):
        _fail(
            "bundled_database_release_pair_mismatch",
            "安装包内数据库与当前应用版本不匹配",
            {
                "expected_release_pair_id": RELEASE_PAIR_ID,
                "actual_release_pair_id": checked_pair.get("release_pair_id"),
                "expected_database_release_id": DATABASE_RELEASE_ID,
                "actual_database_release_id": checked_manifest.get(
                    "database_release_id"
                ),
            },
        )
    return checked_manifest, checked_pair


def _load_verified_bundled_release() -> tuple[Path, dict, dict, str]:
    root = resource_dir()
    bundle = root / "bundled"
    package_database = bundle / "genshin.db"
    if not package_database.is_file():
        _fail(
            "bundled_database_missing",
            "安装包内缺少配套数据库，请重新安装当前版本",
        )
    try:
        manifest = load_database_release_manifest(bundle / MANIFEST_FILENAME)
        pair = load_release_pair_manifest(bundle / PAIR_MANIFEST_FILENAME)
    except DatabaseReleaseError as error:
        raise BundledDatabaseActivationError(error.reason) from error
    manifest, pair = _validate_target_pair(manifest, pair)
    package_root = Path(sys.executable).resolve().parent
    try:
        verified = verify_package_artifact(package_root)
    except PackageIntegrityError as error:
        raise BundledDatabaseActivationError(error.code) from error
    digest = verified["manifest_sha256"]
    if digest != pair["app"]["artifact_manifest"]["sha256"]:
        _fail("bundled_database_app_artifact_mismatch")
    return package_database, manifest, pair, digest


def _compare_sequence(
    *,
    current: Mapping[str, Any],
    target: Mapping[str, Any],
    sequence_field: str,
    downgrade_reason: str,
    split_reason: str,
) -> None:
    current_sequence = current[sequence_field]
    target_sequence = target[sequence_field]
    if current_sequence > target_sequence:
        _fail(
            downgrade_reason,
            "当前数据库版本比安装包更新，已拒绝降级覆盖",
            {"current_sequence": current_sequence, "target_sequence": target_sequence},
        )
    same_content = (
        database_release_semantically_equal(current, target)
        if sequence_field == "release_sequence"
        else dict(current) == dict(target)
    )
    if current_sequence == target_sequence and not same_content:
        _fail(
            split_reason,
            "发现相同版本序号对应不同发行内容，已拒绝覆盖",
            {"sequence": current_sequence},
        )


def _refuse_downgrade_or_split(
    data_dir: Path,
    *,
    target_manifest: Mapping[str, Any],
    target_pair: Mapping[str, Any],
) -> None:
    manifest_path = data_dir / LIVE_MANIFEST_FILENAME
    pair_path = data_dir / LIVE_PAIR_MANIFEST_FILENAME
    if pair_path.exists() and not manifest_path.exists():
        _fail(
            "bundled_database_live_identity_incomplete",
            "当前数据库版本凭据不完整，需要先恢复后再安装",
        )
    if manifest_path.exists():
        try:
            current_manifest = load_database_release_manifest(manifest_path)
        except DatabaseReleaseError as error:
            raise BundledDatabaseActivationError(error.reason) from error
        _compare_sequence(
            current=current_manifest,
            target=target_manifest,
            sequence_field="release_sequence",
            downgrade_reason="bundled_database_downgrade_forbidden",
            split_reason="bundled_database_release_sequence_conflict",
        )
    if pair_path.exists():
        try:
            current_pair = load_release_pair_manifest(pair_path)
        except DatabaseReleaseError as error:
            raise BundledDatabaseActivationError(error.reason) from error
        _compare_sequence(
            current=current_pair,
            target=target_pair,
            sequence_field="pair_sequence",
            downgrade_reason="bundled_release_pair_downgrade_forbidden",
            split_reason="bundled_release_pair_sequence_conflict",
        )


def _verify_installed_database(
    data_dir: Path,
    target_manifest: Mapping[str, Any],
) -> dict:
    try:
        installed_manifest = load_database_release_manifest(
            data_dir / LIVE_MANIFEST_FILENAME
        )
        receipt_value = json.loads(
            (data_dir / LIVE_RECEIPT_FILENAME).read_text(encoding="utf-8")
        )
        receipt = validate_database_live_receipt(receipt_value)
        identity = read_database_identity(data_dir / "genshin.db")
        matches = database_live_receipt_matches(
            receipt, identity, installed_manifest
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise BundledDatabaseActivationError(
            "bundled_database_installed_identity_unreadable"
        ) from error
    except DatabaseReleaseError as error:
        raise BundledDatabaseActivationError(error.reason) from error
    if (
        not database_release_semantically_equal(
            installed_manifest,
            target_manifest,
        )
        or not matches
    ):
        _fail("bundled_database_installed_identity_mismatch")
    return identity


def _activate_database_locked(
    data_dir: Path,
    *,
    consent_source: str,
    cancel_event=None,
    confirmation_digest: str | None = None,
    progress=None,
    fault_hook=None,
) -> dict[str, Any]:
    """Install the running frozen package's fixed database after consent."""

    if not is_frozen():
        _fail(
            "bundled_database_activation_requires_frozen_app",
            "该操作只允许由已安装的桌面应用执行",
        )
    if consent_source not in _CONSENT_SOURCES:
        _fail(
            "bundled_database_activation_consent_required",
            "安装或修复当前数据库需要明确用户确认",
        )
    directory = Path(data_dir).expanduser().resolve()
    package_database, manifest, pair, artifact_digest = (
        _load_verified_bundled_release()
    )
    directory.mkdir(parents=True, exist_ok=True)
    _require_no_peer()
    before = _activation_snapshot(directory)
    digest = _confirmation_digest(directory, before, manifest, pair, artifact_digest)
    if confirmation_digest is not None and confirmation_digest != digest:
        _fail("bundled_database_confirmation_changed", "数据库或安装包已变化，请重新检查并确认。")
    _check_cancel(cancel_event)
    pending = read_activation_journal(directory)
    target_digest = _target_digest(manifest, pair, artifact_digest)
    if journal_pending(pending) and pending.get("target_digest") != target_digest:
        _fail("database_activation_recovery_target_changed", "未完成操作属于另一个版本，请先使用对应版本恢复。")
    journal = pending if journal_pending(pending) else {
        "contract": "genshin-database-activation-journal-v1", "operation_id": uuid4().hex,
        "target_digest": target_digest, "before": before, "consent_source": consent_source,
    }
    # A crash after the DB commit must not repeat the migration.  Conversely,
    # interrupted same-DB metadata writes may be reconstructed from verified
    # immutable package bytes, never from arbitrary journal-supplied paths.
    force_migration_resume = False
    # Inspect current public identities before recovery can write anything.
    # Another accepted pair may reuse the exact DB bytes; an old matching SHA
    # is not permission to restore that pair's older manifest or receipt.
    _refuse_downgrade_or_split(
        directory, target_manifest=manifest, target_pair=pair,
    )
    if journal_pending(pending):
        _validated_recovery_controls(journal)
        abandon_unpublished_database_attempt(directory, manifest,
            expected_old_sha256=(journal.get("before") or {}).get("database_sha256"))
        force_migration_resume = _recover_activation_metadata(directory, journal, manifest)
    _refuse_downgrade_or_split(
        directory,
        target_manifest=manifest,
        target_pair=pair,
    )
    write_activation_journal(directory, journal, "prepared")
    if progress is not None:
        progress({"stage": "installing", "message": "正在安装程序随附的配套数据库，请保留此窗口。"})

    def migration_hook(phase):
        if phase in {"before_stage_copy", "after_candidate_validation", "after_rollback_snapshot", "before_replace"}:
            _check_cancel(cancel_event)
            _require_no_peer()
        if fault_hook is not None:
            fault_hook(phase)

    try:
        identity = None if force_migration_resume else verified_reusable_database(directory, manifest)
        if identity is None:
            write_activation_journal(directory, journal, "database_installing")
            result = install_database_release(
            directory,
            package_database,
            manifest,
            logic_profile=DATABASE_LOGIC_PROFILE,
            data_contract_version=DATABASE_DATA_CONTRACT_VERSION,
            user_confirmed=True,
            production=DATABASE_RELEASE_CHANNEL == "stable",
            app_release_id=APP_RELEASE_ID,
            release_pair_id=RELEASE_PAIR_ID,
            required_database_release_id=DATABASE_RELEASE_ID,
            pair_policy=DATABASE_PAIR_POLICY,
            app_version=APP_VERSION,
            app_artifact_manifest_sha256=artifact_digest,
                pair_manifest=pair,
                fault_hook=migration_hook,
            )
            identity = _verify_installed_database(directory, manifest)
        else:
            _check_cancel(cancel_event)
            rebind_database_sidecars(directory, manifest, identity)
            result = {"status": "database_reused", "rollback_retained": False}
        # From DB acceptance onwards cancellation is a request to finish the
        # postcheck, never permission to undo or falsely report no changes.
        write_activation_journal(directory, journal, "database_ready")
        _require_no_peer()
        if fault_hook is not None:
            fault_hook("before_pair_commit")
        _write_json_atomic(directory / LIVE_PAIR_MANIFEST_FILENAME, pair)
        write_activation_journal(directory, journal, "pair_committed")
        if fault_hook is not None:
            fault_hook("after_pair_commit")
        gate = prepare_database_startup(directory)
        if gate.get("can_start") is not True:
            _fail("bundled_database_postcheck_failed")
        write_activation_journal(directory, journal, "complete")
    except BaseException as error:
        try:
            current = _activation_snapshot(directory)
            unchanged = all(current.get(k) == journal["before"].get(k) for k in ("database_sha256", "controls"))
        except Exception:  # noqa: BLE001 - never replace the original failure during diagnostic inspection
            unchanged = False
        state = "cancelled" if isinstance(error, BundledDatabaseActivationCancelled) and unchanged else (
            "failed" if unchanged else "unknown")
        # Keep the original failure if writing the recovery note itself fails.
        try:
            write_activation_journal(directory, journal, state, reason=getattr(error, "reason", type(error).__name__))
        except OSError:
            pass
        raise
    return {
        "contract": ACTIVATION_CONTRACT,
        "status": "activated",
        "consent_source": consent_source,
        "app_release_id": APP_RELEASE_ID,
        "release_pair_id": RELEASE_PAIR_ID,
        "database_release_id": DATABASE_RELEASE_ID,
        "database_sha256": identity["sha256"],
        "migration_status": result.get("status"),
        "rollback_retained": bool(result.get("rollback_retained", False)),
        "startup_state": gate.get("state"),
        "cancel_requested_after_commit": bool(cancel_event is not None and cancel_event.is_set()),
        "operation_id": journal["operation_id"],
    }


class BundledDatabaseActivationCancelled(BundledDatabaseActivationError):
    def __init__(self):
        super().__init__("database_activation_cancelled", "已取消；尚未提交数据库变更。")


def _check_cancel(event):
    if event is not None and event.is_set():
        raise BundledDatabaseActivationCancelled()


def _require_no_peer():
    try:
        peers = packaged_peer_processes()
    except OSError as error:
        raise BundledDatabaseActivationError("database_legacy_instance_check_failed", "无法检查其他应用实例，请关闭其他版本后重试。") from error
    if peers:
        _fail("database_legacy_instance_running", "请先自行关闭其他原神拉表窗口或CLI进程，再重新检查。", {"processes": peers})


def _sha_file(path):
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _activation_snapshot(directory):
    controls = {}
    for name in (LIVE_MANIFEST_FILENAME, LIVE_RECEIPT_FILENAME, LIVE_PAIR_MANIFEST_FILENAME):
        path = directory / name
        if path.exists() and path.stat().st_size > 4 * 1024 * 1024:
            _fail("database_activation_control_too_large")
        controls[name] = path.read_text(encoding="utf-8") if path.exists() else None
    return {"database_sha256": _sha_file(directory / "genshin.db"), "controls": controls}


def _target_digest(manifest, pair, artifact):
    return hashlib.sha256(json.dumps([manifest, pair, artifact], sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _confirmation_digest(directory, snapshot, manifest, pair, artifact):
    journal = read_activation_journal(directory)
    return hashlib.sha256(json.dumps([str(directory.resolve()), snapshot,
        _target_digest(manifest, pair, artifact), journal], sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _validated_recovery_controls(journal):
    """Validate the entire original control snapshot before any recovery write.

    This may be an incomplete pre-release profile, so restoring the original
    bytes is not an assertion that its old release was ready. Target admission
    still uses the complete manifest/receipt/pair validators after installation.
    """
    previous = journal.get("before")
    controls = previous.get("controls") if isinstance(previous, dict) else None
    names = {LIVE_MANIFEST_FILENAME, LIVE_RECEIPT_FILENAME, LIVE_PAIR_MANIFEST_FILENAME}
    if not isinstance(controls, dict) or set(controls) != names:
        raise ActivationJournalError()
    for raw in controls.values():
        if raw is None:
            continue
        if type(raw) is not str:
            raise ActivationJournalError()
        try:
            if len(raw.encode("utf-8")) > 4 * 1024 * 1024:
                raise ValueError("control size")
            value = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
            if type(value) is not dict:
                raise ValueError("control shape")
        except (ValueError, UnicodeError, RecursionError) as error:
            raise ActivationJournalError() from error
    return controls


def _recover_activation_metadata(directory, journal, manifest):
    controls = _validated_recovery_controls(journal)
    current_sha = _sha_file(directory / "genshin.db")
    target_sha = (manifest.get("database") or {}).get("sha256")
    if target_sha and current_sha == target_sha:
        migration = directory / "database-release-migration-journal.json"
        if migration.exists():
            state = json.loads(migration.read_text(encoding="utf-8")).get("state")
            if state in {"swap_started", "postchecked", "committed", "operational"}:
                return True  # Finish the original database subtransaction.
        identity = read_database_identity(directory / "genshin.db")
        rebind_database_sidecars(directory, manifest, identity)
        return False
    previous = journal.get("before")
    if not isinstance(previous, dict) or current_sha != previous.get("database_sha256"):
        _fail("database_activation_recovery_required", "数据库状态尚不确定，请保留日志并完成事务恢复。")
    for name in (LIVE_MANIFEST_FILENAME, LIVE_RECEIPT_FILENAME, LIVE_PAIR_MANIFEST_FILENAME):
        raw = controls.get(name)
        path = directory / name
        if raw is None:
            path.unlink(missing_ok=True)
        elif type(raw) is str and len(raw.encode()) <= 4 * 1024 * 1024:
            # The complete bounded snapshot was validated before this loop.
            temporary = path.with_name(f".{name}.{uuid4().hex}.restore")
            temporary.write_text(raw, encoding="utf-8")
            os.replace(temporary, path)
        else:
            _fail("database_activation_journal_invalid")
    return False


def inspect_bundled_activation(data_dir: Path) -> dict:
    """Read-only confirmation material; never constructs engine or Agent."""
    if not is_frozen():
        _fail("bundled_database_activation_requires_frozen_app")
    directory = Path(data_dir).expanduser().resolve()
    _require_no_peer()
    _package, manifest, pair, artifact = _load_verified_bundled_release()
    before = _activation_snapshot(directory)
    _refuse_downgrade_or_split(directory, target_manifest=manifest, target_pair=pair)
    current = json.loads(before["controls"][LIVE_MANIFEST_FILENAME] or "{}")
    return {"contract": "genshin-bundled-database-confirmation-v1", "data_dir": str(directory),
            "current_database_release_id": current.get("database_release_id"),
            "target_database_release_id": manifest["database_release_id"],
            "target_pair_id": pair["release_pair_id"],
            "confirmation_digest": _confirmation_digest(directory, before, manifest, pair, artifact),
            "message": "仅安装当前程序随附的完整数据库；旧结果可能需要Agent适配并重新计算。"}


def activate_bundled_database(data_dir: Path, *, consent_source: str,
                              confirmation_digest: str | None = None,
                              cancel_event=None, progress=None, fault_hook=None) -> dict[str, Any]:
    if not is_frozen():
        _fail("bundled_database_activation_requires_frozen_app", "该操作只允许由桌面应用执行。")
    if consent_source not in _CONSENT_SOURCES:
        _fail("bundled_database_activation_consent_required", "安装配套数据库需要明确确认。")
    _check_cancel(cancel_event)
    try:
        with profile_writer(data_dir), activation_owner(data_dir):
            return _activate_database_locked(data_dir, consent_source=consent_source,
                confirmation_digest=confirmation_digest, cancel_event=cancel_event,
                progress=progress, fault_hook=fault_hook)
    except LockBusyError as error:
        raise BundledDatabaseActivationError("database_migration_busy", "数据库正在更新，请稍后重新检查。") from error


def write_activation_report(
    data_dir: Path,
    *,
    status: str,
    consent_source: str,
    result: Mapping[str, Any] | None = None,
    error: BaseException | None = None,
) -> Path:
    """Persist a bounded installer/repair receipt without exposing secrets."""

    payload: dict[str, Any] = {
        "contract": ACTIVATION_CONTRACT,
        "status": status,
        "recorded_at": _now(),
        "consent_source": consent_source,
        "app_release_id": APP_RELEASE_ID,
        "release_pair_id": RELEASE_PAIR_ID,
        "database_release_id": DATABASE_RELEASE_ID,
    }
    if result is not None:
        payload["result"] = {
            key: copy.deepcopy(result.get(key))
            for key in (
                "status",
                "migration_status",
                "rollback_retained",
                "startup_state",
                "database_sha256",
            )
        }
    if error is not None:
        payload["error"] = {
            "reason": getattr(error, "reason", type(error).__name__),
            "message": str(error) or "数据库激活失败",
        }
    path = Path(data_dir).expanduser().resolve() / "logs" / ACTIVATION_REPORT_FILENAME
    _write_json_atomic(path, payload)
    return path


__all__ = [
    "ACTIVATION_CONTRACT",
    "ACTIVATION_REPORT_FILENAME",
    "BundledDatabaseActivationError",
    "activate_bundled_database",
    "write_activation_report",
]
