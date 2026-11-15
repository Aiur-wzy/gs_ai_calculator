"""Independent B1 directory/activation boundary tests; never production writes."""
from __future__ import annotations

import copy
import sqlite3
from contextlib import closing, contextmanager
from types import SimpleNamespace

import pytest

from genshin_db import runtime_action_activation as ac
from genshin_db import runtime_capacity_delta as cc
from genshin_db.equipment_queries import borrow_read_connection, equipment_read_session
from genshin_db.runtime_catalog import fingerprint
from tests.action_resource_fixtures import (
    action_resource_synthetic_db_only,  # noqa: F401
)

pytestmark = pytest.mark.usefixtures("action_resource_synthetic_db_only")


@pytest.fixture
def rig(monkeypatch, tmp_path):
    avatar = {"id": 1, "skillDepotId": 10, "candSkillDepotIds": [], "combatConfigHash": 321}
    depot = {"id": 10, "skills": [101, 102], "subSkills": [104], "energySkill": 103,
             "talents": [201, 202, 203, 204, 205, 206],
             "inherentProudSkillOpens": [{"proudSkillGroupId": 400, "needAvatarPromoteLevel": 4}],
             "specialProudSkillOpens": [{"proudSkillGroupId": 500, "unlockCondition": "QUEST", "quest": [99]}],
             "extraAbilities": [], "attackModeSkill": 105, "leaderTalent": 700,
             "unknownFutureRoot": {"ability": "do_not_infer"}}
    tables = {
        "Avatar": [avatar], "AvatarSkillDepot": [depot],
        "AvatarSkill": [{"id": i, "proudSkillGroupId": i} for i in range(101, 106)],
        "AvatarTalent": [{"talentId": i, "openConfig": f"Talent{i}"} for i in range(201, 207)],
        "ProudSkill": [*({"proudSkillId": i * 100 + level, "proudSkillGroupId": i, "level": level}
                         for i in range(101, 106) for level in (1, 10, 13)),
                       {"proudSkillId": 40001, "proudSkillGroupId": 400, "level": 1, "breakLevel": 4},
                       {"proudSkillId": 50001, "proudSkillGroupId": 500, "level": 1}],
    }
    rows, raw_by_id, abilities = {}, {}, {}
    calls = {"closure": [], "node": [], "lookup": []}

    def rebuild():
        rows.clear()
        raw_by_id.clear()
        for table, records in tables.items():
            key_name = {"AvatarTalent": "talentId", "ProudSkill": "proudSkillId"}.get(table, "id")
            path = f"ExcelBinOutput/{table}ExcelConfigData.json"
            for index, raw in enumerate(records):
                ident = f"{table}:{index}"
                row = {"definition_id": ident, "definition_kind": "excel_record",
                       "source_path": path, "json_pointer": f"/{index}",
                       "node_sha256": fingerprint(raw), "exact_key": {key_name: raw[key_name]}}
                rows[ident], raw_by_id[ident] = row, raw
        for ident, raw in abilities.items():
            rows[ident] = {"definition_id": ident, "definition_kind": "ability", "source_path": "Bin/Abilities.json",
                          "json_pointer": f"/{ident}", "node_sha256": fingerprint(raw), "exact_key": "shared"}
            raw_by_id[ident] = raw

    rebuild()
    state = {"snapshot_id": "independent-engineering", "version_line": "release"}

    db_path = tmp_path / "synthetic-catalog.db"
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("CREATE TABLE fixture_identity(value INTEGER)")

    @contextmanager
    def read(path, *args):
        assert path == db_path
        with equipment_read_session(path):
            yield borrow_read_connection(path), state

    class Nodes:
        normalization = SimpleNamespace(mapping_fingerprint="m" * 64)

        def __init__(self, *args):
            pass

        def node(self, row):
            calls["node"].append(row["definition_id"])
            raw = raw_by_id[row["definition_id"]]
            return raw, {"source_path": row["source_path"], "source_sha256": "s" * 64,
                         "json_pointer": row["json_pointer"]}

        def source(self, path):
            assert path.endswith("ProudSkillExcelConfigData.json")
            return {}, tables["ProudSkill"]

    def lookup(conn, line, kind, key, source_path=None):
        calls["lookup"].append((kind, copy.deepcopy(key), source_path))
        return [r for r in rows.values() if r["definition_kind"] == kind and r["exact_key"] == key
                and (source_path is None or r["source_path"] == source_path)]

    def level(*args, slot, **kwargs):
        return {"status": "pending", "selection": {"skill_id": {"normal": 101, "skill": 102, "burst": 103}[slot],
                                                    "slot": slot}, "issues": [{"code": "unknown_writer"}]}

    def passive(*args, **kwargs):
        return {"status": "pending", "entry_records": {
            "selected_opening": {"record": copy.deepcopy(depot["inherentProudSkillOpens"][0]), "json_pointer": "/0/inherentProudSkillOpens/0"},
            "proud_skill": {"record": copy.deepcopy(tables["ProudSkill"][-2]), "json_pointer": "/15"},
            "promote_records": [{"record": {"promoteLevel": 6, "unlockMaxLevel": 90}}]},
            "issues": [{"code": "unknown_ability"}]}

    def closure(conn, state, roots, options):
        calls["closure"].append((roots, options))
        return {"definitions": [copy.deepcopy(rows[r]) for r in roots], "truncated": False,
                "frontier": [], "unresolved": []}

    monkeypatch.setattr(ac, "_read", read)
    monkeypatch.setattr(ac, "_Nodes", Nodes)
    monkeypatch.setattr(ac, "_lookup", lookup)
    monkeypatch.setattr(ac, "_row", lambda conn, line, ident: rows[ident])
    monkeypatch.setattr(ac, "_definition_summary", copy.deepcopy)
    monkeypatch.setattr(ac, "_closure", closure)
    monkeypatch.setattr(ac, "get_runtime_talent_level", level)
    monkeypatch.setattr(ac, "get_runtime_passive_source_profile", passive)
    return SimpleNamespace(avatar=avatar, depot=depot, tables=tables, rebuild=rebuild, calls=calls,
                           abilities=abilities, level=level, passive=passive, state=state, db_path=db_path)


