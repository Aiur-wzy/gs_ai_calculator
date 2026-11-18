"""Static release admission without executing PyInstaller or App entrypoints."""
from __future__ import annotations

import ast
import hashlib
import json
import marshal
from pathlib import Path
from types import SimpleNamespace

import pytest

from genshin_app import version
from genshin_db.release_profile import (
    create_release_pair_manifest,
    load_database_release_manifest,
)
from genshin_engine.runtime_dependencies import (
    FROZEN_MANIFEST,
    engine_runtime_dependency_descriptor,
    engine_runtime_module_closure,
)
from scripts import build_dist, engine_runtime_release
from scripts.app_runtime_release import (
    APP_RUNTIME_ROOTS,
    NON_PORTABLE_RELEASE_ENTRIES,
    app_runtime_module_closure,
)

ROOT = Path(__file__).resolve().parents[1]


def _current_archive(pyz):
    entry = compile((ROOT / "app_entry.py").read_text("utf-8-sig"), "old/build/path/app_entry.py", "exec", dont_inherit=True)
    return SimpleNamespace(toc={"PYZ.pyz": (0, 0, 0, 0, "z"), "app_entry": (0, 0, 0, 0, "s")},
                           open_embedded_archive=lambda _: pyz, extract=lambda _: marshal.dumps(entry))


def test_actual_entry_closure_contains_new_public_paths_without_collect_all():
    closure = app_runtime_module_closure(ROOT / "src")
    required = {
        "genshin_app.input_editing", "genshin_app.native_input_projection",
        "genshin_app.native_input_snapshot", "genshin_app.calculation_qualification",
        "genshin_app.calculation_assistance", "genshin_app.energy_trials",
        "genshin_app.agent_chat.native_cli_link", "genshin_app.gui.frontend_document_projection",
        "genshin_app.agent_chat.native_runtime_command",
        "genshin_app.agent_chat.calculation_coverage",
        "genshin_app.calculation_coverage",
        "genshin_app.frozen_selfcheck_frontend",
    }
    assert required <= set(closure)
    assert set(APP_RUNTIME_ROOTS) <= set(closure)
    assert all(path.is_file() for path in closure.values())
    # The collector parses definitions; it cannot execute imported module code.
    tree = ast.parse((ROOT / "scripts/app_runtime_release.py").read_text(encoding="utf-8"))
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                   and node.func.attr == "import_module" for node in ast.walk(tree))


def test_retained_coverage_import_has_exact_shared_owner_without_duplicate_logic():
    from genshin_app import calculation_coverage as owner
    from genshin_app.agent_chat import calculation_coverage as compatibility

    assert compatibility.qualify_calculation_coverage is owner.qualify_calculation_coverage
    assert compatibility.build_automatic_coverage_receipt is owner.build_automatic_coverage_receipt
    assert compatibility.qualify_calculation_coverage.__module__ == "genshin_app.calculation_coverage"
    tree = ast.parse((ROOT / "src/genshin_app/agent_chat/calculation_coverage.py").read_text("utf8"))
    assert len(tree.body) == 2
    assert isinstance(tree.body[1], ast.ImportFrom)
    assert tree.body[1].module == "genshin_app.calculation_coverage"
    assert [alias.name for alias in tree.body[1].names] == ["*"]


