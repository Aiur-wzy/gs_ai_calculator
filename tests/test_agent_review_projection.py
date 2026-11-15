"""Agent transaction/identity tests; synthetic large pages exercise publication only."""
from __future__ import annotations

import copy
import json
import threading
import uuid
from pathlib import Path

import pytest

from genshin_app import calculation_review_batch as batch
from genshin_app.agent_chat import candidate as candidate_module
from genshin_app.agent_chat import review_projection as projection
from genshin_app.agent_chat import tools
from genshin_app.agent_chat.candidate import CandidateSession
from genshin_app.equipment.files import json_bytes
from genshin_app.equipment.schema import PlanError
from genshin_app.review_batch_json import PROJECTION_WORK, Budget
from tests.test_calculation_delivery import delivery_plan


@pytest.fixture
def session(tmp_path):
    assert Path(candidate_module.__file__).resolve().is_relative_to(Path(__file__).resolve().parents[1] / 'src')
    instance = CandidateSession(uuid.uuid4().hex, tmp_path / "session", tmp_path / "absent.db", "native", delivery_plan())
    instance.calculate(0)
    return instance


def request(session):
    return session.call_tool("gs_review_result_batch", {"expected_revision": session.revision,
                                                       "request": {"requests": [{"view": "hits"}, {"view": "summary"}]}})


def test_batch_capabilities_are_discoverable(session):
    result = session.call_tool("gs_read_context", {"resource": "calculation_review_batch_capabilities"})
    assert result["code"] == 0
    fields = {item["key"]: item["value"] for item in result["data"]["items"]}
    assert fields["contract"] == "calculation-review-batch-v1"
    assert fields["limits"]["request_bytes"] == 64 * 1024
    assert fields["limits"]["projection_work"] == 8_000_000


def large_output(session):
    value = batch._review_complete_calculated(session.calculated, {"requests": [{"view": "hits"}]})
    # This synthetic padding is only a publication/transport oracle. Engine
    # verification itself is tested with unchanged real calculated packages.
    value["pages"][0]["review"]["transport_test_padding"] = "文" * 110_000
    return value


def stub_large(monkeypatch, session, output=None):
    value = large_output(session) if output is None else output
    calls = []
    def controlled(calculated, req, *, _finish):
        calls.append(1)
        budget = Budget(PROJECTION_WORK, "projection")
        try:
            return _finish(copy.deepcopy(value), budget)
        finally:
            budget.close()
    monkeypatch.setattr(batch, "_review_complete_calculated", controlled)
    return calls


def publish(monkeypatch, session):
    calls = stub_large(monkeypatch, session)
    response = request(session)
    assert response["code"] == 0, response
    assert calls == [1]
    assert len(json_bytes(response)) <= tools.MAX_RESPONSE
    return response["data"]["reference"]["resource"], response


def assert_identity(value, reference):
    for key in projection.META_FIELDS:
        assert value[key] == reference[key]
    assert value["historical_projection"] is True
    assert value["original_document"]["readable"] is False


def test_real_small_uses_one_full_validation_and_no_legacy_packaging(session, monkeypatch):
    from genshin_app.equipment import delivery
    original = delivery.validate_delivery_calculated
    calls = []
    def checked(value, **kwargs):
        calls.append(kwargs.get("_session"))
        return original(value, **kwargs)
    monkeypatch.setattr(delivery, "validate_delivery_calculated", checked)
    monkeypatch.setattr(tools, "_calculated_package", lambda *_: pytest.fail("legacy prevalidation"))
    response = request(session)
    assert response["code"] == 0, response
    assert len(calls) == 1 and calls[0] is not None
    assert response["data"]["contract"] == "calculation-review-batch-v1"
    assert response["data"]["session_id"] == session.session_id
    assert len(response["data"]["pages"]) == 2
    assert session.resources == {}
    assert request(session)["code"] == 0 and len(calls) == 2 and calls[0] is not calls[1]


@pytest.mark.parametrize("field", ["plan", "report"])
def test_full_binding_tamper_rejected(session, field):
    if field == "plan":
        session.calculated[field]["plan_id"] = "changed"
    else:
        session.calculated[field]["input_fingerprint"] = "0" * 64
    assert request(session)["code"] == 1
    assert session.resources == {}


@pytest.mark.parametrize("args", [{}, {"expected_revision": 0}, {"expected_revision": False, "request": {}},
                                    {"expected_revision": 0, "request": {}, "other": 1}])
