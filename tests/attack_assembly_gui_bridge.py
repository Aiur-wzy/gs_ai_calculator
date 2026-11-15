"""JSONL author GUI bridge using an isolated, real attack-metadata database."""
from __future__ import annotations

import copy
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(ROOT / "tests")]

import pytest

from genshin_app.equipment.attack_source_assembly import (
    assemble_attack_source,
    attack_source_assembly_capabilities,
)
from genshin_app.equipment.service import plan_capabilities
from genshin_db.runtime_queries import query_runtime
from tests.test_action_events import action_event_plan
from tests.test_pipeline import fake_repo, stub_fetcher
from tests.test_runtime_attack_metadata import metadata_db


def main():
    work = Path(sys.argv[1]).resolve()
    allowed = (ROOT / "outputs" / "engine-continuation-20261002" / "ec21-research").resolve()
    if not work.is_relative_to(allowed) or work.exists():
        raise ValueError("Use a new, isolated EC21 evidence directory")
    work.mkdir(parents=True)
    patches = pytest.MonkeyPatch()
    repo = fake_repo.__wrapped__(work)
    state = stub_fetcher.__wrapped__(repo, patches)
    db = metadata_db.__wrapped__(work, repo, state)
    plan = action_event_plan(db)
    plan["manual_template"]["hits"][0]["multiplier"] = 1
    plan["manual_template"]["characters"][0]["name"] = "工程角色"
    for key in ("hit_parameter_sources", "action_event_profiles", "action_event_bindings", "action_event_overrides"):
        plan.pop(key, None)
    plan["mechanics_model"]["elemental_model"]["profiles"] = []
    try:
        for line in sys.stdin:
            request = {}
            try:
                request = json.loads(line)
                if request["operation"] == "init":
                    data = {"plan": copy.deepcopy(plan), "caps": plan_capabilities(),
                            "contract": attack_source_assembly_capabilities(), "database": str(db)}
                elif request["operation"] == "query":
                    if request["request"].get("action") != "attack_metadata_profile":
                        raise ValueError("Only bounded attack metadata queries are allowed")
                    data = query_runtime(db, request["request"])
                elif request["operation"] == "assemble":
                    data = assemble_attack_source(request["plan"], request["request"], db)
                else:
                    raise ValueError("Unknown bridge operation")
                answer = {"id": request["id"], "code": 0, "data": data}
            except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
                answer = {"id": request.get("id"), "code": 1, "message": str(exc), "error_type": type(exc).__name__}
            print(json.dumps(answer, ensure_ascii=False, allow_nan=False), flush=True)
    finally:
        patches.undo()


if __name__ == "__main__":
    main()
