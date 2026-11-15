"""Author cases for the pure finite-hit compiler; sources are offline materialized JSON."""
import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest

from genshin_engine import runtime_limited_hit_benefit_compiler as compiler
from genshin_engine.mechanics import execute_mechanics
from genshin_runtime.limited_hit_benefit_identity import (
    fingerprint,
    limited_hit_benefit_source_payload,
)
from tests.test_mechanics_execution import (
    EVIDENCE,
    ability,
    action,
    add,
    condition,
    fixture,
    rule,
    second_character,
)


@pytest.fixture
def bundle():
    return json.loads(Path(__file__).with_name("fixtures").joinpath(
        "runtime_limited_hit_benefit_current.json").read_text("utf8"))


def old_bundle():
    return json.loads(Path(__file__).with_name("fixtures").joinpath(
        "runtime_limited_hit_benefit.json").read_text("utf8"))


def parent(bundle):
    selection = {k: bundle["selection"][k] for k in ("character_id", "skill_depot_id", "skill_id", "constellation")}
    return {"kind": "calibrated_single_contact_v1", "variant": "hold",
            "selection": {**selection, "talent_level": 10}, "param_index": bundle["param_index"],
            "profile_fingerprint": "a" * 64, "source_dependency_fingerprint": "b" * 64,
            "metadata_source_fingerprint": None, "metadata_dependency_fingerprint": None,
            "hit_semantics": {"damage_system": "classic", "scaling_stat": "atk", "count": 1,
                              "element": "cryo", "damage_type_ids": ["damage.elemental_skill"]},
            "application_semantics": {"element": "cryo", "gauge_units": 2, "icd": {"mode": "none"}},
            "contact_semantics": {"freeze_interaction": "none", "status": "assumed", "source": "工程接触"}}


def scenario():
    return {**compiler.limited_hit_benefit_compiler_caps()["accepted_scenario_values"],
            "status": "assumed", "source": "固定存活队伍，范围谓词成立，普通单目标且零卡肉"}


def calibration():
    return {"activation_offset_seconds": 0.1, "precision_seconds": 0.000001,
            "status": "assumed", "source": "独立工程时点；不代表客户端实测"}


def compile_source(bundle, **changes):
    args = {"parent_semantics": parent(bundle), "scenario": scenario(), "calibration": calibration()}
    args.update(changes)
    return compiler.compile_limited_hit_benefit_source(bundle, **args)


def bind(compiled, owner="a"):
    result = copy.deepcopy(compiled["rule_template"])
    result.update(id="quill", owner=owner, source_id="quill:source")
    result["trigger"]["action_ids"] = ["cast"]
    return result


def resign(bundle):
    bundle["semantic_dependency"] = limited_hit_benefit_source_payload(bundle)
    bundle["source_fingerprint"] = fingerprint(bundle["semantic_dependency"])
    bundle["content_fingerprint"] = fingerprint({k: v for k, v in bundle.items() if k != "content_fingerprint"})


def test_full_source_projects_unbound_one_pool_template(bundle):
    original = copy.deepcopy(bundle)
    out = compile_source(bundle)
    assert out["status"] == "ready", out["issues"]
    assert bundle == original
    assert out["whole_source_status"] == "partial"
    cert, template = out["benefit_certificate"], out["rule_template"]
    assert cert["coefficient"] == 0.8218079805374146
    assert cert["capacity"] == 7 and cert["duration_seconds"] == 15
    assert cert["activation"]["offset_ticks"] == 100000
    assert cert["activation"]["initial_hit_landed_required"] is False
    assert not {"id", "owner", "source_id"} & template.keys()
    assert "action_ids" not in template["trigger"]
    assert template["quota"]["scope"] == "per_beneficiary" and template["quota"]["refresh"] == "reset"
    assert template["effects"][0]["value"]["sample"] == "current"


def test_previous_accepted_pair_is_stale_and_is_not_resigned():
    historical = old_bundle()
    original = copy.deepcopy(historical)
    out = compile_source(historical)
    assert out["status"] == "pending"
    assert out["source_status"] == "pending"
    assert out["rule_template"] is None
    assert historical == original


@pytest.mark.parametrize("key,value", [("all_party_alive", False), ("all_party_alive", 1),
    ("initial_effect", "unknown"), ("hitlag_policy", "unknown"), ("status", "pending"), ("source", "   ")])
def test_unknown_scene_never_returns_template(bundle, key, value):
    scene = scenario()
    scene[key] = value
    out = compile_source(bundle, scenario=scene)
    assert out["source_status"] == "ready" and out["status"] == "pending"
    assert out["rule_template"] is None and out["benefit_fingerprint"] is None


@pytest.mark.parametrize("value", [None, True, -0.1, 0.0000001, float("inf"), float("nan"), 10**400])
def test_time_cannot_be_defaulted_or_rounded(bundle, value):
    value_input = calibration()
    value_input["activation_offset_seconds"] = value
    out = compile_source(bundle, calibration=value_input)
    assert out["status"] == "pending" and out["rule_template"] is None


