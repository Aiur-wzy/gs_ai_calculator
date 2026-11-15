"""Knowledge candidates share one axis but never gain execution authority."""
import copy
import sqlite3
from contextlib import closing

from genshin_app.equipment import compose_equipment_plan
from genshin_db.runtime_queries import query_runtime
from genshin_db.semantic_store import import_bundle, read_record
from tests.mechanism_combined_fixture import (  # noqa: F401
    ADDITION,
    combined_db,
    combined_plan,
    fake_repo,
    mechanism_repo,
)


# ruff: noqa: F811
def test_synthetic_same_batch_em_writer_invalidates_other_fixed_em_scope(combined_plan):
    plan, db = combined_plan
    with closing(sqlite3.connect(db)) as conn:
        descriptor = read_record(conn, "release", "descriptor", ADDITION, 2)
        fact = read_record(conn, "release", "fact", ADDITION + ".rule.addition", 1)
    keys = ("id", "revision", "subject", "payload", "evidence", "applicability", "review")
    descriptor, fact = ({k: copy.deepcopy(r[k]) for k in keys} for r in (descriptor, fact))
    descriptor["revision"] = descriptor["payload"]["descriptor"]["revision"] = 3
    fact["revision"] = fact["payload"]["revision"] = 2
    fact["payload"]["claim"]["rule"]["name_zh"] = "明确合成的同组精通写入反例；不是游戏事实"
    fact["payload"]["claim"]["rule"]["effects"].append({"id": "synthetic-em", "benefit_type_id": "benefit.em.all",
        "domain": "self", "value": 100})
    descriptor["payload"]["descriptor"]["rule_templates"][0] = copy.deepcopy(fact["payload"]["claim"])
    refs = descriptor["payload"]["descriptor"]["evidence_fact_ids"]
    refs[refs.index(fact["id"] + "@1")] = fact["id"] + "@2"
    for row in (descriptor, fact):
        row["review"] = {"status": "approved", "reviewer": "SYNTHETIC-NOT-GAME-EVIDENCE", "reviewed_at": "2026-10-03"}
    bundle = {"schema_version": 1, "id": "synthetic-em-collision", "facts": [fact], "descriptors": [descriptor], "heads": [
        {"record_kind": "fact", "record_id": fact["id"], "revision": 2, "expected_head_revision": 1},
        {"record_kind": "descriptor", "record_id": ADDITION, "revision": 3, "expected_head_revision": 2}]}
    assert import_bundle(db, bundle, dry_run=False)["applied"]
    source = query_runtime(db, {"action": "mechanism_profile", "mechanism_id": ADDITION})
    assert source["compiler_result"]["status"] == "ready", source["issues"]
    report = compose_equipment_plan(plan, db)
    assert report["state"] == "pending"
    assert all(r["status"] == "pending" and r["required_confirmation"] is None for r in report["mechanism_compilations"])
    assert all(not r[key] for r in report["mechanism_compilations"] for key in ("parts", "rules", "initial_rules"))
    assert report["mechanics_model"]["rules"] == []
    assert any("NONEXTRA" in i["message"] for r in report["mechanism_compilations"] for i in r["issues"])