@pytest.mark.parametrize("entry", ["GenshinCalc.exe", "GenshinCalcGUI.exe"])
def test_both_archive_entries_must_include_retained_coverage_facade(monkeypatch, tmp_path, entry):
    from genshin_engine.runtime_dependencies import module_source

    app = app_runtime_module_closure(ROOT / "src")
    engine = engine_runtime_module_closure(ROOT / "src")
    inventory = set(app) | set(engine) | engine_runtime_release.changed_project_modules(ROOT / "src")
    missing = "genshin_app.agent_chat.calculation_coverage"

    def archive(path):
        names = inventory - {missing} if Path(path).name == entry else inventory
        pyz = SimpleNamespace(toc={name: () for name in names}, extract=lambda name: compile(
            module_source(ROOT / "src", name).read_text("utf-8-sig"), name, "exec", dont_inherit=True))
        return _current_archive(pyz)

    monkeypatch.setattr("PyInstaller.archive.readers.CArchiveReader", archive)
    for name in ("GenshinCalc.exe", "GenshinCalcGUI.exe"):
        (tmp_path / name).write_bytes(b"inert archive fixture; never execute")
    with pytest.raises(engine_runtime_release.EngineRuntimePackageError) as failure:
        engine_runtime_release.validate_engine_package_archive(tmp_path)
    assert failure.value.reason == "app_runtime_dependency_missing"
    assert failure.value.details == {"entry": entry, "modules": [missing]}


def test_changed_source_guard_still_rejects_undeclared_unreachable_changed_module(monkeypatch, tmp_path):
    app = app_runtime_module_closure(ROOT / "src")
    engine = engine_runtime_module_closure(ROOT / "src")
    missing = "genshin_app.unreachable_changed_fixture"
    inventory = {name: () for name in set(app) | set(engine)}
    archive = SimpleNamespace(toc={"PYZ.pyz": (0, 0, 0, 0, "z")},
        open_embedded_archive=lambda _: SimpleNamespace(toc=inventory))
    monkeypatch.setattr("PyInstaller.archive.readers.CArchiveReader", lambda _: archive)
    monkeypatch.setattr(engine_runtime_release, "changed_project_modules", lambda _: {missing})
    (tmp_path / "GenshinCalc.exe").write_bytes(b"inert archive fixture; never execute")
    with pytest.raises(engine_runtime_release.EngineRuntimePackageError) as failure:
        engine_runtime_release.validate_engine_package_archive(tmp_path)
    assert failure.value.reason == "engine_changed_source_missing"
    assert failure.value.details == {"entry": "GenshinCalc.exe", "modules": [missing]}


def test_portable_archive_uses_actual_entry_closure_with_explicit_release_only_owners(monkeypatch, tmp_path):
    from genshin_engine.runtime_dependencies import CONTRACT, module_source

    source = ROOT / "src"
    app = app_runtime_module_closure(source)
    engine = engine_runtime_module_closure(source)
    inventory = set(app) | set(engine)
    actual_changed = engine_runtime_release.changed_project_modules(source)
    assert NON_PORTABLE_RELEASE_ENTRIES.isdisjoint(inventory)
    assert all(module_source(source, name).is_file() for name in NON_PORTABLE_RELEASE_ENTRIES)
    assert "genshin_app.release_pair_coordinator" in app
    assert "genshin_app.release_pair_helper" in app
    assert "genshin_app.product_release_index" in app

    # Simulate these new source files in Git even when running from a clean
    # checkout. The archive inventory remains precisely the real closure.
    unknown = "genshin_app.new_unreachable_runtime_module"
    changed = (actual_changed & inventory) | NON_PORTABLE_RELEASE_ENTRIES
    monkeypatch.setattr(engine_runtime_release, "changed_project_modules", lambda _: changed)
    pyz = SimpleNamespace(toc={name: () for name in inventory}, extract=lambda name: compile(
        module_source(source, name).read_text("utf-8-sig"), name, "exec", dont_inherit=True))
    archive = _current_archive(pyz)
    monkeypatch.setattr("PyInstaller.archive.readers.CArchiveReader", lambda _: archive)
    for name in ("GenshinCalc.exe", "GenshinCalcGUI.exe"):
        (tmp_path / name).write_bytes(b"inert archive fixture; never execute")
    internal = tmp_path / "_internal"
    manifest = internal / "genshin_engine" / FROZEN_MANIFEST
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"contract": CONTRACT, "modules": sorted(engine)}), encoding="utf-8")
    descriptor = engine_runtime_dependency_descriptor()
    for relative in descriptor["package_data"]:
        target = internal / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((source / relative).read_bytes())
    for extension in descriptor["native_extensions"]:
        if extension["platform"] == engine_runtime_release.sys.platform:
            suffix = ".pyd" if extension["platform"] == "win32" else ".so"
            (internal / (extension["module"] + suffix)).write_bytes(b"inert extension fixture")

    result = engine_runtime_release.validate_engine_package_archive(tmp_path, source_root=source)
    assert all(entry["semantic_source_modules"] == len(inventory) for entry in result["entries"])
    changed.add(unknown)
    with pytest.raises(engine_runtime_release.EngineRuntimePackageError) as failure:
        engine_runtime_release.validate_engine_package_archive(tmp_path, source_root=source)
    assert failure.value.reason == "engine_changed_source_missing"
    assert failure.value.details["modules"] == [unknown]


