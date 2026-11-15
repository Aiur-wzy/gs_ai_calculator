"""EC20 independent counterexamples; synthetic inputs are not game-source approval."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.mechanism_descriptor_compiler import compile_mechanism_descriptor
from genshin_runtime.mechanism_semantics import (
    descriptor_fingerprint,
    fact_fingerprint,
    fingerprint,
    parameter_fingerprint,
)
from tests.test_mechanics_execution import (
    EVIDENCE,
    add,
    condition,
    damages,
    fixture,
    rule,
    run,
    second_character,
)
from tests.test_swirl_review import inputs as swirl_inputs


def review_material(*, stat="atk", sample="current", domain="provider", quota=True):
    """Author-independent material, deliberately without any character recipe ID."""
    template = {
        "key": "addition",
        "rule": {
            "name_zh": "独立工程语义",
            "trigger": {"event": "action_start", "phase": "pre"},
            "beneficiaries": {"mode": "team", "scope": "locked"},
            "timing": {"cooldown": 0, "cooldown_anchor": "success"},
            "stacking": {"max": 1, "add": 1, "policy": "replace"},
            "effects": [{"id": "base", "benefit_type_id": "benefit.base_add.all",
                         "value": {"kind": "stat", "subject": "owner", "stat": stat,
                                   "sample": sample}, "domain": domain,
                         "filter": {"event": "hit", "phase": "pre", "elements": ["cryo"]}}],
        },
        "value_bindings": [{"target": "timing.duration", "parameter_ref": "duration"},
                           {"target": "effects.base.value.factor", "parameter_ref": "factor"}],
        "execution_ports": [{"target": "owner", "role": "provider"},
                            {"target": "trigger.action_ids", "role": "qualifying_actions"},
                            {"target": "timing.delay", "role": "activation_delay"}],
        "requirements": ["single_target", "ordinary_stat_read", "fresh_empty_instance",
                         "fixed_party", "range_declared", "no_hitlag", "no_unmodelled_cancel",
                         "activation_before_initial_hit", "qualified_action_origin",
                         "no_duplicate_source"],
    }
    specs = [("duration", "seconds_exact_us", "seconds", 5),
             ("factor", "finite_number", "ratio", .5)]
    if quota:
        template["rule"]["quota"] = {
            "scope": "per_beneficiary", "refresh": "reset",
            "consume": {"event": "hit", "phase": "pre", "elements": ["cryo"]},
        }
        template["value_bindings"].append({"target": "quota.capacity", "parameter_ref": "capacity"})
        specs.append(("capacity", "positive_integer", "count", 2))
    descriptor = {"schema_version": 1, "mechanism_id": "review-addition", "revision": 1,
                  "evidence_fact_ids": [], "rule_templates": [template]}
    facts = [{"id": "review-rule", "revision": 1, "kind": "rule_template",
              "claim": copy.deepcopy(template), "evidence_tier": "experimental_reference",
              "evidence_refs": ["review-reference"]}]
    parameters = []
    for name, value_type, unit, value in specs:
        facts.append({"id": "role-" + name, "revision": 1, "kind": "parameter_role",
                      "claim": {"ref": name, "value_type": value_type, "unit": unit,
                                "targets": [{"rule_key": "addition", "target": binding["target"]}
                                            for binding in template["value_bindings"]
                                            if binding["parameter_ref"] == name]},
                      "evidence_tier": "experimental_reference", "evidence_refs": ["review-reference"]})
        parameters.append({"ref": name, "value_type": value_type, "value": value, "unit": unit,
                           "status": "ready", "origin_kind": "upstream_field", "derivation": "identity",
                           "evidence_ref": {"node_id": "review-values", "pointer": "/" + name}})
    descriptor["evidence_fact_ids"] = [f"{f['id']}@{f['revision']}" for f in facts]
    payload = {name: value for name, _, _, value in specs}
    proof = {
        "schema_version": 1,
        "snapshot": {"version_line": "release", "snapshot_id": "c" * 64,
                     "upstream_commit": "a" * 40, "indexer_version": "6", "normalization": {}},
        "subject": {"target_type": "character", "target_id": 90000001},
        "nodes": [{"id": "review-values", "source_path": "synthetic/review.json",
                   "source_sha256": fingerprint(payload), "json_pointer": "",
                   "node_sha256": fingerprint(payload), "payload": payload}],
        "evidence": [{"id": "review-reference", "kind": "reference_implementation",
                      "locator": "synthetic independent engineering test; not a game fact",
                      "sha256": "b" * 64}],
        "review": {"descriptor_status": "approved",
                   "fact_statuses": {ref: "approved" for ref in descriptor["evidence_fact_ids"]}},
        "coverage": {"whole_source_status": "partial", "excluded_branches": ["all game semantics"],
                     "unknown_branches": []},
    }
    inputs = {"source_proof": proof, "facts": facts, "parameters": parameters}
    rehash(descriptor, inputs)
    return descriptor, inputs


def rehash(descriptor, inputs):
    proof = inputs["source_proof"]
    proof["descriptor_fingerprint"] = descriptor_fingerprint(descriptor)
    proof["fact_fingerprints"] = {f"{f['id']}@{f['revision']}": fact_fingerprint(f) for f in inputs["facts"]}
    proof["parameter_fingerprints"] = {p["ref"]: parameter_fingerprint(p) for p in inputs["parameters"]}


def compile_input(pair):
    return compile_mechanism_descriptor(pair[0], **pair[1])


def pending(out):
    assert out["status"] == "pending", out
    assert out["rule_templates"] == []
    assert out["initial_rule_templates"] == []
    assert out["execution_ports"] == []
    assert out["issues"]


def test_review_materialized_approval_is_not_fresh_execution_authority():
    pair = review_material()
    before = copy.deepcopy(pair)
    out = compile_input(pair)
    assert out["status"] == "ready", out
    assert out["execution_status"] == "not_evaluated"
    assert out["verification_scope"] == "materialized_consistency_only"
    assert out["whole_source_status"] == "partial"
    assert pair == before
    out["rule_templates"][0]["rule"]["effects"][0]["value"]["factor"] = 999
    assert compile_input(pair)["rule_templates"][0]["rule"]["effects"][0]["value"]["factor"] == .5


@pytest.mark.parametrize("state", ["pending", "revoked", "stale", "conflict"])
def test_review_fact_status_is_not_overridden_by_valid_content_hash(state):
    pair = review_material()
    pair[1]["source_proof"]["review"]["fact_statuses"]["role-factor@1"] = state
    pending(compile_input(pair))


@pytest.mark.parametrize("field,value", [("stat", "def"), ("sample", "trigger")])
def test_review_rule_semantics_cannot_change_under_same_approved_fact(field, value):
    pair = review_material()
    pair[0]["rule_templates"][0]["rule"]["effects"][0]["value"][field] = value
    rehash(*pair)
    pending(compile_input(pair))


@pytest.mark.parametrize("value", [None, 0, .5])
def test_review_source_owned_value_must_be_absent_even_when_same(value):
    pair = review_material()
    pair[0]["rule_templates"][0]["rule"]["effects"][0]["value"]["factor"] = value
    # A malformed descriptor may be rejected by the canonical helper too;
    # the public compiler must always return a bounded, atomic pending result.
    pending(compile_input(pair))


@pytest.mark.parametrize("field,value", [("unit", "seconds"), ("value_type", "positive_integer"),
                                          ("value", True), ("value", 10**400)])
def test_review_parameter_type_unit_and_value_do_not_become_valid_by_rehash(field, value):
    pair = review_material()
    pair[1]["parameters"][1][field] = value
    pending(compile_input(pair))


def test_review_valid_raw_hash_does_not_authorize_wrong_parameter_pointer():
    pair = review_material()
    pair[1]["parameters"][1]["evidence_ref"]["pointer"] = "/capacity"
    rehash(*pair)
    pending(compile_input(pair))


def test_review_valid_fact_does_not_hide_unknown_source_branch():
    pair = review_material()
    pair[1]["source_proof"]["coverage"]["unknown_branches"] = ["unresolved conditional writer"]
    pending(compile_input(pair))


def test_review_local_identity_only_changes_provenance_not_compiled_algorithm():
    pair = review_material()
    one = compile_input(pair)
    pair[1]["source_proof"]["subject"]["target_id"] = 90000099
    two = compile_input(pair)
    assert one["status"] == two["status"] == "ready"
    assert one["rule_templates"] == two["rule_templates"]
    assert one["source_fingerprint"] != two["source_fingerprint"]


def test_review_duplicate_and_parent_child_owned_targets_are_atomic():
    pair = review_material()
    template = pair[0]["rule_templates"][0]
    template["value_bindings"].append({"target": "effects.base.value", "parameter_ref": "factor"})
    pending(compile_input(pair))


def test_review_local_event_ids_cannot_be_smuggled_inside_filter_data():
    pair = review_material()
    pair[0]["rule_templates"][0]["rule"]["effects"][0]["filter"]["event_ids"] = ["secret-local-hit"]
    pending(compile_input(pair))


def test_review_one_invalid_rule_retracts_whole_descriptor():
    pair = review_material()
    second = copy.deepcopy(pair[0]["rule_templates"][0])
    second["key"] = "invalid-second"
    second["rule"]["resources"] = [{"kind": "unreviewed"}]
    pair[0]["rule_templates"].append(second)
    pending(compile_input(pair))


def bind_engine_rule(out, *, owner="b"):
    assert out["status"] == "ready", out
    compiled = copy.deepcopy(out["rule_templates"][0]["rule"])
    compiled.update(id="review-generic", owner=owner, source_id="review-generic", **EVIDENCE)
    compiled["trigger"]["action_ids"] = ["cast"]
    compiled["timing"]["delay"] = 0
    return compiled


def test_review_heldout_def_current_uses_same_compiler_and_real_quota():
    out = compile_input(review_material(stat="def"))
    t, r, m = fixture((.2, 1.2, 1.3))
    second_character(t, r)
    t["characters"][1]["stats"].update({"def": 200, "base_def": 100})
    for desc in m["hit_events"]:
        desc["self_snapshot_event"] = "cast"
    condition(m, "later_def", 1)
    change = rule("ordinary-def", "benefit.stat_add.def", 200,
                  trigger={"event": "condition", "phase": "pre", "event_ids": ["later_def"]},
                  beneficiaries={"mode": "characters", "characters": ["b"], "scope": "locked"})
    add(m, bind_engine_rule(out), change)
    result = run(t, r, m)
    # Independent arithmetic: (100 + .5 * current_DEF) * .5DEF * .9RES.
    assert damages(result) == pytest.approx([90, 135, 45])
    assert [row["after"] for row in result["mechanics_trace"]
            if row["type"] == "quota" and row["cycle"] == 0] == [1, 0, 0]


def test_review_trigger_sample_does_not_resample_later_owner_stat():
    out = compile_input(review_material(sample="trigger", domain="self", quota=False))
    t, r, m = fixture((.2, 1.2, 1.3))
    second_character(t, r)
    condition(m, "later_atk", 1)
    change = rule("ordinary-atk", "benefit.stat_add.atk", 200,
                  trigger={"event": "condition", "phase": "pre", "event_ids": ["later_atk"]},
                  beneficiaries={"mode": "characters", "characters": ["b"], "scope": "locked"})
    add(m, bind_engine_rule(out), change)
    result = run(t, r, m)
    assert damages(result) == pytest.approx([90, 90, 90])
    assert not any(row["type"] == "quota" for row in result["mechanics_trace"])


def test_review_referenced_execution_requirement_is_never_silently_dropped():
    pair = review_material()
    extra = {"id": "additional-guard", "revision": 1, "kind": "execution_requirement",
             "claim": {"requirement": "no_dynamic_em"}, "evidence_tier": "experimental_reference",
             "evidence_refs": ["review-reference"]}
    pair[1]["facts"].append(extra)
    pair[0]["evidence_fact_ids"].append("additional-guard@1")
    pair[1]["source_proof"]["review"]["fact_statuses"]["additional-guard@1"] = "approved"
    rehash(*pair)
    out = compile_input(pair)
    # Either reject unsupported requirement placement or retain its actual guard;
    # an accepted typed constraint cannot turn into display-only evidence.
    if out["status"] == "ready":
        assert "no_dynamic_em" in out["required_execution_checks"]
    else:
        pending(out)


def test_review_same_compiler_dispatches_reaction_without_allocating_quota():
    pair = review_material(stat="em", sample="trigger", domain="self", quota=False)
    template = pair[0]["rule_templates"][0]
    template["rule"]["trigger"] = {
        "event": "elemental_reaction", "phase": "post", "reaction_types": ["swirl"],
        "reaction_elements": ["cryo"], "owner_presence": "any",
    }
    template["rule"]["stacking"]["policy"] = "refresh"
    template["rule"]["effects"][0]["benefit_type_id"] = "benefit.dmg_bonus.cryo"
    template["execution_ports"][1] = {"target": "trigger.characters", "role": "provider_characters"}
    template["requirements"] = ["single_target", "fresh_empty_instance", "qualified_reaction_origin",
                                "fixed_nonextra_em_equality", "no_dynamic_em", "no_duplicate_source"]
    pair[1]["facts"][0]["claim"] = copy.deepcopy(template)
    # A transparent engineering factor, not a declaration about a game's talent.
    pair[1]["parameters"][1]["value"] = .001
    node = pair[1]["source_proof"]["nodes"][0]
    node["payload"]["factor"] = .001
    node["node_sha256"] = node["source_sha256"] = fingerprint(node["payload"])
    rehash(*pair)
    out = compile_input(pair)
    assert out["status"] == "ready", out
    t, r, m = swirl_inputs(times=(.2, .3, .4))
    t["characters"][0]["stats"]["em"] = 400
    m["elemental_model"]["profiles"].append({"id": "cryo-no-application", "element": "cryo",
                                            "application": "none", **EVIDENCE})
    for h, desc in zip(t["hits"][1:], m["hit_events"][1:], strict=True):
        h["element"] = "cryo"
        desc["application"] = {"profile_id": "cryo-no-application", "target_id": "enemy", "landed": True}
    compiled = copy.deepcopy(out["rule_templates"][0]["rule"])
    compiled.update(id="review-reaction", owner="a", source_id="review-reaction", **EVIDENCE)
    compiled["trigger"]["characters"] = ["a"]
    compiled["timing"]["delay"] = 0
    add(m, compiled)
    result = run(t, r, m)
    ordinary = {h["id"]: h["expected"] for h in result["result"]["hits"] if h["id"] in {"h1", "h2"}}
    # 100 * .5 DEF * .9 RES * (1 + 400 * .001) = 63.
    assert ordinary == pytest.approx({"h1": 63, "h2": 63})
    assert not any(row["type"] == "quota" for row in result["mechanics_trace"])
