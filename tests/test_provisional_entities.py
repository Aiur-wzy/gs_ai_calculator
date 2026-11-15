from __future__ import annotations

import copy
import csv
import io
import json
import zipfile
from pathlib import Path

import pytest
from formula_workbook_fixtures import envelope

from genshin_app.equipment import service as equipment_service
from genshin_app.equipment.execution_verification import ExecutionVerificationError
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.provisional_entities import (
    APPROVAL_CONTRACT,
    ATTESTATION_CONTRACT,
    CONTRACT,
    LOADOUT_VALUES_CONTRACT,
    LOOKUP_RECEIPT_CONTRACT,
    ProvisionalEntityError,
    approval_request,
    attestation_request,
    authorization_basis_fingerprint,
    build_public_data_diff,
    build_usage_receipt,
    capabilities,
    entity_payload_fingerprint,
    provisional_panel_basis,
    resolve_entity_ref,
    validate_loadout_entity_values,
    validate_registry,
    validate_usage_receipt,
)
from genshin_app.equipment.schema import PlanError, new_plan, validate_plan
from genshin_app.equipment.service import result_bytes
from genshin_app.export import export_result
from genshin_app.formula_workbook import _Projection, _xlsx, build_formula_workbook
from genshin_engine import calculate, convert_v1_to_v2

DB_FP = "a" * 64


def entity(**changes):
    value = {
        "contract": CONTRACT,
        "provisional_id": "local.weapon.reviewed",
        "entity_type": "weapon",
        "authorization_kind": "database_missing",
        "display_name": "受审临时武器",
        "version_claim": {"version_line": "release", "game_version": "7.0.0"},
        "values": {"base_atk": 741.0, "weapon_type": "claymore"},
        "source_refs": [{
            "kind": "agent_research",
            "reference": "https://example.invalid/reviewed-source",
            "status": "verified",
            "fingerprint": "b" * 64,
            "note": "工程夹具，不代表真实游戏资料",
        }],
        "reason": "当前数据库没有该公共实体",
        "necessity": "当前方案必须知道武器基础攻击力",
        "impact": "影响角色基础攻击力及其全部攻击力倍率伤害",
        "risk": "来源可能与当前发行快照不一致",
        "alternatives": ["等待数据库更新", "保留配装草稿但不计算"],
        "database_lookup": {
            "contract": LOOKUP_RECEIPT_CONTRACT,
            "receipt_id": "lookup.weapon.reviewed",
            "issued_by": "app_database_query",
            "status": "not_found",
            "query": {
                "kind": "weapon", "version_line": "release",
                "exact_name": "受审临时武器",
            },
            "database_snapshot_fingerprint": DB_FP,
            "observed_candidates": [],
            "message": "当前快照未找到精确匹配",
            "issued_at": "2026-10-27T00:00:00Z",
        },
        "public_data_diff": None,
        "scope": "current_plan_only",
        "database_authority": False,
        "payload_fingerprint": None,
        "attestation": None,
        "approval": None,
    }
    value.update(changes)
    return value


def plan(row=None, *, plan_id="plan.reviewed"):
    return {"plan_id": plan_id, "provisional_entities": [row or entity()]}


def approved(row=None, *, plan_id="plan.reviewed", candidate_revision=4,
             stage_revision=2, approval_id="approval.reviewed", used=False,
             extensions=None):
    value = copy.deepcopy(row or entity())
    value["payload_fingerprint"] = entity_payload_fingerprint(value)
    receipt = {
        "contract": APPROVAL_CONTRACT,
        "approval_id": approval_id,
        "status": "user_confirmed",
        "issued_by": "user_decision_ledger",
        "plan_id": plan_id,
        "candidate_revision": candidate_revision,
        "stage_revision": stage_revision,
        "authorization_basis_fingerprint": authorization_basis_fingerprint(
            {**plan(value, plan_id=plan_id), "extensions": copy.deepcopy(extensions)}
            if extensions is not None else
            used_plan(value, plan_id=plan_id) if used else plan(value, plan_id=plan_id)
        ),
        "database_snapshot_fingerprint": DB_FP,
        "payload_fingerprint": value["payload_fingerprint"],
        "question_id": "d2-public-patch-reviewed",
        "answer_receipt": "answer.reviewed",
        "approved_at": "2026-10-27T00:00:00Z",
    }
    value["approval"] = receipt
    return value, receipt


def attested(row=None, *, plan_id="plan.reviewed", candidate_revision=4,
             stage_revision=2, attestation_id="attestation.reviewed", used=False,
             extensions=None, database_snapshot_fingerprint=DB_FP):
    value = copy.deepcopy(row or entity())
    value["payload_fingerprint"] = entity_payload_fingerprint(value)
    receipt = {
        "contract": ATTESTATION_CONTRACT,
        "attestation_id": attestation_id,
        "status": "agent_reported",
        "issued_by": "app_agent_materialization",
        "plan_id": plan_id,
        "candidate_revision": candidate_revision,
        "stage_revision": stage_revision,
        "authorization_basis_fingerprint": authorization_basis_fingerprint(
            {**plan(value, plan_id=plan_id), "extensions": copy.deepcopy(extensions)}
            if extensions is not None else
            used_plan(value, plan_id=plan_id) if used else plan(value, plan_id=plan_id)
        ),
        "database_snapshot_fingerprint": database_snapshot_fingerprint,
        "payload_fingerprint": value["payload_fingerprint"],
        "disclosed_at": "2026-10-27T00:00:00Z",
    }
    value["attestation"] = receipt
    return value, receipt


def provisional_ref():
    return {"kind": "provisional", "entity_type": "weapon",
            "provisional_id": "local.weapon.reviewed"}


