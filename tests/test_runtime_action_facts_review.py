"""Independent action-facts receipt and unresolved-input counterexamples."""

from __future__ import annotations

import json

import pytest

from genshin_db.equipment_queries import borrow_read_connection, equipment_read_session
from genshin_db.runtime_catalog import RuntimeConfigError, resolve_pointer
from genshin_db.runtime_queries import query_runtime
from tests import fixture_candidate_builder as fixture_builder
from tests.test_pipeline import fake_repo, stub_fetcher  # noqa: F401
from tests.test_runtime_action_facts import action_db  # noqa: F401

# ruff: noqa: F811


def _facts(db, **fields):
    return query_runtime(db, {"action": "action_facts", "character_id": 90000001,
                              "slot": "normal", **fields})


def _change_talent(fake_repo, change):
    path = fake_repo / "BinOutput/Talent/EquipTalents/Fixture.json"
    raw = json.loads(path.read_text("utf8"))
    change(raw["Fixture_Effect"])
    path.write_text(json.dumps(raw), "utf8")


def test_unresolved_parameter_index_cannot_report_ready_facts(action_db, fake_repo):
    _change_talent(fake_repo, lambda operations: operations[1].update(paramDelta="%99"))
    fixture_builder.build_database(action_db.parent, force=True)
    result = _facts(action_db)
    assert result["parameter_bindings"][0]["parameter_input_status"] == "pending"
    assert result["status"] == "pending"


def test_missing_non_modify_talent_reference_cannot_report_ready_facts(action_db, fake_repo):
    _change_talent(fake_repo, lambda operations: operations[0].update(abilityName="Missing_Ability"))
    fixture_builder.build_database(action_db.parent, force=True)
    result = _facts(action_db)
    assert any(ref["resolution_status"] == "missing" for ref in result["talents"][0]["references"])
    assert result["status"] == "pending"


def test_record_and_binding_provenance_resolve_in_the_stored_raw_sources(action_db, fake_repo):
    result = _facts(action_db)
    for record in result["records"].values():
        raw = json.loads((fake_repo / record["source_path"]).read_text("utf8"))
        for name, pointer in record["field_pointers"].items():
            assert resolve_pointer(raw, pointer) == record["fields"][name]
    for binding in result["parameter_bindings"]:
        origin = binding["parameter_input_origin"]
        raw = json.loads((fake_repo / origin["source_path"]).read_text("utf8"))
        assert resolve_pointer(raw, origin["json_pointer"]) == binding["parameter_input"]
        operations = json.loads((fake_repo / binding["source_path"]).read_text("utf8"))
        assert resolve_pointer(operations, binding["operation_pointer"])["paramDelta"] == binding["raw_expression"]


def test_all_exact_lookups_reuse_the_callers_read_snapshot(action_db, monkeypatch):
    from genshin_db import runtime_queries

    observed = []
    original = runtime_queries._lookup

    def observe(conn, *args, **kwargs):
        observed.append(conn.connection)
        assert conn.in_transaction
        return original(conn, *args, **kwargs)

    monkeypatch.setattr(runtime_queries, "_lookup", observe)
    with equipment_read_session(action_db):
        expected = borrow_read_connection(action_db).connection
        result = _facts(action_db)
        repeated = _facts(action_db, snapshot_id=result["snapshot"]["snapshot_id"])
        assert repeated["content_fingerprint"] == result["content_fingerprint"]
        assert observed and all(conn is expected for conn in observed)


@pytest.mark.parametrize("limit_name", ["max_operations", "max_abilities"])
def test_operation_and_ability_limits_fail_closed(action_db, monkeypatch, limit_name):
    from genshin_db.runtime_action_facts import ACTION_FACT_LIMITS

    monkeypatch.setitem(ACTION_FACT_LIMITS, limit_name, 0)
    with pytest.raises(RuntimeConfigError) as exc:
        _facts(action_db)
    assert exc.value.reason == "response_too_large"
