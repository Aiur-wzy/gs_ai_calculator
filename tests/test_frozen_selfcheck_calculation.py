"""Source probes exercise package-owned engineering inputs, never frozen claims."""
from __future__ import annotations

import copy
import sqlite3
import sys
import uuid

import pytest

from genshin_app.frozen_selfcheck_calculation import (
    verify_appendix_index_response,
    verify_fixed_calculations,
    verify_flow_budget,
    verify_flow_damage_explanations,
)
from genshin_app.frozen_selfcheck_isolation import SelfcheckError
from tests.test_queries import db_path as db_path  # noqa: PLC0414


@pytest.fixture
def explained_flow(tmp_path, db_path):
    from genshin_app.agent_chat.artifact_candidate import ArtifactCandidateSession
    from genshin_app.frozen_selfcheck_inputs import build_fixed_inputs
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = dict(connection.execute("SELECT * FROM character LIMIT 1").fetchone())
        row.update(id=90000002, name="第二工程身份")
        connection.execute("INSERT INTO character (" + ",".join(row) + ") VALUES (" + ",".join("?" for _ in row) + ")", tuple(row.values()))
    seed, calls = build_fixed_inputs(tmp_path, db_path)
    session = ArtifactCandidateSession(uuid.uuid4().hex, tmp_path / "explanation-source-flow", db_path, "whiteboard", seed)
    for call in calls:
        reply = session.call_tool(call["tool"], call["arguments"])
        assert reply["code"] == 0, reply
    return session.calculated


def test_flow_explanation_check_reads_real_saved_evidence_without_recalculation(explained_flow):
    before = copy.deepcopy(explained_flow)
    receipt = verify_flow_damage_explanations(explained_flow)
    assert receipt == {"contract": "damage-explanation-v1", "schema_version": 1, "status": "verified",
        "document_fingerprint": explained_flow["result"]["damage_explanation_fingerprint"],
        "hit_count": 5, "explained_hit_count": 5, "all_hit_references_closed": True,
        "ordinary_scaling": [100] * 5, "evidence_only_no_second_solver": True}
    assert explained_flow["result"]["summary"]["total_damage"] == pytest.approx(594)
    window = explained_flow['result']['damage_report_window']
    assert window['status'] == 'complete' and window['end'] == pytest.approx(5.4)
    assert explained_flow['result']['damage_report_execution']['rotation_model']['period'] == 6
    assert explained_flow == before


def test_flow_explanation_check_rejects_missing_and_inconsistent_saved_evidence(explained_flow):
    def missing(value):
        value["result"].pop("damage_explanations")
    def wrong_version(value):
        value["result"]["damage_explanations"]["schema_version"] = 2
    def bad_fingerprint(value):
        value["result"]["damage_explanation_fingerprint"] = "0" * 64
    def dangling_hit(value):
        value["result"]["hits"][0]["explanation_ref"] = "absent"
    def wrong_panel(value):
        panel = next(iter(value["result"]["damage_explanations"]["panels"].values()))
        panel["inputs"]["scaling"] = 200
    for mutate in (missing, wrong_version, bad_fingerprint, dangling_hit, wrong_panel):
        value = copy.deepcopy(explained_flow)
        mutate(value)
        with pytest.raises(SelfcheckError, match="flow_damage_explanation"):
            verify_flow_damage_explanations(value)


def test_appendix_navigation_check_preserves_knowledge_and_body_boundaries():
    value = {"code": 0, "data": {"contract_id": "agent-character-appendix-index-v1", "db_character_id": 42,
        "status": "complete", "database_fingerprint": "a" * 64, "resource": "query-1",
        "body_read": False, "execution_authority": "none"}}
    before = copy.deepcopy(value)
    assert verify_appendix_index_response(value, 42)["body_read"] is False
    assert value == before
    for field, replacement in (("contract_id", "old"), ("db_character_id", 43), ("status", "not_found"),
                               ("body_read", True), ("execution_authority", "executable"), ("resource", "")):
        changed = copy.deepcopy(value)
        changed["data"][field] = replacement
        with pytest.raises(SelfcheckError, match="appendix_navigation"):
            verify_appendix_index_response(changed, 42)
    for changed in (None, {}, {"code": 1, "data": value["data"]}):
        with pytest.raises(SelfcheckError, match="appendix_navigation"):
            verify_appendix_index_response(changed, 42)