def used_plan(row, *, plan_id="plan.reviewed"):
    value = plan(row, plan_id=plan_id)
    value["extensions"] = {"selected_entity": provisional_ref()}
    return value


def lookup_ledger(row):
    receipt = row["database_lookup"]
    return {receipt["receipt_id"]: copy.deepcopy(receipt)}


def conflict_entity(**changes):
    lookup = copy.deepcopy(entity()["database_lookup"])
    lookup.update(
        status="conflict",
        observed_candidates=[{
            "database_id": 1, "base_atk": 740.0, "weapon_type": "claymore",
        }],
        message="数据库已有值与核实来源冲突",
    )
    value = entity(
        authorization_kind="public_data_patch", database_lookup=lookup, **changes,
    )
    value["public_data_diff"] = build_public_data_diff(lookup, value["values"])
    return value


def test_registry_is_closed_bounded_and_current_plan_only():
    row = validate_registry(plan())[0]
    assert row["database_authority"] is False
    assert row["scope"] == "current_plan_only"

    forged = entity(extra=True)
    with pytest.raises(ProvisionalEntityError) as caught:
        validate_registry(plan(forged))
    assert caught.value.reason == "provisional_entity_shape"


def test_loadout_value_contract_is_closed_generic_and_resolves_panel_basis():
    character = {
        "contract": LOADOUT_VALUES_CONTRACT, "weapon_type": "claymore",
        "panel_basis": [{
            "level": 90, "base_atk": 300, "base_hp": 12000,
            "base_def": 700,
        }], "extensions": {},
    }
    weapon = {
        "contract": LOADOUT_VALUES_CONTRACT, "weapon_type": "claymore",
        "panel_basis": [{"level": 90, "ascension": 6, "base_atk": 600}],
        "passive_treatment": "included_in_panel",
        "passive_note": "固定面板已包含常驻被动", "extensions": {},
    }
    assert validate_loadout_entity_values("character", character) == character
    assert provisional_panel_basis(
        character, level=90, weapon_values=weapon,
        weapon_level=90, weapon_ascension=6,
    ) == {"base_atk": 900.0, "base_hp": 12000.0, "base_def": 700.0}

    role_specific = copy.deepcopy(character)
    role_specific["special_character_rule"] = True
    with pytest.raises(ProvisionalEntityError) as caught:
        validate_loadout_entity_values("character", role_specific)
    assert caught.value.reason == "provisional_entity_shape"


def test_loadout_panel_basis_rejects_weapon_type_and_stale_level_mismatch():
    character = {
        "contract": LOADOUT_VALUES_CONTRACT, "weapon_type": "sword",
        "panel_basis": [{
            "level": 90, "base_atk": 300, "base_hp": 12000,
            "base_def": 700,
        }], "extensions": {},
    }
    weapon = {
        "contract": LOADOUT_VALUES_CONTRACT, "weapon_type": "claymore",
        "panel_basis": [{"level": 90, "ascension": 6, "base_atk": 600}],
        "passive_treatment": "no_effect_in_scope",
        "passive_note": "当前场景不消费被动", "extensions": {},
    }
    with pytest.raises(ProvisionalEntityError) as caught:
        provisional_panel_basis(
            character, level=90, weapon_values=weapon,
            weapon_level=90, weapon_ascension=6,
        )
    assert caught.value.reason == "provisional_weapon_type_mismatch"

    with pytest.raises(ProvisionalEntityError) as caught:
        provisional_panel_basis(character, level=100)
    assert caught.value.reason == "provisional_character_basis_missing"


def test_provisional_result_cannot_export_without_current_session_authority():
    audit = {"contract": "provisional-public-entity-usage-receipt-v1"}
    with pytest.raises(PlanError) as caught:
        result_bytes(
            {"provisional_entity_usage_receipt": copy.deepcopy(audit)},
            {"provisional_entity_usage_receipt": copy.deepcopy(audit)},
            "json",
        )
    assert caught.value.reason == "provisional_entity_usage_receipt_untrusted"


def _export_authority_fixture():
    snapshot = {"kind": "test-database-snapshot", "version": "current"}
    database_fingerprint = fingerprint(snapshot)
    lookup = copy.deepcopy(entity()["database_lookup"])
    lookup["database_snapshot_fingerprint"] = database_fingerprint
    row, attestation = attested(
        entity(database_lookup=lookup), used=True,
        database_snapshot_fingerprint=database_fingerprint,
    )
    current_plan = used_plan(row)
    resolved = resolve_entity_ref(
        current_plan, provisional_ref(), consumer="agent_materialization",
        database_snapshot_fingerprint=database_fingerprint,
        candidate_revision=4, stage_revision=2, approval_ledger={},
        database_lookup_ledger=lookup_ledger(row),
        agent_attestation_ledger={attestation["attestation_id"]: attestation},
        supported_consumers=frozenset({"agent_materialization"}),
    )
    usage = build_usage_receipt(
        current_plan, [resolved], candidate_revision=4, stage_revision=2,
        database_snapshot_fingerprint=database_fingerprint,
    )

    class CurrentCandidateSession:
        def __init__(self):
            self.candidate = current_plan
            self.revision = 4
            self.stage_state = {"stage_revision": 2}
            self.database_snapshot = snapshot

        def provisional_usage_receipt(self):
            return copy.deepcopy(usage)

    template = json.loads(
        (Path(__file__).parent.parent / "examples" / "freeze_team.json").read_text(
            encoding="utf-8"
        )
    )
    # Identity is part of the captured explanation. Declare it on the input,
    # rather than changing a calculated row after its evidence was produced.
    template = convert_v1_to_v2(template, hit_damage_types=[["damage.other"] for _ in template["hits"]])
    for index, hit in enumerate(template["hits"]):
        hit["id"] = f"fixture.hit.{index}"
    result = calculate(template)
    for hit in result["hits"]:
        hit.setdefault("matched_effect_refs", [])
    result["provisional_entity_usage_receipt"] = copy.deepcopy(usage)
    result["result_fidelity"] = "approximate_with_provisional_entities"
    receipt = {
        "plan_id": "plan.reviewed", "revision": 4,
        "input_fingerprint": "1" * 64, "output_fingerprint": "2" * 64,
        "source_fingerprint": "3" * 64, "template_version": 2,
        "engine_version": "test", "calculated_at": "2026-10-27T00:00:00Z",
        "result_fingerprint": fingerprint(result), "data_versions": [],
        "assumptions": [],
        "provisional_entity_usage_receipt": copy.deepcopy(usage),
        "result_fidelity": "approximate_with_provisional_entities",
    }
    return CurrentCandidateSession(), result, receipt


