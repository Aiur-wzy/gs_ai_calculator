from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from genshin_app import managed_installation as module
from genshin_app import paths, product_first_install
from genshin_app.installation_location import (
    disjoint_roots,
    read_registration,
    resolve_runtime_root,
)
from genshin_app.product_install_contract import FirstInstallError
from tests.test_product_release_index import first_install, releases  # noqa: F401


class Registry:
    def __init__(self):
        self.location = None
        self.app = None
    def read_location(self):
        return self.location
    def write_location(self, row):
        self.location = row
    def remove_location(self, installation_id):
        if self.location and self.location["installation_id"] == installation_id:
            self.location = None
    def register_app(self, **row):
        self.app = row
    def unregister_app(self, installation_id):
        if self.app and self.app["installation_id"] == installation_id:
            self.app = None


@pytest.fixture
def managed(first_install, monkeypatch):  # noqa: F811 - re-exported pytest fixture
    legacy, local, _, _ = first_install
    registry = Registry()
    programs = local / "Programs"
    programs.mkdir()
    monkeypatch.setattr("genshin_app.shortcuts._windows_link", lambda spec: json.dumps(spec, sort_keys=True).encode())
    monkeypatch.setattr(module, "_quiet", lambda *_args: None)
    setup = module.ManagedInstallationCoordinator(local=local, install_root=local / "自选 目录 (Stable)",
        system_registration=registry, url=legacy._url, trust_verifier=legacy._trust_verifier,
        programs_folder=lambda: programs)
    return setup, registry, local


def install(setup, **options):
    preview = setup.preview(**options)
    result = setup.install(user_confirmed=True, confirmation_digest=preview["confirmation_digest"], **options)
    assert result["status"] == "complete"
    return result


def test_custom_root_contains_app_db_cache_and_registered_maintenance(managed):
    setup, registry, _ = managed
    result = install(setup)
    root = setup.layout.root
    assert (root / "app" / "GenshinCalcGUI.exe").exists()
    assert (root / "data" / "genshin.db").exists()
    assert (root / "cache").is_dir()
    assert result["registration_state"] == "installed"
    assert registry.location["root"] == str(root)
    assert registry.app["executable"] == root / "maintenance" / "GenshinCalcSetup.exe"
    assert resolve_runtime_root(root / "app" / "GenshinCalcGUI.exe") == root
    assert len(read_registration(root)["shortcuts"]) == 2


def test_qa_registration_identity_is_scoped_but_distinct_for_app_and_uninstall_shortcuts(managed):
    setup, registry, _ = managed
    registry.display_name = "原神拉表（安装验收 QA abcdef01）"
    registry.app_user_model_id = "Aiur.GenshinCalc.QA." + "a" * 32
    install(setup)
    folder = setup._programs_folder()
    links = list(folder.glob("*.lnk"))
    assert {path.name for path in links} == {registry.display_name + ".lnk", "卸载" + registry.display_name + ".lnk"}
    assert {json.loads(path.read_bytes())["app_user_model_id"] for path in links} == {
        registry.app_user_model_id, registry.app_user_model_id + ".Uninstall"}
    preview = setup.uninstall_preview(keep_user_data=False)
    setup.uninstall(preview["confirmation_digest"], user_confirmed=True, keep_user_data=False)
    assert not any(path.exists() for path in links)


def test_isolated_frozen_lifecycle_runs_real_coordinators(managed, releases, monkeypatch):  # noqa: F811
    from genshin_app import setup_frozen_qualification as qualification
    from tests.test_product_release_index import _signed

    setup, _, local = managed
    _, _, documents, sources, payload = releases
    signed = documents[2, 2]
    index = local / "lifecycle-index.json"
    index.write_text(json.dumps(_signed(payload)), encoding="utf-8")
    root = local / "lifecycle-sandbox"
    root.mkdir()
    monkeypatch.setattr(qualification, "_stable_trust", lambda: setup._trust_verifier)
    monkeypatch.setattr(qualification, "_compiled_document", lambda _: signed)
    # This small fixture is not a frozen App; separately test the startup gate.
    monkeypatch.setattr(qualification, "_verify_startup_admission", lambda *_: {"can_start": True})
    result = qualification._verify_local_lifecycle(root, index,
        sources[signed["artifacts"]["app"]["url"]], sources[signed["artifacts"]["database"]["url"]])
    assert result["status"] == "verified"
    assert "damaged_app_uninstall" in result["checked"]
    assert result["app_only_then_database"]["archive_requests"] == ["app", "database"]
    assert result["app_only_then_database"]["same_selected_root"] is True
    assert result["app_only_then_database"]["cleanup_complete"] is True
    assert result["not_exercised"] == ["windows_registration", "start_menu_search", "gui_rendering", "crash_injection"]
    assert not list(root.rglob("genshin.db"))


