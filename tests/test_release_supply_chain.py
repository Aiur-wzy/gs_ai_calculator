from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from genshin_app import package_integrity

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "release_supply_chain.py"
_SPEC = importlib.util.spec_from_file_location("release_supply_chain_under_test", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
supply = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = supply
_SPEC.loader.exec_module(supply)


@pytest.mark.parametrize("explicit", [False, True])
def test_standalone_cli_manifest_uses_current_release_owner(tmp_path, monkeypatch, capsys, explicit):
    from scripts.native_agent_release import RUNTIME_MANIFEST
    captured = {}
    def generate(**kwargs):
        captured.update(kwargs)
        return {"state": "engineering-no-io"}
    monkeypatch.setattr(supply, "generate_supply_chain_materials", generate)
    runtime = tmp_path / "chosen-runtime"
    expected = runtime / RUNTIME_MANIFEST
    args = ["--output", str(tmp_path / "out"), "--app-version", "test", "--runtime-root", str(runtime),
            "--node", str(tmp_path / "node.exe"), "--node-version", "24.19.0"]
    if explicit:
        args += ["--runtime-manifest", str(expected)]
    assert supply.main(args) == 0
    assert captured["runtime_manifest"] == expected
    assert "engineering-no-io" in capsys.readouterr().out


def _package(
    store: Path,
    virtual: str,
    name: str,
    version: str,
    *,
    license_name: str | None,
    license_text: str | None,
) -> Path:
    base = store / virtual / "node_modules"
    if name.startswith("@"):
        scope, leaf = name.split("/", 1)
        base = base / scope / leaf
    else:
        base = base / name
    base.mkdir(parents=True)
    metadata = {"name": name, "version": version}
    if license_name is not None:
        metadata["license"] = license_name
    (base / "package.json").write_text(
        json.dumps(metadata, sort_keys=True),
        encoding="utf-8",
    )
    if license_text is not None:
        (base / "LICENSE").write_text(license_text, encoding="utf-8")
    return base


def _physical_package(
    path: Path,
    name: str,
    version: str,
    *,
    license_name: str | None,
    license_text: str | None,
) -> Path:
    path.mkdir(parents=True)
    metadata = {"name": name, "version": version}
    if license_name is not None:
        metadata["license"] = license_name
    (path / "package.json").write_text(
        json.dumps(metadata, sort_keys=True),
        encoding="utf-8",
    )
    if license_text is not None:
        (path / "LICENSE").write_text(license_text, encoding="utf-8")
    return path


def _database(path: Path, commit: str = "a" * 40) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        connection.execute(
            "INSERT INTO meta(key, value) VALUES ('raw_upstream_commit', ?)",
            (commit,),
        )


def _lock(path: Path, *identities: tuple[str, str]) -> Path:
    lines = [
        "lockfileVersion: '9.0'",
        "",
        "importers:",
        "",
        "  .:",
        "    dependencies:",
    ]
    for name, version in identities:
        lines.extend(
            [
                f"      '{name}':",
                f"        specifier: {version}",
                f"        version: {version}",
            ],
        )
    lines.extend(["", "packages:", ""])
    lines.extend(f"  '{name}@{version}': {{}}" for name, version in identities)
    lines.extend(["", "snapshots:", ""])
    lines.extend(f"  '{name}@{version}': {{}}" for name, version in identities)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _hoisted_state(
    runtime: Path,
    lock: Path,
    locations: dict[str, list[str]],
) -> None:
    node_modules = runtime / "node_modules"
    store = node_modules / ".pnpm"
    store.mkdir(parents=True, exist_ok=True)
    (store / "lock.yaml").write_bytes(lock.read_bytes())
    (runtime / "package.json").write_text(
        json.dumps({"packageManager": "pnpm@11.28.5"}),
        encoding="utf-8",
    )
    (node_modules / ".modules.yaml").write_text(
        json.dumps(
            {
                "nodeLinker": "hoisted",
                "packageManager": "pnpm@11.28.5",
                "included": {
                    "dependencies": True,
                    "devDependencies": False,
                    "optionalDependencies": True,
                },
                "hoistedLocations": locations,
            },
        ),
        encoding="utf-8",
    )


def test_pnpm_inventory_deduplicates_peer_variants_and_records_missing_text(tmp_path):
    runtime = tmp_path / "agent-runtime"
    node_modules = runtime / "node_modules"
    first = _physical_package(
        node_modules / "@scope" / "tool",
        "@scope/tool",
        "1.2.3",
        license_name="MIT",
        license_text="MIT text\n",
    )
    _physical_package(
        node_modules / "plain",
        "plain",
        "2.0.0",
        license_name="ISC",
        license_text=None,
    )
    second = _physical_package(
        node_modules / "plain" / "node_modules" / "@scope" / "tool",
        "@scope/tool",
        "1.2.3",
        license_name="MIT",
        license_text="MIT text\n",
    )
    assert first != second

    lock = _lock(
        runtime / "pnpm-lock.yaml",
        ("@scope/tool", "1.2.3"),
        ("plain", "2.0.0"),
    )
    _hoisted_state(
        runtime,
        lock,
        {
            "@scope/tool@1.2.3": [
                "node_modules\\@scope\\tool",
                "node_modules\\plain\\node_modules\\@scope\\tool",
            ],
            "plain@2.0.0": ["node_modules\\plain"],
        },
    )
    records = supply.inventory_pnpm_packages(node_modules, lock)

    assert [(record.name, record.version) for record in records] == [
        ("@scope/tool", "1.2.3"),
        ("plain", "2.0.0"),
    ]
    assert records[0].instance_count == 2
    assert len(records[0].license_texts) == 1
    assert records[0].review_codes == ()
    assert records[1].review_codes == ("license_text_missing",)


def test_pnpm_inventory_rejects_divergent_peer_metadata(tmp_path):
    runtime = tmp_path / "agent-runtime"
    node_modules = runtime / "node_modules"
    _physical_package(
        node_modules / "tool",
        "tool",
        "1.0.0",
        license_name="MIT",
        license_text="one\n",
    )
    package = _physical_package(
        node_modules / "tool" / "node_modules" / "tool",
        "tool",
        "1.0.0",
        license_name="MIT",
        license_text="one\n",
    )
    (package / "package.json").write_text(
        json.dumps({"name": "tool", "version": "1.0.0", "license": "ISC"}),
        encoding="utf-8",
    )

    lock = _lock(runtime / "pnpm-lock.yaml", ("tool", "1.0.0"))
    _hoisted_state(
        runtime,
        lock,
        {
            "tool@1.0.0": [
                "node_modules\\tool",
                "node_modules\\tool\\node_modules\\tool",
            ],
        },
    )
    with pytest.raises(supply.SupplyChainError) as raised:
        supply.inventory_pnpm_packages(node_modules, lock)

    assert raised.value.code == "supply_chain_peer_variant_metadata_mismatch"


def test_pnpm_inventory_accepts_exact_hoisted_layout_and_ignores_fixture_metadata(tmp_path):
    runtime = tmp_path / "agent-runtime"
    node_modules = runtime / "node_modules"
    package = node_modules / "@scope" / "tool"
    package.mkdir(parents=True)
    (package / "package.json").write_text(
        json.dumps({"name": "@scope/tool", "version": "1.2.3", "license": "MIT"}),
        encoding="utf-8",
    )
    (package / "LICENSE").write_text("MIT text\n", encoding="utf-8")
    fixture = package / "templates" / "fixture"
    fixture.mkdir(parents=True)
    (fixture / "package.json").write_text(
        json.dumps({"name": "not-installed", "version": "9.9.9"}),
        encoding="utf-8",
    )
    lock = _lock(runtime / "pnpm-lock.yaml", ("@scope/tool", "1.2.3"))
    _hoisted_state(
        runtime,
        lock,
        {"@scope/tool@1.2.3": ["node_modules\\@scope\\tool"]},
    )

    records = supply.inventory_pnpm_packages(node_modules, lock)

    assert [(record.name, record.version) for record in records] == [
        ("@scope/tool", "1.2.3"),
    ]
    assert records[0].instance_count == 1
    assert records[0].review_codes == ()


def test_pnpm_inventory_rejects_unlisted_hoisted_package_slot(tmp_path):
    runtime = tmp_path / "agent-runtime"
    node_modules = runtime / "node_modules"
    for name in ("tool", "injected"):
        package = node_modules / name
        package.mkdir(parents=True)
        (package / "package.json").write_text(
            json.dumps({"name": name, "version": "1.0.0", "license": "MIT"}),
            encoding="utf-8",
        )
    lock = _lock(runtime / "pnpm-lock.yaml", ("tool", "1.0.0"))
    _hoisted_state(
        runtime,
        lock,
        {"tool@1.0.0": ["node_modules\\tool"]},
    )

    with pytest.raises(supply.SupplyChainError) as raised:
        supply.inventory_pnpm_packages(node_modules, lock)

    assert raised.value.code == "supply_chain_pnpm_lock_installation_mismatch"


def test_pnpm_inventory_explicitly_rejects_isolated_layout(tmp_path):
    runtime = tmp_path / "agent-runtime"
    node_modules = runtime / "node_modules"
    _package(
        node_modules / ".pnpm",
        "tool@1.0.0",
        "tool",
        "1.0.0",
        license_name="MIT",
        license_text="MIT text\n",
    )
    lock = _lock(runtime / "pnpm-lock.yaml", ("tool", "1.0.0"))
    (node_modules / ".pnpm" / "lock.yaml").write_bytes(lock.read_bytes())

    with pytest.raises(supply.SupplyChainError) as raised:
        supply.inventory_pnpm_packages(node_modules, lock)

    assert raised.value.code == "supply_chain_pnpm_hoisted_layout_required"


def test_pnpm_inventory_rejects_missing_locked_production_context(tmp_path):
    runtime = tmp_path / "agent-runtime"
    node_modules = runtime / "node_modules"
    _physical_package(
        node_modules / "tool",
        "tool",
        "1.0.0",
        license_name="MIT",
        license_text="MIT\n",
    )
    lock = _lock(
        runtime / "pnpm-lock.yaml",
        ("tool", "1.0.0"),
        ("required", "2.0.0"),
    )
    _hoisted_state(
        runtime,
        lock,
        {"tool@1.0.0": ["node_modules\\tool"]},
    )

    with pytest.raises(supply.SupplyChainError) as raised:
        supply.inventory_pnpm_packages(node_modules, lock)

    assert raised.value.code == "supply_chain_pnpm_lock_installation_mismatch"


def test_pnpm_inventory_rejects_stale_installed_lock(tmp_path):
    runtime = tmp_path / "agent-runtime"
    node_modules = runtime / "node_modules"
    _physical_package(
        node_modules / "tool",
        "tool",
        "1.0.0",
        license_name="MIT",
        license_text="MIT\n",
    )
    lock = _lock(runtime / "pnpm-lock.yaml", ("tool", "1.0.0"))
    _hoisted_state(
        runtime,
        lock,
        {"tool@1.0.0": ["node_modules\\tool"]},
    )
    (node_modules / ".pnpm" / "lock.yaml").write_text(
        "lockfileVersion: '9.0'\n",
        encoding="utf-8",
    )

    with pytest.raises(supply.SupplyChainError) as raised:
        supply.inventory_pnpm_packages(node_modules, lock)

    assert raised.value.code == "supply_chain_pnpm_installation_lock_mismatch"


def test_pnpm_inventory_rejects_hoisted_location_escape(tmp_path):
    runtime = tmp_path / "agent-runtime"
    node_modules = runtime / "node_modules"
    _physical_package(
        node_modules / "tool",
        "tool",
        "1.0.0",
        license_name="MIT",
        license_text="MIT\n",
    )
    lock = _lock(runtime / "pnpm-lock.yaml", ("tool", "1.0.0"))
    _hoisted_state(
        runtime,
        lock,
        {"tool@1.0.0": ["node_modules\\..\\outside"]},
    )

    with pytest.raises(supply.SupplyChainError) as raised:
        supply.inventory_pnpm_packages(node_modules, lock)

    assert raised.value.code == "supply_chain_pnpm_installation_metadata_invalid"


def test_pnpm_inventory_rejects_reparse_package_slot(tmp_path, monkeypatch):
    runtime = tmp_path / "agent-runtime"
    node_modules = runtime / "node_modules"
    package = _physical_package(
        node_modules / "tool",
        "tool",
        "1.0.0",
        license_name="MIT",
        license_text="MIT\n",
    )
    lock = _lock(runtime / "pnpm-lock.yaml", ("tool", "1.0.0"))
    _hoisted_state(
        runtime,
        lock,
        {"tool@1.0.0": ["node_modules\\tool"]},
    )
    original = supply._is_reparse_point
    monkeypatch.setattr(
        supply,
        "_is_reparse_point",
        lambda path: path == package or original(path),
    )

    with pytest.raises(supply.SupplyChainError) as raised:
        supply.inventory_pnpm_packages(node_modules, lock)

    assert raised.value.code == "supply_chain_dependency_tree_reparse_unsupported"


def test_pnpm_inventory_preserves_and_requires_peer_context(tmp_path):
    runtime = tmp_path / "agent-runtime"
    node_modules = runtime / "node_modules"
    _physical_package(
        node_modules / "tool",
        "tool",
        "1.0.0",
        license_name="MIT",
        license_text="MIT\n",
    )
    _physical_package(
        node_modules / "peer",
        "peer",
        "2.0.0",
        license_name="ISC",
        license_text="ISC\n",
    )
    lock = runtime / "pnpm-lock.yaml"
    lock.write_text(
        """lockfileVersion: '9.0'

importers:

  .:
    dependencies:
      tool:
        specifier: 1.0.0
        version: 1.0.0(peer@2.0.0)

packages:

  'tool@1.0.0': {}

  'peer@2.0.0': {}

snapshots:

  'tool@1.0.0(peer@2.0.0)':
    dependencies:
      peer: 2.0.0

  'peer@2.0.0': {}
""",
        encoding="utf-8",
    )
    _hoisted_state(
        runtime,
        lock,
        {
            "tool@1.0.0(peer@2.0.0)": ["node_modules\\tool"],
            "peer@2.0.0": ["node_modules\\peer"],
        },
    )

    records = supply.inventory_pnpm_packages(node_modules, lock)

    assert {(record.name, record.version) for record in records} == {
        ("tool", "1.0.0"),
        ("peer", "2.0.0"),
    }


def test_pnpm_inventory_rejects_physical_version_drift(tmp_path):
    runtime = tmp_path / "agent-runtime"
    node_modules = runtime / "node_modules"
    _physical_package(
        node_modules / "tool",
        "tool",
        "1.0.1",
        license_name="MIT",
        license_text="MIT\n",
    )
    lock = _lock(runtime / "pnpm-lock.yaml", ("tool", "1.0.0"))
    _hoisted_state(
        runtime,
        lock,
        {"tool@1.0.0": ["node_modules\\tool"]},
    )

    with pytest.raises(supply.SupplyChainError) as raised:
        supply.inventory_pnpm_packages(node_modules, lock)

    assert raised.value.code == "supply_chain_pnpm_lock_installation_mismatch"


def test_pnpm_lock_closure_filters_optional_platform_before_descendants(tmp_path):
    lock = tmp_path / "pnpm-lock.yaml"
    lock.write_text(
        """lockfileVersion: '9.0'

importers:

  .:
    dependencies:
      root:
        specifier: 1.0.0
        version: 1.0.0

packages:

  'root@1.0.0': {}
  'win@1.0.0':
    os: [win32]
    cpu: [x64]
  'mac@1.0.0':
    os: [darwin]
    cpu: [arm64]
  'mac-child@1.0.0': {}

snapshots:

  'root@1.0.0':
    optionalDependencies:
      win: 1.0.0
      mac: 1.0.0
  'win@1.0.0': {}
  'mac@1.0.0':
    dependencies:
      mac-child: 1.0.0
  'mac-child@1.0.0': {}
""",
        encoding="utf-8",
    )
    graph = supply._parse_pnpm_lock(lock)

    selected = supply._pnpm_production_contexts(
        graph,
        target_os="win32",
        target_cpu="x64",
    )

    assert selected == frozenset({"root@1.0.0", "win@1.0.0"})


def test_supply_chain_outputs_are_deterministic_and_keep_database_provenance(tmp_path):
    runtime = tmp_path / "agent-runtime"
    _physical_package(
        runtime / "node_modules" / "tool",
        "tool",
        "1.0.0",
        license_name="MIT",
        license_text="MIT fixture\n",
    )
    node = tmp_path / "node.exe"
    node.write_bytes(b"pinned-node")
    node_license = tmp_path / "node-LICENSE"
    node_license.write_text("Node fixture licence\n", encoding="utf-8")
    python_license = tmp_path / "python-LICENSE"
    python_license.write_text("Python fixture licence\n", encoding="utf-8")
    database = tmp_path / "genshin.db"
    _database(database)
    lock = _lock(runtime / "pnpm-lock.yaml", ("tool", "1.0.0"))
    _hoisted_state(
        runtime,
        lock,
        {"tool@1.0.0": ["node_modules\\tool"]},
    )
    manifest = runtime / "phase5b-official-turn-runtime-manifest.json"
    manifest.write_text("{}\n", encoding="utf-8")
    output = tmp_path / "supply-chain"

    first = supply.generate_supply_chain_materials(
        output_directory=output,
        app_version="0.45.0",
        runtime_root=runtime,
        node_executable=node,
        node_version="24.19.0",
        node_license_path=node_license,
        python_license_path=python_license,
        python_distribution_names=("bottle",),
        database=database,
        pnpm_lock=lock,
        runtime_manifest=manifest,
    )
    first_bytes = {path.name: path.read_bytes() for path in output.iterdir()}
    second = supply.generate_supply_chain_materials(
        output_directory=output,
        app_version="0.45.0",
        runtime_root=runtime,
        node_executable=node,
        node_version="24.19.0",
        node_license_path=node_license,
        python_license_path=python_license,
        python_distribution_names=("bottle",),
        database=database,
        pnpm_lock=lock,
        runtime_manifest=manifest,
    )

    assert first == second
    assert first_bytes == {path.name: path.read_bytes() for path in output.iterdir()}
    sbom = json.loads((output / supply.SBOM_FILENAME).read_text(encoding="utf-8"))
    database_component = next(
        component
        for component in sbom["components"]
        if component["name"] == "YuanShenResources-derived genshin.db"
    )
    assert "licenses" not in database_component
    assert {item["value"] for item in database_component["properties"]} >= {
        "NOASSERTION",
        "user-managed",
        "not-applicable",
    }
    assert "prohibited-by-project-policy" not in {
        item["value"] for item in database_component["properties"]
    }
    license_manifest = json.loads(
        (output / supply.LICENSE_MANIFEST_FILENAME).read_text(encoding="utf-8"),
    )
    assert license_manifest["contract"] == "genshin-release-license-manifest-v2"
    assert license_manifest["database"]["license_concluded"] == "NOASSERTION"
    assert license_manifest["database"]["policy"] == "user_managed_legal_assessment"
    assert license_manifest["database"]["engineering_release_gate"] is False
    assert license_manifest["database"]["adr"] == supply.DATABASE_ADR
    notices = (output / supply.NOTICE_FILENAME).read_text(encoding="utf-8")
    assert "No open-source" in notices
    assert "user-managed" in notices
    assert "not an automated engineering release gate" in notices
    assert supply.DATABASE_ADR in notices


def test_package_manifest_is_sorted_and_excludes_itself(tmp_path):
    package = tmp_path / "GenshinCalc"
    (package / "z").mkdir(parents=True)
    (package / "z" / "last.bin").write_bytes(b"last")
    (package / "first.bin").write_bytes(b"first")

    first = supply.write_package_file_manifest(package)
    first_text = (package / supply.PACKAGE_MANIFEST_FILENAME).read_text(encoding="utf-8")
    second = supply.write_package_file_manifest(package)

    assert first == second
    lines = first_text.splitlines()
    assert lines[0].endswith("  first.bin")
    assert lines[1].endswith("  z/last.bin")
    assert supply.PACKAGE_MANIFEST_FILENAME not in first_text
    expected = hashlib.sha256(b"first").hexdigest()
    assert lines[0].startswith(expected)


def test_package_manifest_verifier_recomputes_without_rewriting(tmp_path):
    package = tmp_path / "GenshinCalc"
    (package / "sub").mkdir(parents=True)
    (package / "alpha.bin").write_bytes(b"alpha")
    (package / "sub" / "beta.bin").write_bytes(b"beta")
    generated = supply.write_package_file_manifest(package)
    manifest = package / supply.PACKAGE_MANIFEST_FILENAME
    sealed = manifest.read_bytes()

    verified = supply.verify_package_file_manifest(package)

    assert manifest.read_bytes() == sealed
    assert verified == {
        "contract": "genshin-package-file-manifest-verification-v1",
        "definition": generated["definition"],
        "status": "verified",
        "files": 2,
        "bytes": 9,
        "manifest_sha256": generated["manifest_sha256"],
    }


@pytest.mark.parametrize(
    ("manifest_text", "code"),
    [
        ("not-a-row\n", "supply_chain_package_manifest_malformed"),
        (
            f"{'0' * 64}  ../escape.bin\n",
            "supply_chain_package_manifest_path_invalid",
        ),
        (
            f"{'0' * 64}  C:/escape.bin\n",
            "supply_chain_package_manifest_path_invalid",
        ),
        (
            f"{'0' * 64}  alpha.bin\n{'0' * 64}  alpha.bin\n",
            "supply_chain_package_manifest_duplicate",
        ),
        (
            f"{'0' * 64}  z.bin\n{'0' * 64}  a.bin\n",
            "supply_chain_package_manifest_unsorted",
        ),
    ],
)
def test_package_manifest_verifier_rejects_untrusted_rows(
    tmp_path, manifest_text, code
):
    package = tmp_path / "GenshinCalc"
    package.mkdir()
    (package / supply.PACKAGE_MANIFEST_FILENAME).write_text(
        manifest_text,
        encoding="utf-8",
        newline="",
    )

    with pytest.raises(supply.SupplyChainError) as raised:
        supply.verify_package_file_manifest(package)

    assert raised.value.code == code


def test_package_manifest_verifier_rejects_missing_unlisted_and_changed_files(tmp_path):
    package = tmp_path / "GenshinCalc"
    package.mkdir()
    payload = package / "payload.bin"
    payload.write_bytes(b"original")
    supply.write_package_file_manifest(package)

    payload.write_bytes(b"changed")
    with pytest.raises(supply.SupplyChainError) as changed:
        supply.verify_package_file_manifest(package)
    assert changed.value.code == "supply_chain_package_manifest_hash_mismatch"

    payload.unlink()
    with pytest.raises(supply.SupplyChainError) as missing:
        supply.verify_package_file_manifest(package)
    assert missing.value.code == "supply_chain_package_manifest_file_missing"

    payload.write_bytes(b"original")
    (package / "extra.bin").write_bytes(b"extra")
    with pytest.raises(supply.SupplyChainError) as unlisted:
        supply.verify_package_file_manifest(package)
    assert unlisted.value.code == "supply_chain_package_manifest_file_unlisted"


def test_package_manifest_verifier_rejects_reparse_members(tmp_path, monkeypatch):
    package = tmp_path / "GenshinCalc"
    package.mkdir()
    payload = package / "payload.bin"
    payload.write_bytes(b"payload")
    supply.write_package_file_manifest(package)
    original = package_integrity._is_reparse_point

    def fake_reparse(path):
        return Path(path) == payload or original(path)

    monkeypatch.setattr(package_integrity, "_is_reparse_point", fake_reparse)
    with pytest.raises(supply.SupplyChainError) as raised:
        supply.verify_package_file_manifest(package)

    assert raised.value.code == "supply_chain_package_link_unsupported"


def test_database_identity_must_come_from_database_metadata(tmp_path):
    database = tmp_path / "genshin.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")

    with pytest.raises(supply.SupplyChainError) as raised:
        supply._database_identity(database)

    assert raised.value.code == "supply_chain_database_identity_unavailable"


