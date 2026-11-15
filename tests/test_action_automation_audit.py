import copy

import pytest

from genshin_app.equipment.action_automation_audit import (
    _compare_occurrences,
    build_action_automation_audit,
    build_action_materialization_receipts,
    validate_action_materialization_receipt,
    validate_action_materialization_result_receipt,
)
from genshin_app.equipment.finite_action_schema import KIND as FINITE_APPLICATION_KIND
from genshin_app.equipment.periodic_schema import (
    APPLICATION_KIND as MULTI_APPLICATION_KIND,
)
from genshin_db.runtime_catalog import fingerprint
from genshin_db.runtime_gameplay_action_manifest import materialization_expectation


def _plan():
    return {
        "manual_template": {"hits": [
            {"id": "manual", "reaction": {"type": "amplify"}},
            {"id": "auto-prototype"},
        ]},
        "rotation_model": {
            "abilities": [
                {"id": "e", "action_type_id": "action.elemental_skill"},
                {"id": "q", "action_type_id": "action.elemental_burst"},
                {"id": "swap", "action_type_id": "action.switch"},
                {"id": "mystery", "action_type_id": "action.custom"},
            ],
            "actions": [
                {"id": "e1", "ability_id": "e"},
                {"id": "q1", "ability_id": "q"},
                {"id": "s1", "ability_id": "swap"},
                {"id": "x1", "ability_id": "mystery"},
            ],
            "action_hit_links": [{"action_id": "q1", "hit_id": "manual"}],
        },
        "mechanics_model": {"elemental_model": {"ruleset": "gauge-swirl-dynamic-v3"}},
        "action_event_profiles": [{"id": "profile", "kind": "calibrated_single_application_v2",
                                   "prototype_hit_id": "auto-prototype"}],
        "action_event_bindings": [{"ability_id": "e", "profile_id": "profile"}],
    }


def test_audit_separates_automatic_calibration_manual_and_unknown():
    audit = build_action_automation_audit(_plan(), [{
        "profile_id": "profile",
        "kind": "calibrated_single_application_v2",
        "status": "ready",
        "generated": [{"action_id": "e1", "hit_id": "auto.e1"}],
    }])
    rows = {row["action_id"]: row for row in audit["actions"]}
    assert rows["e1"]["category"] == "automatic_generated"
    assert rows["q1"]["category"] == "manual_input_required"
    assert rows["s1"]["category"] == "not_applicable"
    assert rows["x1"]["category"] == "unclassified"
    assert audit["summary"] == {
        "total_actions": 4,
        "attack_actions": 2,
        "automatic_actions": 1,
        "reviewed_dispatch_actions": 0,
        "reusable_profile_actions": 1,
        "scenario_calibration_actions": 0,
        "manual_actions": 1,
        "unclassified_actions": 1,
        "automatic_generated_hits": 1,
        "manual_hit_rows": 2,
        "reusable_prototype_hit_rows": 1,
        "manual_per_instance_hit_rows": 1,
        "manual_action_hit_links": 1,
        "manual_reaction_answers": 1,
        "reusable_prototype_reaction_answers": 0,
        "manual_per_instance_reaction_answers": 1,
        "automatic_elemental_attempts": 1,
        "known_pending_contacts": 0,
        "unknown_contact_cardinality_actions": 1,
        "unknown_work_orders": 1,
        "unique_dispatch_profile_decisions": 0,
        "unique_reusable_profile_decisions": 1,
        "automatic_action_ratio": .5,
        "reviewed_dispatch_action_ratio": 0,
    }
    assert audit["dispatch_manifest"]["status"] == "absent"
    assert audit["elemental"]["status"] == "automatic_active"


def test_pending_bound_profile_is_a_calibration_work_item():
    plan = _plan()
    plan["rotation_model"]["action_hit_links"] = []
    audit = build_action_automation_audit(plan, [{
        "profile_id": "profile",
        "kind": "calibrated_single_application_v2",
        "status": "pending",
        "generated": [],
        "issues": [{"code": "action_event_confirmation", "message": "需要确认"}],
    }])
    row = next(row for row in audit["actions"] if row["action_id"] == "e1")
    assert row["category"] == "scenario_calibration"
    assert row["issues"][0]["code"] == "action_event_confirmation"
    assert audit["summary"]["automatic_elemental_attempts"] == 0


def test_absent_elemental_model_never_claims_simulation():
    plan = _plan()
    plan["mechanics_model"] = {}
    audit = build_action_automation_audit(plan, [])
    assert audit["elemental"]["status"] == "disabled"
    assert audit["summary"]["manual_reaction_answers"] == 1


def test_manual_reaction_answers_include_reusable_prototypes():
    plan = _plan()
    plan["manual_template"]["hits"][1]["reaction"] = {"type": "amplify"}

    audit = build_action_automation_audit(plan, [])

    assert audit["summary"]["manual_reaction_answers"] == 2
    assert audit["summary"]["reusable_prototype_reaction_answers"] == 1
    assert audit["summary"]["manual_per_instance_reaction_answers"] == 1


