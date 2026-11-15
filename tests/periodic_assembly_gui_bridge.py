"""Author JSONL GUI bridge; real isolated periodic DB and public GuiApi only."""
from __future__ import annotations

import copy
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(ROOT / "tests")]

import pytest
from periodic_assembly_fixtures import assembly_inputs
from test_pipeline import fake_repo, stub_fetcher
from test_runtime_periodic_attack import periodic_db

from genshin_app.gui.api import GuiApi


def main():
    work = Path(sys.argv[1]).resolve()
    allowed = (ROOT / "outputs/engine-continuation-20261002/ec24-research").resolve()
    if not work.is_relative_to(allowed) or work.exists():
        raise ValueError("Use a new isolated EC24 GUI evidence directory")
    work.mkdir(parents=True)
    patches = pytest.MonkeyPatch()
    try:
        repo = fake_repo.__wrapped__(work)
        state = stub_fetcher.__wrapped__(repo, patches)
        db = periodic_db.__wrapped__(work, repo, state)
        plan, request, _ = assembly_inputs(db)
        api = GuiApi(db.parent)
        for line in sys.stdin:
            message = {}
            try:
                message = json.loads(line)
                operation = message["operation"]
                if operation == "init":
                    reply = {"code": 0, "data": {"plan": copy.deepcopy(plan), "request": copy.deepcopy(request),
                                                   "caps": api.plan_capabilities()["data"]}}
                elif operation == "query":
                    if message["request"].get("action") != "periodic_attack_profile":
                        raise ValueError("Only periodic source queries are allowed")
                    reply = api.query_runtime(message["request"])
                elif operation == "assemble":
                    reply = api.assemble_periodic_source(message["plan"], message["request"])
                elif operation == "compose":
                    reply = api.compose_equipment_plan(message["plan"])
                elif operation == "calc":
                    reply = api.run_plan_calc(message["plan"])
                else:
                    raise ValueError("Unknown bridge operation")
            except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
                reply = {"code": 1, "message": str(exc), "error_type": type(exc).__name__}
            print(json.dumps({"id": message.get("id"), **reply}, ensure_ascii=False, allow_nan=False), flush=True)
    finally:
        patches.undo()


if __name__ == "__main__":
    main()