@pytest.mark.parametrize("fmt", ["json", "csv", "xlsx"])
def test_result_export_uses_candidate_session_only_for_provisional_authority(fmt):
    candidate_session, result, receipt = _export_authority_fixture()
    expected_audit = receipt["provisional_entity_usage_receipt"]["entities"][0][
        "audit"
    ]
    payload = result_bytes(
        result, receipt, fmt, candidate_session=candidate_session,
    )
    assert payload.startswith(b"PK") if fmt == "xlsx" else bool(payload)
    if fmt == "json":
        exported = json.loads(payload)
        assert exported["export_metadata"]["human_label"] == "临时公共资料·近似"
        assert exported["result"]["provisional_entity_usage_receipt"] == (
            receipt["provisional_entity_usage_receipt"]
        )
        assert expected_audit["values"] == {"base_atk": 741.0,
                                             "weapon_type": "claymore"}
        assert expected_audit["source_refs"][0]["reference"].endswith(
            "/reviewed-source"
        )
    elif fmt == "csv":
        rows = list(csv.reader(io.StringIO(payload.decode("utf-8-sig"))))
        notice = rows[0].index("result_notice")
        proof = rows[0].index("provisional_entity_usage_json")
        assert rows[1][notice] == "临时公共资料·近似"
        assert json.loads(rows[1][proof]) == receipt["provisional_entity_usage_receipt"]
    else:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            workbook = archive.read("xl/workbook.xml").decode("utf-8")
            all_xml = "\n".join(
                archive.read(name).decode("utf-8") for name in archive.namelist()
                if name.startswith("xl/worksheets/")
            )
        assert 'name="临时公共资料"' in workbook
        assert "临时公共资料·近似" in all_xml
        assert receipt["provisional_entity_usage_receipt"]["receipt_fingerprint"] in all_xml
        assert expected_audit["audit_fingerprint"] in all_xml
        assert "example.invalid/reviewed-source" in all_xml

    with pytest.raises(ExecutionVerificationError, match="内部核验对象无效"):
        result_bytes(result, receipt, fmt, _session=candidate_session)


@pytest.mark.parametrize("fmt", ["json", "csv", "xlsx"])
def test_direct_export_requires_current_candidate_and_writes_only_after_validation(
    fmt, tmp_path,
):
    candidate_session, result, receipt = _export_authority_fixture()
    target = tmp_path / f"result.{fmt}"
    with pytest.raises(ProvisionalEntityError) as caught:
        export_result(result, fmt, target, receipt=receipt)
    assert caught.value.reason == "provisional_entity_usage_receipt_untrusted"
    assert not target.exists()

    exported = export_result(
        result, fmt, target, receipt=receipt,
        candidate_session=candidate_session,
    )
    assert exported == target
    assert target.is_file() and target.stat().st_size > 0
    if fmt == "json":
        exported_value = json.loads(target.read_text(encoding="utf-8"))
        assert exported_value["export_metadata"]["human_label"] == "临时公共资料·近似"
        assert exported_value["result"]["provisional_entity_usage_receipt"] == (
            receipt["provisional_entity_usage_receipt"]
        )
        from genshin_app.calculation_integrity import result_transport_parts
        saved, saved_receipt = result_transport_parts(exported_value)
        assert saved == result and saved_receipt == receipt
    elif fmt == "csv":
        rows = list(csv.reader(io.StringIO(target.read_text(encoding="utf-8-sig"))))
        assert rows[1][rows[0].index("result_notice")] == "临时公共资料·近似"
        assert json.loads(rows[1][rows[0].index("provisional_entity_usage_json")]) == (
            receipt["provisional_entity_usage_receipt"]
        )
    else:
        with zipfile.ZipFile(target) as archive:
            workbook = archive.read("xl/workbook.xml").decode("utf-8")
            all_xml = "\n".join(
                archive.read(name).decode("utf-8") for name in archive.namelist()
                if name.startswith("xl/worksheets/")
            )
        assert 'name="临时公共资料"' in workbook
        assert "临时公共资料·近似" in all_xml


