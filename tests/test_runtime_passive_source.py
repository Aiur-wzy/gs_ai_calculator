"""EC12 same-snapshot passive ownership and limited numerical qualification."""
from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures("isolated_cli_database_admission")

from genshin_db import runtime_queries
from genshin_db.equipment_queries import equipment_read_session
from genshin_db.runtime_catalog import RuntimeConfigError, dumps, fingerprint
from genshin_db.runtime_passive_source import (
    PASSIVE_LIMITS,
    get_passive_source_context,
    get_runtime_passive_source_profile,
    project_passive_source,
)
from genshin_runtime.passive_source_identity import (
    passive_source_fingerprint,
    passive_unlock_fingerprint,
)
from tests import fixture_candidate_builder as fixture_builder
from tests.test_pipeline import fake_repo, stub_fetcher  # noqa: F401

# ruff: noqa: F811


def change(repo, table, transform):
    path = repo / "ExcelBinOutput" / (table + "ExcelConfigData.json")
    value = json.loads(path.read_text("utf8"))
    transform(value)
    path.write_text(json.dumps(value, ensure_ascii=False), "utf8")


@pytest.fixture
def passive_db(tmp_path, fake_repo, stub_fetcher):
    change(fake_repo, "AvatarSkillDepot", lambda rows: rows[0].update(inherentProudSkillOpens=[
        {"proudSkillGroupId": 771, "needAvatarPromoteLevel": 4}, {"proudSkillGroupId": 772}, {}, {}]))
    change(fake_repo, "ProudSkill", lambda rows: rows.extend([
        {"proudSkillId": 77101, "proudSkillGroupId": 771, "level": 1, "breakLevel": 4,
         "paramList": [.0004, 8], "openConfig": "Fixture_Effect", "nameTextMapHash": 401,
         "descTextMapHash": 402, "unlockDescTextMapHash": 403},
        {"proudSkillId": 77201, "proudSkillGroupId": 772, "level": 1, "paramList": [1],
         "openConfig": "Fixture_Effect", "nameTextMapHash": 404}] ))
    change(fake_repo, "AvatarPromote", lambda rows: rows.extend([
        {"avatarPromoteId": 900, "unlockMaxLevel": 20},
        *[{"avatarPromoteId": 900, "promoteLevel": stage, "unlockMaxLevel": cap}
          for stage, cap in ((1, 40), (2, 50), (3, 60), (4, 70), (5, 80), (6, 90))]]))
    text_path = fake_repo / "TextMap/TextMap_MediumCHS.json"
    texts = json.loads(text_path.read_text("utf8"))
    texts.update({"401": "独立角色固有天赋", "402": "完整原文保留；此处不解释游戏行为。", "404": "缺省门槛不推零"})
    text_path.write_text(json.dumps(texts, ensure_ascii=False), "utf8")
    fixture_builder.build_database(tmp_path)
    return tmp_path / "genshin.db"


def query(db, **kwargs):
    return get_runtime_passive_source_profile(db, **{"character_id": 90000001, "proud_skill_id": 77101, **kwargs})


def test_discovery_does_not_select_or_claim_unlock(passive_db):
    out = query(passive_db, proud_skill_id=None)
    assert out["mode"] == "discovery" and out["status"] == "pending"
    assert out["source_bundle"] is None and out["unlock"]["status"] == "not_selected"
    assert [c["proud_skill_id"] for c in out["candidates"]] == [77101, 77201]
    assert [r["state"] for r in out["opening_inventory"]] == ["candidate", "candidate", "empty", "empty"]


