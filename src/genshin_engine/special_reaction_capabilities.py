"""Frozen database authority for characters that enable native reactions.

The combat runtime uses stable database character IDs.  Display names are
kept only in this reviewed database resource and never participate in engine
dispatch.
"""
from __future__ import annotations

import copy
import hashlib
import json
from functools import lru_cache
from importlib import resources
from typing import Any

from genshin_engine.special_reaction_contracts import REQUIRED_CAPABILITY_TAGS

SPECIAL_CAPABILITY_CATALOG_ID = "native.special-reaction-capabilities.v1"
SPECIAL_CAPABILITY_CATALOG_REVISION = 2
SPECIAL_CAPABILITY_VERSION_LINE = "release"
# The project exposes the reviewed closed-beta (OSCB/CNCB) snapshot through its existing
# ``release`` compatibility line.  Keep that public label stable, but preserve
# the upstream track independently so a beta catalog is never mistaken for an
# OSREL source.
SPECIAL_CAPABILITY_CATALOG_TRACK = "beta"
SPECIAL_CAPABILITY_SNAPSHOT_MATCH = "equipment_snapshot_matches_catalog"
_PENDING_SNAPSHOT_REASONS = {
    "equipment_data_version_missing": "装备输入缺少数据库版本快照",
    "equipment_version_line_mismatch": "装备快照资料线与角色引用或资格目录不一致",
    "equipment_upstream_commit_mismatch": "装备快照提交与资格目录来源提交不一致",
}
_VALID_TAGS = frozenset(REQUIRED_CAPABILITY_TAGS.values())
_TOP_LEVEL_FIELDS = frozenset({
    "schema_version", "collection_id", "revision", "status", "version_line",
    "source_upstream_commit", "source_target_version", "source_files",
    "content_hash", "entries",
})
_ENTRY_FIELDS = frozenset({
    "character_id", "required_skill_depot_id", "form_selection_required",
    "display_name", "capability_tags", "runtime_profile_id",
    "runtime_support_status", "evidence", "entry_fingerprint",
})


