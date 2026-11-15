"""Candidate packaging must neither swap the live DB nor accept outside paths."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def builder(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[1] / "scripts/build_dist.py"
    spec = importlib.util.spec_from_file_location("candidate_build_dist", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setattr(module, "ROOT", root)
    # Orchestration fixtures without a predecessor model a first release.
    # Successor/predecessor cases below opt into an explicit later sequence.
    isolated_version = SimpleNamespace(**{name: value for name, value in vars(module.app_version).items()
                                         if not name.startswith("__")})
    isolated_version.RELEASE_PAIR_SEQUENCE = 1
    monkeypatch.setattr(module, "app_version", isolated_version)
    monkeypatch.setenv("GENSHIN_BUNDLED_DATABASE", "untrusted-inherited-value")
    plan = SimpleNamespace(
        public=lambda: {"state": "validated"},
        release_ready=True,
        release_blockers=(),
        product_app_version="0.45.0",
        runtime_root=root / "agent-runtime",
        node_executable=root / "node.exe",
        identity={"node_version": "24.19.0"},
        manifest_path=root / "agent-runtime/manifest.json",
        dependencies={"directories": 0},
    )
    monkeypatch.setattr(
        module,
        "create_native_agent_build_plan",
        lambda **_kwargs: plan,
    )
    monkeypatch.setattr(
        module,
        "prepare_native_agent_stage",
        lambda _plan, **kwargs: Path(kwargs["staging_root"]),
    )
    monkeypatch.setattr(module, "cleanup_native_agent_stage", lambda *_a, **_k: None)
    monkeypatch.setattr(module, "write_build_plan", lambda *_a, **_k: None)
    module._real_validate_database_release_hygiene = (
        module._validate_database_release_hygiene
    )
    monkeypatch.setattr(
        module,
        "_validate_database_release_hygiene",
        lambda *_a, **_k: None,
    )
    database_manifest = {"contract": "test-database-release-manifest"}
    pair_manifest = {"contract": "test-release-pair-manifest"}
    monkeypatch.setattr(
        module,
        "create_database_release_manifest",
        lambda *_a, **_k: database_manifest,
    )
    monkeypatch.setattr(
        module,
        "write_database_release_manifest",
        lambda path, _manifest: path,
    )
    monkeypatch.setattr(
        module,
        "load_database_release_manifest",
        lambda _path: database_manifest,
    )
    monkeypatch.setattr(
        module,
        "create_release_pair_manifest",
        lambda *_a, **_k: pair_manifest,
    )
    monkeypatch.setattr(
        module,
        "write_release_pair_manifest",
        lambda path, _manifest: path,
    )
    monkeypatch.setattr(
        module,
        "load_release_pair_manifest",
        lambda _path: pair_manifest,
    )
    monkeypatch.setattr(
        module,
        "generate_supply_chain_materials",
        lambda **_kwargs: {
            "component_counts": {"total": 0},
            "review_required_count": 0,
        },
    )
    return module, root


def _release_database(path, *, error=None, extra_value=None):
    connection = sqlite3.connect(path)
    try:
        with connection:
            connection.execute(
                "CREATE TABLE import_log (id INTEGER PRIMARY KEY, error TEXT)"
            )
            if error is not None:
                connection.execute("INSERT INTO import_log VALUES (1, ?)", (error,))
            if extra_value is not None:
                connection.execute("CREATE TABLE extra (value TEXT)")
                connection.execute("INSERT INTO extra VALUES (?)", (extra_value,))
    finally:
        connection.close()


def test_candidate_is_passed_to_build_without_replacing_formal(builder, monkeypatch):
    module, root = builder
    formal = root / "data/genshin.db"
    formal.parent.mkdir()
    formal.write_bytes(b"formal")
    candidate = root / "candidate/genshin.db"
    candidate.parent.mkdir()
    candidate.write_bytes(b"candidate")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=9)  # No real build or filesystem mutation.

    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "build_dist.py",
            "--database",
            str(candidate),
            "--dist-root",
            "dist/candidate",
            "--new-database-release",
        ],
    )
    assert module.main() == 9
    assert calls[0][1]["env"]["GENSHIN_BUNDLED_DATABASE"] == str(candidate.resolve())
    assert calls[0][1]["env"]["GENSHIN_DATABASE_RELEASE_MANIFEST"].endswith(
        "build-py\\database-release-manifest.json",
    )
    assert calls[0][1]["env"]["GENSHIN_RELEASE_PAIR_MANIFEST"].endswith(
        "build-py\\release-pair-manifest.json",
    )
    assert calls[0][1]["env"]["GENSHIN_NATIVE_AGENT_STAGE"].endswith(
        "build\\native-agent-v2\\pyinstaller-stage",
    )
    assert formal.read_bytes() == b"formal" and candidate.read_bytes() == b"candidate"


@pytest.mark.parametrize("accepted_name", ["database-release-manifest.json", "database-live-manifest.json"])
def test_new_app_pair_can_reuse_unchanged_database_release(builder, monkeypatch, accepted_name):
    """App/Pair identities advance independently from unchanged DB content."""

    module, root = builder
    monkeypatch.setattr(module.app_version, "RELEASE_PAIR_SEQUENCE", 4)
    candidate = root / "candidate/genshin.db"
    candidate.parent.mkdir()
    candidate.write_bytes(b"same accepted database release")
    accepted_path = root / "accepted" / accepted_name
    accepted_path.parent.mkdir()
    accepted_path.write_text("{}", encoding="utf-8")
    predecessor_path = root / "accepted-predecessor" / "release-pair-manifest.json"
    predecessor_path.parent.mkdir()
    predecessor_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        module.app_version,
        "ACCEPTED_PREDECESSOR_PAIR_MANIFEST_SHA256",
        hashlib.sha256(b"{}").hexdigest(),
    )
    captured = {}
    database_manifest = {
        "contract": "database-release-v2",
        "database_release_id": module.app_version.DATABASE_RELEASE_ID,
        "release_sequence": module.app_version.DATABASE_RELEASE_SEQUENCE,
        "channel": "dev",
        "logic_profile": "genshin-calculation-v1",
        "data_contract_version": 1,
        "database": {
            "sha256": "a" * 64,
            "size_bytes": candidate.stat().st_size,
            "meta_fingerprint": "b" * 64,
        },
    }
    predecessor = {
        "contract": "release-pair-v2",
        "pair_sequence": module.app_version.RELEASE_PAIR_SEQUENCE - 1,
        "release_pair_id": "pair-0.46.0-db-7.1.53-r1",
        "channel": "dev",
        "pair_policy": "current_only",
        "app": {"app_release_id": "app-0.46.0"},
    }

    def create_pair(_database_manifest, **kwargs):
        captured["pair"] = kwargs
        current = {
            "contract": "release-pair-v2",
            "pair_sequence": kwargs["pair_sequence"],
            "release_pair_id": kwargs["release_pair_id"],
            "channel": "dev",
            "pair_policy": kwargs["pair_policy"],
            "app": {"app_release_id": kwargs["app_release_id"]},
        }
        captured["current_pair"] = current
        return current

    monkeypatch.setattr(
        module,
        "create_database_release_manifest",
        lambda *_a, **_k: pytest.fail("accepted DB release must not be reissued"),
    )
    monkeypatch.setattr(module, "create_release_pair_manifest", create_pair)
    monkeypatch.setattr(
        module, "load_database_release_manifest", lambda _path: database_manifest
    )
    monkeypatch.setattr(
        module,
        "load_release_pair_manifest",
        lambda path: (
            predecessor
            if Path(path).resolve() == predecessor_path.resolve()
            else captured["current_pair"]
        ),
    )
    monkeypatch.setattr(
        module,
        "read_database_identity",
        lambda _path: {
            "sha256": "a" * 64,
            "size_bytes": candidate.stat().st_size,
            "meta_fingerprint": "b" * 64,
        },
    )
    monkeypatch.setattr(module, "database_matches_manifest_core", lambda *_a: True)
    monkeypatch.setattr(
        module, "release_pair_matches_database_manifest", lambda *_a: True
    )
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=9),
    )
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "build_dist.py",
            "--database",
            str(candidate),
            "--dist-root",
            "dist/candidate",
            "--accepted-database-manifest",
            str(accepted_path),
            "--accepted-predecessor-pair-manifest",
            str(predecessor_path),
        ],
    )

    assert module.main() == 9
    assert captured["pair"]["release_pair_id"] == module.app_version.RELEASE_PAIR_ID
    assert captured["pair"]["pair_sequence"] == module.app_version.RELEASE_PAIR_SEQUENCE
    assert captured["pair"]["app_release_id"] == module.app_version.APP_RELEASE_ID
    assert (root / "build-py/database-release-manifest.json").read_bytes() == accepted_path.read_bytes()


def test_reused_database_build_requires_predecessor_before_database_scan(
    builder,
    monkeypatch,
):
    module, _root = builder
    monkeypatch.setattr(module.app_version, "RELEASE_PAIR_SEQUENCE", 4)
    monkeypatch.setattr(
        module,
        "_validate_database_release_hygiene",
        lambda *_a, **_k: pytest.fail("missing predecessor must fail first"),
    )
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "build_dist.py",
            "--accepted-database-manifest",
            "accepted/database-release-manifest.json",
        ],
    )

    with pytest.raises(SystemExit) as failure:
        module.main()

    assert failure.value.code == 2


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ("sequence", "release_pair_predecessor_not_immediate"),
        ("pair_id", "release_pair_successor_identity_not_unique"),
        ("app_id", "release_pair_successor_identity_not_unique"),
        ("channel", "release_pair_predecessor_channel_mismatch"),
        ("policy", "release_pair_predecessor_policy_mismatch"),
        ("database", "release_pair_predecessor_database_mismatch"),
    ],
)
def test_accepted_predecessor_must_be_immediate_unique_and_compatible(
    builder,
    monkeypatch,
    change,
    reason,
):
    module, root = builder
    predecessor_path = root / "accepted" / "release-pair-manifest.json"
    predecessor_path.parent.mkdir()
    predecessor_path.write_text("{}", encoding="utf-8")
    predecessor = {
        "pair_sequence": 3,
        "release_pair_id": "pair-old",
        "channel": "dev",
        "pair_policy": "current_only",
        "app": {"app_release_id": "app-old"},
    }
    current = {
        "pair_sequence": 4,
        "release_pair_id": "pair-new",
        "channel": "dev",
        "pair_policy": "current_only",
        "app": {"app_release_id": "app-new"},
    }
    if change == "sequence":
        predecessor["pair_sequence"] = 2
    elif change == "pair_id":
        predecessor["release_pair_id"] = current["release_pair_id"]
    elif change == "app_id":
        predecessor["app"]["app_release_id"] = current["app"]["app_release_id"]
    elif change == "channel":
        predecessor["channel"] = "stable"
    elif change == "policy":
        predecessor["pair_policy"] = "exact"
    monkeypatch.setattr(
        module, "load_release_pair_manifest", lambda _path: predecessor
    )
    monkeypatch.setattr(
        module,
        "release_pair_matches_database_manifest",
        lambda *_a: change != "database",
    )

    with pytest.raises(module.DatabaseReleaseError) as failure:
        module._load_accepted_predecessor_pair(
            predecessor_path,
            workspace_root=root,
            build_root=root / "build-py",
            dist_root=root / "dist/target",
            pair_manifest=current,
            database_manifest={"database_release_id": "db-current"},
            expected_sha256=hashlib.sha256(b"{}").hexdigest(),
        )

    assert failure.value.reason == reason


def test_accepted_predecessor_bytes_are_pinned(builder, monkeypatch):
    module, root = builder
    predecessor_path = root / "accepted" / "release-pair-manifest.json"
    predecessor_path.parent.mkdir()
    predecessor_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        module,
        "load_release_pair_manifest",
        lambda _path: pytest.fail("hash mismatch must fail before parsing"),
    )

    with pytest.raises(module.DatabaseReleaseError) as failure:
        module._load_accepted_predecessor_pair(
            predecessor_path,
            workspace_root=root,
            build_root=root / "build-py",
            dist_root=root / "dist/target",
            pair_manifest={"pair_sequence": 4},
            database_manifest={},
            expected_sha256="0" * 64,
        )

    assert failure.value.reason == (
        "accepted_predecessor_pair_manifest_sha256_mismatch"
    )


def test_reused_database_manifest_must_match_pinned_release_and_candidate(
    builder, monkeypatch
):
    module, root = builder
    candidate = root / "candidate/genshin.db"
    candidate.parent.mkdir()
    candidate.write_bytes(b"candidate")
    accepted_path = root / "accepted/database-release-manifest.json"
    accepted_path.parent.mkdir()
    accepted_path.write_text("{}", encoding="utf-8")
    accepted = {
        "database_release_id": "db-7.1.53-r1",
        "release_sequence": 2,
        "channel": "dev",
        "logic_profile": "genshin-calculation-v1",
        "data_contract_version": 1,
        "database": {
            "sha256": "a" * 64,
            "size_bytes": candidate.stat().st_size,
            "meta_fingerprint": "b" * 64,
        },
    }
    monkeypatch.setattr(module, "load_database_release_manifest", lambda _path: accepted)
    monkeypatch.setattr(
        module,
        "read_database_identity",
        lambda _path: {
            "sha256": "c" * 64,
            "size_bytes": candidate.stat().st_size,
            "meta_fingerprint": "b" * 64,
        },
    )
    monkeypatch.setattr(module, "database_matches_manifest_core", lambda *_a: True)
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_a, **_k: pytest.fail("mismatched accepted DB must not build"),
    )
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "build_dist.py",
            "--database",
            str(candidate),
            "--accepted-database-manifest",
            str(accepted_path),
        ],
    )

    with pytest.raises(SystemExit) as failure:
        module.main()
    assert failure.value.code == 2


@pytest.mark.parametrize(
    "extra",
    [
        [],
        [
            "--accepted-database-manifest",
            "accepted/database-release-manifest.json",
            "--new-database-release",
        ],
    ],
)
def test_database_release_source_mode_must_be_explicit_and_exclusive(
    builder, monkeypatch, extra
):
    module, _root = builder
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_a, **_k: pytest.fail("ambiguous DB release source must not build"),
    )
    monkeypatch.setattr(module.sys, "argv", ["build_dist.py", *extra])

    with pytest.raises(SystemExit) as failure:
        module.main()

    assert failure.value.code == 2


@pytest.mark.parametrize("value", ["../genshin.db", "candidate/other.db", "missing/genshin.db",
                                  "dist/GenshinCalc/_internal/bundled/genshin.db", "build-py/genshin.db"])
def test_invalid_database_rejected_before_pyinstaller(builder, monkeypatch, value):
    module, root = builder
    if not value.startswith("missing"):
        path = root / value
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not a publication")
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: pytest.fail("must validate before build"))
    monkeypatch.setattr(module.sys, "argv", ["build_dist.py", "--database", value])
    with pytest.raises(SystemExit) as failure:
        module.main()
    assert failure.value.code == 2


def test_release_identity_cli_override_is_rejected_before_database_or_build(
    builder, monkeypatch
):
    module, _root = builder
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_a, **_k: pytest.fail("mismatched release pins must not build"),
    )
    monkeypatch.setattr(
        module.sys,
        "argv",
        ["build_dist.py", "--database-release-id", "db-9.9.9-r1"],
    )
    with pytest.raises(SystemExit) as failure:
        module.main()
    assert failure.value.code == 2


@pytest.mark.parametrize("directory", ["", "数据库 #验收%25"])
def test_database_release_hygiene_accepts_vacuumed_path_free_database(builder, directory):
    module, root = builder
    database = root / directory / "clean.db"
    database.parent.mkdir(exist_ok=True)
    _release_database(database, error="[WinError 5] active database replacement denied")
    module._real_validate_database_release_hygiene(database, workspace_root=root)


@pytest.mark.parametrize("location", ["import_log", "other_table"])
def test_database_release_hygiene_rejects_workspace_path_bytes(builder, location):
    module, root = builder
    database = root / f"leaked-{location}.db"
    leaked = str(root / "data" / "genshin.db")
    _release_database(
        database,
        error=f"failed to replace '{leaked}'" if location == "import_log" else None,
        extra_value=leaked if location == "other_table" else None,
    )
    with pytest.raises(ValueError, match="database_release_.*local_path|workspace_path"):
        module._real_validate_database_release_hygiene(database, workspace_root=root)


def test_database_release_hygiene_rejects_free_pages(builder):
    module, root = builder
    database = root / "free-pages.db"
    _release_database(database)
    connection = sqlite3.connect(database)
    try:
        with connection:
            connection.execute("CREATE TABLE discarded (value BLOB)")
            connection.executemany(
                "INSERT INTO discarded VALUES (?)",
                [(b"x" * 8192,) for _index in range(32)],
            )
            connection.execute("DELETE FROM discarded")
        assert connection.execute("PRAGMA freelist_count").fetchone()[0] > 0
    finally:
        connection.close()
    with pytest.raises(ValueError, match="database_release_freelist_not_empty"):
        module._real_validate_database_release_hygiene(database, workspace_root=root)


def test_native_version_blocker_rejects_before_staging_or_pyinstaller(
    builder,
    monkeypatch,
):
    module, root = builder
    database = root / "data/genshin.db"
    database.parent.mkdir()
    database.write_bytes(b"formal")
    blocked = SimpleNamespace(
        public=lambda: {
            "state": "blocked",
            "release_blockers": ["native_release_app_version_mismatch"],
        },
        release_ready=False,
        release_blockers=("native_release_app_version_mismatch",),
    )
    monkeypatch.setattr(
        module,
        "create_native_agent_build_plan",
        lambda **_kwargs: blocked,
    )
    monkeypatch.setattr(
        module,
        "prepare_native_agent_stage",
        lambda *_a, **_k: pytest.fail("blocked plan must not enter staging"),
    )
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_a, **_k: pytest.fail("blocked plan must not run PyInstaller"),
    )
    monkeypatch.setattr(
        module.sys,
        "argv",
        ["build_dist.py", "--new-database-release"],
    )

    with pytest.raises(SystemExit) as failure:
        module.main()
    assert failure.value.code == 2


def test_supply_chain_failure_cleans_stage_and_rejects_before_pyinstaller(
    builder,
    monkeypatch,
):
    module, root = builder
    database = root / "data/genshin.db"
    database.parent.mkdir()
    database.write_bytes(b"formal")
    cleanups = []

    def fail_supply_chain(**_kwargs):
        raise module.SupplyChainError("supply_chain_fixture_failure")

    monkeypatch.setattr(module, "generate_supply_chain_materials", fail_supply_chain)
    monkeypatch.setattr(
        module,
        "cleanup_native_agent_stage",
        lambda *args, **kwargs: cleanups.append((args, kwargs)),
    )
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_a, **_k: pytest.fail("failed supply-chain gate must not build"),
    )
    monkeypatch.setattr(
        module.sys,
        "argv",
        ["build_dist.py", "--new-database-release"],
    )

    with pytest.raises(SystemExit) as failure:
        module.main()

    assert failure.value.code == 2
    assert len(cleanups) == 1
    assert cleanups[0][0][0] == root / "build/native-agent-v2/pyinstaller-stage"


def test_inno_inputs_follow_the_exact_versioned_dist_root(builder):
    module, root = builder
    dist_root = root / "dist/release-0.45.0-r1"
    dist = dist_root / "GenshinCalc"
    updater_root = dist_root / "GenshinCalcUpdater"
    updater_root.mkdir(parents=True)
    updater_exe = updater_root / "GenshinCalcUpdater.exe"
    updater_exe.write_bytes(b"reviewed updater fixture")
    updater_sha256 = hashlib.sha256(updater_exe.read_bytes()).hexdigest()
    updater_receipt = updater_root / "updater-artifact.json"
    updater_receipt.write_text(
        json.dumps(
            {
                "contract": module.UPDATER_ARTIFACT_CONTRACT,
                "schema_version": 1,
                "build_provenance": {
                    "app": {
                        "version": module.app_version.APP_VERSION,
                        "release_id": module.app_version.APP_RELEASE_ID,
                        "release_pair_id": module.app_version.RELEASE_PAIR_ID,
                    }
                },
                "updater": {
                    "filename": updater_exe.name,
                    "helper_contract": module.HELPER_RESULT_CONTRACT,
                    "request_schema_version": module.UPDATER_REQUEST_SCHEMA_VERSION,
                    "sha256": updater_sha256,
                    "size_bytes": updater_exe.stat().st_size,
                },
            }
        ),
        encoding="utf-8",
    )

    module._write_inno_build_inputs(
        dist_root=dist_root,
        dist=dist,
        updater_exe=updater_exe,
        updater_receipt=updater_receipt,
        updater_sha256=updater_sha256,
        version="0.45.0",
    )

    assert (root / "build-py/app-version.iss").read_text(encoding="utf-8") == (
        '#define AppVersion "0.45.0"\n'
    )
    assert (root / "build-py/app-layout.iss").read_text(encoding="utf-8") == (
        '#define AppSourceDir "dist\\release-0.45.0-r1\\GenshinCalc"\n'
        '#define InstallerOutputDir "dist\\release-0.45.0-r1"\n'
        '#define UpdaterSourceFile "dist\\release-0.45.0-r1\\GenshinCalcUpdater\\GenshinCalcUpdater.exe"\n'
        '#define UpdaterReceiptFile "dist\\release-0.45.0-r1\\GenshinCalcUpdater\\updater-artifact.json"\n'
        f'#define UpdaterSHA256 "{updater_sha256}"\n'
    )
    installer = (Path(__file__).resolve().parents[1] / "installer.iss").read_text(
        encoding="utf-8",
    )
    assert '#include "build-py\\app-layout.iss"' in installer
    assert 'Source: "{#AppSourceDir}\\*"' in installer
    assert "OutputDir={#InstallerOutputDir}" in installer
    assert 'Source: "{#UpdaterSourceFile}"' in installer
    assert 'Source: "{#UpdaterReceiptFile}"' in installer
    assert 'DestDir: "{#UpdaterInstallDir}"' in installer
    assert '#define UpdaterInstallDir "{commonappdata}\\GenshinCalc\\Updater"' in installer
    assert 'DestDir: "{app}"; DestName: "GenshinCalcUpdater.exe"' not in installer
    assert 'Source: "dist\\GenshinCalc\\*"' not in installer
    assert 'Root: HKLM; Subkey: "Software\\GenshinCalc"' in installer
    assert 'ValueName: "InstallLocation"; ValueData: "{app}"' in installer
    assert '#define UninstallerInstallDir "{commonappdata}\\GenshinCalc\\Uninstall"' in installer
    assert "UninstallFilesDir={#UninstallerInstallDir}" in installer
    assert 'Name: "{app}\\unins*.exe"' in installer
    assert "function PrepareToInstall" in installer
    assert "db activate-bundled --consent-source installer" in installer
    assert "ExecAsOriginalUser" in installer
    assert "Check: DatabaseActivationSucceeded" in installer
    assert "DatabaseActivationApproved" not in installer
    assert "if CurStep <> ssPostInstall then" in installer


def test_updater_build_uses_onefile_sibling_and_writes_hash_receipt(
    builder, monkeypatch
):
    module, root = builder
    allowed_root = root / "dist"
    dist_root = allowed_root / "release-0.45.0-r1"
    main_dist = dist_root / "GenshinCalc"
    main_dist.mkdir(parents=True)
    (main_dist / "keep.txt").write_text("main candidate", encoding="utf-8")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        output = Path(command[command.index("--distpath") + 1])
        output.mkdir(parents=True)
        (output / module.UPDATER_EXE_FILENAME).write_bytes(b"frozen updater")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", run)
    code, executable, receipt_path, receipt = module._build_updater_artifact(
        dist_root=dist_root,
        allowed_root=allowed_root,
    )

    assert code == 0
    assert calls[0][0][3] == "genshin_updater.spec"
    assert calls[0][0][calls[0][0].index("--distpath") + 1] == str(
        dist_root / module.UPDATER_ARTIFACT_DIRECTORY
    )
    assert executable == (
        dist_root / module.UPDATER_ARTIFACT_DIRECTORY / module.UPDATER_EXE_FILENAME
    )
    assert receipt_path == executable.parent / module.UPDATER_RECEIPT_FILENAME
    assert receipt == json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["updater"]["sha256"] == hashlib.sha256(
        executable.read_bytes()
    ).hexdigest()
    assert receipt["updater"]["helper_contract"] == (
        "genshin-release-pair-helper-result-v1"
    )
    assert receipt["updater"]["request_schema_version"] == 1
    assert receipt["updater"]["size_bytes"] == executable.stat().st_size
    assert (main_dist / "keep.txt").read_text(encoding="utf-8") == "main candidate"


@pytest.mark.parametrize("returncode,create_output", [(8, True), (0, False)])
def test_updater_build_failure_cleans_only_updater_candidate(
    builder, monkeypatch, returncode, create_output
):
    module, root = builder
    allowed_root = root / "dist"
    dist_root = allowed_root / "release-0.45.0-r1"
    main_dist = dist_root / "GenshinCalc"
    main_dist.mkdir(parents=True)
    (main_dist / "keep.txt").write_text("main candidate", encoding="utf-8")

    def run(command, **_kwargs):
        output = Path(command[command.index("--distpath") + 1])
        if create_output:
            output.mkdir(parents=True)
            (output / "partial.tmp").write_bytes(b"partial")
        return SimpleNamespace(returncode=returncode)

    monkeypatch.setattr(module.subprocess, "run", run)
    code, executable, receipt_path, receipt = module._build_updater_artifact(
        dist_root=dist_root,
        allowed_root=allowed_root,
    )

    assert code == (returncode or 1)
    assert executable is None and receipt_path is None and receipt is None
    assert not (dist_root / module.UPDATER_ARTIFACT_DIRECTORY).exists()
    assert (main_dist / "keep.txt").read_text(encoding="utf-8") == "main candidate"


def test_updater_entry_and_spec_define_a_separate_onefile_executable():
    root = Path(__file__).resolve().parents[1]
    entry = (root / "updater_entry.py").read_text(encoding="utf-8")
    spec = (root / "genshin_updater.spec").read_text(encoding="utf-8")

    assert "from genshin_app.release_pair_helper import main" in entry
    assert 'name="GenshinCalcUpdater"' in spec
    assert "a.binaries" in spec and "a.datas" in spec
    assert "COLLECT(" not in spec


def test_updater_build_rejects_output_outside_controlled_dist(builder, monkeypatch):
    module, root = builder
    allowed_root = root / "dist"
    allowed_root.mkdir()
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_a, **_k: pytest.fail("escaped output must fail before PyInstaller"),
    )

    with pytest.raises(ValueError):
        module._build_updater_artifact(
            dist_root=root / "outside",
            allowed_root=allowed_root,
        )
