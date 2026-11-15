"""描述来源与旧库回填：只从匹配的 raw 快照读取，不推测跨版本来源。"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from genshin_db.locking import database_write
from genshin_db.validation import version_line as validate_line

_COLOR = re.compile(r"</?color=[^>]*>|</color>")
_COLUMNS = {
    "skill": {"desc_source": "TEXT", "desc_status": "TEXT"},
    "talent": {"desc_source": "TEXT", "desc_status": "TEXT"},
    "extra_info": {
        "source_id": "INTEGER", "proud_skill_group_id": "INTEGER", "name": "TEXT",
        "desc_template": "TEXT", "desc_source": "TEXT", "desc_status": "TEXT",
        "description_variants": "TEXT",
    },
}


def resolve_text(textmap: dict, text_hash) -> str:
    return _COLOR.sub("", textmap.get(str(text_hash), "")) if text_hash else ""


def extra_content(kind_label, name, group_id, desc, group_entries, resolve, *, legacy=False):
    """兼容旧组合文本；新输出保留零槽，legacy 仅用于精确识别旧条目。"""
    parts = [f"{kind_label}「{name}」"]
    if group_id is not None:
        parts[0] += f"（参数组 {group_id}）"
    if desc:
        parts.append(f"描述：{desc}")
    if group_entries:
        descs = next(([resolve(h) for h in e.get("paramDescList", []) if h]
                      for e in group_entries if e.get("paramDescList")), [])
        if descs and any(d.strip() for d in descs):
            label = "参数含义（按序与参数对应）：" if legacy else "参数含义（按 paramN 引用参数序号）："
            parts.append(label + "；".join(descs))
        for entry in sorted(group_entries, key=lambda x: x.get("level", 0)):
            params = ", ".join(f"{p:g}" for p in entry.get("paramList", []) if not legacy or p)
            if params:
                parts.append(f"  Lv{entry.get('level', '?')}: {params}")
    return "\n".join(parts)


def ensure_description_columns(conn: sqlite3.Connection) -> None:
    """调用者拥有事务；ALTER TABLE 与回填一起提交或回滚。"""
    for table, additions in _COLUMNS.items():
        columns = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not columns:
            continue
        for name, kind in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")


def _json(value):
    return json.dumps(value, ensure_ascii=False) if value is not None else None


class DescriptionIndex:
    def __init__(self, raw: dict, commit: str | None):
        self.raw = raw
        self.commit = commit
        self.textmap = raw.get("TextMap_MediumCHS", {})
        self.skills = {s["id"]: s for s in raw.get("AvatarSkillExcelConfigData", [])}
        self.talents = {t["talentId"]: t for t in raw.get("AvatarTalentExcelConfigData", [])}
        self.groups = defaultdict(list)
        for entry in raw.get("ProudSkillExcelConfigData", []):
            self.groups[entry.get("proudSkillGroupId")].append(entry)
        self.main_relations = {"skill": set(), "talent": set()}
        self.extras = defaultdict(list)
        owners = {}
        names = {}
        for avatar in raw.get("AvatarExcelConfigData", []):
            cid, did = avatar["id"], avatar.get("skillDepotId")
            names[cid] = self.resolve(avatar.get("nameTextMapHash"))
            if did and (did not in owners or owners[did] >= 10000900 > cid):
                owners[did] = cid
        for depot in raw.get("AvatarSkillDepotExcelConfigData", []):
            did = depot.get("id")
            if did and did not in owners and did // 100 in (5, 7):
                owners[did] = {5: 10000005, 7: 10000007}[did // 100]
            cid = owners.get(did)
            if cid is None:
                continue
            for sid in [*depot.get("skills", [])[:2], depot.get("energySkill")]:
                if sid:
                    self.main_relations["skill"].add((cid, sid))
            for tid in depot.get("talents", []):
                if tid:
                    self.main_relations["talent"].add((cid, tid))
            for sid in [*depot.get("subSkills", []), *depot.get("skills", [])[2:]]:
                if sid in self.skills:
                    sk = self.skills[sid]
                    self._extra(cid, names.get(cid, ""), "sub_skill", sk,
                                sk.get("proudSkillGroupId"), None)
            for item in depot.get("inherentProudSkillOpens", []):
                gid = item.get("proudSkillGroupId") if isinstance(item, dict) else item
                if gid and self.groups.get(gid):
                    need = item.get("needAvatarPromoteLevel") if isinstance(item, dict) else None
                    self._extra(cid, names.get(cid, ""), "inherent_skill",
                                self.groups[gid][0], gid, need)

    def resolve(self, text_hash):
        return resolve_text(self.textmap, text_hash)

    def describe(self, record, table, id_key):
        text_hash = str(record["descTextMapHash"]) if record.get("descTextMapHash") else None
        desc = self.resolve(text_hash)
        record_id = record.get(id_key)
        if not isinstance(record_id, int) or isinstance(record_id, bool):
            return {"desc_template": desc, "desc_source": None, "desc_status": "legacy_unverified"}
        source = {"table": table, "record_id": record_id, "text_map_hash": text_hash,
                  "language": "CHS", "upstream_commit": self.commit}
        status = "present" if desc else "source_empty"
        if text_hash is not None and text_hash not in self.textmap:
            status = "textmap_missing"
        return {"desc_template": desc, "desc_source": source, "desc_status": status}

    def _extra(self, cid, character_name, kind, record, gid, need):
        inherent = kind == "inherent_skill"
        table = "ProudSkillExcelConfigData" if inherent else "AvatarSkillExcelConfigData"
        id_key = "proudSkillId" if inherent else "id"
        info = self.describe(record, table, id_key)
        name = self.resolve(record.get("nameTextMapHash"))
        entries = self.groups.get(gid, [])
        variants = ([{"level": e.get("level"), **self.describe(e, table, id_key)}
                     for e in sorted(entries, key=lambda e: e.get("level", 0))] if inherent else [])
        values = {"source_id": gid if inherent else record["id"], "proud_skill_group_id": gid,
                  "name": name, **info, "description_variants": variants}
        contents = []
        for legacy in (False, True):
            content = f"[{character_name}] " + extra_content(
                "固有天赋" if inherent else "附属技能", name, gid, info["desc_template"],
                entries, self.resolve, legacy=legacy)
            if need:
                content += f"\n解锁条件：角色突破 {need}"
            contents.append(content)
        values["content"] = contents[0]
        self.extras[cid, kind].append((contents, values))


def _update(conn, table, row, values):
    values = {k: _json(v) if k in ("desc_source", "description_variants") else v
              for k, v in values.items()}
    changed = {k: v for k, v in values.items() if row[k] != v}
    if not changed:
        return 0
    keys = ("id", "character_id", "version_line") if table != "extra_info" else ("id",)
    conn.execute(f"UPDATE {table} SET " + ",".join(f"{k}=?" for k in changed)
                 + " WHERE " + " AND ".join(f"{k}=?" for k in keys),
                 [*changed.values(), *(row[k] for k in keys)])
    return 1


def populate_descriptions(conn, version_line, raw, commit, *, trusted):
    """新建库和迁移共用；不改现有主技能/命座正文，匹配失败只标记不可靠。"""
    index = DescriptionIndex(raw, commit) if trusted else None
    updated = 0
    counts = {}
    for table in _COLUMNS:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
            continue
        counts[table] = Counter()
        for row in conn.execute(f"SELECT * FROM {table} WHERE version_line=?", (version_line,)):
            if table == "extra_info" and row["info_type"] not in ("sub_skill", "inherent_skill"):
                continue
            # 已验证的来源随原快照保留，重复迁移不得拿当前共享 raw 改写另一份历史来源。
            if row["desc_source"] and row["desc_status"] in (
                    "present", "source_empty", "textmap_missing"):
                counts[table][row["desc_status"]] += 1
                continue
            values = {"desc_source": None, "desc_status": "legacy_unverified"}
            if index is not None and table in ("skill", "talent"):
                records = index.skills if table == "skill" else index.talents
                record = records.get(row["id"])
                if record and (row["character_id"], row["id"]) in index.main_relations[table]:
                    info = index.describe(record, "AvatarSkillExcelConfigData" if table == "skill"
                                          else "AvatarTalentExcelConfigData",
                                          "id" if table == "skill" else "talentId")
                    if (row["desc_template"] == info["desc_template"]
                            and row["name"] == index.resolve(record.get("nameTextMapHash"))):
                        values = {k: info[k] for k in ("desc_source", "desc_status")}
            elif index is not None and row["target_type"] == "character":
                candidates = [v for texts, v in index.extras[row["target_id"], row["info_type"]]
                              if row["content"] in texts
                              and (row["source_id"] is None or row["source_id"] == v["source_id"])]
                unique = {_json(v): v for v in candidates}
                if len(unique) == 1:
                    values = next(iter(unique.values()))
            updated += _update(conn, table, row, values)
            counts[table][values["desc_status"]] += 1
    return {"version_line": version_line, "updated_rows": updated, "status_counts": counts}


@database_write
def migrate_descriptions(db_path: Path, version_line: str = "release") -> dict:
    """安全幂等升级；列、来源和组合文本同事务，失败回滚，不触碰数值/标签/自建层。"""
    validate_line(version_line)
    conn = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=rw", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            ensure_description_columns(conn)
            meta = dict(conn.execute("SELECT key,value FROM meta"))
            raw = {r["source_file"]: json.loads(r["data_json"])
                   for r in conn.execute("SELECT source_file,data_json FROM raw_table")}
            raw_line = meta.get("raw_version_line", "release")
            commit = meta.get("raw_upstream_commit", meta.get("upstream_commit"))
            line_commit = meta.get(f"upstream_commit:{version_line}")
            if line_commit is None and version_line == "release":
                line_commit = meta.get("upstream_commit")
            textmap = dict(conn.execute("SELECT hash,text FROM text_map WHERE locale='CHS'"))
            raw_textmap = raw.get("TextMap_MediumCHS", {})
            trusted = (version_line == raw_line and bool(commit) and line_commit == commit
                       and bool(raw_textmap)
                       and all(textmap.get(str(h)) == text for h, text in raw_textmap.items()))
            report = populate_descriptions(conn, version_line, raw, commit, trusted=trusted)
            if meta.get("description_schema_version") != "1":
                conn.execute("INSERT OR REPLACE INTO meta VALUES ('description_schema_version','1')")
            return report
    finally:
        conn.close()
