"""Independent query identity checks before/after the EC17b physical index."""
import copy
import json
import sqlite3
from contextlib import closing

import pytest

from genshin_db.runtime_catalog import RuntimeConfigError
from genshin_db.runtime_queries import get_runtime_definition, query_runtime_closure
from genshin_db.runtime_store import validate_runtime
from genshin_engine.runtime_energy_trigger_compiler import compile_energy_trigger_source
from genshin_runtime.energy_trigger_identity import LIMITS, recipe
from tests.test_energy_trigger_compiler import scenario, semantics
from tests.test_pipeline import fake_repo  # noqa: F401
from tests.test_runtime_energy_dispatch import (  # noqa: F401
    context,
    trigger_db,
    trigger_repo,
)
from tests.test_runtime_object_index import build, rows, write

# Imported fixture functions are intentionally registered with pytest.
# ruff: noqa: F811
INDEX = "idx_runtime_physical_ref_target_cover_v1"
COLUMNS = "version_line,resolved_definition_id,resolution_status,target_kind,target_key_json"


def _toggle(db, enabled):
    with closing(sqlite3.connect(db, cached_statements=0)) as conn, conn:
        if enabled:
            conn.execute(f"CREATE INDEX {INDEX} ON runtime_reference({COLUMNS})")
        else:
            conn.execute(f"DROP INDEX IF EXISTS {INDEX}")
        validate_runtime(conn, "release", deep=True)


def _repack_reference_rows(db):
    """A legal historical insertion order, with every indexed/raw value retained.

    SQLite rowid is a physical detail. The new covering index uses it as a
    final tie breaker, so this exercises more than freshly sorted bulk import.
    """
    with closing(sqlite3.connect(db)) as conn, conn:
        values = conn.execute("SELECT * FROM runtime_reference ORDER BY reference_id DESC").fetchall()
        before = sorted(values, key=repr)
        conn.execute("DELETE FROM runtime_reference")
        conn.executemany("INSERT INTO runtime_reference VALUES (" + ",".join("?" for _ in values[0]) + ")", values)
        assert sorted(conn.execute("SELECT * FROM runtime_reference").fetchall(), key=repr) == before
        validate_runtime(conn, "release", deep=True)


def test_review_same_key_and_local_modifier_full_queries_keep_identity(tmp_path, fake_repo):
    path = "BinOutput/Ability/Temp/ReviewSameKey.json"
    value = [{"Default": {"abilityName": name, "modifiers": {"SameModifier": {"life": 10}},
        "onAdded": [{"$type": "ApplyModifier", "modifierName": "SameModifier"},
                    {"$type": "AddAbility", "abilityName": "SameAbility"}]}}
        for name in ("OwnerA", "OwnerB")]
    value += [{"Default": {"abilityName": "SameAbility"}},
              {"Alternate": {"abilityName": "SameAbility"}}]
    write(fake_repo, path, value)
    db = tmp_path / "same-key.db"
    build(db, fake_repo)
    _toggle(db, False)
    _repack_reference_rows(db)
    definitions = rows(db, "runtime_definition", "WHERE source_path=? ORDER BY json_pointer", (path,))
    owners = [d["definition_id"] for d in definitions if json.loads(d["exact_key_json"]) in ("OwnerA", "OwnerB")]
    assert len(owners) == 2

    def read():
        return {"definitions": [get_runtime_definition(db, owner, sections=("summary", "references", "payload")) for owner in owners],
                "closure": query_runtime_closure(db, {"roots": owners})}

    before = read()
    for owner, projection in zip(owners, before["definitions"], strict=True):
        modifier, ability = projection["references"]
        assert modifier["resolution_status"] == "resolved"
        selected = next(d for d in definitions if d["definition_id"] == modifier["resolved_definition_id"])
        assert selected["owner_definition_id"] == owner
        assert ability["resolution_status"] == "ambiguous"
        assert len(ability["candidate_ids"]) == 2
        assert ability["candidate_ids"] == sorted(ability["candidate_ids"])
    _toggle(db, True)
    assert read() == before


def _mount_projection(db):
    bundle = context(db).public_projection
    return bundle, compile_energy_trigger_source(bundle, parent_semantics=semantics(bundle), scenario=scenario())


@pytest.mark.parametrize("extra_kind", ["unreviewed_shape", "second_valid"])
def test_review_multiple_avatar_mounts_keep_full_projection_and_acceptance(trigger_db, trigger_repo, extra_kind):
    mount = recipe()["mount"]
    extra = {"abilities" if extra_kind == "unreviewed_shape" else mount["new_pointer"][1:]: copy.deepcopy(mount["new_raw"])}
    write(trigger_repo, "BinOutput/Avatar/ReviewSecondMount.json", extra)
    build(trigger_db, trigger_repo, upstream_commit=recipe()["commit"])
    _toggle(trigger_db, False)
    _repack_reference_rows(trigger_db)
    before, accepted_before = _mount_projection(trigger_db)
    assert before["status"] == ("ready" if extra_kind == "unreviewed_shape" else "pending")
    mounts = [e for e in before["source_bundle"]["entries"] if e["definition"]["definition_kind"] == "avatar_config"]
    assert len(mounts) == 2
    _toggle(trigger_db, True)
    after, accepted_after = _mount_projection(trigger_db)
    # Never sort the API arrays here: their exact order enters source/acceptance
    # fingerprints, and the sole mutation was adding a physical index.
    assert after == before
    assert accepted_after == accepted_before


def test_review_mount_candidate_budget_is_not_an_ordered_prefix_selection(trigger_db, trigger_repo, monkeypatch):
    mount = recipe()["mount"]
    for suffix in ("A", "B"):
        write(trigger_repo, f"BinOutput/Avatar/ReviewMount{suffix}.json", {mount["new_pointer"][1:]: copy.deepcopy(mount["new_raw"])})
    build(trigger_db, trigger_repo, upstream_commit=recipe()["commit"])
    _toggle(trigger_db, False)
    monkeypatch.setitem(LIMITS, "max_candidates", 2)
    failures = []
    for indexed in (False, True):
        if indexed:
            _toggle(trigger_db, True)
        with pytest.raises(RuntimeConfigError) as caught:
            context(trigger_db)
        failures.append((caught.value.reason, caught.value.status, caught.value.details))
    assert failures[0] == failures[1]
    assert failures[0][0:2] == ("response_too_large", 413)
    assert failures[0][2]["limit_kind"] == "mount_candidates"
