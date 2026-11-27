"""Authority follows the admitted database, never equipment- or plan-supplied claims."""
from __future__ import annotations

import copy
import json
import sqlite3
from argparse import Namespace
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Barrier
from types import SimpleNamespace

import pytest
from test_elemental_multiaura_v4 import aura
from test_plan_mechanics import native
from test_special_reaction_mechanics_projection import _application_fixture, _enable

from genshin_app.equipment import special_capability_authority as authority
from genshin_app.equipment.composer import compose_equipment_plan
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError
from genshin_db.equipment_queries import equipment_read_session
from genshin_db.release_integrity import build_release_core
from genshin_db.release_profile import write_database_release_manifest
from genshin_engine import special_reaction_capabilities as capability
from tests.test_database_startup_gate import _identity, _manifest
from tests.test_runtime_action_resource import (  # noqa: F401
    fake_repo,
    resource_db,
    stub_fetcher,
)


def _catalog(commit, *, revision=731):
    """Synthetic future database release, outside the packaged compatibility list."""
    data = capability.load_reviewed_special_reaction_capabilities()
    data.update(source_upstream_commit=commit, source_target_version="OSCBWin8.0.52", revision=revision)
    data["content_hash"] = capability._catalog_hash(data)
    return data


def _evidence(catalog, *, actor_id="ice"):
    row = next(entry for entry in catalog["entries"]
               if entry["runtime_support_status"] == "supported" and entry["form_selection_required"])
    equipment = {actor_id: {
        "character_ref": {
            "db_character_id": row["character_id"],
            "skill_depot_id": row["required_skill_depot_id"],
            "version_line": "release",
        },
        "data_version": {"version_line": "release", "upstream_commit": catalog["source_upstream_commit"]},
    }}
    return row, equipment


def _confirmed(equipment):
    return [
        row for row in capability.project_special_reaction_party_capabilities(equipment)
        if row["status"] == "confirmed"
    ]


def test_scope_qualifies_new_commit_but_forged_plan_projection_cannot_grant_it():
    commit = "1" * 40
    catalog = capability.validate_reviewed_special_reaction_catalog(_catalog(commit), upstream_commit=commit)
    entry, equipment = _evidence(catalog)
    assert _confirmed(equipment) == []
    with capability.use_special_reaction_capability_catalog(catalog):
        row, = _confirmed(equipment)
        assert row["equipment_upstream_commit"] == row["catalog_source_upstream_commit"] == commit
        assert capability.verified_capability_tags(row) == tuple(entry["capability_tags"])
        for field, value in (
            ("catalog_content_hash", "0" * 64),
            ("catalog_entry_fingerprint", "0" * 64),
            ("equipment_upstream_commit", "2" * 40),
            ("catalog_revision", -1),
            ("skill_depot_id", entry["required_skill_depot_id"] + 1),
            ("runtime_profile_id", "invented-profile"),
        ):
            assert not capability.verified_capability_tags({**row, field: value}), field
        wrong_form = copy.deepcopy(equipment)
        wrong_form["ice"]["character_ref"].pop("skill_depot_id")
        assert _confirmed(wrong_form) == []
    assert not capability.verified_capability_tags(row)


def test_database_scope_does_not_silently_fall_back_to_a_valid_legacy_row():
    previous = capability.load_reviewed_special_reaction_capabilities(
        "f11cf545a7eee32a29999f960ebfba305f896ed3")
    entry, equipment = _evidence(previous)
    old_row, = _confirmed(equipment)
    assert capability.verified_capability_tags(old_row) == tuple(entry["capability_tags"])
    future = _catalog("6" * 40)
    with capability.use_special_reaction_capability_catalog(future):
        _, current = _evidence(future)
        supplied = capability.load_reviewed_special_reaction_capabilities()
        supplied["entries"].clear()
        future["entries"].clear()
        assert _confirmed(current)
        assert _confirmed(equipment) == []
        assert not capability.verified_capability_tags(old_row)
    assert capability.verified_capability_tags(old_row)


@pytest.mark.parametrize("kind", ["hash", "entry_hash", "identity"])
def test_catalog_validation_rejects_broken_hashes_and_release_identity(kind):
    commit = "2" * 40
    catalog = _catalog(commit)
    expected_commit = commit
    if kind == "hash":
        catalog["revision"] += 1
    elif kind == "entry_hash":
        catalog["entries"][0]["required_skill_depot_id"] += 1
        catalog["content_hash"] = capability._catalog_hash(catalog)
    else:
        expected_commit = "3" * 40
    with pytest.raises((RuntimeError, ValueError)):
        capability.validate_reviewed_special_reaction_catalog(catalog, upstream_commit=expected_commit)


