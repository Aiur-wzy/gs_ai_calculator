"""Read-only equipment presentation, built from inputs and fresh composer evidence.

This module does no stat arithmetic and never trusts a serialized resolution cache.
Callers supply the report returned by this invocation of the composer, or omit it
to show the original selections with explicitly unverified provenance.
"""
from __future__ import annotations

import copy
import json
from collections import Counter

from genshin_app.presentation import STAT_LABELS, display_names
from genshin_db.equipment import PROPERTIES

from .files import fingerprint, input_view
from .schema import SLOTS

SLOT_LABELS = dict(zip(SLOTS, ("生之花", "死之羽", "时之沙", "空之杯", "理之冠"), strict=True))


def build_equipment_summary(plan, report=None, *, kind="applied"):
    template = plan.get("manual_template", plan)
    report = report or {}
    if report and report.get("input_fingerprint") != fingerprint(input_view(plan)):
        raise ValueError("装备摘要的组合报告不属于当前输入")
    preview = report.get("contribution_report", {})
    evidence = preview.get("equipment_sources", {})
    names = display_names(plan)["characters"]
    rows = []
    for char in template.get("characters", []):
        cid = char["id"]
        gear = plan.get("equipment_inputs", {}).get(cid)
        panel = bool(gear and gear.get("mode") == "panel")
        row = {"actor_id": cid, "display_name": names.get(cid, "角色中文名待补"),
               "level": char.get("level") if panel or not gear else gear.get("level"),
               "input_mode": "panel" if panel else "equipment" if gear else "manual_resolved",
               "weapon": {"state": "not_provided"}, "artifact_slots": [], "artifact_sets": [],
               "panel_stages": {}, "contributions": [], "base_sources": [], "effect_statuses": [],
               "source_status": "unverified", "missing_inputs": []}
        if not gear:
            row["panel_stages"] = {"final": copy.deepcopy(char.get("stats", {}))}
            row["artifact_slots"] = [{"slot": s, "slot_label": SLOT_LABELS[s], "state": "not_provided"} for s in SLOTS]
            rows.append(row)
            continue
        src = evidence.get(cid, {})
        character = src.get("character", {})
        row["display_name"] = character.get("display_name") or character.get("name") or row["display_name"]
        row["data_version"] = copy.deepcopy(gear.get("data_version"))
        if panel:
            row["panel_policy"] = gear["panel_policy"]
            row["source_refs"] = copy.deepcopy(gear.get("source_refs", []))
        path = f"equipment_inputs.{cid}"
        row["missing_inputs"] = copy.deepcopy([m for m in report.get("missing_inputs", [])
                                                if m.get("path", "") in ("equipment_inputs", path)
                                                or m.get("path", "").startswith(path + ".")])
        source_failed = any(m.get("path") in ("equipment_inputs", path)
                            or m.get("code") == "equipment_unavailable" for m in row["missing_inputs"])
        row["source_status"] = ("stale" if any(m.get("code") == "source_changed" for m in row["missing_inputs"])
                                else "unverified" if source_failed
                                else "current" if any(k in src for k in ("character", "weapon")) or src.get("artifacts") or src.get("mode") == "panel" else "unverified")
        stages = preview.get("characters", {}).get(cid, {})
        row["panel_stages"] = {k: copy.deepcopy(stages[k]) for k in ("base", "static", "final") if k in stages}
        if panel and "static" not in row["panel_stages"]:
            row["panel_stages"]["static"] = copy.deepcopy(char.get("stats", {}))
        row["contributions"] = copy.deepcopy(stages.get("contributions", []))
        row["base_sources"] = copy.deepcopy(src.get("base_sources", []))
        weapon = gear.get("weapon")
        if weapon:
            detail = src.get("weapon", {}).get("weapon", {})
            row["weapon"] = {**copy.deepcopy(weapon), "state": "selected", "path": path + ".weapon",
                             "display_name": detail.get("display_name") or detail.get("name") or "武器资料暂不可用",
                             "source_refs": copy.deepcopy(detail.get("source_refs", []))}
        sets = Counter({s["set_id"]: s["count"] for s in gear.get("artifact_sets", [])}) if panel else Counter()
        for slot in (() if panel else SLOTS):
            item = gear.get("artifacts", {}).get(slot)
            entry = {"slot": slot, "slot_label": SLOT_LABELS[slot], "path": path + ".artifacts." + slot,
                     **copy.deepcopy(item or {"state": "not_specified"})}
            if item and item["state"] == "equipped":
                artifact = src.get("artifacts", {}).get(slot, {}).get("artifact", {})
                entry.update(display_name=artifact.get("display_name") or artifact.get("name") or "单件资料暂不可用",
                             rarity=artifact.get("rarity"), set_id=artifact.get("set_id"),
                             main_stat=copy.deepcopy(src.get("main_stats", {}).get(slot, {}).get("stat")),
                             source_refs=copy.deepcopy(artifact.get("source_refs", [])))
                if entry["set_id"] is not None:
                    sets[entry["set_id"]] += 1
            row["artifact_slots"].append(entry)
        for requirement in report.get("effect_requirements", []):
            if requirement["provider_character_id"] != cid:
                continue
            binding = next((b for b in plan.get("effect_bindings", []) if b["binding_id"] == requirement["binding_id"]), {})
            issues = [m for m in report.get("missing_inputs", []) if m.get("binding_id") == requirement["binding_id"]]
            parts = binding.get("parts", [])
            compiled = next((c for c in report.get("upstream_compilations", [])
                             if c["binding_id"] == requirement["binding_id"]), None)
            if compiled is not None:
                parts = compiled.get("parts", [])
            state = "pending" if issues or not parts else "excluded" if all(p["state"] == "excluded" for p in parts) else "resolved"
            mechanism_coverage = requirement.get('mechanism_coverage')
            if mechanism_coverage:
                mechanism = next((r for r in report.get('mechanism_compilations', [])
                                  if r['id'] == mechanism_coverage['mechanism_source_id'] and r['status'] == 'ready'), None)
                if mechanism:
                    parts = [{**p, 'state': 'resolved'} for p in mechanism['parts']]
                    state = 'pending' if issues else 'resolved'
            row["effect_statuses"].append({"binding_id": requirement["binding_id"], "owner": copy.deepcopy(requirement["owner"]),
                                          "state": state, "effect": copy.deepcopy(requirement.get("effect")),
                                          "parts": copy.deepcopy(parts), "missing_inputs": copy.deepcopy(issues)})
            if mechanism_coverage:
                row['effect_statuses'][-1]['mechanism_coverage'] = copy.deepcopy(mechanism_coverage)
            if compiled is not None:
                row["effect_statuses"][-1].update(automatic=True,
                    gameplay_semantics_status=compiled.get("gameplay_semantics_status"),
                    compilation_fingerprint=compiled.get("compilation_fingerprint"))
                if "projection_status" in compiled:
                    row["effect_statuses"][-1].update({k: copy.deepcopy(compiled.get(k)) for k in
                        ("projection_status", "whole_source_status", "coverage_certificate", "branches")})
        for set_id, count in sets.items():
            detail = src.get("sets", {}).get(str(set_id), {})
            record = detail.get("artifact_set", detail.get("set", {}))
            effects = []
            for relation in detail.get("effects", []):
                matched = next((e for e in row["effect_statuses"] if e["owner"].get("set_id") == set_id
                                and e["owner"].get("required_count") == relation["required_count"]), None)
                effects.append({**copy.deepcopy(relation), "state": "not_applicable" if count < relation["required_count"]
                                else matched["state"] if matched else "pending"})
            row["artifact_sets"].append({"set_id": set_id, "count": count,
                                         "display_name": record.get("display_name") or record.get("name") or "套装中文名待补",
                                         "source_refs": copy.deepcopy(record.get("source_refs", [])),
                                         "effects": effects})
        if not panel:
            row["specified_slots"] = sum(a["state"] in ("equipped", "empty") for a in row["artifact_slots"])
            row["equipped_slots"] = sum(a["state"] == "equipped" for a in row["artifact_slots"])
        row["pending_effects"] = sum(e["state"] == "pending" for e in row["effect_statuses"])
        rows.append(row)
    summary = {"summary_version": 1, "context": {"kind": kind, "plan_id": plan.get("plan_id"),
            "revision": plan.get("revision"), "state": report.get("state", "unverified"),
            **{k: report.get(k) for k in ("input_fingerprint", "source_fingerprint", "output_fingerprint")}},
            "characters": rows}
    if report.get("energy_generation_compilations"):
        summary["energy_generation"] = {
            "sources": [{**{k: r.get(k) for k in ("id", "actor", "status", "source_status", "trigger_status", "whole_source_status")},
                         "issue_codes": [i.get("code") for i in r.get("issues", [])]}
                        for r in report["energy_generation_compilations"]],
            "summary": copy.deepcopy(report.get("energy_generation_summary"))}
    if report.get("limited_hit_benefit_compilations"):
        summary["limited_hit_benefit"] = {
            "sources": [{**{k: r.get(k) for k in ("id", "actor", "status", "source_status", "benefit_status", "whole_source_status")},
                         "issue_codes": [i.get("code") for i in r.get("issues", [])]}
                        for r in report["limited_hit_benefit_compilations"]],
            "summary": copy.deepcopy(report.get("limited_hit_benefit_summary"))}
    if report.get("mechanism_compilations"):
        summary["mechanisms"] = {
            "sources": [{**{k: r.get(k) for k in ("id", "actor", "status", "whole_source_status")},
                         "rule_count": len(r.get("rules", [])),
                         "issue_codes": [i.get("code") for i in r.get("issues", [])]}
                        for r in report["mechanism_compilations"]]}
    return summary