def test_formula_workbook_prominently_marks_provisional_result():
    _, _, authority_receipt = _export_authority_fixture()
    calculated = envelope()
    usage = copy.deepcopy(authority_receipt["provisional_entity_usage_receipt"])
    calculated["result"]["provisional_entity_usage_receipt"] = usage
    calculated["result"]["result_fidelity"] = (
        "approximate_with_provisional_entities")
    calculated["result_receipt"]["provisional_entity_usage_receipt"] = copy.deepcopy(usage)
    calculated["result_receipt"]["result_fidelity"] = (
        "approximate_with_provisional_entities")
    calculated["result_receipt"]["result_fingerprint"] = fingerprint(
        calculated["result"]
    )
    payload = _xlsx(_Projection(calculated, _session=None).finish().sheets)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        workbook = archive.read("xl/workbook.xml").decode("utf-8")
        all_xml = "\n".join(
            archive.read(name).decode("utf-8") for name in archive.namelist()
            if name.startswith("xl/worksheets/")
        )
    assert 'name="临时公共资料"' in workbook
    assert "临时公共资料·近似" in all_xml
    assert usage["receipt_fingerprint"] in all_xml
    assert usage["entities"][0]["audit"]["audit_fingerprint"] in all_xml
    assert "example.invalid/reviewed-source" in all_xml


def test_export_rejects_stale_candidate_revision():
    candidate_session, result, receipt = _export_authority_fixture()
    candidate_session.revision += 1
    with pytest.raises(PlanError) as caught:
        result_bytes(
            result, receipt, "json", candidate_session=candidate_session,
        )
    assert caught.value.reason == "provisional_entity_usage_receipt_stale"


def test_export_rejects_usage_receipt_grafted_onto_changed_result():
    candidate_session, result, receipt = _export_authority_fixture()
    result["summary"]["total_damage"] += 1
    with pytest.raises(PlanError) as caught:
        result_bytes(
            result, receipt, "json", candidate_session=candidate_session,
        )
    # The shared saved-result binding rejects changed bytes before a narrower
    # provisional-entity authority check can interpret the old usage receipt.
    assert caught.value.reason == "source_changed"


def test_formula_workbook_cannot_bypass_current_candidate_authority():
    _, result, receipt = _export_authority_fixture()
    with pytest.raises(ProvisionalEntityError) as caught:
        build_formula_workbook({"result": result, "result_receipt": receipt})
    assert caught.value.reason == "provisional_entity_usage_receipt_untrusted"


def test_plan_export_forwards_candidate_authority_to_calc_and_serializer(
    monkeypatch, tmp_path,
):
    from genshin_app.equipment.execution_verification import (
        ExecutionVerificationSession,
    )

    candidate_session = object()
    calls = []
    calculated = {
        "result": {}, "result_receipt": {}, "plan": {},
        "equipment_summary": {},
    }

    def fake_calc(plan_value, db_path, *, capture_display, candidate_session, _session):
        calls.append(("calc", plan_value, db_path, capture_display,
                      candidate_session, _session))
        return calculated

    def completion_port(value):
        # This test spies argument forwarding only, not numerical acceptance.
        assert value is calculated
        calls.append(("completion_port", value))

    def fake_bytes(
        result, receipt, fmt, display, equipment_summary, *,
        candidate_session, _session,
    ):
        calls.append(("serialize", result, receipt, fmt, display,
                      equipment_summary, candidate_session, _session))
        return b"{}"

    monkeypatch.setattr(equipment_service, "run_plan_calc", fake_calc)
    monkeypatch.setattr(equipment_service, "result_bytes", fake_bytes)
    monkeypatch.setattr(
        "genshin_app.calculation_completion.require_calculation_complete",
        completion_port,
    )
    target = tmp_path / "result.json"
    equipment_service.export_plan_result(
        {"plan_id": "forwarding.fixture"}, tmp_path / "db.sqlite", "json",
        target, candidate_session=candidate_session,
    )
    assert calls[0][-2] is candidate_session
    assert calls[1] == ("completion_port", calculated)
    assert calls[2][-2] is candidate_session
    assert isinstance(calls[0][-1], ExecutionVerificationSession)
    assert calls[2][-1] is calls[0][-1]
    assert target.read_bytes() == b"{}"


def test_plan_export_rejects_incomplete_calculation_before_serializer(
        monkeypatch, tmp_path):
    monkeypatch.setattr(equipment_service, "run_plan_calc", lambda *_a, **_k: {
        "result": {}, "result_receipt": {}, "plan": {}, "equipment_summary": {},
    })
    monkeypatch.setattr(equipment_service, "result_bytes", lambda *_a, **_k:
                        pytest.fail("incomplete calculation reached serializer"))
    target = tmp_path / "incomplete.json"
    with pytest.raises(PlanError) as caught:
        equipment_service.export_plan_result(
            {"plan_id": "guard.fixture"}, tmp_path / "db.sqlite", "json", target)
    assert caught.value.reason == "calculation_incomplete"
    assert not target.exists()


def test_capabilities_claim_only_the_wired_loadout_consumer():
    value = capabilities()
    assert value["formal_result_usage"]["status"] == (
        "equipment_composer_wired_other_consumers_fail_closed"
    )
    assert value["production_consumer_status"]["agent_materialization"] == (
        "shared_resolver_facade_ready_actual_consumer_unwired"
    )
    assert value["production_consumer_status"]["equipment_composer"] == (
        "formal_usage_receipt_wired"
    )


def test_native_plan_schema_accepts_registry_but_rejects_unknown_fields():
    native = new_plan()
    native["provisional_entities"] = [entity()]
    assert validate_plan(native)["valid"] is True

    native["provisional_entities"][0]["unknown"] = True
    with pytest.raises(PlanError):
        validate_plan(native)


def test_approval_request_binds_exact_payload_plan_revisions_and_database():
    row = conflict_entity()
    request = approval_request(
        plan(row), row, candidate_revision=4, stage_revision=2,
        database_snapshot_fingerprint=DB_FP,
    )
    assert request["contract"] == APPROVAL_CONTRACT
    assert request["payload_fingerprint"] == entity_payload_fingerprint(row)
    assert request["plan_id"] == "plan.reviewed"
    assert request["candidate_revision"] == 4
    assert request["stage_revision"] == 2
    assert request["scope"] == "current_plan_only"
    assert request["database_authority"] is False
    assert request["payload"]["public_data_diff"] == row["public_data_diff"]
    assert row["public_data_diff"]["changed_paths"] == ["/base_atk"]


