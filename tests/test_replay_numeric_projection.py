"""Optional display retention is separate from numerical replay and provenance."""
from __future__ import annotations

import copy

import pytest

from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.replay_numeric_projection import (
    legacy_source_vocabulary,
    numeric_budget_result,
    replay_numeric_ledgers,
    replay_output_template,
)
from tests.resource_reaction_review_helpers import live_case, resign
from tests.test_resource_reaction_app_review import saved


def trace_row():
    return {"type": "hit", "event_id": "h0", "cycle": 0, "t": 1, "status": "executed"}


@pytest.mark.parametrize("producer", ["0.46.0", "0.44.0-current-marker"])
def test_current_result_keeps_all_parent_binding_fields_even_if_removed(producer):
    old = trace_row()
    new = {**old, "action_id": "cast", "ability_id": "e", "run_id": None}
    result = {"hits": [{"id": "h0"}], "mechanics_trace": [old]}
    receipt = {"engine_version": producer.split("-", 1)[0]}
    if "marker" in producer:
        result["calculation_producer_contract"] = "current"
    expected = {"hits": [{"id": "h0", "expected": 1, "explanation_ref": "hit:h0"}], "mechanics_trace": [new]}
    actual = {"hits": [{"id": "h0", "expected": 1}], "mechanics_trace": [old]}
    left, right = replay_numeric_ledgers(expected, actual, result, receipt)
    assert left["hits"] == right["hits"]
    assert left["mechanics_trace"] != right["mechanics_trace"]
    assert not legacy_source_vocabulary(result, receipt)


def test_exact_legacy_vocabulary_projects_new_trace_fields_without_mutating_saved():
    row = trace_row()
    reaction = {**row, "type": "reaction_damage", "event_id": "derived", "reaction_run_id": "0:derived",
                "action_id": "cast", "run_id": "0:derived"}
    actual = {"hits": [{"id": "h0", "expected": 1}], "mechanics_trace": [row, reaction]}
    expected = {"hits": [{"id": "h0", "expected": 1, "explanation_ref": "hit:h0"}],
                "mechanics_trace": [{**row, "action_id": "cast", "ability_id": "e", "run_id": None},
                                    {**reaction, "ability_id": "e"}]}
    result = {"hits": actual["hits"], "mechanics_trace": actual["mechanics_trace"]}
    before = copy.deepcopy((expected, actual, result))
    left, right = replay_numeric_ledgers(expected, actual, result, {"engine_version": "0.44.0"})
    assert left == right
    assert right["mechanics_trace"][1]["action_id"] == "cast"
    assert right["mechanics_trace"][1]["run_id"] == "0:derived"
    assert (expected, actual, result) == before


@pytest.mark.parametrize("field", ["action_id", "ability_id", "run_id", "execution_ref",
                                   "in_input_window", "report_disposition"])
def test_real_current_resource_proof_cannot_drop_parent_fields_after_rehash(tmp_path, field):
    from genshin_app.equipment.resource_reaction_receipt import (
        validate_resource_reaction_result_receipt,
    )
    value = live_case(tmp_path)
    row = next(row for row in value["result"]["mechanics_trace"] if row["type"] == "hit")
    row.pop(field)
    resign(value)
    with pytest.raises(ValueError):
        validate_resource_reaction_result_receipt(value["result"], value["result_receipt"])


@pytest.mark.parametrize("producer", ["0.42.0", "0.44.0"])
def test_legacy_output_projection_keeps_values_and_other_extensions(producer):
    from genshin_engine.mechanics import execute_mechanics
    from tests.test_mechanics_execution import fixture
    template = execute_mechanics(*fixture((.2,)))['resolved_template']
    template['hits'][0]['extensions']['caller_note'] = {'source': 'retained'}
    result = {"hits": [{"id": "h0"}], "mechanics_trace": [trace_row()]}
    before = copy.deepcopy(template)
    expected = copy.deepcopy(template)
    for hit in expected['hits']:
        hit['extensions'].pop('execution_ref')
        if not hit['extensions']:
            del hit['extensions']
    assert replay_output_template(template, result, {"engine_version": producer}) == expected
    assert template == before


@pytest.mark.parametrize("marker", ["current_producer", "execution_ref", "in_input_window", "report_disposition"])
def test_current_or_partially_migrated_output_cannot_opt_into_legacy_projection(marker):
    result = {"hits": [{"id": "h0"}], "mechanics_trace": [trace_row()]}
    if marker == 'current_producer':
        result['calculation_producer_contract'] = 'genshin-calculation-result-v2'
    else:
        result['mechanics_trace'][0][marker] = None
    template = {'hits': [{'id': 'h0', 'extensions': {'execution_ref': {'must': 'remain'}}}]}
    assert not legacy_source_vocabulary(result, {"engine_version": "0.44.0"})
    assert replay_output_template(template, result, {"engine_version": "0.44.0"}) is template


