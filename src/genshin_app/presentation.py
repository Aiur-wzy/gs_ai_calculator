"""人类界面的中文词汇；计算键与公共资料身份不随显示文字改变。"""

from __future__ import annotations

import copy
import re

from genshin_db.display_names import normalize_alias

LOCALE = "zh-CN"
BUFF_PRESENTATION_CONTRACT = "buff-presentation-v1"


def buff_presentation(buff: dict) -> dict | None:
    """Recognize an explicit display purpose, never infer or suppress effects."""
    if not isinstance(buff, dict):
        return None
    extension = buff.get("extensions")
    value = extension.get("presentation") if isinstance(extension, dict) else None
    if (not isinstance(value, dict) or value.get("contract") != BUFF_PRESENTATION_CONTRACT
            or value.get("purpose") != "calculation_sampling"
            or not isinstance(value.get("reason"), str) or not value["reason"].strip()):
        return None
    return {key: value[key] for key in ("contract", "purpose", "reason")}


def buff_presentation_capabilities():
    return {"contract": BUFF_PRESENTATION_CONTRACT,
            "input_path": "manual_template.buffs[].extensions.presentation",
            "purpose": "calculation_sampling", "required": ["contract", "purpose", "reason"],
            "reason": "非空说明：这是为本轴计算提供的采样窗口，并非游戏效果寿命。",
            "authority": "display_only_no_execution_or_visibility_authority_over_damage",
            "default": "未标记、未知或无效标记保持原来的可见区间；不按时长或ID猜用途。",
            "display": "计算采样默认不占持续Buff轴；高级查看、逐击贡献、保存记录和导出仍完整保留。",
            "mixed_purpose": "真实持续效果与计算采样须拆为不同输入Buff，不能整体标记采样而隐去真实持续效果。",
            "recompute": "修改输入标记后重新计算；不改写已封口结果。"}