def test_request_required_and_strict_before_snapshot(session, args):
    assert session.call_tool("gs_review_result_batch", args)["code"] == 1


def test_revision_and_missing_calculation_reject(session):
    assert session.call_tool("gs_review_result_batch", {"expected_revision": 1, "request": {}})["reason"] == "agent_stale_revision"
    session.calculated = None
    assert request(session)["reason"] == "agent_calculation_required"


def test_request_subclass_protocol_never_runs(session):
    class Dangerous(dict):
        def items(self):
            pytest.fail("custom items invoked")
    assert session.call_tool("gs_review_result_batch", {"expected_revision": 0, "request": Dangerous()})["code"] == 1


def test_history_subtrees_references_revision_and_reload(session, monkeypatch):
    name, response = publish(monkeypatch, session)
    reference = response["data"]["reference"]
    for pointer in ("", "/projection/pages", "/projection/pages/0/review/items", "/projection/pages/0/review/items/0/expected"):
        reply = session.call_tool("gs_read_context", {"resource": name, "pointer": pointer, "limit": 1})
        assert reply["code"] == 0, reply
        assert_identity(reply["data"], reference)
        for row in reply["data"].get("items", []):
            if isinstance(row["value"], dict) and "reference" in row["value"]:
                assert_identity(row["value"]["reference"], reference)
    changed = copy.deepcopy(session.candidate)
    changed["manual_template"]["hits"][0]["multiplier"] = 2
    session.replace_candidate(changed, 0, "变更使原计算失效")
    assert session.calculated is None
    assert not list(session.root.glob("calculated-*.json"))
    # Reload/read the selected historical fields must not replay or validate a
    # missing original calculation, and must not regain current readiness.
    monkeypatch.setattr(candidate_module, "validate_delivery_calculated", lambda *_: pytest.fail("history replayed"))
    loaded = CandidateSession.load(session.session_id, session.root, session.db_path)
    reply = loaded.call_tool("gs_read_context", {"resource": name, "pointer": "/projection/pages/0/review/items/0/expected"})
    assert reply["data"]["value"] == 45
    assert_identity(reply["data"], reference)
    assert loaded.summary()["ready"] is False
    assert request(loaded)["reason"] == "agent_calculation_required"
    assert loaded.call_tool("gs_read_context", {"resource": name, "pointer": "/result/hits/0"})["code"] == 1


@pytest.mark.parametrize("mutation", ["owner", "contract", "content", "length", "missing_record", "future_revision", "duplicate", "record_owner"])
def test_reload_rejects_projection_or_checkpoint_tampering(session, monkeypatch, mutation):
    name, _ = publish(monkeypatch, session)
    path = session.root / (name + ".json")
    checkpoint_path = session.root / "checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_bytes())
    document = json.loads(path.read_bytes())
    if mutation == "owner":
        document["session_id"] = uuid.uuid4().hex
    elif mutation == "contract":
        document["contract"] = "fake"
    elif mutation == "content":
        document["projection"]["pages"][0]["review"]["items"][0]["expected"] = 900
    elif mutation == "length":
        checkpoint["review_projections"][name]["byte_length"] += 1
    elif mutation == "missing_record":
        checkpoint["review_projections"] = {}
    elif mutation == "future_revision":
        document["original_revision"] = 999
    elif mutation == "record_owner":
        checkpoint["review_projections"][name]["session_id"] = uuid.uuid4().hex
    elif mutation == "duplicate":
        checkpoint["resources"].append(name)
    path.write_bytes(json_bytes(document))
    checkpoint_path.write_bytes(json_bytes(checkpoint))
    with pytest.raises(PlanError):
        CandidateSession.load(session.session_id, session.root, session.db_path)


def test_memory_tamper_rejected_without_rerun(session, monkeypatch):
    name, _ = publish(monkeypatch, session)
    session.resources[name]["projection"]["pages"][0]["review"]["items"][0]["expected"] += 1
    assert session.call_tool("gs_read_context", {"resource": name})["reason"] == "agent_projection_invalid"


