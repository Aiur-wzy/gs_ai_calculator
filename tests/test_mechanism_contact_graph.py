"""Actual source compilations feed a pure contact graph; no gameplay approval."""
from __future__ import annotations

import copy
import json

import pytest

from genshin_app.equipment import compose_equipment_plan
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.mechanism_contact_graph import (
    _database_rule_group,
    build_mechanism_contact_graph,
)
from tests.test_action_events import action_event_plan, confirm_action_events
from tests.test_attack_metadata_app import metadata_native
from tests.test_mechanics_execution import EVIDENCE
from tests.test_periodic_actions_review import accept_periodic, source_bound_plan
from tests.test_pipeline import fake_repo, stub_fetcher  # noqa: F401
from tests.test_runtime_attack_metadata import metadata_db  # noqa: F401
from tests.test_runtime_periodic_attack import periodic_db  # noqa: F401

# ruff: noqa: F811


def compiled(plan, db):
    report = compose_equipment_plan(plan, db)
    assert report["state"] == "ready", report["missing_inputs"]
    work = copy.deepcopy(plan)
    work["mechanics_model"] = copy.deepcopy(report["mechanics_model"])
    template = report["plan"]["resolved_template"]
    args = {"actor": "a", "source_snapshot": report["hit_parameter_compilations"][0]["snapshot"],
            "scope": {"action_type_ids": [], "damage_type_ids": [], "elements": []},
            "action_records": report["action_event_compilations"],
            "parameter_records": report["hit_parameter_compilations"],
            "metadata_records": report.get("attack_metadata_compilations", []),
            "elemental_records": report.get("elemental_compilations", []),
            "reaction_records": report.get("reaction_damage_compilations", []),
            "periodic_records": report.get("periodic_attack_compilations", [])}
    return work, template, args


@pytest.fixture
def single(metadata_db):
    plan = metadata_native(metadata_db)
    confirm_action_events(plan, metadata_db)
    return compiled(plan, metadata_db)


@pytest.fixture
def multi(periodic_db):
    plan = source_bound_plan(periodic_db)
    accept_periodic(plan, periodic_db)
    return compiled(plan, periodic_db)


def run(inputs):
    plan, template, args = inputs
    return build_mechanism_contact_graph(plan, template, **args)


def pending(inputs):
    result = run(inputs)
    assert result["status"] == "pending", result
    assert result["parents"] == result["dependencies"] == []
    return result


@pytest.mark.parametrize(("record", "expected"), [
    ({"rule_group": {"status": "database_confirmed", "group": "default"}}, "default"),
    ({"rule_group": {"status": "adapter_derived", "group": "adapter.icd.fixture"},
      "database_rule_group": "default"}, "default"),
    ({"rule_group": "legacy.default"}, "legacy.default"),
])
def test_elemental_receipt_rule_group_resolves_database_baseline(record, expected):
    assert _database_rule_group(record) == expected


@pytest.mark.parametrize("record", [
    {"rule_group": {"status": "declared", "group": "default"}},
    {"rule_group": {"status": "database_confirmed", "group": "adapter.icd.forged"},
     "database_rule_group": "default"},
    {"rule_group": {"status": "adapter_derived", "group": ""},
     "database_rule_group": "default"},
    {"rule_group": {"status": "adapter_derived", "group": "adapter.icd.forged"},
     "database_rule_group": ""},
])
def test_elemental_receipt_rule_group_rejects_forged_status(record):
    with pytest.raises(ValueError):
        _database_rule_group(record)


def test_single_actual_metadata_routes_and_detached_parameter_identity(single):
    before = copy.deepcopy(single)
    result = run(single)
    assert result["status"] == "ready", result["issues"]
    assert [p["absolute_tick"] for p in result["parents"]] == [200000, 2200000, 4200000]
    assert all(p["ordinal"] == 1 and p["channel_id"] is None for p in result["parents"])
    assert all(p["damage_classification"]["source_proven"] for p in result["parents"])
    dep = result["dependencies"][0]
    assert dep["selection"]["character_id"] == 90000001
    assert dep["parameter"]["source"]["parameter_pointer"].endswith("/paramList/0")
    assert set(dep["parameter"]["entry_sources"]) == {"avatar", "depot", "skill"}
    assert result["execution_status"] == "not_evaluated"
    assert single == before
    result["parents"][0]["prototype"].clear()
    assert single == before


