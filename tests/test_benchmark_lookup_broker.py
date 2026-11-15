"""Synthetic benchmark queue tests: no game data, credentials, or network."""

import importlib.util
import json
import threading
import time
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "benchmarks/bm01/scripts/lookup_broker.py"
_SPEC = importlib.util.spec_from_file_location("bm01_lookup_broker", _SCRIPT)
broker_module = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(broker_module)
LookupBroker = broker_module.LookupBroker
BrokerError = broker_module.BrokerError


@pytest.fixture
def broker(tmp_path, monkeypatch):
    # Treat the test's disposable directory as the workspace. Production retains
    # its repository-derived bound and exposes no model-selected root parameter.
    monkeypatch.setattr(broker_module, "_WORKSPACE", tmp_path)
    return LookupBroker(tmp_path / "private", "run_A")


def request(broker, call="call_submit_1"):
    return broker.submit(query="How long does a toy latch stay open?", intent="mechanism",
                         local_reason="Local toy manual omits timing.",
                         local_checks=["Read local toy-manual section Timing."],
                         actual_call_id=call, requested_version="toy-1")


def source(broker, request_id):
    text = "ANSWER_CANARY: winning schedule is SECRET_SEQUENCE.\nThe latch stays open for 4 seconds.\nSCORE_CANARY: 999 points."
    metadata = {"title": "ANSWER_TITLE_CANARY winning schedule and score",
                "url": "https://example.invalid/toy-latch", "retrieved_at": "2026-09-13T00:00:00+00:00",
                "source_date": "2026-09-01", "version": "toy-1"}
    record = broker.host_register_raw(request_id, text=text, **metadata)
    return text, metadata, record


def excerpt(text, metadata, raw):
    literal = "The latch stays open for 4 seconds."
    start = text.index(literal)
    return {"raw_id": raw["raw_id"], "raw_sha256": raw["text_sha256"],
            "start": start, "end": start + len(literal), "excerpt": literal,
            "approved_metadata": {k: v for k, v in metadata.items() if k != "title"}}


def review(broker, request_id, **changes):
    payload = {"decision": "allow", "outcome": "found", "reviewer_id": "host_reviewer",
               "review_note": "PRIVATE_REVIEW_CANARY: reviewed the source and excluded answer-bearing regions.",
               "excerpts": []}
    payload.update(changes)
    return broker.host_review(request_id, **payload)


def test_pending_hides_mixed_page_and_answer_search_result(broker):
    submitted = request(broker)
    rid = submitted["request_id"]
    source(broker, rid)
    broker.host_register_raw(rid, title="SEARCH_TITLE_ANSWER_CANARY", text="SEARCH_SNIPPET_ANSWER_CANARY",
                             url="https://example.invalid/search-hit", retrieved_at="2026-09-13",
                             source_date=None, version=None, kind="search_result")
    visible = broker.poll(rid, actual_call_id="call_poll_1")
    assert visible == {"request_id": rid, "status": "pending", "retry_after_ms": 1000}
    pending = broker.host_pending()
    assert pending[0]["actual_call_id"] == "call_submit_1"
    assert pending[0]["run_id"] == "run_A"
    model_log = (broker.root / "model-visible.jsonl").read_text(encoding="utf8")
    assert "CANARY" not in model_log


def test_deny_and_empty_never_expose_raw_or_private_review(broker):
    for index, (decision, outcome) in enumerate((("deny", "denied"), ("empty", "not_found"), ("empty", "conflict"))):
        rid = request(broker, f"call_submit_{index}")["request_id"]
        source(broker, rid)
        review(broker, rid, decision=decision, outcome=outcome)
        visible = broker.poll(rid, actual_call_id=f"call_poll_{index}")
        assert visible == {"request_id": rid, "status": "reviewed", "decision": decision, "outcome": outcome}
    assert "CANARY" not in (broker.root / "model-visible.jsonl").read_text(encoding="utf8")
    assert "PRIVATE_REVIEW_CANARY" in (broker.root / "host-audit.jsonl").read_text(encoding="utf8")
    assert broker.host_pending() == []


def test_allow_only_literal_mechanism_excerpt_and_explicit_metadata(broker):
    rid = request(broker)["request_id"]
    text, metadata, raw = source(broker, rid)
    approved = excerpt(text, metadata, raw)
    review(broker, rid, excerpts=[approved])
    visible = broker.poll(rid, actual_call_id="call_poll_approved")
    assert visible["decision"] == "allow"
    assert visible["sources"] == [{k: v for k, v in approved.items() if k != "approved_metadata"} | approved["approved_metadata"]]
    assert "title" not in visible["sources"][0]
    assert "CANARY" not in json.dumps(visible)
    assert visible["sources"][0]["excerpt"] == text[approved["start"]:approved["end"]]
    assert broker.host_raw(rid, raw["raw_id"])["text"] == text


