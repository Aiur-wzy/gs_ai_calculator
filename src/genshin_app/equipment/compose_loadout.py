"""Resolve selected loadout facts into the one private composition context.

The caller owns the database read scope. Provisional-public-entity checks retain
their session authority; this stage cannot grant approval or write shared data.
"""

from __future__ import annotations

import copy
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from genshin_db import queries

from .compose_context import ComposeContext
from .compose_diagnostics import EFFECT_BINDING_REPAIR_HINT
from .data_version import equipment_data_version
from .files import fingerprint
from .panel_calculation import PROP_MAP, build_character_basis, character_source
from .provisional_entities import ProvisionalEntityError
from .schema import STATS, PlanError


@dataclass(frozen=True, slots=True)
class LoadoutResolution:
    meta: dict[str, Any] | None


def resolve_compose_loadout(
    context: ComposeContext, db_path: Path, *, candidate_session=None,
    formal_provisional=False, resolved_provisional_uses=None, readonly_provisional_context=None,
) -> LoadoutResolution:
    plan = context.plan
    template = context.template
    assumptions = context.assumptions
    requirements = context.requirements
    sources = context.sources
    versions = context.versions
    preview = context.preview
    bases = context.bases
    contributions = context.contributions
    fixed_panels = context.fixed_panels
    panel_bases = context.panel_bases
    missing = context.missing
    _formal_provisional = formal_provisional
    _resolved_provisional_uses = resolved_provisional_uses

    def resolve_loadout_entity(ref, expected_type, path):
        """Resolve one provisional ref through the session-owned D2 ledger."""

        if not isinstance(ref, dict) or ref.get("kind") != "provisional":
            return None
        if candidate_session is None and readonly_provisional_context is None:
            missing(
                "provisional_entity_session_required", path,
                "临时公共实体只能在其当前原生Agent会话中解析；重开文件后需重新核对来源。",
                "resume_agent_session", category="core_data_missing",
                consumer="equipment_composer", required=True, consumed=True,
                impact="high", blocking_scope="loadout", retryable=True,
                repair_hint="在创建该临时实体的当前会话中重新执行数据库核对与授权",
            )
            return None
        try:
            resolver = readonly_provisional_context if readonly_provisional_context is not None else candidate_session
            resolved = resolver.resolve_provisional_entity_ref(
                ref=ref,
                consumer=("equipment_composer" if _formal_provisional
                          else "equipment_composer_preview"),
            )
            if resolved["ref"]["entity_type"] != expected_type:
                raise PlanError("临时实体类型与配装位置不一致", "entity_ref_type_mismatch")
            from .provisional_entities import validate_loadout_entity_values
            values = validate_loadout_entity_values(expected_type, resolved["value"])
            if _formal_provisional and isinstance(_resolved_provisional_uses, list):
                _resolved_provisional_uses.append(resolved)
            return {"resolved": resolved, "values": values}
        except (PlanError, ProvisionalEntityError) as exc:
            category = getattr(exc, "category", None) or (
                getattr(exc, "details", {}) or {}).get("category") or "core_data_missing"
            missing(
                getattr(exc, "reason", "provisional_entity_unresolved"), path,
                str(exc) or "临时公共实体尚未通过当前会话授权与配装值校验。",
                "review_provisional_entity", category=category,
                consumer="equipment_composer", required=True, consumed=True,
                impact="high", blocking_scope="loadout", retryable=True,
                repair_hint="核对数据库查询、来源、精确差异和当前方案授权后重新组合",
            )
            return None


    def prop_contribution(item, cid, path):
        value = item.get("value")
        if value is None:
            missing("missing_input", path, "源属性数值缺失", "choose_equipment")
            return
        mapped = PROP_MAP.get(item["prop_type"])
        if mapped is None:
            missing("unsupported_effect", path, f"当前不消费属性 {item['prop_type']}")
            return
        stat, operation = mapped
        if stat == "base_atk":
            bases[cid]["atk"] += value
            preview["equipment_sources"][cid]["base_sources"].append({"contribution_id": path,
                "stat": "atk", "value": value, "source_refs": item.get("source_refs", []),
                "note": "武器白值已计入计算基础攻击力，不再重复加算"})
        else:
            contributions[cid].append({"contribution_id": path, "stat": stat, "operation": operation,
                                       "value": value, "source_refs": item.get("source_refs", [])})

    def requirement(cid, owner, effect, refs, annotations=None):
        source_fp = fingerprint({"owner": owner, "effect": effect, "annotations": annotations or [],
                                 "data_version": plan["equipment_inputs"][cid]["data_version"]})
        found = [b for b in plan["effect_bindings"] if b["provider_character_id"] == cid and b["owner"] == owner]
        if len(found) > 1:
            raise PlanError("同一装备效果重复绑定", details={
                "code": "duplicate_effect_binding", "path": "effect_bindings",
                "input_ref": "/effect_bindings",
                "conflicts": [{"binding_id": item["binding_id"],
                               "input_ref": f"/effect_bindings/{index}"}
                              for index, item in enumerate(plan["effect_bindings"]) if item in found],
                "repair_hint": "同一角色和装备效果仅保留一份绑定，合并以下绑定中的有效部件："
                    + "、".join(item["binding_id"] for item in found)
                    + "。独立收益可放进不同 parts，各自声明条件、受益者与范围。",
                "recovery": {"action": "correct_arguments", "retry_same_request": False}})
        bid = found[0]["binding_id"] if found else "binding." + fingerprint([cid, owner])[:24]
        refs = refs or [{"kind": "database", "owner": owner}]
        proposed = {"binding_id": bid, "provider_character_id": cid, "owner": owner,
                    "source_refs": refs, "source_fingerprint": source_fp,
                    "coverage": {"state": "unreviewed", "reviewed_by": None,
                                 "source_fingerprint": None, "note": None}, "parts": []}
        req = {"binding_id": bid, "provider_character_id": cid, "owner": owner,
               "source_refs": refs, "source_fingerprint": source_fp, "effect": effect,
               "proposed_binding": proposed}
        requirements.append(req)
        sources.append({"owner": owner, "effect": effect, "annotations": annotations or [],
                        "source_fingerprint": source_fp})
        if not found:
            missing("effect_pending", f"effect_bindings.{bid}", "需要核对完整效果并填写部件或明确不计依据",
                    binding_id=bid, repair_hint=EFFECT_BINDING_REPAIR_HINT)
        return req

    def database_equipment_used(equipment):
        weapon = equipment.get("weapon")
        database_weapon = bool(weapon and (
            not isinstance(weapon.get("entity_ref"), dict)
            or weapon["entity_ref"].get("kind") == "database"))
        database_sets = any(
            not isinstance(item.get("entity_ref"), dict)
            or item["entity_ref"].get("kind") == "database"
            for item in equipment.get("artifact_sets", []))
        database_artifacts = any(
            isinstance(item, dict) and item.get("state") == "equipped"
            for item in equipment.get("artifacts", {}).values())
        return bool(equipment.get("character_ref") or database_weapon
                    or database_sets or database_artifacts)

    meta = None
    database_required = (
        any(database_equipment_used(item) for item in plan["equipment_inputs"].values())
        or any(b["owner"]["kind"] == "character_skill"
               for b in plan["effect_bindings"])
    )
    if database_required:
        if not db_path.is_file():
            missing("equipment_unavailable", "equipment_inputs", "数据库不存在", "apply_data_update")
        else:
            try:
                meta = queries.get_meta(db_path)
            except (OSError, sqlite3.Error) as exc:
                raise PlanError(f"读取数据库失败：{exc}", "equipment_unavailable") from exc
    for cid, equipment in plan["equipment_inputs"].items():
        path = f"equipment_inputs.{cid}"
        panel_mode = equipment.get("mode") == "panel"
        char = next(c for c in template["characters"] if c["id"] == cid)
        level = char["level"] if panel_mode else equipment["level"]
        line = (equipment.get("character_ref") or equipment.get("data_version") or {
            "version_line": template.get("meta", {}).get("version_line", "release")
        }).get("version_line", "release")
        basis = equipment.get("basis")
        gear_sources = {"artifacts": {}, "main_stats": {}, "sets": {}, "base_sources": []}
        preview["equipment_sources"][cid] = gear_sources
        contributions[cid] = []
        character_entity_ref = equipment.get("character_entity_ref")
        resolved_character = resolve_loadout_entity(
            character_entity_ref, "character", f"{path}.character_entity_ref")
        weapon = equipment["weapon"]
        weapon_entity_ref = weapon.get("entity_ref") if weapon else None
        weapon_is_provisional = (
            isinstance(weapon_entity_ref, dict)
            and weapon_entity_ref.get("kind") == "provisional"
        )
        resolved_weapon = resolve_loadout_entity(
            weapon_entity_ref, "weapon", f"{path}.weapon.entity_ref")
        resolved_sets = []
        provisional_set_flags = []
        for set_index, selected_set in enumerate(equipment.get("artifact_sets", [])):
            selected_ref = selected_set.get("entity_ref")
            provisional_set_flags.append(
                isinstance(selected_ref, dict)
                and selected_ref.get("kind") == "provisional"
            )
            resolved_sets.append(resolve_loadout_entity(
                selected_ref, "artifact_set",
                f"{path}.artifact_sets.{set_index}.entity_ref",
            ))
        for entity_kind, resolved in (("character", resolved_character),
                                      ("weapon", resolved_weapon)):
            if resolved is None:
                continue
            audit = {
                "kind": "provisional", "entity_type": entity_kind,
                "ref": copy.deepcopy(resolved["resolved"]["ref"]),
                "authority": resolved["resolved"]["authority"],
                "payload_fingerprint": resolved["resolved"]["payload_fingerprint"],
                "scope": "current_plan_only", "database_authority": False,
            }
            gear_sources[entity_kind] = audit
            sources.append(audit)
        for selected_set, resolved in zip(
                equipment.get("artifact_sets", []), resolved_sets, strict=True):
            if resolved is None:
                continue
            key = "provisional:" + resolved["resolved"]["ref"]["provisional_id"]
            audit = {
                "kind": "provisional", "entity_type": "artifact_set",
                "ref": copy.deepcopy(resolved["resolved"]["ref"]),
                "authority": resolved["resolved"]["authority"],
                "payload_fingerprint": resolved["resolved"]["payload_fingerprint"],
                "count": selected_set["count"], "scope": "current_plan_only",
                "database_authority": False,
            }
            gear_sources["sets"][key] = audit
            sources.append(audit)
        if panel_mode:
            fixed_panels[cid] = copy.deepcopy(char["stats"])
            panel_bases[cid] = {s: char["stats"][f"base_{s}"] for s in ("atk", "hp", "def")
                                if f"base_{s}" in char["stats"]}
            refs = copy.deepcopy(equipment["source_refs"])
            gear_sources.update(mode="panel", panel_policy=equipment["panel_policy"], source_refs=refs)
            gear_sources["base_sources"] = [{"contribution_id": f"manual_template.characters.{cid}.stats.base_{stat}",
                "stat": stat, "value": value, "source_refs": refs,
                "note": "初始面板显式给定的基础值，不从最终属性逆推"} for stat, value in panel_bases[cid].items()]
            sources.append({"kind": "panel", "character_id": cid, "level": level,
                            "stats": fixed_panels[cid], "source_refs": refs,
                            "panel_policy": equipment["panel_policy"]})
            assumptions.append(f"{cid} 使用给定初始面板，已含两件套等常驻属性；武器成长及圣遗物词条不再加算")
        else:
            gear_sources["base_sources"] = [
                {"contribution_id": path + ".basis.character_base_" + stat, "stat": stat,
                 "value": basis.get("character_base_" + stat), "source_refs": copy.deepcopy(basis["source_refs"]),
                 "note": "角色基础值"} for stat in ("atk", "hp", "def")]
            missing_basis = [key for key, value in basis.items() if value is None]
            if missing_basis:
                for key in missing_basis:
                    missing("missing_input", f"{path}.basis.{key}", "需要有依据的角色基础值", "edit_basis")
                continue
            bases[cid] = {s: basis.get(f"character_base_{s}", basis.get(s)) for s in STATS}
            bases[cid]["dmg_bonus"] = copy.deepcopy(basis["dmg_bonus"])
            for contribution in basis["ascension_contributions"] + equipment["manual_contributions"]:
                if contribution["value"] is None:
                    missing("missing_input", f"{path}.{contribution['contribution_id']}", "静态贡献缺少数值", "edit_basis")
                else:
                    contributions[cid].append(copy.deepcopy(contribution))
            if basis["mode"] == "manual" and not any(r.get("kind") == "manual" and r.get("note") for r in basis["source_refs"]):
                missing("missing_input", f"{path}.basis.source_refs", "手工基础需要明确人工依据", "edit_basis")
        if not panel_mode and any(
                value is not None for value in [resolved_character, resolved_weapon, *resolved_sets]):
            missing(
                "provisional_managed_loadout_unsupported", path,
                "临时公共实体当前只接入固定面板配装；逐件成长与词条仍请改用受审手工基础或固定面板。",
                "use_fixed_panel", category="engine_capability_missing",
                consumer="equipment_composer", required=True, consumed=True,
                impact="high", blocking_scope="loadout", retryable=True,
                repair_hint="切换为固定面板，或等待后续托管配装值合同扩展",
            )
        if resolved_weapon is not None:
            values = resolved_weapon["values"]
            treatment = values["passive_treatment"]
            if treatment == "agent_materialization_required":
                missing(
                    "provisional_weapon_passive_pending", f"{path}.weapon.entity_ref",
                    "临时武器被动仍需Agent通过通用事件、Buff或机制输入材料化。",
                    "materialize_effect", category="knowledge_missing",
                    consumer="weapon_passive", required=True, consumed=True,
                    impact="high", blocking_scope="loadout", retryable=True,
                    repair_hint=values["passive_note"],
                )
            else:
                assumptions.append(
                    f"{cid} 临时武器被动按“{values['passive_note']}”处理；"
                    f"状态={treatment}，不是数据库权威。")
        for selected_set, resolved in zip(
                equipment.get("artifact_sets", []), resolved_sets, strict=True):
            if resolved is None:
                continue
            for threshold in resolved["values"]["thresholds"]:
                if selected_set["count"] < threshold["required_count"]:
                    continue
                if threshold["treatment"] == "agent_materialization_required":
                    missing(
                        "provisional_artifact_effect_pending",
                        f"{path}.artifact_sets",
                        "临时套装已启用的效果仍需Agent通过通用事件、Buff或机制输入材料化。",
                        "materialize_effect", category="knowledge_missing",
                        consumer="artifact_set_effect", required=True, consumed=True,
                        impact="high", blocking_scope="loadout", retryable=True,
                        repair_hint=threshold["note"],
                    )
                else:
                    assumptions.append(
                        f"{cid} 临时套装{threshold['required_count']}件效果按“{threshold['note']}”处理；"
                        f"状态={threshold['treatment']}，不是数据库权威。")
        if (panel_mode and equipment.get("panel_basis_mode", "database") == "database"
                and resolved_character is not None
                and (weapon is None or resolved_weapon is not None)):
            from .provisional_entities import provisional_panel_basis
            try:
                provisional_basis = provisional_panel_basis(
                    resolved_character["values"], level=level,
                    weapon_values=(resolved_weapon or {}).get("values"),
                    weapon_level=weapon.get("level") if weapon else None,
                    weapon_ascension=weapon.get("ascension") if weapon else None,
                )
                if weapon is None:
                    provisional_basis.pop("base_atk", None)
                    missing("weapon_selection_required", f"{path}.weapon",
                            "数据库基础模式缺少武器，不能确定总基础攻击力。",
                            "choose_equipment")
                for key, value in provisional_basis.items():
                    existing = fixed_panels[cid].get(key)
                    if existing is None:
                        fixed_panels[cid][key] = value
                        char["stats"][key] = value
                    elif existing != value:
                        missing(
                            "provisional_panel_basis_conflict",
                            f"manual_template.characters.{cid}.stats.{key}",
                            "固定面板基础值与当前已授权临时实体不一致；如需保留用户值请切换手工基础模式。",
                            "review_panel_basis",
                        )
                panel_bases[cid] = {
                    stat: fixed_panels[cid][f"base_{stat}"]
                    for stat in ("atk", "hp", "def")
                    if fixed_panels[cid].get(f"base_{stat}") is not None
                }
            except ProvisionalEntityError as exc:
                missing(getattr(exc, "reason", "provisional_panel_basis_pending"), path,
                        str(exc), "review_panel_basis")
        if meta is None:
            continue
        try:
            ref = equipment.get("character_ref")
            # A provisional ref that failed closed must never fall through to
            # the database path merely because its resolver returned ``None``.
            database_weapon = weapon is not None and not weapon_is_provisional
            database_sets = [
                item for item, is_provisional in zip(
                    equipment.get("artifact_sets", []), provisional_set_flags,
                    strict=True,
                )
                if not is_provisional
            ]
            uses_database_identity = bool(
                ref or database_weapon or database_sets
                or equipment.get("artifacts"))
            if uses_database_identity:
                current_version = equipment_data_version(meta, line)
                versions.append(current_version)
                if equipment["data_version"] != current_version:
                    missing("source_changed", f"{path}.data_version", "需显式应用当前装备资料版本", "apply_data_update")
            character = None
            wd = None
            if ref:
                detail = queries.get_character_detail(db_path, ref["db_character_id"], line)
                character = detail["character"]
                gear_sources["character"] = character
                source = character_source(meta, ref["db_character_id"], line, level, character)
                sources.append({"character": source, "annotations": detail.get("annotations", [])})
                versions.append(source)
                if not panel_mode and basis["mode"] == "database":
                    actual_basis = build_character_basis(meta, detail, ref["db_character_id"], equipment["level"], line)
                    if basis != actual_basis:
                        missing("source_changed", f"{path}.basis", "角色基础或来源已变化，需要复核", "apply_data_update")
                    omitted = basis["source_refs"][0].get("unsupported_ascension_prop")
                    if omitted:
                        assumptions.append(f"{cid} 突破属性 {omitted} 不参与当前伤害数值面板")
            elif (resolved_character is None and not panel_mode
                  and basis["mode"] == "database"):
                missing("missing_input", f"{path}.character_ref", "数据库基础缺少角色绑定", "edit_basis")
            if weapon is None and not panel_mode:
                missing("missing_input", f"{path}.weapon", "请选择武器和等级状态", "choose_equipment")
            elif weapon is not None and not weapon_is_provisional:
                wd = queries.get_weapon_detail(db_path, weapon["weapon_id"], line)
                gear_sources["weapon"] = wd
                sources.append(wd)
                if character and wd["weapon"]["weapon_type"] != character["weapon_type"]:
                    missing("missing_input", f"{path}.weapon", "武器类型与绑定角色不符", "choose_equipment")
                if not panel_mode and (weapon["level"] is None or weapon["ascension"] is None):
                    missing("missing_input", f"{path}.weapon", "武器等级/突破尚未指定", "choose_equipment")
                elif not panel_mode:
                    stats = queries.get_weapon_stats(db_path, weapon["weapon_id"], weapon["level"], weapon["ascension"], line)
                    sources.append(stats)
                    for index, item in enumerate(stats["properties"]):
                        prop_contribution(item, cid, f"{path}.weapon.properties.{index}")
                selected = {(r["source_index"], r["affix_group_id"]): r["refinement"] for r in weapon["refinements"]}
                actual_slots = {(e["source_index"], e["affix_group_id"]) for e in wd["effects"]}
                if selected.keys() != actual_slots:
                    missing("missing_input", f"{path}.weapon.refinements", "精炼选择必须覆盖实际效果槽", "choose_equipment")
                for slot in wd["effects"]:
                    chosen = selected.get((slot["source_index"], slot["affix_group_id"]))
                    effect = next((e for e in slot["refinements"] if e["refinement"] == chosen), None)
                    if effect is None:
                        missing("missing_input", f"{path}.weapon.refinements", "精炼行不存在", "choose_equipment")
                        continue
                    owner = {"kind": "weapon", "weapon_id": weapon["weapon_id"],
                             "source_index": slot["source_index"], "affix_id": effect["affix_id"]}
                    requirement(cid, owner, effect, effect.get("source_refs"), wd.get("annotations"))
            if (panel_mode and equipment.get("panel_basis_mode", "database") == "database"
                    and weapon is not None
                    and ((resolved_character is not None and database_weapon)
                         or (resolved_weapon is not None and ref is not None))):
                mixed_basis = {}
                if resolved_character is not None:
                    from .provisional_entities import provisional_panel_basis
                    mixed_basis = provisional_panel_basis(
                        resolved_character["values"], level=level)
                    if wd is None:
                        raise PlanError("数据库武器资料未解析", "weapon_data_unavailable")
                    character_type = resolved_character["values"]["weapon_type"]
                    if character_type != wd["weapon"].get("weapon_type"):
                        raise PlanError("临时角色与数据库武器类型不匹配",
                                        "provisional_weapon_type_mismatch")
                    weapon_stats = queries.get_weapon_stats(
                        db_path, weapon["weapon_id"], weapon["level"],
                        weapon["ascension"], line)
                    base_rows = [row for row in weapon_stats["properties"]
                                 if row.get("prop_type") == "FIGHT_PROP_BASE_ATTACK"]
                    if len(base_rows) != 1 or type(base_rows[0].get("value")) not in (int, float):
                        raise PlanError("数据库武器缺少唯一基础攻击力", "weapon_basis_missing")
                    mixed_basis["base_atk"] += float(base_rows[0]["value"])
                    sources.append(weapon_stats)
                else:
                    if character is None:
                        raise PlanError("数据库角色资料未解析", "character_data_unavailable")
                    values = resolved_weapon["values"]
                    if character.get("weapon_type") != values["weapon_type"]:
                        raise PlanError("数据库角色与临时武器类型不匹配",
                                        "provisional_weapon_type_mismatch")
                    rows = [row for row in values["panel_basis"]
                            if row["level"] == weapon.get("level")
                            and row["ascension"] == weapon.get("ascension")]
                    if len(rows) != 1:
                        raise PlanError("临时武器缺少当前等级突破基础攻击力",
                                        "provisional_weapon_basis_missing")
                    mixed_basis = {
                        "base_atk": float(character.get(f"atk_{level}"))
                                    + float(rows[0]["base_atk"]),
                        "base_hp": float(character.get(f"hp_{level}")),
                        "base_def": float(character.get(f"def_{level}")),
                    }
                for key, value in mixed_basis.items():
                    existing = fixed_panels[cid].get(key)
                    if existing is None:
                        fixed_panels[cid][key] = value
                        char["stats"][key] = value
                    elif existing != value:
                        missing("provisional_panel_basis_conflict",
                                f"manual_template.characters.{cid}.stats.{key}",
                                "固定面板基础值与当前数据库/临时实体组合不一致；如需保留用户值请切换手工基础模式。",
                                "review_panel_basis")
                panel_bases[cid] = {
                    stat: fixed_panels[cid][f"base_{stat}"]
                    for stat in ("atk", "hp", "def")
                    if fixed_panels[cid].get(f"base_{stat}") is not None
                }
            sets = Counter({item["set_id"]: item["count"] for item in database_sets})
            for slot, item in equipment.get("artifacts", {}).items():
                apath = f"{path}.artifacts.{slot}"
                if item is None:
                    missing("missing_input", apath, "槽位尚未指定；空槽需明确说明", "choose_equipment")
                    continue
                if item["state"] == "empty":
                    assumptions.append(f"{cid} {slot} 空槽：{item['reason']}")
                    continue
                ad = queries.get_artifact_detail(db_path, item["artifact_id"], line)
                gear_sources["artifacts"][slot] = ad
                sources.append(ad)
                if ad["artifact"]["equip_type"] != slot:
                    missing("missing_input", apath, "圣遗物配置与槽位不符", "choose_equipment")
                if ad["artifact"].get("set_id"):
                    sets[ad["artifact"]["set_id"]] += 1
                if item["main_prop_id"] is None or item["level"] is None:
                    missing("missing_input", apath, "请选择主词条和强化等级", "choose_equipment")
                else:
                    stat = queries.get_artifact_main_stats(db_path, item["artifact_id"], item["level"], item["main_prop_id"], line)
                    gear_sources["main_stats"][slot] = stat
                    sources.append(stat)
                    prop_contribution(stat["stat"], cid, apath + ".main_stat")
                if item["substats"] is None:
                    missing("missing_input", apath + ".substats", "副词条尚未指定；[] 为明确不计", "choose_equipment")
                else:
                    if not item["substats"]:
                        assumptions.append(f"{cid} {slot} 明确未计副词条")
                    for index, substat in enumerate(item["substats"]):
                        if substat["value"] < 0:
                            raise PlanError("副词条累计值不能为负数")
                        prop_contribution({**substat, "source_refs": [substat["source"]]}, cid, f"{apath}.substats.{index}")
            for set_id, count in sets.items():
                sd = queries.get_artifact_set_detail(db_path, set_id, line)
                gear_sources["sets"][str(set_id)] = sd
                sources.append(sd)
                for relation in sd["effects"]:
                    if count < relation["required_count"]:
                        continue
                    effect = relation["effect"]
                    owner = {"kind": "artifact_set", "set_id": set_id,
                             "required_count": relation["required_count"],
                             "affix_id": effect["affix_id"] if effect else None}
                    requirement(cid, owner, effect, (effect or {}).get("source_refs"), sd.get("annotations"))
                    if effect is None:
                        set_path = f"{path}.artifact_sets.{set_id}" if panel_mode else f"{path}.artifacts"
                        missing("effect_pending", set_path, "套装效果原关联缺失，需有依据地处理")
        except PlanError as exc:
            if exc.reason in ("dataset_invalid", "unsupported_schema"):
                raise
            # A source mismatch may explicitly ask for a data refresh; an
            # arbitrary validation failure must not manufacture that action.
            from genshin_app.domain_errors import tool_failure_guidance
            guidance = tool_failure_guidance(exc.reason, operation="gs_compose", details=exc.details)
            recovery = guidance["recovery"]
            missing(exc.reason, guidance.get("path", path), str(exc),
                    recovery.get("action") or recovery.get("tool") or "fill_input",
                    repair_hint=guidance.get("repair_hint"),
                    diagnostics={**(exc.details if isinstance(exc.details, dict) else {}),
                                 "recovery": recovery})
        except (TypeError, ValueError, queries.NotFoundError) as exc:
            if getattr(exc, "reason", None) in ("dataset_invalid", "unsupported_schema"):
                raise PlanError(str(exc), exc.reason, getattr(exc, "details", None)) from exc
            missing("missing_input", path, str(exc), "choose_equipment")

    return LoadoutResolution(meta=meta)


__all__ = ["LoadoutResolution", "resolve_compose_loadout"]

