"""Frozen pre-pass installation coordinator; used only for state/fault oracles."""
# ruff: noqa: F821 - original globals are injected by make_reference_install_locked
from __future__ import annotations

from types import FunctionType


def _finish_accepted_journal_reference(
    *,
    data_dir: Path,
    live: Path,
    journal_path: Path,
    receipt_path: Path,
    manifest_path: Path,
    previous: Mapping[str, Any],
    checked_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Finish cleanup after a crash that occurred after the write fence reopened."""

    expected_manifest_fingerprint = _fingerprint(checked_manifest)
    if (
        previous["target_release_id"] != checked_manifest["database_release_id"]
        or previous.get("target_manifest_fingerprint")
        != expected_manifest_fingerprint
    ):
        _fail(
            "database_migration_recovery_required",
            "上次已发布数据库尚未完成清理，请先使用对应发行包恢复",
            {"state": previous["state"]},
        )
    with database_lock(live, timeout=0):
        active_identity = _validate_database(
            live,
            required_components=required_manifest_components(checked_manifest),
        )
        active_identity = _integrity_identity(
            live,
            active_identity,
            checked_manifest["database"],
            mismatch_reason="database_migration_active_release_core_mismatch",
        )
        receipt = _read_live_receipt(receipt_path)
        live_manifest = _read_live_manifest(manifest_path)
        try:
            receipt_matches = receipt is not None and database_live_receipt_matches(
                receipt, active_identity, checked_manifest
            )
        except DatabaseReleaseError:
            receipt_matches = False
        if (
            receipt is None
            or live_manifest is None
            or live_manifest != checked_manifest
            or _fingerprint(live_manifest)
            != previous["live_manifest_fingerprint"]
            or receipt["receipt_fingerprint"]
            != previous["live_receipt_fingerprint"]
            or not receipt_matches
        ):
            _fail(
                "database_migration_recovery_required",
                "已发布数据库与迁移日志不一致，需要人工恢复",
                {"state": previous["state"]},
            )
        _journal(
            journal_path,
            operation_id=previous["operation_id"],
            target_release_id=previous["target_release_id"],
            state="operational",
            production=previous["production"],
            old_sha256=previous.get("old_sha256"),
            target_package_sha256=previous["target_package_sha256"],
            active_sha256=active_identity["sha256"],
            staging_name=previous.get("staging_name"),
            rollback_name=previous.get("rollback_name"),
            rollback_receipt_name=previous.get("rollback_receipt_name"),
            rollback_manifest_name=previous.get("rollback_manifest_name"),
            old_receipt_present=previous.get("old_receipt_present", False),
            old_manifest_present=previous.get("old_manifest_present", False),
            target_manifest_fingerprint=expected_manifest_fingerprint,
            live_receipt_fingerprint=receipt["receipt_fingerprint"],
            live_manifest_fingerprint=_fingerprint(live_manifest),
        )
    staging = _journal_artifact(data_dir, previous, "staging_name")
    rollback = _journal_artifact(data_dir, previous, "rollback_name")
    rollback_receipt = _journal_artifact(
        data_dir, previous, "rollback_receipt_name"
    )
    rollback_manifest = _journal_artifact(
        data_dir, previous, "rollback_manifest_name"
    )
    if staging is not None:
        staging.unlink(missing_ok=True)
    if previous["production"] and rollback is not None:
        rollback.unlink(missing_ok=True)
    if previous["production"] and rollback_receipt is not None:
        rollback_receipt.unlink(missing_ok=True)
    if previous["production"] and rollback_manifest is not None:
        rollback_manifest.unlink(missing_ok=True)
    _journal(
        journal_path,
        operation_id=previous["operation_id"],
        target_release_id=previous["target_release_id"],
        state="cleanup_complete",
        production=previous["production"],
        old_sha256=previous.get("old_sha256"),
        target_package_sha256=previous["target_package_sha256"],
        active_sha256=active_identity["sha256"],
        rollback_name=(
            previous.get("rollback_name") if not previous["production"] else None
        ),
        rollback_receipt_name=(
            previous.get("rollback_receipt_name")
            if not previous["production"]
            else None
        ),
        rollback_manifest_name=(
            previous.get("rollback_manifest_name")
            if not previous["production"]
            else None
        ),
        old_receipt_present=previous.get("old_receipt_present", False),
        old_manifest_present=previous.get("old_manifest_present", False),
        target_manifest_fingerprint=expected_manifest_fingerprint,
        live_receipt_fingerprint=receipt["receipt_fingerprint"],
        live_manifest_fingerprint=_fingerprint(live_manifest),
    )
    return active_identity


def _install_locked_reference(
    *,
    data_dir: Path,
    package: Path,
    live: Path,
    build: Path,
    journal_path: Path,
    checked_manifest: Mapping[str, Any],
    package_identity: Mapping[str, Any],
    production: bool,
    fault_hook: Callable[[str], None] | None,
) -> dict[str, Any]:
    target_release_id = checked_manifest["database_release_id"]
    target_database = checked_manifest["database"]
    target_manifest_fingerprint = _fingerprint(checked_manifest)
    receipt_path = data_dir / LIVE_RECEIPT_FILENAME
    manifest_path = data_dir / LIVE_MANIFEST_FILENAME
    previous = _read_journal(journal_path)
    if previous is not None and previous["state"] in _ACCEPTED_JOURNAL_STATES:
        if previous["production"] is not production:
            _fail(
                "database_migration_recovery_required",
                "上次数据库迁移的运行模式不同，请按原模式完成清理",
                {"state": previous["state"]},
            )
        active_identity = _finish_accepted_journal(
            data_dir=data_dir,
            live=live,
            journal_path=journal_path,
            receipt_path=receipt_path,
            manifest_path=manifest_path,
            previous=previous,
            checked_manifest=checked_manifest,
        )
        return {
            "status": "already_current",
            "scope": "database_subtransaction",
            "pair_operational": False,
            "target_release_id": target_release_id,
            "active_sha256": active_identity["sha256"],
            "production": production,
            "recovered_cleanup": True,
        }
    if previous is not None and previous["state"] == "swap_started":
        # A hard process stop can happen after the reviewed candidate has been
        # atomically exchanged into the live path but before its manifest and
        # receipt are published.  Resume only when every durable journal pin
        # still identifies this exact package.  This is not a best-effort
        # repair: an unknown live file or a journal from another invocation
        # remains an explicit recovery-required refusal.
        resumable_swap = (
            previous["production"] is production
            and previous["target_release_id"] == target_release_id
            and previous["target_package_sha256"] == package_identity["sha256"]
            and previous.get("target_manifest_fingerprint")
            == target_manifest_fingerprint
            and live.is_file()
        )
        active_identity = None
        if resumable_swap:
            try:
                with database_lock(live, timeout=0):
                    candidate_identity = _validate_database(
                        live,
                        required_components=required_manifest_components(
                            checked_manifest
                        ),
                    )
                    active_identity = _integrity_identity(
                        live,
                        candidate_identity,
                        target_database,
                        mismatch_reason="database_migration_interrupted_swap_mismatch",
                    )
                    live_receipt = create_database_live_receipt(
                        checked_manifest,
                        active_identity,
                        installed_at=_now(),
                    )
                    _write_json_atomic(manifest_path, checked_manifest)
                    _write_json_atomic(receipt_path, live_receipt)
                    _journal(
                        journal_path,
                        operation_id=previous["operation_id"],
                        target_release_id=previous["target_release_id"],
                        state="postchecked",
                        production=production,
                        old_sha256=previous.get("old_sha256"),
                        target_package_sha256=previous[
                            "target_package_sha256"
                        ],
                        target_manifest_fingerprint=target_manifest_fingerprint,
                        active_sha256=active_identity["sha256"],
                        staging_name=previous.get("staging_name"),
                        rollback_name=previous.get("rollback_name"),
                        rollback_receipt_name=previous.get(
                            "rollback_receipt_name"
                        ),
                        rollback_manifest_name=previous.get(
                            "rollback_manifest_name"
                        ),
                        old_receipt_present=previous.get(
                            "old_receipt_present", False
                        ),
                        old_manifest_present=previous.get(
                            "old_manifest_present", False
                        ),
                        live_receipt_fingerprint=live_receipt[
                            "receipt_fingerprint"
                        ],
                        live_manifest_fingerprint=target_manifest_fingerprint,
                    )
            except (DatabaseMigrationError, DatabaseReleaseError, LockBusyError):
                active_identity = None
        if active_identity is not None:
            resumed = _read_journal(journal_path)
            if resumed is None:
                _fail("database_migration_recovery_required")
            active_identity = _finish_accepted_journal(
                data_dir=data_dir,
                live=live,
                journal_path=journal_path,
                receipt_path=receipt_path,
                manifest_path=manifest_path,
                previous=resumed,
                checked_manifest=checked_manifest,
            )
            return {
                "status": "already_current",
                "scope": "database_subtransaction",
                "pair_operational": False,
                "target_release_id": target_release_id,
                "active_sha256": active_identity["sha256"],
                "production": production,
                "recovered_cleanup": True,
                "recovered_interrupted_swap": True,
                "rollback_retained": bool(
                    previous.get("rollback_name") and not production
                ),
            }
    if previous is not None and previous["state"] not in _TERMINAL_JOURNAL_STATES:
        _fail(
            "database_migration_recovery_required",
            "上次数据库迁移尚未收敛",
            {"state": previous["state"]},
        )
    if (
        previous is not None
        and previous["state"] == "cleanup_complete"
        and previous["target_release_id"] == target_release_id
        and live.is_file()
    ):
        current_identity = _validate_database(
            live,
            required_components=required_manifest_components(checked_manifest),
        )
        current_identity = _integrity_identity(
            live,
            current_identity,
            target_database,
            mismatch_reason="database_migration_active_release_core_mismatch",
        )
        receipt = _read_live_receipt(receipt_path)
        live_manifest = _read_live_manifest(manifest_path)
        try:
            receipt_matches = receipt is not None and database_live_receipt_matches(
                receipt, current_identity, checked_manifest
            )
        except DatabaseReleaseError:
            receipt_matches = False
        if (
            receipt is not None
            and live_manifest == checked_manifest
            and receipt_matches
        ):
            return {
                "status": "already_current",
                "scope": "database_subtransaction",
                "pair_operational": False,
                "target_release_id": target_release_id,
                "active_sha256": current_identity["sha256"],
                "production": production,
                "recovered_cleanup": False,
            }
        _fail(
            "database_migration_recovery_required",
            "数据库与安装receipt不一致，需要人工恢复",
            {"state": previous["state"]},
        )

    try:
        previous_receipt_bytes = (
            receipt_path.read_bytes() if receipt_path.exists() else None
        )
        previous_manifest_bytes = (
            manifest_path.read_bytes() if manifest_path.exists() else None
        )
    except OSError as error:
        raise DatabaseMigrationError(
            "database_live_identity_unreadable",
            "无法读取当前数据库本机身份文件",
        ) from error
    if (previous_receipt_bytes is None) != (previous_manifest_bytes is None):
        _fail(
            "database_migration_recovery_required",
            "accepted manifest 与 receipt 不完整，需要人工恢复",
        )
    if not live.exists() and (
        previous_receipt_bytes is not None or previous_manifest_bytes is not None
    ):
        _fail(
            "database_migration_recovery_required",
            "在用数据库缺失但本机发行身份仍存在，需要人工恢复",
        )

    live_size = live.stat().st_size if live.exists() else 0
    required_free = (
        MINIMUM_HEADROOM_BYTES
        + package_identity["size_bytes"]
        + 2 * live_size
        + SQLITE_WORK_ALLOWANCE_BYTES
    )
    free = shutil.disk_usage(data_dir).free
    if free < required_free:
        _fail(
            "database_migration_space_insufficient",
            "数据库升级空间不足",
            {"required_free": required_free, "available_free": free},
        )

    operation_id = "dbm_" + uuid4().hex
    staging = build / f"database-release-{operation_id}.db"
    rollback_dir = data_dir / ("build" if production else "backups")
    rollback = rollback_dir / f"database-rollback-{operation_id}.db"
    rollback_receipt = rollback_dir / f"database-rollback-{operation_id}.receipt.json"
    rollback_manifest = rollback_dir / f"database-rollback-{operation_id}.manifest.json"
    old_receipt_present = previous_receipt_bytes is not None
    old_manifest_present = previous_manifest_bytes is not None
    old_sha = _sha256(live) if live.exists() else None
    live_receipt = None
    publication_attempted = False
    accepted = False
    rollback_recorded = False
    recovery_ambiguous = False
    _journal(
        journal_path,
        operation_id=operation_id,
        target_release_id=target_release_id,
        state="prepared",
        production=production,
        old_sha256=old_sha,
        target_package_sha256=package_identity["sha256"],
        target_manifest_fingerprint=target_manifest_fingerprint,
        staging_name=staging.name,
    )
    try:
        _fault(fault_hook, "before_stage_copy")
        shutil.copyfile(package, staging)
        _fsync_file(staging)
        staged_identity = _validate_database(
            staging,
            required_components=required_manifest_components(checked_manifest),
        )
        if not _manifest_database_matches(staged_identity, target_database):
            _fail("database_migration_staging_identity_mismatch")
        _journal(
            journal_path,
            operation_id=operation_id,
            target_release_id=target_release_id,
            state="validated",
            production=production,
            old_sha256=old_sha,
            target_package_sha256=package_identity["sha256"],
            target_manifest_fingerprint=target_manifest_fingerprint,
            staging_name=staging.name,
            old_receipt_present=old_receipt_present,
            old_manifest_present=old_manifest_present,
        )
        _fault(fault_hook, "after_candidate_validation")
        with database_lock(live, timeout=0):
            # The preflight hash is advisory.  This is the authoritative old
            # identity after all earlier writers have drained from the fence.
            old_sha = _sha256(live) if live.exists() else None
            if old_manifest_present:
                old_manifest = _read_live_manifest(manifest_path)
                old_receipt = _read_live_receipt(receipt_path)
                if old_manifest is None or old_receipt is None or not live.exists():
                    _fail(
                        "database_migration_recovery_required",
                        "当前数据库的 accepted 身份不完整，需要人工恢复",
                    )
                old_identity = _integrity_identity(
                    live,
                    _validate_database(
                        live,
                        required_components=required_manifest_components(old_manifest),
                    ),
                    old_manifest["database"],
                    mismatch_reason="database_migration_current_release_core_mismatch",
                )
                try:
                    old_identity_matches = database_live_receipt_matches(
                        old_receipt,
                        old_identity,
                        old_manifest,
                    )
                except DatabaseReleaseError:
                    old_identity_matches = False
                if not old_identity_matches:
                    _fail(
                        "database_migration_recovery_required",
                        "当前数据库、accepted manifest 与 receipt 不一致",
                    )
            rollback_created = False
            if live.exists():
                rollback_dir.mkdir(parents=True, exist_ok=True)
                _rollback_snapshot(live, rollback)
                rollback_created = True
            if previous_receipt_bytes is not None:
                _write_bytes_atomic(rollback_receipt, previous_receipt_bytes)
            if previous_manifest_bytes is not None:
                _write_bytes_atomic(rollback_manifest, previous_manifest_bytes)
            _fault(fault_hook, "after_rollback_snapshot")
            # The staged file is the complete reviewed release.  Never merge
            # project rows from the active database here: that would change a
            # package after its manifest was approved and would make the
            # installed content depend on timing.  Approved project-content
            # changes are applied while building the candidate and are covered
            # by its release manifest before this installer is called.
            final_candidate_identity = _validate_database(
                staging,
                required_components=required_manifest_components(checked_manifest),
            )
            final_candidate_identity = _integrity_identity(
                staging,
                final_candidate_identity,
                target_database,
                mismatch_reason="database_migration_release_core_changed",
            )
            _journal(
                journal_path,
                operation_id=operation_id,
                target_release_id=target_release_id,
                state="swap_started",
                production=production,
                old_sha256=old_sha,
                target_package_sha256=package_identity["sha256"],
                target_manifest_fingerprint=target_manifest_fingerprint,
                staging_name=staging.name,
                rollback_name=rollback.name if rollback_created else None,
                rollback_receipt_name=(
                    rollback_receipt.name if old_receipt_present else None
                ),
                rollback_manifest_name=(
                    rollback_manifest.name if old_manifest_present else None
                ),
                old_receipt_present=old_receipt_present,
                old_manifest_present=old_manifest_present,
            )
            _fault(fault_hook, "before_replace")
            try:
                publication_attempted = True
                os.replace(staging, live)
                _fsync_directory(data_dir)
                _fault(fault_hook, "after_replace")
                active_identity = _validate_database(
                    live,
                    required_components=required_manifest_components(checked_manifest),
                )
                active_identity = _integrity_identity(
                    live,
                    active_identity,
                    target_database,
                    mismatch_reason="database_migration_active_postcheck_failed",
                )
                _fault(fault_hook, "after_active_postcheck")
                try:
                    live_receipt = create_database_live_receipt(
                        checked_manifest,
                        active_identity,
                        installed_at=_now(),
                    )
                except DatabaseReleaseError as receipt_error:
                    raise DatabaseMigrationError(
                        receipt_error.reason, str(receipt_error)
                    ) from receipt_error
                _write_json_atomic(manifest_path, checked_manifest)
                _fault(fault_hook, "after_manifest_write")
                _write_json_atomic(receipt_path, live_receipt)
                _fault(fault_hook, "after_receipt_write")
                _journal(
                    journal_path,
                    operation_id=operation_id,
                    target_release_id=target_release_id,
                    state="postchecked",
                    production=production,
                    old_sha256=old_sha,
                    target_package_sha256=package_identity["sha256"],
                    target_manifest_fingerprint=target_manifest_fingerprint,
                    active_sha256=active_identity["sha256"],
                    staging_name=staging.name,
                    rollback_name=rollback.name if rollback_created else None,
                    rollback_receipt_name=(
                        rollback_receipt.name if old_receipt_present else None
                    ),
                    rollback_manifest_name=(
                        rollback_manifest.name if old_manifest_present else None
                    ),
                    old_receipt_present=old_receipt_present,
                    old_manifest_present=old_manifest_present,
                    live_receipt_fingerprint=live_receipt["receipt_fingerprint"],
                    live_manifest_fingerprint=target_manifest_fingerprint,
                )
                # From this point the active-path validation has durably passed.
                # Once the fence opens, no failure may replace this file again.
                accepted = True
            except BaseException as error:
                if publication_attempted:
                    try:
                        _restore_rollback(
                            live=live,
                            rollback=rollback,
                            keep_rollback=not production,
                        )
                        _restore_sidecar(receipt_path, previous_receipt_bytes)
                        _restore_sidecar(manifest_path, previous_manifest_bytes)
                        restored_sha = _sha256(live) if live.exists() else None
                        _journal(
                            journal_path,
                            operation_id=operation_id,
                            target_release_id=target_release_id,
                            state="rolled_back",
                            production=production,
                            old_sha256=old_sha,
                            target_package_sha256=package_identity["sha256"],
                            target_manifest_fingerprint=target_manifest_fingerprint,
                            active_sha256=restored_sha,
                            staging_name=staging.name,
                            rollback_name=(
                                rollback.name
                                if rollback.exists() and not production
                                else None
                            ),
                            rollback_receipt_name=(
                                rollback_receipt.name
                                if rollback_receipt.exists() and not production
                                else None
                            ),
                            rollback_manifest_name=(
                                rollback_manifest.name
                                if rollback_manifest.exists() and not production
                                else None
                            ),
                            old_receipt_present=old_receipt_present,
                            old_manifest_present=old_manifest_present,
                            error_reason=getattr(
                                error, "reason", type(error).__name__
                            ),
                        )
                        rollback_recorded = True
                    except BaseException as rollback_error:
                        recovery_ambiguous = True
                        raise DatabaseMigrationError(
                            "database_migration_recovery_required",
                            "数据库发布失败且自动回滚未完成",
                            {
                                "migration_error": getattr(
                                    error, "reason", type(error).__name__
                                ),
                                "rollback_error": type(rollback_error).__name__,
                            },
                        ) from rollback_error
                raise

        # The writer fence has reopened.  The accepted database remains
        # immutable; cleanup faults are resumable and never trigger rollback
        # because active-path validation and publication already completed.
        _fault(fault_hook, "after_commit")
        _journal(
            journal_path,
            operation_id=operation_id,
            target_release_id=target_release_id,
            state="operational",
            production=production,
            old_sha256=old_sha,
            target_package_sha256=package_identity["sha256"],
            target_manifest_fingerprint=target_manifest_fingerprint,
            active_sha256=active_identity["sha256"],
            staging_name=staging.name,
            rollback_name=rollback.name if rollback_created else None,
            rollback_receipt_name=(
                rollback_receipt.name if old_receipt_present else None
            ),
            rollback_manifest_name=(
                rollback_manifest.name if old_manifest_present else None
            ),
            old_receipt_present=old_receipt_present,
            old_manifest_present=old_manifest_present,
            live_receipt_fingerprint=live_receipt["receipt_fingerprint"],
            live_manifest_fingerprint=target_manifest_fingerprint,
        )
        _fault(fault_hook, "before_cleanup")
        if production:
            rollback.unlink(missing_ok=True)
            rollback_receipt.unlink(missing_ok=True)
            rollback_manifest.unlink(missing_ok=True)
        staging.unlink(missing_ok=True)
        _journal(
            journal_path,
            operation_id=operation_id,
            target_release_id=target_release_id,
            state="cleanup_complete",
            production=production,
            old_sha256=old_sha,
            target_package_sha256=package_identity["sha256"],
            target_manifest_fingerprint=target_manifest_fingerprint,
            active_sha256=active_identity["sha256"],
            rollback_name=(rollback.name if rollback_created and not production else None),
            rollback_receipt_name=(
                rollback_receipt.name
                if old_receipt_present and not production
                else None
            ),
            rollback_manifest_name=(
                rollback_manifest.name
                if old_manifest_present and not production
                else None
            ),
            old_receipt_present=old_receipt_present,
            old_manifest_present=old_manifest_present,
            live_receipt_fingerprint=live_receipt["receipt_fingerprint"],
            live_manifest_fingerprint=target_manifest_fingerprint,
        )
        return {
            "status": "installed",
            "scope": "database_subtransaction",
            "pair_operational": False,
            "target_release_id": target_release_id,
            "active_sha256": active_identity["sha256"],
            "old_sha256": old_sha,
            "manual_content_fingerprint": active_identity.get(
                "manual_content_fingerprint"
            ),
            "mutable_layer_fingerprint": active_identity[
                "mutable_layer_fingerprint"
            ],
            "live_manifest_fingerprint": target_manifest_fingerprint,
            "production": production,
            "rollback_retained": bool(rollback_created and not production),
            "live_receipt": copy.deepcopy(live_receipt),
        }
    except BaseException as error:
        if accepted or recovery_ambiguous:
            raise
        if not rollback_recorded:
            try:
                _journal(
                    journal_path,
                    operation_id=operation_id,
                    target_release_id=target_release_id,
                    state="rolled_back",
                    production=production,
                    old_sha256=old_sha,
                    target_package_sha256=package_identity["sha256"],
                    target_manifest_fingerprint=target_manifest_fingerprint,
                    active_sha256=old_sha,
                    staging_name=staging.name,
                    rollback_name=(
                        rollback.name if rollback.exists() and not production else None
                    ),
                    rollback_receipt_name=(
                        rollback_receipt.name
                        if rollback_receipt.exists() and not production
                        else None
                    ),
                    rollback_manifest_name=(
                        rollback_manifest.name
                        if rollback_manifest.exists() and not production
                        else None
                    ),
                    old_receipt_present=old_receipt_present,
                    old_manifest_present=old_manifest_present,
                    error_reason=getattr(error, "reason", type(error).__name__),
                )
            except Exception as journal_error:
                raise DatabaseMigrationError(
                    "database_migration_recovery_required",
                    "数据库保持旧版本，但迁移日志未能收敛",
                    {"migration_error": getattr(error, "reason", type(error).__name__)},
                ) from journal_error
        staging.unlink(missing_ok=True)
        if production:
            rollback.unlink(missing_ok=True)
            rollback_receipt.unlink(missing_ok=True)
            rollback_manifest.unlink(missing_ok=True)
        raise


def make_reference_install_locked(migration_module):
    scope = dict(vars(migration_module))
    finish = FunctionType(_finish_accepted_journal_reference.__code__, scope)
    scope["_finish_accepted_journal"] = finish
    return FunctionType(_install_locked_reference.__code__, scope)
