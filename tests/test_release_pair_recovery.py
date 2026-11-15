from __future__ import annotations

import shutil
from types import SimpleNamespace

import pytest

from genshin_app import database_legacy_processes, product_release_index
from genshin_app import release_pair_coordinator as coordinator
from genshin_app import release_pair_helper as helper
from genshin_app import release_pair_transport as transport
from genshin_app import release_pair_update as update
from genshin_app.product_release_index import canonical
from tests.test_product_release_index import (  # noqa: F401
    _install_facts,
    _sha,
    _signed,
    _verify,
    releases,
)


class InterruptedUpdate(BaseException):
    """Model process loss rather than a recoverable exception inside the updater."""


def _prepare_profile(tmp_path, monkeypatch, releases, mode):  # noqa: F811
    apps, databases, documents, sources, index = releases
    profile = tmp_path / "installed"
    app, data, workspace = profile / "app", profile / "data", profile / "update-workspace"
    shutil.copytree(apps[1][0], app)
    document = documents[1, 1]
    _install_facts(data, app, document["pair_manifest"], databases[1], document)
    user_file = data / "user-document.json"
    user_file.write_text('{"keep":true}', encoding="utf-8")
    monkeypatch.setattr(product_release_index, "_fetch_https", lambda *_a, **_kw: (200, canonical(_signed(index)), None))

    def download(artifact, destination, **_kwargs):
        shutil.copyfile(sources[artifact["url"]], destination)
        return {"verified": True}

    monkeypatch.setattr(transport, "_download_https", download)
    monkeypatch.setattr(helper, "sys", SimpleNamespace(frozen=True, executable=__file__))
    monkeypatch.setattr(database_legacy_processes, "packaged_peer_processes", list)
    service = coordinator.ReleasePairCoordinator(
        data_dir=data, active_app_root=app, workspace=workspace, channel="stable",
        discovery_url="https://example.invalid/latest.json", trust_verifier=_verify,
        updater_locator=lambda: {"executable": tmp_path / "updater.exe"},
        launcher=lambda *_args: {"accepted": True}, restart_relative="app.bin",
    )
    discovery = service.discover()
    option = next(row for row in discovery["update_options"] if row["mode"] == mode)
    handoff = service.prepare_and_launch(discovery, confirmed_document_sha256=option["confirmation_digest"],
                                         user_confirmed=True)
    return service, handoff["operation_id"], user_file


@pytest.fixture
def interrupted_profile(tmp_path, monkeypatch, releases, request):  # noqa: F811
    apps, databases, documents, _, _ = releases
    service, operation_id, user_file = _prepare_profile(tmp_path, monkeypatch, releases, request.param)
    app, data, workspace = service.active_app_root, service.data_dir, service.workspace

    def stop_before_postcheck(*_args, **_kwargs):
        raise InterruptedUpdate()

    with monkeypatch.context() as fault:
        fault.setattr(update, "postcheck_pair_update", stop_before_postcheck)
        with pytest.raises(InterruptedUpdate):
            helper.execute_external_pair_update(workspace, app, data, operation_id=operation_id, current_user=True)
    assert update.read_pair_update_journal(workspace)["state"] == "activation_started"
    return app, data, workspace, operation_id, documents[1, 1], apps[1][0], databases[1][0], user_file


@pytest.mark.parametrize("interrupted_profile", ["app_only", "database_only", "both"], indirect=True)
def test_explicit_recovery_restores_previous_signed_pair(interrupted_profile):
    app, data, work, operation_id, document, old_app, old_db, user_file = interrupted_profile
    with pytest.raises(helper.ReleasePairHelperError, match="release_pair_update_confirmation_required"):
        helper.recover_interrupted_pair_update(work, app, data, operation_id=operation_id, user_confirmed=False)
    report = helper.recover_interrupted_pair_update(work, app, data, operation_id=operation_id, user_confirmed=True)
    assert report["action"] == "restored_previous_pair"
    assert report["release_pair_id"] == document["pair_manifest"]["release_pair_id"]
    assert _sha(app / "app.bin") == _sha(old_app / "app.bin")
    assert _sha(data / "genshin.db") == _sha(old_db)
    assert user_file.read_text(encoding="utf-8") == '{"keep":true}'
    assert update.read_pair_update_journal(work)["state"] == "cleanup_complete"


