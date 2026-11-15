# ruff: noqa: F811 - pytest fixture injection uses imported names.
"""Bounded model discovery over synthetic SQLite only.

The catalog is deliberately weaker than the fresh capacity profile.  These
tests protect that separation while exercising the public runtime route.
"""
from __future__ import annotations

import copy
import sqlite3
from contextlib import closing

import pytest

from genshin_db import runtime_action_capacity as capacity
from genshin_db.runtime_catalog import RuntimeConfigError
from genshin_db.runtime_queries import query_runtime
from genshin_db.semantic_store import read_record, review_record
from tests.action_resource_fixtures import (
    action_resource_synthetic_db_only,  # noqa: F401
)
from tests.test_action_resource_sources import install, source_db  # noqa: F401
from tests.test_capacity_model import ready  # noqa: F401
from tests.test_pipeline import fake_repo  # noqa: F401

pytestmark = pytest.mark.usefixtures("action_resource_synthetic_db_only")


QUERY_CONTRACT = "action-capacity-model-catalog-query-v1"


def request(model, *, page_size=16, cursor=None):
    claim = model["payload"]["claim"]
    domain = claim["domain"]
    return {
        "contract": QUERY_CONTRACT,
        "version_line": "release",
        "character_id": claim["target"]["character_id"],
        "observed": {
            "target": {
                "skill_depot_id": claim["target"]["skill_depot_id"],
                "skill_id": claim["target"]["skill_id"],
                "variant": domain["variant"],
            },
            "progression": {
                "character_level": domain["character_level"],
                "ascension": domain["ascension"],
                "constellation": 0,
                "base_level": domain["base_level"],
            },
            "weapon": copy.deepcopy(domain["weapon"]),
            "artifacts": domain["artifacts"],
            "team": domain["team"],
            "context": copy.deepcopy(domain["context"]),
        },
        "unknown_paths": [],
        "view": "all",
        "page_size": page_size,
        "cursor": cursor,
    }


def clone_model(db, model, ident, *, status="approved"):
    other = copy.deepcopy(model)
    other["id"] = other["payload"]["id"] = ident
    other["review"] = {
        "status": status,
        "reviewer": "catalog-fixture" if status in ("approved", "revoked") else None,
        "reviewed_at": "2026-10-13T00:00:00+00:00" if status in ("approved", "revoked") else None,
    }
    assert install(db, other)["applied"]
    return other


def catalog(db, req):
    return query_runtime(db, {"action": "action_capacity_model_candidates", "request": req})


def test_catalog_runtime_route_accepts_no_common_fields(ready):
    result = catalog(ready[0], request(ready[1]))
    assert result["contract"] == "action-capacity-model-catalog-page-v1"


def test_catalog_runtime_route_accepts_matching_common_fields(ready):
    db, model, _ = ready
    first = catalog(db, request(model))
    result = query_runtime(db, {
        "action": "action_capacity_model_candidates",
        "request": request(model),
        "version_line": "release",
        "snapshot_id": first["runtime_snapshot_id"],
    })
    assert result["runtime_snapshot_id"] == first["runtime_snapshot_id"]


def test_catalog_runtime_route_rejects_conflicting_version_line(ready):
    db, model, _ = ready
    with pytest.raises(ValueError) as exc:
        query_runtime(db, {
            "action": "action_capacity_model_candidates",
            "request": request(model),
            "version_line": "beta",
        })
    assert getattr(exc.value, "code", None) == "capacity_catalog_query"


def test_catalog_runtime_route_uses_existing_snapshot_changed_contract(ready):
    db, model, _ = ready
    with pytest.raises(RuntimeConfigError) as exc:
        query_runtime(db, {
            "action": "action_capacity_model_candidates",
            "request": request(model),
            "snapshot_id": "0" * 64,
        })
    assert exc.value.reason == "snapshot_changed" and exc.value.status == 409


