"""EC22 author tests: raw identity + typed casts, no DB/game timing claims."""
import copy
import json
from pathlib import Path

import pytest

from genshin_engine.mechanics import execute_mechanics
from genshin_engine.mechanism_descriptor_compiler import (
    compile_mechanism_descriptor,
    mechanism_descriptor_capabilities,
)
from genshin_runtime.mechanism_qualification import (
    validate_execution_qualification,
    verify_execution_qualification,
)
from genshin_runtime.mechanism_semantics import (
    fact_fingerprint,
    fingerprint,
    parameter_fingerprint,
    validate_fact,
)
from tests.test_mechanics_execution import add, fixture
from tests.test_mechanism_descriptor_compiler import bind, case
from tests.test_mechanism_qualification import claim, qualified_case, refresh


def cast_case(equipment=False, phase="pre"):
    """Fixed real callback nodes with engineering review/parameters, no DB approval."""
    d, args = case()
    p = args["source_proof"]
    saved = json.loads((Path(__file__).parent / "fixtures/mechanism_cast_nodes.json").read_text(encoding="utf-8"))
    ids = {"noblesse.ability", "noblesse.set", "noblesse.affix"} if equipment else {"hold.ability"}
    for n in saved["nodes"]:
        if n["id"] not in ids:
            continue
        p["nodes"].append({**{k: n[k] for k in ("id", "source_path", "source_sha256", "json_pointer", "node_sha256")}, "payload": n["raw"]})
        p["evidence"].append({"id": n["id"], "kind": "runtime_node", "locator": n["source_path"] + "#" + n["json_pointer"], "sha256": n["node_sha256"]})
    q = claim()
    q.update(schema_version=2, provider={"relation": "source_character"},
             target_dependency={"mode": "ordinary_single_target_reference", "ruleset": "gauge-freeze-reference-v1"})
    q["parent"] = {"profile_kind": "successful_action_v1",
        "selection": {"slot": "burst" if equipment else "skill", "constellation": 0},
        "required_receipts": ["cast_identity"],
        "callback_bridge": {"kind": "owner_burst_listener", "node_id": "noblesse.ability",
            "callback_pointer": "/FBHJJNMDIPC/0/BODPAABHKGE", "apply_pointers": ["/FBHJJNMDIPC/0/BODPAABHKGE/1"]} if equipment else
            {"kind": "skill_start_callback", "node_id": "hold.ability", "callback_pointer": "/ECFFDLHMCLK",
             "skill_id_pointer": "/ECFFDLHMCLK/0/ACFKMLJKONM", "apply_pointers": ["/ECFFDLHMCLK/5"]},
        "activation": {"phase": phase, "offset_policy": "calibrated", "cancellation": "reject_before_activation"}}
    item = d["rule_templates"][0]
    item["rule"]["trigger"]["phase"] = phase
    if equipment:
        p["subject"] = {"target_type": "artifact_set", "target_id": 15007}
        q["provider"] = {"relation": "equipped_artifact_set", "set_node_id": "noblesse.set", "affix_node_id": "noblesse.affix",
            "required_count": 4, "affix_id": 2150071, "source_level": 1, "requirement_coverage": "complete_selected_effect_reference"}
        q["target_dependency"] = {"mode": "none"}
        q["consumer"] = None
        q["scenario_requirements"].pop("target_kind")
        q["duplicate_identity"] = {"kind": "artifact_set", "source_id": 15007, "parts": ["team_stat"]}
        item["requirements"].remove("single_target")
        item["rule"].pop("quota")
        item["rule"]["effects"] = [{"id": "value", "benefit_type_id": "benefit.stat_pct.atk", "domain": "self"}]
        item["value_bindings"] = [{"target": "timing.duration", "parameter_ref": "duration"}, {"target": "effects.value.value", "parameter_ref": "factor"}]
        args["facts"] = [f for f in args["facts"] if f["id"] != "capacity"]
        args["parameters"] = [v for v in args["parameters"] if v["ref"] != "capacity"]
        for v in args["parameters"]:
            index = 0 if v["ref"] == "factor" else 1
            v["value"] = .20000000298023224 if index == 0 else 12
            v["evidence_ref"] = {"node_id": "noblesse.affix", "pointer": "/paramList/" + str(index)}
        for f in args["facts"]:
            if f["id"] == "factor":
                f["claim"]["targets"] = [{"rule_key": "effect", "target": "effects.value.value"}]
    else:
        q["parent"]["selection"].update(skill_id=10632, talent_level=10, variant="hold")
        q["duplicate_identity"]["source_id"] = 10632
    q["coverage"] = {"mode": "all_provider_actions", "action_type_ids": ["action.elemental_burst" if equipment else "action.elemental_skill"], "skill_scope": "parent_selection"}
    args["facts"][0]["claim"] = copy.deepcopy(item)
    args["facts"].append({"id": "qualification", "revision": 1, "kind": "execution_qualification", "claim": q,
        "evidence_tier": "experimental_reference", "evidence_refs": sorted(ids)})
    p["parameter_fingerprints"] = {v["ref"]: parameter_fingerprint(v) for v in args["parameters"]}
    refresh(d, args)
    return d, args


