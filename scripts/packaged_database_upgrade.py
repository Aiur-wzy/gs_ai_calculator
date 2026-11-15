"""Build-only proof of an explicit bundled DB upgrade; never an updater/feed."""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from genshin_app.frozen_selfcheck_isolation import clean_environment
from genshin_db.release_profile import (
    MANIFEST_FILENAME,
    DatabaseReleaseError,
    create_database_live_receipt,
    database_live_receipt_matches,
    database_matches_manifest_core,
    load_database_release_manifest,
    load_release_pair_manifest,
    read_database_identity,
    release_pair_matches_database_manifest,
)

# Test observation budget, not a product/Agent execution rule. The accepted
# project migration took 1372.879s; 2h keeps substantial deterministic-work room.
DEFAULT_OBSERVATION_TIMEOUT_SECONDS = 2 * 60 * 60


def _observation_timeout(value):
    if (type(value) not in (int, float) or value <= 0
            or isinstance(value, float) and not math.isfinite(value)):
        raise ValueError("packaged_database_upgrade_observation_timeout_invalid")
    return value


def _sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@dataclass(frozen=True)
class AcceptedPredecessorDatabase:
    database: Path
    manifest_path: Path
    pair_path: Path
    manifest: dict
    pair: dict
    artifact_shas: tuple[str, str, str]

    def assert_unchanged(self):
        if tuple(_sha(path) for path in (self.database, self.manifest_path, self.pair_path)) != self.artifact_shas:
            raise DatabaseReleaseError("accepted_predecessor_artifacts_changed")


def load_predecessor_database(*, pair_path, predecessor_pair, target_manifest,
                              workspace_root, build_root, dist_root,
                              expected_pair_sha256,
                              database_path=None, manifest_path=None):
    """Resolve only explicit paths or the accepted package's sibling artifacts."""
    pair_path = Path(pair_path).resolve()
    database = Path(database_path).resolve() if database_path else pair_path.with_name("genshin.db")
    manifest_path = Path(manifest_path).resolve() if manifest_path else pair_path.with_name(MANIFEST_FILENAME)
    workspace, build, dist = (Path(p).resolve() for p in (workspace_root, build_root, dist_root))
    for path in (pair_path, database, manifest_path):
        if (not path.is_relative_to(workspace) or path.is_relative_to(build)
                or path.is_relative_to(dist) or not path.is_file()):
            raise DatabaseReleaseError("accepted_predecessor_database_artifact_path_invalid")
    baseline = tuple(_sha(path) for path in (database, manifest_path, pair_path))
    if baseline[2] != expected_pair_sha256:
        raise DatabaseReleaseError("accepted_predecessor_pair_manifest_sha256_mismatch")
    if load_release_pair_manifest(pair_path) != predecessor_pair:
        raise DatabaseReleaseError("accepted_predecessor_pair_changed")
    manifest = load_database_release_manifest(manifest_path)
    if not release_pair_matches_database_manifest(predecessor_pair, manifest):
        raise DatabaseReleaseError("accepted_predecessor_database_manifest_mismatch")
    if (manifest["database_release_id"] == target_manifest["database_release_id"]
            or manifest["channel"] != target_manifest["channel"]
            or manifest["release_sequence"] >= target_manifest["release_sequence"]):
        raise DatabaseReleaseError("accepted_predecessor_database_sequence_not_advanced")
    identity = read_database_identity(database)
    expected = manifest["database"]
    if (any(identity.get(key) != expected.get(key) for key in ("sha256", "size_bytes", "meta_fingerprint"))
            or not database_matches_manifest_core(identity, expected)):
        raise DatabaseReleaseError("accepted_predecessor_database_artifact_mismatch")
    accepted = AcceptedPredecessorDatabase(database, manifest_path, pair_path, manifest, predecessor_pair, baseline)
    accepted.assert_unchanged()
    return accepted


