"""Independent GUI JSONL bridge; one isolated real source DB, no query/engine mocks."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(ROOT / "tests")]

import pytest
from test_attack_source_assembly_review import empty_plan
from test_pipeline import fake_repo, stub_fetcher
from test_runtime_attack_metadata import metadata_db

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.attack_source_assembly import assemble_attack_source
from genshin_app.equipment.service import plan_capabilities
from genshin_db.runtime_queries import query_runtime


def main():
    work = Path(sys.argv[1]).resolve()
    allowed = ROOT / "outputs" / "engine-continuation-20261002" / "ec21-research"
    if not work.is_relative_to(allowed.resolve()) or work.exists():
        raise ValueError("A new isolated EC21 review directory is required")
    work.mkdir(parents=True)
    patch = pytest.MonkeyPatch()
    repo = fake_repo.__wrapped__(work)
    state = stub_fetcher.__wrapped__(repo, patch)
    db = metadata_db.__wrapped__(work, repo, state)
    plan = empty_plan(db)
    original_sha = hashlib.sha256(db.read_bytes()).hexdigest()
    try:
        for line in sys.stdin:
            item = {}
            try:
                item = json.loads(line)
                op = item["operation"]
                if op == "init":
                    data = {"plan": plan, "caps": plan_capabilities(), "database_sha": original_sha}
                elif op == "query":
                    if item["request"].get("action") != "attack_metadata_profile":
                        raise ValueError("Only bounded metadata discovery is allowed")
                    data = query_runtime(db, item["request"])
                elif op == "assemble":
                    data = assemble_attack_source(item["plan"], item["request"], db)
                elif op == "compose":
                    data = compose_equipment_plan(item["plan"], db)
                elif op == "calc":
                    data = run_plan_calc(item["plan"], db)
                elif op == "database_sha":
                    data = {"before": original_sha, "after": hashlib.sha256(db.read_bytes()).hexdigest()}
                else:
                    raise ValueError("Unknown independent bridge operation")
                response = {"id": item["id"], "code": 0, "data": data}
            except (KeyError, ValueError, OSError, TypeError) as exc:
                response = {"id": item.get("id"), "code": 1, "message": str(exc), "error_type": type(exc).__name__}
            print(json.dumps(response, ensure_ascii=False, allow_nan=False), flush=True)
    finally:
        patch.undo()


if __name__ == "__main__":
    main()