def test_current_saved_output_cannot_drop_reference_from_pin_after_resigning(tmp_path):
    from genshin_app.equipment.resource_reaction_receipt import (
        validate_resource_reaction_result_receipt,
    )
    value = live_case(tmp_path)
    projected = copy.deepcopy(value['plan']['resolved_template'])
    for hit in projected['hits']:
        hit['extensions'].pop('execution_ref')
        if not hit['extensions']:
            del hit['extensions']
    assert fingerprint(projected) != value['result_receipt']['output_fingerprint']
    value['result_receipt']['output_fingerprint'] = fingerprint(projected)
    resign(value)
    with pytest.raises(ValueError):
        validate_resource_reaction_result_receipt(value['result'], value['result_receipt'])


@pytest.mark.parametrize("kind", ["excluded", "pre-current"])
def test_old_saved_output_pin_is_still_required(kind):
    import gzip
    import json
    from pathlib import Path

    from genshin_app.equipment.mechanism_receipt import (
        validate_mechanism_result_receipt,
    )
    from genshin_app.equipment.resource_reaction_receipt import (
        validate_resource_reaction_result_receipt,
    )
    if kind == 'excluded':
        value = saved('excluded')
        validate = validate_resource_reaction_result_receipt
    else:
        value = json.loads(gzip.decompress((Path(__file__).parent /
            'fixtures/mechanism_current/pre-guard-calculated.json.gz').read_bytes()))
        validate = validate_mechanism_result_receipt
    validate(value['result'], value['result_receipt'])
    value['result_receipt']['output_fingerprint'] = '0' * 64
    with pytest.raises(ValueError):
        validate(value['result'], value['result_receipt'])


def test_legacy_replay_cannot_hide_changed_time_or_work_counter_after_rehash():
    from genshin_app.equipment.resource_reaction_receipt import (
        validate_resource_reaction_result_receipt,
    )
    for change in ("time", "work"):
        value = saved("income")
        if change == "time":
            next(row for row in value["result"]["mechanics_trace"] if row["type"] == "hit")["t"] += .01
        else:
            value["result"]["resource_reactions"]["usage"]["work_consumed"] += 1
        resign(value)
        with pytest.raises(ValueError):
            validate_resource_reaction_result_receipt(value["result"], value["result_receipt"])


def test_current_engineering_work_is_not_normalized():
    row = trace_row()
    actual = {"hits": [], "mechanics_trace": [row],
              "resource_reactions": {"contract": "ordinary-reaction-resource-v1", "usage": {"work_consumed": 4}}}
    expected = copy.deepcopy(actual)
    expected["resource_reactions"]["usage"]["work_consumed"] = 5
    left, right = replay_numeric_ledgers(expected, actual, actual, {"engine_version": "0.46.0"})
    assert fingerprint(left) != fingerprint(right)


def test_private_replay_cache_bounds_only_numerical_output_without_mutating_engine(monkeypatch):
    import json

    from genshin_app.equipment.execution_verification import (
        ExecutionVerificationSession,
    )
    from genshin_engine import mechanics
    from tests.test_mechanics_execution import fixture
    values = fixture((.2, .4))
    execution = dict(zip(("template", "rotation_model", "mechanics_model"), values, strict=True), matches=[])
    raw = mechanics.execute_mechanics(**execution)
    before = copy.deepcopy(raw)
    numeric = {**raw, "result": numeric_budget_result(raw["result"])}
    size = lambda value: len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    maximum = (size(raw) + size(numeric)) // 2
    assert size(numeric) < maximum < size(raw)
    calls = []
    def execute(**kwargs):
        assert kwargs == execution
        calls.append(1)
        return raw
    monkeypatch.setattr(mechanics, "execute_mechanics", execute)
    with ExecutionVerificationSession(budget={
            "policy": "execution-verification-budget-v2", "limits": {"output_bytes": maximum}}) as session:
        first = session.replay(execution)
        first["result"]["hits"][0]["expected"] = -1
        second = session.replay(execution)
        assert second == numeric and calls == [1]
        assert session.report()["usage"]["retained_output_bytes"] == size(numeric)
        assert session.report()["usage"]["returned_bytes"] == size(numeric) * 2
    assert raw == before and "damage_explanations" in raw["result"]
    assert all("explanation_ref" in row for row in raw["result"]["hits"])
    from genshin_app.equipment.execution_verification import ExecutionVerificationError
    with ExecutionVerificationSession(budget={
            "policy": "execution-verification-budget-v2", "limits": {"output_bytes": size(numeric) - 1}}) as session:
        with pytest.raises(ExecutionVerificationError) as caught:
            session.replay(execution)
        assert caught.value.code == "verification_size"
        assert session._cache == []