def test_periodic_and_terminal_are_one_actual_graph(multi):
    result = run(multi)
    assert result["status"] == "ready", result["issues"]
    assert len(result["parents"]) == 15 and len(result["dependencies"]) == 2
    assert [p["absolute_tick"] for p in result["parents"][:5]] == [200000, 450000, 700000, 950000, 1100000]
    assert [p["channel_id"] for p in result["parents"][:5]] == ["slice"] * 4 + ["blast"]
    assert all(p["damage_classification"]["source_proven"] for p in result["parents"])


@pytest.mark.parametrize("case", ["summary", "occurrence_time"])
def test_periodic_lifecycle_projection_tamper_is_atomic(multi, case):
    record = multi[2]["action_records"][0]
    projection = record["lifecycle_projection"]
    if case == "summary":
        projection["summary"]["materialized_occurrences"] += 1
    else:
        projection["occurrences"][0]["time_ticks"] += 1
    record["instance_payload"]["lifecycle_projection"] = copy.deepcopy(projection)
    record["instance_fingerprint"] = fingerprint(record["instance_payload"])
    pending(multi)


@pytest.mark.parametrize("case", ["acceptance", "duplicate_dispatch", "missing_parameter", "parameter_pointer",
                                  "wrong_snapshot", "metadata_selection", "metadata_level", "actual_pattern",
                                  "action_time", "missing_action_route", "ability_actor", "raw_parent"])
def test_single_related_inconsistency_is_atomic(single, case):
    plan, _, args = single
    if case == "acceptance":
        plan["action_event_profiles"][0]["accepted_profile_fingerprint"] = None
    elif case == "duplicate_dispatch":
        args["action_records"].append(copy.deepcopy(args["action_records"][0]))
    elif case == "missing_parameter":
        args["parameter_records"].clear()
    elif case == "parameter_pointer":
        args["parameter_records"][0]["source"]["parameter_pointer"] += "0"
    elif case == "wrong_snapshot":
        args["source_snapshot"] = {**args["source_snapshot"], "snapshot_id": "x" * 64}
    elif case == "metadata_selection":
        args["metadata_records"][0]["query"]["selection"]["character_id"] = 10000063
    elif case == "metadata_level":
        args["metadata_records"][0]["query"]["semantic_dependency"]["level"]["effective_level"] = 9
    elif case == "actual_pattern":
        plan["mechanics_model"]["hit_patterns"][0]["orders"][0] += 1
    elif case == "action_time":
        plan["rotation_model"]["actions"][0]["start"] += .01
    elif case == "missing_action_route":
        record = args["action_records"][0]
        record["generated"].pop()
        actions = {a["id"]: a for a in plan["rotation_model"]["actions"]}
        record["instance_fingerprint"] = fingerprint({"profile_fingerprint": record["profile_fingerprint"],
            "generated": record["generated"], "actions": [actions[g["action_id"]] for g in record["generated"]],
            "period": plan["rotation_model"]["period"]})
    elif case == "ability_actor":
        plan["rotation_model"]["abilities"][0]["character"] = "b"
    else:
        query = args["metadata_records"][0]["query"]
        node = next(n for n in query["source_graph"] if n["source"] == query["semantic_dependency"]["ability"])
        node["raw"]["unreviewed"] = True
    pending(single)


