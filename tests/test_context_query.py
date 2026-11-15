"""RF04: projections, shared identity, snapshot consistency and real HTTP reads."""

# ruff: noqa: F811 - pytest resolves imported fixtures through function arguments.

import sqlite3
import threading

import pytest

from genshin_db import queries
from genshin_db.context_query import query_context, validate_context_request
from genshin_db.service import GenshinHTTPServer
from tests.test_equipment import equipment_db  # noqa: F401
from tests.test_equipment_http import request
from tests.test_pipeline import fake_repo, stub_fetcher  # noqa: F401
from tests.test_query_descriptions import description_db, legacy_db  # noqa: F401


@pytest.fixture
def context_db(description_db):
    with sqlite3.connect(description_db) as conn:
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY,value TEXT)")
        conn.execute("INSERT INTO meta VALUES ('upstream_commit','context-test')")
        conn.execute("INSERT INTO skill_param VALUES (100,'release',10,'[0,0,30,0]','[\"持续|{param3:I}\"]','{}','[\"stat_add\"]')")
    return description_db


def test_character_projection_preserves_full_selected_context_and_zeros(context_db):
    original = queries.get_character_detail(context_db, 1)
    selected = queries.get_character_detail(context_db, 1, sections=["skills"], levels=[10, 13])
    assert set(selected) == {"character", "skills", "annotations", "selection"}
    assert selected["character"] == original["character"]
    assert selected["annotations"] == original["annotations"]
    skill = selected["skills"][0]
    assert skill["desc_template"] == original["skills"][0]["desc_template"]
    assert skill["desc_source"] == original["skills"][0]["desc_source"]
    assert skill["params"] == {"10": original["skills"][0]["params"]["10"]}
    assert skill["params"]["10"]["param_list"] == [0, 0, 30, 0]
    assert selected["selection"]["missing"] == [{"path": "skills[0].params", "value": 13, "reason": "source_missing"}]
    assert "extra_info" in selected["selection"]["not_requested"]
    assert "1" in queries.get_character_detail(context_db, 1)["skills"][0]["params"]


def test_levels_do_not_remove_inherent_levels_or_shared_skill_owners(context_db):
    detail = queries.get_character_detail(context_db, 1, levels=[10])
    innate = next(e for e in detail["extra_info"] if e["info_type"] == "inherent_skill")
    assert "1" in innate["params"] and len(innate["description_variants"]) == 2
    skills = queries.get_skill(context_db, 10, levels=[10])["items"]
    assert {s["character_id"] for s in skills} == {1, 2}
    assert all(s["annotations"] and s["context_ref"]["character_id"] == s["character_id"] for s in skills)


def test_weapon_refinement_and_set_threshold_keep_effect_body_sources_and_order(equipment_db):
    weapon = queries.get_weapon_detail(equipment_db, 11401)
    selected = queries.get_weapon_detail(equipment_db, 11401, refinement=1)
    for before, after in zip(weapon["effects"], selected["effects"], strict=True):
        assert after["source_index"] == before["source_index"]
        assert after["refinements"] == [r for r in before["refinements"] if r["refinement"] == 1]
    assert selected["effects"][0]["refinements"][0]["param_list"] == [0.2, 0, 2, 0]
    missing_refinement = queries.get_weapon_detail(equipment_db, 11401, refinement=5)
    assert missing_refinement["selection"]["missing"][0]["value"] == 5
    original = queries.get_artifact_set_detail(equipment_db, 15)
    selected = queries.get_artifact_set_detail(equipment_db, 15, sections=["effects"], set_thresholds=[2])
    assert "piece_refs" not in selected
    assert selected["effects"] == [e for e in original["effects"] if e["required_count"] == 2]
    assert selected["artifact_set"] == original["artifact_set"]
    missing = queries.get_artifact_set_detail(equipment_db, 15, set_thresholds=[5])
    assert missing["effects"] == [] and missing["selection"]["missing"][0]["value"] == 5


