"""Author tests for generic data-driven templates; engineering values, no DB."""
import copy

import pytest

from genshin_engine.mechanics import execute_mechanics
from genshin_engine.mechanism_descriptor_compiler import compile_mechanism_descriptor
from genshin_runtime.mechanism_semantics import (
    descriptor_fingerprint,
    fact_fingerprint,
    fingerprint,
    parameter_fingerprint,
    target_location,
)
from tests.test_mechanics_execution import EVIDENCE, add, condition, fixture, rule
from tests.test_mechanics_swirl import setup


def case(kind="flat"):
    is_flat = kind != "swirl"
    key = "effect"
    consume = {"event": "hit", "phase": "pre", "elements": ["cryo"],
               "damage_type_ids": ["damage.normal_attack"] if kind == "def" else ["damage.normal_attack", "damage.charged_attack", "damage.plunging_attack", "damage.elemental_skill", "damage.elemental_burst"]}
    template = {"name_zh": "有限加算" if is_flat else "反应增伤",
        "trigger": {"event": "action_start", "phase": "pre"} if is_flat else
                   {"event": "elemental_reaction", "phase": "post", "reaction_types": ["swirl"], "reaction_elements": ["cryo"], "owner_presence": "any"},
        "beneficiaries": {"mode": "team", "scope": "locked"},
        "timing": {"cooldown": 0, "cooldown_anchor": "success"},
        "stacking": {"max": 1, "add": 1, "policy": "replace" if is_flat else "refresh"},
        "effects": [{"id": "value", "benefit_type_id": "benefit.base_add.all" if is_flat else "benefit.dmg_bonus.cryo",
             "domain": "provider" if is_flat else "self",
             "value": {"kind": "stat", "subject": "owner", "stat": "def" if kind == "def" else "atk" if is_flat else "em", "sample": "current" if is_flat else "trigger"}}]}
    if is_flat:
        template["effects"][0]["filter"] = copy.deepcopy(consume)
        template["quota"] = {"scope": "per_beneficiary", "refresh": "reset", "consume": consume}
    bindings = [{"target": "timing.duration", "parameter_ref": "duration"},
                {"target": "effects.value.value.factor", "parameter_ref": "factor"}]
    if is_flat:
        bindings.append({"target": "quota.capacity", "parameter_ref": "capacity"})
    item = {"key": key, "rule": template, "value_bindings": bindings,
        "execution_ports": [{"target": "owner", "role": "provider"},
            {"target": "trigger.action_ids" if is_flat else "trigger.characters", "role": "qualifying_actions" if is_flat else "provider_characters"},
            {"target": "timing.delay", "role": "activation_delay"}],
        "requirements": ["single_target", "fresh_empty_instance"] + ([] if is_flat else ["fixed_nonextra_em_equality", "no_dynamic_em"])}
    facts = [{"id": "rule", "revision": 1, "kind": "rule_template", "claim": copy.deepcopy(item),
              "evidence_tier": "experimental_reference", "evidence_refs": ["source"]}]
    values = {"duration": (15 if is_flat else 8, "seconds_exact_us", "seconds"),
              "factor": (.8218079805374146 if kind == "flat" else .25 if kind == "def" else .00039999998989515007, "finite_number", "ratio")}
    if is_flat:
        values["capacity"] = (7, "positive_integer", "count")
    parameters = []
    for ref, (value, value_type, unit) in values.items():
        parameters.append({"ref": ref, "value_type": value_type, "value": value, "unit": unit, "status": "ready",
            "origin_kind": "upstream_field", "evidence_ref": {"node_id": "source", "pointer": "/" + ref}, "derivation": "identity"})
        facts.append({"id": ref, "revision": 1, "kind": "parameter_role", "claim": {"ref": ref, "value_type": value_type, "unit": unit,
            "targets": [{"rule_key": key, "target": b["target"]} for b in bindings if b["parameter_ref"] == ref]},
            "evidence_tier": "experimental_reference", "evidence_refs": ["source"]})
    descriptor = {"schema_version": 1, "mechanism_id": "engineering." + kind, "revision": 1,
        "evidence_fact_ids": [f["id"] + "@1" for f in facts], "rule_templates": [item]}
    raw = {ref: v[0] for ref, v in values.items()}
    proof = {"schema_version": 1, "snapshot": {"version_line": "release", "snapshot_id": "a"*64,
        "upstream_commit": "b"*40, "indexer_version": "6", "normalization": None}, "subject": {"target_type": "character", "target_id": 1},
        "nodes": [{"id": "source", "source_path": "fixture.json", "source_sha256": fingerprint(raw), "json_pointer": "", "node_sha256": fingerprint(raw), "payload": raw}],
        "evidence": [{"id": "source", "kind": "runtime_node", "locator": "fixture.json#", "sha256": fingerprint(raw)}],
        "review": {"descriptor_status": "approved", "fact_statuses": {f["id"]+"@1": "approved" for f in facts}},
        "coverage": {"whole_source_status": "partial", "excluded_branches": ["engineering fixture, not client validation"], "unknown_branches": []},
        "descriptor_fingerprint": descriptor_fingerprint(descriptor),
        "fact_fingerprints": {f["id"]+"@1": fact_fingerprint(f) for f in facts},
        "parameter_fingerprints": {p["ref"]: parameter_fingerprint(p) for p in parameters}}
    return descriptor, {"source_proof": proof, "facts": facts, "parameters": parameters}