ELEMENT_LABELS = {
    "all": "全部元素", "default": "默认抗性", "physical": "物理", "pyro": "火元素",
    "hydro": "水元素", "electro": "雷元素", "cryo": "冰元素", "anemo": "风元素",
    "geo": "岩元素", "dendro": "草元素",
}
SKILL_LABELS = {
    "A": "普通攻击", "N": "普通攻击", "NA": "普通攻击", "normal": "普通攻击",
    "normal_attack": "普通攻击", "E": "元素战技", "skill": "元素战技",
    "elemental_skill": "元素战技", "Q": "元素爆发", "burst": "元素爆发",
    "elemental_burst": "元素爆发", "CA": "重击", "charged": "重击",
    "charged_attack": "重击", "PA": "下落攻击", "plunging": "下落攻击",
    "plunge": "下落攻击", "plunging_attack": "下落攻击",
    "burst_attacks": "爆发期间攻击", "reaction": "元素反应", "additional": "附加伤害",
    "other": "其他伤害",
}
STAT_LABELS = {
    "all": "全部", "atk": "攻击力", "hp": "生命值", "def": "防御力", "em": "元素精通",
    "base_atk": "基础攻击力", "base_hp": "基础生命值", "base_def": "基础防御力",
    "crit_rate": "暴击率", "crit_dmg": "暴击伤害", "energy_recharge": "元素充能效率",
    "charge_efficiency": "元素充能效率", "dmg_bonus": "伤害加成",
    "healing_bonus": "治疗加成",
    **{f"dmg_bonus:{key}": ("全部伤害加成" if key == "all" else f"{value}伤害加成")
       for key, value in ELEMENT_LABELS.items() if key != "default"},
}
ZONE_LABELS = {
    "base_add": "倍率后基础伤害加算", "base_mult": "基础伤害乘算",
    "stat_add": "面板平值加成", "stat_pct": "基础面板百分比加成",
    "dmg_bonus": "伤害加成", "crit_rate": "暴击率加成", "crit_dmg": "暴击伤害加成",
    "em": "元素精通加成", "reaction_bonus": "反应伤害加成",
    "reaction_flat": "反应伤害平值加算", "lunar_base": "月曜基础增伤",
    "lunar_elevate": "月曜擢升", "lunar_mult": "月曜大权乘算",
    "stellar_base": "星烁基础增伤", "stellar_elevate": "星烁擢升",
    "stellar_mult": "星烁大权乘算", "def_reduction": "敌人防御降低",
    "def_ignore": "无视防御", "res_shred": "敌人抗性降低",
}
REACTION_LABELS = {
    "all": "全部反应", "amplify": "增幅反应", "aggravate": "超激化", "spread": "蔓激化",
    "burning": "燃烧", "swirl": "扩散", "superconduct": "超导", "electro_charged": "感电",
    "bloom": "绽放", "overload": "超载", "shattered": "碎冰", "burgeon": "烈绽放",
    "hyperbloom": "超绽放", "lunar_bloom": "月绽放", "lunar_electro": "月感电",
    "lunar_crystallize": "月结晶", "stellar_swirl": "星扩散",
    "stellar_swirl_anemo": "星扩散风元素分项", "stellar_swirl_cryo_lv2": "星扩散1级风旋爆发",
    "stellar_swirl_cryo_lv3": "星扩散2级风旋爆发", "stellar_superconduct": "星超导",
}
LABELS = {
    "elements": ELEMENT_LABELS, "skills": SKILL_LABELS, "stats": STAT_LABELS,
    "buff_zones": ZONE_LABELS, "reaction_types": REACTION_LABELS,
    "damage_systems": {"classic": "传统伤害", "transformative": "剧变反应",
                       "lunar": "月曜伤害", "stellar": "星烁伤害"},
    "buff_scopes": {"team": "全队", "frontline": "当前场上角色"},
    "result_zones": {
        "base": "基础伤害", "base_mult": "基础伤害乘区", "aggravate_flat": "激化基础加算",
        "dmg_bonus": "伤害加成乘区", "resistance": "抗性乘区", "defense": "防御乘区",
        "amplify": "增幅反应乘区", "crit_expected": "暴击期望乘区",
        "reaction_base": "反应基础伤害", "em_zone": "精通乘区", "reaction_flat": "反应平值加算",
        "base_in": "输入基础伤害", "system_factor": "体系系数", "base_bonus": "基础增伤乘区",
        "big_mult": "大权乘区", "elevate": "擢升乘区",
    },
    "version_lines": {"release": "正式数据线", "beta": "测试数据线"},
    "states": {
        "ready": "已就绪", "pending": "待完善", "resolved": "已明确", "unresolved": "待解析",
        "draft": "草稿", "approved": "已批准", "rejected": "已拒绝", "orphan": "来源已失效",
        "invalid": "无效", "incompatible": "版本不兼容", "missing": "缺失", "absent": "未收录",
        "unknown": "未知", "unavailable": "不可用", "unreviewed": "待核对", "complete": "已完整核对",
        "confirmed": "已确认", "excluded": "明确不计入", "empty": "空槽", "equipped": "已装备",
        "stale": "已失效", "current": "当前有效", "present": "已收录", "source_empty": "上游未提供",
        "textmap_missing": "文本映射缺失", "legacy_unverified": "旧数据待核验",
        "mapped": "已映射", "rebuilt": "已重建", "unchanged": "已是最新",
        "not_required": "无需处理", "not_applicable": "不适用", "valid": "有效", "saved": "已保存",
    },
    "annotation_target_types": {
        "character": "角色", "skill": "技能", "talent": "命座", "skill_param": "技能参数组",
        "inherent_skill": "固有天赋", "weapon": "武器", "artifact_set": "圣遗物套装",
        "artifact": "圣遗物", "equip_affix": "装备效果",
    },
    "annotation_categories": {
        "ICD": "元素附着冷却", "icd": "元素附着冷却", "snapshot": "快照机制",
        "mechanics": "机制", "rotation": "排轴", "timing": "动作时序",
        "energy": "能量", "aura": "元素附着", "hit_pattern": "命中模式",
        "action_dispatch": "动作派发", "action_lifecycle": "状态生命周期",
        "periodic_attack": "周期攻击", "elemental_application": "元素附着",
        "correction": "修正", "note": "备注",
    },
    "effect_rule_kinds": {"constant": "固定数值", "stat_scale": "按面板属性折算",
                          "explicit_hit": "明确伤害事件"},
    "dependency_modes": {"source_only": "仅依赖来源原文", "stat_rule": "按属性重算",
                         "manual_context": "当前场景人工值", "independent": "与装备无关"},
    "stat_stages": {"base": "基础面板", "static": "静态面板"},
    "operations": {"flat": "平值加算", "percent": "百分比加算", "add": "加算", "multiply": "乘算",
                   "add_base_fraction": "基础比例加算", "adjust": "增补或排除", "replace": "替换"},
    "units": {"flat": "数值", "ratio": "比例", "multiplier": "倍数"},
    "applies_to_modes": {"all": "全部伤害", "types": "指定伤害类型", "hits": "指定伤害事件",
                         "none": "明确不关联"},
    "selected_by": {"agent": "智能助手", "user": "用户"},
}


