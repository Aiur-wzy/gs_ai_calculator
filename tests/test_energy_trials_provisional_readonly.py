"""Real App authorization resolves read-only ER copies without new receipts."""
from __future__ import annotations

# ruff: noqa: F811 - imported pytest database fixtures.
import copy
import json
import uuid

import pytest

from genshin_app.agent_chat.artifact_candidate import ArtifactCandidateSession
from genshin_app.agent_chat.provisional_authority import prepare_readonly_projection
from genshin_app.calculation_completion import (
    calculation_completion,
    require_calculation_complete,
)
from genshin_app.calculation_qualification import qualification_for_claim
from genshin_app.energy_trials import _apply_stats, trial_energy_recharge
from genshin_app.equipment.provisional_entities import LOADOUT_VALUES_CONTRACT
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import result_bytes, run_plan_calc
from genshin_db import queries
from tests.test_energy_trials import plan, request
from tests.test_equipment import equipment_db, fake_repo, stub_fetcher  # noqa: F401
from tests.test_provisional_authority import _draft, _normalized_question


@pytest.fixture
def session(tmp_path, monkeypatch):
    database = tmp_path / "genshin.db"
    database.touch()
    monkeypatch.setattr(queries, "get_meta", lambda *_: {"state": "controlled_test_snapshot"})
    monkeypatch.setattr(queries, "list_weapons", lambda *a, **kw: {"items": [], "total": 0, "page": 1, "page_size": 100})
    source = plan()
    source["equipment_inputs"] = {"a": {"mode": "panel", "panel_policy": "static_included", "panel_basis_mode": "manual",
        "weapon": {"weapon_id": None, "level": 90, "ascension": 6, "selected_by": "agent", "refinements": [],
                   "entity_ref": {"kind": "provisional", "entity_type": "weapon", "provisional_id": "local.weapon"}},
        "artifact_sets": [], "source_refs": [{"kind": "manual", "note": "controlled given panel"}], "data_version": None}}
    values = {"contract": LOADOUT_VALUES_CONTRACT, "extensions": {}, "weapon_type": "sword",
        "panel_basis": [{"level": 90, "ascension": 6, "base_atk": 741}],
        "passive_treatment": "included_in_panel", "passive_note": "controlled test panel includes passive"}
    source["provisional_entities"] = [_draft("local.weapon", "临时武器", values=values)]
    return ArtifactCandidateSession(uuid.uuid4().hex, tmp_path / "session", database, "native", source)


def authorize_missing(session):
    session.verify_provisional_lookup(provisional_id="local.weapon", expected_revision=session.revision,
        database_id=None, reason="actual App query")
    session.attest_provisional_database_missing(provisional_id="local.weapon", expected_revision=session.revision,
        reason="actual App attestation of sourced missing fact")


def state(session):
    return (copy.deepcopy(session.candidate), copy.deepcopy(session.provisional_authority), session.revision,
        copy.deepcopy(session.stage_state), {path.name: path.read_bytes() for path in session.root.iterdir() if path.is_file()})


def test_actual_missing_fact_resolver_allows_stats_only_trial_without_session_writes(session, monkeypatch):
    authorize_missing(session)
    before = state(session)
    monkeypatch.setattr(session, "save", lambda: pytest.fail("readonly trial attempted to save Session"))
    monkeypatch.setattr(session, "publish_provisional_usage_receipt", lambda **kw: pytest.fail("readonly trial published formal usage"))
    result = trial_energy_recharge(session.candidate, session.db_path, request(), candidate_session=session,
                                  energy_intent="multi_round_legal")
    assert state(session) == before
    baseline, changed = result["trials"]
    assert baseline["status"] == changed["status"] == "tested", result
    assert baseline["energy"]["bursts"][1]["deficit"] == 2
    assert changed["energy"]["bursts"][1]["deficit"] == 0
    assert changed["calculation"]["execution_admission"] == "accepted"
    assert changed["calculation"]["task_admission"] == "not_requested"
    healthy = changed["energy_qualification"]
    assert healthy["status"] == "passed" and healthy["execution_passed"] is True
    assert healthy["generic_engine_validation"] == healthy["energy_validation"] == "passed"
    assert healthy["formal_result_qualification"] == "not_applicable" and healthy["formal_result_authority"] is False
    assert "can_accept" not in healthy and healthy["issue_count"] == 0
    failed = baseline["energy_qualification"]
    assert failed["status"] == "failed" and failed["execution_passed"] is False
    assert failed["generic_engine_validation"] == failed["energy_validation"] == "failed"
    assert "explicit_window_energy_failed" in {row["code"] for row in failed["issues"]}
    proof = changed["read_only_public_facts"]
    assert proof["formal_result_authority"] is False
    assert proof["uses"][0]["authority"] == "agent_reported_database_missing"
    assert session.provisional_usage_receipt() is None