def _invoke(cli, package, profile, args, env, *, timeout_seconds=DEFAULT_OBSERVATION_TIMEOUT_SECONDS):
    timeout_seconds = _observation_timeout(timeout_seconds)
    completed = subprocess.run([str(cli), "--data-dir", str(profile), *args], cwd=package,
        check=False, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout_seconds, env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    stream = completed.stdout if completed.returncode == 0 else completed.stderr
    try:
        result = json.loads(stream)
    except (ValueError, TypeError) as error:
        raise ValueError("packaged_database_upgrade_cli_receipt_invalid") from error
    if not isinstance(result, dict):
        raise ValueError("packaged_database_upgrade_cli_receipt_invalid")  # noqa: TRY004 - invalid CLI receipt value
    return completed.returncode, result


def verify_upgrade_profile(profile, *, predecessor, target_manifest, pair_manifest, original_sidecars):
    """Read actual post-upgrade and rollback evidence, never relabel old bytes."""
    profile = Path(profile)
    actual_pair = load_release_pair_manifest(profile / "release-pair-live-manifest.json")
    actual_manifest = load_database_release_manifest(profile / "database-live-manifest.json")
    identity = read_database_identity(profile / "genshin.db")
    receipt = json.loads((profile / "database-live-receipt.json").read_text(encoding="utf-8"))
    if (actual_pair != pair_manifest or actual_manifest != target_manifest
            or identity["sha256"] != target_manifest["database"]["sha256"]
            or not database_live_receipt_matches(receipt, identity, target_manifest)):
        raise ValueError("packaged_database_upgrade_active_identity_mismatch")
    journal = json.loads((profile / "database-release-migration-journal.json").read_text(encoding="utf-8"))
    if journal.get("state") not in ("postchecked", "committed", "operational", "cleanup_complete"):
        raise ValueError("packaged_database_upgrade_journal_not_terminal")
    for key in ("rollback_name", "rollback_receipt_name", "rollback_manifest_name"):
        name = journal.get(key)
        if not isinstance(name, str) or Path(name).name != name or not (profile / "backups" / name).is_file():
            raise ValueError("packaged_database_upgrade_rollback_missing")
    rollback = profile / "backups" / journal["rollback_name"]
    rollback_identity = read_database_identity(rollback)
    # SQLite backup() preserves logical content, not file-header counters.
    # The original accepted package artifacts remain independently byte-pinned.
    if (not database_matches_manifest_core(rollback_identity, predecessor.manifest["database"])
            or rollback_identity["meta_fingerprint"] != predecessor.manifest["database"]["meta_fingerprint"]):
        raise ValueError("packaged_database_upgrade_rollback_identity_mismatch")
    for key, live_name in (("rollback_receipt_name", "database-live-receipt.json"),
                           ("rollback_manifest_name", "database-live-manifest.json")):
        if (profile / "backups" / journal[key]).read_bytes() != original_sidecars[live_name]:
            raise ValueError("packaged_database_upgrade_rollback_sidecar_mismatch")
    old_receipt = json.loads(original_sidecars["database-live-receipt.json"])
    if not database_live_receipt_matches(old_receipt, rollback_identity, predecessor.manifest):
        raise ValueError("packaged_database_upgrade_rollback_receipt_mismatch")
    predecessor.assert_unchanged()
    return identity, rollback_identity


def run_packaged_database_upgrade_smoke(*, package_root, database_manifest, pair_manifest,
                                       predecessor, temporary_directory,
                                       timeout_seconds=DEFAULT_OBSERVATION_TIMEOUT_SECONDS):
    """Observe fixed hidden CLI steps; real timeout propagates, never retries."""
    timeout_seconds = _observation_timeout(timeout_seconds)
    if database_manifest["channel"] != "dev":
        raise ValueError("packaged_database_upgrade_retained_rollback_requires_dev")
    package = Path(package_root)
    cli, bundled = package / "GenshinCalc.exe", package / "_internal" / "bundled" / "genshin.db"
    if not cli.is_file() or not bundled.is_file():
        raise ValueError("packaged_database_upgrade_input_missing")
    predecessor.assert_unchanged()
    with temporary_directory() as root:
        profile = root / "profile"
        profile.mkdir()
        shutil.copyfile(predecessor.database, profile / "genshin.db")
        shutil.copyfile(predecessor.manifest_path, profile / "database-live-manifest.json")
        shutil.copyfile(predecessor.pair_path, profile / "release-pair-live-manifest.json")
        before = read_database_identity(profile / "genshin.db")
        receipt = create_database_live_receipt(predecessor.manifest, before,
            installed_at=predecessor.manifest["released_at"])
        (profile / "database-live-receipt.json").write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
        names = ("database-live-manifest.json", "database-live-receipt.json", "release-pair-live-manifest.json")
        sidecars = {name: (profile / name).read_bytes() for name in names}
        note = profile / "plans" / "user-content.json"
        note.parent.mkdir()
        note.write_bytes(b'{"preserve":"user-content"}')
        env = clean_environment(root)
        env["DSH_HOME"] = str(root / "environment" / "dsh")
        for name in ("TEMP", "TMP", "LOCALAPPDATA", "APPDATA", "USERPROFILE", "HOME", "DSH_HOME"):
            Path(env[name]).mkdir(parents=True, exist_ok=True)
        output = root / "before-upgrade.whiteboard.json"
        status, refused = _invoke(cli, package, profile, ["whiteboard", "new", "--output", str(output)], env,
                                  timeout_seconds=timeout_seconds)
        if (status == 0 or refused.get("reason") != "database_live_manifest_not_bundled_release" or output.exists()
                or read_database_identity(profile / "genshin.db") != before
                or any((profile / name).read_bytes() != data for name, data in sidecars.items())):
            raise ValueError("packaged_database_upgrade_preconsent_gate_failed")
        status, activated = _invoke(cli, package, profile, ["db", "activate-bundled", "--consent-source", "user_repair"], env,
                                    timeout_seconds=timeout_seconds)
        result = activated.get("data", {})
        if (status or result.get("status") != "activated" or result.get("migration_status") != "installed"
                or result.get("rollback_retained") is not True):
            raise ValueError("packaged_database_upgrade_activation_failed")
        identity, rollback = verify_upgrade_profile(profile, predecessor=predecessor,
            target_manifest=database_manifest, pair_manifest=pair_manifest, original_sidecars=sidecars)
        output = root / "after-upgrade.whiteboard.json"
        status, board = _invoke(cli, package, profile, ["whiteboard", "new", "--output", str(output)], env,
                                timeout_seconds=timeout_seconds)
        if status or not output.is_file() or board.get("code") != 0 or note.read_bytes() != b'{"preserve":"user-content"}':
            raise ValueError("packaged_database_upgrade_postcheck_failed")
        predecessor.assert_unchanged()
        return {"status": "passed", "mode": "accepted_predecessor_database_upgrade",
            "prior_pair_sequence": predecessor.pair["pair_sequence"],
            "current_pair_sequence": pair_manifest["pair_sequence"], "release_pair_id": pair_manifest["release_pair_id"],
            "prior_database_release_id": predecessor.manifest["database_release_id"],
            "database_release_id": database_manifest["database_release_id"], "database_sha256": identity["sha256"],
            "preconsent_startup": "refused_unchanged", "rollback": "logical_identity_and_sidecars_verified",
            "rollback_sha256": rollback["sha256"], "accepted_predecessor_bytes_unchanged": True,
            "observation_timeout_seconds": timeout_seconds}
