"""Current-source review, fresh admission and legacy identities remain distinct."""
import copy
import gzip
import json
import sqlite3
from collections import Counter
from pathlib import Path

import pytest

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment import static_inclusion as compiler
from genshin_app.equipment.static_inclusion_profiles import (
    PROFILE_V3,
    PROFILE_V3_FINGERPRINT,
)
from genshin_app.equipment.static_inclusion_receipt import (
    validate_static_inclusion_receipt,
)
from genshin_db import equipment_queries, queries, runtime_queries
from genshin_db.runtime_catalog import dumps, fingerprint
from tests.test_equipment_panel import panel_plan

ROOT = Path(__file__).resolve().parents[1]
MATERIALS = json.loads(gzip.decompress((ROOT / "tests/fixtures/static_inclusion_profiles/current-224708ed-materials.json.gz").read_bytes()))
SELECTION = {"profile_id": PROFILE_V3["id"], "profile_version": 3, "profile_fingerprint": PROFILE_V3_FINGERPRINT}


def test_current_review_is_bound_to_frozen_evidence_and_no_numeric_delivery():
    review = json.loads((ROOT / PROFILE_V3["review_evidence"]["reference"]).read_text(encoding="utf8"))
    assert fingerprint(review) == PROFILE_V3["review_evidence"]["fingerprint"]
    assert review["database_unchanged"] and review["database_sha256_before"] == review["database_sha256_after"]
    assert fingerprint(PROFILE_V3) == PROFILE_V3_FINGERPRINT
    assert fingerprint(MATERIALS) == review["materials_fingerprint"]
    results = [compiler.compile_static_inclusion(m, **SELECTION) for m in MATERIALS]
    assert Counter(r["status"] for r in results) == {"shape_ready": 34, "pending": 91}
    assert all(r["source_authority"] == "not_granted" for r in results)
    assert all(r["declaration"]["numeric_application"] is False for r in results if r["status"] == "shape_ready")
    assert compiler.PROFILE_FINGERPRINT == "6dc628f5a73a244ad3bfe9d987fc08f91ecba2590abfa430eb3158fe64f5f39e"
    assert compiler.PROFILE_V2_FINGERPRINT == "e07844a863e6eea9a973e9213811446b0337f22bf5ed0800f6e1b683acbf7d49"


@pytest.mark.parametrize("field", ["upstream_commit", "snapshot_id", "manifest_fingerprint", "normalization", "source_capabilities"])
def test_current_scope_changes_cannot_be_relabelled(field):
    snapshot = copy.deepcopy(MATERIALS[0]["parent"]["snapshot"])
    assert compiler.select_static_inclusion_profile(snapshot) == {"status": "selected", **SELECTION}
    snapshot[field] = {} if isinstance(snapshot[field], dict) else "f" * 64
    assert compiler.select_static_inclusion_profile(snapshot)["status"] == "pending"


@pytest.fixture
def current_database(tmp_path, monkeypatch):
    path = tmp_path / "current.db"
    parents = {m["parent"]["definition"]["definition_id"]: m["parent"] for m in MATERIALS}
    sources = {m["source"]["source"]["affix_id"]: m["source"] for m in MATERIALS}
    details = {}
    for material in MATERIALS:
        sid = material["selection"]["set_id"]
        detail = details.setdefault(sid, {"artifact_set": {"id": sid, "name": material["normalized"]["name"]},
            "annotations": [], "effects": [], "data_version": material["selection"]["data_version"]})
        detail["effects"].append({**material["relation"], "effect": material["normalized"]})
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE runtime_definition (definition_id TEXT, version_line TEXT, source_path TEXT, exact_key_json TEXT)")
        for ident, parent in parents.items():
            conn.execute("INSERT INTO runtime_definition VALUES (?,?,?,?)", (ident, "release", compiler.PARENT_PATH,
                dumps(parent["definition"]["exact_key"])))
    version = MATERIALS[0]["selection"]["data_version"]
    meta = {"datasets": {"equipment": {"lines": {"release": {"state": "ready", "schema_version": 1,
        "upstream_commit": version["upstream_commit"], "built_at": version["built_at"]}}}}}
    monkeypatch.setattr(queries, "get_meta", lambda *a, **k: copy.deepcopy(meta))
    monkeypatch.setattr(queries, "get_artifact_set_detail", lambda _, sid, *a, **k: copy.deepcopy(details[sid]))
    monkeypatch.setattr(equipment_queries, "get_artifact_set_detail", lambda _, sid, *a, **k: copy.deepcopy(details[sid]))
    def source(_, aid, *a, snapshot_id=None, **k):
        value = copy.deepcopy(sources[aid])
        assert snapshot_id is None or snapshot_id == value["snapshot"]["snapshot_id"]
        return value
    monkeypatch.setattr(runtime_queries, "assemble_runtime_source", source)
    monkeypatch.setattr(runtime_queries, "get_runtime_definition", lambda _, ident, **k: copy.deepcopy(parents[ident]))
    return path, version


@pytest.mark.parametrize("set_id", [10001, 10007, 15002])
def test_current_source_fresh_admission_preserves_panel_and_result_receipt(current_database, set_id):
    path, version = current_database
    plan = panel_plan()
    plan["equipment_inputs"]["char1"].update(data_version=version, artifact_sets=[{"set_id": set_id, "count": 2}])
    before = copy.deepcopy(plan["manual_template"]["characters"])
    result = run_plan_calc(plan, path)
    record = result["report"]["static_inclusion_compilations"][0]
    assert record["status"] == "ready" and record["source_authority"] == "fresh_database_admission"
    assert record["shape_certificate"]["profile_fingerprint"] == PROFILE_V3_FINGERPRINT
    assert result["plan"]["resolved_template"]["characters"] == before
    assert record["declaration"]["numeric_application"] is False
    validate_static_inclusion_receipt(result["plan"], result["result"], result["report"], result["result_receipt"])


@pytest.mark.parametrize("state", ["pending", "excluded", "resolved"])
def test_current_profile_cannot_override_explicit_owner_choice(current_database, state):
    path, version = current_database
    plan = panel_plan()
    plan["equipment_inputs"]["char1"].update(data_version=version, artifact_sets=[{"set_id": 10007, "count": 2}])
    requirement = compose_equipment_plan(plan, path)["effect_requirements"][0]
    binding = copy.deepcopy(requirement["proposed_binding"])
    binding["parts"] = [{"part_id": "explicit", "label": "保留已有处理", "state": state,
        "rule": None, "delivery": None, "conditions": [], "reason": "显式决定不被自动改写",
        "dependencies": {"mode": "source_only", "context_fingerprint": None, "note": "当前场景决定"}}]
    if state == "resolved":
        binding["parts"][0].update(rule={"kind": "constant", "value": 80},
            delivery={"kind": "panel", "target_character_id": "char1", "stat": "em", "operation": "flat"})
    plan["effect_bindings"] = [binding]
    before = copy.deepcopy(plan)
    report = compose_equipment_plan(plan, path)
    assert "static_inclusion_compilations" not in report
    assert report["plan"]["effect_bindings"] == before["effect_bindings"]
    assert plan == before
