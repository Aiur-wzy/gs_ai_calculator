# ruff: noqa: F811 - pytest fixtures imported by name.
from __future__ import annotations

from genshin_app.public_data_query import related_sources
from tests.test_character_appendix_navigation import db as navigation_db  # noqa: F401


def test_character_empty_annotations_does_not_hide_skill_and_subskill_appendix(navigation_db):
    value = {"version_line": "release", "snapshot": {"version": "fixture"}, "items": [
        {"entity_type": "character", "id": 1, "status": "ok", "data": {
            "character": {"id": 1}, "annotations": [],
            "selection": {"not_requested": ["talents"], "missing": []}}}]}
    result = related_sources(navigation_db, value)
    sources = result["items"][0]["related_sources"]
    targets = sources["appendix_index"]["targets"]
    skill = next(t for t in targets if t["target_type"] == "skill" and t["target_id"] == 10474)
    assert skill["categories"][0]["status"] == "pending"
    assert skill["categories"][0]["body_request"] == {
        "target_type": "skill", "target_id": 10474, "version_line": "release",
        "status": "pending", "category": "action_dispatch", "page": 1, "page_size": 10}
    assert sources["not_requested"] == ["talents"] and sources["missing"] == []
    assert sources["appendix_index"]["body_read"] is False
    assert value["items"][0]["data"]["annotations"] == []
