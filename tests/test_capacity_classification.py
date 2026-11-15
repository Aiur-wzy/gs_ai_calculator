"""Real synthetic SQLite classification checks; never access a product DB."""

# ruff: noqa: F811 - pytest injects the imported engineering fixtures.
import copy
import json
import sqlite3
from contextlib import closing

import pytest
from capacity_fixture import context, selection
from test_capacity_model import (  # noqa: F401
    action_resource_synthetic_db_only,
    fake_repo,
    query,
    ready,
    source_db,
)

from genshin_db import runtime_action_capacity as capacity
from genshin_db.runtime_action_sources import _exact
from genshin_db.runtime_queries import _Nodes, _read
from genshin_db.semantic_store import HEAD_TABLE, TABLES, ensure_schema
from genshin_runtime.mechanism_semantics import fingerprint, validate_record
from tests.test_action_resource_sources import CID, install

pytestmark = pytest.mark.usefixtures("action_resource_synthetic_db_only")


def unrelated(delta, *, other_actor=False, status="approved", number=0):
    record = copy.deepcopy(delta)
    record["id"] = record["payload"]["id"] = f"fixture.unrelated.{number}"
    if other_actor:
        record["subject"]["target_id"] = CID + 1
        claim = record["payload"]["claim"]
        claim["source_claim"]["character_id"] = CID + 1
        claim["prior_review"]["source_claim_sha256"] = fingerprint(
            claim["source_claim"]
        )
    else:
        record["payload"].update(
            kind="execution_requirement", claim={"requirement": "single_target"}
        )
    record["review"] = {
        "status": status,
        "reviewer": "synthetic-only" if status in ("approved", "revoked") else None,
        "reviewed_at": "2026-10-11T00:00:00+00:00"
        if status in ("approved", "revoked")
        else None,
    }
    validate_record(record, "fact")
    return record


@pytest.mark.parametrize("other_actor", [False, True])
@pytest.mark.parametrize(
    "damage", ["payload", "evidence", "applicability", "review", "fingerprint", "head"]
)
def test_bad_unrelated_record_cannot_disappear_before_complete_validation(
    ready, other_actor, damage
):
    record = unrelated(ready[2], other_actor=other_actor)
    install(ready[0], record)
    field, value = {
        "payload": ("payload_json", json.dumps({**record["payload"], "unknown": True})),
        "evidence": ("evidence_json", "[]"),
        "applicability": ("applicability_json", "{}"),
        "review": ("status", "broken"),
        "fingerprint": ("payload_fingerprint", "0" * 64),
        "head": ("updated_at", ""),
    }[damage]
    table = HEAD_TABLE if damage == "head" else TABLES["fact"]
    with closing(sqlite3.connect(ready[0])) as conn, conn:
        conn.execute(
            f"UPDATE {table} SET {field}=? WHERE record_id=?", (value, record["id"])
        )
    with pytest.raises(ValueError):
        query(ready)


def test_many_legal_unrelated_records_do_not_use_character_capacity_budget(ready):
    baseline = query(ready)
    for number in range(44):
        install(
            ready[0],
            unrelated(
                ready[2],
                number=number,
                other_actor=number % 2 == 0,
                status=("approved", "pending", "revoked", "draft")[number % 4],
            ),
        )
    assert query(ready) == baseline


def test_32_valid_capacity_candidates_are_complete_and_33_are_not_truncated(
    ready, monkeypatch
):
    db, model, _ = ready
    other = copy.deepcopy(model)
    claim = other["payload"]["claim"]
    with _read(db, "release") as (conn, _):
        skill = _exact(
            conn, _Nodes(conn, "release"), "release", "AvatarSkill", {"id": 90011}
        )
    claim["target"].update(skill_id=90011, depot_pointer="/skills/0")
    node = next(item for item in claim["materials"]["nodes"] if item["id"] == "skill")
    node.update(
        definition_id=skill["definition"]["definition_id"],
        **{
            key: skill[key]
            for key in ("source_path", "source_sha256", "json_pointer", "node_sha256")
        },
    )
    for number in range(30):
        other["id"] = other["payload"]["id"] = f"fixture.other.model.{number:02}"
        install(db, other)
    result = query(ready)
    inventory = result["competitor_inventory"]
    assert result["status"] == "ready" and result["capacity"] == 2
    assert inventory["complete"] is True and len(inventory["entries"]) == 32
    assert (
        sum(
            row["disposition"] == "different_target_verified"
            for row in inventory["entries"]
        )
        == 30
    )
    other["id"] = other["payload"]["id"] = "fixture.other.model.30"
    install(db, other)
    monkeypatch.setattr(
        capacity,
        "fresh_model_material",
        lambda *args: pytest.fail("33 candidates reached raw materials"),
    )
    with pytest.raises(ValueError) as error:
        query(ready)
    assert error.value.code == "capacity_budget"