def test_multi_channel_prototypes_are_not_counted_as_per_instance_rows():
    plan = _plan()
    plan["manual_template"]["hits"] = [
        {"id": "channel.one", "reaction": {"type": "amplify"}},
        {"id": "channel.two"},
        {"id": "manual.instance", "reaction": {"type": "amplify"}},
    ]
    plan["action_event_profiles"] = [{
        "id": "profile", "kind": MULTI_APPLICATION_KIND,
        "channels": [
            {"prototype_hit_id": "channel.one"},
            {"prototype_hit_id": "channel.two"},
        ],
    }]

    audit = build_action_automation_audit(plan, [])

    assert audit["summary"]["manual_hit_rows"] == 3
    assert audit["summary"]["reusable_prototype_hit_rows"] == 2
    assert audit["summary"]["manual_per_instance_hit_rows"] == 1
    assert audit["summary"]["reusable_prototype_reaction_answers"] == 1
    assert audit["summary"]["manual_per_instance_reaction_answers"] == 1
    assert audit["summary"]["manual_reaction_answers"] == 2


def test_dynamic_multi_profile_counts_every_generated_elemental_attempt():
    plan = _plan()
    plan["action_event_profiles"][0]["kind"] = MULTI_APPLICATION_KIND
    generated = [{"action_id": "e1", "hit_id": f"auto.e1:{index}"} for index in range(1, 5)]
    audit = build_action_automation_audit(plan, [{
        "profile_id": "profile", "kind": MULTI_APPLICATION_KIND,
        "status": "ready", "generated": generated,
    }])
    assert audit["summary"]["automatic_generated_hits"] == 4
    assert audit["summary"]["automatic_elemental_attempts"] == 4
    assert audit["elemental"]["status"] == "automatic_active"


def test_finite_profile_counts_every_generated_elemental_attempt():
    plan = _plan()
    plan["action_event_profiles"][0]["kind"] = FINITE_APPLICATION_KIND
    generated = [{"action_id": "e1", "hit_id": f"auto.e1:{index}"}
                 for index in range(1, 4)]
    audit = build_action_automation_audit(plan, [{
        "profile_id": "profile", "kind": FINITE_APPLICATION_KIND,
        "status": "ready", "generated": generated,
    }])
    assert audit["summary"]["automatic_generated_hits"] == 3
    assert audit["summary"]["automatic_elemental_attempts"] == 3


def test_reviewed_dispatch_origin_requires_complete_component_manifest():
    plan = _plan()
    event = [{
        "profile_id": "profile", "kind": "calibrated_single_application_v2",
        "status": "ready", "generated": [{"action_id": "e1", "hit_id": "auto.e1"}],
    }]
    dispatch = [{
        "status": "ready", "expected_components": ["primary"],
        "source_verification": {"status": "ready"},
        "native_fragments_fingerprint": "a" * 64,
        "materialized": {"component_ids": ["primary"], "profile_ids": ["profile"]},
    }]
    audit = build_action_automation_audit(plan, event, dispatch)
    assert audit["actions"][0]["automation_origin"] == "reviewed_dispatch_profile"
    assert audit["summary"]["reviewed_dispatch_actions"] == 1
    assert audit["summary"]["unique_dispatch_profile_decisions"] == 1
    assert audit["dispatch_manifest"] == {
        "status": "complete", "records": 1, "complete_records": 1,
        "pending_records": 0, "expected_components": 1, "materialized_components": 1,
    }

    dispatch[0]["materialized"]["component_ids"] = []
    incomplete = build_action_automation_audit(plan, event, dispatch)
    assert incomplete["actions"][0]["automation_origin"] == "reusable_plan_profile"
    assert incomplete["summary"]["reviewed_dispatch_actions"] == 0
    assert incomplete["dispatch_manifest"]["status"] == "pending"


def test_dispatch_audit_uses_private_execution_view_without_counting_generated_rows_as_manual():
    execution_plan = _plan()
    native_plan = copy.deepcopy(execution_plan)
    native_plan["manual_template"]["hits"] = []
    native_plan["action_event_profiles"] = []
    native_plan["action_event_bindings"] = []
    event = [{
        "profile_id": "profile", "kind": "calibrated_single_application_v2",
        "status": "ready", "generated": [{"action_id": "e1", "hit_id": "auto.e1"}],
    }]
    dispatch = [{
        "status": "ready", "expected_components": ["primary"],
        "source_verification": {"status": "ready"},
        "native_fragments_fingerprint": "b" * 64,
        "materialized": {"component_ids": ["primary"], "profile_ids": ["profile"]},
    }]

    audit = build_action_automation_audit(
        native_plan, event, dispatch, execution_plan=execution_plan)

    row = next(row for row in audit["actions"] if row["action_id"] == "e1")
    assert row["category"] == "automatic_generated"
    assert row["automation_origin"] == "reviewed_dispatch_profile"
    assert audit["summary"]["reviewed_dispatch_actions"] == 1
    assert audit["summary"]["manual_hit_rows"] == 0
    assert audit["summary"]["reusable_prototype_hit_rows"] == 0
    assert audit["summary"]["manual_per_instance_hit_rows"] == 0
    assert audit["summary"]["unique_reusable_profile_decisions"] == 0


def _evidence():
    return {"status": "confirmed", "category": "calibration_required", "source_refs": [{
        "kind": "calibration_record", "reference": "fixture://materialization-receipt",
        "fingerprint": "1" * 64,
    }]}