def test_factory_uses_location_coordinator(first_install):  # noqa: F811
    _, local, _, _ = first_install
    assert isinstance(product_first_install.build_first_install_coordinator(install_root=local / "new"), module.ManagedInstallationCoordinator)


def test_damage_does_not_block_uninstall_and_unknown_files_are_preserved(managed):
    setup, registry, _ = managed
    install(setup)
    root = setup.layout.root
    (root / "app" / "app.bin").write_bytes(b"damaged")
    (root / "app" / "my-notes.txt").write_text("keep")
    (root / "data" / "plans").mkdir()
    (root / "data" / "plans" / "plan.json").write_text("user plan")
    browser_draft = root / "data/webview-profile/Local Storage/draft"
    browser_draft.parent.mkdir(parents=True)
    browser_draft.write_text("unsent")
    choice = setup.uninstall_preview()
    result = setup.uninstall(choice["confirmation_digest"], user_confirmed=True)
    assert result["database_removed"] and not (root / "data" / "genshin.db").exists()
    assert not (root / "app" / "app.bin").exists()
    assert (root / "app" / "my-notes.txt").read_text() == "keep"
    assert (root / "data" / "plans" / "plan.json").exists()
    assert browser_draft.read_text() == "unsent"
    assert registry.app is None and registry.location is None
    assert not result["cleanup_pending"]


def test_uninstall_keep_choice_is_confirmation_bound(managed):
    setup, _, _ = managed
    install(setup)
    choice = setup.uninstall_preview()
    with pytest.raises(FirstInstallError, match="confirmation_mismatch"):
        setup.uninstall(choice["confirmation_digest"], user_confirmed=True, keep_user_data=False)
    assert (setup.layout.data / "genshin.db").exists()


def test_uninstall_removes_selected_personal_data_but_not_external_files(managed):
    setup, _, local = managed
    install(setup)
    for name in ("plans/plan.json", "native_agent_sessions_v2/session.json", "webview-profile/Local Storage/test.db"):
        path = setup.layout.data / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("personal")
    external = local / "external-plan.json"
    external.write_text("external")
    (setup.layout.data / "foreign.txt").write_text("foreign")
    choice = setup.uninstall_preview(keep_user_data=False)
    setup.uninstall(choice["confirmation_digest"], user_confirmed=True, keep_user_data=False)
    assert not (setup.layout.data / "plans/plan.json").exists()
    assert not (setup.layout.data / "webview-profile/Local Storage/test.db").exists()
    assert external.exists() and (setup.layout.data / "foreign.txt").exists()


def test_path_change_and_overlap_are_rejected(managed):
    setup, _, _ = managed
    with pytest.raises(FirstInstallError, match="roots_overlap"):
        disjoint_roots(setup.layout.root, setup.layout.app)
    choice = setup.preview()
    original = setup.layout.root
    original.rename(original.with_name("moved"))
    with pytest.raises(FirstInstallError, match="confirmation_mismatch"):
        setup.install(user_confirmed=True, confirmation_digest=choice["confirmation_digest"])


def test_migration_moves_database_and_personal_data_not_foreign_files(managed):
    setup, registry, local = managed
    install(setup)
    root = setup.layout.root
    (root / "data/plans").mkdir()
    (root / "data/plans/test.json").write_text("personal")
    (root / "foreign.txt").write_text("foreign")
    target = local / "迁移目标"
    preview = setup.migration_preview(target)
    result = setup.migrate(target, preview["confirmation_digest"], user_confirmed=True)
    assert result["status"] == "complete"
    assert not (root / "app/GenshinCalcGUI.exe").exists()
    assert not (root / "data/genshin.db").exists()
    assert (target / "data/genshin.db").exists()
    assert (target / "data/plans/test.json").read_text() == "personal"
    assert (root / "foreign.txt").exists() and not (target / "foreign.txt").exists()
    assert registry.location["root"] == str(target)
    assert resolve_runtime_root(target / "app/GenshinCalcGUI.exe") == target