def display_contract() -> dict:
    """可随语言包扩展的只读显示契约；当前只有简体中文，无切换选项。"""
    return {"locale": LOCALE, "labels": copy.deepcopy(LABELS),
            "supported_locales": [LOCALE], "language_switching": False,
            "identity_policy": "显示名称与计算标识分开；未识别原文保留，不猜译名称或机制"}


def label(group: str, key: object, fallback: str = "中文名称待补") -> str:
    text = str(key) if key is not None else ""
    if text in LABELS.get(group, {}):
        return LABELS[group][text]
    return text if re.search(r"[\u3400-\u9fff]", text) else fallback


def display_names(template: dict, catalog: dict | None = None) -> dict:
    """供结果导出使用的显示映射；不修改模板，也不影响引擎输出或指纹。"""
    plan = template if "manual_template" in template else {}
    template = plan.get("resolved_template") or plan.get("manual_template") or template
    entries = (catalog or {}).get("characters", [])
    by_id = {str(item["id"]): item for item in entries}
    aliases: dict[str, set[str]] = {}
    for item in entries:
        for alias in [str(item["id"]), item.get("name", ""), *item.get("aliases", [])]:
            if alias:
                aliases.setdefault(normalize_alias(alias), set()).add(str(item["id"]))
    characters = {}
    for index, char in enumerate(template.get("characters", []), 1):
        cid = char["id"]
        name = char.get("name", "")
        reference = plan.get("equipment_inputs", {}).get(cid, {}).get("character_ref") or {}
        same_line = not reference or reference.get("version_line") == (catalog or {}).get("version_line")
        db_entry = by_id.get(str(reference.get("db_character_id"))) if same_line else None
        if not db_entry and not reference:
            candidates = aliases.get(normalize_alias(cid), set())
            if len(candidates) == 1:
                db_entry = by_id[next(iter(candidates))]
        if re.search(r"[\u3400-\u9fff]", name):
            characters[cid] = name
        elif db_entry:
            characters[cid] = db_entry.get("display_name") or db_entry["name"]
        else:
            characters[cid] = label("", cid, f"角色{index}（中文名待补）")
    buffs = {}
    for index, buff in enumerate(template.get("buffs", []), 1):
        text = next((buff.get(key) for key in ("name", "source")
                     if isinstance(buff.get(key), str) and re.search(r"[\u3400-\u9fff]", buff[key])), None)
        buffs[buff["id"]] = text or f"增益{index}（中文名待补）"
    return {"characters": characters, "buffs": buffs}


def presentation_issues(plan: dict) -> list[dict]:
    """Locate missing free Chinese descriptions without translating identities or game rules."""
    template = plan.get("manual_template", plan)
    issues = []

    def check(value, path, caption):
        if not isinstance(value, str) or not re.search(r"[\u3400-\u9fff]", value):
            issues.append({"code": "chinese_description_missing", "severity": "warning", "path": path,
                           "message": f"{caption}缺少明确中文描述；请补充显示文本，保留机器标识"})

    check(template.get("meta", {}).get("name"), "manual_template.meta.name", "方案名称")
    for i, hit in enumerate(template.get("hits", [])):
        check(hit.get("hit_name"), f"manual_template.hits[{i}].hit_name", "伤害名称")
    for i, buff in enumerate(template.get("buffs", [])):
        check(buff.get("name") or buff.get("source"), f"manual_template.buffs[{i}].name", "增益名称")
    for i, text in enumerate(template.get("meta", {}).get("assumptions", [])):
        check(text, f"manual_template.meta.assumptions[{i}]", "假设")
    for i, rule in enumerate((plan.get("mechanics_model") or {}).get("rules", [])):
        check(rule.get("name_zh"), f"mechanics_model.rules[{i}].name_zh", "机制名称")
        check(rule.get("source"), f"mechanics_model.rules[{i}].source", "机制依据摘要")
    for i, event in enumerate((plan.get("mechanics_model") or {}).get("condition_events", [])):
        check(event.get("source"), f"mechanics_model.condition_events[{i}].source", "条件依据摘要")
    for i, match in enumerate(plan.get("rotation_tuning", {}).get("buff_hit_matches", [])):
        check(match.get("reason"), f"rotation_tuning.buff_hit_matches[{i}].reason", "轴微调理由")
    return issues