def _coverage(component_ids, occurrences=None, *, topology_kind="finite_sequence",
              refresh_policy="none"):
    rows = (copy.deepcopy(occurrences) if occurrences is not None else [
        {"occurrence_id": component_id, "component_id": component_id, "ordinal": index}
        for index, component_id in enumerate(component_ids, 1)
    ])
    components = [{"component_id": component_id, "critical_domains": ["damage"],
                   "evidence": _evidence()} for component_id in component_ids]
    if topology_kind == "finite_sequence":
        steps = []
        for index, row in enumerate(rows):
            steps.append({"step_id": row["occurrence_id"], "ordinal": row["ordinal"],
                "component_id": row["component_id"], "offset_seconds": index / 10,
                "order": row.get("order", 0)})
        topology = {"kind": "finite_sequence", "steps": steps,
            "terminal_step_id": steps[-1]["step_id"], "interruption_policy": "none",
            "evidence": _evidence()}
    else:
        channels = []
        for channel_id in dict.fromkeys(row.get("channel_id") for row in rows):
            channel_rows = [row for row in rows if row.get("channel_id") == channel_id]
            component_id = channel_rows[0]["component_id"]
            schedule = ({"kind": "fixed", "offset_seconds": 0}
                        if len(channel_rows) == 1 else
                        {"kind": "periodic", "first_offset_seconds": 0,
                         "interval_seconds": .25, "count": len(channel_rows)})
            channels.append({"channel_id": channel_id, "component_id": component_id,
                             "schedule": schedule})
        topology = {"kind": "periodic_channels", "channels": channels,
            "lifecycle": {"establishment_offset_seconds": 0,
                "instance_end_offset_seconds": max(.25, (len(rows) - 1) / 4),
                "refresh_policy": refresh_policy, "pause_resume_policy": "none",
                "off_field_policy": "continue", "evidence": _evidence()},
            "evidence": _evidence()}
    return {
        "manifest_id": "fixture.coverage", "status": "confirmed",
        "selector": {"version_line": "release", "character_id": 1001,
            "skill_depot_id": 2001, "skill_id": 3001, "slot": "skill",
            "effective_level": 10, "constellation": 0, "variant": "default",
            "cast_stage": None},
        "components": components, "topology": topology,
        "dataset": {"version_line": "release", "upstream_commit": "fixture-commit",
            "normalization_profile_id": "fixture.normalization", "snapshot_id": None},
        "review": {"status": "reviewed", "date": "2026-10-21", "reviewer": "test",
                   "notes": "Synthetic independent materialization topology."},
    }


def _trusted_bundle(*manifests):
    return {"schema_version": 1, "contract_id": "gameplay-action-manifest-v1",
            "manifests": [copy.deepcopy(row) for row in manifests]}


def _dispatch(component_ids, *, coverage_marker=True):
    row = {
        "ability_id": "e",
        "status": "ready",
        "expected_components": component_ids,
        "source_verification": {"status": "ready"},
        "native_fragments_fingerprint": "a" * 64,
        "materialized": {"component_ids": component_ids, "profile_ids": ["profile"]},
    }
    if coverage_marker is not False:
        manifest = (_coverage(component_ids) if coverage_marker is True
                    else copy.deepcopy(coverage_marker))
        row.update(
            selector=copy.deepcopy(manifest["selector"]),
            coverage_status="ready",
            declared_coverage_manifest_id=manifest["manifest_id"],
            expected_coverage_manifest_fingerprint=fingerprint(manifest),
            coverage_manifest=manifest,
            coverage_manifest_id=manifest["manifest_id"],
            coverage_manifest_fingerprint=fingerprint(manifest),
            coverage_expectation=materialization_expectation(manifest),
            coverage_issues=[],
        )
    else:
        row.update(coverage_status="unknown", coverage_manifest=None,
                   coverage_expectation={"status": "unknown"}, coverage_issues=[])
    return row


def _event(*generated):
    return [{
        "profile_id": "profile",
        "kind": MULTI_APPLICATION_KIND,
        "status": "ready",
        "generated": list(generated),
    }]


def _materialization_action(audit):
    rows = audit["materialization_receipt"]["actions"]
    return next(row for row in rows if row["action_id"] == "e1")


def _single_action_plan(*, start=0):
    plan = _plan()
    plan["rotation_model"]["abilities"] = plan["rotation_model"]["abilities"][:1]
    plan["rotation_model"]["actions"] = [{"id": "e1", "ability_id": "e", "start": start}]
    plan["rotation_model"]["action_hit_links"] = []
    return plan


def _sealed_materialization(plan, event, dispatch):
    public = build_action_automation_audit(
        plan, event, dispatch)["materialization_receipt"]
    source = {"contract": "action-materialization-source-v1",
        "rotation_model": copy.deepcopy(plan["rotation_model"]),
        "action_dispatch_compilations": copy.deepcopy(dispatch)}
    result = {"action_materialization_receipt": copy.deepcopy(public),
              "action_materialization_source": source,
              "action_event_compilations": copy.deepcopy(event)}
    receipt = {"action_materialization_receipt": copy.deepcopy(public),
        "action_materialization_proof": {
            "contract": "action-materialization-proof-v1",
            "public_receipt_fingerprint": fingerprint(public),
            "action_event_compilations_fingerprint": fingerprint(event),
            "source_fingerprint": fingerprint(source),
        },
        "result_fingerprint": fingerprint(result)}
    return public, result, receipt


