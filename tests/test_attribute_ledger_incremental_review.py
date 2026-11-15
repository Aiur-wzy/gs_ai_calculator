"""Independent incremental-contract edge cases; no mechanics wiring implied."""
import copy

import pytest
from test_attribute_ledger import contribution, read
from test_attribute_ledger_incremental import empty_ledger, frame

from genshin_engine.attribute_ledger import AttributeLedgerError


def test_old_pending_receipt_and_old_capture_remain_independent_after_new_frame():
    ledger = empty_ledger()
    pending = read(ledger, frame="future")
    ledger.adopt_frame(frame())
    captured = read(ledger)
    ledger.adopt_frame(frame("future", ["gain"]), contributions=[contribution(value=90)])
    assert pending["status"] == "pending" and "value" not in pending
    assert read(ledger) == captured and read(ledger, frame="future")["value"] == 390
    read(ledger, frame="future")["contributions"].clear()
    assert read(ledger, frame="future")["value"] == 390


def test_query_work_exhaustion_blocks_new_registration_without_changing_history(monkeypatch):
    limits = {'execution_work': 4}
    ledger = empty_ledger(limits=limits)
    ledger.adopt_frame(frame())
    old = read(ledger)
    assert read(ledger, "nonextra")["status"] == "known"
    with pytest.raises(AttributeLedgerError) as err:
        ledger.adopt_frame(frame("later"))
    assert err.value.issue["code"] == "attribute_registration_limit"
    assert read(ledger) == old


def test_failed_registration_exhaustion_does_not_poison_already_cached_receipt(monkeypatch):
    limits = {'execution_work': 7}
    ledger = empty_ledger(limits=limits)
    ledger.adopt_frame(frame())
    old = read(ledger)
    with pytest.raises(AttributeLedgerError):
        ledger.adopt_frame(frame("bad", ["missing"]))
    with pytest.raises(AttributeLedgerError):
        ledger.adopt_frame(frame("later"))
    assert read(ledger) == old
    assert read(ledger, frame="later")["status"] == "pending"


def test_old_node_can_be_reused_with_new_layer_but_never_reclassified():
    ledger = empty_ledger()
    first = contribution("layer-one", 20)
    second = contribution("layer-two", 60)
    second["component"] = copy.deepcopy(first["component"])
    second["layer_id"] = "layer-two"
    ledger.adopt_frame(frame(members=[first["id"]]), contributions=[first])
    old = read(ledger)
    ledger.adopt_frame(frame("later", [first["id"], second["id"]]), contributions=[second])
    assert read(ledger) == old and read(ledger, frame="later")["value"] == 380
    changed = copy.deepcopy(first)
    changed["nonextra_inclusion"] = "excluded"
    with pytest.raises(AttributeLedgerError):
        ledger.adopt_frame(frame("bad", [first["id"]]), contributions=[changed])
    assert read(ledger, frame="later")["value"] == 380


def test_origin_phase_and_epoch_are_distinct_at_same_microsecond():
    ledger = empty_ledger()
    ledger.adopt_frame(frame())
    later = frame("later", ["gain"])
    later.update(run_id="0:f0", phase="post", state_epoch=1)
    ledger.adopt_frame(later, contributions=[contribution()])
    assert read(ledger)["value"] == 300 and read(ledger, frame="later")["value"] == 340
    alias = copy.deepcopy(later)
    alias.update(id="alias", event_id="changed", t_us=100)
    with pytest.raises(AttributeLedgerError) as err:
        ledger.adopt_frame(alias)
    assert err.value.issue["code"] == "attribute_frame_alias"


def test_unreferenced_new_node_cannot_escape_through_old_frame_replay():
    ledger = empty_ledger()
    ledger.adopt_frame(frame())
    with pytest.raises(AttributeLedgerError):
        ledger.adopt_frame(frame(), contributions=[contribution()])
    with pytest.raises(AttributeLedgerError):
        ledger.adopt_frame(frame("later", ["gain"]))