def test_selected_complete_source_missing_text_and_limited_eligibility(passive_db):
    before = hashlib.sha256(passive_db.read_bytes()).hexdigest()
    out = query(passive_db, character_level=90, ascension=6)
    assert out["status"] == out["source_status"] == "ready"
    assert out["unlock"]["status"] == "eligible_under_declared_progression"
    assert out["unlock"]["complete_character_validity"] == "not_evaluated"
    assert out["descriptions"]["status"] == "partial"
    assert out["descriptions"]["unlock_description"]["text_origin"] is None
    assert out["descriptions"]["unlock_description"]["requested_pointer"] == "/403"
    assert out["selection"]["proud_level"] == 1
    source = out["source_bundle"]
    assert source["source_kind"] == "inherent_proud_skill" and source["reference_closed"]
    assert source["param_list"] == [.0004, 8] and source["talent"]["operations"]
    assert any(r["field_name"] == "openConfig" for r in source["references"])
    assert source["execution_support"] == out["execution_support"] == "not_evaluated"
    assert out["source_fingerprint"] == passive_source_fingerprint(out)
    assert out["unlock_fingerprint"] == passive_unlock_fingerprint(out)
    assert out["content_fingerprint"] == fingerprint({k: v for k, v in out.items() if k != "content_fingerprint"})
    assert hashlib.sha256(passive_db.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("level,ascension,status", [(None, None, "pending"), (90, None, "pending"),
    (None, 6, "pending"), (60, 3, "locked"), (60, 4, "eligible_under_declared_progression"),
    (90, 4, "invalid"), (1, 4, "eligible_under_declared_progression"), (1, 0, "pending"), (90, 77, "pending")])
def test_numerical_gate_does_not_claim_full_character_legality(passive_db, level, ascension, status):
    out = query(passive_db, character_level=level, ascension=ascension)
    assert out["source_status"] == "ready"
    assert out["unlock"]["status"] == status
    assert "minimum_character_level" in out["unlock"]["unchecked"]


@pytest.mark.parametrize("field,value", [("character_id", True), ("character_level", True), ("ascension", 1.0),
                                      ("ascension", -1), ("proud_skill_id", 0), ("skill_depot_id", "9001")])
def test_bad_input_rejected_before_missing_db(tmp_path, field, value):
    with pytest.raises(ValueError):
        query(tmp_path / "missing.db", **{field: value})


def test_unselected_progression_and_public_null_unknown_fields_rejected(passive_db):
    with pytest.raises(ValueError):
        query(passive_db, proud_skill_id=None, ascension=6)
    for extra in ({"ascension": None}, {"status": "confirmed"}, {"constellation": 0}, {"talent_level": 10}):
        with pytest.raises(ValueError):
            runtime_queries.query_runtime(passive_db, {"action": "passive_source_profile", "character_id": 90000001, **extra})


def test_exact_missing_and_foreign_proud_selections(passive_db, fake_repo):
    with pytest.raises(RuntimeConfigError) as exc:
        query(passive_db, proud_skill_id=999999)
    assert exc.value.reason == "not_found"
    change(fake_repo, "ProudSkill", lambda rows: rows.append({"proudSkillId": 99801, "proudSkillGroupId": 998, "level": 1}))
    fixture_builder.build_database(passive_db.parent, force=True)
    with pytest.raises(ValueError, match="不属于"):
        query(passive_db, proud_skill_id=99801)


def test_missing_threshold_keeps_source_ready_but_unlock_pending(passive_db):
    out = query(passive_db, proud_skill_id=77201, character_level=90, ascension=6)
    assert out["source_status"] == "ready" and out["unlock"]["status"] == "pending"
    assert out["unlock"]["requirement"]["required_ascension"] is None


@pytest.mark.parametrize("mutation", ["threshold_mismatch", "threshold_bool", "duplicate_stage", "missing_cap", "cap_bool"])
def test_source_numeric_uncertainty_is_separate_from_ability_closure(passive_db, fake_repo, mutation):
    if mutation.startswith("threshold"):
        change(fake_repo, "ProudSkill", lambda rows: rows[1].update(breakLevel=3 if mutation == "threshold_mismatch" else True))
    elif mutation == "duplicate_stage":
        change(fake_repo, "AvatarPromote", lambda rows: rows.append(copy.deepcopy(rows[-1])))
    else:
        change(fake_repo, "AvatarPromote", lambda rows: rows[-1].pop("unlockMaxLevel") if mutation == "missing_cap" else rows[-1].update(unlockMaxLevel=True))
    fixture_builder.build_database(passive_db.parent, force=True)
    out = query(passive_db, character_level=90, ascension=6)
    assert out["source_status"] == "ready" and out["unlock"]["status"] == "pending"


def test_source_pending_cannot_be_bypassed_by_high_progression(passive_db, fake_repo):
    change(fake_repo, "ProudSkill", lambda rows: rows[1].update(openConfig="Missing_Passive"))
    fixture_builder.build_database(passive_db.parent, force=True)
    out = query(passive_db, character_level=90, ascension=6)
    assert out["source_status"] == out["unlock"]["status"] == "pending"
    assert any(i["blocks_source"] for i in out["issues"])


def test_source_and_unlock_fingerprints_are_independent_of_request_and_display_noise(passive_db):
    out = query(passive_db, character_level=60, ascension=3)
    other = query(passive_db, character_level=90, ascension=6)
    assert out["source_fingerprint"] == other["source_fingerprint"]
    assert out["unlock_fingerprint"] != other["unlock_fingerprint"]
    copy_out = copy.deepcopy(out)
    copy_out["snapshot"]["built_at"] = "presentation changed"
    copy_out["source_bundle"]["snapshot"]["built_at"] = "presentation changed"
    copy_out["source_bundle"]["closure_fingerprint"] = "presentation-only old closure receipt"
    copy_out["issues"].append({"message": "translated diagnostic"})
    assert passive_source_fingerprint(copy_out) == out["source_fingerprint"]
    copy_out["source_bundle"]["talent"]["operations"][0]["unknown_added"] = True
    assert passive_source_fingerprint(copy_out) != out["source_fingerprint"]


def test_private_context_is_detached_and_source_cache_cannot_be_polluted(passive_db, monkeypatch):
    calls = []
    original = runtime_queries._closure
    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(runtime_queries, "_closure", counted)
    with equipment_read_session(passive_db):
        context = get_passive_source_context(passive_db, character_id=90000001, proud_skill_id=77101)
        out = project_passive_source(context, character_level=90, ascension=6)
        out["entry_records"]["proud_skill"]["record"]["breakLevel"] = 0
        same = query(passive_db, character_level=60, ascension=3)
        assert same["unlock"]["status"] == "locked"
        explicit = query(passive_db, skill_depot_id=9001, character_level=90, ascension=6)
        assert explicit["source_fingerprint"] == out["source_fingerprint"]
    assert len(calls) == 1
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: pytest.fail("late database access"))
    assert project_passive_source(context, character_level=90, ascension=6)["unlock"]["status"] == "eligible_under_declared_progression"


