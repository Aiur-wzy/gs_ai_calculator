"""Carry reviewed reaction qualifications across verified database snapshots."""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from hashlib import sha256
from importlib import resources

CATALOG_META_KEY = "special_reaction_capability_catalog"
CATALOG_REVIEW_META_KEY = "special_reaction_capability_catalog_review"
CATALOG_BUILD_VERSION = 1
_SOURCES = (
    "ExcelBinOutput/AvatarExcelConfigData.json",
    "ExcelBinOutput/AvatarSkillDepotExcelConfigData.json",
    "ExcelBinOutput/ProudSkillExcelConfigData.json",
    "TextMap/TextMap_MediumCHS.json",
)
_LEGACY = {
    "f11cf545a7eee32a29999f960ebfba305f896ed3": "special-reaction-capabilities.json",
    "6044f5aae7dd79fbbe15c5748ef99b654f6132b7": "special-reaction-capabilities-7.2.52.json",
}
_LINK = re.compile(r"\{LINK#[^{}]+\}|\{/LINK\}")
_STYLE = re.compile(r"</?(?:color|i|b)(?:=[^>]+)?>")
_REPLACEMENT_PHRASES = (
    "转化为月感电", "转化为月绽放", "转化为月结晶", "转化为星扩散", "转化为星超导",
    "转为月感电", "转为月绽放", "转为月结晶", "转为星扩散", "转为星超导",
    "转换为月感电", "转换为月绽放", "转换为月结晶", "转换为星扩散", "转换为星超导",
)


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _fingerprint(value):
    return sha256(_encoded(value).encode()).hexdigest()


def _without(value, key):
    return {name: item for name, item in value.items() if name != key}


def _valid_seed(value, commit):
    if (not isinstance(value, dict) or value.get("schema_version") != 1
            or value.get("source_upstream_commit") != commit
            or value.get("version_line") != "release" or value.get("status") != "reviewed"
            or value.get("collection_id") != "native.special-reaction-capabilities.v1"
            or type(value.get("revision")) is not int or value["revision"] < 1
            or not isinstance(value.get("entries"), list) or not value["entries"]
            or _fingerprint(_without(value, "content_hash")) != value.get("content_hash")
            or any(not isinstance(row, dict)
                   or _fingerprint(_without(row, "entry_fingerprint")) != row.get("entry_fingerprint")
                   for row in value["entries"])):
        raise ValueError("reaction capability catalog seed identity is invalid")
    return value


def _legacy(commit):
    name = _LEGACY.get(commit)
    if name is None:
        return None
    raw = resources.files("genshin_db").joinpath("resources", "mechanics", name)
    return _valid_seed(json.loads(raw.read_bytes()), commit)


def _metadata(conn, key):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def _rows(conn, path, key=None):
    row = conn.execute("SELECT data_json FROM raw_table WHERE source_file=?",
                       (path.rsplit("/", 1)[1].removesuffix(".json"),)).fetchone()
    if row is None:
        return {}
    value = json.loads(row[0])
    if key is None:
        return value if isinstance(value, dict) else {}
    if not isinstance(value, list):
        return {}
    counts = {}
    unique = {}
    for record in value:
        identity = record.get(key) if isinstance(record, dict) else None
        if type(identity) is not int:
            continue
        counts[identity] = counts.get(identity, 0) + 1
        unique[identity] = record
    return {identity: record for identity, record in unique.items() if counts[identity] == 1}


def _evidence_source(conn):
    return tuple(_rows(conn, path, key) for path, key in zip(
        _SOURCES, ("id", "id", "proudSkillId", None), strict=True,
    ))


def _binding(source, entry):
    avatars, depots, proud_skills, text_map = source
    avatar = avatars.get(entry["character_id"], {})
    depot_id = entry["required_skill_depot_id"]
    depot = depots.get(depot_id, {})
    if entry["form_selection_required"]:
        identity_matches = depot_id in (avatar.get("candSkillDepotIds") or [])
    else:
        identity_matches = depot_id == avatar.get("skillDepotId")
    if not identity_matches:
        return None
    evidence = entry["evidence"]
    group_id = evidence["proud_skill_group_id"]
    skill = proud_skills.get(evidence["proud_skill_id"], {})
    openings = [row for row in (depot.get("inherentProudSkillOpens") or [])
                if isinstance(row, dict) and row.get("proudSkillGroupId") == group_id]
    if len(openings) != 1 or skill.get("proudSkillGroupId") != group_id:
        return None
    description_hash = str(skill.get("descTextMapHash"))
    description = text_map.get(description_hash)
    if not isinstance(description, str) or not description.strip() or not description_hash.isdigit():
        return None
    return {"opening": openings[0], "open_config": skill.get("openConfig"),
            "proud_skill_type": skill.get("proudSkillType"),
            "description": _LINK.sub("", description),
            "description_hash": description_hash}


