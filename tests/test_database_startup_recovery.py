from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_database_release_migration as migration_fixture
from test_database_release_migration import _make_pair
from test_database_startup_gate import (
    _artifacts,
    _database,
    _freeze_startup,
    _identity,
    _seed_live_profile,
)

from genshin_app import bundled_database_activation as activation
from genshin_app import database_bootstrap as bootstrap
from genshin_app import database_startup_recovery as recovery
from genshin_app import database_transaction as transaction
from genshin_app.gui import launcher
from genshin_db import release_migration as migration
from genshin_db.release_profile import create_release_pair_manifest


@pytest.fixture
def fixed_package(tmp_path, monkeypatch):
    data = tmp_path / "profile"
    data.mkdir()
    (data / "genshin.db").write_bytes(b"old")
    package = tmp_path / "package.db"
    package.write_bytes(b"new")
    target = {"database_release_id": "db-new", "release_sequence": 2,
              "database": {"sha256": hashlib.sha256(b"new").hexdigest()}}
    pair = {"release_pair_id": "pair-new", "pair_sequence": 2}
    monkeypatch.setattr(activation, "is_frozen", lambda: True)
    monkeypatch.setattr(activation, "packaged_peer_processes", list)
    monkeypatch.setattr(activation, "_load_verified_bundled_release", lambda: (package, target, pair, "a" * 64))
    monkeypatch.setattr(activation, "load_database_release_manifest",
                        lambda path: json.loads(path.read_text(encoding="utf-8")))
    monkeypatch.setattr(activation, "load_release_pair_manifest",
                        lambda path: json.loads(path.read_text(encoding="utf-8")))
    monkeypatch.setattr(activation, "database_release_semantically_equal", lambda a, b: a == b)
    monkeypatch.setattr(activation, "verified_reusable_database", lambda *_: None)

    def install(directory, candidate, manifest, **kwargs):
        kwargs["fault_hook"]("before_replace")
        (directory / "genshin.db").write_bytes(candidate.read_bytes())
        (directory / activation.LIVE_MANIFEST_FILENAME).write_text(json.dumps(manifest), encoding="utf-8")
        (directory / activation.LIVE_RECEIPT_FILENAME).write_text("{}", encoding="utf-8")
        kwargs["fault_hook"]("after_commit")
        return {"status": "installed"}

    monkeypatch.setattr(activation, "install_database_release", install)
    monkeypatch.setattr(activation, "_verify_installed_database", lambda *_: {"sha256": target["database"]["sha256"]})
    monkeypatch.setattr(activation, "prepare_database_startup", lambda *_: {"can_start": True, "state": "current_dev_ready"})
    return data, package, target, pair


def test_confirmation_is_precise_and_inspection_does_not_write(fixed_package):
    data, _, _, _ = fixed_package
    before = {p.name: p.read_bytes() for p in data.iterdir()}
    confirmed = activation.inspect_bundled_activation(data)
    assert {p.name: p.read_bytes() for p in data.iterdir()} == before
    (data / "genshin.db").write_bytes(b"changed after consent")
    with pytest.raises(activation.BundledDatabaseActivationError) as failure:
        activation.activate_bundled_database(data, consent_source="user_repair",
            confirmation_digest=confirmed["confirmation_digest"])
    assert failure.value.reason == "bundled_database_confirmation_changed"
    assert not (data / transaction.ACTIVATION_JOURNAL).exists()


def test_cancel_before_commit_keeps_old_active_files(fixed_package):
    data, _, _, _ = fixed_package
    event = threading.Event()
    def cancel(phase):
        if phase == "before_replace":
            event.set()
    # Simulate a request arriving before the migration's prepublication hook.
    original = activation.install_database_release
    def install(*args, **kwargs):
        event.set()
        return original(*args, **kwargs)
    activation.install_database_release = install
    try:
        with pytest.raises(activation.BundledDatabaseActivationCancelled):
            activation.activate_bundled_database(data, consent_source="user_repair", cancel_event=event, fault_hook=cancel)
    finally:
        activation.install_database_release = original
    assert (data / "genshin.db").read_bytes() == b"old"
    assert transaction.read_activation_journal(data)["state"] == "cancelled"


def test_late_cancel_finishes_committed_release(fixed_package):
    data, _, _, pair = fixed_package
    event = threading.Event()
    result = activation.activate_bundled_database(data, consent_source="user_repair", cancel_event=event,
        fault_hook=lambda phase: event.set() if phase == "after_commit" else None)
    assert result["status"] == "activated" and result["cancel_requested_after_commit"]
    assert json.loads((data / activation.LIVE_PAIR_MANIFEST_FILENAME).read_text()) == pair
    assert transaction.read_activation_journal(data)["state"] == "complete"