def test_real_calculation_copy_cannot_be_published_or_acquire_formal_usage(session):
    authorize_missing(session)
    trial = _apply_stats(session.candidate, request()["trials"][0])
    context = prepare_readonly_projection(session, trial, request()["trials"][0]["stats"])
    calculated = run_plan_calc(trial, session.db_path, _readonly_provisional_context=context)
    assert calculated["result"]["summary"]["total_damage"] > 0
    assert "provisional_entity_usage_receipt" not in calculated["result_receipt"]
    assert calculation_completion(calculated)["task_admission"] != "accepted"
    assert qualification_for_claim(calculated, energy_intent="multi_round_legal", trusted_context=session)["can_accept"] is False
    with pytest.raises(PlanError, match="正式计算未完成"):
        require_calculation_complete(calculated)
    with pytest.raises(ValueError):
        result_bytes(calculated["result"], calculated["result_receipt"], "json", candidate_session=session)
    assert session.provisional_usage_receipt() is None


def test_missing_or_unapproved_original_authority_is_not_assumed(session):
    before = state(session)
    result = trial_energy_recharge(session.candidate, session.db_path, request(), candidate_session=session)
    assert all(row["status"] == "incomplete" for row in result["trials"])
    assert state(session) == before
    authorize_missing(session)
    independent = trial_energy_recharge(session.candidate, session.db_path, request())
    assert all(row["status"] == "incomplete" for row in independent["trials"])
    assert all(row["failure"]["reason"] == "energy_trial_trust_scope_unavailable" for row in independent["trials"])


@pytest.mark.parametrize("mutate", [
    lambda source: source["provisional_entities"][0]["values"]["panel_basis"][0].update(base_atk=999),
    lambda source: source["provisional_entities"][0]["source_refs"][0].update(reference="https://not-approved.invalid"),
    lambda source: source["equipment_inputs"]["a"]["weapon"]["entity_ref"].update(provisional_id="another"),
    lambda source: source["rotation_model"]["actions"][1].update(start=9),
    lambda source: source.update(plan_id="another-plan"),
])
def test_projection_rejects_any_non_stat_change_instead_of_resigning(session, mutate):
    authorize_missing(session)
    declaration = request()["trials"][0]
    trial = _apply_stats(session.candidate, declaration)
    mutate(trial)
    with pytest.raises(PlanError) as failure:
        prepare_readonly_projection(session, trial, declaration["stats"])
    assert failure.value.reason == "energy_trial_authorization_scope_changed"


def test_projection_rejects_later_scope_revocation(session):
    authorize_missing(session)
    declaration = request()["trials"][0]
    trial = _apply_stats(session.candidate, declaration)
    context = prepare_readonly_projection(session, trial, declaration["stats"])
    session.provisional_authority["attestation_ledger"].clear()
    with pytest.raises(PlanError) as failure:
        run_plan_calc(trial, session.db_path, _readonly_provisional_context=context)
    assert failure.value.reason == "energy_trial_authorization_stale"


