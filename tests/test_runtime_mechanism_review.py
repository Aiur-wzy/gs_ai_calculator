"""Independent boundary probes; this file never opens the formal database."""
import copy
from contextlib import closing, contextmanager
from decimal import Decimal

import pytest

from genshin_db import runtime_mechanism, runtime_queries
from genshin_db.semantic_store import import_bundle
from tests.mechanism_source_fixtures import (  # noqa: F401
    fake_repo,
    fixture_catalog,
    mechanism_db,
    mechanism_repo,
)
from tests.test_mechanics_execution import (
    EVIDENCE,
    ability,
    action,
    add,
    run,
    second_character,
)
from tests.test_swirl_review import inputs as swirl_inputs

# ruff: noqa: F811


def fresh(db, ident="skill.current-atk-limited-addition"):
    return runtime_queries.query_runtime(db, {"action": "mechanism_profile", "mechanism_id": ident})


def extra_descriptor(db, edit, *, facts=None):
    seed = fixture_catalog(db)
    record = copy.deepcopy(seed["descriptors"][0])
    record["id"] = record["payload"]["descriptor"]["mechanism_id"] = "review-derived"
    edit(record)
    data = {"schema_version": 1, "id": "independent-extra", "facts": facts or [],
            "descriptors": [record], "heads": [{"record_kind": "descriptor", "record_id": record["id"],
                                                 "revision": 1, "expected_head_revision": None}]}
    data["heads"].extend({"record_kind": "fact", "record_id": f["id"], "revision": 1,
                          "expected_head_revision": None} for f in data["facts"])
    assert import_bundle(db, data, dry_run=False)["applied"]
    return record["id"]


@pytest.mark.parametrize("operation", ["catalog", "profile"])
def test_review_materializer_result_cannot_mutate_shared_read_state(monkeypatch, operation):
    import sqlite3

    state = {"version_line": "release", "snapshot_id": "a" * 64, "counts": {"runtime_definition": 5}}
    with closing(sqlite3.connect(":memory:")) as conn:
        conn.execute("BEGIN")
        @contextmanager
        def session(*args, **kwargs):
            # _read returns a shallow top-level dict over cached nested state.
            yield conn, dict(state)

        monkeypatch.setattr(runtime_queries, "_read", session)
        def query():
            if operation == "catalog":
                return runtime_mechanism.get_mechanism_catalog("unused-fixture-path")
            return runtime_mechanism.get_mechanism_profile("unused-fixture-path", mechanism_id="review")
        first = query()
        first["snapshot"]["counts"]["runtime_definition"] = -1
        assert query()["snapshot"]["counts"]["runtime_definition"] == 5
        assert state["counts"]["runtime_definition"] == 5


@pytest.mark.parametrize("pointer", ["/paramList", "/paramList/2", "/absent-value"])
def test_review_wrong_scalar_source_remains_diagnostic_without_default_or_exception(mechanism_db, pointer):
    def edit(record):
        parameter = next(p for p in record["payload"]["source_binding"]["parameters"] if p["ref"] == "capacity")
        parameter["pointer"] = pointer

    ident = extra_descriptor(mechanism_db, edit)
    out = fresh(mechanism_db, ident)
    assert out["status"] == "pending"
    assert out["issues"]
    assert out["compiler_result"]["rule_templates"] == []
    assert out["compiler_result"]["execution_ports"] == []
    assert out["compiler_result"]["initial_rule_templates"] == []


def test_review_fully_approved_fact_for_other_subject_does_not_authorize_source(mechanism_db):
    seed = fixture_catalog(mechanism_db)
    old_ref = seed["descriptors"][0]["payload"]["descriptor"]["evidence_fact_ids"][0]
    old_id = old_ref.rsplit("@", 1)[0]
    fact = copy.deepcopy(next(f for f in seed["facts"] if f["id"] == old_id))
    fact["id"] = fact["payload"]["id"] = "review-wrong-subject"
    fact["subject"]["target_id"] += 123

    def edit(record):
        refs = record["payload"]["descriptor"]["evidence_fact_ids"]
        refs[refs.index(old_ref)] = "review-wrong-subject@1"

    ident = extra_descriptor(mechanism_db, edit, facts=[fact])
    out = fresh(mechanism_db, ident)
    assert out["status"] == "pending"
    assert any(issue["code"] == "mechanism_fact_scope_mismatch" for issue in out["issues"])
    assert out["compiler_result"]["rule_templates"] == []


