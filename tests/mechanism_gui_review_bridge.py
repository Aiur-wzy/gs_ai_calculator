"""Independent JSONL GUI review bridge backed by an isolated real runtime DB.

Only test fixtures and the public compose/query functions are called. No formal
database, GUI window, network, or gameplay oracle is used.
"""
from __future__ import annotations

import copy
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(ROOT / "tests")]

import pytest

from genshin_app.equipment import compose_equipment_plan
from genshin_app.equipment.service import plan_capabilities
from genshin_db.runtime_queries import query_runtime
from tests.mechanism_combined_fixture import combined_db, combined_plan
from tests.mechanism_source_fixtures import mechanism_repo
from tests.test_mechanism_binding_review import def_descriptor
from tests.test_pipeline import fake_repo


def main():
    work = Path(sys.argv[1]).resolve()
    evidence_root = (ROOT / "outputs" / "engine-continuation-20261002").resolve()
    if not work.is_relative_to(evidence_root) or work.exists():
        raise ValueError("Review workspace must be a new directory under the evidence root")
    work.mkdir(parents=True)
    patches = pytest.MonkeyPatch()
    repo = mechanism_repo.__wrapped__(fake_repo.__wrapped__(work))
    db = combined_db.__wrapped__(work, repo)
    plan, _ = combined_plan.__wrapped__(db, patches)
    def_id = def_descriptor(db)
    plan["manual_template"]["characters"][0]["stats"].update({"def": 500, "base_def": 100})
    plan["manual_template"]["characters"][0]["name"] = "工程冰角色"
    plan["manual_template"]["characters"][1]["name"] = "工程风角色"
    identifiers = ["skill.current-atk-limited-addition", "passive.swirl-elemental-bonus", def_id]
    profiles = {ident: query_runtime(db, {"action": "mechanism_profile", "mechanism_id": ident}) for ident in identifiers}
    try:
        for line in sys.stdin:
            request = {}
            try:
                request = json.loads(line)
                operation = request["operation"]
                if operation == "init":
                    data = {"plan": copy.deepcopy(plan), "caps": plan_capabilities(), "profiles": profiles,
                            "database": str(db), "source": "isolated_fixture_raw_and_reviewed_semantics"}
                elif operation == "compose":
                    data = compose_equipment_plan(request["plan"], db)
                elif operation == "profile":
                    data = query_runtime(db, {"action": "mechanism_profile", "mechanism_id": request["mechanism_id"]})
                elif operation == "query":
                    query = request["request"]
                    if query.get("action") not in ("mechanism_profile", "mechanism_catalog"):
                        raise ValueError("Only mechanism catalog/profile queries are allowed")
                    data = query_runtime(db, query)
                else:
                    raise ValueError("Unknown review operation")
                answer = {"id": request["id"], "code": 0, "data": data}
            except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
                answer = {"id": request.get("id"), "code": 1, "message": str(exc), "error_type": type(exc).__name__}
            print(json.dumps(answer, ensure_ascii=False, allow_nan=False), flush=True)
    finally:
        patches.undo()


if __name__ == "__main__":
    main()
