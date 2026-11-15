"""Optional diagnostics cannot grant authority or alter business results/errors."""
from __future__ import annotations

import copy
import json
from dataclasses import FrozenInstanceError

import pytest

from genshin_runtime import diagnostics
from genshin_runtime.diagnostics import (
    capture_runtime_diagnostics,
    diagnosed,
    diagnostic_stage,
    project_reaction_diagnostics,
)


def test_disabled_diagnostics_do_not_evaluate_material_or_hash_or_read_clock(monkeypatch):
    forbidden = lambda *_a, **_kw: pytest.fail("disabled observer evaluated")
    monkeypatch.setattr(diagnostics, "_fingerprint", forbidden)
    monkeypatch.setattr(diagnostics.time, "perf_counter", forbidden)

    @diagnosed("test", "leaf-v1")
    def operation(value):
        with diagnostic_stage("test", "inner-v1", inputs=forbidden, prestate=forbidden) as audit:
            audit.result(outputs={"result": value})
        return value

    expected = {"exact": [1, 2, 3]}
    assert operation(expected) is expected


def test_records_are_immutable_detached_and_written_only_on_explicit_request(tmp_path):
    original = {"value": [1, 2]}
    with (capture_runtime_diagnostics(operation_id="operation-one") as capture,
          diagnostic_stage("test", "read-v1", inputs={"request": original}, prestate={"revision": 0}) as audit):
        audit.result(outputs={"result": original}, poststate={"revision": 1}, changed_paths=("revision",))
    record = capture.records[0]
    original["value"].append(3)
    with pytest.raises(FrozenInstanceError):
        record.status = "forged"
    exported = record.as_dict()
    exported["input_fingerprints"].clear()
    assert record.input_fingerprints
    path = tmp_path / "maintenance.jsonl"
    assert not path.exists()
    assert capture.write_jsonl(path) == {"status": "written", "records": 1}
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["operation_id"] == "operation-one"
    assert saved["prestate_fingerprint"] != saved["poststate_fingerprint"]
    assert saved["changed_paths"] == ["revision"]
    assert "value" not in json.dumps(saved)
    assert capture.write_jsonl(tmp_path)["status"] == "failed"


@pytest.mark.parametrize("sink_error", [RuntimeError("observer"), KeyboardInterrupt()])
def test_sink_failure_does_not_change_success_or_first_exception(sink_error):
    def broken(_record):
        raise sink_error

    original = ValueError("business first failure")
    with capture_runtime_diagnostics(sink=broken) as capture:
        with diagnostic_stage("test", "success-v1") as audit:
            value = 42
            audit.result(outputs={"value": value})
        assert value == 42
        with pytest.raises(ValueError) as caught, diagnostic_stage("test", "failed-v1"):
            raise original
        assert caught.value is original
    assert capture.observer_failures == 2
    assert [row.status for row in capture.records] == ["completed", "failed"]
    assert capture.records[-1].cause_chain == (("ValueError", None),)
    assert "business first failure" not in json.dumps([row.as_dict() for row in capture.records])


def test_input_projection_failure_and_callable_arguments_cannot_run_business_callbacks():
    calls = []

    def callback():
        calls.append("MUST NOT RUN")

    @diagnosed("test", "callback-port-v1")
    def operation(value):
        return {"result": "exact"}

    with capture_runtime_diagnostics() as capture:
        assert operation(callback) == {"result": "exact"}
        with diagnostic_stage("test", "bad-input-v1", inputs=lambda: 1 / 0):
            calls.append("business")
    assert calls == ["business"]
    assert capture.observer_failures == 2
    assert len(capture.records) == 2


def test_invalid_diagnostic_metadata_cannot_mask_business_abort():
    class BusinessAbort(BaseException):
        pass

    original = BusinessAbort("not recorded")
    with (capture_runtime_diagnostics() as capture, pytest.raises(BusinessAbort) as caught,
          diagnostic_stage("test", "bad-metadata-v1", unknown_field="value")):
        raise original
    assert caught.value is original
    assert capture.observer_failures == 1


def test_failed_lazy_metadata_does_not_enter_the_business_exception_graph():
    def fail():
        raise RuntimeError("private observer payload")

    original = ValueError("business")
    with (capture_runtime_diagnostics() as capture, pytest.raises(ValueError) as caught,
          diagnostic_stage("test", "metadata-v1", source_commit=fail)):
        raise original
    assert caught.value is original
    assert original.__cause__ is original.__context__ is None
    assert capture.observer_failures == 1


def test_sensitive_mapping_fields_are_redacted_before_fingerprinting():
    values = []
    for secret in ("private-one", "private-two"):
        with capture_runtime_diagnostics() as capture, diagnostic_stage(
            "test", "secret-v1", inputs={"request": {"api_key": secret, "actual_value": 1}},
        ):
            pass
        values.append(capture.records[0].input_fingerprints)
        assert secret not in json.dumps(capture.records[0].as_dict())
    assert values[0] == values[1]


def test_nested_scopes_and_stream_only_sink_do_not_create_a_second_event_owner():
    streamed = []
    with capture_runtime_diagnostics(operation_id="outer") as outer:
        with diagnostic_stage("test", "outer-before"):
            pass
        with (capture_runtime_diagnostics(operation_id="inner", sink=streamed.append, retain=False) as inner,
              diagnostic_stage("test", "inner-only")):
            pass
        with diagnostic_stage("test", "outer-after"):
            pass
    assert [row.stage_id for row in outer.records] == ["outer-before", "outer-after"]
    assert inner.records == ()
    assert streamed[0]["operation_id"] == "inner"
    assert diagnostics.diagnostics_enabled() is False


def test_reaction_observation_uses_existing_trace_without_modifying_or_replaying_it():
    trace = [{"id": "reaction-one", "reaction_type": "stellar_superconduct",
              "atomic_parent_id": "parent-one", "damage": 12.5}]
    before = copy.deepcopy(trace)
    with capture_runtime_diagnostics() as capture:
        project_reaction_diagnostics(trace, bindings={"stellar_superconduct": {"contract_version": 1}})
    assert trace == before
    record = capture.records[0]
    assert record.status == "observed" and record.handler_family == "stellar_superconduct"
    assert record.contract_version == 1
    assert record.event_id == "reaction-one" and record.atomic_parent_id == "parent-one"
    assert record.input_fingerprints == record.output_fingerprints