@pytest.mark.parametrize("stage", ["write", "write_after_commit", "registration", "checkpoint", "checkpoint_replace", "final_envelope"])
def test_failure_atomicity_preserves_checkpoint_and_no_ready_resource(session, monkeypatch, stage):
    calls = stub_large(monkeypatch, session)
    before = (session.root / "checkpoint.json").read_bytes()
    if stage in ("write", "write_after_commit"):
        original = projection.publish_files
        def fail(*args, **kwargs):
            if stage == "write_after_commit":
                original(*args, **kwargs)
            raise OSError("injected file failure")
        monkeypatch.setattr(projection, "publish_files", fail)
    elif stage == "registration":
        def fail(instance, name, document, record):
            instance.resources[name] = document
            raise RuntimeError("injected registration failure")
        monkeypatch.setattr(projection, "_register", fail)
    elif stage == "checkpoint":
        def fail(*args):
            raise OSError("injected checkpoint failure")
        monkeypatch.setattr(projection, "_commit_checkpoint", fail)
    elif stage == "checkpoint_replace":
        original = projection.os.replace
        def fail(source, destination):
            if Path(destination).name == "checkpoint.json":
                raise OSError("injected atomic replace failure")
            return original(source, destination)
        monkeypatch.setattr(projection.os, "replace", fail)
    else:
        original = projection._encoded
        def fail(value, budget, maximum):
            if value.get("code") == 0 and value.get("data", {}).get("storage") == "session_resource":
                raise PlanError("injected final wrapper failure", "agent_response_limit")
            return original(value, budget, maximum)
        monkeypatch.setattr(projection, "_encoded", fail)
    response = request(session)
    assert response["code"] == 1 and "reference" not in response
    assert session.resources == {} and session._review_projection_records == {}
    assert (session.root / "checkpoint.json").read_bytes() == before
    assert calls == [1]
    loaded = CandidateSession.load(session.session_id, session.root, session.db_path)
    assert loaded.resources == {}
    orphaned = list(session.root.glob("review-*.json"))
    if stage in ("registration", "checkpoint", "checkpoint_replace", "write_after_commit"):
        assert len(orphaned) == 1
        assert session._review_projection_orphans[0]["file_name"] == orphaned[0].name
        assert list(session.root.glob("unpublished-review-*.json"))
    else:
        assert orphaned == []


def test_previous_success_remains_readable_after_next_publication_failure(session, monkeypatch):
    output = large_output(session)
    name, response = publish(monkeypatch, session)
    before_resources = copy.deepcopy(session.resources)
    before_records = copy.deepcopy(session._review_projection_records)
    before_checkpoint = (session.root / "checkpoint.json").read_bytes()
    stub_large(monkeypatch, session, output)
    def fail(*args):
        raise OSError("next checkpoint fails")
    monkeypatch.setattr(projection, "_commit_checkpoint", fail)
    assert request(session)["code"] == 1
    assert session.resources == before_resources and session._review_projection_records == before_records
    assert (session.root / "checkpoint.json").read_bytes() == before_checkpoint
    loaded = CandidateSession.load(session.session_id, session.root, session.db_path)
    reply = loaded.call_tool("gs_read_context", {"resource": name, "pointer": "/projection/pages/0/review/items/0/expected"})
    assert reply["data"]["value"] == 45
    assert_identity(reply["data"], response["data"]["reference"])


@pytest.mark.parametrize("field,value", [("revision", 99), ("revision", False), ("version", True),
                                        ("session_id", "other"), ("receipt_fingerprint", "0" * 64)])
def test_stale_checkpoint_rejected_before_creating_resource(session, monkeypatch, field, value):
    stub_large(monkeypatch, session)
    path = session.root / "checkpoint.json"
    changed = json.loads(path.read_bytes())
    changed[field] = value
    path.write_bytes(json_bytes(changed))
    before = path.read_bytes()
    assert request(session)["reason"] == "agent_checkpoint_invalid"
    assert path.read_bytes() == before and session.resources == {}
    assert not list(session.root.glob("review-*.json"))


def test_checkpoint_manifest_rejects_bool_equivalent_revision(session, monkeypatch):
    name, _ = publish(monkeypatch, session)
    path = session.root / "checkpoint.json"
    changed = json.loads(path.read_bytes())
    changed["review_projections"][name]["original_revision"] = False
    path.write_bytes(json_bytes(changed))
    with pytest.raises(PlanError):
        CandidateSession.load(session.session_id, session.root, session.db_path)


def test_shared_budget_exhaustion_prevents_publication(session):
    output = large_output(session)
    budget = Budget(10, "projection")
    with pytest.raises(PlanError, match="预算"):
        projection.finish(session, output, budget, maximum_response=tools.MAX_RESPONSE,
                          maximum_resources=tools.MAX_RESOURCES, maximum_total=tools.MAX_RESOURCE_TOTAL)
    assert not session.resources and not list(session.root.glob("review-*.json"))


