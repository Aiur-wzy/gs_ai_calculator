"""Incremental fact registration must preserve past frames and total budgets."""
import copy
import json

import pytest
from test_attribute_ledger import attach, contribution, declaration, read

from genshin_engine import attribute_ledger as ledger_module
from genshin_engine.attribute_ledger import AttributeLedger, AttributeLedgerError


def empty_ledger(**kwargs):
    data = declaration()
    data["frames"] = []
    return AttributeLedger(data, **kwargs)


def frame(ident="f0", members=()):
    result = declaration()["frames"][0]
    result.update(id=ident, run_id="0:" + ident, event_id=ident, contribution_ids=list(members))
    return result


def test_future_pending_read_can_become_known_only_after_frame_adoption():
    ledger = empty_ledger()
    pending = read(ledger)
    assert pending["status"] == "pending"
    ident = ledger.adopt_frame(frame(), contributions=[])
    assert ident == "f0"
    assert read(ledger)["value"] == 300
    assert pending["status"] == "pending" and "value" not in pending


def test_new_frame_and_node_do_not_change_prior_capture_or_receipt():
    ledger = empty_ledger()
    ledger.adopt_frame(frame())
    before = read(ledger)
    later = frame("post", ["gain"])
    later.update(phase="post", state_epoch=5)
    node = contribution()
    ledger.adopt_frame(later, contributions=[node])
    node["value"] = 10000
    later["contribution_ids"] = []
    assert read(ledger) == before
    assert read(ledger, frame="post")["value"] == 340


def test_semantically_same_frame_registration_is_idempotent_not_an_alias():
    ledger = empty_ledger()
    nodes = [contribution("a", 20), contribution("b", 40)]
    snapshot = frame(members=["a", "b"])
    ledger.adopt_frame(snapshot, contributions=nodes)
    old = read(ledger)
    reordered = dict(reversed(list(snapshot.items())))
    reordered["contribution_ids"].reverse()
    assert ledger.adopt_frame(reordered, contributions=list(reversed(nodes))) == "f0"
    assert read(ledger) == old


@pytest.mark.parametrize("changed", ["t_us", "state_epoch", "contribution_ids"])
def test_same_frame_id_cannot_replace_past_facts(changed):
    ledger = empty_ledger()
    snapshot = frame()
    ledger.adopt_frame(snapshot)
    old = read(ledger)
    if changed == "contribution_ids":
        snapshot[changed] = ["gain"]
    else:
        snapshot[changed] += 1
    with pytest.raises(AttributeLedgerError):
        ledger.adopt_frame(snapshot, contributions=[contribution()])
    assert read(ledger) == old


@pytest.mark.parametrize("changed,value", [
    ("value", 999), ("nonextra_inclusion", "excluded"),
    ("component", {"source_fingerprint": "changed", "provider": "b", "part_id": "em", "effect_id": "gain", "recipient": "a"}),
])
def test_registered_node_cannot_be_replaced_under_an_existing_id(changed, value):
    ledger = AttributeLedger(attach(declaration(), contribution()))
    old = read(ledger)
    altered = contribution()
    altered[changed] = value
    with pytest.raises(AttributeLedgerError):
        ledger.adopt_frame(frame("later", ["gain"]), contributions=[altered])
    assert read(ledger) == old
    assert read(ledger, frame="later")["status"] == "pending"


def test_failed_adoption_does_not_register_even_a_valid_new_node():
    ledger = empty_ledger()
    with pytest.raises(AttributeLedgerError):
        ledger.adopt_frame(frame(members=["gain", "missing"]), contributions=[contribution()])
    assert read(ledger)["status"] == "pending"
    # If the failed attempt leaked gain, this would incorrectly succeed.
    with pytest.raises(AttributeLedgerError):
        ledger.adopt_frame(frame(members=["gain"]))
    ledger.adopt_frame(frame(members=["gain"]), contributions=[contribution()])
    assert read(ledger)["value"] == 340


