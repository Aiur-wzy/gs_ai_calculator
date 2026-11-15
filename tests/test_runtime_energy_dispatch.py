"""Finite full-source trigger bridges on isolated real-structure fixtures."""
from __future__ import annotations

import copy
import json
from collections import defaultdict

import pytest

from genshin_db.equipment_queries import equipment_read_session
from genshin_db.runtime_attack_metadata import get_attack_metadata_context
from genshin_db.runtime_energy_dispatch import get_energy_dispatch_context
from genshin_runtime.energy_trigger_identity import LIMITS, all_entries, recipe
from tests.test_pipeline import fake_repo  # noqa: F401
from tests.test_runtime_object_index import build, rows, write

# ruff: noqa: F811


@pytest.fixture
def trigger_repo(fake_repo):
    r = recipe()
    tables = defaultdict(list)
    for record in r["fixture_evidence"]["records"]:
        tables[record["source_path"]].append(copy.deepcopy(record["raw"]))
    for path, records in tables.items():
        write(fake_repo, path, records)
    abilities = defaultdict(list)
    for parent in r["parents"].values():
        abilities[parent["source_path"]].append({"Default": parent["new_raw"]})
    for path, value in abilities.items():
        write(fake_repo, path, value)
    for talent in r["fixture_evidence"]["talents"]:
        write(fake_repo, talent["source_path"], {talent["name"]: talent["raw"]})
    mount = r["mount"]
    write(fake_repo, mount["source_path"], {mount["new_pointer"][1:]: mount["new_raw"]})
    write(fake_repo, "ExcelBinOutput/MaterialExcelConfigData.json", [m["new_raw"] for m in r["fixture_evidence"]["materials"]])
    write(fake_repo, "ExcelBinOutput/AttackAttenuationExcelConfigData.json", [
        {"group": "default", "resetCycle": 2.5, "durabilitySequence": [1, 0, 0], "damageSequence": [1, 1, 1]}])
    return fake_repo


@pytest.fixture
def trigger_db(tmp_path, trigger_repo):
    db = tmp_path / "triggers.db"
    build(db, trigger_repo, upstream_commit=recipe()["commit"])
    return db


def identity(db, variant="tap", local=False):
    char, depot, skill, index = (10000063, 6301, 10632, 1) if local else (10000047, 4701, 10472, 0 if variant == "tap" else 2)
    selection = {"character_id": char, "skill_depot_id": depot, "skill_id": skill, "constellation": 0, "talent_level": 10}
    candidate = None
    if not local:
        found = get_attack_metadata_context(db, **selection).public_projection
        candidate = next(c["candidate_id"] for c in found["candidates"] if c["status"] == "ready" and c["projection"]["parameter"]["param_index"] == index)
    return {"version_line": "release", "snapshot_id": None, "selection": selection,
            "param_index": index, "metadata_candidate_id": candidate}


def origin(db, variant="tap", local=False):
    suffix = "/EMGBJJOIENB/" + ("0" if local or variant == "tap" else "1") + "/KKMLMJNCLLG"
    modifier = "ElementalArt_ElemBall" if local else "Avatar_Kazuha_ElementalArt_DropBall"
    return next(r["legacy_reference_id"] for r in rows(db, "runtime_material_reference")
                if r["json_pointer"].endswith(suffix) and "/" + modifier + "/" in r["json_pointer"])


def context(db, variant="tap", local=False):
    with equipment_read_session(db):
        return get_energy_dispatch_context(db, origin_reference_id=origin(db, variant, local),
            parent_identity=identity(db, variant, local))


@pytest.mark.parametrize("variant,local,count", [("tap", False, 3), ("hold", True, 4), ("hold", False, 4)])
def test_two_source_families_and_holdout(trigger_db, variant, local, count):
    data = context(trigger_db, variant, local).public_projection
    assert data["status"] == "ready", data["issues"]
    assert data["source_query"]["reference_projection"]["count"] == count
    assert data["whole_source_status"] == "partial" and data["execution_support"] == "not_evaluated"
    assert data["source_bundle"]["parent_bridge"]["variant"] == variant


def test_invalid_identity_before_database(tmp_path):
    with pytest.raises(ValueError):
        get_energy_dispatch_context(tmp_path / "missing.db", origin_reference_id="x" * 64, parent_identity={})