def test_parallel_database_reads_and_failed_nested_read_do_not_share_authority():
    catalogs = [
        capability.validate_reviewed_special_reaction_catalog(_catalog(token * 40), upstream_commit=token * 40)
        for token in ("4", "5")
    ]
    equipment = [_evidence(catalog)[1] for catalog in catalogs]
    barrier = Barrier(2, timeout=10)

    def check(own, other):
        with capability.use_special_reaction_capability_catalog(catalogs[own]):
            barrier.wait()
            assert _confirmed(equipment[other]) == []
            expected, = _confirmed(equipment[own])
            with (pytest.raises(ValueError, match="failed nested read"),
                  capability.use_special_reaction_capability_catalog(catalogs[other])):
                assert _confirmed(equipment[own]) == []
                assert _confirmed(equipment[other])
                raise ValueError("failed nested read")
            barrier.wait()
            assert _confirmed(equipment[own]) == [expected]
        assert not capability.verified_capability_tags(expected)

    with ThreadPoolExecutor(2) as pool:
        left = pool.submit(check, 0, 1)
        right = pool.submit(check, 1, 0)
        left.result(timeout=15)
        right.result(timeout=15)


@pytest.fixture
def database_catalog(tmp_path):
    identity = _identity()
    data = _catalog(identity["upstream_commit"])
    data["source_target_version"] = identity["upstream_track"] + identity["game_version"]
    data["content_hash"] = capability._catalog_hash(data)
    db = tmp_path / "genshin.db"
    with closing(sqlite3.connect(db)) as connection, connection:
        connection.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.executemany("INSERT INTO meta VALUES(?,?)", [
            (authority.META_KEY, json.dumps(data)),
            ("upstream_commit", identity["upstream_commit"]),
        ])
        identity["release_core"] = build_release_core(connection)
    identity["datasets"]["equipment"]["lines"]["release"]["upstream_commit"] = identity["upstream_commit"]
    manifest = tmp_path / "database-live-manifest.json"
    write_database_release_manifest(manifest, _manifest(identity, channel="dev"))
    return db, data, manifest


def test_loads_database_owned_authority_without_packaged_commit(database_catalog):
    db, data, _ = database_catalog
    with equipment_read_session(db):
        verified, proof = authority.load_database_capability_catalog(db)
        assert verified == data
        assert proof["contract"] == authority.CONTRACT
        assert authority.load_database_capability_catalog(db) == (verified, proof)
    with capability.use_special_reaction_capability_catalog(verified):
        entry, equipment = _evidence(data)
        row, = _confirmed(equipment)
        assert capability.verified_capability_tags(row) == tuple(entry["capability_tags"])


def test_cli_and_native_capability_discovery_follow_the_open_database(database_catalog):
    from genshin_app.agent_chat.tools import _fixed_context_getters
    from genshin_app.cli import _cmd_capabilities

    db, data, _ = database_catalog
    cli = _cmd_capabilities(Namespace(data_dir=db.parent))
    session = SimpleNamespace(workflow="artifact", db_path=db, _is_board=lambda: False)
    native_doc = _fixed_context_getters(session)["special_reaction_capabilities"]()
    cli_catalog = cli["special_reaction_contract"]["input_contract"]["capability_catalog"]
    native_catalog = native_doc["input_contract"]["capability_catalog"]
    assert cli_catalog == native_catalog
    assert cli_catalog["source_upstream_commit"] == data["source_upstream_commit"]
    assert cli_catalog["content_hash"] == data["content_hash"]
    assert cli_catalog["revision"] == data["revision"]
    assert cli["plan_contract"]["mechanics_contract"]["special_reactions"]["capability_catalog"] == cli_catalog
    assert capability.load_reviewed_special_reaction_capabilities()["source_upstream_commit"] != data["source_upstream_commit"]