def test_independent_coverage_manifest_detects_profile_that_omits_gameplay_components():
    coverage = _coverage(
        ["loop.1", "loop.2", "loop.3", "loop.4"],
        [{"occurrence_id": f"loop.{index}", "component_id": f"loop.{index}",
          "ordinal": index} for index in range(1, 5)],
    )
    dispatch = _dispatch(["loop.1", "loop.2"], coverage_marker=coverage)
    event = _event(
        {"action_id": "e1", "hit_id": "hit.1", "component_id": "loop.1",
         "step_id": "loop.1", "ordinal": 1, "topology_order": 0,
         "terminal": False, "offset_ticks": 0},
        {"action_id": "e1", "hit_id": "hit.2", "component_id": "loop.2",
         "step_id": "loop.2", "ordinal": 2, "topology_order": 0,
         "terminal": False, "offset_ticks": 100000},
    )

    row = _materialization_action(build_action_automation_audit(_plan(), event, [dispatch]))

    assert row["coverage_status"] == "incomplete"
    assert row["calculation_class"] == "diagnostic"
    assert row["expected_components"] == 4
    assert row["materialized_occurrences"] == 2
    assert {item["component_id"] for item in row["unresolved_occurrences"]} == {"loop.3", "loop.4"}
    assert {item["component_id"] for item in row["unresolved_components"]} == {"loop.3", "loop.4"}
    assert all(item["reason_code"] == "dispatch_profile_missing_component"
               for item in row["unresolved_components"])
    assert any(issue["code"] == "action_materialization_profile_coverage_mismatch"
               for issue in row["issues"])


def test_profile_self_consistency_without_independent_manifest_is_unknown():
    dispatch = _dispatch(["primary"], coverage_marker=False)
    event = _event({"action_id": "e1", "hit_id": "hit.1", "component_id": "primary"})

    receipts = build_action_automation_audit(_plan(), event, [dispatch])["materialization_receipt"]
    row = next(item for item in receipts["actions"] if item["action_id"] == "e1")

    assert row["coverage_status"] == "unknown"
    assert row["calculation_class"] == "diagnostic"
    assert row["expected_components"] is None
    assert row["materialized_occurrences"] == 1
    assert receipts["calculation_class"] == "diagnostic"


def test_normalized_expectation_and_digest_without_raw_manifest_remain_unknown():
    manifest = _coverage(["primary"])
    dispatch = _dispatch(["primary"], coverage_marker=manifest)
    dispatch["coverage_manifest"] = None
    event = _event({"action_id": "e1", "hit_id": "hit.1", "component_id": "primary",
                    "step_id": "primary", "ordinal": 1, "topology_order": 0,
                    "terminal": True, "offset_ticks": 0})

    row = _materialization_action(build_action_automation_audit(_plan(), event, [dispatch]))

    assert row["coverage_status"] == "unknown"
    assert row["expected_components"] is None
    assert row["coverage_manifest_id"] == manifest["manifest_id"]


def test_complete_finite_sequence_receipt_joins_component_and_step():
    coverage = _coverage(
        ["opening", "finisher"],
        [
            {"occurrence_id": "opening.1", "component_id": "opening", "ordinal": 1},
            {"occurrence_id": "finisher.2", "component_id": "finisher", "ordinal": 2},
        ],
    )
    dispatch = _dispatch(["opening", "finisher"], coverage_marker=coverage)
    event = _event(
        {"action_id": "e1", "hit_id": "hit.open", "component_id": "opening",
         "step_id": "opening.1", "ordinal": 1, "topology_order": 0,
         "terminal": False, "offset_ticks": 0},
        {"action_id": "e1", "hit_id": "hit.finish", "component_id": "finisher",
         "step_id": "finisher.2", "ordinal": 2, "topology_order": 0,
         "terminal": True, "offset_ticks": 100000},
    )

    row = _materialization_action(build_action_automation_audit(_plan(), event, [dispatch]))

    assert row["coverage_status"] == "complete"
    assert row["calculation_class"] == "normal"
    assert row["expected_components"] == 2
    assert row["materialized_components"] == 2
    assert row["unresolved_components"] == []
    assert row["unresolved_occurrences"] == []
    assert row["unexpected_occurrences"] == []


def test_unconsumed_resource_buff_or_state_domains_can_never_claim_complete():
    manifest = _coverage(["primary"])
    manifest["components"][0]["critical_domains"] = ["damage", "resource", "buff", "state"]
    dispatch = _dispatch(["primary"], coverage_marker=manifest)
    event = _event({"action_id": "e1", "hit_id": "hit.1", "component_id": "primary",
                    "step_id": "primary", "ordinal": 1, "topology_order": 0,
                    "terminal": True, "offset_ticks": 0})

    row = _materialization_action(build_action_automation_audit(
        _single_action_plan(), event, [dispatch]))

    assert row["coverage_status"] == "incomplete"
    assert row["calculation_class"] == "diagnostic"
    assert row["unresolved_components"] == [{
        "component_id": "primary",
        "reason_code": "critical_domain_not_materialized",
        "critical_domains": ["damage", "resource", "buff", "state"],
    }]
    assert {issue["code"] for issue in row["issues"]} == {
        "action_materialization_critical_domain_unsupported"}


def test_materialization_indexes_compiler_records_once_for_many_actions():
    class CountingRecords(list):
        iterations = 0

        def __iter__(self):
            self.iterations += 1
            return super().__iter__()

    plan = _single_action_plan()
    plan["rotation_model"]["actions"] = [
        {"id": f"e{index}", "ability_id": "e", "start": index}
        for index in range(1, 101)]
    records = CountingRecords(_event(*[
        {"action_id": f"e{index}", "hit_id": f"hit.{index}",
         "component_id": "primary", "step_id": "primary", "ordinal": 1,
         "topology_order": 0, "terminal": True, "offset_ticks": 0}
        for index in range(1, 101)]))

    receipt = build_action_materialization_receipts(
        plan, records, [_dispatch(["primary"])])

    assert records.iterations == 1
    assert receipt["summary"]["actions"] == 100


