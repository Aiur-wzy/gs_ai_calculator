"""Read-only appendix ownership and navigation over existing curated identities.

No knowledge text is interpreted or returned here.  Callers supply one read
transaction, so discovery, ownership and category counts describe one snapshot.
"""
from __future__ import annotations

from collections import defaultdict

from genshin_db.targets import related_targets

CONTRACT = "agent-character-appendix-index-v1"
STATUSES = ("approved", "pending")


def annotation_owner_scope(connection, target_type, target_id, version_line):
    """Resolve exact stored ownership, including skills stored as extra_info."""
    tables = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    characters, weapons, artifact_sets = set(), set(), set()
    if target_type == "character":
        characters.add(target_id)
    elif target_type in {"skill", "talent"} and target_type in tables:
        characters.update(row[0] for row in connection.execute(
            f"SELECT DISTINCT character_id FROM {target_type} "
            "WHERE id=? AND version_line=? AND character_id IS NOT NULL",
            (target_id, version_line)))
    elif target_type == "skill_param" and "skill" in tables:
        characters.update(row[0] for row in connection.execute(
            "SELECT DISTINCT character_id FROM skill WHERE proud_skill_group_id=? "
            "AND version_line=? AND character_id IS NOT NULL", (target_id, version_line)))

    if "extra_info" in tables:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(extra_info)")}
        if target_type == "skill" and "source_id" in columns:
            characters.update(row[0] for row in connection.execute(
                "SELECT DISTINCT target_id FROM extra_info WHERE target_type='character' "
                "AND info_type='sub_skill' AND source_id=? AND version_line=? "
                "AND target_id IS NOT NULL", (target_id, version_line)))
        if target_type in {"skill_param", "inherent_skill"} and "proud_skill_group_id" in columns:
            where = ("target_type='character' AND proud_skill_group_id=? "
                     "AND version_line=? AND target_id IS NOT NULL")
            if target_type == "inherent_skill":
                where += " AND info_type='inherent_skill'"
            characters.update(row[0] for row in connection.execute(
                f"SELECT DISTINCT target_id FROM extra_info WHERE {where}",
                (target_id, version_line)))

    if target_type == "weapon":
        weapons.add(target_id)
    elif target_type == "equip_affix":
        for table, column, output in (("weapon_effect", "weapon_id", weapons),
                                      ("artifact_set_effect", "set_id", artifact_sets)):
            if table in tables:
                output.update(row[0] for row in connection.execute(
                    f"SELECT DISTINCT {column} FROM {table} WHERE affix_id=? "
                    f"AND version_line=? AND {column} IS NOT NULL", (target_id, version_line)))
    if target_type == "artifact_set":
        artifact_sets.add(target_id)
    elif target_type == "artifact" and "artifact" in tables:
        artifact_sets.update(row[0] for row in connection.execute(
            "SELECT DISTINCT set_id FROM artifact WHERE id=? AND version_line=? "
            "AND set_id IS NOT NULL", (target_id, version_line)))
    return {"target": {"target_type": target_type, "target_id": target_id},
            "character_ids": sorted(characters), "weapon_ids": sorted(weapons),
            "artifact_set_ids": sorted(artifact_sets)}