def qualification(args):
    return args["facts"][-1]["claim"]


def rehash_node(args, node_id):
    p = args["source_proof"]
    node = next(n for n in p["nodes"] if n["id"] == node_id)
    node["node_sha256"] = fingerprint(node["payload"])
    next(e for e in p["evidence"] if e["id"] == node_id)["sha256"] = node["node_sha256"]
    return node["payload"]


@pytest.mark.parametrize("equipment,phase", [(False, "pre"), (False, "post"), (True, "pre"), (True, "post")])
def test_real_callback_shapes_compile_without_dummy_hit(equipment, phase):
    d, a = cast_case(equipment, phase)
    before = copy.deepcopy((d, a))
    out = compile_mechanism_descriptor(d, **a)
    assert out["status"] == "ready", out["issues"]
    assert out["compiler"] == "typed-mechanism-d20-v2"
    assert out["compiler_version"] == 2
    assert out["execution_qualification"] == qualification(a)
    assert "qualified_cast_origin" in out["required_execution_checks"]
    assert "activation_within_cast_window" in out["required_execution_checks"]
    assert "activation_before_initial_hit" not in out["required_execution_checks"]
    assert ("single_target" in out["required_execution_checks"]) is not equipment
    assert out["execution_status"] == "not_evaluated"
    assert out["verification_scope"] == "materialized_consistency_only"
    assert before == (d, a)


def test_targetless_team_stat_executes_in_existing_d20_without_elemental_model():
    d, a = cast_case(True, "post")
    t, r, m = fixture((.2, .3))
    assert "elemental_model" not in m
    t["characters"][0]["stats"]["base_atk"] = 100
    add(m, *bind(compile_mechanism_descriptor(d, **a)))
    out = execute_mechanics(t, r, m)
    assert out["result"] is not None, out["issues"]
    assert [h["expected"] for h in out["result"]["hits"]] == pytest.approx([54.00000013411045] * 2)


def test_complete_and_partial_affix_coverage_are_distinct_reviewed_claims():
    d, a = cast_case(True)
    complete = compile_mechanism_descriptor(d, **a)
    qualification(a)["provider"]["requirement_coverage"] = "partial"
    refresh(d, a)
    partial = compile_mechanism_descriptor(d, **a)
    assert partial["status"] == "ready"
    assert partial["execution_qualification"]["provider"]["requirement_coverage"] == "partial"
    assert partial["mechanism_fingerprint"] != complete["mechanism_fingerprint"]


