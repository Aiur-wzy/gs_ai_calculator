"""Reproducible, local NEXT-04 projection/capacity measurement.

Reads historical files without modifying them. It does not start a model, GUI,
database update or gameplay execution and does not certify a gameplay answer.
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

ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def without_explanations(result):
    value = copy.deepcopy(result)
    for key in ("damage_explanations", "damage_explanation_fingerprint"):
        value.pop(key, None)
    for row in value["hits"]:
        row.pop("explanation_ref", None)
    return value


def measure(dense_hits=30000):
    sys.path.insert(0, str(ROOT / "src"))
    from genshin_app.damage_explanation_reporting import (
        assemble_damage_explanations,
        make_explanation_receipt,
        validate_damage_explanations,
    )
    from genshin_engine.core import calculate
    from genshin_engine.damage_explanations import digest, encoded

    sources = {}
    for team in ("ayaka", "diluc"):
        index = json.loads((ROOT / "benchmarks/followup/r9-three-team-sol61-high-20261107/sealed"
                            / team / "delivery.original.json").read_text(encoding="utf8"))
        sources[team] = Path(index["artifacts"]["calculated"]["path"])
    sources["stellar"] = ROOT / ("benchmarks/followup/r10-stellar-sol61-high-20261107/outputs/delivery-r9/"
                                 "generation-c74a7afacfc5401681bf68d345a10227/calculated.json")
    reports = []
    for team, path in sources.items():
        input_sha = sha256(path)
        saved = json.loads(path.read_text(encoding="utf8"))["calculated"]
        template = saved["plan"]["resolved_template"]
        start = time.perf_counter()
        plain = calculate(template, include_damage_explanations=False)
        plain_seconds = time.perf_counter() - start
        start = time.perf_counter()
        result = calculate(template)
        capture_seconds = time.perf_counter() - start
        projected = without_explanations(result)
        assert projected == plain, "Explanation changed existing numeric output or event order"
        for field in ("mechanics_trace", "sampling_trace", "attribute_ledger", "rotation_check",
                      "mechanics", "mechanics_status", "action_event_compilations"):
            if field in saved["result"]:
                result[field] = copy.deepcopy(saved["result"][field])
        start = time.perf_counter()
        assemble_damage_explanations(saved["plan"], result, saved["report"])
        assembly_seconds = time.perf_counter() - start
        receipt = {**{key: saved["report"][key] for key in
                      ("input_fingerprint", "output_fingerprint", "source_fingerprint")},
                   "damage_explanations": make_explanation_receipt(result)}
        validate_damage_explanations(result, receipt, report=saved["report"])
        doc = result["damage_explanations"]
        assert input_sha == sha256(path), "Historical input changed during measurement"
        reports.append({"fixture": team, "input": str(path.resolve()), "input_sha256": input_sha,
            "template_pointer": "/calculated/plan/resolved_template",
            "scope": "saved_resolved_numeric_projection_and_observation_join_only",
            "hits": len(result["hits"]), "saved_sampling_rows": len(result.get("sampling_trace", [])),
            "saved_mechanics_rows": len(result.get("mechanics_trace", [])),
            "plain_core_seconds": plain_seconds, "capture_core_seconds": capture_seconds,
            "observation_assembly_seconds": assembly_seconds,
            "numeric_and_order_identical": True, "numeric_projection_sha256": digest(plain),
            "status": doc["status"], "references_and_saved_sampling_consistent": True,
            "tables": {key: len(doc[key]) for key in ("sources", "panels", "modifiers", "formulas", "events")},
            "explanation_bytes": len(encoded(doc)), "saved_packet_bytes": path.stat().st_size,
            "unsealed_projected_result_bytes": len(encoded(result))})
    template = {"version": 2, "form": "resolved", "id_registry_version": 1,
        "type_definitions": {"damage_types": []}, "loop_duration": 10,
        "enemy": {"level": 90, "resistance": {"default": .1}},
        "characters": [{"id": "a", "level": 90, "stats": {"atk": 100, "crit_rate": 0, "crit_dmg": .5}}],
        "buffs": [], "switches": [], "hits": [
            {"id": f"dense.{index}", "t": round(.2 + index * .0001, 6), "character": "a",
             "skill": "工程技能", "hit_name": "共享工程分量", "element": "cryo", "damage_system": "classic",
             "damage_type_ids": ["damage.elemental_skill"], "multiplier": 1} for index in range(dense_hits)]}
    start = time.perf_counter()
    plain = calculate(template, include_damage_explanations=False)
    plain_seconds = time.perf_counter() - start
    start = time.perf_counter()
    result = calculate(template)
    capture_seconds = time.perf_counter() - start
    assert without_explanations(result) == plain
    start = time.perf_counter()
    validate_damage_explanations(result)
    verification_seconds = time.perf_counter() - start
    doc = result["damage_explanations"]
    result_bytes = len(encoded(result))
    assert result_bytes < 64 * 1024 * 1024
    stress = {"fixture": "dense_synthetic", "scope": "synthetic_core_capacity_only",
        "input_sha256": digest(template), "hits": dense_hits, "plain_core_seconds": plain_seconds,
        "capture_core_seconds": capture_seconds, "verification_seconds": verification_seconds,
        "status": doc["status"], "coverage": doc["coverage"],
        "tables": {key: len(doc[key]) for key in ("sources", "panels", "modifiers", "formulas", "events")},
        "explanation_bytes": len(encoded(doc)), "result_bytes": result_bytes,
        "within_64MiB": True, "references_closed": True, "numeric_and_order_identical": True}
    return {"measured_at": datetime.now(UTC).isoformat(), "contract": "damage-explanation-measurement-v1",
        "disclaimer": "Pure local projection measurement; not a new gameplay experiment, formal acceptance or verification of old mechanics.",
        "hit_scope": "Explicit saved resolved numeric rows; no action, reaction, layer or energy simulation is rerun.",
        "cases": reports, "stress": stress}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dense-hits", type=int, default=30000)
    args = parser.parse_args()
    if not 1 <= args.dense_hits <= 90000:
        parser.error("--dense-hits must keep synthetic rows inside the 10 second input window")
    value = measure(args.dense_hits)
    value["script_sha256"] = sha256(Path(__file__))
    value["source_sha256"] = {str(path): sha256(ROOT / path) for path in (
        "src/genshin_engine/core.py", "src/genshin_engine/formulas.py",
        "src/genshin_engine/damage_explanations.py", "src/genshin_app/damage_explanation_reporting.py")}
    text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf8")
        print(json.dumps({"output": str(args.output.resolve()), "sha256": sha256(args.output)}, ensure_ascii=False))
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