@pytest.mark.parametrize(("field", "value"), [
    ("topology_order", 7),
    ("terminal", False),
])
def test_finite_occurrence_proof_rejects_wrong_local_order_or_terminal(field, value):
    manifest = _coverage(["primary"])
    dispatch = _dispatch(["primary"], coverage_marker=manifest)
    generated = {"action_id": "e1", "hit_id": "hit.1", "component_id": "primary",
                 "step_id": "primary", "ordinal": 1, "topology_order": 0,
                 "terminal": True, "offset_ticks": 0}
    generated[field] = value

    row = _materialization_action(build_action_automation_audit(
        _single_action_plan(), _event(generated), [dispatch]))

    assert row["coverage_status"] == "incomplete"
    assert [item["occurrence_id"] for item in row["unresolved_occurrences"]] == ["primary"]
    assert [item["occurrence_id"] for item in row["unexpected_occurrences"]] == ["primary"]


def test_occurrence_comparison_is_linear_for_maximum_periodic_manifest(monkeypatch):
    count = 32 * 256
    expected = [{"component_id": f"component.{index // 256}",
                 "occurrence_id": f"occurrence.{index}", "ordinal": index + 1}
                for index in range(count)]
    actual = list(reversed(copy.deepcopy(expected)))
    calls = 0

    from genshin_app.equipment import action_automation_audit as module
    original = module._occurrence_matches

    def observe(expected_row, actual_row):
        nonlocal calls
        calls += 1
        return original(expected_row, actual_row)

    monkeypatch.setattr(module, "_occurrence_matches", observe)
    unresolved, unexpected = _compare_occurrences(expected, actual)

    assert unresolved == unexpected == []
    assert calls == count


def test_occurrence_comparison_rejects_duplicate_stable_identity():
    expected = [{"component_id": "primary", "occurrence_id": "primary.1",
                 "ordinal": 1}]
    actual = [copy.deepcopy(expected[0]), copy.deepcopy(expected[0])]

    unresolved, unexpected = _compare_occurrences(expected, actual)

    assert unresolved == expected
    assert unexpected == actual


def test_complete_periodic_receipt_joins_channel_and_ordinal():
    coverage = _coverage(["pulse"], [
        {"occurrence_id": f"pulse:{index}", "component_id": "pulse",
         "channel_id": "pulse", "ordinal": index} for index in range(1, 4)
    ], topology_kind="periodic_channels")
    dispatch = _dispatch(["pulse"], coverage_marker=coverage)
    event = _event(*[
        {"action_id": "e1", "hit_id": f"hit.{index}", "channel_id": "pulse",
         "ordinal": index, "offset_ticks": (index - 1) * 250000}
        for index in range(1, 4)
    ])

    row = _materialization_action(build_action_automation_audit(_plan(), event, [dispatch]))

    assert row["coverage_status"] == "complete"
    assert row["expected_occurrences"] == 3
    assert row["materialized_occurrences"] == 3
    assert row["spawned_channels"] == [{
        "channel_id": "pulse", "component_id": "pulse", "expected_occurrences": 3}]
    assert row["coverage_manifest_id"] == coverage["manifest_id"]
    assert row["coverage_manifest_fingerprint"] == fingerprint(coverage)


def test_missing_or_tampered_periodic_occurrence_is_diagnostic():
    coverage = _coverage(["pulse"], [
        {"occurrence_id": f"pulse:{index}", "component_id": "pulse",
         "channel_id": "pulse", "ordinal": index} for index in range(1, 4)
    ], topology_kind="periodic_channels")
    dispatch = _dispatch(["pulse"], coverage_marker=coverage)
    # Ordinal 2 is missing and ordinal 4 is unexpected.  Equal row counts must
    # not be mistaken for complete materialization.
    event = _event(*[
        {"action_id": "e1", "hit_id": f"hit.{index}", "channel_id": "pulse",
         "ordinal": index, "offset_ticks": (index - 1) * 250000}
        for index in (1, 3, 4)
    ])

    row = _materialization_action(build_action_automation_audit(_plan(), event, [dispatch]))

    assert row["coverage_status"] == "incomplete"
    assert row["calculation_class"] == "diagnostic"
    assert [item["ordinal"] for item in row["unresolved_occurrences"]] == [2]
    assert [item["ordinal"] for item in row["unexpected_occurrences"]] == [4]


def test_tampered_raw_gameplay_manifest_cannot_authorize_normalized_expectation():
    coverage = _coverage(["primary"])
    dispatch = _dispatch(["primary"], coverage_marker=coverage)
    dispatch["coverage_manifest"] = {"manifest_id": "tampered"}
    event = _event({"action_id": "e1", "hit_id": "hit.1", "component_id": "primary",
                    "step_id": "primary", "ordinal": 1, "topology_order": 0,
                    "terminal": True, "offset_ticks": 0})

    row = _materialization_action(build_action_automation_audit(_plan(), event, [dispatch]))

    assert row["coverage_status"] == "incomplete"
    assert row["calculation_class"] == "diagnostic"
    assert row["issues"][0]["code"] == "action_coverage_expectation_invalid"