@pytest.mark.parametrize("phase", ["before_pair_commit", "after_pair_commit"])
def test_pair_crash_retains_unknown_and_resumes_without_reinstall(fixed_package, monkeypatch, phase):
    data, _, target, pair = fixed_package
    def crash(at):
        if at == phase:
            raise SystemExit("simulated process stop")
    with pytest.raises(SystemExit):
        activation.activate_bundled_database(data, consent_source="user_repair", fault_hook=crash)
    assert transaction.read_activation_journal(data)["state"] == "unknown"
    monkeypatch.setattr(activation, "read_database_identity", lambda *_: {"sha256": target["database"]["sha256"]})
    monkeypatch.setattr(activation, "rebind_database_sidecars", lambda *_: None)
    monkeypatch.setattr(activation, "verified_reusable_database", lambda *_: {"sha256": target["database"]["sha256"]})
    monkeypatch.setattr(activation, "install_database_release", lambda *_a, **_k: pytest.fail("must not repeat accepted migration"))
    confirmed = activation.inspect_bundled_activation(data)
    result = activation.activate_bundled_database(data, consent_source="user_repair",
        confirmation_digest=confirmed["confirmation_digest"])
    assert result["migration_status"] == "database_reused"
    assert transaction.read_activation_journal(data)["state"] == "complete"
    assert json.loads((data / activation.LIVE_PAIR_MANIFEST_FILENAME).read_text()) == pair


def test_legacy_peer_refusal_precedes_mutation(fixed_package, monkeypatch):
    data, _, _, _ = fixed_package
    monkeypatch.setattr(activation, "packaged_peer_processes", lambda: [{"pid": 123, "name": "GenshinCalcGUI.exe"}])
    with pytest.raises(activation.BundledDatabaseActivationError) as failure:
        activation.activate_bundled_database(data, consent_source="user_repair")
    assert failure.value.reason == "database_legacy_instance_running"
    assert (data / "genshin.db").read_bytes() == b"old"
    assert not (data / transaction.ACTIVATION_JOURNAL).exists()