def test_fixed_table_decode_is_reused_across_distinct_selected_passives(passive_db, monkeypatch):
    from genshin_db.equipment_queries import borrow_read_cache
    from genshin_db.runtime_store import COMPONENT
    calls = []
    original = runtime_queries.decode_source
    def counted(row):
        path = row["source_path"]
        if path.endswith(("ProudSkillExcelConfigData.json", "AvatarPromoteExcelConfigData.json")):
            calls.append(path)
        return original(row)
    monkeypatch.setattr(runtime_queries, "decode_source", counted)
    with equipment_read_session(passive_db):
        query(passive_db)
        borrow_read_cache(passive_db, COMPONENT)[("release", "sources")].clear()
        query(passive_db, proud_skill_id=77201)
    assert len(calls) == 2


def test_snapshot_batch_caps_and_no_execution_upgrade(passive_db):
    request = {"action": "passive_source_profile", "character_id": 90000001, "proud_skill_id": 77101}
    one = runtime_queries.query_runtime(passive_db, request)
    batch = runtime_queries.query_runtime(passive_db, {"action": "batch", "items": [
        {"id": "missing-progression", "request": request},
        {"id": "locked", "request": {**request, "character_level": 60, "ascension": 3}}]})
    assert batch["status"] == "complete" and batch["items"][0]["data"] == one
    assert batch["items"][1]["data"]["unlock"]["status"] == "locked"
    caps = runtime_queries.runtime_capabilities()
    assert "passive_source_profile" in caps["batch"]["actions"]
    assert caps["required"]["passive_source_profile"] == ["character_id"]
    with pytest.raises(RuntimeConfigError) as exc:
        query(passive_db, snapshot_id="old-snapshot")
    assert exc.value.reason == "snapshot_changed"


def test_budgets_reject_without_partial_source(passive_db, monkeypatch):
    monkeypatch.setitem(PASSIVE_LIMITS, "max_bytes", 2000)
    with pytest.raises(RuntimeConfigError) as exc:
        query(passive_db)
    assert exc.value.status == 413