def test_direct_replace_previous_projects_reviewed_cancellation_without_false_gap():
    manifest = _coverage(["pulse"], [
        {"occurrence_id": f"pulse:{index}", "component_id": "pulse",
         "channel_id": "pulse", "ordinal": index} for index in range(1, 5)
    ], topology_kind="periodic_channels", refresh_policy="replace_previous")
    dispatch = _dispatch(["pulse"], coverage_marker=manifest)
    generated = [
        {"action_id": "e1", "hit_id": f"hit.{index}", "channel_id": "pulse",
         "ordinal": index, "offset_ticks": (index - 1) * 250000}
        for index in range(1, 4)
    ]
    event = _event(*generated)
    event[0]["replacements"] = [{"replaced_action_id": "e1",
        "replacing_action_id": "e2", "replacement_time_ticks": 625000,
        "cancelled_hits": 1}]

    row = _materialization_action(build_action_automation_audit(
        _single_action_plan(), event, [dispatch]))

    assert row["coverage_status"] == "complete"
    assert row["expected_occurrences"] == row["materialized_occurrences"] == 3
    assert row["cancelled_occurrences"] == [{
        "occurrence_id": "pulse:4", "component_id": "pulse", "ordinal": 4,
        "channel_id": "pulse", "offset_ticks": 750000,
        "reason_code": "cancelled_by_reviewed_replace_previous",
        "critical_domains": ["damage"]}]

    emitted_after_cancel = copy.deepcopy(event)
    emitted_after_cancel[0]["generated"].append({
        "action_id": "e1", "hit_id": "hit.4", "channel_id": "pulse",
        "ordinal": 4, "offset_ticks": 750000})
    tampered = _materialization_action(build_action_automation_audit(
        _single_action_plan(), emitted_after_cancel, [dispatch]))
    assert tampered["coverage_status"] == "incomplete"
    assert [item["occurrence_id"] for item in tampered["unexpected_occurrences"]] == ["pulse:4"]


def test_replace_boundary_uses_lifecycle_time_and_order_projection():
    manifest = _coverage(["pulse"], [
        {"occurrence_id": f"pulse:{index}", "component_id": "pulse",
         "channel_id": "pulse", "ordinal": index} for index in range(1, 5)
    ], topology_kind="periodic_channels", refresh_policy="replace_previous")
    dispatch = _dispatch(["pulse"], coverage_marker=manifest)
    generated = [
        {"action_id": "e1", "hit_id": f"hit.{index}", "channel_id": "pulse",
         "ordinal": index, "offset_ticks": (index - 1) * 250000,
         "expected_time_ticks": (index - 1) * 250000,
         "order": 0 if index == 1 else 5}
        for index in (1, 2)
    ]
    event = _event(*generated)
    event[0]["replacements"] = [{"replaced_action_id": "e1",
        "replacing_action_id": "e2", "replacement_time_ticks": 250000,
        "replacement_order": 10, "cancelled_hits": 2}]
    projection_rows = [{"generation": 1, "trigger_id": "e1",
                        "root_action_id": "e1", "channel_id": "pulse",
                        "ordinal": index, "time_ticks": (index - 1) * 250000,
                        "order": 0 if index == 1 else 5}
                       for index in range(1, 5)]
    event[0]["lifecycle_projection"] = {
        "contract": "periodic-lifecycle-projection-v1", "status": "ready",
        "generations": [{"generation": 1, "trigger_id": "e1",
            "root_action_id": "e1", "status": "replaced"}],
        "occurrences": projection_rows[:2],
        "cancelled": [{**row, "reason": "replace_previous"}
                      for row in projection_rows[2:]],
        "deferred": [], "skipped": [], "summary": {"generations": 1,
            "materialized_occurrences": 2, "cancelled_occurrences": 2,
            "deferred_intervals": 0, "skipped_controls": 0}}

    row = _materialization_action(build_action_automation_audit(
        _single_action_plan(), event, [dispatch]))

    assert row["coverage_status"] == "complete"
    assert row["materialized_occurrences"] == row["expected_occurrences"] == 2
    assert [item["ordinal"] for item in row["cancelled_occurrences"]] == [3, 4]

    tampered = copy.deepcopy(event)
    tampered[0]["lifecycle_projection"]["summary"]["generations"] = 2
    rejected = _materialization_action(build_action_automation_audit(
        _single_action_plan(), tampered, [dispatch]))
    assert rejected["coverage_status"] == "incomplete"
    assert {item["code"] for item in rejected["issues"]} == {
        "lifecycle_projection_invalid"}

    del event[0]["lifecycle_projection"]
    pending = _materialization_action(build_action_automation_audit(
        _single_action_plan(), event, [dispatch]))
    assert pending["coverage_status"] == "incomplete"
    assert "lifecycle_projection_pending" in {
        issue["code"] for issue in pending["issues"]}