def equipment_rows(summary):
    """A readable single Excel sheet; numerical values and source identity retained."""
    rows = [["角色", "项目", "属性/部位", "内容", "来源/说明"]]
    dump = lambda value: json.dumps(value, ensure_ascii=False)
    for char in summary.get("characters", []):
        name = char["display_name"]
        panel = char["input_mode"] == "panel"
        if char["input_mode"] == "manual_resolved":
            rows.append([name, "数值快照", "装备来源", "未提供", "不能从最终面板还原装备"])
        else:
            if panel:
                rows.append([name, "初始面板", "常驻属性", "已包含武器、词条及两件套等常驻属性",
                             dump(char.get("source_refs", []))])
                rows.append([name, "换装口径", "固定面板", "换套后需提供对应的初始面板；不自动重算词条", "条件增益按时间轴生效"])
            w = char["weapon"]
            refs = "、".join(f"效果槽{r['source_index']}：精炼{r['refinement']}阶" for r in w.get("refinements", []))
            rows.append([name, "武器", w.get("display_name", "尚未指定"),
                         (f"{refs}；仅展示与被动效果" if panel else
                          f"等级 {w.get('level', '未提供')} / 突破 {w.get('ascension', '未提供')} / {refs}"), dump(w)])
            for a in char["artifact_slots"]:
                rows.append([name, "圣遗物", a["slot_label"], a.get("display_name", a.get("reason", "尚未指定")), dump(a)])
                if a.get("main_stat"):
                    stat = a["main_stat"]
                    rows.append([name, "主词条", stat.get("prop_name", stat["prop_type"]), stat["value"], dump(stat.get("source_refs", []))])
                for s in a.get("substats") or []:
                    rows.append([name, "累计副词条", PROPERTIES.get(s["prop_type"], {}).get("name", "属性中文名待补"), s["value"], dump(s)])
            for s in char["artifact_sets"]:
                rows.append([name, "套装件数", s["display_name"], s["count"], dump(s["effects"])])
            for e in char["effect_statuses"]:
                rows.append([name, "效果处理", e["binding_id"], {"pending": "待完善", "excluded": "明确不计", "resolved": "已计入"}[e["state"]], dump(e)])
        for stage, stats in char["panel_stages"].items():
            for key, value in stats.items():
                values = {f"dmg_bonus:{el}": v for el, v in value.items()} if isinstance(value, dict) else {key: value}
                for stat, number in values.items():
                    rows.append([name, {"base": "计算基础值", "static": "给定初始面板" if panel else "静态组合阶段", "final": "组合后面板"}[stage],
                                 STAT_LABELS.get(stat, stat), number, "比例使用小数；组合后面板不包含全部区间增益"])
        for c in char["base_sources"] + char["contributions"]:
            rows.append([name, "基础来源" if c in char["base_sources"] else "面板贡献", STAT_LABELS.get(c["stat"], c["stat"]), c["value"], dump(c)])
    rows.append(["", "计算输入绑定", "快照", dump(summary["context"]), ""])
    return rows