def test_catalog_pages_models_in_stable_order_without_capacity_values(ready):
    db, model, _ = ready
    clone_model(db, model, "fixture.capacity.a")
    clone_model(db, model, "fixture.capacity.z")
    first = catalog(db, request(model, page_size=2))
    assert first["execution_authority"] is False and first["has_more"] is True
    assert [x["model_ref"]["id"] for x in first["items"]] == ["fixture.capacity.a", "fixture.capacity.model"]
    second_request = request(model, page_size=2, cursor=first["next_cursor"])
    second = catalog(db, second_request)
    assert [x["model_ref"]["id"] for x in second["items"]] == ["fixture.capacity.z"]
    assert second["capacity_inventory_fingerprint"] == first["capacity_inventory_fingerprint"]
    forbidden = {"capacity", "reviewed_cases", "materials", "text_inventory", "base"}
    stack = [first, second]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            assert not (set(value) & forbidden)
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)


def test_catalog_is_non_materializing_and_only_compares_declared_domain(ready, monkeypatch):
    db, model, _ = ready
    monkeypatch.setattr(capacity, "fresh_model_material", lambda *a, **k: pytest.fail("materialized"))
    monkeypatch.setattr(capacity, "selection_matches", lambda *a, **k: pytest.fail("claimed applicability"))
    result = capacity.list_action_capacity_model_candidates(db, request(model))
    item = result["items"][0]
    assert item["declared_filter"]["status"] == "exact_match"
    assert item["material_status"] == item["applicability"] == "not_evaluated"
    assert item["execution_authority"] is False


def test_catalog_uses_one_active_transaction_for_inventory_records_reviews_and_names(ready, monkeypatch):
    db, model, _ = ready
    seen = []
    for name in ("current_capacity_refs", "read_record", "read_current_reviewed_record", "_display_name"):
        original = getattr(capacity, name)

        def checked(conn, *args, _original=original, _name=name, **kwargs):
            assert conn.in_transaction
            seen.append((_name, id(conn)))
            return _original(conn, *args, **kwargs)

        monkeypatch.setattr(capacity, name, checked)
    capacity.list_action_capacity_model_candidates(db, request(model))
    assert {connection for _, connection in seen} and len({connection for _, connection in seen}) == 1
    assert {name for name, _ in seen} == {
        "current_capacity_refs", "read_record", "read_current_reviewed_record", "_display_name"
    }


def test_catalog_pagination_cannot_bypass_target_capacity_fact_limit(ready):
    db, model, _ = ready
    # ready contains one model and one delta; 31 additional current models
    # cross the existing complete target-inventory limit of 32.
    for index in range(31):
        clone_model(db, model, f"fixture.capacity.limit.{index:02d}")
    with pytest.raises(ValueError) as exc:
        catalog(db, request(model, page_size=1))
    assert getattr(exc.value, "code", None) == "capacity_budget"


def test_catalog_typed_exact_partial_mismatch_and_zero(ready):
    db, model, _ = ready
    exact = request(model)
    assert catalog(db, exact)["items"][0]["declared_filter"]["status"] == "exact_match"
    partial = request(model)
    del partial["observed"]["weapon"]["refinement"]
    partial["unknown_paths"] = ["weapon.refinement"]
    item = catalog(db, partial)["items"][0]
    assert item["declared_filter"]["status"] == "partial_match"
    assert item["declared_filter"]["unknown_input_paths"] == ["weapon.refinement"]
    mismatch = request(model)
    mismatch["observed"]["weapon"]["id"] += 1
    item = catalog(db, mismatch)["items"][0]
    assert item["declared_filter"]["status"] == "mismatch"
    assert item["declared_filter"]["mismatched"] == [{
        "path": "weapon.id",
        "expected": model["payload"]["claim"]["domain"]["weapon"]["id"],
        "actual": mismatch["observed"]["weapon"]["id"],
    }]
    assert exact["observed"]["progression"]["constellation"] == 0


def test_pending_and_revoked_models_are_visible_but_not_selectable(ready):
    db, model, _ = ready
    clone_model(db, model, "fixture.capacity.pending", status="pending")
    clone_model(db, model, "fixture.capacity.revoked", status="revoked")
    items = {x["model_ref"]["id"]: x for x in catalog(db, request(model))["items"]}
    assert items["fixture.capacity.model"]["selection_status"] == "reference_selectable_for_fresh_preview"
    assert items["fixture.capacity.pending"]["selection_status"] == "review_not_approved"
    assert items["fixture.capacity.revoked"]["selection_status"] == "review_not_approved"