def test_actual_user_patch_requires_original_user_decision_but_not_a_new_trial_approval(session, monkeypatch):
    source = copy.deepcopy(session.candidate)
    source["provisional_entities"][0].update(authorization_kind="public_data_patch", display_name="数据库武器")
    source["manual_template"]["characters"][0]["stats"]["energy_recharge"] = 2
    session.replace_candidate(source, session.revision, "prepare actual controlled correction proposal")
    monkeypatch.setattr(queries, "list_weapons", lambda *a, **kw: {"items": [{"id": 9001, "name": "数据库武器"}],
        "total": 1, "page": 1, "page_size": 100})
    monkeypatch.setattr(queries, "get_weapon_detail", lambda *a, **kw: {"weapon": {"id": 9001, "name": "数据库武器", "weapon_type": "sword", "base_atk": 740}})
    monkeypatch.setattr(queries, "get_weapon_stats", lambda *a, **kw: {"properties": [{"prop_type": "FIGHT_PROP_BASE_ATTACK",
        "unit": "flat", "value": 740, "source_refs": [{"kind": "database", "table": "weapon_property"}]}]})
    session.verify_provisional_lookup(provisional_id="local.weapon", expected_revision=session.revision,
        database_id=None, reason="actual query of existing DB data")
    denied = trial_energy_recharge(session.candidate, session.db_path, request(), candidate_session=session)
    assert all(row["status"] == "incomplete" for row in denied["trials"])
    proposed = session.request_provisional_public_patch_approval(provisional_id="local.weapon", expected_revision=session.revision,
        reason="ask user for exact original correction")
    normalized = _normalized_question(proposed["question"])
    session.bind_provisional_approval_question(batch_id="approval-batch", call_id="approval-call", items=[normalized])
    session.settle_provisional_approval_question(question={"question_id": "approval-batch", "answer_receipt": "actual-answer",
        "items": [normalized], "answer": {"items": [{"question_id": normalized["question_id"],
            "value_ids": [normalized["options"][0]["value_id"]], "text": None, "skipped": False}]}}, reason="Host-received exact user decision")
    # Formal execution still requires and publishes the original real receipt.
    formal = run_plan_calc(session.candidate, session.db_path, candidate_session=session)
    assert formal["result"]["provisional_entity_usage_receipt"]["entities"][0]["approval_id"]
    result_bytes(formal["result"], formal["result_receipt"], "json", candidate_session=session)
    from genshin_app.agent_chat.candidate import CandidateSession
    session = CandidateSession.load(session.session_id, session.root, session.db_path)
    before = state(session)
    output = trial_energy_recharge(session.candidate, session.db_path,
        {"trials": [{"id": "er-150", "stats": {"a": {"energy_recharge": 1.5}}}]}, candidate_session=session)
    assert all(row["status"] == "tested" for row in output["trials"]), output
    assert output["trials"][1]["read_only_public_facts"]["uses"][0]["authority"] == "user_confirmed_public_data_patch"
    assert state(session) == before
    assert len(session.provisional_authority["approval_ledger"]) == 1



