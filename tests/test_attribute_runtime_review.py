"""Independent runtime frame/budget oracles, using declared synthetic sources."""
import copy

import pytest

from genshin_engine.mechanics import execute_mechanics
from tests.test_attribute_runtime import classify, enable, reader
from tests.test_mechanics_execution import (
    add,
    condition,
    damages,
    fixture,
    rule,
    second_character,
)


def test_real_timer_capture_receipts_retain_distinct_dispatch_occurrence_ids():
    from tests.test_state_runtime_execution import field_case
    t, r, m = field_case()
    enable(t, m)
    effect = m['rules'][0]['effects'][0]
    effect['value'] = {'kind': 'stat', 'subject': 'owner', 'stat': 'em',
                       'basis': 'nonextra', 'factor': 1, 'sample': 'trigger'}
    out = execute(t, r, m)
    assert out['status'] in ('passed', 'unproven') and out['result'] is not None, out['issues']
    assert out['issues'] == []
    activations = [row for row in out['mechanics_trace'] if row['type'] == 'activation']
    ids = {row['run_id'] for row in activations}
    routes = out['attribute_ledger']['frame_routes']
    timer_routes = [row for row in routes if row['actual_event_run_id'] in ids]
    assert len(ids) == 2
    assert {row['actual_event_run_id'] for row in timer_routes} == ids
    assert all(row['run_id'] == '0:' + row['actual_event_run_id'] for row in timer_routes)
    assert len({row['frame_id'] for row in timer_routes}) == 2


def execute(t, r, m):
    before = copy.deepcopy((t, r, m))
    result = execute_mechanics(t, r, m)
    assert (t, r, m) == before
    return result


@pytest.mark.parametrize("writer_id", ["0-first", "z-last"])
def test_same_phase_mixed_trigger_and_current_read_have_distinct_frames(writer_id):
    t, r, m = fixture((.2,))
    enable(t, m)
    add(m, reader("capture-reader", "nonextra", "trigger"),
        rule(writer_id, "benefit.em.all", 50), reader("current-reader", "nonextra"))
    classify(m, writer_id)
    out = execute(t, r, m)
    assert out["status"] == "passed", out["issues"]
    assert damages(out) == pytest.approx([157.5])  # (ATK100 + old100 + current150) * .5 * .9
    samples = {s["rule_id"]: s for s in out["sampling_trace"] if s["cycle"] == 0}
    assert samples["capture-reader"]["source_value"] == 100
    assert samples["current-reader"]["source_value"] == 150
    assert samples["capture-reader"]["attribute_receipt_id"] != samples["current-reader"]["attribute_receipt_id"]


def test_independent_layers_keep_distinct_historical_basis_then_replace_oldest():
    t, r, m = fixture((.2, .4, .6))
    enable(t, m)
    condition(m, "gain", .3)
    add(m, rule("em", "benefit.em.all", 50, trigger={"event": "condition", "phase": "pre", "event_ids": ["gain"]}),
        reader(sample="trigger", basis="nonextra", trigger={"event": "hit", "phase": "pre"},
               stacking={"max": 2, "add": 1, "policy": "independent"}))
    classify(m, "em")
    out = execute(t, r, m)
    assert out["status"] == "passed", out["issues"]
    assert damages(out) == pytest.approx([90, 157.5, 180])
    samples = [s for s in out["sampling_trace"] if s["cycle"] == 0]
    assert [s["source_value"] for s in samples] == [100, 100, 150, 150, 150]
    assert samples[0]["attribute_receipt_id"] == samples[1]["attribute_receipt_id"]
    assert samples[0]["attribute_receipt_id"] != samples[2]["attribute_receipt_id"]


def test_once_manual_source_not_reused_by_same_hit_id_in_later_origin():
    t, r, m = fixture((.2,))
    enable(t, m)
    m["manual_buff_repeat"] = "once"
    t["buffs"] = [{"id": "one-time", "start": 0, "end": .3, "target": "a", "extensions": {"owner": "a"},
        "effects": [{"id": "value", "zone": "em", "stat": "all", "benefit_type_id": "benefit.em.all", "value": 50,
                     "applies_to": {"mode": "all"}}]}]
    m["attribute_model"]["effect_classifications"] = [{"effect_ref": {"kind": "manual_buff", "buff_id": "one-time", "effect_id": "value"},
        "component": {"provider": "a", "part_id": "one-time", "effect_id": "value", "source_fingerprint": "engineering-once"},
        "nonextra_inclusion": "included", "status": "assumed", "source": "One finite engineering window"}]
    add(m, reader(basis="nonextra"))
    out = execute(t, r, m)
    assert out["status"] == "passed", out["issues"]
    assert damages(out) == pytest.approx([112.5])
    assert [(s["cycle"], s["source_value"]) for s in out["sampling_trace"]] == [(0, 150), (1, 100), (2, 100)]
    receipts = out["attribute_ledger"]["receipts"]
    assert {x["query"]["frame"]["origin"] for x in receipts} == {0, 1, 2}


def test_budget_failure_preserves_receipt_prefix_without_a_damage_result(monkeypatch):
    t, r, m = fixture((.2, .4, .6))
    enable(t, m)
    add(m, reader())
    m.setdefault('execution_options', {'schema_version':1, 'attribute_limits':{}})['attribute_limits']['execution_work'] = 7
    out = execute(t, r, m)
    assert out["status"] == "pending" and out["result"] is None
    report = out["attribute_ledger"]
    assert len(report["receipts"]) == 1 and report["receipts"][0]["value"] == 300
    assert report["usage"]["execution_work"] <= 7
    assert report["usage"]["execution_work"] == sum(report["usage"][k] for k in ("query_work", "capture_work", "registration_work"))
    assert out["rotation_check"]["event_log"][-1]["status"] == "pending"


def test_unrelated_actor_em_overflow_cannot_block_closed_owner_nonextra_read():
    t, r, m = fixture((.2,))
    enable(t, m)
    second_character(t, r)
    add(m, reader(basis="nonextra"))
    for name in ("large-one", "large-two"):
        add(m, rule(name, "benefit.em.all", 1e308, owner="b", beneficiaries={"mode": "self", "scope": "locked"}))
        classify(m, name, "unknown")
        m["attribute_model"]["effect_classifications"][-1]["component"]["provider"] = "b"
    out = execute(t, r, m)
    assert out["status"] == "passed", out["issues"]
    assert damages(out) == pytest.approx([90])
    assert all(x["query"]["actor"] == "a" for x in out["attribute_ledger"]["receipts"])


def test_same_overflow_is_refused_when_that_actor_is_actually_requested():
    t, r, m = fixture((.2,))
    enable(t, m)
    second_character(t, r)
    add(m, reader(basis="total", owner="b"))
    for name in ("large-one", "large-two"):
        add(m, rule(name, "benefit.em.all", 1e308, owner="b", beneficiaries={"mode": "self", "scope": "locked"}))
        classify(m, name, "included")
        m["attribute_model"]["effect_classifications"][-1]["component"]["provider"] = "b"
    out = execute(t, r, m)
    assert out["status"] in ("pending", "invalid") and out["result"] is None
    assert any(i["code"] == "attribute_value_invalid" for i in out["issues"])