def test_wrong_parameter_never_attaches_same_value(trigger_db):
    ident = identity(trigger_db, local=True)
    ident["param_index"] = 0
    data = get_energy_dispatch_context(trigger_db, origin_reference_id=origin(trigger_db, local=True), parent_identity=ident).public_projection
    assert data["status"] == "pending"


def test_complete_raw_mutation_is_pending(trigger_db, trigger_repo):
    path = recipe()["parents"]["local_hold"]["source_path"]
    raw = json.loads((trigger_repo / path).read_text("utf8"))
    raw[0]["Default"]["unknownController"] = True
    write(trigger_repo, path, raw)
    build(trigger_db, trigger_repo, upstream_commit=recipe()["commit"])
    assert context(trigger_db, "hold", True).public_projection["status"] == "pending"


def test_same_controller_identity_across_variants(trigger_db):
    a, b = context(trigger_db).public_projection, context(trigger_db, "hold").public_projection
    assert a["source_bundle"]["parent_bridge"]["controller"]["controller_key"] == b["source_bundle"]["parent_bridge"]["controller"]["controller_key"]
    assert a["source_fingerprint"] != b["source_fingerprint"]


def test_same_session_parameter_context_reused_and_detached(trigger_db, monkeypatch):
    from genshin_db import runtime_action_parameter as parameters
    original, calls = parameters.get_action_parameter_context, []
    def counted(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)
    monkeypatch.setattr(parameters, "get_action_parameter_context", counted)
    with equipment_read_session(trigger_db):
        ident = identity(trigger_db)
        first = get_energy_dispatch_context(trigger_db, origin_reference_id=origin(trigger_db), parent_identity=ident)
        first.parameter_context["facts"]["records"].clear()
        first.public_projection["source_query"]["origin"]["operation"]["raw"].clear()
        second = get_energy_dispatch_context(trigger_db, origin_reference_id=origin(trigger_db), parent_identity=ident)
    assert len(calls) == 1
    assert second.public_projection["status"] == "ready" and second.parameter_context["facts"]["records"]


def test_unknown_related_writer_cannot_be_ignored(trigger_db, trigger_repo):
    item = recipe()["fixture_evidence"]["talents"][1]
    operations = copy.deepcopy(item["raw"])
    operations.append({"$type": "BIAHEMMGABC", "GBPFBNHLOFD": True,
        "IIBMNPFLNMJ": "Avatar_Shenhe_ElementalArt_Attack_H", "PEOJKHKHAKL": "unknownTimingControl", "PCABAADALND": "%2"})
    write(trigger_repo, item["source_path"], {item["name"]: operations})
    build(trigger_db, trigger_repo, upstream_commit=recipe()["commit"])
    out = context(trigger_db, "hold", True).public_projection
    assert out["status"] == "pending"
    assert any(i["code"] == "energy_trigger_complete_talent_unreviewed" for i in out["issues"])


def test_missing_and_ambiguous_mount_fail_closed(trigger_db, trigger_repo):
    mount = recipe()["mount"]
    write(trigger_repo, "BinOutput/Avatar/DuplicateMount.json", {mount["new_pointer"][1:]: mount["new_raw"]})
    build(trigger_db, trigger_repo, upstream_commit=recipe()["commit"])
    out = context(trigger_db).public_projection
    assert out["status"] == "pending"
    assert any(i["code"] == "energy_trigger_mount_identity_pending" for i in out["issues"])


def test_full_node_limit_includes_nested_unit_entries(trigger_db, monkeypatch):
    from genshin_db.runtime_catalog import RuntimeConfigError
    baseline = context(trigger_db, "hold", True).public_projection
    count = len(all_entries(baseline))
    monkeypatch.setitem(LIMITS, "max_nodes", count)
    assert context(trigger_db, "hold", True).public_projection["status"] == "ready"
    monkeypatch.setitem(LIMITS, "max_nodes", count - 1)
    with pytest.raises(RuntimeConfigError) as error:
        context(trigger_db, "hold", True)
    assert error.value.reason == "response_too_large"


def test_unreviewed_mount_field_is_pending_not_key_error(trigger_db, trigger_repo):
    mount = recipe()["mount"]
    write(trigger_repo, mount["source_path"], {"abilities": mount["new_raw"]})
    build(trigger_db, trigger_repo, upstream_commit=recipe()["commit"])
    out = context(trigger_db).public_projection
    assert out["status"] == "pending"
    assert any(i["code"] == "energy_trigger_mount_identity_pending" for i in out["issues"])