def test_real_sqlite_weapon_query_approval_reload_formal_calc_and_readonly_trial(equipment_db, tmp_path):
    from genshin_app.agent_chat.candidate import CandidateSession
    from genshin_app.equipment.provisional_baseline import FIELD as BASELINE_FIELD
    detail = queries.get_weapon_detail(equipment_db, 11401)
    observed = queries.get_weapon_stats(equipment_db, 11401, 20, 1)
    base = next(row["value"] for row in observed["properties"] if row["prop_type"] == "FIGHT_PROP_BASE_ATTACK")
    source = plan()
    source["manual_template"]["characters"][0]["stats"]["energy_recharge"] = 2
    values = {"contract": LOADOUT_VALUES_CONTRACT, "extensions": {}, "weapon_type": detail["weapon"]["weapon_type"],
        "panel_basis": [{"level": 20, "ascension": 1, "base_atk": base + 1}],
        "passive_treatment": "included_in_panel", "passive_note": "fixture given panel includes passive"}
    source["provisional_entities"] = [_draft("local.weapon", detail["weapon"]["name"],
        authorization_kind="public_data_patch", values=values)]
    source["equipment_inputs"] = {"a": {"mode": "panel", "panel_policy": "static_included", "panel_basis_mode": "manual",
        "weapon": {"weapon_id": None, "level": 20, "ascension": 1, "selected_by": "agent", "refinements": [],
                   "entity_ref": {"kind": "provisional", "entity_type": "weapon", "provisional_id": "local.weapon"}},
        "artifact_sets": [], "source_refs": [{"kind": "manual", "note": "fixture given panel"}], "data_version": None}}
    current = ArtifactCandidateSession(uuid.uuid4().hex, tmp_path / "live-real-session", equipment_db, "native", source)
    current.verify_provisional_lookup(provisional_id="local.weapon", expected_revision=current.revision, database_id=11401, reason="real sqlite query")
    entity = current.candidate["provisional_entities"][0]
    assert entity["public_data_diff"]["before"]["panel_basis"][0]["base_atk"] == base == 145
    assert entity["public_data_diff"]["after"]["panel_basis"][0]["base_atk"] == 146
    raw = entity["database_lookup"]["observed_candidates"][0]
    assert "contract" not in raw and raw["weapon"] == detail["weapon"]
    evidence = raw[BASELINE_FIELD]["field_sources"]["/panel_basis/0/base_atk"]
    assert evidence["response"] == observed and evidence["query"]["level"] == 20
    proposed = current.request_provisional_public_patch_approval(provisional_id="local.weapon", expected_revision=current.revision, reason="exact correction")
    normalized = _normalized_question(proposed["question"])
    current.bind_provisional_approval_question(batch_id="real-sqlite-batch", call_id="real-question", items=[normalized])
    current.settle_provisional_approval_question(question={"question_id": "real-sqlite-batch", "answer_receipt": "real-sqlite-answer",
        "items": [normalized], "answer": {"items": [{"question_id": normalized["question_id"],
        "value_ids": [normalized["options"][0]["value_id"]], "text": None, "skipped": False}]}}, reason="Host-delivered user answer")
    current = CandidateSession.load(current.session_id, current.root, equipment_db)
    before = state(current)
    trials = trial_energy_recharge(current.candidate, equipment_db, request(), candidate_session=current)
    assert all(row["status"] == "tested" for row in trials["trials"]), trials
    assert state(current) == before and current.provisional_usage_receipt() is None
    formal = run_plan_calc(current.candidate, equipment_db, candidate_session=current)
    assert formal["result"]["provisional_entity_usage_receipt"]["entities"][0]["audit"]["values"]["panel_basis"][0]["base_atk"] == 146
    result_bytes(formal["result"], formal["result_receipt"], "json", candidate_session=current)


def test_current_public_host_can_use_readonly_trials_without_mutating_its_session(session):
    from tests.test_native_app_tool_host import call, host
    authorize_missing(session)
    before = state(session)
    owner = host(session.root.parent / "native-call-ledger", session,
                 lambda _s, tool, args: session.call_tool(tool, args))
    response = call(owner, tool="gs_diagnose_result", raw=json.dumps({"expected_revision": session.revision,
        "request": {"view": "energy_trials", **request()}}))
    assert response["terminal_state"] == "settled_success", response
    assert all(row["status"] == "tested" for row in response["result"]["data"]["trials"])
    assert state(session) == before and session.provisional_usage_receipt() is None


def test_readonly_trial_keeps_stale_attribute_review_unapproved(session, monkeypatch):
    source = copy.deepcopy(session.candidate)
    source["attribute_review"] = {"mode": "manual_context", "context_fingerprint": "a" * 64, "note": "old author classification"}
    session.replace_candidate(source, session.revision, "preserve explicit original attribute review")
    authorize_missing(session)
    before = state(session)
    monkeypatch.setattr("genshin_app.equipment.attributes.prepare_attribute_review",
        lambda *_a, **_k: pytest.fail("readonly trial silently reapproved attributes"))
    output = trial_energy_recharge(session.candidate, session.db_path, request(), candidate_session=session)
    assert all(row["status"] == "incomplete" for row in output["trials"])
    assert state(session) == before


