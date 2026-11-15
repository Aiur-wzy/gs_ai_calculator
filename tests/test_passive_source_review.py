"""Independent EC12 query counterexamples; isolated source data, no live DB."""
from __future__ import annotations

import copy
import json
import sqlite3

import pytest
from test_runtime_passive_source import fake_repo as fake_repo  # noqa: PLC0414
from test_runtime_passive_source import passive_db as passive_db  # noqa: PLC0414
from test_runtime_passive_source import stub_fetcher as stub_fetcher  # noqa: PLC0414

from genshin_db import runtime_queries
from genshin_db.equipment_queries import equipment_read_session
from genshin_db.runtime_catalog import RuntimeConfigError
from genshin_db.runtime_passive_source import (
    get_passive_source_context,
    project_passive_source,
)
from genshin_runtime.passive_source_identity import (
    passive_source_fingerprint,
    passive_unlock_fingerprint,
)
from tests import fixture_candidate_builder as fixture_builder


def query(db, **changes):
    return runtime_queries.query_runtime(db, {"action": "passive_source_profile", "character_id": 90000001,
        "proud_skill_id": 77101, "character_level": 90, "ascension": 6, **changes})


def rewrite(repo, table, update):
    path = repo / "ExcelBinOutput" / (table + "ExcelConfigData.json")
    data = json.loads(path.read_text("utf8"))
    update(data)
    path.write_text(json.dumps(data, ensure_ascii=False), "utf8")


def rebuild(db):
    fixture_builder.build_database(db.parent, force=True)


def test_review_explicit_proud_level_is_not_automatically_first_or_maximum(passive_db, fake_repo):
    def extra(rows):
        one = copy.deepcopy(next(x for x in rows if x.get("proudSkillId") == 77101))
        one.update(proudSkillId=77107, level=7, paramList=[.9, 17])
        rows.insert(0, one)
    rewrite(fake_repo, "ProudSkill", extra)
    rebuild(passive_db)
    chosen = query(passive_db, proud_skill_id=77107)
    original = query(passive_db)
    assert chosen["selection"]["proud_level"] == 7
    assert chosen["source_bundle"]["param_list"] == [.9, 17]
    assert original["selection"]["proud_level"] == 1
    assert original["source_bundle"]["param_list"] == [.0004, 8]
    assert chosen["execution_support"] == "not_evaluated"


def test_review_repeated_opening_is_ambiguous_even_when_payloads_are_identical(passive_db, fake_repo):
    rewrite(fake_repo, "AvatarSkillDepot", lambda rows: rows[0]["inherentProudSkillOpens"].append(
        copy.deepcopy(rows[0]["inherentProudSkillOpens"][0])))
    rebuild(passive_db)
    with pytest.raises(RuntimeConfigError) as exc:
        query(passive_db)
    assert exc.value.reason == "ambiguous_reference"


def test_review_secondary_depot_never_borrows_primary_inherent_ownership(passive_db, fake_repo):
    rewrite(fake_repo, "Avatar", lambda rows: rows[0].update(candSkillDepotIds=[9002]))
    def alternate(rows):
        other = copy.deepcopy(rows[0])
        other.update(id=9002, inherentProudSkillOpens=[{"proudSkillGroupId": 772}])
        rows.append(other)
    rewrite(fake_repo, "AvatarSkillDepot", alternate)
    rebuild(passive_db)
    with pytest.raises(RuntimeConfigError) as exc:
        query(passive_db)
    assert exc.value.reason == "skill_depot_selection_required"
    with pytest.raises(ValueError):
        query(passive_db, skill_depot_id=9002)
    assert query(passive_db, skill_depot_id=9001)["source_status"] == "ready"


def test_review_unselected_unknown_opening_does_not_destroy_selected_complete_source(passive_db, fake_repo):
    rewrite(fake_repo, "AvatarSkillDepot", lambda rows: rows[0]["inherentProudSkillOpens"].extend([
        {"proudSkillGroupId": "unresolved"}, {"proudSkillGroupId": 999001}]))
    rebuild(passive_db)
    out = query(passive_db)
    assert out["source_status"] == "ready"
    assert out["unlock"]["status"] == "eligible_under_declared_progression"
    assert [r["state"] for r in out["opening_inventory"]][-2:] == ["pending", "pending"]
    assert all(not i["blocks_source"] for i in out["issues"] if i["scope"] == "other_candidate")


def test_review_passive_query_never_asks_for_combat_talent_level_or_constellation(passive_db, monkeypatch):
    from genshin_db import runtime_action_facts, runtime_action_parameter
    monkeypatch.setattr(runtime_action_facts, "get_runtime_action_facts", lambda *a, **k: pytest.fail("unrelated combat query"))
    monkeypatch.setattr(runtime_action_parameter, "get_action_parameter_context", lambda *a, **k: pytest.fail("unrelated combat parameter"))
    out = query(passive_db)
    assert out["selection"]["proud_level"] == 1
    assert "constellation" not in out["selection"]


