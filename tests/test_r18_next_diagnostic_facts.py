"""Real producer -> collector -> failure outline -> native context preservation."""
import copy

import pytest

from genshin_app.agent_chat.feedback_context import feedback_context
from genshin_app.equipment.compose_diagnostics import ComposeIssueCollector
from genshin_app.failure_analysis import failure_outline
from genshin_engine import special_reaction_capabilities as capabilities


def test_exact_facts_survive_all_projection_boundaries_without_aliasing():
    facts = {"actual": {"fingerprint": "old"}, "expected": {"fingerprint": "current"},
             "input_ref": "/inputs/0/source", "conflicts": ["first", "second"],
             "checked_scope": "binding", "not_checked": ["damage"],
             "recovery": {"action": "inspect_selected_character_identity", "retry_same_request": False}}
    issues = []
    ComposeIssueCollector(issues)("binding_mismatch", "inputs.0", "来源不同", diagnostics=facts)
    expected = copy.deepcopy(facts)
    facts["actual"]["fingerprint"] = "mutated"
    outline = failure_outline({"missing_inputs": issues})
    native = feedback_context({"latest_attempt": {"failure_summary": outline}})["latest_attempt"]["problem"]
    for key, value in expected.items():
        assert native[key] == value
    assert native["detail_ref"] == {"container": "details", "pointer": "/missing_inputs/0"}


def test_compaction_is_explicit_and_keeps_exact_full_detail_location():
    issue = {"code": "many_conflicts", "conflicts": list(range(20))}
    primary = failure_outline({"details": {"issues": [issue]}})["primary_reported_issue"]
    assert primary["details_truncated"] is True
    assert primary["detail_ref"]["pointer"] == "/details/issues/0"
    assert len(issue["conflicts"]) == 20


@pytest.fixture
def catalog():
    data = capabilities.load_reviewed_special_reaction_capabilities()
    with capabilities.use_special_reaction_capability_catalog(data):
        yield data


def equipped(catalog):
    entry = next(row for row in catalog["entries"]
                 if row["form_selection_required"] and row["runtime_support_status"] == "supported")
    return {"actor": {"character_ref": {"db_character_id": entry["character_id"],
                "skill_depot_id": entry["required_skill_depot_id"], "version_line": catalog["version_line"]},
            "data_version": {"version_line": catalog["version_line"],
                             "upstream_commit": catalog["source_upstream_commit"]}}}


def test_missing_identity_is_input_problem_not_database_failure(catalog):
    result = capabilities.project_special_reaction_party_capabilities_detailed({}, actor_ids=["actor"])
    issue = result["diagnostics"][0]
    assert result["rows"] == []
    assert issue["code"] == "special_capability_identity_missing"
    assert issue["category"] == "request_error"
    assert issue["input_ref"] == "/equipment_inputs/actor/character_ref"


@pytest.mark.parametrize("change,reason", [
    (lambda x: x["character_ref"].update(db_character_id=999), "special_capability_catalog_entry_missing"),
    (lambda x: x["character_ref"].update(skill_depot_id=999), "special_capability_skill_depot_mismatch"),
    (lambda x: x.pop("data_version"), "equipment_data_version_missing"),
    (lambda x: x["data_version"].update(upstream_commit="b" * 40), "equipment_upstream_commit_mismatch"),
])
def test_different_rejections_have_precise_evidence(catalog, change, reason):
    value = equipped(catalog)
    change(value["actor"])
    result = capabilities.project_special_reaction_party_capabilities_detailed(value)
    assert result["diagnostics"][0]["code"] == reason
    assert all(row["status"] != "confirmed" for row in result["rows"])
    assert capabilities.project_special_reaction_party_capabilities(value) == result["rows"]


def test_valid_projection_keeps_old_api_rows(catalog):
    equipment = equipped(catalog)
    result = capabilities.project_special_reaction_party_capabilities_detailed(equipment)
    assert result["diagnostics"] == []
    assert result["rows"][0]["status"] == "confirmed"
    assert capabilities.project_special_reaction_party_capabilities(equipment) == result["rows"]