def test_database_identity_quotes_uri_characters_in_directory(tmp_path):
    directory = tmp_path / "许可 # reviewed"
    directory.mkdir()
    database = directory / "genshin.db"
    _database(database)
    assert supply._database_identity(database)[0] == "a" * 40


def _review_fixture(tmp_path, monkeypatch):
    record = supply.ComponentRecord(
        "npm", "tool", "1.0.0", "pkg:npm/tool@1.0.0", "MIT", "a" * 64,
        1, (), ("license_text_missing",),
    )
    monkeypatch.setattr(supply, "inventory_pnpm_packages", lambda *_: (record,))
    monkeypatch.setattr(supply, "inventory_python_packages", lambda *_: ())
    source = tmp_path / "original-LICENSE"
    source.write_bytes(b"Original BSD attribution and terms\n")
    document = {
        "path": source.name, "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "source_url": "https://example.org/upstream/commit/LICENSE",
    }
    entry = {
        "purl": record.purl, "binding": {"package_metadata_sha256": record.metadata_sha256},
        "concluded_license": "BSD-2-Clause", "resolved_codes": ["license_text_missing"],
        "resolution": "source_notice_verified", "review_note": "Metadata differs from original source notice.",
        "documents": [document],
    }
    evidence = tmp_path / "review.json"
    evidence.write_text(json.dumps({"contract": "genshin-reviewed-license-evidence-v1", "components": [entry]}))
    node = tmp_path / "node.exe"
    node.write_bytes(b"pinned-node")
    database = tmp_path / "genshin.db"
    _database(database)
    runtime = tmp_path / "runtime.json"
    runtime.write_text("{}")
    return entry, evidence, source, {
        "output_directory": tmp_path / "out", "app_version": "test", "runtime_root": tmp_path,
        "node_executable": node, "node_version": "24.19.0", "node_license_path": source,
        "python_license_path": source, "database": database, "pnpm_lock": runtime,
        "runtime_manifest": runtime, "license_evidence_path": evidence,
    }