def test_review_equivalent_explicit_and_resolved_depots_share_one_closure(passive_db, monkeypatch):
    called = []
    original = runtime_queries._closure
    def watched(*a, **k):
        called.append(1)
        return original(*a, **k)
    monkeypatch.setattr(runtime_queries, "_closure", watched)
    with equipment_read_session(passive_db):
        default = query(passive_db)
        explicit = query(passive_db, skill_depot_id=9001, character_level=60, ascension=3)
    assert default["source_fingerprint"] == explicit["source_fingerprint"]
    assert explicit["unlock"]["status"] == "locked"
    assert len(called) == 1


def test_review_context_and_public_result_cannot_poison_other_same_session_queries(passive_db, monkeypatch):
    with equipment_read_session(passive_db):
        first = get_passive_source_context(passive_db, character_id=90000001, proud_skill_id=77101)
        second = get_passive_source_context(passive_db, character_id=90000001, proud_skill_id=77101)
        first.source_view["entry_records"]["promote_records"][-1]["record"]["unlockMaxLevel"] = 1
        first.source_view["snapshot"]["counts"]["independent_poison"] = 1
        second_out = project_passive_source(second, character_level=90, ascension=6)
        assert second_out["unlock"]["status"] == "eligible_under_declared_progression"
        assert "independent_poison" not in second_out["snapshot"]["counts"]
        second_out["source_bundle"]["abilities"].clear()
        third = query(passive_db)
        assert third["source_bundle"]["abilities"]
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("late SQLite"))
    monkeypatch.setattr(sqlite3.dbapi2, "connect", lambda *a, **k: pytest.fail("late dbapi SQLite"))
    assert project_passive_source(second, character_level=60, ascension=3)["unlock"]["status"] == "locked"


@pytest.mark.parametrize("mutation", ["opening", "promote", "text", "nested-normalization", "reference"])
def test_review_source_identity_covers_semantic_leaves_not_just_bundle_hash(passive_db, mutation):
    original = query(passive_db)
    changed = copy.deepcopy(original)
    if mutation == "opening":
        changed["entry_records"]["selected_opening"]["record"]["needAvatarPromoteLevel"] = 5
    elif mutation == "promote":
        changed["entry_records"]["promote_records"][-1]["record"]["unlockMaxLevel"] = 100
    elif mutation == "text":
        changed["descriptions"]["description"]["text"] += " 原文发生变化。"
    elif mutation == "nested-normalization":
        changed["snapshot"].setdefault("normalization", {})["mapping_fingerprint"] = "1" * 64
    else:
        changed["source_bundle"]["references"][0]["json_pointer"] += "/unrelated"
    assert passive_source_fingerprint(changed) != original["source_fingerprint"]


@pytest.mark.parametrize("mutation", ["supplied", "requirement", "check", "status"])
def test_review_unlock_only_mutation_leaves_source_identity_intact(passive_db, mutation):
    original = query(passive_db)
    changed = copy.deepcopy(original)
    unlock = changed["unlock"]
    if mutation == "supplied":
        unlock["supplied"]["ascension"] = 4
    elif mutation == "requirement":
        unlock["requirement"]["required_ascension"] = 5
    elif mutation == "check":
        unlock["checks"][0]["status"] = "fail"
    else:
        unlock["status"] = "locked"
    assert passive_source_fingerprint(changed) == original["source_fingerprint"]
    assert passive_unlock_fingerprint(changed) != original["unlock_fingerprint"]


def test_review_known_zero_stage_is_distinct_from_missing_stage(passive_db, fake_repo):
    rewrite(fake_repo, "AvatarPromote", lambda rows: next(r for r in rows if r.get("avatarPromoteId") == 900
        and "promoteLevel" not in r).update(promoteLevel=0))
    rebuild(passive_db)
    out = query(passive_db, character_level=20, ascension=0)
    assert out["unlock"]["status"] == "locked"
    assert out["unlock"]["selected_promote_record"]["record"]["promoteLevel"] == 0


def test_review_two_batch_progression_decisions_do_not_share_mutable_unlock(passive_db):
    base = {"action": "passive_source_profile", "character_id": 90000001, "proud_skill_id": 77101}
    out = runtime_queries.query_runtime(passive_db, {"action": "batch", "items": [
        {"id": "one", "request": {**base, "character_level": 1, "ascension": 6}},
        {"id": "cap-failure", "request": {**base, "character_level": 90, "ascension": 4}},
        {"id": "valid", "request": {**base, "character_level": 90, "ascension": 6}}]})
    assert out["status"] == "complete"
    rows = [item["data"] for item in out["items"]]
    assert [row["unlock"]["status"] for row in rows] == ["eligible_under_declared_progression", "invalid", "eligible_under_declared_progression"]
    assert len({r["source_fingerprint"] for r in rows}) == 1
    assert len({r["unlock_fingerprint"] for r in rows}) == 3
    rows[0]["unlock"]["supplied"]["ascension"] = 0
    assert rows[2]["unlock"]["supplied"]["ascension"] == 6
    assert all(r["unlock"]["complete_character_validity"] == "not_evaluated" for r in rows)