@pytest.mark.parametrize("interrupted_profile", ["app_only"], indirect=True)
def test_recovery_retries_after_target_app_was_already_parked(interrupted_profile):
    app, data, work, operation_id, document, old_app, _, _ = interrupted_profile
    protected = helper._protected_rollback_root(app, operation_id)
    app.rename(protected / "failed-app")
    report = helper.recover_interrupted_pair_update(work, app, data, operation_id=operation_id, user_confirmed=True)
    assert report["release_pair_id"] == document["pair_manifest"]["release_pair_id"]
    assert _sha(app / "app.bin") == _sha(old_app / "app.bin")
    assert not protected.exists()


@pytest.mark.parametrize("interrupted_profile", ["both"], indirect=True)
def test_recovery_rejects_changed_backup_before_touching_active_paths(interrupted_profile):
    app, data, work, operation_id, *_ = interrupted_profile
    original_app, original_db = _sha(app / "app.bin"), _sha(data / "genshin.db")
    saved = helper._protected_rollback_root(app, operation_id) / "app" / "app.bin"
    saved.write_bytes(b"changed")
    with pytest.raises(update.ReleasePairUpdateError, match="supply_chain_package_manifest_hash_mismatch"):
        helper.recover_interrupted_pair_update(work, app, data, operation_id=operation_id, user_confirmed=True)
    assert (_sha(app / "app.bin"), _sha(data / "genshin.db")) == (original_app, original_db)


@pytest.mark.parametrize("interrupted_profile", ["app_only"], indirect=True)
def test_diagnostic_tracks_the_interrupted_operation_and_disappears_after_recovery(interrupted_profile):
    app, data, work, operation_id, *_ = interrupted_profile
    helper._record_update_outcome(work, operation_id, helper.ReleasePairHelperError("database_migration_validation_failed"))
    assert helper.read_update_outcome(work) == {
        "status": "failed", "state": "activation_started", "reason": "database_migration_validation_failed",
        "previous_pair_restored": False,
    }
    helper.recover_interrupted_pair_update(work, app, data, operation_id=operation_id, user_confirmed=True)
    assert helper.read_update_outcome(work) is None


def test_fresh_signed_index_replaces_obsolete_staging_for_same_target(tmp_path, monkeypatch, releases):  # noqa: F811
    service, old_operation, user_file = _prepare_profile(tmp_path, monkeypatch, releases, "app_only")
    app, data = service.active_app_root, service.data_dir
    installed = (_sha(app / "app.bin"), _sha(data / "genshin.db"), _sha(user_file))

    def confirm():
        discovery = service.discover()
        option = next(row for row in discovery["update_options"] if row["mode"] == "app_only")
        return service.prepare_and_launch(discovery, confirmed_document_sha256=option["confirmation_digest"],
                                          user_confirmed=True)["operation_id"]

    assert confirm() == old_operation
    index = releases[-1]
    renewed = {**index, "index_sequence": index["index_sequence"] + 1}
    monkeypatch.setattr(product_release_index, "_fetch_https", lambda *_a, **_kw: (200, canonical(_signed(renewed)), None))
    # The coordinator can discard staging from within the running App. It cannot swap the App.
    monkeypatch.setattr(helper, "sys", SimpleNamespace(frozen=True, executable=str(app / "app.bin")))
    assert confirm() != old_operation
    assert (_sha(app / "app.bin"), _sha(data / "genshin.db"), _sha(user_file)) == installed