def collect(rig, **kwargs):
    return ac.read_activation_catalog(rig.db_path, character_id=1, constellation=2,
                                      character_level=90, ascension=6, **kwargs)


def test_known_optional_and_unclassified_containers_remain_visible(rig):
    out = collect(rig)
    assert out["root_directory_status"] == "enumerated"
    assert "unknownFutureRoot" in out["unclassified_depot_fields"]
    assert any(r["raw"] == 105 for r in out["root_occurrences"] if r["kind"] == "skill_record")
    assert any(r["raw"] == 700 and r["activation_status"] == "pending"
               for r in out["root_occurrences"] if r["kind"] == "leader_talent")
    assert out["activation_closure_status"] == "pending" and out["execution_authority"] is False


def test_constellation_rank_does_not_activate_other_roots(rig):
    out = collect(rig)
    roots = out["root_occurrences"]
    assert [r["activation_status"] for r in roots if r["kind"] == "constellation"] == ["active"] * 2 + ["inactive"] * 4
    assert next(r for r in roots if r["kind"] == "special_opening")["activation_status"] == "pending"
    assert next(r for r in roots if r["kind"] == "inherent_opening")["activation_status"] == "active"
    assert next(r for r in roots if r["kind"] == "avatar_base_resource")["activation_status"] == "pending"


def test_unknown_inherent_activation_keeps_material_without_becoming_active(rig):
    rig.depot["inherentProudSkillOpens"][0]["unknownCondition"] = True
    rig.rebuild()
    out = collect(rig)
    root = next(r for r in out["root_occurrences"] if r["kind"] == "inherent_opening")
    assert root["activation_status"] == "pending" and root["definition_ids"]
    assert root["issues"] == [{"code": "unknown_opening_fields"}]


@pytest.mark.parametrize("field", ac.REQUIRED_CONTAINERS)
def test_absent_required_container_is_never_successfully_enumerated(rig, field):
    del rig.depot[field]
    rig.rebuild()
    out = collect(rig)
    assert out["root_directory_status"] == "pending"
    root = next(r for r in out["root_occurrences"] if r["kind"] == "missing_container")
    assert root["occurrence_pointer"].endswith("/" + field)


@pytest.mark.parametrize("field,bad", [("skills", 101), ("subSkills", [True]), ("energySkill", 103.0),
                                       ("talents", [201] * 6), ("extraAbilities", None)])