def test_historical_public_returns_never_alias_registered_resource(session, monkeypatch):
    name, published = publish(monkeypatch, session)
    before = copy.deepcopy(session.resources[name])
    before_record = copy.deepcopy(session._review_projection_records[name])
    published["data"]["identity"]["plan_id"] = "mutated outer identity"
    published["data"]["reference"]["identity"]["result_fingerprint"] = "mutated reference identity"
    for pointer in ("/projection/pages/0/review/items", "/projection/pages/0/review/items/0", "/projection/pages"):
        response = session.call_tool("gs_read_context", {"resource": name, "pointer": pointer})
        assert response["code"] == 0, response
        response["data"]["identity"]["plan_id"] = "mutated page identity"
        response["data"]["items"][0]["value"] = {"mutated": []}
        assert session.resources[name] == before
        assert session._review_projection_records[name] == before_record
    # Mutate nested leaves instead of merely replacing an outer row reference.
    response = session.call_tool("gs_read_context", {"resource": name, "pointer": "/projection/pages/0/review/items"})
    response["data"]["items"][0]["value"]["expected"] = 900
    response = session.call_tool("gs_read_context", {"resource": name, "pointer": "/projection/pages"})
    response["data"]["items"][0]["value"]["reference"]["identity"]["plan_id"] = "mutated nested reference"
    assert session.resources[name] == before
    assert session._review_projection_records[name] == before_record
    assert session.call_tool("gs_read_context", {"resource": name, "pointer": "/projection/pages/0/review/items/0/expected"})["data"]["value"] == 45


def test_large_content_skips_doomed_direct_response_scan(session, monkeypatch):
    output = large_output(session)
    original = projection.prepay
    checked = []
    def traced(value, budget, **kwargs):
        assert not (isinstance(value, dict) and value.get("code") == 0
                    and "pages" in value.get("data", {})), "oversized direct envelope was scanned"
        checked.append(value)
        return original(value, budget, **kwargs)
    monkeypatch.setattr(projection, "prepay", traced)
    calls = stub_large(monkeypatch, session, output)
    response = request(session)
    assert response["code"] == 0 and response["data"]["storage"] == "session_resource"
    assert calls == [1] and checked


def test_session_revision_and_plan_revision_are_distinct(tmp_path, monkeypatch):
    plan = delivery_plan()
    plan["revision"] = 7
    session = CandidateSession(uuid.uuid4().hex, tmp_path / "offset-revision", tmp_path / "absent.db", "native", plan)
    session.calculate(0)
    name, response = publish(monkeypatch, session)
    assert response["data"]["original_revision"] == 0
    assert response["data"]["identity"]["revision"] == 7
    loaded = CandidateSession.load(session.session_id, session.root, session.db_path)
    page = loaded.call_tool("gs_read_context", {"resource": name, "pointer": "/projection/pages/0/review/items"})
    assert page["data"]["original_revision"] == 0
    assert page["data"]["identity"]["revision"] == 7


@pytest.mark.parametrize("limit", ["count", "bytes", "projection", "envelope"])
def test_publication_bounds_fail_before_file(session, monkeypatch, limit):
    output = large_output(session)
    if limit == "count":
        monkeypatch.setattr(tools, "MAX_RESOURCES", 0)
    elif limit == "bytes":
        monkeypatch.setattr(tools, "MAX_RESOURCE_TOTAL", 1)
    elif limit == "projection":
        output["pages"][0]["review"]["transport_test_padding"] = "x" * (2 * 1024 * 1024)
    else:
        monkeypatch.setattr(tools, "MAX_RESPONSE", 1)
    stub_large(monkeypatch, session, output)
    assert request(session)["code"] == 1
    assert not list(session.root.glob("review-*.json")) and not session.resources


def test_rlock_holds_entire_batch_and_blocks_close(session, monkeypatch):
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    original = batch._review_complete_calculated
    replies = []
    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(batch, "_review_complete_calculated", blocked)
    worker = threading.Thread(target=lambda: replies.append(request(session)))
    closer = threading.Thread(target=lambda: (session.close(), closed.set()))
    worker.start()
    assert entered.wait(5)
    closer.start()
    try:
        assert not closed.wait(.1)
        assert session._lock.acquire(blocking=False) is False
    finally:
        release.set()
        worker.join(10)
        closer.join(10)
    assert not worker.is_alive() and not closer.is_alive() and closed.is_set()
    assert replies[0]["code"] == 0
