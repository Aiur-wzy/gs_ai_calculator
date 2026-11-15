"""Read-only source review for an App static-inclusion profile, never installation.

Run with --db-path and a fresh --output-dir. The matrix and material archive are
review evidence; this script does not register profiles or modify the database.
"""
from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import json
import math
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from genshin_app.equipment import static_inclusion as compiler
from genshin_app.equipment.static_inclusion_sources import _read_material
from genshin_db import equipment_queries, queries, runtime_queries
from genshin_db.equipment import PROPERTIES
from genshin_db.runtime_catalog import fingerprint


def file_sha(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def independent_properties(material):
    """Cross-check raw attributes against the DB's public unit catalogue.

    This does not grant admission and intentionally doesn't call the compiler's
    raw-affix/property helpers or use its already-produced declarations.
    """
    raw = material["source"]["affix"]["record"]
    if "openConfig" in raw or len(raw.get("paramList", [])) != 8:
        return None
    if any(type(v) not in (int, float) or v != 0
           or type(v) is float and math.copysign(1, v) < 0 for v in raw["paramList"]):
        return None
    rows, seen = [], set()
    for index, prop in enumerate(raw.get("addProps", [])):
        if prop == {}:
            continue
        code, value = prop.get("propType"), prop.get("value")
        unit = PROPERTIES.get(code, {}).get("unit")
        if (set(prop) != {"propType", "value"} or code in seen or unit not in {"flat", "ratio"}
                or type(value) not in (int, float) or not math.isfinite(value) or value <= 0):
            return None
        rows.append({"source_index": index, "prop_type": code, "value": value, "unit": unit})
        seen.add(code)
    return rows or None


def review(db_path, version_line="release", profile_version=3):
    if type(profile_version) is not int or profile_version < 3:
        raise ValueError("new source reviews require an explicit profile version >= 3")
    before = file_sha(db_path)
    matrix, materials = [], []
    with equipment_queries.equipment_read_session(db_path):
        meta = queries.get_meta(db_path)
        snapshot = runtime_queries.query_runtime(db_path, {"action": "status", "version_line": version_line})
        scope_keys = ("version_line", "upstream_commit", "schema_version", "indexer_version", "snapshot_id",
                      "manifest_fingerprint", "normalization", "normalization_state", "source_capability_contract",
                      "source_capability_state", "source_capabilities")
        profile = copy.deepcopy(compiler.PROFILE)
        profile.update(id=f"equipment-static-included-v{profile_version}", version=profile_version,
            upstream_commit=snapshot["upstream_commit"], indexer_version=snapshot["indexer_version"],
            snapshot_scope={key: copy.deepcopy(snapshot[key]) for key in scope_keys if key in snapshot})
        page = 1
        while True:
            listing = equipment_queries.list_artifact_sets(db_path, version_line, catalog_scope="all", page=page, page_size=100)
            for item in listing["items"]:
                detail = equipment_queries.get_artifact_set_detail(db_path, item["id"], version_line)
                for relation in detail["effects"]:
                    effect = relation.get("effect") or {}
                    owner = {"kind": "artifact_set", "set_id": item["id"],
                             "required_count": relation["required_count"], "affix_id": effect.get("affix_id")}
                    row = {"set_id": item["id"], "name": item["name"],
                           "required_count": relation["required_count"], "affix_id": effect.get("affix_id"),
                           "status": "pending", "issues": []}
                    if not effect.get("affix_id"):
                        row["issues"] = [{"code": "relation_missing", "path": "effect.affix_id"}]
                        matrix.append(row)
                        continue
                    plan = {"equipment_inputs": {"review": {"mode": "panel", "panel_policy": "static_included",
                        "data_version": detail["data_version"], "artifact_sets": [{"set_id": item["id"],
                            "count": relation["required_count"]}]}},
                        "manual_template": {"characters": [{"id": "review", "stats": {}}]}}
                    req = {"provider_character_id": "review", "owner": owner, "effect": effect}
                    try:
                        material = _read_material(plan, req, db_path)
                        materials.append(material)
                        row["material_fingerprint"] = fingerprint(material)
                        declaration, _ = compiler._compile(material, profile)
                        expected = independent_properties(material)
                        actual = [{key: prop[key] for key in ("source_index", "prop_type", "value", "unit")}
                                  for prop in declaration["properties"]]
                        if not expected or expected != actual or declaration["numeric_application"] is not False:
                            raise ValueError("independent property/unit comparison failed")
                        row.update(status="shape_ready", properties=actual, numeric_application=False)
                    except compiler._Pending as exc:
                        row["issues"] = [{"code": exc.code, "path": exc.path}]
                    except (ValueError, TypeError, KeyError) as exc:
                        row["issues"] = [{"code": "source_review_failed", "path": "material", "detail": str(exc)[:512]}]
                    matrix.append(row)
            if page * listing["page_size"] >= listing["total"]:
                break
            page += 1
    after = file_sha(db_path)
    if before != after:
        raise ValueError("database changed during read-only review")
    result = {"contract": "static-inclusion-profile-review-v1",
        "verification_scope": "source_shape_and_independent_units_no_source_authority",
        "database_sha256_before": before, "database_sha256_after": after, "database_unchanged": True,
        "database_fingerprint": fingerprint(meta), "snapshot": snapshot,
        "candidate_profile": profile, "counts": dict(Counter(row["status"] for row in matrix)),
        "pending_reasons": dict(Counter(issue["code"] for row in matrix for issue in row["issues"])),
        "matrix": matrix, "materials_fingerprint": fingerprint(materials)}
    return result, materials


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--version-line", choices=("release", "beta"), default="release")
    parser.add_argument("--profile-version", type=int, default=3,
                        help="review identity; choose a new version for a new snapshot")
    args = parser.parse_args()
    if args.output_dir.exists():
        parser.error("output directory must be new; preserve previous review evidence")
    report, materials = review(args.db_path.resolve(strict=True), args.version_line, args.profile_version)
    args.output_dir.mkdir(parents=True)
    (args.output_dir / "review.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
    (args.output_dir / "materials.json.gz").write_bytes(gzip.compress(
        json.dumps(materials, ensure_ascii=False, separators=(",", ":")).encode("utf8"), mtime=0))
    print(json.dumps({"counts": report["counts"], "pending_reasons": report["pending_reasons"],
                      "database_unchanged": report["database_unchanged"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