def test_catalog_cursor_binds_semantic_review_and_query(ready):
    db, model, _ = ready
    clone_model(db, model, "fixture.capacity.other")
    req = request(model, page_size=1)
    first = catalog(db, req)
    with closing(sqlite3.connect(db)) as conn:
        stored = read_record(conn, "release", "fact", model["id"], model["revision"])
    review_record(
        db,
        version_line="release",
        record_kind="fact",
        record_id=model["id"],
        revision=model["revision"],
        status="revoked",
        reviewer="catalog-revoker",
        reviewed_at="2026-10-13T01:00:00+00:00",
        expected_review_fingerprint=stored["review_fingerprint"],
    )
    changed = request(model, page_size=1, cursor=first["next_cursor"])
    with pytest.raises(ValueError) as exc:
        catalog(db, changed)
    assert getattr(exc.value, "code", None) == "capacity_catalog_changed"


def test_catalog_cursor_is_opaque_and_binds_page_size(ready):
    db, model, _ = ready
    clone_model(db, model, "fixture.capacity.other")
    first = catalog(db, request(model, page_size=1))
    wrong_size = request(model, page_size=2, cursor=first["next_cursor"])
    with pytest.raises(ValueError) as exc:
        catalog(db, wrong_size)
    assert getattr(exc.value, "code", None) == "capacity_catalog_cursor"
    corrupt = request(model, page_size=1, cursor=first["next_cursor"][:-1] + "x")
    with pytest.raises(ValueError) as exc:
        catalog(db, corrupt)
    assert getattr(exc.value, "code", None) == "capacity_catalog_cursor"


def test_catalog_chinese_names_are_typed_and_missing_never_falls_back_to_id(ready):
    db, model, _ = ready
    claim = model["payload"]["claim"]
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("UPDATE character SET name=? WHERE version_line=? AND id=?", ("测试角色", "release", claim["target"]["character_id"]))
        conn.execute("UPDATE skill SET name=? WHERE version_line=? AND character_id=? AND id=?", ("测试战技", "release", claim["target"]["character_id"], claim["target"]["skill_id"]))
        conn.execute("UPDATE weapon SET name=? WHERE version_line=? AND id=?", ("测试武器", "release", claim["domain"]["weapon"]["id"]))
    display = catalog(db, request(model))["items"][0]["display"]
    assert display == {
        "character": {"value": "测试角色", "status": "present"},
        "skill": {"value": "测试战技", "status": "present"},
        "weapon": {"value": "测试武器", "status": "present"},
    }
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("DELETE FROM skill WHERE version_line=? AND character_id=? AND id=?", ("release", claim["target"]["character_id"], claim["target"]["skill_id"]))
    missing = catalog(db, request(model))["items"][0]["display"]["skill"]
    assert missing == {"value": None, "status": "missing"}


@pytest.mark.parametrize("bad", [True, 0, 33, "16"])
def test_catalog_page_size_is_exact_bounded_integer(ready, bad):
    req = request(ready[1])
    req["page_size"] = bad
    with pytest.raises(ValueError) as exc:
        catalog(ready[0], req)
    assert getattr(exc.value, "code", None) == "capacity_catalog_query"


def test_catalog_unknown_paths_must_describe_exact_missing_typed_fields(ready):
    req = request(ready[1])
    del req["observed"]["weapon"]["refinement"]
    with pytest.raises(ValueError) as exc:
        catalog(ready[0], req)
    assert getattr(exc.value, "code", None) == "capacity_catalog_query"


@pytest.mark.parametrize("unknown", [[[]], [{}], ["weapon.refinement", []]])
def test_catalog_unhashable_unknown_paths_are_typed_query_errors(ready, unknown):
    req = request(ready[1])
    req["unknown_paths"] = unknown
    with pytest.raises(ValueError) as exc:
        catalog(ready[0], req)
    assert getattr(exc.value, "code", None) == "capacity_catalog_query"