def test_managed_runtime_default_does_not_fall_back_to_c_drive(managed, monkeypatch):
    setup, _, _ = managed
    install(setup)
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    monkeypatch.setattr(paths.sys, "executable", str(setup.layout.app / "GenshinCalcGUI.exe"))
    assert paths.default_data_dir() == setup.layout.data


def test_reinstall_downloads_database_and_retains_personal_files(managed):
    setup, _, _ = managed
    install(setup)
    (setup.layout.data / "plans").mkdir()
    personal = setup.layout.data / "plans/important.json"
    personal.write_text("keep")
    choice = setup.uninstall_preview()
    setup.uninstall(choice["confirmation_digest"], user_confirmed=True)
    assert not (setup.layout.data / "genshin.db").exists()
    preview = setup.preview()
    assert preview["download_bytes"]["database"] > 0
    setup.install(user_confirmed=True, confirmation_digest=preview["confirmation_digest"])
    assert personal.read_text() == "keep" and (setup.layout.data / "genshin.db").exists()


def test_interrupted_uninstall_recovers_after_database_removed(managed, monkeypatch):
    setup, _, _ = managed
    install(setup)
    original = module.delete_members
    calls = []
    def fail_once(root, names):
        if not calls:
            calls.append(True)
            original(root, [name for name in names if name == "data/genshin.db"])
            raise OSError("power loss after database delete")
        return original(root, names)
    monkeypatch.setattr(module, "delete_members", fail_once)
    choice = setup.uninstall_preview()
    with pytest.raises(OSError, match="power loss"):
        setup.uninstall(choice["confirmation_digest"], user_confirmed=True)
    assert not (setup.layout.data / "genshin.db").exists()
    recovery = setup.uninstall_preview()
    result = setup.uninstall(recovery["confirmation_digest"], user_confirmed=True)
    assert result["status"] == "uninstalled"


def test_migration_recovers_after_source_deactivation(managed, monkeypatch):
    import genshin_app.installation_migration as migration
    setup, registry, local = managed
    install(setup)
    target = local / "recovery-target"
    choice = setup.migration_preview(target)
    original = migration._save
    def fail_after(operation, state):
        original(operation, state)
        if state == "source_disabled":
            raise OSError("power loss")
    monkeypatch.setattr(migration, "_save", fail_after)
    with pytest.raises(OSError, match="power loss"):
        setup.migrate(target, choice["confirmation_digest"], user_confirmed=True)
    assert not (setup.layout.data / "genshin.db").exists()
    monkeypatch.setattr(migration, "_save", original)
    recovery = setup.maintenance_preview()
    result = migration.recover_migration(setup, recovery["confirmation_digest"], user_confirmed=True)
    assert result["status"] == "complete" and registry.location["root"] == str(target)


def test_forged_maintenance_proof_cannot_authorize_removal(managed):
    setup, _, _ = managed
    install(setup)
    proof = setup.layout.maintenance / "proof/signed-release.json"
    raw = json.loads(proof.read_text())
    raw["signed_release"]["artifacts"]["app"]["url"] = "https://example.invalid/forged.zip"
    proof.write_text(json.dumps(raw))
    with pytest.raises(RuntimeError):
        setup.uninstall_preview()
    assert (setup.layout.data / "genshin.db").exists()


def test_nonempty_and_nested_roots_cannot_be_adopted(managed):
    setup, registry, local = managed
    target = local / "foreign"
    target.mkdir()
    (target / "user.txt").write_text("not owned")
    with pytest.raises(FirstInstallError, match="target_not_empty"):
        module.ManagedInstallationCoordinator(local=local, install_root=target, system_registration=registry,
            url=setup._url, trust_verifier=setup._trust_verifier)
    install(setup)
    with pytest.raises(FirstInstallError, match="roots_overlap"):
        setup.migration_preview(setup.layout.root / "child")
    assert (target / "user.txt").exists()