def test_lazily_imported_transitive_modules_follow_actual_graph(tmp_path):
    package = tmp_path / "genshin_app"
    package.mkdir()
    (package / "__init__.py").write_text("raise RuntimeError('must not run')")
    (package / "entry.py").write_text("def run():\n from .child import value\n")
    (package / "child.py").write_text("from .leaf import answer\nvalue=answer\n")
    (package / "leaf.py").write_text("answer=42\n")
    (package / "unreachable.py").write_text("pass\n")
    closure = app_runtime_module_closure(tmp_path, roots=("genshin_app.entry",))
    assert set(closure) == {"genshin_app", "genshin_app.entry", "genshin_app.child", "genshin_app.leaf"}
    with pytest.raises(ValueError, match="app_runtime_source_missing"):
        app_runtime_module_closure(tmp_path, roots=("genshin_app.absent",))


def test_archive_guard_checks_app_paths_after_engine_closure(monkeypatch, tmp_path):
    engine = engine_runtime_module_closure(ROOT / "src")
    app = app_runtime_module_closure(ROOT / "src")
    missing = "genshin_app.agent_chat.native_cli_link"
    inventory = {name: () for name in set(engine) | set(app) if name != missing}
    archive = SimpleNamespace(toc={"PYZ.pyz": (0, 0, 0, 0, "z")},
                              open_embedded_archive=lambda _: SimpleNamespace(toc=inventory))
    monkeypatch.setattr("PyInstaller.archive.readers.CArchiveReader", lambda _: archive)
    (tmp_path / "GenshinCalc.exe").write_bytes(b"unexecuted fixture archive")
    with pytest.raises(engine_runtime_release.EngineRuntimePackageError) as failure:
        engine_runtime_release.validate_engine_package_archive(tmp_path)
    assert failure.value.reason == "app_runtime_dependency_missing"
    assert missing in failure.value.details["modules"]


def _current_predecessor_path():
    public_input = ROOT / "release-inputs/1.0.1/release-pair-manifest.json"
    if public_input.is_file():
        return public_input
    return ROOT / "docs/publication/public-main" / public_input.relative_to(ROOT)


def test_current_identity_reuses_database_and_pins_exact_accepted_1_0_1_predecessor():
    raw = _current_predecessor_path().read_bytes()
    prior = json.loads(raw)
    assert hashlib.sha256(raw).hexdigest() == version.ACCEPTED_PREDECESSOR_PAIR_MANIFEST_SHA256
    assert prior["pair_sequence"] + 1 == version.RELEASE_PAIR_SEQUENCE
    assert prior["database"]["database_release_id"] == version.DATABASE_RELEASE_ID == "db-7.2.50-r6"
    assert version.APP_VERSION == "1.0.2"
    assert version.APP_RELEASE_ID == "app-1.0.2"
    assert version.RELEASE_PAIR_ID == "pair-1.0.2-db-7.2.50-r6"
    assert version.NATIVE_AGENT_PACKAGE_MANAGER == "pnpm@11.25.0"
    for relative in ("agent-runtime/package.json", "frontend/package.json"):
        assert json.loads((ROOT / relative).read_text(encoding="utf-8"))["packageManager"] == version.NATIVE_AGENT_PACKAGE_MANAGER