def test_malformed_directory_stays_pending(rig, field, bad):
    rig.depot[field] = bad
    rig.rebuild()
    assert collect(rig)["root_directory_status"] == "pending"


def test_every_unknown_level_is_retained_for_main_and_auxiliary_skills(rig):
    out = collect(rig)
    skills = [r for r in out["root_occurrences"] if r["kind"] == "skill_record"]
    assert len(skills) == 5
    for root in skills:
        assert root["candidate_parameter_levels"] == [1, 10, 13]
        assert len(root["definition_ids"]) == 4
        assert root["parameter_selection"] == "all_exact_group_candidates_no_selected_effective_level"


def test_equal_roots_have_distinct_occurrence_identity(rig):
    rig.depot["subSkills"].append(104)
    rig.rebuild()
    roots = [r for r in collect(rig)["root_occurrences"] if r["kind"] == "skill_record" and r["raw"] == 104]
    assert len(roots) == 2 and roots[0]["definition_ids"] == roots[1]["definition_ids"]
    assert roots[0]["occurrence_identity"] != roots[1]["occurrence_identity"]


def test_ambiguous_extra_ability_retains_all_materials(rig):
    rig.depot["extraAbilities"] = ["shared"]
    rig.abilities.update(a={"name": "shared"}, b={"name": "shared"})
    rig.rebuild()
    root = next(r for r in collect(rig)["root_occurrences"] if r["kind"] == "extra_ability")
    assert root["definition_ids"] == ["a", "b"]
    assert root["issues"] == [{"code": "extra_ability_missing_or_ambiguous"}]


def test_closure_frontier_preserved_and_no_execution_claim(rig, monkeypatch):
    monkeypatch.setattr(ac, "_closure", lambda *a: {"definitions": [], "truncated": True,
                        "frontier": ["unvisited:1"], "unresolved": [{"reference_id": "r:1", "resolution_status": "missing"}]})
    out = collect(rig)
    assert out["issues"][-2:] == [
        {"code": "reference_graph_truncated", "frontier": ["unvisited:1"]},
        {"code": "unresolved_reference", "reference_id": "r:1", "status": "missing"}]
    assert out["activation_closure_status"] == "pending" and out["action_dispatch"] == "pending"


@pytest.mark.parametrize("limit,value", [("roots", 2), ("definitions", 2)])
def test_root_and_definition_caps_reject_before_success(rig, monkeypatch, limit, value):
    monkeypatch.setitem(ac.ROOT_LIMITS, limit, value)
    with pytest.raises(cc.CapacityError, match="budget"):
        collect(rig)


def test_detached_return_does_not_mutate_original_records(rig):
    out = collect(rig)
    out["containers"]["skills"]["raw"].append(999)
    out["materials"]["AvatarSkillDepot:0"]["record"]["skills"].append(999)
    assert rig.depot["skills"] == [101, 102]


def test_oversized_level_profile_is_not_downgraded_to_pending(rig, monkeypatch):
    monkeypatch.setitem(ac.CATALOG_BUDGET, 'bytes', 64 * 1024)
    collect(rig)  # The normal fixture must fit this isolated test budget.
    def huge(*args, **kwargs):
        out = rig.level(*args, **kwargs)
        if kwargs["slot"] == "skill":
            out["original_description"] = "x" * (ac.CATALOG_BUDGET['bytes'] + 1)
        return out
    monkeypatch.setattr(ac, "get_runtime_talent_level", huge)
    with pytest.raises(cc.CapacityError) as failure:
        collect(rig)
    assert failure.value.code == "capacity_budget"


def test_oversized_passive_profile_is_not_downgraded_to_pending(rig, monkeypatch):
    monkeypatch.setitem(ac.CATALOG_BUDGET, 'bytes', 64 * 1024)
    collect(rig)
    def huge(*args, **kwargs):
        out = rig.passive(*args, **kwargs)
        out["original_description"] = "x" * (ac.CATALOG_BUDGET['bytes'] + 1)
        return out
    monkeypatch.setattr(ac, "get_runtime_passive_source_profile", huge)
    with pytest.raises(cc.CapacityError) as failure:
        collect(rig)
    assert failure.value.code == "capacity_budget"