@pytest.mark.parametrize("case", ["source_missing", "source_projection", "source_snapshot", "missing_terminal", "semantic_pair", "pattern_removed", "native_source_missing"])
def test_periodic_requires_full_source_and_all_actual_channels(multi, case):
    plan, _, args = multi
    if case == "source_missing":
        args["periodic_records"].clear()
    elif case == "source_projection":
        args["periodic_records"][0]["channels"][0]["compiled_fields"]["multiplier"] += 1
    elif case == "source_snapshot":
        args["periodic_records"][0]["snapshot"]["snapshot_id"] = "x" * 64
    elif case == "missing_terminal":
        record = args["action_records"][0]
        record["generated"] = [g for g in record["generated"] if g["channel_id"] != "blast"]
        record["instance_payload"]["generated"] = copy.deepcopy(record["generated"])
        record["instance_fingerprint"] = fingerprint(record["instance_payload"])
    elif case == "semantic_pair":
        plan["action_event_profiles"][0]["channels"][0]["contact_profile_id"] = "contact.blast"
    elif case == "native_source_missing":
        plan.pop("periodic_attack_sources")
    else:
        plan["mechanics_model"]["hit_patterns"].pop()
    pending(multi)


def test_irrelevant_physical_unproven_profile_does_not_block(single):
    plan, template, args = single
    args["scope"]["elements"] = ["cryo"]
    hit = copy.deepcopy(template["hits"][0])
    hit.update(id="physical", element="physical")
    template["hits"].append(hit)
    profile = copy.deepcopy(plan["action_event_profiles"][0])
    profile.update(id="unreviewed", prototype_hit_id="physical", accepted_profile_fingerprint=None)
    plan["action_event_profiles"].append(profile)
    ability = copy.deepcopy(plan["rotation_model"]["abilities"][0])
    ability["id"] = "physical.e"
    plan["rotation_model"]["abilities"].append(ability)
    action = copy.deepcopy(plan["rotation_model"]["actions"][0])
    action.update(id="physical.cast", ability_id=ability["id"])
    plan["rotation_model"]["actions"].append(action)
    plan["action_event_bindings"].append({"ability_id": ability["id"], "profile_id": profile["id"]})
    plan["mechanics_model"]["hit_events"].append({"hit_id": "physical", "action_id": action["id"], "order": 0})
    assert run(single)["status"] == "ready"
    hit.pop("element")
    pending(single)  # Unknown is not proof of irrelevance.


@pytest.mark.parametrize("field", ["hit_events", "hit_patterns"])
def test_related_manual_bypass_is_not_an_accepted_parent(single, field):
    plan, _, _ = single
    if field == "hit_events":
        extra = {"hit_id": "h0", "action_id": "cast0", "order": 9}
    else:
        extra = copy.deepcopy(plan["mechanics_model"]["hit_patterns"][0])
        extra["id"] = "manual.route"
    plan["mechanics_model"][field].append(extra)
    pending(single)


def test_standalone_application_is_not_a_hit_and_unknown_contact_is_not_silently_ignored(single):
    plan, _, _ = single
    original = plan["mechanics_model"]["hit_patterns"][0]
    plan["mechanics_model"]["application_events"] = [{"id": "pure.application", "action_id": "cast0", "t": .3,
        "order": 1, "application": copy.deepcopy(original["application"]), "contact": copy.deepcopy(original["contact"])}]
    result = run(single)
    assert result["status"] == "ready" and len(result["parents"]) == 3
    plan["mechanics_model"]["application_events"][0].pop("contact")
    pending(single)


def test_miss_hidden_and_icd_do_not_decide_trigger_eligibility(metadata_db):
    plan = metadata_native(metadata_db)
    plan["action_event_overrides"] = [{"action_id": "cast1", "landed": False, **EVIDENCE}]
    confirm_action_events(plan, metadata_db)
    inputs = compiled(plan, metadata_db)
    inputs[0]["mechanics_model"]["hit_patterns"][0]["report"] = False
    result = run(inputs)
    assert result["status"] == "ready", result["issues"]
    assert [p["landed"] for p in result["parents"]] == [True, False, True]
    assert result["parents"][0]["report"] is False
    assert result["parents"][0]["application"]["icd"]["kind"] == "sequence"


@pytest.mark.parametrize("limit", ["MAX_PARENTS", "MAX_GRAPH_BYTES"])
def test_small_budget_discards_all_partial_graph(single, monkeypatch, limit):
    from genshin_app.equipment import mechanism_contact_graph as graph
    monkeypatch.setattr(graph, limit, 2 if limit == "MAX_PARENTS" else 1024)
    pending(single)


