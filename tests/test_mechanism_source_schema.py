"""Native boundaries for generic source references; no manufactured ready compiler."""
import copy

import pytest
from test_mechanics_execution import fixture

from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.mechanics import enable_mechanics, prepare_mechanics
from genshin_app.equipment.mechanism_source_schema import (
    DEFAULT_SOURCE,
    PREFIX,
    mechanism_rule_id,
    mechanism_source_contract,
)
from genshin_app.equipment.schema import PlanError, new_plan, validate_plan


def source_plan():
    template, rotation, mechanics = fixture()
    plan = enable_mechanics(new_plan(template))
    plan.update(rotation_model=rotation, mechanics_model=mechanics)
    source = copy.deepcopy(DEFAULT_SOURCE)
    source.update(actor="a", mechanism_id="reviewed.example")
    plan["mechanism_sources"] = [source]
    return plan


def test_unknown_draft_and_old_plan_roundtrip_are_valid_and_unmodified():
    plan = source_plan()
    before = copy.deepcopy(plan)
    assert validate_plan(plan)["valid"]
    assert plan == before
    old = new_plan(fixture()[0])
    assert validate_plan(old)["valid"] and "mechanism_sources" not in old


@pytest.mark.parametrize("patch", [
    {"id": " "}, {"id": "x" * 65}, {"actor": ""}, {"actor": "x" * 129},
    {"mechanism_id": "Unreviewed"}, {"mechanism_id": "x" * 81},
    {"revision": True}, {"revision": 0}, {"revision": 2147483648},
    {"accepted_fingerprint": "A" * 64}, {"accepted_fingerprint": "a" * 63},
    {"version_line": "other"}, {"rules": []}, {"source_material": {}},
])
def test_strict_reference_fields(patch):
    plan = source_plan()
    plan["mechanism_sources"][0].update(patch)
    with pytest.raises(PlanError):
        validate_plan(plan)


@pytest.mark.parametrize("values", [{"all_party_alive": 1}, {"all_party_alive": False},
                                     {"unknown_switch": True}, {"stat_basis": "guessed"}])
def test_typed_scenario_rejects_bool_integer_alias_and_unknown_keys(values):
    plan = source_plan()
    plan["mechanism_sources"][0]["scenario"]["values"] = values
    with pytest.raises(PlanError):
        validate_plan(plan)


@pytest.mark.parametrize("field,value", [("target_id", " "), ("target_id", "t" * 129),
                                         ("source", "x" * 4097)])
def test_scenario_text_bounds(field, value):
    plan = source_plan()
    plan["mechanism_sources"][0]["scenario"][field] = value
    with pytest.raises(PlanError):
        validate_plan(plan)


def test_optional_field_is_v3_only_and_list_bounded_unique():
    plan = source_plan()
    for version in (1, 2):
        changed = copy.deepcopy(plan)
        changed["plan_version"] = version
        changed["mechanism_sources"] = []
        with pytest.raises(PlanError):
            validate_plan(changed)
    plan["mechanism_sources"] *= 2
    with pytest.raises(PlanError):
        validate_plan(plan)
    plan["mechanism_sources"] = [{**copy.deepcopy(plan["mechanism_sources"][0]), "id": f"s{i}"} for i in range(9)]
    with pytest.raises(PlanError):
        validate_plan(plan)


def test_caps_are_isolated_and_rule_ids_use_exact_pair():
    assert mechanism_rule_id("local", "piece") == PREFIX + fingerprint(["local", "piece"])[:24]
    assert mechanism_rule_id("a:b", "c") != mechanism_rule_id("a", "b:c")
    caps = mechanism_source_contract()
    caps["schema"]["properties"].clear()
    assert "actor" in mechanism_source_contract()["schema"]["properties"]


def test_reserved_id_rejected_even_without_declared_sources_but_cache_ignored():
    plan = source_plan()
    plan.pop("mechanism_sources")
    plan["manual_template"]["hits"][0]["id"] = PREFIX + "forged"
    with pytest.raises(PlanError, match="保留命名空间"):
        validate_plan(plan)
    plan = source_plan()
    plan["resolution"]["cached"] = {"id": PREFIX + "forged"}
    assert validate_plan(plan)["valid"]


def test_pending_namespace_tuning_never_enters_legacy_matching():
    plan = source_plan()
    plan["rotation_tuning"]["buff_hit_matches"] = [{"id": "tuning",
        "effect_ref": {"buff_id": mechanism_rule_id("mechanism-source", "benefit"), "effect_id": "v"}}]
    issues = []
    model, matches, previews, legacy = prepare_mechanics(
        plan, [], plan["manual_template"], "a" * 64, lambda *args: issues.append(args),
        mechanism_compilations=[{"id": "mechanism-source", "status": "pending"}])
    assert model["rules"] == matches == previews == legacy == []
    assert any(issue[0] == "mechanism_pending" for issue in issues)