@pytest.mark.parametrize("app_only", [False, True])
def test_registration_failure_is_recoverable_without_redownload(managed, monkeypatch, app_only):
    setup, registry, _ = managed
    original = registry.register_app
    monkeypatch.setattr(registry, "register_app", lambda **kwargs: (_ for _ in ()).throw(OSError("registry denied")))
    result = install(setup, app_only=app_only)
    assert result["registration_state"] == "registration_pending"
    assert (setup.layout.data / "genshin.db").exists() is not app_only
    monkeypatch.setattr(registry, "register_app", original)
    choice = setup.maintenance_preview()
    monkeypatch.setattr(setup, "_discover", lambda **kwargs: pytest.fail("must not download"))
    result = setup.recover_installation(choice["confirmation_digest"], user_confirmed=True)
    assert result["registration_state"] == "installed"
    assert result["database_state"] == ("database_not_installed" if app_only else "installed")


def test_user_modified_shortcut_survives_and_new_owned_link_is_created(managed):
    setup, _, local = managed
    install(setup)
    original = local / "Programs/原神拉表（正式版）.lnk"
    original.write_bytes(b"user link")
    result = setup.refresh_registration()
    assert result["registration_state"] == "installed"
    assert original.read_bytes() == b"user link"
    assert any(name.startswith("原神拉表（正式版）-") for name in result["start_menu_names"])
    choice = setup.uninstall_preview()
    setup.uninstall(choice["confirmation_digest"], user_confirmed=True)
    assert original.read_bytes() == b"user link"


def test_publish_interruption_recovers_from_durable_manifest(managed, monkeypatch):
    setup, _, _ = managed
    original = Path.rename
    failed = []
    def rename(path, target):
        if Path(target) == setup.layout.data / "genshin.db" and not failed:
            failed.append(True)
            raise OSError("simulated interruption")
        return original(path, target)
    monkeypatch.setattr(Path, "rename", rename)
    preview = setup.preview()
    with pytest.raises(FirstInstallError):
        setup.install(user_confirmed=True, confirmation_digest=preview["confirmation_digest"])
    assert read_registration(setup.layout.root)["installation_state"] == "publishing"
    recovery = setup.maintenance_preview()
    result = setup.recover_installation(recovery["confirmation_digest"], user_confirmed=True)
    assert result["registration_state"] == "installed" and (setup.layout.data / "genshin.db").exists()


def test_recovery_database_must_match_signed_release_not_mutable_journal(managed, monkeypatch):
    setup, _, _ = managed
    original = setup._finish_publication
    monkeypatch.setattr(setup, "_finish_publication", lambda _: (_ for _ in ()).throw(OSError("interrupted")))
    preview = setup.preview()
    with pytest.raises(FirstInstallError):
        setup.install(user_confirmed=True, confirmation_digest=preview["confirmation_digest"])
    journal = setup.layout.maintenance / "operations/publish.json"
    operation = json.loads(journal.read_text("utf-8"))
    database = Path(operation["staged"]) / "data/genshin.db"
    database.write_bytes(b"not the signed database")
    operation["members"]["data/genshin.db"] = module.sha256(database)
    journal.write_text(json.dumps(operation), encoding="utf-8")
    monkeypatch.setattr(setup, "_finish_publication", original)
    recovery = setup.maintenance_preview()
    with pytest.raises(FirstInstallError, match="recovery_database_mismatch"):
        setup.recover_installation(recovery["confirmation_digest"], user_confirmed=True)
    assert read_registration(setup.layout.root)["installation_state"] == "publishing"


def test_managed_executable_with_missing_registration_does_not_fall_back(managed):
    setup, _, _ = managed
    install(setup)
    (setup.layout.root / module.REGISTRATION_FILENAME).unlink()
    with pytest.raises(FirstInstallError, match="registration_missing"):
        resolve_runtime_root(setup.layout.app / "GenshinCalcGUI.exe")


def test_malformed_installation_id_has_structured_failure(managed):
    setup, _, _ = managed
    setup.preview()
    file = setup.layout.root / module.REGISTRATION_FILENAME
    row = json.loads(file.read_text("utf-8"))
    row["installation_id"] = {"wrong": "type"}
    file.write_text(json.dumps(row), encoding="utf-8")
    with pytest.raises(FirstInstallError, match="registration_invalid"):
        read_registration(setup.layout.root)


