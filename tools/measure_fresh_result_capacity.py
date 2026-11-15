"""Reproduce optional-evidence envelope handling with one engineering plan.

No GUI, model, real credentials, upstream network or production DB is touched.
The smaller byte limit is process-local and never saved as an App setting.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def measure(count):
    from genshin_app.calculation_delivery_files import LIMITS
    from genshin_app.equipment import new_plan, service
    from genshin_app.equipment.calculation_wire import calculation_wire_sizes
    from genshin_app.equipment.delivery import (
        build_calculation_delivery,
        validate_delivery_calculated,
    )
    from genshin_app.equipment.files import fingerprint, json_bytes
    from genshin_app.equipment.fresh_result_capacity import finish_fresh_result
    from genshin_app.equipment.schema import PlanError

    plan = new_plan()
    plan["plan_id"] = "e781d469-1cde-473c-8830-ceaf87447366"
    plan["manual_template"].update(loop_duration=count + 1,
        characters=[{"id": "a", "name": "容量工程角色", "level": 90,
                     "stats": {"atk": 100, "crit_rate": 0, "crit_dmg": .5}}],
        hits=[{"id": f"h{i}", "t": i + .5, "character": "a", "skill": "工程命中",
               "multiplier": 1, "damage_type_ids": ["damage.elemental_skill"],
               "damage_system": "classic", "element": "cryo"} for i in range(count)])
    db = ROOT / "build/r12-capacity-measurement-absent.db"
    if db.exists():
        raise RuntimeError("Measurement expects no database file at its reserved path")
    def run(limit=None, *, minimum=False):
        candidates = []
        original = service.calculate
        def finish(build):
            def candidate(maximum):
                value = build(maximum)
                candidates.append({"target": maximum, "wire": calculation_wire_sizes(value),
                                   "coverage": copy.deepcopy(value["result"]["damage_explanations"]["coverage"])})
                return value
            return candidate(0) if minimum else finish_fresh_result(candidate)
        with (patch.dict(LIMITS, calculated_bytes=limit or 64 * 1024 * 1024),
              patch("genshin_app.equipment.fresh_result_capacity.finish_fresh_result", finish),
              patch.object(service, "calculate", wraps=original) as calculated):
            started = time.perf_counter()
            value = service.run_plan_calc(copy.deepcopy(plan), db, capture_display=True)
            seconds = time.perf_counter() - started
            assert calculated.call_count == 1
        return value, {"seconds": seconds, "producer_calculations": calculated.call_count,
                       "candidates": candidates, "wire": calculation_wire_sizes(value),
                       "coverage": copy.deepcopy(value["result"]["damage_explanations"]["coverage"])}
    normal, normal_record = run()
    _minimum, minimum_record = run(minimum=True)
    limit = (max(normal_record["wire"].values()) + max(minimum_record["wire"].values())) // 2
    reduced, reduced_record = run(limit)
    numeric = lambda value: [{key: item for key, item in row.items() if key != "explanation_ref"}
                             for row in value["result"]["hits"]]
    assert numeric(normal) == numeric(reduced)
    assert normal["result"]["summary"] == reduced["result"]["summary"]
    assert normal["result"]["damage_explanations"]["status"] == "complete"
    assert reduced["result"]["damage_explanations"]["status"] == "partial"
    assert max(reduced_record["wire"].values()) <= limit
    with patch.dict(LIMITS, calculated_bytes=limit):
        built = build_calculation_delivery(reduced, input_file_sha=fingerprint(plan))
        restored = json.loads(built["artifacts"]["calculated"])["calculated"]
        validate_delivery_calculated(restored)
        assert restored == reduced
    files = ["tools/measure_fresh_result_capacity.py", "src/genshin_app/damage_explanation_reporting.py",
             "src/genshin_app/equipment/service.py", "src/genshin_app/equipment/calculation_sealing.py",
             "src/genshin_app/equipment/fresh_result_capacity.py", "src/genshin_app/equipment/calculation_wire.py",
             "src/genshin_app/equipment/replay_numeric_projection.py", "tests/test_fresh_result_capacity.py"]
    try:
        with patch.dict(LIMITS, calculated_bytes=1024):
            service.run_plan_calc(copy.deepcopy(plan), db, capture_display=True)
    except PlanError as exc:
        exhausted = {"reason": exc.reason, "details": exc.details}
        assert exc.reason == "delivery_size_limit"
    else:
        raise AssertionError("Mandatory material cannot fit a 1 KiB test envelope")
    assert not db.exists()
    return {"recorded_at": datetime.now(UTC).isoformat(),
            "scope": "fresh producer/writer engineering capacity; no gameplay certification or model experiment",
            "input": {"hit_rows": count, "plan_fingerprint": fingerprint(plan), "plan": plan},
            "production_limit_unchanged": 64 * 1024 * 1024, "process_local_test_limit": limit,
            "normal": normal_record, "minimum": minimum_record, "reduced": reduced_record,
            "numeric_and_order_exact": True,
            "reduced_document_budget": reduced["result"]["damage_explanations"]["budget"],
            "actual_artifact_bytes": {role: len(content) for role, content in built["artifacts"].items()},
            "fresh_restored_valid": True, "mandatory_exhaustion": exhausted,
            "files": [{"path": name, "sha256": hashlib.sha256((ROOT / name).read_bytes()).hexdigest()}
                      for name in files], "input_bytes": len(json_bytes(plan))}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--hits", type=int, default=512)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 5 <= args.hits <= 5000:
        parser.error("engineering measurement supports 5..5000 hits")
    value = measure(args.hits)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"path": str(args.output), "normal": value["normal"]["wire"],
                      "reduced": value["reduced"]["wire"], "limit": value["process_local_test_limit"],
                      "explained": value["reduced"]["coverage"]["explained"]}, ensure_ascii=False))