@pytest.mark.parametrize("body", [
    {}, {"raw": "SELECT * FROM character"}, {"characters": [{"id": True}]},
    {"characters": [{"id": 1, "levels": []}]}, {"characters": [{"id": 1, "levels": [10, 10]}]},
    {"characters": [{"id": 1, "levels": [16]}]}, {"characters": [{"id": 1, "levels": [10.0]}]},
    {"characters": [{"id": 1, "sections": ["sql"]}]}, {"characters": [{"id": 1, "sections": None}]},
    {"characters": [{"id": 1, "refinement": 1}]}, {"weapons": [{"id": 1, "refinement": 6}]},
    {"artifact_sets": [{"id": 1, "set_thresholds": [0]}]}, {"characters": [{"id": 1}] * 5},
    {"version_line": "main", "characters": [{"id": 1}]},
])
def test_invalid_context_parameters_rejected_before_io(body):
    with pytest.raises(ValueError):
        validate_context_request(body)


def test_context_partial_errors_and_manual_content_deletion_invalidate(context_db):
    body = {"characters": [{"id": 1, "sections": ["skills"], "levels": [10]}, {"id": 999}], "weapons": [{"id": 1}]}
    before = query_context(context_db, body)
    assert [item["status"] for item in before["items"]] == ["ok", "error", "error"]
    assert before["items"][1]["error"]["reason"] == "not_found"
    assert before["items"][2]["error"]["reason"] == "dataset_unavailable"
    assert query_context(context_db, body) == before
    with sqlite3.connect(context_db) as conn:
        # Not the newest annotation: MAX(updated_at) would remain unchanged.
        conn.execute("DELETE FROM annotation WHERE id=1")
    after = query_context(context_db, body)
    assert after["snapshot"]["upstream_commit"] == before["snapshot"]["upstream_commit"]
    assert after["snapshot"]["manual_content_fingerprint"] != before["snapshot"]["manual_content_fingerprint"]
    assert after["items"][0]["content_fingerprint"] != before["items"][0]["content_fingerprint"]


def test_batch_uses_one_sqlite_snapshot_even_for_uncoordinated_wal_writer(context_db, monkeypatch):
    with sqlite3.connect(context_db) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
    query = queries.get_character_detail
    calls = []

    def modify_between_reads(*args, **kwargs):
        data = query(*args, **kwargs)
        calls.append(args[1])
        if len(calls) == 1:
            with sqlite3.connect(context_db) as conn:
                conn.execute("UPDATE annotation SET content='changed by independent writer' WHERE id=1")
        return data

    monkeypatch.setattr(queries, "get_character_detail", modify_between_reads)
    response = query_context(context_db, {"characters": [{"id": 1}, {"id": 2}]})
    notes = [next(a for a in item["data"]["annotations"] if a["id"] == 1) for item in response["items"]]
    assert notes[0]["content"] == notes[1]["content"] == "共享机制"
    assert queries.get_meta(context_db)["manual_content_fingerprint"] != response["snapshot"]["manual_content_fingerprint"]


def test_context_http_read_needs_no_token_and_projection_matches_shared(context_db):
    srv = GenshinHTTPServer(context_db.parent, port=0)
    worker = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    worker.start()
    try:
        body = {"characters": [{"id": 1, "sections": ["skills"], "levels": [10]}]}
        status, result, _ = request(srv, "/v1/context/query", method="POST", body=body)
        assert status == 200 and result["data"] == query_context(context_db, body)
        status, detail, _ = request(srv, "/v1/characters/1?sections=skills&levels=10")
        assert status == 200 and detail["data"] == result["data"]["items"][0]["data"]
        assert request(srv, "/v1/context/query")[0] == 405
        assert request(srv, "/v1/context/query?raw=1", method="POST", body=body)[0] == 400
        assert request(srv, "/v1/characters/1?sections=typo")[0] == 400
        assert request(srv, "/v1/skills/10?levels=10,10")[0] == 400
        assert request(srv, "/v1/annotations", method="POST", body={})[0] == 401
    finally:
        srv.shutdown()
        worker.join(timeout=3)
        srv.server_close()
