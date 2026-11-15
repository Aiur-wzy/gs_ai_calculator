"""Activation receipts identify refreshes without changing numerical semantics."""
from test_mechanics_execution import add, fixture, rule, run


def test_activation_refresh_retains_instance_and_reactivation_gets_new_identity():
    template, rotation, model = fixture((.2, .4, 1.5, 2.6))
    add(model, rule(trigger={"event": "hit", "phase": "pre", "event_ids": ["h0", "h1", "h2"]}, duration=1))
    executed = run(template, rotation, model)
    activations = [row for row in executed["mechanics_trace"] if row["type"] == "activation" and row["cycle"] == 0]
    assert activations[0]["instance_id"] == activations[1]["instance_id"]
    assert activations[2]["instance_id"] != activations[1]["instance_id"]
    expiries = [row for row in executed["mechanics_trace"] if row["type"] == "expiry"]
    assert {row["instance_id"] for row in activations} <= {row["instance_id"] for row in expiries}
    assert [hit["expected"] for hit in executed["result"]["hits"]] == [90, 90, 90, 45]