def _discovery_sources(connection, character_id, version_line):
    sources = defaultdict(list)
    issues = []

    def add(kind, ident, table, row_id, field):
        if type(ident) is int and ident > 0:
            sources[(kind, ident, version_line)].append(
                {"table": table, "record_id": row_id, "field": field})

    add("character", character_id, "character", character_id, "id")
    for row in connection.execute(
            "SELECT id,proud_skill_group_id FROM skill WHERE character_id=? AND version_line=?",
            (character_id, version_line)):
        add("skill", row[0], "skill", row[0], "id")
        add("skill_param", row[1], "skill", row[0], "proud_skill_group_id")
    for row in connection.execute(
            "SELECT id FROM talent WHERE character_id=? AND version_line=?", (character_id, version_line)):
        add("talent", row[0], "talent", row[0], "id")
    columns = {row[1] for row in connection.execute("PRAGMA table_info(extra_info)")}
    if columns:
        source = "source_id" if "source_id" in columns else "NULL"
        group = "proud_skill_group_id" if "proud_skill_group_id" in columns else "NULL"
        for row in connection.execute(
                f"SELECT id,info_type,{source},{group} FROM extra_info "
                "WHERE target_type='character' AND target_id=? AND version_line=?",
                (character_id, version_line)):
            if row[1] == "sub_skill":
                add("skill", row[2], "extra_info", row[0], "source_id")
                if type(row[2]) is not int or row[2] <= 0:
                    issues.append({"code": "sub_skill_source_id_unavailable",
                                   "table": "extra_info", "record_id": row[0]})
            add("skill_param", row[3], "extra_info", row[0], "proud_skill_group_id")
            if row[1] == "inherent_skill":
                add("inherent_skill", row[3], "extra_info", row[0], "proud_skill_group_id")
                if type(row[3]) is not int or row[3] <= 0:
                    issues.append({"code": "inherent_skill_group_id_unavailable",
                                   "table": "extra_info", "record_id": row[0]})
    else:
        issues.append({"code": "extra_info_dataset_unavailable", "table": "extra_info", "record_id": 0})
    for rows in sources.values():
        rows.sort(key=lambda row: (row["table"], row["record_id"], row["field"]))
    issues.sort(key=lambda row: (row["code"], row["record_id"]))
    return sources, issues


def character_appendix_index(connection, character_id, version_line):
    """Enumerate existing identities and public categories, never appendix bodies."""
    result = {"contract_id": CONTRACT, "schema_version": 1,
              "db_character_id": character_id, "version_line": version_line,
              "status": "not_found", "character_name": None,
              "execution_authority": "none", "body_read": False,
              "targets": [], "nonempty_targets": [], "issues": [],
              "coverage": {"targets": 0, "nonempty_targets": 0,
                           "total_by_status": {status: 0 for status in STATUSES},
                           "unreadable_categories": 0, "enumeration_complete": False}}
    character = connection.execute(
        "SELECT name FROM character WHERE id=? AND version_line=?", (character_id, version_line)).fetchone()
    if character is None:
        result["issues"].append({"code": "character_not_found"})
        return result
    result["character_name"] = character[0]
    identities = related_targets(connection, character_id, version_line)
    sources, discovery_issues = _discovery_sources(connection, character_id, version_line)
    result["issues"].extend(discovery_issues)
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    counts = defaultdict(list)
    if "annotation" in tables:
        for row in connection.execute(
                "SELECT target_type,target_id,status,COALESCE(category,''),COUNT(*) FROM annotation "
                "WHERE status IN ('approved','pending') AND (version_line IS NULL OR version_line=?) "
                "GROUP BY target_type,target_id,status,COALESCE(category,'') "
                "ORDER BY target_type,target_id,status,COALESCE(category,'')", (version_line,)):
            if (row[0], row[1], version_line) in identities:
                counts[(row[0], row[1])].append(
                    {"status": row[2], "category": row[3], "count": row[4],
                     "body_query_supported": bool(row[3])})
    else:
        result["issues"].append({"code": "appendix_dataset_unavailable"})
    for kind, ident, line in sorted(identities):
        categories = counts[(kind, ident)]
        owner = annotation_owner_scope(connection, kind, ident, line)
        target = {"target_type": kind, "target_id": ident, "version_line": line,
                  "discovery_sources": sources[(kind, ident, line)],
                  "owner_scope": owner, "categories": categories}
        result["targets"].append(target)
        if not target["discovery_sources"]:
            result["issues"].append({"code": "target_discovery_evidence_missing",
                                     "target_type": kind, "target_id": ident})
        if character_id not in owner["character_ids"]:
            result["issues"].append({"code": "target_owner_unresolved",
                                     "target_type": kind, "target_id": ident})
        if categories:
            result["nonempty_targets"].append({key: target[key] for key in
                                               ("target_type", "target_id", "version_line", "categories")})
        for category in categories:
            result["coverage"]["total_by_status"][category["status"]] += category["count"]
            if not category["body_query_supported"]:
                result["coverage"]["unreadable_categories"] += 1
                result["issues"].append({"code": "appendix_category_not_queryable",
                                         "target_type": kind, "target_id": ident,
                                         "status": category["status"]})
    result["coverage"].update(targets=len(result["targets"]),
                              nonempty_targets=len(result["nonempty_targets"]),
                              enumeration_complete=not result["issues"])
    result["status"] = "partial" if result["issues"] else "complete"
    return result