@pytest.mark.parametrize("path,value", [
    (("schema_version",), True), (("schema_version",), 3),
    (("parent", "profile_kind"), "calibrated_single_contact_v1"),
    (("parent", "selection", "constellation"), False),
    (("parent", "selection", "skill_id"), True),
    (("parent", "selection", "slot"), "normal"),
    (("parent", "parameter_binding"), None),
    (("parent", "required_receipts"), ["action_event"]),
    (("parent", "callback_bridge", "kind"), "any_callback"),
    (("parent", "callback_bridge", "apply_pointers"), ["/ECFFDLHMCLK/5", "/ECFFDLHMCLK/5"]),
    (("parent", "callback_bridge", "apply_pointers"), ["/ECFFDLHMCLK2/5"]),
    (("parent", "callback_bridge", "callback_pointer"), ""),
    (("parent", "callback_bridge", "skill_id_pointer"), "/ECFFDLHMCLK/0/~2bad"),
    (("parent", "activation", "phase"), "after_hit"),
    (("parent", "activation", "offset_policy"), "auto"),
    (("parent", "activation", "cancellation"), "ignore"),
    (("provider", "relation"), "equipped_weapon"),
    (("target_dependency", "ruleset"), "other"),
    (("coverage", "action_type_ids"), ["action.elemental_burst"]),
    (("coverage", "mode"), "selected_actions_only"),
])
def test_cast_schema_rejects_unknown_or_old_contact_shortcuts(path, value):
    _, a = cast_case()
    q = qualification(a)
    obj = q
    for key in path[:-1]:
        obj = obj[key]
    obj[path[-1]] = value
    with pytest.raises(ValueError):
        validate_execution_qualification(q)


@pytest.mark.parametrize("change", ["different_skill", "bool_skill", "missing_callback", "missing_apply", "non_operation",
                                    "node_evidence_missing", "node_hash", "locator", "wrong_subject", "reaction_rule", "phase",
                                    "consumer_missing", "delay_fixed_but_port", "same_tick_not_zero"])
def test_bridge_and_rule_mismatch_with_self_consistent_hashes_fails(change):
    d, a = cast_case()
    q = qualification(a)
    raw = next(n["payload"] for n in a["source_proof"]["nodes"] if n["id"] == "hold.ability")
    if change == "different_skill": raw["ECFFDLHMCLK"][0]["ACFKMLJKONM"] = 777
    if change == "bool_skill": raw["ECFFDLHMCLK"][0]["ACFKMLJKONM"] = True
    if change == "missing_callback": raw["ECFFDLHMCLK"] = []
    if change == "missing_apply": q["parent"]["callback_bridge"]["apply_pointers"] = ["/ECFFDLHMCLK/1000"]
    if change == "non_operation": raw["ECFFDLHMCLK"][5] = 3
    rehash_node(a, "hold.ability")
    if change == "node_evidence_missing": a["source_proof"]["evidence"] = [e for e in a["source_proof"]["evidence"] if e["id"] != "hold.ability"]
    if change == "node_hash": a["source_proof"]["nodes"][-1]["node_sha256"] = "0" * 64
    if change == "locator": a["source_proof"]["evidence"][-1]["locator"] += "/other"
    if change == "wrong_subject": a["source_proof"]["subject"]["target_type"] = "artifact_set"
    if change == "reaction_rule":
        d["rule_templates"][0]["rule"]["trigger"] = {"event": "elemental_reaction", "phase": "post", "reaction_types": ["swirl"], "reaction_elements": ["cryo"]}
        d["rule_templates"][0]["execution_ports"][1] = {"target": "trigger.characters", "role": "provider_characters"}
    if change == "phase": q["parent"]["activation"]["phase"] = "post"
    if change == "consumer_missing": q["consumer"] = None
    if change in ("delay_fixed_but_port", "same_tick_not_zero"):
        q["parent"]["activation"]["offset_policy"] = "fixed_zero"
        if change == "same_tick_not_zero":
            d["rule_templates"][0]["execution_ports"].pop()
            d["rule_templates"][0]["rule"]["timing"]["delay"] = .1
    a["facts"][0]["claim"] = copy.deepcopy(d["rule_templates"][0])
    refresh(d, a)
    out = compile_mechanism_descriptor(d, **a)
    assert out["status"] == "pending"
    assert out["rule_templates"] == out["initial_rule_templates"] == out["execution_ports"] == []


