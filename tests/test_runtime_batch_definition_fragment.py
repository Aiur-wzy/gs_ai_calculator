"""Raw definitions and fragments use exactly the same snapshot and bytes in batches."""
# ruff: noqa: F811 - imported pytest fixtures are intentionally injected.
import sqlite3

import pytest

from genshin_db.runtime_catalog import RuntimeConfigError
from genshin_db.runtime_queries import query_runtime
from tests.test_runtime_action_facts import (  # noqa: F401
    action_db,
    fake_repo,
    stub_fetcher,
)


def _queries(database):
    with sqlite3.connect(database) as conn:
        identifier = conn.execute("SELECT definition_id FROM runtime_definition ORDER BY definition_id LIMIT 1").fetchone()[0]
    return [{"action": "definition", "definition_id": identifier, "sections": ["summary", "references", "payload"]},
            {"action": "fragment", "definition_id": identifier, "relative_pointer": "", "max_bytes": 65536}]


def test_definition_and_fragment_batch_are_exact_single_reads(action_db):
    queries = _queries(action_db)
    single = [query_runtime(action_db, query) for query in queries]
    result = query_runtime(action_db, {"action": "batch", "snapshot_id": single[0]["snapshot"]["snapshot_id"],
        "items": [{"id": str(index), "request": query} for index, query in enumerate(queries)]})
    assert result["status"] == "complete"
    assert [row["data"] for row in result["items"]] == single
    assert all(row["data"]["snapshot"] == result["snapshot"] for row in result["items"])
    assert result["items"][1]["data"]["payload_json"] == single[1]["payload_json"]


def test_fragment_missing_or_invalid_is_item_error_and_later_read_continues(action_db):
    queries = _queries(action_db)
    result = query_runtime(action_db, {"action": "batch", "items": [
        {"id": "bad-pointer", "request": {**queries[1], "relative_pointer": "/not-a-real-field"}},
        {"id": "bad-limit", "request": {**queries[1], "max_bytes": True}},
        {"id": "good", "request": queries[0]}]})
    assert [row["status"] for row in result["items"]] == ["error", "error", "ok"]
    assert result["items"][0]["error"]["reason"] == "not_found"
    assert result["items"][1]["error"]["details"]["path"] == "/max_bytes"


def test_fragment_source_corruption_is_not_downgraded_to_item_error(action_db, monkeypatch):
    from genshin_db import runtime_queries
    queries = _queries(action_db)
    def fail(*_args, **_kwargs):
        raise RuntimeConfigError("source identity changed", "dataset_invalid", 500)
    monkeypatch.setattr(runtime_queries, "get_runtime_fragment", fail)
    with pytest.raises(RuntimeConfigError) as caught:
        query_runtime(action_db, {"action": "batch", "items": [{"id": "raw", "request": queries[1]}]})
    assert caught.value.reason == "dataset_invalid"


@pytest.mark.parametrize("action", ["action_facts", "action_resource_profile", "talent_level_profile",
    "attack_metadata_profile", "periodic_attack_profile", "limited_hit_benefit_profile"])
@pytest.mark.parametrize("bad_selection", [{"skill_id": 777}, {"slot": "normal", "skill_depot_id": 42}])
def test_exact_request_membership_failure_is_typed_not_a_program_fault(action_db, action, bad_selection):
    from genshin_app.app_system_fault import is_domain_exception
    from genshin_db.validation import QueryValidationError
    request = {"action": action, "character_id": 90000001, **bad_selection}
    if action == "limited_hit_benefit_profile":
        request["param_index"] = 0
    with pytest.raises(QueryValidationError) as caught:
        query_runtime(action_db, request)
    assert is_domain_exception(caught.value)
    assert caught.value.reason == "invalid_query_request"


def test_input_classification_does_not_turn_source_corruption_into_request_error(action_db, monkeypatch):
    from genshin_db.runtime_queries import _Nodes
    def fail(*_args, **_kwargs):
        raise RuntimeConfigError("source hash mismatch", "dataset_invalid", 500)
    monkeypatch.setattr(_Nodes, "node", fail)
    with pytest.raises(RuntimeConfigError) as caught:
        query_runtime(action_db, {"action": "action_resource_profile", "character_id": 90000001, "slot": "normal"})
    assert caught.value.reason == "dataset_invalid" and caught.value.status == 500
