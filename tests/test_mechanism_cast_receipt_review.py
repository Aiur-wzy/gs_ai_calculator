"""EC22 result-only proof checks built from actual typed/binder/D20 execution.

The saved-node fixture is engineering data, not a fresh DB approval test.
"""
import copy
import sqlite3

import pytest

from genshin_app.equipment import snapshot_capture_receipt as captures
from genshin_app.equipment.files import fingerprint, input_view
from genshin_app.equipment.mechanism_receipt import (
    make_mechanism_receipt,
    mechanism_ledger_fingerprint,
    mechanism_summary,
    validate_mechanism_result_receipt,
)
from genshin_app.equipment.mechanism_sources import make_replay_context
from genshin_app.rotation_check import describe_rotation_check, resolve_rotation_model
from genshin_engine.mechanics import execute_mechanics
from tests.test_mechanism_cast_integration import compose, scene


def executed_case(*, include_capture_proof=False):
    plan, template, material, requirements = scene()
    template["characters"][0]["stats"]["energy_recharge"] = 1
    _, records, _, _ = compose(plan, template, material, requirements)
    plan["mechanism_sources"][0].update(records[0]["required_confirmation"])
    model, records, _, issues = compose(plan, template, material, requirements)
    assert not issues and model is not None, issues
    rotation, panel_bindings = resolve_rotation_model(template, plan["rotation_model"])
    actual = execute_mechanics(template, rotation, model)
    assert actual["result"] is not None, actual["issues"]
    result = actual["result"]
    result["rotation_check"] = describe_rotation_check(actual["rotation_check"], template, rotation,
        panel_bindings, mechanics_model=model, matches=[])
    result["mechanics_trace"] = actual["mechanics_trace"]
    result["sampling_trace"] = actual.get("sampling_trace", [])
    result["mechanics_status"] = actual["status"]
    result["mechanics_issues"] = actual.get("issues", [])
    context = make_replay_context(plan, template, records=records, action_event_compilations=[],
        attack_metadata_compilations=[], parameter_compilations=[], equipment_requirements=requirements)
    result["mechanism_compilations"] = copy.deepcopy(records)
    result["mechanism_replay_context"] = context
    result["mechanism_execution"] = {"model": copy.deepcopy(model), "template": copy.deepcopy(template)}
    result["mechanism_summary"] = mechanism_summary(result)
    report = {"mechanics_model": model, "mechanics_input_template": template,
              "output_fingerprint": fingerprint(actual["resolved_template"])}
    receipt = {"mechanisms": make_mechanism_receipt(plan, report, result),
               "result_fingerprint": fingerprint(result)}
    if include_capture_proof:
        receipt.update(input_fingerprint=fingerprint(input_view(plan)), output_fingerprint=report["output_fingerprint"])
        receipt["snapshot_captures"] = captures.make_snapshot_capture_receipt(plan, report, result)
        receipt["result_fingerprint"] = fingerprint(result)
    return result, receipt


def test_review_valid_cast_has_no_fabricated_action_event_receipt_and_no_offline_database(monkeypatch):
    result, receipt = executed_case()
    assert "action_events" not in receipt
    assert not result.get("action_event_compilations")
    assert len(result["hits"]) == 3
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("offline proof opened DB"))
    validate_mechanism_result_receipt(result, receipt)


@pytest.mark.parametrize("fault", ["identity", "equipment", "source", "all_proofs"])
def test_review_missing_proofs_are_not_repaired_by_outer_result_hash(fault):
    result, receipt = executed_case()
    if fault == "identity":
        del result["mechanism_compilations"][0]["parent_graph"]["dependencies"][0]["identity_proof"]
    elif fault == "equipment":
        result["mechanism_replay_context"]["equipment_requirements"] = []
    elif fault == "source":
        del result["mechanism_compilations"][0]["source_material"]
    else:
        for key in ("mechanism_compilations", "mechanism_replay_context", "mechanism_execution", "mechanism_summary"):
            del result[key]
        del receipt["mechanisms"]
    receipt["result_fingerprint"] = fingerprint(result)
    with pytest.raises(ValueError):
        validate_mechanism_result_receipt(result, receipt)


@pytest.mark.parametrize("fault", ["model", "template", "rotation"])
def test_review_no_action_event_anchor_still_binds_full_execution_to_replayed_context(fault):
    result, receipt = executed_case()
    proof = receipt["mechanisms"]
    if fault == "model":
        model = result["mechanism_execution"]["model"]
        model["hit_events"][0]["self_snapshot_event"] = "cast"
        proof["compiled_mechanics_model_fingerprint"] = fingerprint(model)
    elif fault == "template":
        template = result["mechanism_execution"]["template"]
        template["characters"][0]["stats"]["atk"] = 999
        proof["mechanics_input_template_fingerprint"] = fingerprint(template)
    else:
        model = result["rotation_check"]["resolved_rotation_model"]
        model["abilities"][0]["energy"]["amount"] = 0
        proof["execution_rotation_model_fingerprint"] = fingerprint(model)
        proof["ledger_fingerprint"] = mechanism_ledger_fingerprint(result)
    receipt["result_fingerprint"] = fingerprint(result)
    with pytest.raises(ValueError):
        validate_mechanism_result_receipt(result, receipt)


def test_review_replay_context_cannot_silently_supply_a_different_capture_definition():
    result, receipt = executed_case()
    context = result["mechanism_replay_context"]
    context["plan"]["mechanics_model"]["snapshot_captures"][0]["offset"] = .8
    receipt["mechanisms"]["replay_context_fingerprint"] = fingerprint(context)
    receipt["result_fingerprint"] = fingerprint(result)
    with pytest.raises(ValueError):
        validate_mechanism_result_receipt(result, receipt)


def test_review_cast_and_capture_proofs_accept_the_same_actual_queue_offline(monkeypatch):
    result, receipt = executed_case(include_capture_proof=True)
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("combined proof opened DB"))
    before = copy.deepcopy((result, receipt))
    validate_mechanism_result_receipt(result, receipt)
    captures.validate_snapshot_capture_result_receipt(result, receipt)
    assert (result, receipt) == before
    after = (100 + 100 * .20000000298023224) * .5 * .9
    assert [h["expected"] for h in result["hits"]] == pytest.approx([after, 45, after])


@pytest.mark.parametrize("removed", ["mechanisms", "snapshot_captures"])
def test_review_one_valid_proof_cannot_replace_the_other_proof(removed):
    result, receipt = executed_case(include_capture_proof=True)
    del receipt[removed]
    receipt["result_fingerprint"] = fingerprint(result)
    validator = validate_mechanism_result_receipt if removed == "mechanisms" else captures.validate_snapshot_capture_result_receipt
    with pytest.raises(ValueError):
        validator(result, receipt)


def test_review_consumer_snapshot_change_and_rehashed_capture_envelope_cannot_rewrite_actual_queue():
    result, receipt = executed_case(include_capture_proof=True)
    model = result["snapshot_capture_execution"]["mechanics_model"]
    model["hit_events"][1]["self_snapshot_event"] = "sample"
    receipt["snapshot_captures"]["execution_fingerprint"] = fingerprint(result["snapshot_capture_execution"])
    result["rotation_check"]["input_fingerprint"] = fingerprint(result["snapshot_capture_execution"])
    receipt["result_fingerprint"] = fingerprint(result)
    with pytest.raises(ValueError):
        captures.validate_snapshot_capture_result_receipt(result, receipt)