@pytest.mark.parametrize("change", ["variant", "actor", "constellation_bool", "level", "param_bool", "count_bool", "count_two", "element", "metadata", "unknown_key"])
def test_parent_scope_is_strict(bundle, change):
    p = parent(bundle)
    if change == "variant": p["variant"] = "tap"
    if change == "actor": p["selection"]["character_id"] += 1
    if change == "constellation_bool": p["selection"]["constellation"] = False
    if change == "level": p["selection"]["talent_level"] = 13
    if change == "param_bool": p["param_index"] = True
    if change == "count_bool": p["hit_semantics"]["count"] = True
    if change == "count_two": p["hit_semantics"]["count"] = 2
    if change == "element": p["hit_semantics"]["element"] = "anemo"
    if change == "metadata": p["metadata_source_fingerprint"] = "c" * 64
    if change == "unknown_key": p["pretend_verified"] = True
    out = compile_source(bundle, parent_semantics=p)
    assert out["status"] == "pending" and out["rule_template"] is None


@pytest.mark.parametrize("field", ["source_fingerprint", "content_fingerprint"])
def test_source_hash_mismatch_is_rejected(bundle, field):
    bundle[field] = "0" * 64
    assert compile_source(bundle)["source_status"] == "pending"


def test_resigned_projection_echo_cannot_change_capacity(bundle):
    bundle["projection"]["capacity"]["value"] = 8
    resign(bundle)
    out = compile_source(bundle)
    assert out["status"] == "pending" and out["rule_template"] is None


@pytest.mark.parametrize("change", ["missing_modifier", "modifier_owner", "unknown_ability_field", "writer_flag_type"])
def test_complete_source_structure_remains_mandatory_after_resigning(bundle, change):
    graph = bundle["source_bundle"]
    entries = {e["definition"]["definition_id"]: e for e in graph["entries"]}
    if change == "missing_modifier":
        ident = graph["entry_ids"]["modifiers"].pop()
        graph["entries"] = [e for e in graph["entries"] if e["definition"]["definition_id"] != ident]
    elif change == "modifier_owner":
        entries[graph["entry_ids"]["modifiers"][0]]["definition"]["owner_definition_id"] = "0" * 64
    else:
        entry = entries[graph["entry_ids"]["ability" if change == "unknown_ability_field" else "talent"]]
        if change == "unknown_ability_field":
            entry["payload"]["unreviewedControl"] = True
        else:
            entry["payload"][0]["GBPFBNHLOFD"] = 1
        entry["node_sha256"] = entry["definition"]["node_sha256"] = fingerprint(entry["payload"])
    resign(bundle)
    out = compile_source(bundle)
    assert out["source_status"] == "pending" and out["rule_template"] is None


def test_all_three_parameters_are_data_driven_and_not_character_constants(bundle):
    # Synthetic changed materialized Proud row, not a claim about another live snapshot.
    entries = {e["definition"]["definition_id"]: e for e in bundle["source_bundle"]["entries"]}
    entry = entries[bundle["source_bundle"]["entry_ids"]["proud_skill"]]
    for key, value in (("coefficient", 0.25), ("duration_seconds", 12), ("capacity", 3.0)):
        field = bundle["projection"][key]
        entry["payload"]["paramList"][field["origin"]["param_index"]] = value
        field["value"] = value
    entry["node_sha256"] = entry["definition"]["node_sha256"] = fingerprint(entry["payload"])
    resign(bundle)
    out = compile_source(bundle)
    assert out["status"] == "ready", out["issues"]
    template = out["rule_template"]
    assert template["effects"][0]["value"]["factor"] == 0.25
    assert template["timing"]["duration"] == 12
    assert template["quota"]["capacity"] == 3 and type(template["quota"]["capacity"]) is int


@pytest.mark.parametrize("limit", ["max_source_bytes", "max_parent_bytes", "max_declaration_bytes", "max_output_bytes"])
def test_budget_failure_withdraws_executable_template(bundle, monkeypatch, limit):
    monkeypatch.setitem(compiler.LIMITS, limit, 1)
    out = compile_source(bundle)
    assert out["status"] == "pending" and out["rule_template"] is None
    assert out["benefit_certificate"] is None and out["semantic_dependency"] is None


def test_results_and_caps_are_not_mutable_shared_cache(bundle):
    before = compile_source(bundle)
    baseline = copy.deepcopy(before)
    before["rule_template"]["effects"][0]["value"]["factor"] = 99
    before["semantic_dependency"]["scenario"]["all_party_alive"] = False
    caps = compiler.limited_hit_benefit_compiler_caps()
    caps["accepted_scenario_values"]["all_party_alive"] = False
    assert compile_source(bundle) == baseline
    assert compiler.limited_hit_benefit_compiler_caps()["accepted_scenario_values"]["all_party_alive"] is True