def test_fixed_calculation_probes_execute_and_preserve_separate_oracles(tmp_path):
    result = verify_fixed_calculations(tmp_path)
    assert result["execution"] == {"layer": "source_probe", "actual_frozen_tested": False}
    assert result["included_in_594_total"] is False
    assert result["conversion"]["result"] == pytest.approx(108)
    assert result["conversion"]["adopted"] is result["conversion"]["executes"] is False
    assert len(result["tamper_rejections"]) == 12
    assert result["weighted_workbook"]["actual"] == pytest.approx(result["weighted_workbook"]["expected_by_independent_joint_outcomes"])
    assert result["zero_workbook"]["actual"] == [0, 0]
    assert result["zero_workbook"]["hit_count"] == 2
    assert len(result["fixed_weight_workbooks"]) == 4
    assert result["declarations"]["capacity_issue"]["attempted"] == 2
    assert result["declarations"]["capacity_guidance"]["recovery"]["action"] == "adjust_execution_budget"
    assert result["atomic_credit"]["balances"] == {"a": 0, "b": 0, "c": 0}
    assert result["atomic_credit"]["attempt"]["status"] == "not_committed"
    assert result["public_cli"]["family_count"] == 5
    assert len(result["public_cli"]["hashes"]) == 3
    assert all(row["authority"] == "none" and row["game_facts_verified"] is False for row in result["public_cli"]["hashes"])
    assert result["v3_saved_proof"]["capture_display"] is True
    assert result["v3_saved_proof"]["numeric_check"]["result"] == pytest.approx(108)
    assert {row["mutation"] for row in result["v3_saved_proof"]["recomputed_hash_tamper_rejections"]} == {"ledger", "sampling", "hit", "proof"}
    assert all(weight > 0 for weight in result["zero_workbook"]["weights"])
    assert [row["energy"][0]["gain"] for row in result["delayed_credit"]["credits"]] == pytest.approx([3.3, 3.6])
    assert not (tmp_path/"intentionally-absent.db").exists()


def test_corrupted_engine_weighting_fails_independent_oracle(tmp_path, monkeypatch):
    from genshin_engine import mechanics_special_damage
    original = mechanics_special_damage._apply_special_damage_weights
    def corrupt(template, result, groups):
        value = original(template, result, groups)
        template["hits"][0]["result_weight"] *= .9
        return value
    monkeypatch.setattr(mechanics_special_damage, "_apply_special_damage_weights", corrupt)
    with pytest.raises(SelfcheckError, match="numeric_mismatch"):
        verify_fixed_calculations(tmp_path)


def test_source_flags_cannot_claim_frozen_execution(tmp_path, monkeypatch):
    with pytest.raises(SelfcheckError, match="helper_not_frozen"):
        verify_fixed_calculations(tmp_path, frozen_package_root=tmp_path)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    with pytest.raises(SelfcheckError, match="helper_loader_not_frozen"):
        verify_fixed_calculations(tmp_path, frozen_package_root=tmp_path)


def test_budget_verifier_rejects_missing_proof():
    value = {"plan": {}, "result": {}, "result_receipt": {}}
    before = copy.deepcopy(value)
    with pytest.raises(SelfcheckError, match="flow_budget_not_consumed"):
        verify_flow_budget(value)
    assert value == before


def test_public_cli_hash_probe_detects_wrong_producer_digest(tmp_path, monkeypatch):
    from genshin_app.agent_chat import agent_hash_input
    from genshin_app.frozen_selfcheck_calculation import _public_cli_probe
    original = agent_hash_input.hash_input
    def wrong_digest(*args, **kwargs):
        result = original(*args, **kwargs)
        result["content_hash"] = "0"*64
        return result
    monkeypatch.setattr(agent_hash_input, "hash_input", wrong_digest)
    with pytest.raises(SelfcheckError, match="public_hash_wrong_or_authorizing"):
        _public_cli_probe(tmp_path)