def test_migration_copy_interruption_can_resume(managed, monkeypatch):
    import genshin_app.installation_migration as migration
    setup, _, local = managed
    install(setup)
    target = local / "copy-recovery"
    choice = setup.migration_preview(target)
    original = migration.shutil.copyfile
    failed = []
    def copyfile(source, destination, *args, **kwargs):
        if "/candidate/" in Path(destination).as_posix() and not failed:
            failed.append(True)
            raise OSError("copy interrupted")
        return original(source, destination, *args, **kwargs)
    monkeypatch.setattr(migration.shutil, "copyfile", copyfile)
    with pytest.raises(OSError, match="copy interrupted"):
        setup.migrate(target, choice["confirmation_digest"], user_confirmed=True)
    assert (setup.layout.data / "genshin.db").exists()
    (setup.layout.data / "plans").mkdir(exist_ok=True)
    (setup.layout.data / "plans/created-after-interruption.json").write_text("latest user work")
    recovery = setup.maintenance_preview()
    result = migration.recover_migration(setup, recovery["confirmation_digest"], user_confirmed=True)
    assert result["status"] == "complete" and (target / "data/genshin.db").exists()
    assert (target / "data/plans/created-after-interruption.json").read_text() == "latest user work"


def test_cancelled_migration_leaves_source_usable_and_target_reusable(managed):
    setup, registry, local = managed
    install(setup)
    target = local / "cancel-target"
    choice = setup.migration_preview(target)
    with pytest.raises(FirstInstallError, match="cancelled"):
        setup.migrate(target, choice["confirmation_digest"], user_confirmed=True, cancel_check=lambda: True)
    assert registry.location["root"] == str(setup.layout.root)
    assert (setup.layout.data / "genshin.db").exists()
    repeated = setup.migration_preview(target)
    result = setup.migrate(target, repeated["confirmation_digest"], user_confirmed=True)
    assert result["status"] == "complete"


def test_busy_product_cannot_be_uninstalled(managed, monkeypatch):
    setup, _, _ = managed
    install(setup)
    choice = setup.uninstall_preview()
    def busy(*_args):
        raise FirstInstallError("database_legacy_instance_running")
    monkeypatch.setattr(module, "_quiet", busy)
    with pytest.raises(FirstInstallError, match="legacy_instance_running"):
        setup.uninstall(choice["confirmation_digest"], user_confirmed=True)
    assert (setup.layout.app / "GenshinCalcGUI.exe").exists() and (setup.layout.data / "genshin.db").exists()


def test_runtime_leases_allow_multiple_instances_and_block_maintenance(managed):
    from genshin_app.installation_runtime import (
        installation_mutation_lock,
        installation_runtime_lease,
    )
    setup, _, _ = managed
    install(setup)
    with (installation_runtime_lease(setup.layout.root), installation_runtime_lease(setup.layout.root),
          pytest.raises(FirstInstallError, match="installation_running"), installation_mutation_lock(setup.layout.root)):
        pass
    with installation_mutation_lock(setup.layout.root):
        pass


def test_new_runtime_cannot_enter_during_maintenance(managed):
    from genshin_app.installation_runtime import (
        installation_mutation_lock,
        installation_runtime_lease,
    )
    setup, _, _ = managed
    install(setup)
    def enter():
        with installation_runtime_lease(setup.layout.root):
            return True
    with ThreadPoolExecutor(max_workers=1) as worker:
        with installation_mutation_lock(setup.layout.root), pytest.raises(FirstInstallError, match="maintenance_busy"):
            worker.submit(enter).result()
        assert worker.submit(enter).result()


def test_stale_runtime_lease_is_retired_after_crash(managed):
    from genshin_app.installation_runtime import installation_mutation_lock
    setup, _, _ = managed
    install(setup)
    directory = setup.layout.maintenance / "runtime-leases"
    directory.mkdir()
    stale = directory / "stale.lock"
    stale.write_bytes(b"\0")
    with installation_mutation_lock(setup.layout.root):
        assert not stale.exists()