def test_native_authored_qualification_rows_cannot_substitute_for_equipment(database_catalog):
    db, data, _ = database_catalog
    # An attacker can copy a byte-for-byte legitimate row from another plan.
    # A standalone row check is therefore insufficient at the App boundary.
    entry = next(item for item in data["entries"]
                 if "native_reaction_qualification.stellar_swirl" in item["capability_tags"])
    equipment = {"a": {"character_ref": {
        "db_character_id": entry["character_id"], "skill_depot_id": entry["required_skill_depot_id"],
        "version_line": data["version_line"],
    }, "data_version": {
        "upstream_commit": data["source_upstream_commit"], "version_line": data["version_line"],
    }}}
    template, rotation, model = _application_fixture("anemo", initial=[aura("cryo", .8)])
    _enable(model, "stellar_swirl")
    with capability.use_special_reaction_capability_catalog(data):
        model["special_reaction_party_capabilities"] = _confirmed(equipment)
        assert model["special_reaction_party_capabilities"]
    template["characters"][0]["stats"]["energy_recharge"] = 1
    plan = native()
    plan.update(manual_template=template, rotation_model=rotation, mechanics_model=model, effect_bindings=[])
    assert plan["equipment_inputs"] == {}
    report = compose_equipment_plan(plan, db)
    assert report["state"] == "pending"
    assert any(row["code"] == "special_capability_projection_pending" for row in report["missing_inputs"])


@pytest.mark.parametrize("mutation", ["valid_rehashed_catalog", "missing_manifest", "unsigned_frozen"])
def test_live_catalog_requires_exact_manifest_witness_and_frozen_trust(database_catalog, mutation, monkeypatch):
    db, data, manifest = database_catalog
    if mutation == "valid_rehashed_catalog":
        update = {**data, "revision": data["revision"] + 1}
        update["content_hash"] = capability._catalog_hash(update)
        with closing(sqlite3.connect(db)) as connection, connection:
            connection.execute("UPDATE meta SET value=? WHERE key=?", (json.dumps(update), authority.META_KEY))
    elif mutation == "missing_manifest":
        manifest.unlink()
    else:
        monkeypatch.setattr(authority, "is_frozen", lambda: True)
    with equipment_read_session(db), pytest.raises(PlanError) as failure:
        authority.load_database_capability_catalog(db)
    assert failure.value.reason == "database_special_capability_catalog_invalid"


def test_saved_catalog_can_be_bound_offline_but_tampered_witness_cannot(database_catalog, monkeypatch):
    from genshin_app.equipment.execution_verification import (
        ExecutionVerificationSession,
    )

    db, data, _ = database_catalog
    with equipment_read_session(db):
        _, proof = authority.load_database_capability_catalog(db)
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("Historical validation touched the database"))
    result = {authority.FIELD: proof}
    receipt = {authority.FIELD: fingerprint(proof), "result_fingerprint": fingerprint(result)}
    with ExecutionVerificationSession() as session:
        authority.bind_saved_capability_catalog(result, receipt, session)
        assert session.special_capability_proof_fingerprint == fingerprint(proof)
        assert _confirmed(_evidence(data)[1]) == []  # live execution still has no authority
    changed = copy.deepcopy(result)
    changed[authority.FIELD]["meta_siblings"][0] = "0" * 64
    rehashed = {authority.FIELD: fingerprint(changed[authority.FIELD]), "result_fingerprint": fingerprint(changed)}
    with ExecutionVerificationSession() as session, pytest.raises(ValueError):
        authority.bind_saved_capability_catalog(changed, rehashed, session)


def test_frozen_loader_and_offline_replay_require_matching_signed_pair(database_catalog, monkeypatch):
    from genshin_app import release_pair_trust, release_pair_update
    from genshin_app.equipment.execution_verification import (
        ExecutionVerificationSession,
    )
    from genshin_app.release_pair_discovery import (
        DISCOVERY_CONTRACT,
        DISCOVERY_SCHEMA_VERSION,
    )
    from genshin_db.release_profile import (
        create_release_pair_manifest,
        load_database_release_manifest,
        write_release_pair_manifest,
    )
    from tests.test_product_release_index import _signed, _verify

    db, data, manifest_path = database_catalog
    manifest = load_database_release_manifest(manifest_path)
    pair = create_release_pair_manifest(
        manifest, release_pair_id="fixture-pair", pair_sequence=1, pair_policy="exact",
        app_release_id="fixture-app", app_version="1.2.3", app_artifact_manifest_sha256="1" * 64,
    )
    locator = {
        "contract": DISCOVERY_CONTRACT, "schema_version": DISCOVERY_SCHEMA_VERSION,
        "channel": manifest["channel"], "pair_manifest": pair, "database_manifest": manifest,
        "artifacts": {
            "app": {"url": "https://example.invalid/app.zip", "format": "app-zip",
                    "sha256": "2" * 64, "size_bytes": 10, "unpacked_size_bytes": 20, "unpacked_files": 1},
            "database": {"url": "https://example.invalid/genshin.db", "format": "sqlite",
                         "sha256": manifest["database"]["sha256"], "size_bytes": manifest["database"]["size_bytes"]},
        },
    }
    product_proof = _signed(locator)
    write_release_pair_manifest(db.parent / "release-pair-live-manifest.json", pair)
    (db.parent / release_pair_update.LIVE_PAIR_PROOF_FILENAME).write_text(json.dumps(product_proof), "utf-8")
    # Inject the existing isolated signing verifier at the cryptographic
    # boundary. Production RSA interoperability runs in test_release_pair_trust.
    monkeypatch.setattr(release_pair_update, "build_trust_verifier", lambda _keys: _verify)
    monkeypatch.setattr(release_pair_trust, "build_trust_verifier", lambda _keys: _verify)
    monkeypatch.setattr(authority, "is_frozen", lambda: True)
    with equipment_read_session(db):
        loaded, proof = authority.load_database_capability_catalog(db)
    assert loaded == data
    assert proof["signed_pair"]["proof"] == product_proof
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("Offline authority reopened the database"))
    for valid in (True, False):
        current = copy.deepcopy(proof)
        if not valid:
            current["signed_pair"]["proof"]["signature"]["value"] = "0" * 64
        result = {authority.FIELD: current}
        receipt = {authority.FIELD: fingerprint(current), "result_fingerprint": fingerprint(result)}
        with ExecutionVerificationSession() as session:
            if valid:
                authority.bind_saved_capability_catalog(result, receipt, session)
                assert session.special_capability_proof_fingerprint == receipt[authority.FIELD]
            else:
                with pytest.raises(ValueError):
                    authority.bind_saved_capability_catalog(result, receipt, session)