@pytest.mark.parametrize("node,field,value", [
    ("noblesse.set", "setId", 12), ("noblesse.set", "EquipAffixId", 3),
    ("noblesse.set", "setNeedNum", [2, 3]), ("noblesse.set", "setNeedNum", [2, True]),
    ("noblesse.affix", "level", True), ("noblesse.affix", "level", 0),
    ("noblesse.affix", "affixId", 3), ("noblesse.affix", "id", False),
])
def test_complete_equipment_chain_not_equal_number_or_type(node, field, value):
    d, a = cast_case(True)
    raw = next(n["payload"] for n in a["source_proof"]["nodes"] if n["id"] == node)
    raw[field] = value
    rehash_node(a, node)
    out = compile_mechanism_descriptor(d, **a)
    assert out["status"] == "pending"


@pytest.mark.parametrize("change", ["single_target_item", "single_target_fact", "effect_filter", "enemy", "base_add", "consumer", "target_scene", "quota"])
def test_targetless_does_not_erase_actual_target_dependency(change):
    d, a = cast_case(True)
    item = d["rule_templates"][0]
    if change == "single_target_item": item["requirements"].append("single_target")
    if change == "single_target_fact":
        a["facts"].insert(0, {"id": "target", "revision": 1, "kind": "execution_requirement", "claim": {"requirement": "single_target"}, "evidence_tier": "experimental_reference", "evidence_refs": ["source"]})
    if change == "effect_filter": item["rule"]["effects"][0]["filter"] = {"event": "hit", "phase": "pre"}
    if change == "enemy": item["rule"]["effects"][0].update(domain="enemy", benefit_type_id="benefit.res_shred.cryo")
    if change == "base_add": item["rule"]["effects"][0]["benefit_type_id"] = "benefit.dmg_bonus.all"
    if change in ("consumer", "target_scene"):
        q = qualification(a)
        if change == "consumer": q["consumer"] = claim()["consumer"]
        else: q["scenario_requirements"]["target_kind"] = "ordinary_non_animal_monster"
        with pytest.raises(ValueError): validate_execution_qualification(q)
        return
    if change == "quota":
        item["rule"]["quota"] = {"capacity": 1, "scope": "team", "refresh": "reset", "consume": {"event": "hit", "phase": "pre"}}
    next(f for f in a["facts"] if f["kind"] == "rule_template")["claim"] = copy.deepcopy(item)
    refresh(d, a)
    assert compile_mechanism_descriptor(d, **a)["status"] == "pending"


def test_zero_delay_is_reviewed_fixed_value_not_hidden_calibration():
    d, a = cast_case()
    qualification(a)["parent"]["activation"]["offset_policy"] = "fixed_zero"
    item = d["rule_templates"][0]
    item["execution_ports"].pop()
    item["rule"]["timing"]["delay"] = 0
    a["facts"][0]["claim"] = copy.deepcopy(item)
    refresh(d, a)
    assert compile_mechanism_descriptor(d, **a)["status"] == "ready"


def test_new_post_without_cast_qualification_cannot_gain_execution_templates():
    d, a = case()
    d["rule_templates"][0]["rule"]["trigger"]["phase"] = "post"
    a["facts"][0]["claim"] = copy.deepcopy(d["rule_templates"][0])
    refresh(d, a)
    assert compile_mechanism_descriptor(d, **a)["status"] == "pending"


def test_cast_fact_must_cite_all_callback_and_provider_nodes():
    _, a = cast_case(True)
    f = a["facts"][-1]
    f["evidence_refs"].remove("noblesse.set")
    with pytest.raises(ValueError): validate_fact(f)


def test_shared_verifier_is_bounded_and_does_not_mutate_material():
    d, a = cast_case(True)
    q = qualification(a)
    refs = a["facts"][-1]["evidence_refs"]
    got = verify_execution_qualification(q, descriptor=d, source_proof=a["source_proof"], evidence_refs=refs)
    got["parent"]["selection"]["constellation"] = 6
    assert q["parent"]["selection"]["constellation"] == 0
    q["parent"]["callback_bridge"]["apply_pointers"] = ["/x"] * 40000
    with pytest.raises(ValueError): validate_execution_qualification(q)