def test_process_fence_is_cross_process_and_thread_reentrant(tmp_path):
    with transaction.profile_writer(tmp_path), transaction.profile_writer(tmp_path):
        assert (tmp_path / ".update.lock").exists()
    script = """
import sys,time
from pathlib import Path
from genshin_db.locking import file_lock
with file_lock(Path(sys.argv[1])/'.update.lock',timeout=0):
 print('locked',flush=True)
 time.sleep(1)
"""
    env = {**os.environ, "PYTHONPATH": str(Path("src").resolve())}
    process = subprocess.Popen([sys.executable, "-c", script, str(tmp_path)], env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        assert process.stdout.readline().strip() == "locked"
        with pytest.raises(bootstrap.DatabaseStartupError) as failure:
            bootstrap.prepare_database_startup(tmp_path)
        assert failure.value.reason == "database_migration_busy"
        assert not (tmp_path / activation.LIVE_PAIR_MANIFEST_FILENAME).exists()
    finally:
        process.communicate(timeout=5)
    assert process.returncode == 0


@pytest.mark.parametrize("phase", ["before_stage_copy", "after_candidate_validation"])
def test_real_prepublication_process_crash_recovers_verified_attempt(tmp_path, monkeypatch, phase):
    data, live, package, manifest = _make_pair(tmp_path)
    original = hashlib.sha256(live.read_bytes()).hexdigest()
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
    script = """
import sys,json,os
from pathlib import Path
from test_database_release_migration import _fake_identity,_fixture_pair_id
from genshin_db import release_migration as m
from genshin_db.release_profile import create_release_pair_manifest
m._validate_database=_fake_identity
data,package,mp,phase=map(str,sys.argv[1:])
manifest=json.loads(Path(mp).read_text(encoding='utf-8'))
pair=create_release_pair_manifest(manifest,release_pair_id=_fixture_pair_id(manifest),pair_sequence=1,pair_policy='exact',app_release_id='app-0.45.0',app_version='0.45.0',app_artifact_manifest_sha256='f'*64)
m.install_database_release(Path(data),Path(package),manifest,logic_profile='logic-v1',data_contract_version=1,user_confirmed=True,production=True,app_release_id='app-0.45.0',release_pair_id=_fixture_pair_id(manifest),required_database_release_id=manifest['database_release_id'],pair_policy='exact',app_version='0.45.0',app_artifact_manifest_sha256='f'*64,pair_manifest=pair,fault_hook=lambda p:os._exit(77) if p==phase else None)
"""
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(Path("src").resolve()), str(Path("tests").resolve())])}
    result = subprocess.run([sys.executable, "-c", script, str(data), str(package), str(manifest_file), phase],
        env=env, capture_output=True, timeout=20, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert result.returncode == 77, result.stderr.decode(errors="replace")
    assert hashlib.sha256(live.read_bytes()).hexdigest() == original
    journal = json.loads((data / migration.JOURNAL_FILE).read_text())
    assert journal["state"] in {"prepared", "validated"}
    assert migration.abandon_unpublished_database_attempt(data, manifest, expected_old_sha256=original)
    assert json.loads((data / migration.JOURNAL_FILE).read_text())["state"] == "rolled_back"
    monkeypatch.setattr(migration, "_validate_database", migration_fixture._fake_identity)
    pair = migration_fixture.create_release_pair_manifest(manifest,
        release_pair_id=migration_fixture._fixture_pair_id(manifest), pair_sequence=1,
        pair_policy="exact", app_release_id="app-0.45.0", app_version="0.45.0",
        app_artifact_manifest_sha256="f" * 64)
    result = migration.install_database_release(data, package, manifest,
        logic_profile="logic-v1", data_contract_version=1, user_confirmed=True,
        production=True, app_release_id="app-0.45.0",
        release_pair_id=migration_fixture._fixture_pair_id(manifest),
        required_database_release_id=manifest["database_release_id"], pair_policy="exact",
        app_version="0.45.0", app_artifact_manifest_sha256="f" * 64, pair_manifest=pair)
    assert result["status"] == "installed"


def test_controller_requires_consent_and_recovers_through_real_launcher(fixed_package, monkeypatch):
    data, _, _, _ = fixed_package
    created, calls = [], []
    def gate(*_args, **_kwargs):
        if (data / "genshin.db").read_bytes() != b"new":
            raise bootstrap.DatabaseStartupError("database_live_manifest_not_bundled_release", "需要配套数据库")
        return {"can_start": True, "state": "current_dev_ready"}
    monkeypatch.setattr(launcher, "prepare_database_startup", gate)
    monkeypatch.setattr(launcher, "is_frozen", lambda: True)
    monkeypatch.setattr(launcher, "ensure_data_dir", lambda p: p)
    class Manager:
        def __init__(self, *_):
            assert (data / "genshin.db").read_bytes() == b"new"
            calls.append("runtime_after_admission")
        def ensure_running(self):
            return {"base_url": "fixture", "owned": False}
        def stop(self):
            calls.append("stopped")
    monkeypatch.setattr(launcher, "ServiceManager", Manager)
    monkeypatch.setattr(launcher, "GuiApi", lambda *_a, **_k: SimpleNamespace(agent_shutdown=lambda: None))
    monkeypatch.setattr(launcher, "install_close_guard", lambda *_: None)
    def create(_title, **kwargs):
        window = SimpleNamespace(evaluate_js=lambda _script: None, destroy=lambda: calls.append("splash_destroyed"))
        created.append((kwargs, window))
        return window
    def start(callback, *, storage_path, private_mode):
        assert Path(storage_path) == data / "webview-profile"
        assert private_mode is False
        callback()
        assert len(created) == 1 and not calls
        port = created[0][0]["js_api"]
        assert not port.confirm_recovery("forged", True)["accepted"]
        assert port.inspect_recovery()["accepted"]
        port._join()
        confirmation = port.get_status()["confirmation"]
        assert (data / "genshin.db").read_bytes() == b"old"
        assert port.confirm_recovery(confirmation["confirmation_digest"], True)["accepted"]
        port._join()
        assert port.get_status()["status"] == "complete"
        assert len(created) == 2 and "url" in created[1][0]
    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(create_window=create, start=start))
    assert launcher.run_gui(data, emit_startup_status=False) == 0
    assert calls == ["runtime_after_admission", "splash_destroyed", "stopped"]
    diagnostic = json.loads((data / "logs/database-startup-last.json").read_text(encoding="utf-8"))
    assert diagnostic["status"] == "complete"
    trace = next((data / "logs").glob("database-startup-*.jsonl"))
    records = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    assert records[0]["reason"] == "database_live_manifest_not_bundled_release"
    assert records[-1]["status"] == "complete"


def test_diagnostic_write_failure_preserves_reason_and_decline_never_installs(tmp_path, monkeypatch):
    monkeypatch.setattr(transaction, "atomic_json", lambda *_: (_ for _ in ()).throw(PermissionError("read only")))
    status = []
    port = recovery.DatabaseStartupRecovery(tmp_path, publish=status.append, retry=lambda: pytest.fail("no retry"))
    port._failed(bootstrap.DatabaseStartupError("original_database_fault"), repair_available=True)
    assert port.get_status()["reason"] == "original_database_fault"
    assert port.get_status()["diagnostic"]["diagnostic_write_failed"]
    assert port.decline_recovery()["accepted"]
    assert not list(tmp_path.iterdir())