def test_replace_previous_count_mismatch_and_external_refresh_fail_closed():
    manifest = _coverage(["pulse"], [
        {"occurrence_id": f"pulse:{index}", "component_id": "pulse",
         "channel_id": "pulse", "ordinal": index} for index in range(1, 5)
    ], topology_kind="periodic_channels", refresh_policy="replace_previous")
    dispatch = _dispatch(["pulse"], coverage_marker=manifest)
    generated = [{"action_id": "e1", "hit_id": f"hit.{index}", "channel_id": "pulse",
                  "ordinal": index, "offset_ticks": (index - 1) * 250000}
                 for index in range(1, 5)]
    mismatch = _event(*generated)
    mismatch[0]["replacements"] = [{"replaced_action_id": "e1",
        "replacing_action_id": "e2", "replacement_time_ticks": 625000,
        "cancelled_hits": 2}]
    row = _materialization_action(build_action_automation_audit(
        _single_action_plan(), mismatch, [dispatch]))
    assert row["coverage_status"] == "incomplete"
    assert "lifecycle_projection_invalid" in {issue["code"] for issue in row["issues"]}

    external = _event(*generated)
    external[0]["refresh_contract"] = {"operation": "replace"}
    external_row = _materialization_action(build_action_automation_audit(
        _single_action_plan(), external, [dispatch]))
    assert external_row["coverage_status"] == "incomplete"
    assert "lifecycle_projection_pending" in {
        issue["code"] for issue in external_row["issues"]}


def test_closed_public_validator_rejects_forged_complete_empty_receipt():
    forged = {"contract": "action-materialization-receipt-v1", "status": "complete",
        "calculation_class": "normal", "summary": {}, "actions": []}
    with pytest.raises(ValueError):
        validate_action_materialization_receipt(forged)


def test_dispatch_receipt_marks_non_damage_action_not_applicable():
    audit = build_action_automation_audit(
        _plan(), [], [_dispatch(["primary"], coverage_marker=False)])
    row = next(item for item in audit["materialization_receipt"]["actions"]
               if item["action_id"] == "s1")
    assert row["coverage_status"] == "not_applicable"
    assert row["calculation_class"] == "normal"
    assert row["expected_components"] == row["expected_occurrences"] == 0
    validate_action_materialization_receipt(audit["materialization_receipt"])


def test_no_dispatch_compilations_do_not_publish_materialization_receipt():
    audit = build_action_automation_audit(_plan(), [], [])
    assert "materialization_receipt" not in audit


def test_public_materialization_receipt_is_identical_in_result_and_result_receipt():
    dispatch = _dispatch(["primary"], coverage_marker=False)
    event = _event({"action_id": "e1", "hit_id": "hit.1", "component_id": "primary"})
    _, result, receipt = _sealed_materialization(_plan(), event, [dispatch])

    validate_action_materialization_result_receipt(result, receipt)

    receipt["action_materialization_receipt"]["status"] = "complete"
    with pytest.raises(ValueError):
        validate_action_materialization_result_receipt(result, receipt)


def test_public_receipt_is_bound_to_result_and_action_event_compilations():
    dispatch = _dispatch(["primary"], coverage_marker=False)
    event = _event({"action_id": "e1", "hit_id": "hit.1", "component_id": "primary"})
    public, result, receipt = _sealed_materialization(
        _single_action_plan(), event, [dispatch])
    validate_action_materialization_result_receipt(result, receipt)

    # Re-signing only the two public projections and their local proof no longer
    # bypasses the generic result binding.
    result["action_materialization_receipt"]["actions"][0]["issues"].append({
        "code": "tampered", "path": "rotation_model.actions.e1", "message": "tampered"})
    receipt["action_materialization_receipt"] = copy.deepcopy(
        result["action_materialization_receipt"])
    receipt["action_materialization_proof"]["public_receipt_fingerprint"] = fingerprint(
        result["action_materialization_receipt"])
    with pytest.raises(ValueError, match="完整计算结果"):
        validate_action_materialization_result_receipt(result, receipt)

    result["action_materialization_receipt"] = copy.deepcopy(public)
    receipt["action_materialization_receipt"] = copy.deepcopy(public)
    receipt["action_materialization_proof"]["public_receipt_fingerprint"] = fingerprint(public)
    result["action_event_compilations"][0]["generated"][0]["hit_id"] = "tampered.hit"
    receipt["result_fingerprint"] = fingerprint(result)
    with pytest.raises(ValueError, match="动作派发与事件编译来源"):
        validate_action_materialization_result_receipt(result, receipt)


def test_forged_complete_row_cannot_be_resigned_against_empty_event_compilations():
    plan = _single_action_plan()
    manifest = _coverage(["primary"])
    dispatch = _dispatch(["primary"], coverage_marker=manifest)
    event = _event({"action_id": "e1", "hit_id": "hit.1", "component_id": "primary",
                    "step_id": "primary", "ordinal": 1, "topology_order": 0,
                    "terminal": True, "offset_ticks": 0})
    public, result, receipt = _sealed_materialization(plan, event, [dispatch])
    assert public["status"] == "complete"
    validate_action_materialization_result_receipt(
        result, receipt, trusted_manifest_bundle=_trusted_bundle(manifest))

    # The attacker rewrites every public digest but has no generated occurrence.
    # Replay from the frozen raw manifest and dispatch record must still fail.
    result["action_event_compilations"] = []
    receipt["action_materialization_proof"][
        "action_event_compilations_fingerprint"] = fingerprint([])
    receipt["result_fingerprint"] = fingerprint(result)
    with pytest.raises(ValueError, match="重建"):
        validate_action_materialization_result_receipt(
            result, receipt, trusted_manifest_bundle=_trusted_bundle(manifest))