def test_public_data_patch_rejects_incomplete_or_stale_diff():
    row = conflict_entity()
    row["public_data_diff"]["changed_paths"] = ["/weapon_type"]
    with pytest.raises(ProvisionalEntityError) as caught:
        validate_registry(plan(row))
    assert caught.value.reason == "provisional_entity_patch_diff_mismatch"

    row = conflict_entity()
    row["database_lookup"]["observed_candidates"][0]["base_atk"] = 739.0
    with pytest.raises(ProvisionalEntityError) as caught:
        validate_registry(plan(row))
    assert caught.value.reason in {
        "provisional_entity_patch_baseline_stale",
        "provisional_entity_payload_stale",
    }


def test_agent_attestation_request_binds_missing_payload_without_user_approval():
    row = entity()
    request = attestation_request(
        plan(row), row, candidate_revision=4, stage_revision=2,
        database_snapshot_fingerprint=DB_FP,
    )
    assert request["contract"] == ATTESTATION_CONTRACT
    assert request["payload_fingerprint"] == entity_payload_fingerprint(row)
    assert request["plan_id"] == "plan.reviewed"
    assert request["candidate_revision"] == 4
    assert request["stage_revision"] == 2
    assert request["scope"] == "current_plan_only"
    assert request["database_authority"] is False


def test_database_missing_uses_trusted_lookup_and_verified_agent_source_without_user_approval():
    row, attestation = attested()
    resolved = resolve_entity_ref(
        plan(row), provisional_ref(), consumer="identity_preview",
        database_snapshot_fingerprint=DB_FP,
        candidate_revision=4, stage_revision=2, approval_ledger={},
        database_lookup_ledger=lookup_ledger(row),
        agent_attestation_ledger={attestation["attestation_id"]: attestation},
    )
    assert resolved["authority"] == "agent_reported_database_missing"
    assert resolved["database_authority"] is False
    assert resolved["value"] == row["values"]


def test_usage_receipt_binds_actual_missing_entity_use_to_current_app_context():
    row, attestation = attested(used=True)
    current_plan = used_plan(row)
    resolved = resolve_entity_ref(
        current_plan, provisional_ref(), consumer="agent_materialization",
        database_snapshot_fingerprint=DB_FP,
        candidate_revision=4, stage_revision=2, approval_ledger={},
        database_lookup_ledger=lookup_ledger(row),
        agent_attestation_ledger={attestation["attestation_id"]: attestation},
        supported_consumers=frozenset({"agent_materialization"}),
    )
    receipt = build_usage_receipt(
        current_plan, [resolved], candidate_revision=4, stage_revision=2,
        database_snapshot_fingerprint=DB_FP,
    )
    checked = validate_usage_receipt(
        receipt, current_plan, candidate_revision=4, stage_revision=2,
        database_snapshot_fingerprint=DB_FP,
    )
    assert len(checked["entities"]) == 1
    usage_row = checked["entities"][0]
    assert {key: value for key, value in usage_row.items() if key != "audit"} == {
        "entity_type": "weapon",
        "provisional_id": "local.weapon.reviewed",
        "consumer": "agent_materialization",
        "authority": "agent_reported_database_missing",
        "payload_fingerprint": row["payload_fingerprint"],
        "lookup_receipt_id": "lookup.weapon.reviewed",
        "attestation_id": "attestation.reviewed",
        "approval_id": None,
    }
    audit = usage_row["audit"]
    assert audit["contract"] == "provisional-public-entity-used-value-audit-v1"
    assert audit["audit_only"] is True
    assert audit["values"] == row["values"]
    assert audit["source_refs"] == row["source_refs"]
    assert audit["reason"] == row["reason"]
    assert audit["necessity"] == row["necessity"]
    assert audit["impact"] == row["impact"]
    assert audit["risk"] == row["risk"]
    assert audit["database_lookup"] == row["database_lookup"]
    assert audit["public_data_diff"] is None
    assert audit["authority_proof_ids"] == {
        "lookup_receipt_id": "lookup.weapon.reviewed",
        "attestation_id": "attestation.reviewed",
        "approval_id": None,
    }
    assert audit["usage_context"]["plan_fingerprint"] == receipt["plan_fingerprint"]
    assert audit["usage_context"]["database_snapshot_fingerprint"] == DB_FP


def test_usage_receipt_rejects_resigned_actual_value_audit_tampering():
    row, attestation = attested(used=True)
    current_plan = used_plan(row)
    resolved = resolve_entity_ref(
        current_plan, provisional_ref(), consumer="agent_materialization",
        database_snapshot_fingerprint=DB_FP,
        candidate_revision=4, stage_revision=2, approval_ledger={},
        database_lookup_ledger=lookup_ledger(row),
        agent_attestation_ledger={attestation["attestation_id"]: attestation},
        supported_consumers=frozenset({"agent_materialization"}),
    )
    receipt = build_usage_receipt(
        current_plan, [resolved], candidate_revision=4, stage_revision=2,
        database_snapshot_fingerprint=DB_FP,
    )
    tampered = copy.deepcopy(receipt)
    audit = tampered["entities"][0]["audit"]
    audit["values"]["base_atk"] = 999.0
    audit["audit_fingerprint"] = fingerprint({
        key: value for key, value in audit.items() if key != "audit_fingerprint"
    })
    tampered["receipt_fingerprint"] = fingerprint({
        key: value for key, value in tampered.items() if key != "receipt_fingerprint"
    })
    with pytest.raises(ProvisionalEntityError) as caught:
        validate_usage_receipt(
            tampered, current_plan, candidate_revision=4, stage_revision=2,
            database_snapshot_fingerprint=DB_FP,
        )
    assert caught.value.reason == "provisional_entity_usage_audit_mismatch"