def bind(compiled):
    assert compiled["status"] == "ready", compiled["issues"]
    results = []
    for row in compiled["rule_templates"]:
        value = copy.deepcopy(row["rule"])
        value.update(id=row["key"], source_id="source:" + row["key"], **EVIDENCE)
        for port in compiled["execution_ports"]:
            if port["rule_key"] == row["key"]:
                obj, field = target_location(value, port["target"])
                obj[field] = {"provider": "a", "qualifying_actions": ["cast"], "provider_characters": ["a"], "activation_delay": 0}[port["role"]]
        results.append(value)
    return results


@pytest.mark.parametrize("kind", ["flat", "swirl", "def"])
def test_compiles_heterogeneous_data_without_identity_dispatch(kind):
    d, args = case(kind)
    original = copy.deepcopy((d, args))
    out = compile_mechanism_descriptor(d, **args)
    assert out["status"] == "ready", out["issues"]
    assert out["verification_scope"] == "materialized_consistency_only"
    assert out["execution_status"] == "not_evaluated"
    assert (d, args) == original
    assert "owner" not in out["rule_templates"][0]["rule"]


def test_actual_current_attack_consumption_seven_then_empty():
    d, args = case()
    t, r, m = fixture((.2, .3, .4, .5, .6, .7, .8, .9))
    add(m, *bind(compile_mechanism_descriptor(d, **args)))
    out = execute_mechanics(t, r, m)
    assert out["result"] is not None, out["issues"]
    assert [h["expected"] for h in out["result"]["hits"]] == pytest.approx([81.98135912418366]*7 + [45])
    assert [q["after"] for q in out["mechanics_trace"] if q["type"] == "quota" and q["cycle"] == 0] == [6, 5, 4, 3, 2, 1, 0, 0]


def test_actual_provider_current_changes_even_recipient_has_old_snapshot():
    d, args = case()
    t, r, m = fixture((.2, 1.2))
    for event in m["hit_events"]:
        event["self_snapshot_event"] = "cast"
    condition(m, "change", 1)
    add(m, *bind(compile_mechanism_descriptor(d, **args)), rule("change", "benefit.stat_add.atk", 100,
        trigger={"event": "condition", "phase": "pre", "event_ids": ["change"]}))
    out = execute_mechanics(t, r, m)
    assert [h["expected"] for h in out["result"]["hits"]] == pytest.approx([81.98135912418366, 118.96271824836731])


