from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from genshin_app import release_pair_coordinator as coordinator
from genshin_app import release_pair_transport as transport
from genshin_app.release_pair_storage import ReleasePairStorageError
from genshin_app.windows_known_folders import WindowsPathAuthorityError


def _updater(tmp_path: Path):
    root = tmp_path / "ProgramData" / "GenshinCalc" / "Updater"
    root.mkdir(parents=True)
    executable = root / "GenshinCalcUpdater.exe"
    executable.write_bytes(b"reviewed external updater")
    receipt = {
        "contract": coordinator.UPDATER_ARTIFACT_CONTRACT,
        "schema_version": coordinator.UPDATER_ARTIFACT_SCHEMA_VERSION,
        # Provenance may name an older pair; protocol compatibility is the
        # deliberate cross-version contract.
        "build_provenance": {
            "app": {
                "version": "0.44.0",
                "release_id": "app-0.44.0",
                "release_pair_id": "pair-old",
            }
        },
        "updater": {
            "filename": executable.name,
            "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
            "size_bytes": executable.stat().st_size,
            "helper_contract": "genshin-release-pair-helper-result-v1",
            "request_schema_version": 1,
        },
    }
    (root / "updater-artifact.json").write_text(
        json.dumps(receipt), encoding="utf-8"
    )
    return executable, receipt


def _latest():
    return {
        "authoritative": True,
        "document_sha256": "d" * 64,
        "confirmation_digest": "d" * 64,
        "release_pair_id": "pair-next",
        "database_release_id": "db-next",
        "pair_manifest": {"release_pair_id": "pair-next"},
        "database_manifest": {"database_release_id": "db-next"},
        "artifacts": {
            "app": {
                "size_bytes": 10,
                "unpacked_size_bytes": 20,
                "unpacked_files": 2,
            },
            "database": {"size_bytes": 30},
        },
        "trust": {
            "source": "signed_discovery",
            "algorithm": "rsa-pss-sha256",
            "key_id": "release-key",
        },
    }


def test_installed_updater_accepts_older_build_provenance_by_protocol(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        coordinator,
        "reject_effective_write_access",
        lambda *_args, **_kwargs: None,
    )
    executable, receipt = _updater(tmp_path)
    result = coordinator.locate_installed_updater(tmp_path / "ProgramData")
    assert result == {"executable": executable.resolve(), "receipt": receipt}

    executable.write_bytes(b"tampered")
    with pytest.raises(coordinator.ReleasePairCoordinatorError) as failure:
        coordinator.locate_installed_updater(tmp_path / "ProgramData")
    assert failure.value.reason == "release_pair_updater_artifact_mismatch"


def test_default_updater_location_ignores_user_environment(tmp_path, monkeypatch):
    executable, receipt = _updater(tmp_path / "trusted")
    attacker = tmp_path / "attacker-program-data"
    attacker.mkdir()
    monkeypatch.setenv("PROGRAMDATA", str(attacker))
    monkeypatch.setattr(
        coordinator,
        "known_folder_path",
        lambda _folder_id: tmp_path / "trusted" / "ProgramData",
    )
    monkeypatch.setattr(
        coordinator,
        "reject_effective_write_access",
        lambda *_args, **_kwargs: None,
    )

    result = coordinator.locate_installed_updater()
    assert result == {"executable": executable.resolve(), "receipt": receipt}
    assert attacker not in result["executable"].parents


def test_updater_location_fails_closed_when_effectively_user_writable(
    tmp_path, monkeypatch
):
    _updater(tmp_path)

    def writable(_path, *, reason):
        raise WindowsPathAuthorityError(reason)

    monkeypatch.setattr(coordinator, "reject_effective_write_access", writable)
    with pytest.raises(coordinator.ReleasePairCoordinatorError) as failure:
        coordinator.locate_installed_updater(tmp_path / "ProgramData")
    assert failure.value.reason == "release_pair_updater_permissions_invalid"