def test_exact_full_graph_bytes_include_envelope(single, monkeypatch):
    from genshin_app.equipment import mechanism_contact_graph as graph
    result = run(single)
    size = len(json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf8"))
    monkeypatch.setattr(graph, "MAX_GRAPH_BYTES", size)
    assert run(single) == result
    monkeypatch.setattr(graph, "MAX_GRAPH_BYTES", size - 1)
    pending(single)


def test_manual_parameter_interpretation_does_not_replace_attack_metadata(metadata_db):
    plan = action_event_plan(metadata_db)
    confirm_action_events(plan, metadata_db)
    pending(compiled(plan, metadata_db))


def test_removing_native_and_receipt_metadata_together_is_not_a_manual_escape(single):
    plan, _, args = single
    plan.pop("attack_metadata_sources")
    args["metadata_records"].clear()
    pending(single)


@pytest.mark.parametrize("case", ["icd", "prototype_shape", "parameter_rehashed", "scope"])
def test_actual_semantics_and_parameter_consistency(single, case):
    plan, template, args = single
    if case == "icd":
        plan["mechanics_model"]["elemental_model"]["profiles"][0]["icd"]["sequence"] = [1, 1]
    elif case == "prototype_shape":
        template["hits"][0]["count"] = 2
    elif case == "parameter_rehashed":
        rec = args["parameter_records"][0]
        rec["multiplier"] = rec["scalar_projection"]["value"] = template["hits"][0]["multiplier"] = 99
        rec["compilation_fingerprint"] = fingerprint({k: v for k, v in rec.items() if k != "compilation_fingerprint"})
    else:
        args["scope"]["unsupported"] = []
    pending(single)


def test_shared_capture_resolver_binds_all_periodic_contacts(periodic_db):
    plan = source_bound_plan(periodic_db)
    for channel in plan["action_event_profiles"][0]["channels"]:
        channel["self_sampling"] = "action_capture"
    plan["mechanics_model"]["snapshot_captures"] = [
        {"id": "sample." + a["id"], "action_id": a["id"], "offset": .1, "precision": .01, **EVIDENCE}
        for a in plan["rotation_model"]["actions"]]
    accept_periodic(plan, periodic_db)
    inputs = compiled(plan, periodic_db)
    result = run(inputs)
    assert result["status"] == "ready", result["issues"]
    assert len(result["parents"]) == 15
    assert all(p["self_snapshot_event"] == "sample." + p["action_id"] for p in result["parents"])
    inputs[0]["mechanics_model"]["snapshot_captures"][0]["offset"] = .3
    pending(inputs)


def rehash_single_instance(inputs):
    plan, _, args = inputs
    row = args["action_records"][0]
    actions = {a["id"]: a for a in plan["rotation_model"]["actions"]}
    row["instance_fingerprint"] = fingerprint({"profile_fingerprint": row["profile_fingerprint"],
        "generated": row["generated"], "actions": [actions[g["action_id"]] for g in row["generated"]],
        "period": plan["rotation_model"]["period"]})


@pytest.mark.parametrize("change", ["profile_fp", "dependency_fp", "calibration_basis", "precision",
                                    "default_landed", "contact_basis", "elemental_missing", "elemental_basis",
                                    "reaction_added", "reference"])
def test_single_full_acceptance_rebuild_rejects_saved_hash_or_semantic_drift(single, change, monkeypatch):
    plan, _, args = single
    profile, record = plan["action_event_profiles"][0], args["action_records"][0]
    if change == "profile_fp":
        profile["accepted_profile_fingerprint"] = "f" * 64
        record.update(profile_fingerprint="f" * 64, accepted_profile_fingerprint="f" * 64)
        record["required_confirmation"]["accepted_profile_fingerprint"] = "f" * 64
        rehash_single_instance(single)
    elif change == "dependency_fp":
        record["source_dependency_fingerprint"] = "f" * 64
    elif change == "calibration_basis":
        profile["calibration"]["source"] += " changed"
    elif change == "precision":
        profile["calibration"]["precision_seconds"] = .02
    elif change == "default_landed":
        profile["default_landed"] = False
    elif change == "contact_basis":
        plan["mechanics_model"]["elemental_model"]["contact_profiles"][0]["source"] += " changed"
    elif change == "elemental_missing":
        args["elemental_records"].clear()
    elif change == "elemental_basis":
        row = args["elemental_records"][0]
        row["binding_basis"] += " changed"
        row["compilation_fingerprint"] = fingerprint({k: v for k, v in row.items() if k != "compilation_fingerprint"})
    elif change == "reaction_added":
        args["reaction_records"] = [{"version_line": "release", "snapshot": args["source_snapshot"],
                                     "profiles": [{"content_fingerprint": "f" * 64}],
                                     "compiled_icd": {}, "binding_basis": "Different saved reaction source"}]
    else:
        from genshin_app.equipment import action_events
        monkeypatch.setattr(action_events, "REFERENCE_CONTRACT", {**action_events.REFERENCE_CONTRACT, "contract_fingerprint": "f" * 64})
    pending(single)


def single_capture(db):
    from tests.test_action_capture_profiles import with_captures
    plan = with_captures(metadata_native(db))
    confirm_action_events(plan, db)
    return compiled(plan, db)


def test_single_capture_rebuild_all_calibrations_not_just_reference(metadata_db):
    inputs = single_capture(metadata_db)
    assert run(inputs)["status"] == "ready"
    plan, _, args = inputs
    capture = plan["mechanics_model"]["snapshot_captures"][0]
    capture["source"] += " changed"
    # Hash the changed capture and instance coherently; the old acceptance must
    # still fail because evidence text is a portable calibration dependency.
    args["action_records"][0]["generated"][0]["snapshot_capture_fingerprint"] = fingerprint(capture)
    rehash_single_instance(inputs)
    pending(inputs)


def test_single_capture_local_alias_does_not_reconfirm_same_calibration(metadata_db):
    inputs = single_capture(metadata_db)
    assert run(inputs)["status"] == "ready"
    plan, _, args = inputs
    capture = plan["mechanics_model"]["snapshot_captures"][0]
    capture["id"] = "capture.local_renamed"
    generated = args["action_records"][0]["generated"][0]
    generated.update(self_snapshot_event=capture["id"], snapshot_capture_fingerprint=fingerprint(capture))
    next(p for p in plan["mechanics_model"]["hit_patterns"] if p["id"] == generated["pattern_id"])["self_snapshot_event"] = capture["id"]
    rehash_single_instance(inputs)
    assert run(inputs)["status"] == "ready"


def test_shared_single_scope_excludes_display_actor_alias_but_keeps_semantics(single):
    from genshin_app.equipment.action_events import build_single_action_scope
    plan, template, args = single
    profile, param = plan["action_event_profiles"][0], args["parameter_records"][0]
    app = plan["mechanics_model"]["elemental_model"]["profiles"][0]
    contact = plan["mechanics_model"]["elemental_model"]["contact_profiles"][0]
    raw = {**plan["manual_template"]["hits"][0], "element": template["hits"][0]["element"]}
    kwargs = {"raw_hit": raw, "hit": template["hits"][0], "parameter": param,
              "declaration": {"param_index": param["param_index"], "purpose": param["declared_purpose"],
                              "status": param["interpretation_status"], "source": param["interpretation_basis"]},
              "application": app, "contact": contact, "metadata": args["metadata_records"][0],
              "elemental_record": args["elemental_records"][0], "reaction_compilations": args["reaction_records"],
              "selected_actions": plan["rotation_model"]["actions"], "rotation": plan["rotation_model"], "model": plan["mechanics_model"]}
    scope, _ = build_single_action_scope(profile, **kwargs)
    assert fingerprint(scope) == args["action_records"][0]["profile_fingerprint"]
    kwargs = copy.deepcopy(kwargs)
    kwargs["raw_hit"].update(id="different.local.id", character="another.alias", skill="different label")
    kwargs["hit"].update(id="different.local.id", character="another.alias")
    kwargs["parameter"]["actor"] = "another.alias"
    assert build_single_action_scope(profile, **kwargs)[0] == scope
