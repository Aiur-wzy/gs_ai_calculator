from genshin_app.domain_errors import classify_domain_failure, tool_failure_guidance
from genshin_app.failure_analysis import calculation_feedback, failure_outline


def test_unknown_unsupported_word_is_not_proof_of_missing_engine_capability():
    assert classify_domain_failure("unsupported_effect")["category"] == "request_error"
    assert classify_domain_failure("some_unsupported_future_input")["category"] == "request_error"
    assert classify_domain_failure("unsupported_target_domain")["category"] == "engine_capability_missing"


def test_unknown_legacy_code_does_not_invent_a_game_data_repair():
    classification = classify_domain_failure("future_unspecified_failure")
    assert classification["classification_confidence"] == "unknown"
    assert tool_failure_guidance("future_unspecified_failure")["recovery"]["action"] == "inspect_error_details"


def test_declared_duplicate_panel_is_request_error_not_engine_gap():
    guidance = tool_failure_guidance("missing_input", details={"missing_inputs": [
        {"code": "unsupported_effect", "category": "request_error", "path": "part.one",
         "repair_hint": "保留条件收益并用Buff交付"}]})
    assert classify_domain_failure("missing_input", details={"missing_inputs": [
        {"code": "unsupported_effect", "category": "request_error"}]})["category"] == "request_error"
    assert guidance["failure_summary"]["reported_issue_count"] == 1


def test_actual_first_rejection_and_later_unverified_are_separate():
    details = {"issues": [{"code": "cooldown_unavailable", "event_id": "fourth-e", "t": 16.5},
                          {"code": "another_unlinked_issue", "path": "source"}],
        "rotation_check": {"event_log": [{"id": "first-e", "status": "executed", "t": 0},
            {"id": "fourth-e", "run_id": "0:fourth-e", "status": "failed", "type": "action", "t": 16.5,
             "charges_before": 0, "next_ready_at": 17.0},
            {"id": "later-q", "status": "not_verified", "type": "action", "t": 18}]}}
    outline = failure_outline(details)
    assert outline["first_rejected_event"]["id"] == "fourth-e"
    assert outline["first_rejected_event"]["charges_before"] == 0
    assert outline["first_rejected_event"]["next_ready_at"] == 17.0
    assert outline["primary_reported_issue"]["code"] == "cooldown_unavailable"
    assert outline["subsequent_unverified_event_count"] == 1
    assert outline["additional_reported_issues"][0]["code"] == "another_unlinked_issue"
    assert "独立性未知" in outline["dependency_interpretation"]


def test_saved_energy_failure_reports_verified_prefix_and_unrun_dynamic_input():
    rejected = {"id": "r2-q", "action_id": "r2-q", "type": "action", "status": "failed",
                "t": 28.0, "before": 28.8, "required": 60, "deficit": 31.2}
    calculated = {"plan": {"manual_template": {"hits": [{}] * 116},
                           "mechanics_model": {"elemental_model": {"schema_version": 4}}},
                  "report": {"state": "ready"},
                  "completion": {"calculation_status": "diagnostic", "complete_input_window": False},
                  "result": {"hits": [{"t": 27.7}] * 86, "rotation_check": {"event_log": [rejected]}}}
    feedback = calculation_feedback(calculated)
    assert (feedback["reported_hits"], feedback["declared_manual_hits"], feedback["last_reported_hit_t"]) == (86, 116, 27.7)
    assert feedback["composition"] == "ready" and feedback["execution"] == "diagnostic"
    assert feedback["elemental"] == "declared_without_trace"
    assert feedback["failure_summary"]["first_rejected_event"] == rejected


def test_legal_static_execution_and_saved_dynamic_trace_are_separate():
    calculated = {"completion": {"calculation_status": "complete", "complete_input_window": True}, "result": {}}
    feedback = calculation_feedback(calculated)
    assert feedback["elemental"] == "not_declared" and feedback["reported_hits"] == 0
    assert "failure_summary" not in feedback
    calculated["result"]["elemental_trace"] = [{"type": "test"}]
    assert calculation_feedback(calculated)["elemental"] == "trace_saved"
    calculated["result"]["elemental"] = {"trace": [{"type": "one"}, {"type": "two"}]}
    calculated["result"]["special_reactions"] = {"trace": []}
    feedback = calculation_feedback(calculated)
    assert feedback["trace_event_counts"] == {"elemental": 2, "special_reactions": 0}
    assert feedback["special_reactions"] == "trace_empty"


def test_database_contention_reports_wait_instead_of_input_repair():
    reason = "database_migration_busy"
    classification = classify_domain_failure(reason, operation="gs_query_appendix")
    assert classification["category"] == "database_busy"
    guidance = tool_failure_guidance(reason, operation="cli.db.appendix")
    assert guidance["contract_ref"] == {"resource": "cli_capabilities", "command": "db appendix"}
    assert guidance["recovery"]["action"] == "wait_for_database_access"


def test_uncertain_side_effect_directs_readback_instead_of_resubmission():
    reason = "cli_operation_unknown"
    classification = classify_domain_failure(reason, operation="cli.agent.candidate.edit")
    assert classification["category"] == "outcome_unknown"
    assert tool_failure_guidance(reason)["recovery"] == {
        "action": "check_original_operation", "retry_same_request": False,
    }


def test_special_reaction_dependency_points_at_live_elemental_capabilities():
    error = {"issues": [{"code": "special_reaction_ruleset", "path": "mechanics_model.elemental_model.ruleset"}]}
    recovery = tool_failure_guidance("mechanics_pending", details=error)["recovery"]
    assert recovery == {"action": "complete_elemental_dependency", "resource": "mechanics_capabilities",
                        "pointer": "/elemental", "retry_same_request": False}