def test_exact_confirmation_stages_prepares_and_launches_external_helper(
    tmp_path, monkeypatch
):
    latest = _latest()
    executable = tmp_path / "updater.exe"
    executable.write_bytes(b"updater")
    observed = {}
    instance = coordinator.ReleasePairCoordinator(
        data_dir=tmp_path / "profile" / "data",
        active_app_root=tmp_path / "active-app",
        workspace=tmp_path / "profile" / "update-workspace",
        discovery_url="https://updates.example/latest.json",
        channel="dev",
        trust_verifier=lambda *_args: True,
        updater_locator=lambda: {"executable": executable, "receipt": {}},
        launcher=lambda exe, request: observed.update(
            executable=exe,
            request=json.loads(request.read_text(encoding="utf-8")),
        )
        or {"accepted": True, "method": "fixture"},
        restart_relative="GenshinCalcGUI.exe",
        parent_pid=4321,
    )
    orphan = instance.workspace / "staging"
    orphan.mkdir(parents=True)
    orphan_receipt = {"contract": transport.TRANSPORT_RECEIPT_CONTRACT,
                      "schema_version": transport.TRANSPORT_SCHEMA_VERSION,
                      "document_sha256": "a" * 64}
    orphan_receipt["receipt_sha256"] = hashlib.sha256(transport._canonical(orphan_receipt)).hexdigest()
    (orphan / transport.TRANSPORT_RECEIPT_FILENAME).write_text(json.dumps(orphan_receipt), encoding="utf-8")
    (orphan / "stale-old-app.zip").write_bytes(b"orphaned")
    monkeypatch.setattr(instance, "discover", lambda: latest)
    monkeypatch.setattr(
        coordinator,
        "stage_release_pair_artifacts",
        lambda _latest, workspace, **_kwargs: (
            (Path(workspace) / "staging" / "app").mkdir(parents=True),
            (Path(workspace) / "staging" / "genshin.db").write_bytes(b"db"),
        ),
    )
    monkeypatch.setattr(
        coordinator.release_pair_update,
        "read_pair_update_journal",
        lambda _workspace: None,
    )
    monkeypatch.setattr(
        coordinator.release_pair_update,
        "create_signed_discovery_trust_assertion",
        lambda *_args, **_kwargs: {"trusted": True},
    )
    monkeypatch.setattr(
        coordinator,
        "_current_identity",
        lambda *_args: {
            "current": True,
            "database_manifest": {"database": {"size_bytes": 4}},
        },
    )
    def preflight(**_kwargs):
        assert not orphan.exists()
        return {"sufficient": True}

    monkeypatch.setattr(coordinator, "preflight_before_download", preflight)
    monkeypatch.setattr(
        coordinator.release_pair_update,
        "prepare_release_pair_handoff",
        lambda *_args, **_kwargs: {"status": "handoff_ready", "operation_id": "op-1"},
    )

    result = instance.prepare_and_launch(
        latest,
        confirmed_document_sha256="d" * 64,
        user_confirmed=True,
    )
    assert result["action"] == "handoff_launched"
    assert result["release_pair_id"] == "pair-next"
    assert observed["executable"] == executable
    assert observed["request"]["parent_pid"] == 4321
    assert observed["request"]["restart_relative"] == "GenshinCalcGUI.exe"


def test_semantically_equal_installed_database_selects_app_only_path(
    tmp_path, monkeypatch
):
    latest = _latest()
    executable = tmp_path / "updater.exe"
    executable.write_bytes(b"updater")
    observed = {}
    instance = coordinator.ReleasePairCoordinator(
        data_dir=tmp_path / "profile" / "data",
        active_app_root=tmp_path / "active-app",
        workspace=tmp_path / "profile" / "update-workspace",
        discovery_url="https://updates.example/latest.json",
        channel="dev",
        trust_verifier=lambda *_args: True,
        updater_locator=lambda: {"executable": executable, "receipt": {}},
        launcher=lambda *_args: {"accepted": True},
        restart_relative="GenshinCalcGUI.exe",
    )
    monkeypatch.setattr(instance, "discover", lambda: latest)
    monkeypatch.setattr(
        coordinator.release_pair_update,
        "read_pair_update_journal",
        lambda _workspace: None,
    )
    monkeypatch.setattr(
        coordinator,
        "_current_identity",
        lambda *_args: {
            "database_manifest": {"database": {"size_bytes": 4}}
        },
    )
    monkeypatch.setattr(
        coordinator,
        "verified_reusable_database",
        lambda *_args: {"sha256": "a" * 64},
    )
    monkeypatch.setattr(
        coordinator,
        "preflight_before_download",
        lambda **kwargs: observed.update(storage=kwargs) or {"sufficient": True},
    )

    def stage(_latest, workspace, **kwargs):
        observed["transport"] = kwargs
        (Path(workspace) / "staging" / "app").mkdir(parents=True)

    monkeypatch.setattr(coordinator, "stage_release_pair_artifacts", stage)
    monkeypatch.setattr(
        coordinator.release_pair_update,
        "create_signed_discovery_trust_assertion",
        lambda *_args, **_kwargs: {"trusted": True},
    )

    def prepare(_workspace, _app, database, *_args, **kwargs):
        observed["staged_database"] = database
        observed["handoff"] = kwargs
        return {"status": "handoff_ready", "operation_id": "op"}

    monkeypatch.setattr(
        coordinator.release_pair_update, "prepare_release_pair_handoff", prepare
    )
    monkeypatch.setattr(
        instance,
        "_launch",
        lambda _operation_id: {
            "action": "handoff_launched",
            "requires_exit": True,
        },
    )

    instance.prepare_and_launch(
        latest,
        confirmed_document_sha256="d" * 64,
        user_confirmed=True,
    )
    assert observed["storage"]["reuse_installed_database"] is True
    assert observed["transport"]["reuse_installed_database"] is True
    assert observed["staged_database"] is None
    assert observed["handoff"]["reuse_installed_database"] is True


