"""The action-facts contract preserves uncertainty and exact source identity."""
import hashlib
import json

import pytest

from genshin_db.runtime_catalog import RuntimeConfigError
from genshin_db.runtime_queries import query_runtime, runtime_capabilities
from tests import fixture_candidate_builder as fixture_builder
from tests.test_pipeline import fake_repo, stub_fetcher  # noqa: F401

# ruff: noqa: F811


@pytest.fixture
def action_db(tmp_path, fake_repo, stub_fetcher):
    def read(name):
        path = fake_repo / f"ExcelBinOutput/{name}ExcelConfigData.json"
        return path, json.loads(path.read_text("utf8"))

    path, rows = read("AvatarSkill")
    rows[0].update(abilityName="Fixture_Ability", triggerID=2)
    path.write_text(json.dumps(rows), "utf8")
    path, rows = read("ProudSkill")
    rows = [{"proudSkillId": 910010 + level, "proudSkillGroupId": 91001, "level": level,
             "paramList": [value], "paramDescList": [201], "openConfig": "Fixture_Effect"}
            for level, value in ((10, 0.5), (13, 0.8))]
    path.write_text(json.dumps(rows), "utf8")
    fixture_builder.build_database(tmp_path)
    return tmp_path / "genshin.db"


def facts(path, **kwargs):
    return query_runtime(path, {"action": "action_facts", "character_id": 90000001, "slot": "normal", **kwargs})


def test_exact_inputs_are_not_execution_and_db_is_unchanged(action_db):
    before = hashlib.sha256(action_db.read_bytes()).hexdigest()
    result = facts(action_db)
    assert result["status"] == "ready"
    assert result["action_dispatch"]["status"] == "pending"
    assert result["execution_support"] == "not_evaluated"
    assert result["level"]["effective_level"] == 10
    assert result["parameter_bindings"][0]["parameter_input"] == 0.5
    assert result["parameter_bindings"][0]["operation_semantics"] == "not_evaluated"
    assert result["parameter_bindings"][0]["target"]["relation"] == "parameter_modification_target"
    assert result["talents"][0]["operations_raw"][0]["unknown_preserved"] is False
    assert result["talents"][0]["execution_support"] == "not_evaluated"
    assert result["records"]["skill"]["field_pointers"]["triggerID"] == "/0/triggerID"
    assert result["direct_skill_ability"]["resolution_status"] == "resolved"
    assert hashlib.sha256(action_db.read_bytes()).hexdigest() == before
    assert "action_facts" in runtime_capabilities()["actions"]


def test_nonzero_constellation_requires_explicit_effective_level(action_db):
    pending = facts(action_db, constellation=1)
    assert pending["status"] == "pending"
    assert pending["level"]["effective_level"] is None
    assert any(i["code"] == "effective_talent_level_required" for i in pending["issues"])
    explicit = facts(action_db, constellation=1, talent_level=13)
    assert explicit["level"]["effective_level"] == 13
    assert explicit["level"]["constellation_applied"] is False
    assert explicit["parameter_bindings"][0]["parameter_input"] == 0.8


@pytest.mark.parametrize("fields", [
    {"skill_id": 90011}, {"slot": "E"}, {"slot": []}, {"talent_level": 0}, {"talent_level": 16},
    {"talent_level": True}, {"constellation": -1}, {"constellation": 7}, {"character_id": True},
    {"skill_depot_id": 42}, {"unknown": True}, {"talent_level": None},
])
def test_selection_and_type_bounds(action_db, fields):
    with pytest.raises(ValueError):
        facts(action_db, **fields)


def test_skill_id_requires_membership_and_preserves_slot_evidence(action_db):
    request = {"action": "action_facts", "character_id": 90000001, "skill_id": 90011}
    assert query_runtime(action_db, request)["selection"]["matching_slots"] == ["normal"]
    with pytest.raises(ValueError, match="不属于"):
        query_runtime(action_db, {**request, "skill_id": 777})
    with pytest.raises(RuntimeConfigError) as exc:
        facts(action_db, slot="skill")
    assert exc.value.reason == "empty_skill_slot"


def test_multiple_depots_are_not_silently_selected(action_db, fake_repo):
    path = fake_repo / "ExcelBinOutput/AvatarExcelConfigData.json"
    rows = json.loads(path.read_text("utf8"))
    rows[0]["candSkillDepotIds"] = [9001, 9002]
    path.write_text(json.dumps(rows), "utf8")
    fixture_builder.build_database(action_db.parent, force=True)
    with pytest.raises(RuntimeConfigError) as exc:
        facts(action_db)
    assert exc.value.reason == "skill_depot_selection_required"
    assert facts(action_db, skill_depot_id=9001)["status"] == "ready"


def test_variant_ambiguity_and_response_bounds(action_db, fake_repo, monkeypatch):
    from genshin_db.runtime_action_facts import ACTION_FACT_LIMITS
    path = fake_repo / "BinOutput/Ability/Temp/EquipAbilities/Fixture.json"
    rows = json.loads(path.read_text("utf8"))
    rows.append({"Alternate": rows[0]["Default"]})
    path.write_text(json.dumps(rows), "utf8")
    fixture_builder.build_database(action_db.parent, force=True)
    result = facts(action_db)
    assert result["status"] == "pending"
    assert result["direct_skill_ability"]["resolution_status"] == "ambiguous"
    assert len(result["ability_candidates"]) == 2
    monkeypatch.setitem(ACTION_FACT_LIMITS, "max_bytes", 100)
    with pytest.raises(RuntimeConfigError) as exc:
        facts(action_db)
    assert exc.value.reason == "response_too_large"


def test_snapshot_mismatch_and_missing_level_never_fall_back(action_db):
    with pytest.raises(RuntimeConfigError) as exc:
        facts(action_db, snapshot_id="wrong")
    assert exc.value.reason == "snapshot_changed"
    result = facts(action_db, talent_level=12)
    assert result["level"]["effective_level"] == 12
    assert "proud_skill" not in result["records"]
    assert any(i["code"] == "missing_parameter_record" for i in result["issues"])


def test_unresolved_issue_limit_is_not_silently_truncated(action_db, monkeypatch):
    from genshin_db.runtime_action_facts import ACTION_FACT_LIMITS
    monkeypatch.setitem(ACTION_FACT_LIMITS, "max_issues", 0)
    with pytest.raises(RuntimeConfigError) as exc:
        facts(action_db, constellation=1)
    assert exc.value.reason == "response_too_large"