@pytest.mark.parametrize("limit", ["max_openings", "max_candidates", "max_promote_records", "max_operations", "max_abilities"])
def test_individual_source_limits_are_enforced(passive_db, monkeypatch, limit):
    monkeypatch.setitem(PASSIVE_LIMITS, limit, 0)
    with pytest.raises(RuntimeConfigError) as exc:
        query(passive_db)
    assert exc.value.status == 413


def test_duplicate_opening_does_not_pick_first(passive_db, fake_repo):
    change(fake_repo, "AvatarSkillDepot", lambda rows: rows[0]["inherentProudSkillOpens"].append(
        {"proudSkillGroupId": 771, "needAvatarPromoteLevel": 4}))
    fixture_builder.build_database(passive_db.parent, force=True)
    with pytest.raises(RuntimeConfigError) as exc:
        query(passive_db)
    assert exc.value.reason == "ambiguous_reference"


def test_boolean_group_cannot_match_integer_opening(passive_db, fake_repo):
    change(fake_repo, "AvatarSkillDepot", lambda rows: rows[0]["inherentProudSkillOpens"][0].update(proudSkillGroupId=1))
    change(fake_repo, "ProudSkill", lambda rows: rows[1].update(proudSkillGroupId=True))
    fixture_builder.build_database(passive_db.parent, force=True)
    with pytest.raises(RuntimeConfigError) as exc:
        query(passive_db)
    assert exc.value.reason == "dataset_invalid"


def test_depot_selection_is_explicit_when_multiple(passive_db, fake_repo):
    change(fake_repo, "Avatar", lambda rows: rows[0].update(candSkillDepotIds=[9001, 9002]))
    fixture_builder.build_database(passive_db.parent, force=True)
    with pytest.raises(RuntimeConfigError) as exc:
        query(passive_db)
    assert exc.value.reason == "skill_depot_selection_required"
    assert query(passive_db, skill_depot_id=9001)["status"] == "ready"
    with pytest.raises(ValueError):
        query(passive_db, skill_depot_id=9876)


def test_private_projector_rejects_client_dict():
    with pytest.raises(TypeError):
        project_passive_source({"source_view": {}}, character_level=90, ascension=6)


def test_real_source_fixture_and_pure_hashes_need_no_database(monkeypatch):
    from genshin_runtime.passive_source_identity import (
        passive_unlock_payload,
        stable_source_payload,
    )
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: pytest.fail("identity helper accessed DB"))
    source = json.loads((Path(__file__).parent / "fixtures/runtime_passive_kazuha.json").read_text("utf8"))
    assert source["source_fingerprint"] == passive_source_fingerprint(source)
    assert source["unlock_fingerprint"] == passive_unlock_fingerprint(source)
    assert source["source_bundle"]["parameter_record"]["record"]["paramList"][:2] == [.00039999998989515007, 8]
    source_payload, unlock_payload = stable_source_payload(source), passive_unlock_payload(source)
    assert "unlock" not in source_payload and "supplied" in unlock_payload
    source_payload["entry_records"]["avatar"]["record"].clear()
    unlock_payload["checks"].clear()
    assert source["entry_records"]["avatar"]["record"] and source["unlock"]["checks"]


def test_cli_summary_preserves_three_independent_states_without_echoing_raw(passive_db, tmp_path, capsys):
    from genshin_app.cli import main
    request, output = tmp_path / "passive-request.json", tmp_path / "passive-output.json"
    request.write_text(dumps({"action": "passive_source_profile", "character_id": 90000001,
                               "proud_skill_id": 77101, "character_level": 60, "ascension": 3}), "utf8")
    code = main(["--data-dir", str(passive_db.parent), "db", "runtime", "--input", str(request),
                 "--output", str(output), "--summary"])
    assert code == 0
    printed = capsys.readouterr().out
    out = json.loads(printed)["data"]
    assert out["source_status"] == "ready" and out["unlock"]["status"] == "locked"
    assert out["descriptions"]["status"] == "partial"
    assert "unknown_preserved" not in printed and "完整原文保留" not in printed
    assert len(printed.encode()) < 6000
    assert json.loads(output.read_text("utf8"))["source_bundle"]["talent"]["operations"]