def test_changed_document_is_rejected_before_download(tmp_path, monkeypatch):
    latest = _latest()
    newer = {**latest, "document_sha256": "e" * 64}
    executable = tmp_path / "updater.exe"
    executable.write_bytes(b"updater")
    instance = coordinator.ReleasePairCoordinator(
        data_dir=tmp_path / "data",
        active_app_root=tmp_path / "app",
        workspace=tmp_path / "workspace",
        discovery_url="https://updates.example/latest.json",
        channel="dev",
        trust_verifier=lambda *_args: True,
        updater_locator=lambda: {"executable": executable},
        launcher=lambda *_args: {"accepted": True},
        restart_relative="GenshinCalcGUI.exe",
    )
    monkeypatch.setattr(instance, "discover", lambda: newer)
    monkeypatch.setattr(
        coordinator,
        "stage_release_pair_artifacts",
        lambda *_args, **_kwargs: pytest.fail("changed consent must fail before download"),
    )
    with pytest.raises(coordinator.ReleasePairCoordinatorError) as failure:
        instance.prepare_and_launch(
            latest,
            confirmed_document_sha256="d" * 64,
            user_confirmed=True,
        )
    assert failure.value.reason == "release_pair_update_confirmation_mismatch"


def test_insufficient_space_is_rejected_before_any_download(tmp_path, monkeypatch):
    latest = _latest()
    executable = tmp_path / "updater.exe"
    executable.write_bytes(b"updater")
    instance = coordinator.ReleasePairCoordinator(
        data_dir=tmp_path / "data",
        active_app_root=tmp_path / "app",
        workspace=tmp_path / "workspace",
        discovery_url="https://updates.example/latest.json",
        channel="dev",
        trust_verifier=lambda *_args: True,
        updater_locator=lambda: {"executable": executable},
        launcher=lambda *_args: {"accepted": True},
        restart_relative="GenshinCalcGUI.exe",
    )
    monkeypatch.setattr(instance, "discover", lambda: latest)
    monkeypatch.setattr(
        coordinator.release_pair_update,
        "read_pair_update_journal",
        lambda _workspace: None,
    )
    monkeypatch.setattr(
        coordinator,
        "_current_identity",
        lambda *_args: {"database_manifest": {"database": {"size_bytes": 4}}},
    )
    monkeypatch.setattr(
        coordinator,
        "preflight_before_download",
        lambda **_kwargs: (_ for _ in ()).throw(
            ReleasePairStorageError("release_pair_update_disk_space_insufficient")
        ),
    )
    monkeypatch.setattr(
        coordinator,
        "stage_release_pair_artifacts",
        lambda *_args, **_kwargs: pytest.fail("preflight must precede download"),
    )
    with pytest.raises(coordinator.ReleasePairCoordinatorError) as failure:
        instance.prepare_and_launch(
            latest,
            confirmed_document_sha256="d" * 64,
            user_confirmed=True,
        )
    assert failure.value.reason == "release_pair_update_disk_space_insufficient"