def test_historical_r17_identity_reused_database_and_pinned_accepted_r16_predecessor():
    release_inputs = ROOT / "docs/pm/audits/2026-11-14-r18-release-inputs"
    pair_file = ROOT / "docs/pm/audits/2026-11-13-r17-predecessor/release-pair-manifest.json"
    prior = json.loads(pair_file.read_bytes())
    historical_r17 = json.loads((release_inputs / "release-pair-manifest.json").read_bytes())
    assert hashlib.sha256(pair_file.read_bytes()).hexdigest() == (
        "38cb69aa8a6075a664ac1bc1a9fa7cc4311aae960ac769b667ec285fbf3bacff"
    )
    assert prior["pair_sequence"] + 1 == historical_r17["pair_sequence"]
    assert prior["database"]["database_release_id"] == "db-7.2.50-r4"
    assert historical_r17["app"]["app_release_id"] == "app-0.46.0-r17"
    assert historical_r17["database"] == prior["database"]


def test_real_build_predecessor_admission_accepts_1_0_1_for_1_0_2_same_database():
    directory = ROOT / "tests/fixtures/accepted-r20"
    database = load_database_release_manifest(directory / "database-release-manifest.json")
    # Only a prospective in-memory App artifact contract; no final package
    # manifest or runtime pin is published by this structure test.
    pair = create_release_pair_manifest(database, release_pair_id=version.RELEASE_PAIR_ID,
        pair_sequence=version.RELEASE_PAIR_SEQUENCE, pair_policy="exact",
        app_release_id=version.APP_RELEASE_ID, app_version=version.APP_VERSION,
        app_artifact_manifest_sha256="a" * 64, released_at="2026-11-14T00:00:00+00:00")
    predecessor = _current_predecessor_path()
    prior = build_dist._load_accepted_predecessor_pair(predecessor,
        workspace_root=ROOT, build_root=ROOT / "build", dist_root=ROOT / "dist/release-1.0.2",
        pair_manifest=pair, database_manifest=database,
        expected_sha256=version.ACCEPTED_PREDECESSOR_PAIR_MANIFEST_SHA256, allow_database_upgrade=False)
    assert prior["pair_sequence"] == 5
    assert pair["pair_sequence"] == 6
    assert pair["database"]["database_release_id"] == database["database_release_id"]
    assert prior["database"] == pair["database"]
    assert database["release_sequence"] == version.DATABASE_RELEASE_SEQUENCE == 8


def test_historical_r17_to_r18_database_upgrade_remains_supported():
    directory = ROOT / "docs/pm/audits/2026-11-14-r18-release-inputs"
    database = load_database_release_manifest(directory / "r5-database-release-manifest.json")
    predecessor = directory / "release-pair-manifest.json"
    pair = create_release_pair_manifest(database, release_pair_id="pair-0.46.0-r18-db-7.2.50-r5",
        pair_sequence=16, pair_policy="current_only", app_release_id="app-0.46.0-r18", app_version="0.46.0",
        app_artifact_manifest_sha256="a" * 64, released_at="2026-11-14T00:00:00+00:00")
    prior = build_dist._load_accepted_predecessor_pair(predecessor,
        workspace_root=ROOT, build_root=ROOT / "build", dist_root=ROOT / "dist/historical-r18",
        pair_manifest=pair, database_manifest=database,
        expected_sha256=hashlib.sha256(predecessor.read_bytes()).hexdigest(), allow_database_upgrade=True)
    assert prior["pair_sequence"] == 15
    assert prior["database"]["database_release_id"] == "db-7.2.50-r4"
    assert database["release_sequence"] == 7