def test_actual_reaction_trigger_em_without_quota():
    d, args = case("swirl")
    t, r, m = setup((.2, .3, 1.2))
    t["characters"][0]["stats"]["em"] = 900
    for i in (1, 2):
        t["hits"][i]["element"] = "cryo"
        m["hit_events"][i]["application"] = {"profile_id": "none", "target_id": "enemy", "landed": True}
    m["elemental_model"]["profiles"].append({"id": "none", "element": "cryo",
        "application": "none", "status": "assumed", "source": "工程不附着"})
    add(m, *bind(compile_mechanism_descriptor(d, **args)))
    out = execute_mechanics(t, r, m)
    assert out["result"] is not None, out["issues"]
    normal = [h["expected"] for h in out["result"]["hits"] if h["damage_system"] == "classic"]
    assert normal == pytest.approx([45, 61.19999959075358, 61.19999959075358])
    assert not any(q["type"] == "quota" for q in out["mechanics_trace"])


def test_def_holdout_is_data_only_and_filters_normal():
    d, args = case("def")
    t, r, m = fixture((.2, .3))
    t["characters"][0]["stats"]["def"] = 200
    t["hits"][1]["damage_type_ids"] = ["damage.normal_attack"]
    add(m, *bind(compile_mechanism_descriptor(d, **args)))
    out = execute_mechanics(t, r, m)
    assert [h["expected"] for h in out["result"]["hits"]] == pytest.approx([45, 67.5])


def test_actual_two_heterogeneous_rules_coexist_and_swirl_does_not_consume_quota():
    t, r, m = setup((.2, .3, .4))
    t["characters"][0]["stats"]["em"] = 900
    m["elemental_model"]["profiles"].append({"id": "none", "element": "cryo", "application": "none", **EVIDENCE})
    for i in (1, 2):
        t["hits"][i]["element"] = "cryo"
        m["hit_events"][i]["application"] = {"profile_id": "none", "target_id": "enemy", "landed": True}
    for kind in ("flat", "swirl"):
        d, a = case(kind)
        rules = bind(compile_mechanism_descriptor(d, **a))
        for value in rules:
            value["id"] = kind
            value["source_id"] = "source:" + kind
        add(m, *rules)
    out = execute_mechanics(t, r, m)
    assert out["result"] is not None, out["issues"]
    normal = [h["expected"] for h in out["result"]["hits"] if h["damage_system"] == "classic"]
    expected = (100 + 100 * .8218079805374146) * .5 * .9 * (1 + 900 * .00039999998989515007)
    assert normal == pytest.approx([45, expected, expected])
    assert [q["after"] for q in out["mechanics_trace"] if q["type"] == "quota" and q["cycle"] == 0] == [6, 5]


@pytest.mark.parametrize("change", ["fact_missing", "fact_revoke", "unknown_branch", "owned_zero", "owned_null", "role_units", "wrong_raw", "missing_parameter", "unknown_port", "second_bad_rule"])
def test_atomic_failure_never_returns_partial_template(change):
    d, a = case()
    if change == "fact_missing": a["facts"].pop()
    if change == "fact_revoke": a["source_proof"]["review"]["fact_statuses"]["rule@1"] = "revoked"
    if change == "unknown_branch": a["source_proof"]["coverage"]["unknown_branches"] = ["unreviewed"]
    if change == "owned_zero": d["rule_templates"][0]["rule"]["timing"]["duration"] = 0
    if change == "owned_null": d["rule_templates"][0]["rule"]["quota"]["capacity"] = None
    if change == "role_units": a["parameters"][0]["unit"] = "count"
    if change == "wrong_raw": a["parameters"][0]["value"] = 12
    if change == "missing_parameter": a["parameters"].pop()
    if change == "unknown_port": d["rule_templates"][0]["execution_ports"][0]["role"] = "change_capacity"
    if change == "second_bad_rule": d["rule_templates"].append({"key": "bad"})
    out = compile_mechanism_descriptor(d, **a)
    assert out["status"] == "pending"
    assert out["rule_templates"] == out["initial_rule_templates"] == out["execution_ports"] == []


@pytest.mark.parametrize("value", [True, None, float("nan"), float("inf"), 10**400, .0000001])
def test_parameter_finite_types_and_microseconds(value):
    d, a = case()
    a["parameters"][0]["value"] = value
    assert compile_mechanism_descriptor(d, **a)["status"] == "pending"
