"""Optional evidence cannot make an otherwise fitting fresh result unsaveable."""
from __future__ import annotations

import copy
import csv
import io
import json
import zipfile
from datetime import datetime
from types import SimpleNamespace

import pytest

from genshin_app.calculation_delivery_files import LIMITS
from genshin_app.calculation_review import review_calculation
from genshin_app.equipment import calculation_sealing, service
from genshin_app.equipment.calculation_wire import (
    calculation_wire_sizes,
    result_only_bytes,
)
from genshin_app.equipment.delivery import (
    build_calculation_delivery,
    validate_delivery_calculated,
)
from genshin_app.equipment.files import fingerprint, json_bytes
from genshin_app.equipment.fresh_result_capacity import finish_fresh_result
from genshin_app.equipment.schema import PlanError
from tests.test_calculation_delivery import delivery_plan


def five_hits():
    plan = delivery_plan()
    hit = plan["manual_template"]["hits"][0]
    plan["manual_template"]["hits"] = [{**hit, "id": f"h{i}", "t": 1 + i * .01} for i in range(5)]
    return plan


def minimum_result(plan, db, monkeypatch):
    def build_minimum(build_candidate):
        # Follow the producer's complete→minimum construction order so the
        # same verification session has the same warmed private replay.
        build_candidate(None)
        return build_candidate(0)
    with monkeypatch.context() as patch:
        # Measure the actual producer's mandatory proofs and partial header.
        # Suppressing the earlier core builder is a different document and
        # can be larger than the real zero-budget sealing candidate.
        patch.setattr('genshin_app.equipment.fresh_result_capacity.finish_fresh_result',
                      build_minimum)
        return service.run_plan_calc(copy.deepcopy(plan), db, capture_display=True)


def middle_limit(full, minimum):
    return (max(calculation_wire_sizes(full).values()) + max(calculation_wire_sizes(minimum).values())) // 2


def numeric_rows(calculated):
    return [{key: value for key, value in hit.items() if key != "explanation_ref"}
            for hit in calculated["result"]["hits"]]