def test_usage_receipt_does_not_leak_unused_provisional_entity_payload():
    first = entity()
    unused = entity(
        provisional_id="local.weapon.unused-secret",
        display_name="未消费秘密武器",
        values={"base_atk": 123456.0, "weapon_type": "claymore"},
        source_refs=[{
            "kind": "agent_research",
            "reference": "https://example.invalid/unused-secret-source",
            "status": "declared",
            "fingerprint": None,
            "note": "该资料不应进入实际使用回执",
        }],
        database_lookup={
            **copy.deepcopy(first["database_lookup"]),
            "receipt_id": "lookup.weapon.unused-secret",
            "query": {
                "kind": "weapon", "version_line": "release",
                "exact_name": "未消费秘密武器",
            },
        },
    )
    current_plan = {
        "plan_id": "plan.reviewed",
        "provisional_entities": [first, unused],
        "extensions": {"selected_entity": provisional_ref()},
    }
    first["payload_fingerprint"] = entity_payload_fingerprint(first)
    attestation = {
        "contract": ATTESTATION_CONTRACT,
        "attestation_id": "attestation.used-only",
        "status": "agent_reported",
        "issued_by": "app_agent_materialization",
        "plan_id": "plan.reviewed",
        "candidate_revision": 4,
        "stage_revision": 2,
        "authorization_basis_fingerprint": authorization_basis_fingerprint(
            current_plan
        ),
        "database_snapshot_fingerprint": DB_FP,
        "payload_fingerprint": first["payload_fingerprint"],
        "disclosed_at": "2026-10-27T00:00:00Z",
    }
    first["attestation"] = attestation
    resolved = resolve_entity_ref(
        current_plan, provisional_ref(), consumer="agent_materialization",
        database_snapshot_fingerprint=DB_FP,
        candidate_revision=4, stage_revision=2, approval_ledger={},
        database_lookup_ledger=lookup_ledger(first),
        agent_attestation_ledger={attestation["attestation_id"]: attestation},
        supported_consumers=frozenset({"agent_materialization"}),
    )
    receipt = build_usage_receipt(
        current_plan, [resolved], candidate_revision=4, stage_revision=2,
        database_snapshot_fingerprint=DB_FP,
    )
    assert [row["provisional_id"] for row in receipt["entities"]] == [
        "local.weapon.reviewed"
    ]
    encoded = json.dumps(receipt, ensure_ascii=False)
    assert "unused-secret" not in encoded
    assert "未消费秘密武器" not in encoded
    assert "123456" not in encoded


def test_hand_built_resolved_mapping_cannot_mint_formal_usage_receipt():
    row, attestation = attested(used=True)
    current_plan = used_plan(row)
    trusted = resolve_entity_ref(
        current_plan, provisional_ref(), consumer="agent_materialization",
        database_snapshot_fingerprint=DB_FP,
        candidate_revision=4, stage_revision=2, approval_ledger={},
        database_lookup_ledger=lookup_ledger(row),
        agent_attestation_ledger={attestation["attestation_id"]: attestation},
        supported_consumers=frozenset({"agent_materialization"}),
    )
    forged = copy.deepcopy(dict(trusted))
    with pytest.raises(ProvisionalEntityError) as caught:
        build_usage_receipt(
            current_plan, [forged], candidate_revision=4, stage_revision=2,
            database_snapshot_fingerprint=DB_FP,
        )
    assert caught.value.reason == "provisional_entity_usage_resolution_invalid"


def test_usage_receipt_rejects_unresolved_extra_plan_reference():
    extensions = {
        "selected_entity": provisional_ref(),
        "second_entity": provisional_ref(),
    }
    row, attestation = attested(extensions=extensions)
    current_plan = used_plan(row)
    current_plan["extensions"]["second_entity"] = provisional_ref()
    resolved = resolve_entity_ref(
        current_plan, provisional_ref(), consumer="agent_materialization",
        database_snapshot_fingerprint=DB_FP,
        candidate_revision=4, stage_revision=2, approval_ledger={},
        database_lookup_ledger=lookup_ledger(row),
        agent_attestation_ledger={attestation["attestation_id"]: attestation},
        supported_consumers=frozenset({"agent_materialization"}),
    )
    with pytest.raises(ProvisionalEntityError) as caught:
        build_usage_receipt(
            current_plan, [resolved], candidate_revision=4, stage_revision=2,
            database_snapshot_fingerprint=DB_FP,
        )
    assert caught.value.reason == "provisional_entity_usage_coverage_mismatch"


def test_read_only_preview_resolution_cannot_claim_formal_calculation_usage():
    row, attestation = attested(used=True)
    current_plan = used_plan(row)
    resolved = resolve_entity_ref(
        current_plan, provisional_ref(), consumer="identity_preview",
        database_snapshot_fingerprint=DB_FP,
        candidate_revision=4, stage_revision=2, approval_ledger={},
        database_lookup_ledger=lookup_ledger(row),
        agent_attestation_ledger={attestation["attestation_id"]: attestation},
    )
    with pytest.raises(ProvisionalEntityError) as caught:
        build_usage_receipt(
            current_plan, [resolved], candidate_revision=4, stage_revision=2,
            database_snapshot_fingerprint=DB_FP,
        )
    assert caught.value.reason == "provisional_entity_usage_consumer_not_executable"