def _catalog_hash(data: dict[str, Any]) -> str:
    payload = {
        key: copy.deepcopy(value)
        for key, value in data.items()
        if key != "content_hash"
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@lru_cache(maxsize=1)
def _load_reviewed_special_reaction_capabilities() -> dict[str, Any]:
    path = resources.files("genshin_db").joinpath(
        "resources", "mechanics", "special-reaction-capabilities.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("无法读取运行时数据库中的星月反应角色资格目录。") from exc
    if not isinstance(data, dict) or set(data) != _TOP_LEVEL_FIELDS:
        raise RuntimeError("星月反应角色资格目录字段不完整。")
    if (data.get("schema_version") != 1
            or data.get("collection_id") != SPECIAL_CAPABILITY_CATALOG_ID
            or data.get("revision") != SPECIAL_CAPABILITY_CATALOG_REVISION
            or data.get("status") != "reviewed"
            or data.get("version_line") != SPECIAL_CAPABILITY_VERSION_LINE
            or not isinstance(data.get("source_upstream_commit"), str)
            or len(data["source_upstream_commit"]) != 40
            or not isinstance(data.get("source_target_version"), str)
            or not data["source_target_version"].strip()
            or not isinstance(data.get("source_files"), dict)
            or len(data["source_files"]) != 4
            or any(not isinstance(name, str) or not isinstance(digest, str)
                   or len(digest) != 64
                   for name, digest in data["source_files"].items())):
        raise RuntimeError("星月反应角色资格目录身份无效。")
    if data.get("content_hash") != _catalog_hash(data):
        raise RuntimeError("星月反应角色资格目录内容哈希不匹配。")
    entries = data.get("entries")
    if not isinstance(entries, list) or not entries:
        raise RuntimeError("星月反应角色资格目录必须包含受审条目。")
    seen: set[int] = set()
    for row in entries:
        if not isinstance(row, dict) or set(row) != _ENTRY_FIELDS:
            raise RuntimeError("星月反应角色资格条目字段不完整。")
        cid = row.get("character_id")
        depot_id = row.get("required_skill_depot_id")
        tags = row.get("capability_tags")
        evidence = row.get("evidence")
        entry_payload = {
            key: copy.deepcopy(value) for key, value in row.items()
            if key != "entry_fingerprint"
        }
        entry_fingerprint = hashlib.sha256(json.dumps(
            entry_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")).hexdigest()
        if (isinstance(cid, bool) or not isinstance(cid, int) or cid <= 0
                or cid in seen or not isinstance(row.get("display_name"), str)
                or not row["display_name"].strip()
                or isinstance(depot_id, bool) or not isinstance(depot_id, int)
                or depot_id <= 0
                or not isinstance(row.get("form_selection_required"), bool)
                or not isinstance(tags, list) or not tags
                or len(tags) != len(set(tags)) or not set(tags) <= _VALID_TAGS
                or not isinstance(row.get("runtime_profile_id"), str)
                or not row["runtime_profile_id"].strip()
                or row.get("runtime_support_status") not in ("supported", "pending")
                or not isinstance(evidence, dict)
                or set(evidence) != {
                    "proud_skill_group_id", "proud_skill_id", "desc_text_map_hash"}
                or any(isinstance(evidence[key], bool) or not isinstance(evidence[key], int)
                       or evidence[key] <= 0
                       for key in ("proud_skill_group_id", "proud_skill_id"))
                or not isinstance(evidence["desc_text_map_hash"], str)
                or not evidence["desc_text_map_hash"].isdigit()
                or row.get("entry_fingerprint") != entry_fingerprint):
            raise RuntimeError("星月反应角色资格条目内容无效。")
        seen.add(cid)
    return data


def load_reviewed_special_reaction_capabilities() -> dict[str, Any]:
    return copy.deepcopy(_load_reviewed_special_reaction_capabilities())


def special_reaction_capability_catalog() -> dict[str, Any]:
    """Return a detached, validated catalog snapshot."""
    return copy.deepcopy(load_reviewed_special_reaction_capabilities())


def special_reaction_capability_entry(db_character_id: int) -> dict[str, Any] | None:
    catalog = load_reviewed_special_reaction_capabilities()
    for row in catalog["entries"]:
        if row["character_id"] == db_character_id:
            return copy.deepcopy(row)
    return None


def verified_capability_tags(row: dict[str, Any]) -> tuple[str, ...]:
    """Return only tags proven by the exact frozen catalog snapshot."""
    catalog = load_reviewed_special_reaction_capabilities()
    if (row.get("status") != "confirmed"
            or row.get("projection_reason_code") != SPECIAL_CAPABILITY_SNAPSHOT_MATCH
            or row.get("catalog_track") != SPECIAL_CAPABILITY_CATALOG_TRACK
            or row.get("catalog_source_upstream_commit") != catalog["source_upstream_commit"]
            or row.get("equipment_version_line") != catalog["version_line"]
            or row.get("equipment_upstream_commit") != catalog["source_upstream_commit"]
            or row.get("catalog_id") != catalog["collection_id"]
            or row.get("catalog_revision") != catalog["revision"]
            or row.get("catalog_content_hash") != catalog["content_hash"]
            or row.get("version_line") != catalog["version_line"]):
        return ()
    entry = special_reaction_capability_entry(row.get("db_character_id"))
    requested = row.get("capability_tags")
    if (entry is None or not isinstance(requested, list)
            or entry["runtime_support_status"] != "supported"
            or row.get("skill_depot_id") != entry["required_skill_depot_id"]
            or row.get("runtime_profile_id") != entry["runtime_profile_id"]
            or row.get("catalog_entry_fingerprint") != entry["entry_fingerprint"]):
        return ()
    if not set(requested) <= set(entry["capability_tags"]):
        return ()
    return tuple(requested)


def project_special_reaction_party_capabilities(
    equipment_inputs: dict[str, Any],
) -> list[dict[str, Any]]:
    """Project trusted capability rows from App character references.

    The returned rows are derived data.  The Agent chooses replacement
    switches and provider actor IDs, while the App supplies the stable DB
    identity and exact reviewed capability tags.
    """
    return project_special_reaction_party_capabilities_detailed(equipment_inputs)["rows"]


def project_special_reaction_party_capabilities_detailed(
    equipment_inputs: dict[str, Any], *, actor_ids=None,
) -> dict[str, Any]:
    """Explain missing projections without weakening the trusted catalog match.

    Party membership is supplied by the caller's current input, never inferred
    from display names. The compatibility function still returns identical rows.
    """
    catalog = load_reviewed_special_reaction_capabilities()
    entries = {entry["character_id"]: entry for entry in catalog["entries"]}
    rows: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for actor_id in sorted(set(equipment_inputs) | set(actor_ids or ())):
        escaped = str(actor_id).replace("~", "~0").replace("/", "~1")
        path = f"/equipment_inputs/{escaped}"

        def explain(code, field, actual, expected, message, category="request_error"):
            diagnostics.append({"character_id": actor_id, "code": code, "category": category,
                "input_ref": path + field, "actual": copy.deepcopy(actual),
                "expected": copy.deepcopy(expected), "message": message,
                "recovery": {"action": "inspect_selected_character_identity", "retry_same_request": False}})

        equipment = equipment_inputs.get(actor_id)
        reference = equipment.get("character_ref") if isinstance(equipment, dict) else None
        if not isinstance(reference, dict) or type(reference.get("db_character_id")) is not int:
            explain("special_capability_identity_missing", "/character_ref", reference,
                    {"kind": "database_character_reference"},
                    "尚未提供该角色的数据库身份，不能据此判断数据库或反应能力缺失。")
            continue
        entry = entries.get(reference.get("db_character_id"))
        if entry is None:
            explain("special_capability_catalog_entry_missing", "/character_ref/db_character_id",
                    reference["db_character_id"], {"catalog_id": catalog["collection_id"]},
                    "此角色身份未被当前资格自动投影目录覆盖；这不表示角色资料不存在。",
                    "engine_capability_missing")
            continue
        if entry["runtime_support_status"] != "supported":
            explain("special_capability_catalog_entry_pending", "/character_ref/db_character_id",
                    reference["db_character_id"], {"runtime_support_status": "supported"},
                    "资格目录仍将该条目标为待支持，不能自行填写受信资格绕过。",
                    "engine_capability_missing")
            continue
        selected_depot = reference.get("skill_depot_id")
        if ((entry["form_selection_required"] and selected_depot != entry["required_skill_depot_id"])
                or selected_depot not in (None, entry["required_skill_depot_id"])):
            explain("special_capability_skill_depot_mismatch", "/character_ref/skill_depot_id",
                    selected_depot, entry["required_skill_depot_id"],
                    "所选技能组未匹配该角色的资格条目；核对实际形态，不修改用户命座。")
            continue
        data_version = equipment.get("data_version")
        equipment_version_line = (
            data_version.get("version_line") if isinstance(data_version, dict) else None)
        equipment_upstream_commit = (
            data_version.get("upstream_commit") if isinstance(data_version, dict) else None)
        if not isinstance(data_version, dict):
            reason_code = "equipment_data_version_missing"
        elif (reference.get("version_line") != catalog["version_line"]
              or equipment_version_line != reference.get("version_line")):
            reason_code = "equipment_version_line_mismatch"
        elif equipment_upstream_commit != catalog["source_upstream_commit"]:
            reason_code = "equipment_upstream_commit_mismatch"
        else:
            reason_code = SPECIAL_CAPABILITY_SNAPSHOT_MATCH
        status = "confirmed" if reason_code == SPECIAL_CAPABILITY_SNAPSHOT_MATCH else "pending"
        if status != "confirmed":
            explain(reason_code, "/data_version", data_version,
                    {"version_line": catalog["version_line"],
                     "upstream_commit": catalog["source_upstream_commit"]},
                    _PENDING_SNAPSHOT_REASONS[reason_code])
        if status == "confirmed":
            source = (
                "运行时数据库受审星月反应资格目录；装备快照与目录来源提交完全一致；"
                f"{catalog['source_target_version']} / 上游提交 "
                f"{catalog['source_upstream_commit']}"
            )
        else:
            source = (
                f"{_PENDING_SNAPSHOT_REASONS[reason_code]}；"
                f"角色资料线={reference.get('version_line')!r}，"
                f"装备资料线={equipment_version_line!r}，"
                f"装备提交={equipment_upstream_commit!r}，"
                f"目录兼容资料线={catalog['version_line']!r}，"
                f"目录来源提交={catalog['source_upstream_commit']}"
            )
        rows.append({
            "character_id": actor_id,
            "db_character_id": entry["character_id"],
            "skill_depot_id": entry["required_skill_depot_id"],
            "capability_tags": copy.deepcopy(entry["capability_tags"]),
            "runtime_profile_id": entry["runtime_profile_id"],
            "catalog_entry_fingerprint": entry["entry_fingerprint"],
            "version_line": catalog["version_line"],
            "catalog_id": catalog["collection_id"],
            "catalog_revision": catalog["revision"],
            "catalog_content_hash": catalog["content_hash"],
            "catalog_track": SPECIAL_CAPABILITY_CATALOG_TRACK,
            "catalog_source_upstream_commit": catalog["source_upstream_commit"],
            "equipment_version_line": equipment_version_line,
            "equipment_upstream_commit": equipment_upstream_commit,
            "projection_reason_code": reason_code,
            "status": status,
            "source": source,
        })
    return {"rows": rows, "diagnostics": diagnostics,
            "catalog_id": catalog["collection_id"], "catalog_content_hash": catalog["content_hash"]}