def _changes(before, after, entry):
    if before is None:
        return "base_binding_missing"
    if before["description_hash"] != entry["evidence"]["desc_text_map_hash"]:
        return "base_description_reference_changed"
    if after is None:
        return "target_binding_missing"
    if _without(before, "description_hash") != _without(after, "description_hash"):
        return "target_qualification_evidence_changed"
    return None


def _discovered(source, known_ids):
    avatars, depots, proud_skills, text_map = source
    groups = {}
    for skill in proud_skills.values():
        text = text_map.get(str(skill.get("descTextMapHash")))
        plain = _STYLE.sub("", _LINK.sub("", text)) if isinstance(text, str) else ""
        phrases = [phrase for phrase in _REPLACEMENT_PHRASES if phrase in plain]
        if phrases:
            groups.setdefault(skill.get("proudSkillGroupId"), []).append({
                "proud_skill_id": skill["proudSkillId"], "description_hash": str(skill["descTextMapHash"]),
                "phrases": phrases,
            })
    found = []
    for character_id, avatar in sorted(avatars.items()):
        if character_id in known_ids:
            continue
        depots_for_avatar = {avatar.get("skillDepotId"), *(avatar.get("candSkillDepotIds") or [])}
        for depot_id in sorted(d for d in depots_for_avatar if type(d) is int):
            for opening in depots.get(depot_id, {}).get("inherentProudSkillOpens") or []:
                if not isinstance(opening, dict):
                    continue
                for match in groups.get(opening.get("proudSkillGroupId"), []):
                    found.append({"character_id": character_id, "skill_depot_id": depot_id,
                                  **match, "status": "pending"})
    return found


def publish_capability_catalog(conn, base_db, target_version, source_files):
    """Write declaration and audit during a pinned candidate transaction.

    Source hashes are from the upstream manifest verified by the caller.
    Previous support is retained only for the same reviewed passive path.
    Discoveries are review leads with zero execution authority.
    """
    commit = _metadata(conn, "upstream_commit")
    seed = None
    target_source = _evidence_source(conn)
    base_commit = commit
    base_source = target_source
    if base_db is not None:
        with closing(sqlite3.connect(base_db.resolve().as_uri() + "?mode=ro", uri=True)) as base:
            base_commit = _metadata(base, "upstream_commit")
            payload = _metadata(base, CATALOG_META_KEY)
            seed = _valid_seed(json.loads(payload), base_commit) if payload else _legacy(base_commit)
            if seed:
                base_source = _evidence_source(base)
    elif commit:
        seed = _legacy(commit)
    # An unreviewed lineage offers discovery only, including the first full
    # build of a snapshot. Release builds start from an accepted base.
    entries = []
    evidence = []
    if seed is not None and all(path in source_files for path in _SOURCES):
        for previous in seed["entries"]:
            old = _binding(base_source, previous)
            new = _binding(target_source, previous)
            issue = _changes(old, new, previous)
            row = {**previous, "evidence": dict(previous["evidence"])}
            if new:
                row["evidence"]["desc_text_map_hash"] = new["description_hash"]
            if issue:
                row["runtime_support_status"] = "pending"
            row["entry_fingerprint"] = _fingerprint(_without(row, "entry_fingerprint"))
            entries.append(row)
            evidence.append({"character_id": row["character_id"], "status": row["runtime_support_status"],
                             "reason": issue or "unchanged_qualification_evidence",
                             "base_description_hash": old["description_hash"] if old else None,
                             "target_description_hash": new["description_hash"] if new else None,
                             "normalized_description_sha256": (
                                 sha256(new["description"].encode()).hexdigest() if new else None),
                            })
    review = {"contract": "genshin-special-reaction-catalog-build-v1", "base_commit": base_commit,
              "target_commit": commit, "rows": evidence,
              "discoveries": _discovered(target_source, {e["character_id"] for e in entries})}
    if not entries:
        conn.execute("DELETE FROM meta WHERE key=?", (CATALOG_META_KEY,))
    else:
        catalog = {**seed, "revision": seed["revision"] + (commit != base_commit),
                   "source_upstream_commit": commit, "source_target_version": target_version,
                   "source_files": {path: source_files[path]["sha256"] for path in _SOURCES},
                   "entries": entries}
        catalog["content_hash"] = _fingerprint(_without(catalog, "content_hash"))
        conn.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (CATALOG_META_KEY, _encoded(catalog)))
    conn.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (CATALOG_REVIEW_META_KEY, _encoded(review)))
    return {"supported": sum(e["status"] == "supported" for e in evidence),
            "pending": sum(e["status"] == "pending" for e in evidence),
            "discoveries": len(review["discoveries"])}