def test_duplicate_registration_is_charged_and_cannot_reset_query_budget(monkeypatch):
    limits = {'registration_work': 8}
    ledger = empty_ledger(limits=limits)
    ledger.adopt_frame(frame())
    first = read(ledger)
    limited = False
    for _ in range(10):
        try:
            ledger.adopt_frame(frame())
        except AttributeLedgerError as exc:
            assert exc.issue["code"] == "attribute_registration_limit"
            limited = True
            break
    assert limited and read(ledger) == first


def test_invalid_attempts_are_charged_and_eventually_refused(monkeypatch):
    limits = {'registration_work': 5}
    ledger = empty_ledger(limits=limits)
    reasons = []
    for _ in range(4):
        with pytest.raises(AttributeLedgerError) as error:
            ledger.adopt_frame(frame(members=["unknown"]))
        reasons.append(error.value.issue["code"])
    assert reasons[-1] == "attribute_registration_limit"


def test_registration_and_queries_share_an_execution_budget(monkeypatch):
    limits = {'execution_work': 3}
    ledger = empty_ledger(limits=limits)
    ledger.adopt_frame(frame())
    assert read(ledger)["value"] == 300
    result = read(ledger, "nonextra")
    assert result["status"] == "pending" and "value" not in result
    assert result["issues"][0]["code"] == "attribute_work_limit"


def test_total_frame_limit_counts_unique_commits_not_replays(monkeypatch):
    limits = {'frames': 1}
    ledger = empty_ledger(limits=limits)
    ledger.adopt_frame(frame())
    ledger.adopt_frame(copy.deepcopy(frame()))
    with pytest.raises(AttributeLedgerError):
        ledger.adopt_frame(frame("next"))
    assert read(ledger)["value"] == 300
    assert read(ledger, frame="next")["status"] == "pending"


def test_cumulative_canonical_declaration_bytes_include_only_unique_commits(monkeypatch):
    complete = declaration()
    complete["frames"] = [frame()]
    size = len(json.dumps(complete, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf8"))
    limits = {'declaration_bytes': size}
    ledger = empty_ledger(limits=limits)
    ledger.adopt_frame(frame())
    ledger.adopt_frame(frame())
    assert read(ledger)["value"] == 300
    with pytest.raises(AttributeLedgerError) as exc:
        ledger.adopt_frame(frame("next"))
    assert exc.value.issue["code"] == "attribute_declaration_limit"
    assert read(ledger, frame="next")["status"] == "pending"


def test_registration_serialization_does_not_rescan_past_frames(monkeypatch):
    ledger = empty_ledger()
    original = ledger_module._canonical
    sizes = []

    def tracked(value):
        result = original(value)
        sizes.append(len(result))
        assert not isinstance(value, dict) or "frames" not in value
        return result

    monkeypatch.setattr(ledger_module, "_canonical", tracked)
    for index in range(50):
        ledger.adopt_frame(frame(f"frame-{index}"))
    assert max(sizes) < 500
    assert read(ledger, frame="frame-49")["value"] == 300


def test_new_frame_alias_rejection_is_atomic_for_new_contributions():
    ledger = empty_ledger()
    original = frame()
    ledger.adopt_frame(original)
    alias = {**copy.deepcopy(original), "id": "alias", "contribution_ids": ["gain"]}
    with pytest.raises(AttributeLedgerError) as exc:
        ledger.adopt_frame(alias, contributions=[contribution()])
    assert exc.value.issue["code"] == "attribute_frame_alias"
    with pytest.raises(AttributeLedgerError):
        ledger.adopt_frame(frame("later", ["gain"]))


def test_excess_nested_opaque_expression_is_a_domain_error():
    value = {"kind": "stat"}
    for _ in range(40):
        value = {"nested": value}
    ledger = empty_ledger(limits={'declaration_depth':32})
    with pytest.raises(AttributeLedgerError):
        ledger.adopt_frame(frame(members=["gain"]), contributions=[contribution(value=value)])
    assert read(ledger)["status"] == "pending"