def test_normal_writer_sizes_exact_and_one_numerical_execution(tmp_path, monkeypatch):
    calls = []
    original = service.calculate
    def calculate(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(service, "calculate", calculate)
    calculated = service.run_plan_calc(five_hits(), tmp_path / "absent.db", capture_display=True)
    before = copy.deepcopy(calculated)
    built = build_calculation_delivery(calculated, input_file_sha="a" * 64)
    sizes = calculation_wire_sizes(calculated)
    assert sizes["calculated"] == len(built["artifacts"]["calculated"])
    assert sizes["result_only"] == len(service.result_bytes(
        calculated["result"], calculated["result_receipt"], "json", equipment_summary=calculated["equipment_summary"]))
    assert calculated["result"]["damage_explanations"]["status"] == "complete"
    assert calculated == before and calls == [1]


def test_result_only_canonical_writer_preserves_real_proofs_and_fingerprints(tmp_path):
    calculated = service.run_plan_calc(five_hits(), tmp_path / 'absent.db', capture_display=True)
    before = copy.deepcopy(calculated)
    envelope = {key: calculated[key] for key in ('result', 'result_receipt', 'equipment_summary')}
    # This is the actual validated export writer, not a size-only substitute.
    written = service.result_bytes(calculated['result'], calculated['result_receipt'], 'json',
                                   equipment_summary=calculated['equipment_summary'])
    path = tmp_path / 'result.json'
    path.write_bytes(written)
    assert path.read_bytes() == json_bytes(envelope)
    assert json.loads(path.read_bytes()) == envelope
    assert calculated == before
    assert fingerprint(json.loads(written)['result']) == calculated['result_receipt']['result_fingerprint']
    assert len(written) == calculation_wire_sizes(calculated)['result_only']
    validate_delivery_calculated(calculated)
    built = build_calculation_delivery(calculated, input_file_sha='a' * 64,
                                       roles=['native_plan', 'calculated'])
    assert set(built['artifacts']) == {'native_plan', 'calculated'}
    loaded = json.loads(built['artifacts']['calculated'])['calculated']
    validate_delivery_calculated(loaded)
    assert loaded['result_receipt']['result_fingerprint'] == calculated['result_receipt']['result_fingerprint']


def test_result_only_indentation_cannot_reject_fitting_mandatory_material(monkeypatch):
    # Synthetic wire payload: many nested mandatory rows reproduce the exact
    # compact-package < limit < pretty-result failure without a 120-second axis.
    result = {'mandatory_rows': [{'t': i, 'proof': {'passed': True, 'source': '原始资料'}}
                                 for i in range(1200)], 'damage_explanations': {}}
    calculated = {'result': result, 'result_receipt': {'engine_version': 'fixture'},
                  'equipment_summary': {}, 'report': {'composer_version': 'fixture'}}
    sizes = calculation_wire_sizes(calculated)
    envelope = {key: calculated[key] for key in ('result', 'result_receipt', 'equipment_summary')}
    # Empty equipment_summary is intentionally omitted by the public writer.
    envelope.pop('equipment_summary')
    pretty_size = len(json.dumps(envelope, ensure_ascii=False, indent=2).encode('utf-8'))
    assert sizes['result_only'] < sizes['calculated'] < pretty_size
    assert LIMITS['calculated_bytes'] == 96 * 1024 * 1024
    monkeypatch.setitem(LIMITS, 'calculated_bytes', sizes['calculated'])
    attempts = []
    def build(maximum):
        attempts.append(maximum)
        return copy.deepcopy(calculated)
    chosen = finish_fresh_result(build)
    assert attempts == [None], 'a fitting complete candidate must not trim optional evidence'
    assert chosen == calculated
    assert len(result_only_bytes(chosen['result'], chosen['result_receipt'], {})) <= LIMITS['calculated_bytes']
    # A one-byte larger actual calculated role must still fail even though the
    # result-only role fits. Changing max to min would incorrectly admit this.
    monkeypatch.setitem(LIMITS, 'calculated_bytes', sizes['calculated'] - 1)
    with pytest.raises(PlanError) as caught:
        finish_fresh_result(build)
    assert caught.value.reason == 'delivery_size_limit'
    assert caught.value.details['role'] == 'calculated'
    assert caught.value.details['bytes'] == sizes['calculated']


def test_cli_calc_publishes_the_measured_canonical_result_bytes(tmp_path, monkeypatch, capsys):
    from genshin_app.cli import main
    monkeypatch.setattr('genshin_app.cli._require_runtime_database',
                        lambda _data_dir: {'can_start': True, 'state': 'test_ready'})
    plan_path, output = tmp_path / 'plan.json', tmp_path / 'result.json'
    plan_path.write_bytes(json_bytes(five_hits()))
    code = main(['--data-dir', str(tmp_path), 'equipment', 'calc', '--plan', str(plan_path),
                 '--output', str(output), '--summary'])
    captured = capsys.readouterr()
    assert code == 0, captured.err
    response = json.loads(captured.out)['data']
    raw = output.read_bytes()
    envelope = json.loads(raw)
    assert raw == result_only_bytes(envelope['result'], envelope['result_receipt'],
                                    envelope['equipment_summary'])
    assert raw == json_bytes(envelope)
    assert response['result_receipt']['result_fingerprint'] == fingerprint(envelope['result'])


def test_near_limit_fresh_partial_keeps_numbers_and_all_saved_proofs(tmp_path, monkeypatch):
    db, plan = tmp_path / "absent.db", five_hits()
    full = service.run_plan_calc(copy.deepcopy(plan), db, capture_display=True)
    minimum = minimum_result(plan, db, monkeypatch)
    maximum = middle_limit(full, minimum)
    calls = []
    original = service.calculate
    def calculate(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(service, "calculate", calculate)
    monkeypatch.setitem(LIMITS, "calculated_bytes", maximum)
    reduced = service.run_plan_calc(copy.deepcopy(plan), db, capture_display=True)
    assert calls == [1], "capacity must not rerun any numerical computation"
    assert numeric_rows(reduced) == numeric_rows(full)
    assert reduced["result"]["summary"] == full["result"]["summary"]
    doc = reduced["result"]["damage_explanations"]
    assert doc["status"] == "partial" and "calculated_envelope_capacity" in doc["coverage"]["reasons"]
    assert 0 < doc["coverage"]["explained"] < doc["coverage"]["eligible"]
    assert max(calculation_wire_sizes(reduced).values()) <= maximum
    validate_delivery_calculated(reduced)
    built = build_calculation_delivery(reduced, input_file_sha="a" * 64)
    loaded = json.loads(built["artifacts"]["calculated"])["calculated"]
    validate_delivery_calculated(loaded)
    assert review_calculation(loaded, {"view": "damage_explanations"})["explanation"]["status"] == "partial"
    result_only = json.loads(service.result_bytes(loaded["result"], loaded["result_receipt"], "json"))
    assert review_calculation(result_only, {"view": "hits"})["total_rows"] == 5
    assert not db.exists()


@pytest.mark.parametrize("boundary", ["base_too_large", "minimum_header_does_not_fit"])
def test_mandatory_base_and_minimum_partial_limits_remain_real_errors(tmp_path, monkeypatch, boundary):
    monkeypatch.setattr(service, 'datetime',
        SimpleNamespace(now=lambda tz: datetime(2026, 11, 9, 12, 0, 0, 123456, tzinfo=tz)))
    db, plan = tmp_path / "absent.db", five_hits()
    minimum = minimum_result(plan, db, monkeypatch)
    maximum = 1024 if boundary == "base_too_large" else max(calculation_wire_sizes(minimum).values()) - 1
    monkeypatch.setitem(LIMITS, "calculated_bytes", maximum)
    with pytest.raises(PlanError) as caught:
        service.run_plan_calc(plan, db, capture_display=True)
    assert caught.value.reason == "delivery_size_limit"
    assert caught.value.details["optional_explanation_reduction_exhausted"] is True
    assert caught.value.details["bytes"] > maximum
    assert not db.exists()


@pytest.mark.parametrize('spare_bytes', [0, 1])
def test_exact_final_envelope_limit_includes_completion(tmp_path, monkeypatch, spare_bytes):
    monkeypatch.setattr(service, 'datetime',
        SimpleNamespace(now=lambda tz: datetime(2026, 11, 9, 12, 0, 0, 123456, tzinfo=tz)))
    plan, db = five_hits(), tmp_path / 'absent.db'
    minimum = minimum_result(plan, db, monkeypatch)
    maximum = max(calculation_wire_sizes(minimum).values()) + spare_bytes
    monkeypatch.setitem(LIMITS, 'calculated_bytes', maximum)
    result = service.run_plan_calc(plan, db, capture_display=True)
    assert result['completion']['task_admission'] == 'accepted'
    assert max(calculation_wire_sizes(result).values()) <= maximum
    validate_delivery_calculated(result)
    assert max(calculation_wire_sizes(result).values()) <= maximum


def test_delivery_never_trims_an_already_sealed_result(tmp_path, monkeypatch):
    db, plan = tmp_path / "absent.db", five_hits()
    full = service.run_plan_calc(copy.deepcopy(plan), db, capture_display=True)
    minimum = minimum_result(plan, db, monkeypatch)
    before = copy.deepcopy(full)
    monkeypatch.setitem(LIMITS, "calculated_bytes", middle_limit(full, minimum))
    with pytest.raises(PlanError) as caught:
        build_calculation_delivery(full, input_file_sha="a" * 64)
    assert caught.value.reason == "delivery_size_limit" and full == before


@pytest.mark.parametrize("kind", ["external", "fixed"])
def test_reduction_preserves_external_and_fixed_proofs(tmp_path, monkeypatch, kind):
    db = tmp_path / "absent.db"
    if kind == "external":
        from tests.test_external_damage import confirm, external_plan
        plan = external_plan()
        plan["manual_template"]["hits"] = five_hits()["manual_template"]["hits"]
        confirm(plan, db)
    else:
        from tests.test_calculation_overrides_execution import (
            accept_from_compose,
            declaration,
            native_plan,
            override_item,
            target,
        )
        plan = native_plan()
        plan["calculation_event_overrides"] = declaration(override_item(
            "replace_computed_value", target("computed_event", "h0", "result.hits"),
            {"expected": 999, "precision": {"kind": "approximate", "method": "独立固定值工程夹具",
                                           "uncertainty": "仅验证证明绑定", "state_effects": "none"}}))
        accept_from_compose(plan, db)
    full = service.run_plan_calc(copy.deepcopy(plan), db, capture_display=True)
    minimum = minimum_result(plan, db, monkeypatch)
    monkeypatch.setitem(LIMITS, "calculated_bytes", middle_limit(full, minimum))
    reduced = service.run_plan_calc(copy.deepcopy(plan), db, capture_display=True)
    assert numeric_rows(reduced) == numeric_rows(full)
    if kind == "external":
        assert reduced["result"]["calculation_totals"] == full["result"]["calculation_totals"]
        assert reduced["result"]["external_damage"] == full["result"]["external_damage"]
    else:
        assert reduced["result"]["hits"][0]["expected"] == 999
        assert reduced["result"]["hits"][0]["calculation_override"] == full["result"]["hits"][0]["calculation_override"]
    validate_delivery_calculated(reduced)
    built = build_calculation_delivery(reduced, input_file_sha="a" * 64)
    validate_delivery_calculated(json.loads(built["artifacts"]["calculated"])["calculated"])
    assert service.result_bytes(reduced["result"], reduced["result_receipt"], "json")


def test_partial_cannot_hide_tampered_reference_after_outer_rehash(tmp_path, monkeypatch):
    db, plan = tmp_path / "absent.db", five_hits()
    full = service.run_plan_calc(copy.deepcopy(plan), db, capture_display=True)
    minimum = minimum_result(plan, db, monkeypatch)
    monkeypatch.setitem(LIMITS, "calculated_bytes", middle_limit(full, minimum))
    reduced = service.run_plan_calc(plan, db, capture_display=True)
    reduced["result"]["hits"][0]["explanation_ref"] = "missing:tampered"
    reduced["result_receipt"]["result_fingerprint"] = fingerprint(reduced["result"])
    with pytest.raises((ValueError, PlanError)):
        validate_delivery_calculated(reduced)


@pytest.mark.parametrize("final_capacity_failure", [False, True])
def test_private_capacity_candidates_publish_usage_only_once(tmp_path, monkeypatch, final_capacity_failure):
    captured = {}
    original = calculation_sealing.seal_fresh_calculation
    def capture(plan, result, report, **kwargs):
        captured.update(plan=copy.deepcopy(plan), result=copy.deepcopy(result), report=copy.deepcopy(report), kwargs=kwargs)
        return original(plan, result, report, **kwargs)
    monkeypatch.setattr(calculation_sealing, "seal_fresh_calculation", capture)
    service.run_plan_calc(five_hits(), tmp_path / "absent.db", capture_display=True)
    published, trials = [], []
    session = SimpleNamespace(publish_provisional_usage_receipt=lambda **kwargs: published.append(kwargs) or {"fixture": True})
    def exercise(build):
        for maximum in (None, 0, 2048):
            trials.append(build(maximum))
        if final_capacity_failure:
            raise PlanError("工程夹具的必需材料超过最终容量", "delivery_size_limit")
        return trials[-1]
    monkeypatch.setattr("genshin_app.equipment.fresh_result_capacity.finish_fresh_result", exercise)
    kwargs = {**captured["kwargs"], "resolved_provisional_uses": [{"fixture": "no new permission"}],
              "candidate_session": session, "_session": None}
    if final_capacity_failure:
        with pytest.raises(PlanError, match="最终容量"):
            original(captured["plan"], captured["result"], captured["report"], **kwargs)
    else:
        out = original(captured["plan"], captured["result"], captured["report"], **kwargs)
    assert len(trials) == 3 and len(published) == 1
    assert all(item["result_receipt"]["provisional_entity_usage_receipt"] == {"fixture": True} for item in trials)
    # This synthetic publisher only tests exactly-once handling. It is not a
    # trusted approval and must never become an exportable formal authority.
    if not final_capacity_failure:
        with pytest.raises(PlanError):
            service.result_bytes(out["result"], out["result_receipt"], "json")


def test_minimum_partial_reports_metadata_separately_from_zero_optional_payload(tmp_path, monkeypatch):
    def minimum(build):
        return build(0)
    monkeypatch.setattr("genshin_app.equipment.fresh_result_capacity.finish_fresh_result", minimum)
    calculated = service.run_plan_calc(five_hits(), tmp_path / "absent.db", capture_display=True)
    from genshin_app.equipment.files import json_bytes
    doc = calculated["result"]["damage_explanations"]
    budget = doc["budget"]
    assert budget["maximum_bytes"] == 32 * 1024 * 1024
    assert budget["envelope_document_target_bytes"] == 0
    assert budget["optional_payload_bytes"] == 0
    assert budget["minimum_metadata_bytes"] == budget["accounted_bytes"] == len(json_bytes(doc)) > 0
    validate_delivery_calculated(calculated)


def test_capacity_has_at_most_three_linear_candidate_attempts(tmp_path, monkeypatch):
    db, plan = tmp_path / "absent.db", five_hits()
    full = service.run_plan_calc(copy.deepcopy(plan), db, capture_display=True)
    minimum = minimum_result(plan, db, monkeypatch)
    monkeypatch.setitem(LIMITS, "calculated_bytes", middle_limit(full, minimum))
    calls = []
    def build(maximum):
        calls.append(maximum)
        return copy.deepcopy(full if maximum is None else minimum)
    reduced = finish_fresh_result(build)
    assert len(calls) <= 3 and calls[:2] == [None, 0]
    assert reduced["result"]["summary"] == full["result"]["summary"]


def test_multitype_resource_proofs_reuse_one_execution_session_near_limit(tmp_path, monkeypatch):
    from tests.resource_reaction_review_helpers import count_engine
    from tests.test_resource_reaction_app import reaction_plan
    from tests.test_shared_pool_app_delivery import authorize

    db = tmp_path / "absent.db"
    plan = authorize(reaction_plan(), db)
    calls = count_engine(monkeypatch)
    full = service.run_plan_calc(copy.deepcopy(plan), db, capture_display=True)
    normal_executions = len(calls)
    minimum = minimum_result(plan, db, monkeypatch)
    calls.clear()
    monkeypatch.setitem(LIMITS, "calculated_bytes", middle_limit(full, minimum))
    reduced = service.run_plan_calc(copy.deepcopy(plan), db, capture_display=True)
    assert len(calls) == normal_executions, "private proof candidates must reuse the existing verification replay"
    for key in ("elemental", "shared_pools", "resource_reactions", "rotation_check"):
        assert reduced["result"][key] == full["result"][key]
    assert numeric_rows(reduced) == numeric_rows(full)
    assert reduced["result"]["damage_explanations"]["status"] == "partial"
    validate_delivery_calculated(reduced)


def test_optional_document_does_not_consume_legacy_numerical_proof_bytes(tmp_path, monkeypatch):
    from genshin_app.equipment import resource_reaction_receipt as proof
    from genshin_app.equipment.files import json_bytes
    from tests.test_resource_reaction_app import reaction_plan
    from tests.test_shared_pool_app_delivery import authorize
    db = tmp_path / "absent.db"
    plan = authorize(reaction_plan(), db)
    original, sizes = proof._bounded, []
    def observed(value, **kwargs):
        sizes.append(len(json_bytes(value)))
        return original(value, **kwargs)
    monkeypatch.setattr(proof, "_bounded", observed)
    baseline = service.run_plan_calc(copy.deepcopy(plan), db)
    document_bytes = len(json_bytes(baseline["result"]["damage_explanations"]))
    maximum = max(sizes) + document_bytes // 2
    # The old whole-result accounting crosses this exact guard solely because
    # it counts the optional document. Numerical material remains unchanged.
    assert max(sizes) < maximum < max(sizes) + document_bytes
    def bounded(value, **kwargs):
        return original(value, byte_limit=maximum)
    monkeypatch.setattr(proof, "_bounded", bounded)
    actual = service.run_plan_calc(copy.deepcopy(plan), db)
    assert actual["result"]["damage_explanations"]["status"] == "complete"
    assert numeric_rows(actual) == numeric_rows(baseline)
    assert service.result_bytes(actual["result"], actual["result_receipt"], "json")


def test_v2_numerical_budget_roles_exclude_observers_but_preserve_them_in_result(tmp_path, monkeypatch):
    from genshin_app.equipment.execution_verification import (
        ExecutionVerificationSession,
    )
    from tests.test_resource_lifecycle_app_gates import gated_plan
    from tests.test_shared_pool_app_delivery import authorize
    seen = []
    original = ExecutionVerificationSession.scan_receipt_materials
    def scan(self, **kwargs):
        for value in kwargs.get("outputs", ()):
            if isinstance(value, dict) and "hits" in value and "summary" in value:
                seen.append(1)
                assert "damage_explanations" not in value
                assert all("explanation_ref" not in row for row in value["hits"])
        return original(self, **kwargs)
    monkeypatch.setattr(ExecutionVerificationSession, "scan_receipt_materials", scan)
    db = tmp_path / "absent.db"
    calculated = service.run_plan_calc(authorize(gated_plan(), db), db)
    assert seen
    assert calculated["result"]["damage_explanations"]["status"] == "complete"
    assert any("explanation_ref" in row for row in calculated["result"]["hits"])
    assert service.result_bytes(calculated["result"], calculated["result_receipt"], "json")


@pytest.mark.parametrize("fixed_count", [0, 1, 2])
@pytest.mark.parametrize("external", [False, True])
def test_fixed_explanatory_rows_are_not_zipped_as_numeric_hits(tmp_path, fixed_count, external):
    from tests.test_calculation_overrides_execution import (
        accept_from_compose,
        add_mechanics_hit_relation,
        declaration,
        native_plan,
        override_item,
        target,
    )
    from tests.test_external_damage import confirm, supplement

    db = tmp_path / "absent.db"
    plan = native_plan(dormant_hit=True)
    plan["mechanics_model"]["hit_events"].append({"hit_id": "h1", "action_id": "cast", "order": 20})
    add_mechanics_hit_relation(plan, "h1")
    if external:
        plan["external_damage_supplements"] = [supplement()]
    if fixed_count:
        plan["calculation_event_overrides"] = declaration(*(override_item(
            "replace_computed_value", target("computed_event", f"h{i}", "result.hits"),
            {"expected": 999 + i, "precision": {"kind": "approximate", "method": "固定导出工程夹具",
                                               "uncertainty": "仅验证说明行", "state_effects": "none"}},
            suffix=str(i)) for i in range(fixed_count)))
        accept_from_compose(plan, db)
    if external:
        confirm(plan, db)
    calculated = service.run_plan_calc(plan, db)
    result, receipt = calculated["result"], calculated["result_receipt"]
    if external:
        with pytest.raises(PlanError) as caught:
            service.result_bytes(result, receipt, "csv")
        assert caught.value.reason == "unsupported_external_damage_export"
    else:
        rows = list(csv.reader(io.StringIO(service.result_bytes(result, receipt, "csv").decode("utf-8-sig"))))
        column = rows[0].index("hit_id")
        assert [row[column] for row in rows[1:3]] == ["h0", "h1"]
        assert len([row for row in rows if row and row[0] == "固定值事件"]) == int(fixed_count > 0)
        assert sum(bool(row and "approximate" in row) for row in rows[3:]) == fixed_count
    with zipfile.ZipFile(io.BytesIO(service.result_bytes(result, receipt, "xlsx"))) as book:
        event_sheet = book.read("xl/worksheets/sheet2.xml").decode("utf-8")
        assert "h0" in event_sheet and "h1" in event_sheet
        assert ("固定值事件" in event_sheet) is bool(fixed_count)
        assert event_sheet.count(">approximate<") == fixed_count