def test_caps_expose_versioned_cast_without_changing_default():
    caps = mechanism_descriptor_capabilities()
    assert caps["compiler"] == "typed-mechanism-d20-v1"
    assert caps["execution_qualification"]["supported_schema_versions"] == [1, 2, 3]
    assert len(caps["supported_compilers"]) == 3


def test_qualification_fingerprint_covers_every_new_authority_field():
    _, a = cast_case(True)
    fact = a["facts"][-1]
    before = fact_fingerprint(fact)
    fact["claim"]["provider"]["requirement_coverage"] = "partial"
    assert fact_fingerprint(fact) != before


def test_old_v1_complete_outputs_remain_exact():
    # Frozen before EC22 implementation, includes source/mechanism fingerprints.
    expected = {"flat": "dfe5629f972ab37cebd11aa566a638f0e4d15461fbcfe7ef7982055bce7e74d4", "swirl": "b08a7f5145647230504a12456805f86175a6017e4459f8602543346b20c8c8d9"}
    for kind, sha in expected.items():
        d, a = qualified_case(kind)
        assert fingerprint(compile_mechanism_descriptor(d, **a)) == sha


@pytest.mark.parametrize("key,value", [("affix_id", True), ("source_level", False), ("required_count", True),
                                     ("requirement_coverage", "all_equipment")])
def test_equipment_claim_identity_types_are_strict(key, value):
    _, a = cast_case(True)
    q = qualification(a)
    q["provider"][key] = value
    with pytest.raises(ValueError):
        validate_execution_qualification(q)


@pytest.mark.parametrize("equipment", [False, True])
def test_duplicate_source_cannot_be_changed_to_another_same_value_identity(equipment):
    d, a = cast_case(equipment)
    qualification(a)["duplicate_identity"]["source_id"] += 1
    refresh(d, a)
    assert compile_mechanism_descriptor(d, **a)["status"] == "pending"


def test_artifact_source_binding_needs_no_fake_character_selection():
    from genshin_runtime.mechanism_source_binding import validate_source_binding
    saved = json.loads((Path(__file__).parent / "fixtures/mechanism_cast_nodes.json").read_text(encoding="utf-8"))
    raw = [n for n in saved["nodes"] if n["id"].startswith("noblesse.")]
    binding = {"schema_version": 1, "upstream_commit": raw[0]["upstream_commit"], "normalization_fingerprint": "a"*64,
        "subject": {"target_type": "artifact_set", "target_id": 15007}, "selection": {},
        "nodes": [{k: n[k] for k in ("id", "definition_kind", "source_path", "source_sha256", "json_pointer", "node_sha256")} for n in raw],
        "parameters": [], "evidence": [{"id": n["id"], "kind": "runtime_node", "locator": n["source_path"] + "#" + n["json_pointer"], "sha256": n["node_sha256"]} for n in raw],
        "coverage": {"whole_source_status": "partial", "excluded_branches": ["not a whole set interpreter"], "unknown_branches": []}}
    assert validate_source_binding(binding) == binding


def test_legacy_contact_qualification_cannot_gain_post_phase():
    d, a = qualified_case()
    d["rule_templates"][0]["rule"]["trigger"]["phase"] = "post"
    a["facts"][0]["claim"] = copy.deepcopy(d["rule_templates"][0])
    refresh(d, a)
    assert compile_mechanism_descriptor(d, **a)["status"] == "pending"


def test_explicit_no_source_state_changes_is_typed_not_inferred_for_v1():
    _, a = cast_case(True)
    q = qualification(a)
    q["scenario_requirements"]["no_source_state_changes"] = True
    assert validate_execution_qualification(q) == q
    q["scenario_requirements"]["no_source_state_changes"] = False
    with pytest.raises(ValueError):
        validate_execution_qualification(q)
    assert "no_source_state_changes" not in validate_execution_qualification(claim())["scenario_requirements"]
    old = claim()
    old["scenario_requirements"]["no_source_state_changes"] = True
    with pytest.raises(ValueError):
        validate_execution_qualification(old)