def test_readonly_projection_rejects_other_database_and_closed_owner(session, tmp_path):
    authorize_missing(session)
    declaration = request()["trials"][0]
    trial = _apply_stats(session.candidate, declaration)
    context = prepare_readonly_projection(session, trial, declaration["stats"])
    with pytest.raises(PlanError) as failure:
        run_plan_calc(trial, tmp_path / "other.db", _readonly_provisional_context=context)
    assert failure.value.reason == "energy_trial_authorization_scope_changed"
    session.close()
    with pytest.raises(PlanError) as failure:
        run_plan_calc(trial, session.db_path, _readonly_provisional_context=context)
    assert failure.value.reason == "energy_trial_authorization_stale"


def test_advisory_marker_is_rejected_even_if_only_receipt_retains_it(session):
    authorize_missing(session)
    declaration = request()["trials"][0]
    trial = _apply_stats(session.candidate, declaration)
    context = prepare_readonly_projection(session, trial, declaration["stats"])
    calculated = run_plan_calc(trial, session.db_path, _readonly_provisional_context=context)
    calculated["result"].pop("advisory_public_fact_projection")
    assert calculation_completion(calculated)["task_admission"] != "accepted"


@pytest.mark.parametrize("value", [None, [], "invalid", 1])
def test_completion_keeps_its_nondictionary_input_contract(value):
    assert calculation_completion(value)["task_admission"] != "accepted"


def test_readonly_qualification_requires_unchanged_real_producer_result(session):
    from genshin_app.calculation_qualification import qualify_auxiliary_execution
    from genshin_app.equipment.files import fingerprint
    authorize_missing(session)
    declaration = request()["trials"][0]
    trial = _apply_stats(session.candidate, declaration)
    context = prepare_readonly_projection(session, trial, declaration["stats"])
    calculated = run_plan_calc(trial, session.db_path, _readonly_provisional_context=context)
    assert qualify_auxiliary_execution(calculated, energy_intent="multi_round_legal", readonly_context=context)["execution_passed"] is True
    assert qualification_for_claim(calculated, energy_intent="multi_round_legal", trusted_context=session)["can_accept"] is False
    independent = qualify_auxiliary_execution(calculated, energy_intent="multi_round_legal")
    assert independent["execution_passed"] is False
    assert "provisional_entity_usage_receipt_untrusted" in {row["code"] for row in independent["issues"]}
    tampered = copy.deepcopy(calculated)
    tampered["result"]["summary"]["total_damage"] += 999
    tampered["result_receipt"]["result_fingerprint"] = fingerprint(tampered["result"])
    with pytest.raises(PlanError) as failure:
        qualify_auxiliary_execution(tampered, energy_intent="multi_round_legal", readonly_context=context)
    assert failure.value.reason == "energy_trial_result_untrusted"


def test_readonly_qualification_does_not_hide_missing_numerics_or_source_failure(session):
    authorize_missing(session)
    declaration = request()["trials"][0]
    trial = _apply_stats(session.candidate, declaration)
    trial["manual_template"]["characters"][0]["stats"].pop("atk")
    # A non-stats deletion is not in the declared variation and is rejected
    # before execution rather than blessed as a healthy auxiliary projection.
    with pytest.raises(PlanError) as failure:
        prepare_readonly_projection(session, trial, declaration["stats"])
    assert failure.value.reason == "energy_trial_authorization_scope_changed"
    session.provisional_authority["attestation_ledger"].clear()
    output = trial_energy_recharge(session.candidate, session.db_path, request(), candidate_session=session, energy_intent="multi_round_legal")
    assert all(row["status"] == "incomplete" and "energy_qualification" not in row for row in output["trials"])