def test_old_database_requires_clear_user_choice_without_installing(tmp_path):
    statuses = []
    port = recovery.DatabaseStartupRecovery(tmp_path, publish=statuses.append,
                                            retry=lambda: pytest.fail("not confirmed"))
    port._failed(bootstrap.DatabaseStartupError("database_live_manifest_not_bundled_release"),
                 repair_available=True)
    status = port.get_status()
    assert status["status"] == "database_check_required"
    assert status["repair_available"] is True
    assert "确认前不会修改" in status["message"]
    assert status["diagnostic"]["reason"] == "database_live_manifest_not_bundled_release"
    assert not (tmp_path / "genshin.db").exists()


def test_ordinary_startup_cannot_rebind_while_legacy_instance_is_running(tmp_path, monkeypatch):
    data = tmp_path / "profile"
    _database(data)
    identity = _identity()
    resources = tmp_path / "resources"
    manifest, pair = _artifacts(resources, identity, policy="current_only", pair_sequence=2)
    previous = create_release_pair_manifest(manifest, release_pair_id="pair-previous",
        pair_sequence=1, pair_policy="current_only", app_release_id="app-previous",
        app_version=pair["app"]["app_version"],
        app_artifact_manifest_sha256=pair["app"]["artifact_manifest"]["sha256"])
    _seed_live_profile(data, identity, manifest, previous)
    monkeypatch.setattr(bootstrap, "packaged_peer_processes", lambda: [{"pid": 123, "name": "GenshinCalcGUI.exe"}])
    monkeypatch.setattr(bootstrap, "DATABASE_RELEASE_CHANNEL", "dev")
    monkeypatch.setattr(bootstrap, "DATABASE_PAIR_POLICY", "current_only")
    monkeypatch.setattr("genshin_db.release_profile.read_database_identity", lambda *_: identity)
    _freeze_startup(monkeypatch, resources)
    before = (data / bootstrap.LIVE_PAIR_MANIFEST_FILENAME).read_bytes()
    with pytest.raises(bootstrap.DatabaseStartupError) as error:
        bootstrap.prepare_database_startup(data)
    assert error.value.reason == "database_legacy_instance_running"
    assert (data / bootstrap.LIVE_PAIR_MANIFEST_FILENAME).read_bytes() == before
    monkeypatch.setattr(bootstrap, "packaged_peer_processes", list)
    assert bootstrap.prepare_database_startup(data)["rebound_live_pair"]


def test_frozen_current_only_first_install_publishes_complete_identity(tmp_path, monkeypatch):
    data = tmp_path / "profile"
    _database(data)
    identity = _identity()
    resources = tmp_path / "resources"
    _artifacts(resources, identity, policy="current_only")
    _freeze_startup(monkeypatch, resources)
    monkeypatch.setattr(bootstrap, "packaged_peer_processes", list)
    monkeypatch.setattr("genshin_db.release_profile.read_database_identity", lambda *_: identity)
    result = bootstrap.prepare_database_startup(data)
    assert result["initialized_sidecars"]
    for name in (bootstrap.LIVE_MANIFEST_FILENAME, bootstrap.LIVE_PAIR_MANIFEST_FILENAME,
                 bootstrap.LIVE_RECEIPT_FILENAME):
        assert (data / name).is_file()
    assert not bootstrap.prepare_database_startup(data)["initialized_sidecars"]


@pytest.mark.parametrize("attack", ["changed_old", "changed_target", "foreign_staging"])
def test_unpublished_recovery_refuses_changed_identity_or_foreign_staging(tmp_path, attack):
    data, live, _package, manifest = _make_pair(tmp_path)
    old_sha = hashlib.sha256(live.read_bytes()).hexdigest()
    stage = data / "build/database-release-dbm-fixture.db"
    stage.parent.mkdir(exist_ok=True)
    stage.write_bytes(b"preserve until verified")
    migration._journal(data / migration.JOURNAL_FILE, operation_id="dbm-fixture",
        target_release_id=manifest["database_release_id"], state="validated", production=True,
        old_sha256=old_sha, target_package_sha256=manifest["database"]["sha256"],
        target_manifest_fingerprint=migration._fingerprint(manifest), staging_name=stage.name)
    if attack == "changed_old":
        live.write_bytes(b"another database")
    elif attack == "changed_target":
        manifest = {**manifest, "release_sequence": 2}
    else:
        journal = json.loads((data / migration.JOURNAL_FILE).read_text())
        journal["staging_name"] = "unrelated.db"
        foreign = stage.with_name("unrelated.db")
        foreign.write_bytes(b"must never be deleted")
        (data / migration.JOURNAL_FILE).write_text(json.dumps(journal))
    before = (data / migration.JOURNAL_FILE).read_bytes()
    with pytest.raises(migration.DatabaseMigrationError):
        migration.abandon_unpublished_database_attempt(data, manifest, expected_old_sha256=old_sha)
    assert stage.exists()
    assert (data / migration.JOURNAL_FILE).read_bytes() == before