@pytest.mark.parametrize("change,reason", [
    ({"raw_sha256": "0" * 64}, "source_integrity"),
    ({"start": -1}, "excerpt_bounds"),
    ({"end": 100000}, "excerpt_bounds"),
    ({"start": True}, "excerpt_bounds"),
    ({"excerpt": "The latch stays open for 40 seconds."}, "excerpt_mismatch"),
    ({"approved_metadata": {"title": "innocent label"}}, "invalid_review"),
])
def test_invalid_review_is_rejected_and_request_remains_pending(broker, change, reason):
    rid = request(broker)["request_id"]
    text, metadata, raw = source(broker, rid)
    item = excerpt(text, metadata, raw) | change
    with pytest.raises(BrokerError) as error:
        review(broker, rid, excerpts=[item])
    assert error.value.reason == reason
    assert broker.poll(rid, actual_call_id="poll_after_invalid")["status"] == "pending"
    assert not (broker.root / "reviews" / (rid + ".json")).exists()


def test_raw_tamper_cross_request_and_metadata_mismatch_rejected(broker):
    rid = request(broker)["request_id"]
    text, metadata, raw = source(broker, rid)
    other = request(broker, "call_other")["request_id"]
    item = excerpt(text, metadata, raw)
    with pytest.raises(BrokerError, match="does not belong"):
        review(broker, other, excerpts=[item])
    changed = dict(item, approved_metadata=dict(item["approved_metadata"], url="https://example.invalid/different"))
    with pytest.raises(BrokerError, match="metadata must match"):
        review(broker, rid, excerpts=[changed])
    raw_path = broker.root / "raw" / (raw["raw_id"] + ".json")
    record = json.loads(raw_path.read_text(encoding="utf8"))
    record["text"] += "tampered"
    raw_path.write_text(json.dumps(record), encoding="utf8")
    with pytest.raises(BrokerError, match="integrity"):
        review(broker, rid, excerpts=[item])


def test_model_paths_run_escape_and_oversized_wait_rejected(broker):
    with pytest.raises(BrokerError, match="identifier"):
        broker.poll("../../sealed.txt", actual_call_id="call_bad_path")
    with pytest.raises(BrokerError, match="identifier"):
        LookupBroker(broker.root.parent, "../run_B")
    with pytest.raises(BrokerError, match="workspace"):
        LookupBroker(broker_module._WORKSPACE.parent / "outside", "run_X")
    rid = request(broker)["request_id"]
    with pytest.raises(BrokerError, match="wait_ms"):
        broker.poll(rid, actual_call_id="call_bad_wait", wait_ms=40000)
    other = LookupBroker(broker.root.parent, "run_B")
    with pytest.raises(BrokerError, match="not found"):
        other.poll(rid, actual_call_id="call_cross_run")


def test_pending_poll_can_receive_review_from_independent_instance(broker):
    rid = request(broker)["request_id"]
    second = LookupBroker(broker.root.parent, broker.run_id)
    def approve():
        time.sleep(0.1)
        review(second, rid, decision="empty", outcome="not_found")
    worker = threading.Thread(target=approve)
    worker.start()
    visible = broker.poll(rid, actual_call_id="call_wait", wait_ms=2000)
    worker.join(timeout=2)
    assert visible["decision"] == "empty"
    with pytest.raises(BrokerError, match="cannot be replaced"):
        review(second, rid, decision="deny", outcome="denied")


def test_semantic_review_is_required_even_for_hash_valid_answer_text(broker):
    # Deliberately approving an answer-bearing literal passes fidelity validation.
    # This records the limit honestly: hashes and offsets cannot replace judgment.
    rid = request(broker)["request_id"]
    text, metadata, raw = source(broker, rid)
    answer = text.splitlines()[0]
    item = excerpt(text, metadata, raw) | {"start": 0, "end": len(answer), "excerpt": answer}
    review(broker, rid, excerpts=[item])
    visible = broker.poll(rid, actual_call_id="call_demonstrate_semantic_limit")
    assert "ANSWER_CANARY" in visible["sources"][0]["excerpt"]


def test_model_dispatch_cannot_override_identity_or_call_host_methods(broker):
    assert {item["name"] for item in broker_module.model_tool_specs()} == {"web_lookup_submit", "web_lookup_poll"}
    payload = {"query": "Toy latch timing?", "local_reason": "Missing from local manual.",
               "local_checks": ["Read local manual."], "intent": "base_fact"}
    result = broker.model_dispatch("web_lookup_submit", payload, "trusted_call_1")
    assert broker.host_pending()[0]["actual_call_id"] == "trusted_call_1"
    assert broker.model_dispatch("web_lookup_poll", {"request_id": result["request_id"]}, "trusted_call_2")["status"] == "pending"
    with pytest.raises(BrokerError, match="contract"):
        broker.model_dispatch("web_lookup_submit", payload | {"actual_call_id": "forged"}, "trusted_call_3")
    with pytest.raises(BrokerError, match="model-callable"):
        broker.model_dispatch("host_raw", {"path": "private/file"}, "trusted_call_4")