def test_registry_reference_contract_resolves_without_faking_database_identity():
    ref = {
        "contract": "provisional-entity-registry-reference-v1",
        "entity_type": "weapon",
        "provisional_id": "local.weapon.reviewed",
    }
    row, attestation = attested(extensions={"selected_entity": ref})
    current_plan = plan(row)
    current_plan["extensions"] = {"selected_entity": ref}
    resolved = resolve_entity_ref(
        current_plan, ref, consumer="agent_materialization",
        database_snapshot_fingerprint=DB_FP,
        candidate_revision=4, stage_revision=2, approval_ledger={},
        database_lookup_ledger=lookup_ledger(row),
        agent_attestation_ledger={attestation["attestation_id"]: attestation},
        supported_consumers=frozenset({"agent_materialization"}),
    )
    receipt = build_usage_receipt(
        current_plan, [resolved], candidate_revision=4, stage_revision=2,
        database_snapshot_fingerprint=DB_FP,
    )
    assert receipt["entities"][0]["provisional_id"] == "local.weapon.reviewed"


def test_database_missing_cannot_be_self_asserted_without_trusted_app_lookup():
    row, attestation = attested()
    with pytest.raises(ProvisionalEntityError) as caught:
        resolve_entity_ref(
            plan(row), provisional_ref(), consumer="identity_preview",
            database_snapshot_fingerprint=DB_FP,
            candidate_revision=4, stage_revision=2, approval_ledger={},
            database_lookup_ledger={},
            agent_attestation_ledger={attestation["attestation_id"]: attestation},
        )
    assert caught.value.reason == "provisional_entity_lookup_untrusted"


def test_unavailable_query_is_diagnostic_not_proof_of_missing_data():
    lookup = copy.deepcopy(entity()["database_lookup"])
    lookup.update(status="unavailable", message="数据库查询服务暂不可用")
    row = entity(database_lookup=lookup)
    row["payload_fingerprint"] = entity_payload_fingerprint(row)
    with pytest.raises(ProvisionalEntityError) as caught:
        attestation_request(
            plan(row), row, candidate_revision=4, stage_revision=2,
            database_snapshot_fingerprint=DB_FP,
        )
    assert caught.value.reason == "provisional_entity_lookup_unavailable"


def test_public_data_patch_requires_trusted_lookup_and_user_approval():
    row, receipt = approved(conflict_entity())
    with pytest.raises(ProvisionalEntityError) as caught:
        resolve_entity_ref(
            plan(row), provisional_ref(), consumer="identity_preview",
            database_snapshot_fingerprint=DB_FP,
            candidate_revision=4, stage_revision=2, approval_ledger={},
            database_lookup_ledger=lookup_ledger(row),
        )
    assert caught.value.reason == "provisional_entity_approval_untrusted"
    resolved = resolve_entity_ref(
        plan(row), provisional_ref(), consumer="identity_preview",
        database_snapshot_fingerprint=DB_FP,
        candidate_revision=4, stage_revision=2,
        approval_ledger={receipt["approval_id"]: receipt},
        database_lookup_ledger=lookup_ledger(row),
    )
    assert resolved["authority"] == "user_confirmed_public_data_patch"


def test_public_data_patch_usage_receipt_preserves_exact_user_approval():
    row, approval = approved(conflict_entity(), used=True)
    current_plan = used_plan(row)
    resolved = resolve_entity_ref(
        current_plan, provisional_ref(), consumer="agent_materialization",
        database_snapshot_fingerprint=DB_FP,
        candidate_revision=4, stage_revision=2,
        approval_ledger={approval["approval_id"]: approval},
        database_lookup_ledger=lookup_ledger(row),
        supported_consumers=frozenset({"agent_materialization"}),
    )
    receipt = build_usage_receipt(
        current_plan, [resolved], candidate_revision=4, stage_revision=2,
        database_snapshot_fingerprint=DB_FP,
    )
    assert receipt["entities"][0]["authority"] == "user_confirmed_public_data_patch"
    assert receipt["entities"][0]["approval_id"] == approval["approval_id"]
    assert receipt["entities"][0]["attestation_id"] is None
    audit = receipt["entities"][0]["audit"]
    assert audit["values"] == row["values"]
    assert audit["database_lookup"] == row["database_lookup"]
    assert audit["public_data_diff"] == row["public_data_diff"]
    assert audit["authority_proof_ids"] == {
        "lookup_receipt_id": row["database_lookup"]["receipt_id"],
        "attestation_id": None,
        "approval_id": approval["approval_id"],
    }


@pytest.mark.parametrize("mutation", ["value", "evidence", "database", "plan"])
def test_any_bound_context_change_invalidates_provisional_entity(mutation):
    row, receipt = approved(conflict_entity())
    current_plan = plan(row)
    db_fp, revision = DB_FP, 4
    if mutation == "value":
        row["values"]["base_atk"] += 1
    elif mutation == "evidence":
        row["source_refs"][0]["note"] += " changed"
    elif mutation == "database":
        db_fp = "c" * 64
    elif mutation == "plan":
        current_plan["plan_id"] = "plan.other"
    with pytest.raises(ProvisionalEntityError) as caught:
        resolve_entity_ref(
            current_plan, provisional_ref(), consumer="identity_preview",
            database_snapshot_fingerprint=db_fp,
            candidate_revision=revision, stage_revision=2,
            approval_ledger={receipt["approval_id"]: receipt},
            database_lookup_ledger=lookup_ledger(row),
        )
    assert caught.value.reason in {
        "provisional_entity_patch_diff_mismatch",
        "provisional_entity_payload_stale",
        "provisional_entity_database_stale",
        "provisional_entity_approval_stale",
    }