@pytest.mark.parametrize("encoding", ["ascii", "utf8"])
def test_single_huge_body_is_rejected_before_decode_using_stored_bytes(
    ready, monkeypatch, encoding
):
    record = unrelated(ready[2])
    record["id"] = record["payload"]["id"] = "0huge"
    install(ready[0], record)
    maximum = capacity.MAX_CLASSIFICATION_RECORD_BYTES
    body = (
        " " * maximum + "{}"
        if encoding == "ascii"
        else json.dumps({"x": "中" * (maximum // 3)}, ensure_ascii=False)
    )
    if encoding == "utf8":
        assert len(body) < maximum < len(body.encode("utf8"))
    with closing(sqlite3.connect(ready[0])) as conn, conn:
        conn.execute(
            f"UPDATE {TABLES['fact']} SET payload_json=? WHERE record_id='0huge'",
            (body,),
        )
    monkeypatch.setattr(
        capacity,
        "_decode",
        lambda *args: pytest.fail("oversized body reached JSON decoder"),
    )
    with pytest.raises(ValueError) as error:
        query(ready)
    assert error.value.code == "capacity_classification_budget"


def test_cumulative_budget_is_charged_before_next_decode(ready, monkeypatch):
    record = unrelated(ready[2])
    install(ready[0], record)
    size = "+".join(
        f"coalesce(length(CAST({column} AS BLOB)),0)" for column in capacity._COLUMNS
    )
    with closing(sqlite3.connect(ready[0])) as conn:
        sizes = conn.execute(
            f"SELECT record_id,({size}) FROM {TABLES['fact']} ORDER BY record_id"
        ).fetchall()
    assert capacity.MAX_CLASSIFICATION_BYTES == 16 * 1024 * 1024
    monkeypatch.setattr(
        capacity, "MAX_CLASSIFICATION_BYTES", sum(row[1] for row in sizes) - 1
    )
    original, seen = capacity._decode, []

    def observe(row, kind):
        seen.append(row[1])
        return original(row, kind)

    monkeypatch.setattr(capacity, "_decode", observe)
    with pytest.raises(ValueError) as error:
        query(ready)
    assert error.value.code == "capacity_classification_budget"
    assert seen == [row[0] for row in sizes[:-1]]


def test_classification_count_sentinel_is_independent_of_capacity_limit(
    ready, monkeypatch
):
    for number in range(5):
        install(ready[0], unrelated(ready[2], number=number))
    original, seen = capacity._decode, []
    assert capacity.MAX_CLASSIFICATION_FACTS == 4096
    monkeypatch.setattr(capacity, "MAX_CLASSIFICATION_FACTS", 6)

    def observe(row, kind):
        seen.append(row[1])
        return original(row, kind)

    monkeypatch.setattr(capacity, "_decode", observe)
    with pytest.raises(ValueError) as error:
        query(ready)
    assert error.value.code == "capacity_classification_budget" and len(seen) == 6
    assert capacity.capabilities()["max_current_facts"] == 32


@pytest.mark.parametrize("table", [HEAD_TABLE, TABLES["fact"]])
def test_duplicate_current_identity_cannot_be_discarded_as_unrelated(ready, table):
    record = unrelated(ready[2])
    install(ready[0], record)
    with closing(sqlite3.connect(ready[0])) as conn, conn:
        conn.execute(f"CREATE TABLE damaged_copy AS SELECT * FROM {table}")
        conn.execute(f"DROP TABLE {table}")
        conn.execute(f"ALTER TABLE damaged_copy RENAME TO {table}")
        conn.execute(
            f"INSERT INTO {table} SELECT * FROM {table} WHERE record_id=?",
            (record["id"],),
        )
    with pytest.raises(ValueError):
        query(ready)


def test_orphan_current_head_cannot_be_classified_away(ready):
    with closing(sqlite3.connect(ready[0])) as conn, conn:
        conn.execute(
            f"INSERT INTO {HEAD_TABLE} VALUES ('release','fact','fixture.orphan',1,'now')"
        )
    with pytest.raises(ValueError):
        query(ready)


def test_bogus_semantic_head_kind_cannot_be_filtered_out(ready):
    db, _, _ = ready
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute(f"CREATE TABLE damaged_head AS SELECT * FROM {HEAD_TABLE}")
        conn.execute(f"DROP TABLE {HEAD_TABLE}")
        conn.execute(f"ALTER TABLE damaged_head RENAME TO {HEAD_TABLE}")
        conn.execute(
            f"INSERT INTO {HEAD_TABLE} VALUES ('release','bogus','fixture.invalid-head-kind',1,'now')"
        )
    with pytest.raises(ValueError) as error:
        query(ready)
    assert error.value.code == "capacity_current"


def test_classification_budgets_are_public_and_cannot_be_requested(ready):
    declared = capacity.capabilities()["classification"]
    assert declared["max_facts"] == 4096
    assert declared["max_record_bytes"] == 2 * 1024 * 1024
    assert declared["max_total_bytes"] == 16 * 1024 * 1024
    assert declared["sql_vm_steps"] == "not_bounded_here"
    from genshin_db.runtime_queries import query_runtime

    with pytest.raises(ValueError):
        query_runtime(
            ready[0],
            {
                "action": "action_capacity_profile",
                "selection": selection(),
                "context": context(),
                "model_ref": {"id": ready[1]["id"], "revision": 1},
                "max_facts": 8192,
            },
        )


@pytest.mark.parametrize("encoding", ["UTF-16le", "UTF-16be"])
def test_non_utf8_database_is_rejected_before_stored_byte_classification(
    tmp_path, encoding
):
    with closing(sqlite3.connect(tmp_path / "synthetic-encoding.db")) as conn:
        conn.execute(f"PRAGMA encoding='{encoding}'")
        ensure_schema(conn)
        conn.commit()
        assert conn.execute("PRAGMA encoding").fetchone()[0] == encoding
        # CAST(... AS BLOB) is not UTF-8 in this database: 2 versus 3 bytes.
        assert (
            conn.execute("SELECT length(CAST(? AS BLOB))", ("中",)).fetchone()[0] == 2
        )
        assert len("中".encode()) == 3
        conn.execute("BEGIN")
        with pytest.raises(ValueError, match="UTF-8"):
            capacity.current_capacity_refs(conn, "release", CID)


def test_second_connection_head_commit_is_visible_only_next_read_snapshot(
    ready, monkeypatch
):
    db, model, delta = ready
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    new = copy.deepcopy(delta)
    new["revision"] = new["payload"]["revision"] = 2
    new["review"] = {"status": "pending", "reviewer": None, "reviewed_at": None}
    install(db, new, expected=1)
    # Keep revision 2 stored, but publish revision 1 until the reader has begun.
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute(
            f"UPDATE {HEAD_TABLE} SET revision=1 WHERE record_id=?", (delta["id"],)
        )
    original = capacity.current_capacity_refs
    calls = []

    def publish_between_inventory_and_material(conn, *args):
        refs = original(conn, *args)
        calls.append(getattr(conn, "connection", conn))
        with closing(sqlite3.connect(db)) as writer, writer:
            writer.execute(
                f"UPDATE {HEAD_TABLE} SET revision=2,updated_at='second-connection' WHERE record_id=?",
                (delta["id"],),
            )
        return refs

    monkeypatch.setattr(
        capacity, "current_capacity_refs", publish_between_inventory_and_material
    )
    with _read(db, "release") as (conn, state):
        result = capacity.read_action_capacity_profile(
            conn,
            state,
            selection=selection(),
            context=context(),
            model_ref={"id": model["id"], "revision": 1},
        )
        assert result["status"] == "ready" and result["capacity"] == 2
        assert calls == [getattr(conn, "connection", conn)]
        assert (
            next(
                row
                for row in result["competitor_inventory"]["entries"]
                if row["id"] == delta["id"]
            )["revision"]
            == 1
        )
    monkeypatch.setattr(capacity, "current_capacity_refs", original)
    result = query(ready)
    assert result["status"] == "pending" and result["capacity"] is None
    assert (
        next(
            row
            for row in result["competitor_inventory"]["entries"]
            if row["id"] == delta["id"]
        )["revision"]
        == 2
    )
