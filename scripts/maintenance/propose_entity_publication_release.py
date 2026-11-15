"""Prepare the exact entity audit and three appendix additions for r4 → r5 review."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path

from genshin_db.content_change_set import create_change_set, project_content_fingerprint
from genshin_db.publication_evidence import ENTITY_TYPES

BASE = "db-7.2.50-r4"
TARGET = "db-7.2.50-r5"
BASE_SHA = "825e7dd6590f85c8a37763d89850072d22231009d0853b265b005b849e3a9dc2"
UPSTREAM_COMMIT = "f11cf545a7eee32a29999f960ebfba305f896ed3"
COMMUNITY_COMMIT = "8e5badd60b5d894284ccc927a56c9683529d2592"
COMMUNITY_ROOT = f"https://github.com/theBowja/genshin-db/blob/{COMMUNITY_COMMIT}/"
CHECKED_ON = "2026-11-14"
SCOPE = "截至2026-11-14，原神7.2公服曾经上线的角色或装备实体。"
PHASE_I = "https://genshin.hoyoverse.com/en/news/detail/167139"
OFFICIAL = {
    ("character", 10000136): (PHASE_I, "7.2 第一期活动祈愿自 11 月 4 日更新后可获得米提亚。"),
    ("character", 10000137): (PHASE_I, "7.2 第一期活动祈愿自 11 月 4 日更新后可获得瓦列里。"),
    ("weapon", 14525): (PHASE_I, "7.2 第一期活动祈愿自 11 月 4 日更新后可获得秘星典谕（Starsealed Edict）。"),
    ("character", 10000148): ("https://genshin.hoyoverse.com/en/news/detail/165610", "7.0 第一期活动祈愿已开放阿罗夏。"),
    ("character", 10000150): ("https://genshin.hoyoverse.com/en/news/detail/165610", "7.0 第一期活动祈愿已开放奥黛塔。"),
    ("character", 10000005): ("https://genshin.hoyoverse.com/en/news/detail/162141", "官方7.2之前的旅行者新衣装和天赋公告证实基础旅行者已上线。"),
    ("character", 10000007): ("https://genshin.hoyoverse.com/en/news/detail/162141", "官方7.2之前的旅行者新衣装和天赋公告证实基础旅行者已上线。"),
}
USER_CONFIRMED = {10000147: "达妮卡", 10000149: "安娜丝塔夏"}
FUTURE_ONLY_EVIDENCE = {("weapon", 12517), ("weapon", 12438), ("weapon", 15517),
                        ("artifact_set", 15049), ("artifact_set", 15050)}
RAW_TABLES = dict(zip(ENTITY_TYPES, ("AvatarExcelConfigData", "WeaponExcelConfigData", "ReliquarySetExcelConfigData"), strict=True))
APPENDICES = (
    ("LB-APPX-01", "inherent_skill", 12522, 10000125, "山月草露的等价消费",
     ("山月草露可替代普通草露使用。普通草露耗尽时使用山月草露，两者按实际扣除数量产生相同的消费收益。"
     "保留两种来源各自的余额、上限、持续时间和取得条件。动作可用性、耗露伤害、月咏与祷歌引用同一份实际扣除记录。"
     "构造输入时检查普通露为零、山月露足够的情况，并用混合余额核对扣除顺序、技能上限和重复扣除。")),
    ("LB-APPX-02", "inherent_skill", 12221, 10000122, "转核后取消普通核后续事件",
     ("月兆·满辉成立时，在奈芙尔实际生效范围内处理已有草原核转化与施放元素战技后15秒内的生成替换。"
     "取消被转换的普通核尚未发生的爆炸、上限挤爆及其后续触发。替换后的诳言之核保留生成、吸收、叠层和消失过程。"
     "构造输入时分别检查已有核、新生成核、适用范围外普通核和转化前已发生的合法伤害，后两类按自身规则保留。")),
    ("LB-APPX-03", "skill", 11252, 10000125, "重施万古潮汐时保留引力",
     ("旧重力涟漪仍有效时重施元素战技，保留已积累引力。旧效果真正结束后的清除和阈值结算按自身规则处理。"
     "在候选中用未满引力的两次施放与已结束后重新施放作对照。涟漪攻击相位和引力子击命中偏移仍待单独核对。")),
)


def sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source(kind, origin, assessment, **evidence):
    return {"source_kind": kind, "origin": origin, "assessment": assessment, **evidence}


def community_index(path):
    document = json.loads(path.read_text(encoding="utf-8"))
    by_kind = {}
    for kind, directory in zip(ENTITY_TYPES, ("characters", "weapons", "artifacts"), strict=True):
        entries = document[directory]
        index = {}
        for filename, digest, item in entries:
            ident = item["id"]
            if (type(ident) is not int or ident in index or len(digest) != 64
                    or not filename.startswith(f"src/data/ChineseSimplified/{directory}/")):
                raise ValueError(f"Invalid comparison catalog entry: {filename}")
            index[ident] = (filename, digest, item["name"])
        by_kind[kind] = index
    if {kind: len(items) for kind, items in by_kind.items()} != {
            "character": 126, "weapon": 256, "artifact_set": 63}:
        raise ValueError("Unexpected comparison catalog coverage")
    return by_kind


def inventory(conn, comparison):
    rows = []
    for kind, raw_table in RAW_TABLES.items():
        raw_payload = json.loads(conn.execute("SELECT data_json FROM raw_table WHERE source_file=?", (raw_table,)).fetchone()[0])
        id_field = "setId" if kind == "artifact_set" else "id"
        raw_records = {item[id_field]: (offset, item) for offset, item in enumerate(raw_payload)}
        for ident, name, line in conn.execute(f'SELECT id,name,version_line FROM "{kind}" ORDER BY version_line,id'):
            offset, raw = raw_records[ident]
            evidence = [source("upstream_raw", f"{UPSTREAM_COMMIT}:ExcelBinOutput/{raw_table}.json#/{offset}",
                               "提供实体身份和用途线索。",
                               raw_evidence={key: raw[key] for key in (id_field, "useType", "nameTextMapHash", "iconName",
                                                                      "skillDepotId", "weaponType", "disableFilter") if key in raw})]
            status = "unknown"
            reason = "已按原始ID和名称对照固定的7.2公服资料目录，尚未取得可确认该实体正式上线的证据。"
            match = comparison[kind].get(ident)
            if match and match[2] == name:
                status, reason = "released", "固定的独立7.2公服资料目录中存在同一原始ID和名称。"
                evidence.append(source("community_reference", COMMUNITY_ROOT + match[0],
                                       "核对原始ID及同语种名称一致。", sha256=match[1],
                                       raw_evidence={"id": ident, "name": name}))
            if (kind, ident) in OFFICIAL:
                url, reason = OFFICIAL[kind, ident]
                status = "released"
                evidence.append(source("official_reference", url, reason))
            elif kind == "character" and ident in USER_CONFIRMED:
                if status == "released":
                    status, reason = "conflicting", "用户提供的未上线信息与独立公服目录发生冲突，等待复核。"
                else:
                    status, reason = "unreleased", f"用户于{CHECKED_ON}确认{USER_CONFIRMED[ident]}尚未上线。"
                evidence.append(source("user_confirmation", "ADR-0021#用户决策", reason))
            elif kind == "weapon" and ident == 11429:
                status, reason = "released", "资料按独立ID证实该记录为已上线水仙十字之剑的芒荒形态。"
                evidence.append(source("community_reference", "https://genshinimpact.th.gl/db/weapons/weapon_11428",
                                       "条目同时关联11428及11429。", related_entity={"type": "weapon", "id": 11428}))
                evidence.append(source("community_reference", "https://hoyowhatpeon.com/en/genshin/weapons/sword-of-narzissenkreuz-11429",
                                       "11429单独记有4.2上线资料。"))
            elif (kind, ident) in FUTURE_ONLY_EVIDENCE:
                evidence.append(source("community_reference", "https://gi.gachabase.net/changelog/beta?lang=chs",
                                       "目前找到该实体的7.2.50测试快照变更，尚未找到正式上线证据。"))
                reason = "现有线索来自测试快照，独立公服资料目录没有命中。"
            elif status == "unknown" and name is None:
                reason = "原始ID无已核中文名，固定公服目录未命中该ID。"
            elif status == "unknown" and kind == "character" and ident in (10000134, 10000135, 10000901, 10000902, 10000903, 10000904):
                reason = "角色形态或试用候选与已上线主体存在相似关系，现有证据未证明该原始ID可沿用主体的上线结论。"
                evidence.append(source("project_review", "2026-11-14-database-publication-track-audit.md#6",
                                       "对照形态/试用配置、图标、技能库及主体关联。"))
            rows.append({"entity_type": kind, "entity_id": ident, "version_line": line, "name": name,
                         "entity_status": status, "checked_on": CHECKED_ON, "scope": SCOPE, "reason": reason,
                         "evidence": evidence})
    return rows


def proposal(base, reference):
    if sha256_file(base) != BASE_SHA:
        raise ValueError("Base bytes do not match the accepted r4 release")
    comparison = community_index(reference)
    with closing(sqlite3.connect(base.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        rows = inventory(conn, comparison)
        operations = []
        counts = {kind: dict(Counter(row["entity_status"] for row in rows if row["entity_type"] == kind))
                  for kind in ENTITY_TYPES}
        for item in rows:
            row = {key: value for key, value in item.items() if key not in {"evidence", "name"}}
            row["evidence_json"] = json.dumps(item["evidence"], ensure_ascii=False, sort_keys=True)
            key = {name: row[name] for name in ("entity_type", "entity_id", "version_line")}
            operations.append({"operation_id": f"publication-{item['entity_type']}-{item['entity_id']}-{item['version_line']}",
                               "source_id": "entity-audit-2026-11-14", "table": "entity_publication", "action": "add",
                               "key": key, "before_sha256": None, "row": row})
        start = conn.execute("SELECT coalesce(max(id),0) FROM annotation").fetchone()[0] + 1
        for offset, (operation, kind, target_id, owner, title, content) in enumerate(APPENDICES):
            owner_row = conn.execute("SELECT name FROM character WHERE id=? AND version_line='release'", (owner,)).fetchone()
            target_column, table, owner_column = (("proud_skill_group_id", "extra_info", "target_id")
                if kind == "inherent_skill" else ("id", "skill", "character_id"))
            target_row = conn.execute(f"SELECT name FROM {table} WHERE {target_column}=? AND {owner_column}=? AND version_line='release'",
                                      (target_id, owner)).fetchone()
            if owner_row is None or target_row is None:
                raise ValueError(f"Appendix target drift: {operation}")
            row = {"id": start + offset, "target_type": kind, "target_id": target_id, "version_line": "release",
                   "category": "mechanic_notes", "title": title, "status": "pending",
                   "content": f"{content}\n\n归属：{owner_row[0]} · {target_row[0]}。依据：{BASE}原文和{CHECKED_ON}用户补充（{operation}）。供 Agent 核实并构造候选输入，无引擎执行权。",
                   "created_at": CHECKED_ON + "T00:00:00+08:00", "updated_at": CHECKED_ON + "T00:00:00+08:00",
                   "author": "project-maintainer", "reviewer": None, "reviewed_at": None, "game_version_range": "7.2.50"}
            operations.append({"operation_id": operation, "source_id": "lunar-appendices-2026-11-14", "table": "annotation",
                               "action": "add", "key": {"id": row["id"]}, "before_sha256": None, "row": row})
        sources = [{"source_id": "entity-audit-2026-11-14", "kind": "entity_publication_evidence",
                    "locator": "ADR-0021及实体证据行中固定上游、独立目录、官方公告与项目核对出处",
                    "evidence_sha256": hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                    "summary": "逐项核对角色、武器、圣遗物套装实体上线状态，保留未定与冲突。"},
                   {"source_id": "lunar-appendices-2026-11-14", "kind": "appendix",
                    "locator": "r18-next-release-implementation-plan.md#w05-lunar-character-appendices",
                    "evidence_sha256": hashlib.sha256(json.dumps(APPENDICES, ensure_ascii=False).encode()).hexdigest(),
                    "summary": "哥伦比娅山月草露与引力、奈芙尔转核三项待核知识附录。"}]
        change_set = create_change_set(base_database_release_id=BASE, target_database_release_id=TARGET,
            base_project_content_fingerprint=project_content_fingerprint(conn), sources=sources, operations=operations,
            impact={"components": ["annotation", "entity_publication"],
                    "summary": "覆盖全量角色、武器、圣遗物套装的上线证据，新增三条待核附录。继承原始快照与既有知识。"})
    return {"status": "proposal_only_not_approved_not_applied", "base_database_sha256": BASE_SHA,
            "source_upstream_commit": UPSTREAM_COMMIT, "checked_on": CHECKED_ON, "entity_counts": counts,
            "inventory": rows, "change_set": change_set}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--comparison-catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = proposal(args.base, args.comparison_catalog)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"change_set_id": result["change_set"]["change_set_id"], "entity_counts": result["entity_counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