def test_production_startup_html_consent_and_diagnostic_dom(tmp_path):
    root = Path(__file__).resolve().parents[1]
    html = tmp_path / "startup.html"
    html.write_text(launcher._DATABASE_STARTUP_HTML, encoding="utf-8")
    script = r"""
const {JSDOM} = require(process.argv[1] + '/frontend/node_modules/jsdom');
const fs = require('node:fs'), assert = require('node:assert/strict');
const dom = new JSDOM(fs.readFileSync(process.argv[2],'utf8'), {runScripts:'dangerously'});
const w=dom.window, d=w.document, calls=[];
w.pywebview={api:{inspect_recovery:()=>calls.push('inspect'),
 confirm_recovery:(digest,yes)=>calls.push([digest,yes]),
 decline_recovery:()=>calls.push('decline'),cancel_recovery:()=>calls.push('cancel'),
 retry_startup:()=>calls.push('retry')}};
w.databaseStartupFailure({reason:'database_live_manifest_not_bundled_release',status:'database_check_required',message:'需要配套数据库'});
assert.equal(d.getElementById('title').textContent,'需要检查配套数据库');
assert.equal(d.body.classList.contains('failed'),false);
assert.ok(!d.getElementById('detail').textContent.includes('请关闭窗口'));
w.databaseRecoveryStatus({status:'failed',busy:false,repair_available:true,message:'检查后可确认',
 diagnostic:{reason:'original_fault',diagnostic_write_failed:true}});
assert.equal(d.getElementById('recovery').hidden,false);
assert.equal(d.getElementById('confirm').hidden,true);
d.getElementById('inspect').click();
w.databaseRecoveryStatus({status:'awaiting_consent',busy:false,
 confirmation:{confirmation_digest:'exact-digest',current_database_release_id:'old',target_database_release_id:'new',message:'固定包'}});
assert.equal(d.getElementById('confirm').hidden,false);
d.getElementById('confirm').click();
d.getElementById('decline').click();
w.databaseRecoveryStatus({status:'installing',busy:true,message:'正在安装'});
assert.equal(d.getElementById('cancel').hidden,false);
assert.equal(d.getElementById('retry').disabled,true);
d.getElementById('cancel').click();
assert.deepEqual(calls,['inspect',['exact-digest',true],'decline','cancel']);
assert.match(d.getElementById('diagnostic-text').value,/original_fault/);
dom.window.close();
"""
    result = subprocess.run([str(root / "agent-runtime/node.exe"), "-e", script, str(root), str(html)],
        capture_output=True, text=True, check=False, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert result.returncode == 0, result.stderr


def test_late_cancel_does_not_claim_old_database_was_preserved(fixed_package, monkeypatch):
    data, _, _, _ = fixed_package
    events = []
    port = None
    def retry():
        assert port._admission_cancelled()
        port._admission_aborted()
    port = recovery.DatabaseStartupRecovery(data, publish=events.append, retry=retry)
    confirmed = activation.inspect_bundled_activation(data)
    port._confirmation = confirmed
    real_activate = recovery.activate_bundled_database
    def late(*args, **kwargs):
        return real_activate(*args, **kwargs,
            fault_hook=lambda phase: kwargs["cancel_event"].set() if phase == "after_commit" else None)
    monkeypatch.setattr(recovery, "activate_bundled_database", late)
    assert port.confirm_recovery(confirmed["confirmation_digest"], True)["accepted"]
    port._join()
    assert (data / "genshin.db").read_bytes() == b"new"
    assert port.get_status()["status"] == "complete"
    assert "已取消进入应用" in port.get_status()["message"]
    assert not any("已保留原数据库" in event.get("message", "") for event in events)
    public = {name for name in dir(port) if not name.startswith("_") and callable(getattr(port, name))}
    assert public == {"get_status", "inspect_recovery", "confirm_recovery", "decline_recovery", "cancel_recovery", "retry_startup"}
