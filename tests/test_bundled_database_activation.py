from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from genshin_app import bundled_database_activation as activation
from genshin_app import cli


def _target() -> tuple[dict, dict]:
    manifest = {
        "database_release_id": "db-current",
        "release_pair_id": "pair-current",
        "release_sequence": 2,
    }
    pair = {
        "release_pair_id": "pair-current",
        "pair_sequence": 2,
    }
    return manifest, pair


def test_packaged_activation_migrates_only_database_and_commits_pair_last(
    tmp_path, monkeypatch
):
    # This synthetic migration tests commit order, not the user's live App
    # processes. Dedicated recovery tests cover peer-process refusal.
    monkeypatch.setattr(activation, "packaged_peer_processes", list)
    data = tmp_path / "data"
    data.mkdir()
    plan = data / "plans" / "keep.json"
    plan.parent.mkdir()
    plan.write_text('{"keep":true}', encoding="utf-8")
    package = tmp_path / "bundle" / "genshin.db"
    package.parent.mkdir()
    package.write_bytes(b"reviewed database")
    manifest, pair = _target()
    calls = []

    monkeypatch.setattr(activation, "is_frozen", lambda: True)
    monkeypatch.setattr(
        activation,
        "_load_verified_bundled_release",
        lambda: (package, manifest, pair, "a" * 64),
    )

    def install(directory, candidate, selected, **kwargs):
        calls.append((directory, candidate, selected, kwargs))
        assert not (data / activation.LIVE_PAIR_MANIFEST_FILENAME).exists()
        return {
            "status": "installed",
            "rollback_retained": True,
        }

    monkeypatch.setattr(activation, "install_database_release", install)
    monkeypatch.setattr(
        activation,
        "_verify_installed_database",
        lambda directory, selected: {"sha256": "b" * 64},
    )
    monkeypatch.setattr(
        activation,
        "prepare_database_startup",
        lambda directory: {"can_start": True, "state": "current_dev_ready"},
    )

    result = activation.activate_bundled_database(
        data,
        consent_source="installer",
    )

    assert result["status"] == "activated"
    assert result["rollback_retained"] is True
    assert result["startup_state"] == "current_dev_ready"
    assert json.loads(
        (data / activation.LIVE_PAIR_MANIFEST_FILENAME).read_text(encoding="utf-8")
    ) == pair
    assert plan.read_text(encoding="utf-8") == '{"keep":true}'
    assert calls[0][0] == data.resolve()
    assert calls[0][1] == package
    assert calls[0][3]["user_confirmed"] is True
    assert calls[0][3]["app_artifact_manifest_sha256"] == "a" * 64


def test_packaged_activation_never_commits_pair_when_database_install_fails(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(activation, "packaged_peer_processes", list)
    manifest, pair = _target()
    monkeypatch.setattr(activation, "is_frozen", lambda: True)
    monkeypatch.setattr(
        activation,
        "_load_verified_bundled_release",
        lambda: (tmp_path / "candidate.db", manifest, pair, "a" * 64),
    )
    monkeypatch.setattr(
        activation,
        "install_database_release",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("injected migration failure")
        ),
    )

    with pytest.raises(RuntimeError, match="injected migration failure"):
        activation.activate_bundled_database(
            tmp_path / "data",
            consent_source="user_repair",
        )
    assert not (
        tmp_path / "data" / activation.LIVE_PAIR_MANIFEST_FILENAME
    ).exists()


def test_packaged_activation_requires_frozen_app_and_explicit_consent(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(activation, "is_frozen", lambda: False)
    with pytest.raises(activation.BundledDatabaseActivationError) as not_frozen:
        activation.activate_bundled_database(
            tmp_path,
            consent_source="installer",
        )
    assert (
        not_frozen.value.reason
        == "bundled_database_activation_requires_frozen_app"
    )

    monkeypatch.setattr(activation, "is_frozen", lambda: True)
    with pytest.raises(activation.BundledDatabaseActivationError) as no_consent:
        activation.activate_bundled_database(tmp_path, consent_source="automatic")
    assert (
        no_consent.value.reason
        == "bundled_database_activation_consent_required"
    )


@pytest.mark.parametrize(
    ("sequence", "current_id", "reason"),
    [
        (3, "pair-newer", "bundled_release_pair_downgrade_forbidden"),
        (2, "pair-split", "bundled_release_pair_sequence_conflict"),
    ],
)
def test_packaged_activation_refuses_pair_downgrade_or_same_sequence_split(
    tmp_path, monkeypatch, sequence, current_id, reason
):
    data = tmp_path / "data"
    data.mkdir()
    manifest, pair = _target()
    (data / activation.LIVE_MANIFEST_FILENAME).write_text("{}", encoding="utf-8")
    (data / activation.LIVE_PAIR_MANIFEST_FILENAME).write_text("{}", encoding="utf-8")
    current_manifest = {**manifest, "release_sequence": 1}
    current_pair = {**pair, "pair_sequence": sequence, "release_pair_id": current_id}
    monkeypatch.setattr(
        activation,
        "load_database_release_manifest",
        lambda _path: current_manifest,
    )
    monkeypatch.setattr(
        activation,
        "load_release_pair_manifest",
        lambda _path: current_pair,
    )

    with pytest.raises(activation.BundledDatabaseActivationError) as failure:
        activation._refuse_downgrade_or_split(
            data,
            target_manifest=manifest,
            target_pair=pair,
        )
    assert failure.value.reason == reason


def test_cli_activate_bundled_writes_success_report(tmp_path, monkeypatch):
    result = {
        "status": "activated",
        "migration_status": "installed",
        "rollback_retained": True,
        "startup_state": "current_dev_ready",
        "database_sha256": "c" * 64,
    }
    monkeypatch.setattr(
        activation,
        "activate_bundled_database",
        lambda *_args, **_kwargs: result,
    )
    report = tmp_path / "report.json"
    monkeypatch.setattr(
        activation,
        "write_activation_report",
        lambda *_args, **_kwargs: report,
    )

    response = cli._cmd_db_activate_bundled(
        SimpleNamespace(
            data_dir=str(tmp_path),
            consent_source="user_repair",
        )
    )

    assert response["status"] == "activated"
    assert response["report_path"] == str(report)


def test_cli_capabilities_exposes_fixed_bundled_activation_only():
    command = next(
        row
        for row in cli._capabilities_doc()["commands"]
        if row["name"] == "db activate-bundled"
    )
    assert command.get("available", True)
    names = [row["name"] for row in command["args"]]
    assert names == ["--consent-source"]
    assert "--input" not in names