def test_future_database_calculation_package_reopens_offline(resource_db, monkeypatch):  # noqa: F811
    from genshin_app.equipment.data_version import equipment_data_version
    from genshin_app.equipment.delivery import validate_delivery_calculated
    from genshin_app.equipment.service import run_plan_calc
    from genshin_app.version import (
        DATABASE_DATA_CONTRACT_VERSION,
        DATABASE_LOGIC_PROFILE,
    )
    from genshin_db.queries import get_meta
    from genshin_db.release_profile import create_database_release_manifest

    db = resource_db
    meta = get_meta(db)
    data = _catalog(meta["upstream_commit"])
    data["source_target_version"] = meta["upstream_track"] + meta["game_version"]
    entry = next(item for item in data["entries"]
                 if "native_reaction_qualification.stellar_swirl" in item["capability_tags"])
    entry.update(character_id=90000001, required_skill_depot_id=9001, form_selection_required=False)
    entry["entry_fingerprint"] = fingerprint({key: value for key, value in entry.items()
                                                if key != "entry_fingerprint"})
    data["content_hash"] = capability._catalog_hash(data)
    with closing(sqlite3.connect(db)) as connection, connection:
        connection.execute("INSERT INTO meta VALUES(?,?)", (authority.META_KEY, json.dumps(data)))
    manifest = create_database_release_manifest(
        db, database_release_id=f'db-{meta["game_version"]}-r1',
        release_sequence=1, channel="dev", logic_profile=DATABASE_LOGIC_PROFILE,
        data_contract_version=DATABASE_DATA_CONTRACT_VERSION, require_release_ready=False,
    )
    write_database_release_manifest(db.parent / "database-live-manifest.json", manifest)

    template, rotation, model = _application_fixture("anemo", initial=[aura("cryo", .8, "b")])
    from test_mechanics_execution import second_character
    second_character(template, rotation, "b")
    _enable(model, "stellar_swirl")
    model.pop("special_reaction_party_capabilities")
    for c in template["characters"]:
        c["stats"]["energy_recharge"] = 1
    plan = native()
    plan.update(manual_template=template, rotation_model=rotation, mechanics_model=model, effect_bindings=[])
    plan["equipment_inputs"]["a"] = {
        "mode": "panel", "panel_policy": "static_included",
        "character_ref": {"db_character_id": 90000001, "skill_depot_id": 9001, "version_line": "release"},
        "weapon": None, "artifact_sets": [],
        "data_version": equipment_data_version(meta, "release"),
        "source_refs": [{"kind": "manual", "note": "Synthetic explicit panel"}],
        "panel_provenance": {"origin": "agent_constructed", "entered_by": "agent", "note": "Synthetic explicit panel"},
    }
    calc = run_plan_calc(plan, db, capture_display=True)
    assert calc["result"][authority.FIELD]["manifest"]["database"]["upstream_commit"] == data["source_upstream_commit"]
    assert any(h["damage_system"] == "stellar" for h in calc["result"]["hits"])
    saved = json.loads(json.dumps(calc))
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("Offline calculation read queried SQLite"))
    validate_delivery_calculated(saved)