def test_later_authority_only_revision_does_not_invalidate_existing_proof():
    row, receipt = approved(conflict_entity())
    resolved = resolve_entity_ref(
        plan(row), provisional_ref(), consumer="identity_preview",
        database_snapshot_fingerprint=DB_FP,
        candidate_revision=5, stage_revision=2,
        approval_ledger={receipt["approval_id"]: receipt},
        database_lookup_ledger=lookup_ledger(row),
    )
    assert resolved["authority"] == "user_confirmed_public_data_patch"


def test_two_sibling_authorizations_resolve_in_one_final_plan():
    first = entity()
    second = entity(
        provisional_id="local.weapon.second",
        display_name="第二把受审临时武器",
        database_lookup={
            **copy.deepcopy(first["database_lookup"]),
            "receipt_id": "lookup.weapon.second",
            "query": {
                "kind": "weapon", "version_line": "release",
                "exact_name": "第二把受审临时武器",
            },
        },
    )
    refs = [
        provisional_ref(),
        {"kind": "provisional", "entity_type": "weapon",
         "provisional_id": "local.weapon.second"},
    ]
    current_plan = {
        "plan_id": "plan.reviewed",
        "provisional_entities": [first, second],
        "extensions": {"selected_entities": refs},
    }
    basis = authorization_basis_fingerprint(current_plan)
    ledgers = {"lookup": {}, "attestation": {}}
    for index, row in enumerate((first, second), start=1):
        row["payload_fingerprint"] = entity_payload_fingerprint(row)
        proof = {
            "contract": ATTESTATION_CONTRACT,
            "attestation_id": f"attestation.sibling.{index}",
            "status": "agent_reported",
            "issued_by": "app_agent_materialization",
            "plan_id": "plan.reviewed",
            "candidate_revision": 3 + index,
            "stage_revision": 2,
            "authorization_basis_fingerprint": basis,
            "database_snapshot_fingerprint": DB_FP,
            "payload_fingerprint": row["payload_fingerprint"],
            "disclosed_at": "2026-10-27T00:00:00Z",
        }
        row["attestation"] = proof
        ledgers["lookup"][row["database_lookup"]["receipt_id"]] = copy.deepcopy(
            row["database_lookup"]
        )
        ledgers["attestation"][proof["attestation_id"]] = copy.deepcopy(proof)
    resolved = [
        resolve_entity_ref(
            current_plan, ref, consumer="agent_materialization",
            database_snapshot_fingerprint=DB_FP,
            candidate_revision=6, stage_revision=2, approval_ledger={},
            database_lookup_ledger=ledgers["lookup"],
            agent_attestation_ledger=ledgers["attestation"],
            supported_consumers=frozenset({"agent_materialization"}),
        )
        for ref in refs
    ]
    receipt = build_usage_receipt(
        current_plan, resolved, candidate_revision=6, stage_revision=2,
        database_snapshot_fingerprint=DB_FP,
    )
    assert [row["provisional_id"] for row in receipt["entities"]] == [
        "local.weapon.reviewed", "local.weapon.second",
    ]


def test_proof_from_future_candidate_revision_is_rejected():
    row, receipt = approved(conflict_entity(), candidate_revision=5)
    with pytest.raises(ProvisionalEntityError) as caught:
        resolve_entity_ref(
            plan(row), provisional_ref(), consumer="identity_preview",
            database_snapshot_fingerprint=DB_FP,
            candidate_revision=4, stage_revision=2,
            approval_ledger={receipt["approval_id"]: receipt},
            database_lookup_ledger=lookup_ledger(row),
        )
    assert caught.value.reason == "provisional_entity_approval_stale"


def test_unwired_consumer_fails_explicitly_instead_of_silent_adoption():
    row, receipt = approved()
    with pytest.raises(ProvisionalEntityError) as caught:
        resolve_entity_ref(
            plan(row), provisional_ref(), consumer="unreviewed_consumer",
            database_snapshot_fingerprint=DB_FP,
            candidate_revision=4, stage_revision=2,
            approval_ledger={receipt["approval_id"]: receipt},
            database_lookup_ledger=lookup_ledger(row),
        )
    assert caught.value.reason == "provisional_entity_consumer_unsupported"
    assert caught.value.category == "engine_capability_missing"


def test_database_reference_requires_real_resolver_result_and_rejects_fake_id():
    ref = {"kind": "database", "entity_type": "weapon",
           "database_id": 999999999, "version_line": "release"}
    with pytest.raises(ProvisionalEntityError) as caught:
        resolve_entity_ref(
            {"plan_id": "plan.reviewed"}, ref, consumer="identity_preview",
            database_snapshot_fingerprint=DB_FP, candidate_revision=0,
            stage_revision=0, approval_ledger={}, database_resolver=lambda _: None,
        )
    assert caught.value.reason == "entity_not_found"
    assert caught.value.category == "core_data_missing"


def test_approval_from_another_plan_cannot_be_replayed():
    row, receipt = approved(conflict_entity(), plan_id="plan.first")
    with pytest.raises(ProvisionalEntityError) as caught:
        resolve_entity_ref(
            plan(row, plan_id="plan.second"), provisional_ref(),
            consumer="identity_preview", database_snapshot_fingerprint=DB_FP,
            candidate_revision=4, stage_revision=2,
            approval_ledger={receipt["approval_id"]: receipt},
            database_lookup_ledger=lookup_ledger(row),
        )
    assert caught.value.reason == "provisional_entity_approval_stale"