def test_compiled_rule_consumes_seven_then_stops_with_independent_decimal_oracle(bundle):
    t, r, m = fixture(tuple(round(0.2 + i * 0.1, 6) for i in range(8)))
    t["characters"][0]["stats"]["atk"] = 2000
    add(m, bind(compile_source(bundle)))
    out = execute_mechanics(t, r, m)
    assert out["result"] is not None, out["issues"]
    # Provider is also recipient here: the ordinary base is 2000, not 100.
    expected = float((Decimal(2000) + Decimal('1643.6159610748292')) * Decimal('0.45'))
    assert [h["expected"] for h in out["result"]["hits"]] == pytest.approx([expected] * 7 + [900])
    receipts = [x for x in out["mechanics_trace"] if x["type"] == "quota" and x["cycle"] == 0]
    assert [x["after"] for x in receipts] == [6, 5, 4, 3, 2, 1, 0, 0]


def test_compiled_provider_current_does_not_resnapshot_receiver(bundle):
    t, r, m = fixture((0.2, 1.2))
    second_character(t, r)
    t["characters"][1]["stats"]["atk"] = 2000
    for descriptor in m["hit_events"]:
        descriptor["self_snapshot_event"] = "cast"
    condition(m, "change", 1)
    change = {"event": "condition", "phase": "pre", "event_ids": ["change"]}
    add(m, bind(compile_source(bundle), owner="b"),
        rule("provider_atk", "benefit.stat_add.atk", 500, trigger=change,
             beneficiaries={"mode": "characters", "characters": ["b"], "scope": "locked"}),
        rule("receiver_atk", "benefit.stat_add.atk", 900, trigger=change,
             beneficiaries={"mode": "characters", "characters": ["a"], "scope": "locked"}))
    out = execute_mechanics(t, r, m)
    assert out["result"] is not None, out["issues"]
    assert [h["expected"] for h in out["result"]["hits"]] == pytest.approx(
        [784.62718248367314, 969.533978104591425])
    samples = [x for x in out["sampling_trace"] if x["rule_id"] == "quill" and x["cycle"] == 0]
    assert [x["source_value"] for x in samples] == [2000, 2500]


def test_refresh_resets_one_shared_rule_but_preserves_individual_pools(bundle):
    t, r, m = fixture((0.2, 1.2, 2.6, 3.2))
    second_character(t, r)
    t["hits"][1]["character"] = "b"
    r["abilities"].extend([ability("ab", "a", "action.switch"), ability("be", "b"),
                            ability("ba", "b", "action.switch")])
    r["actions"].extend([action("switch_b", "ab", 0.8, 0.81, switch_to="b"),
                         action("b_cast", "be", 1, 1.01),
                         action("switch_a", "ba", 2, 2.01, switch_to="a"),
                         action(aid="recast", start=3, end=3.1, order=1)])
    m["hit_events"][1]["action_id"] = "b_cast"
    compiled_rule = bind(compile_source(bundle))
    compiled_rule["trigger"]["action_ids"].append("recast")
    add(m, compiled_rule)
    out = execute_mechanics(t, r, m)
    assert out["result"] is not None, out["issues"]
    receipts = [x for x in out["mechanics_trace"] if x["type"] == "quota" and x["cycle"] == 0]
    assert [(x["pool"], x["before"], x["after"]) for x in receipts] == [
        ("a", 7, 6), ("b", 7, 6), ("a", 6, 5), ("a", 7, 6)]
    activations = [x for x in out["mechanics_trace"] if x["type"] == "activation" and x["cycle"] == 0]
    assert [x["quota"] for x in activations] == [{"a": 7, "b": 7}, {"a": 7, "b": 7}]


def test_miss_keeps_cast_activation_while_icd_blocked_damage_consumes(bundle):
    t, r, m = fixture((0.2, 0.3, 0.4))
    m["elemental_model"] = {"schema_version": 1, "ruleset": "gauge-basic-v1",
        "target": {"id": "enemy", "kind": "ordinary", "knowledge": "known", "initial_aura": None, **EVIDENCE},
        "profiles": [{"id": "cryo", "element": "cryo", "application": "apply", "gauge_units": 1,
                      "icd": {"kind": "standard", "group": "same"}, **EVIDENCE}]}
    for index, item in enumerate(m["hit_events"]):
        item["application"] = {"profile_id": "cryo", "target_id": "enemy", "landed": index != 0}
    add(m, bind(compile_source(bundle)))
    out = execute_mechanics(t, r, m)
    assert out["result"] is not None, out["issues"]
    assert len(out["result"]["hits"]) == 2
    rows = [x for x in out["mechanics_trace"] if x["type"] == "quota" and x["cycle"] == 0]
    assert [x["after"] for x in rows] == [6, 5]
    assert [x["reason"] for x in out["elemental_trace"][:3]] == ["missed", "first_hit", "icd_blocked"]
    activation = next(x for x in out["mechanics_trace"] if x["type"] == "activation")
    assert activation["t"] == 0.1