def test_review_actual_same_session_raw_and_snapshot_returns_are_detached(mechanism_db):
    from genshin_db.equipment_queries import equipment_read_session

    with equipment_read_session(mechanism_db):
        baseline = fresh(mechanism_db)
        damaged = fresh(mechanism_db)
        damaged["snapshot"]["counts"].clear()
        damaged["source_proof"]["nodes"][0]["payload"].clear()
        damaged["facts"][0]["claim"].clear()
        assert fresh(mechanism_db) == baseline


def test_review_two_real_source_shapes_compose_through_one_generic_runtime(mechanism_db):
    from genshin_runtime.mechanism_semantics import target_location

    addition = fresh(mechanism_db)
    swirl = fresh(mechanism_db, "passive.swirl-elemental-bonus")
    assert addition["status"] == swirl["status"] == "ready"
    assert addition["compiler_result"]["compiler"] == swirl["compiler_result"]["compiler"]
    t, r, m = swirl_inputs(times=(.2, .3, .4, .5, .6, .7, .8, .9, 1.0))
    second_character(t, r)
    t["characters"][0]["stats"]["em"] = 400
    t["characters"][1]["stats"]["atk"] = 2000
    r["initial_frontline"] = "b"
    r["actions"][0].update(start=.1, occupation_end=.11)
    r["abilities"].extend([ability("provider-skill", "b"), ability("provider-switch", "b", "action.switch")])
    r["actions"].extend([
        action("provider-cast", "provider-skill", 0, .01, repeat={"mode": "once"}),
        action("provider-out", "provider-switch", .02, .03, switch_to="a", repeat={"mode": "once"}),
    ])
    m["elemental_model"]["profiles"].append({"id": "cryo-no-application", "element": "cryo",
                                            "application": "none", **EVIDENCE})
    for h, desc in zip(t["hits"][1:], m["hit_events"][1:], strict=True):
        h["element"] = "cryo"
        desc["application"] = {"profile_id": "cryo-no-application", "target_id": "enemy", "landed": True}
    for prefix, projection, owner, action_ids in (("addition", addition, "b", ["provider-cast"]),
                                                ("reaction", swirl, "a", ["cast"])):
        compiled = projection["compiler_result"]
        for item in compiled["rule_templates"]:
            bound = copy.deepcopy(item["rule"])
            bound.update(id=prefix + "-" + item["key"], source_id=prefix + ":" + item["key"], **EVIDENCE)
            for port in compiled["execution_ports"]:
                if port["rule_key"] == item["key"]:
                    parent, leaf = target_location(bound, port["target"])
                    parent[leaf] = {"provider": owner, "qualifying_actions": action_ids,
                                    "provider_characters": [owner], "activation_delay": 0}[port["role"]]
            add(m, bound)
    executed = run(t, r, m)
    assert executed["result"] is not None, executed["issues"]
    ordinary = [h["expected"] for h in executed["result"]["hits"]
                if h["id"] in {f"h{i}" for i in range(1, 9)}]
    # Saved raw constants independently recorded before this implementation;
    # expected values never read the newly compiled factor or quota.
    base_add = Decimal(2000) * Decimal("0.8218079805374146")
    bonus = Decimal(1) + Decimal(400) * Decimal("0.00039999998989515007")
    enhanced = (Decimal(100) + base_add) * Decimal("0.45") * bonus
    exhausted = Decimal(100) * Decimal("0.45") * bonus
    assert ordinary == pytest.approx([float(enhanced)] * 7 + [float(exhausted)])
    consumed = [row for row in executed["mechanics_trace"] if row["type"] == "quota" and row["cycle"] == 0]
    assert [row["after"] for row in consumed] == [6, 5, 4, 3, 2, 1, 0, 0]