def test_coordinated_resign_cannot_replace_trusted_gameplay_manifest():
    plan = _single_action_plan()
    trusted_manifest = _coverage(["primary", "secondary"])
    trusted_dispatch = _dispatch(
        ["primary", "secondary"], coverage_marker=trusted_manifest)
    trusted_events = _event(
        {"action_id": "e1", "hit_id": "hit.primary", "component_id": "primary",
         "step_id": "primary", "ordinal": 1, "topology_order": 0,
         "terminal": False, "offset_ticks": 0},
        {"action_id": "e1", "hit_id": "hit.secondary", "component_id": "secondary",
         "step_id": "secondary", "ordinal": 2, "topology_order": 0,
         "terminal": True, "offset_ticks": 100000},
    )
    public, result, receipt = _sealed_materialization(
        plan, trusted_events, [trusted_dispatch])
    assert public["status"] == "complete"
    trusted_bundle = _trusted_bundle(trusted_manifest)
    validate_action_materialization_result_receipt(
        result, receipt, trusted_manifest_bundle=trusted_bundle)

    # Rewrite every self-contained fact and every digest coherently.  Internal
    # replay alone would accept this truncated one-component world; the trusted
    # runtime manifest must remain the independent authority.
    forged_manifest = _coverage(["primary"])
    forged_dispatch = _dispatch(["primary"], coverage_marker=forged_manifest)
    forged_events = _event(
        {"action_id": "e1", "hit_id": "hit.primary", "component_id": "primary",
         "step_id": "primary", "ordinal": 1, "topology_order": 0,
         "terminal": True, "offset_ticks": 0})
    forged_public = build_action_materialization_receipts(
        plan, forged_events, [forged_dispatch])
    assert forged_public["status"] == "complete"
    result["action_materialization_source"]["action_dispatch_compilations"] = [
        forged_dispatch]
    result["action_event_compilations"] = forged_events
    result["action_materialization_receipt"] = copy.deepcopy(forged_public)
    receipt["action_materialization_receipt"] = copy.deepcopy(forged_public)
    receipt["action_materialization_proof"] = {
        "contract": "action-materialization-proof-v1",
        "public_receipt_fingerprint": fingerprint(forged_public),
        "action_event_compilations_fingerprint": fingerprint(forged_events),
        "source_fingerprint": fingerprint(result["action_materialization_source"]),
    }
    receipt["result_fingerprint"] = fingerprint(result)

    with pytest.raises(ValueError, match="受信运行时清单"):
        validate_action_materialization_result_receipt(
            result, receipt, trusted_manifest_bundle=trusted_bundle)


@pytest.mark.parametrize("field", [
    "selector", "declared_coverage_manifest_id",
    "expected_coverage_manifest_fingerprint",
])
def test_forged_complete_cannot_rebind_manifest_away_from_declared_source(field):
    plan = _single_action_plan()
    dispatch = _dispatch(["primary"])
    event = _event({"action_id": "e1", "hit_id": "hit.1", "component_id": "primary",
                    "step_id": "primary", "ordinal": 1, "topology_order": 0,
                    "terminal": True, "offset_ticks": 0})
    public, result, receipt = _sealed_materialization(plan, event, [dispatch])
    assert public["status"] == "complete"
    frozen = result["action_materialization_source"]["action_dispatch_compilations"][0]
    if field == "selector":
        frozen[field] = {**frozen[field], "character_id": 9999}
    elif field == "declared_coverage_manifest_id":
        frozen[field] = "other.manifest"
    else:
        frozen[field] = "f" * 64
    receipt["action_materialization_proof"]["source_fingerprint"] = fingerprint(
        result["action_materialization_source"])
    receipt["result_fingerprint"] = fingerprint(result)

    with pytest.raises(ValueError, match="重建"):
        validate_action_materialization_result_receipt(result, receipt)


def test_materialization_source_rejects_oversized_forged_json_before_replay():
    dispatch = _dispatch(["primary"], coverage_marker=False)
    event = _event({"action_id": "e1", "hit_id": "hit.1", "component_id": "primary"})
    _, result, receipt = _sealed_materialization(
        _single_action_plan(), event, [dispatch])
    result["action_materialization_source"]["action_dispatch_compilations"][0][
        "oversized"] = "x" * (2 * 1024 * 1024)
    receipt["action_materialization_proof"]["source_fingerprint"] = fingerprint(
        result["action_materialization_source"])
    receipt["result_fingerprint"] = fingerprint(result)

    with pytest.raises(ValueError, match="2MiB"):
        validate_action_materialization_result_receipt(result, receipt)


@pytest.mark.parametrize("target", ["actions", "generated"])
def test_materialization_source_rejects_excessive_replay_cardinality(target):
    dispatch = _dispatch(["primary"], coverage_marker=False)
    event = _event({"action_id": "e1", "hit_id": "hit.1", "component_id": "primary"})
    _, result, receipt = _sealed_materialization(
        _single_action_plan(), event, [dispatch])
    if target == "actions":
        result["action_materialization_source"]["rotation_model"]["actions"] = [
            {"id": "e1", "ability_id": "e", "start": 0}] * 4097
        receipt["action_materialization_proof"]["source_fingerprint"] = fingerprint(
            result["action_materialization_source"])
    else:
        result["action_event_compilations"] = [{"profile_id": "profile",
            "status": "ready", "generated": [{}] * 40001}]
        receipt["action_materialization_proof"][
            "action_event_compilations_fingerprint"] = fingerprint(
                result["action_event_compilations"])
    receipt["result_fingerprint"] = fingerprint(result)

    with pytest.raises(ValueError, match="编译来源"):
        validate_action_materialization_result_receipt(result, receipt)