def test_review_materials_resolve_missing_text_without_rewriting_upstream_declaration(tmp_path, monkeypatch):
    entry, _, _, args = _review_fixture(tmp_path, monkeypatch)
    first = supply.generate_supply_chain_materials(**args)
    assert first == supply.generate_supply_chain_materials(**args)
    assert first["review_required_count"] == 0
    output = args["output_directory"]
    manifest = json.loads((output / supply.LICENSE_MANIFEST_FILENAME).read_bytes())
    component = manifest["components"][0]
    assert component["declared_license"] == "MIT"
    assert component["concluded_license"] == "BSD-2-Clause"
    assert "Original BSD attribution" in (output / supply.NOTICE_FILENAME).read_text()
    review = json.loads((output / supply.LICENSE_REVIEW_FILENAME).read_bytes())
    assert review["components"][0]["binding"] == entry["binding"]
    assert "path" not in review["components"][0]["documents"][0]
    assert supply.LICENSE_REVIEW_FILENAME in first["outputs"]


@pytest.mark.parametrize("change,code", [
    ("metadata", "license_evidence_identity_mismatch"),
    ("version", "license_evidence_identity_mismatch"),
    ("text", "license_evidence_document_hash_mismatch"),
    ("path", "license_evidence_path_invalid"),
    ("empty", "license_evidence_documents_missing"),
    ("scope", "license_evidence_review_scope_mismatch"),
])
def test_review_evidence_cannot_clear_changed_or_unproven_materials(tmp_path, monkeypatch, change, code):
    entry, evidence, source, args = _review_fixture(tmp_path, monkeypatch)
    if change == "metadata":
        entry["binding"]["package_metadata_sha256"] = "b" * 64
    elif change == "version":
        entry["purl"] = "pkg:npm/tool@2.0.0"
    elif change == "text":
        source.write_text("changed")
    elif change == "path":
        entry["documents"][0]["path"] = "../outside"
    elif change == "empty":
        entry["documents"] = []
    elif change == "scope":
        entry["resolved_codes"] = []
    evidence.write_text(json.dumps({"contract": "genshin-reviewed-license-evidence-v1", "components": [entry]}))
    with pytest.raises(supply.SupplyChainError, match=code):
        supply.generate_supply_chain_materials(**args)
    assert not args["output_directory"].exists()


def test_node_review_requires_exact_executable_bytes(tmp_path, monkeypatch):
    entry, evidence, _source, args = _review_fixture(tmp_path, monkeypatch)
    node_review = {
        **entry, "purl": "pkg:generic/node.js@24.19.0",
        "binding": {"binary_sha256": hashlib.sha256(args["node_executable"].read_bytes()).hexdigest()},
    }
    evidence.write_text(json.dumps({"contract": "genshin-reviewed-license-evidence-v1", "components": [entry, node_review]}))
    args["node_license_path"] = None
    assert supply.generate_supply_chain_materials(**args)["review_required_count"] == 0
    args["node_executable"].write_bytes(b"changed-node")
    with pytest.raises(supply.SupplyChainError, match="license_evidence_identity_mismatch"):
        supply.generate_supply_chain_materials(**args)
