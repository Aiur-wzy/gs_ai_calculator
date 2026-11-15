"""时间轴求值核心（engine.md §4/§8/§9）。

calculate(template, patches=None) -> 结果 dict：
switches 前台指针、Buff scope/target 判定、敌方减益 zone 不判 scope、
快照（cast_t 锁定 Buff 集合）、逐 hit 四体系计算、汇总与明细输出。
"""

from __future__ import annotations

import math

from genshin_engine.constants import (
    ENEMY_ZONES,
    LUNAR_FACTOR,
    MULTIPLICATIVE_ZONES,
    STELLAR_FACTOR,
    TRANSFORMATIVE_FACTOR,
    stellar_superconduct_factor,
)
from genshin_engine.damage_explanations import (
    DEFAULT_MAX_BYTES,
    ExplanationBuilder,
    digest,
)
from genshin_engine.formulas import (
    classic_damage,
    lunar_stellar_damage,
    transformative_damage,
)
from genshin_engine.registry import effect_matches_hit
from genshin_engine.template import (
    PatchError,
    TemplateError,
    _copy_json,
    apply_patch,
    validate_template,
)


def _finite_numbers(value: object, where: str) -> None:
    """有限输入仍可能在聚合/乘算时溢出，结果禁止带出 NaN/Infinity。"""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            finite = math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite:
            raise TemplateError(f"{where}: 计算数值溢出或不是有限值")
    elif isinstance(value, dict):
        for key, item in value.items():
            _finite_numbers(item, f"{where}.{key}")
    elif isinstance(value, list):
        for i, item in enumerate(value):
            _finite_numbers(item, f"{where}[{i}]")


def _resolve_system_factor(hit: dict) -> float:
    """剧变/月曜/星烁基础系数：reaction.factor 直给优先，否则查常量表。"""
    reaction = hit.get("reaction") or {}
    if "factor" in reaction:
        return float(reaction["factor"])
    rtype = reaction["type"]
    system = hit["damage_system"]
    if system == "transformative":
        return TRANSFORMATIVE_FACTOR[rtype]
    if system == "lunar":
        return LUNAR_FACTOR[rtype]
    if rtype == "stellar_superconduct":
        return stellar_superconduct_factor(int(reaction.get("layers", 0)))
    return STELLAR_FACTOR[rtype]


def _consumes_effect(hit: dict, effect: dict) -> bool:
    """当前公式实际读取的收益操作；不把候选或其他体系的乘区计入回执。

    这是公式依赖，不是移除收益后重新计算的边际收益分析；零值、封顶或
    相互抵消的合法操作仍可被消费。v1 保持既有聚合及汇总行为。
    """
    system, zone = hit["damage_system"], effect["zone"]
    rtype = (hit.get("reaction") or {}).get("type")
    scaling = system == "classic" or (
        system in ("lunar", "stellar") and not hit.get("reaction_base", False))
    if zone == "res_shred":
        return True
    if zone in ("stat_add", "stat_pct"):
        return scaling and effect["stat"] == hit.get("scaling_stat", "atk")
    if zone == "em":
        return (system != "classic" or rtype in ("amplify", "aggravate", "spread")
                or (scaling and hit.get("scaling_stat", "atk") == "em"))
    if zone == "reaction_bonus":
        return system != "classic" or rtype in ("amplify", "aggravate", "spread")
    if system == "classic":
        return zone in ("base_add", "base_mult", "dmg_bonus", "crit_rate", "crit_dmg",
                        "def_reduction", "def_ignore")
    if system == "transformative":
        return zone == "reaction_flat"
    return zone in ("crit_rate", "crit_dmg", "reaction_flat", f"{system}_base",
                    f"{system}_mult", f"{system}_elevate")


def calculate(template: dict, patches: list[dict] | None = None, *, include_attribute_inputs=False,
              include_damage_explanations=True, explanation_max_bytes=DEFAULT_MAX_BYTES) -> dict:
    """计算模板，返回结果 JSON（engine.md §8）。模板非法抛 TemplateError，补丁非法抛 PatchError。"""
    if patches is not None:
        t = apply_patch(template, patches)
        applied = _copy_json(patches, "patches_applied", PatchError)
    else:
        t = _copy_json(template, "template", TemplateError)
        try:
            validate_template(t)
        except RecursionError as exc:
            raise TemplateError("template: JSON 结构嵌套过深，无法安全校验") from exc
        applied = []

    is_v2 = t["version"] == 2
    chars = {c["id"]: c for c in t["characters"]}
    enemy = t["enemy"]
    enemy_res = enemy.get("resistance", {"default": 0.1})
    base_def_reduction = enemy.get("defense_reduction", 0.0)
    base_def_ignore = enemy.get("defense_ignore", 0.0)
    switches = sorted(t.get("switches", []), key=lambda s: s["t"])
    default_active = t["characters"][0]["id"]
    explanations = ExplanationBuilder(explanation_max_bytes) if include_damage_explanations else None

    def frontline_at(ts: float) -> str:
        cur = default_active
        for sw in switches:
            if sw["t"] <= ts:
                cur = sw["character"]
            else:
                break
        return cur

    def collect(ts: float, hit: dict):
        """聚合 ts 时刻收益；v2 额外返回实际被公式消费的 effect 身份。"""
        acc: dict[str, float] = {}
        # Keep additive terms until the panel base joins the same stable sum.
        # Summing the buffs first can lose a small term before base cancellation.
        terms: dict[str, list[float]] = {}
        used: list[str] = []
        refs: list[dict] = []
        evidence: list[dict] = []
        def record(buff, effect):
            if explanations is not None and _consumes_effect(hit, effect):
                item = {"buff_id": buff["id"], "effect_id": effect.get("id"),
                    "benefit_type_id": effect.get("benefit_type_id"), "zone": effect["zone"],
                    "stat": effect["stat"], "value": effect["value"],
                    "source": buff.get("source"), "origin": "declared_constant"}
                if effect["zone"] == "stat_pct":
                    key = "base_" + effect["stat"]
                    stats = chars[hit["character"]]["stats"]
                    if key in stats:
                        item.update(basis_stat=key, basis_value=stats[key],
                                    resolved_contribution=stats[key] * effect["value"])
                evidence.append(item)
        char_id = hit["character"]
        element = hit["element"]
        rtype = (hit.get("reaction") or {}).get("type", "")
        for b in t.get("buffs", []):
            if not (b["start"] <= ts < b["end"]):
                continue
            target = b.get("target")
            applies = (target is None or target == char_id) and (
                b.get("scope", "team") == "team" or frontline_at(ts) == char_id
            )
            buff_used = False
            for e in b["effects"]:
                if is_v2 and (not effect_matches_hit(e, hit) or not _consumes_effect(hit, e)):
                    continue
                zone, stat, value = e["zone"], e["stat"], e["value"]
                if zone in ENEMY_ZONES:  # 敌方减益不判 scope/target
                    if zone == "res_shred" and stat not in ("all", element):
                        continue
                    terms.setdefault(zone, []).append(value)
                    record(b, e)
                    buff_used = True
                    if is_v2:
                        refs.append({"buff_id": b["id"], "effect_id": e["id"],
                                     "benefit_type_id": e["benefit_type_id"]})
                    continue
                if not applies:
                    continue
                if zone in ("stat_add", "stat_pct"):
                    needs_scaling = hit["damage_system"] == "classic" or (
                        hit["damage_system"] in ("lunar", "stellar")
                        and not hit.get("reaction_base", False)
                    )
                    if not needs_scaling or stat != hit.get("scaling_stat", "atk"):
                        continue
                    key = f"{zone}:{stat}"
                    terms.setdefault(key, []).append(value)
                    record(b, e)
                    buff_used = True
                    if is_v2:
                        refs.append({"buff_id": b["id"], "effect_id": e["id"],
                                     "benefit_type_id": e["benefit_type_id"]})
                    continue
                if zone == "dmg_bonus" and stat not in ("all", element):
                    continue
                if zone == "reaction_bonus" and stat not in ("all", rtype):
                    continue
                if zone in MULTIPLICATIVE_ZONES:
                    acc[zone] = acc.get(zone, 1.0) * value
                else:
                    terms.setdefault(zone, []).append(value)
                buff_used = True
                record(b, e)
                if is_v2:
                    refs.append({"buff_id": b["id"], "effect_id": e["id"],
                                 "benefit_type_id": e["benefit_type_id"]})
            if buff_used:
                used.append(b["id"])
        return acc, terms, used, refs, evidence

    def eval_hit(h: dict, where: str) -> tuple[dict, list[str], list[dict], float]:
        char = chars[h["character"]]
        stats = char["stats"]
        ts = h.get("cast_t", h["t"]) if h.get("snapshot") else h["t"]
        acc, terms, used, refs, evidence = collect(ts, h)
        _finite_numbers({"multiplicative": acc, "additive": terms}, f"{where}.buffs")

        def additive(zone, *bases):
            return math.fsum((*bases, *terms.get(zone, ())))

        scaling_stat = h.get("scaling_stat", "atk")
        effective_em = additive("em", stats.get("em", 0.0))
        scaling_terms = ([stats.get("em", 0.0), *terms.get("em", ())] if scaling_stat == "em"
                         else [stats.get(scaling_stat, 0.0)])
        percent_terms = terms.get(f"stat_pct:{scaling_stat}", ())
        percent = math.fsum(percent_terms)
        if percent:
            base_key = f"base_{scaling_stat}"
            if base_key not in stats:
                raise TemplateError(
                    f"{where}: 生效的 stat_pct({scaling_stat}) 需要角色 "
                    f"{h['character']!r} 提供 stats.{base_key}（不能用最终面板替代）"
                )
        if "base_" + scaling_stat in stats:
            scaling_terms.extend(stats["base_" + scaling_stat] * value for value in percent_terms)
        scaling = math.fsum((*scaling_terms, *terms.get(f"stat_add:{scaling_stat}", ())))
        element = h["element"]
        resistance = math.fsum((enemy_res.get(element, enemy_res.get("default", 0.1)),
                                *(-value for value in terms.get("res_shred", ()))))
        reaction = h.get("reaction") or {}
        own_bonus = ()
        if isinstance(stats.get("dmg_bonus"), dict):
            own_bonus = (stats["dmg_bonus"].get(element, 0.0), stats["dmg_bonus"].get("all", 0.0))
        ctx = {
            "multiplier": h.get("multiplier", 0.0),
            "scaling": scaling,
            "level": char["level"],
            "enemy_level": enemy["level"],
            "resistance": resistance,
            "def_reduction": additive("def_reduction", base_def_reduction),
            "def_ignore": additive("def_ignore", base_def_ignore),
            "em": effective_em,
            "crit_rate": additive("crit_rate", stats.get("crit_rate", 0.0)),
            "crit_dmg": additive("crit_dmg", stats.get("crit_dmg", 0.0)),
            "dmg_bonus": additive("dmg_bonus", *own_bonus),
            "reaction_type": reaction.get("type"),
            "reaction_bonus": additive("reaction_bonus"),
            "reaction_flat": additive("reaction_flat"),
            "reaction_base": bool(h.get("reaction_base")),
        }
        for z in ("base_add", "base_mult", "lunar_base", "lunar_mult", "lunar_elevate",
                  "stellar_base", "stellar_mult", "stellar_elevate"):
            if z in acc:
                ctx[z] = acc[z]
            elif z in terms:
                ctx[z] = additive(z)
        _finite_numbers(ctx, f"{where}.resolved")
        for key in ("scaling", "em", "crit_dmg"):
            if ctx[key] < 0:
                raise TemplateError(f"{where}.resolved.{key}: Buff 聚合后的有效数值不得为负")
        raw_crit_rate = ctx["crit_rate"]
        ctx["crit_rate"] = max(0.0, ctx["crit_rate"])
        system = h["damage_system"]
        if system == "classic":
            if reaction.get("type") == "amplify":
                ctx["amplify_factor"] = reaction["factor"]
            out = classic_damage(ctx, explain=explanations is not None)
        elif system == "transformative":
            ctx["system_factor"] = _resolve_system_factor(h)
            out = transformative_damage(ctx, explain=explanations is not None)
        else:
            ctx["system_factor"] = _resolve_system_factor(h)
            out = lunar_stellar_damage(ctx, system, explain=explanations is not None)
        if explanations is not None:
            out["explanation_capture"] = (ctx, raw_crit_rate, evidence)
        if include_attribute_inputs:
            out['attribute_inputs'] = {'scaling_stat': scaling_stat, 'scaling': scaling, 'em': effective_em}
        return out, used, refs, ts

    hit_results = []
    total = 0.0
    by_skill: dict[str, float] = {}
    by_character: dict[str, float] = {}
    for i, h in enumerate(t["hits"]):
        where = f"hits[{i}]"
        try:
            out, used, refs, ts = eval_hit(h, where)
            count = h.get("count", 1)
            result_weight = h.get("result_weight", 1.0)
            expected = out["expected"] * count * result_weight
            crit = out["crit"] * count * result_weight if out["crit"] is not None else None
        except OverflowError as exc:
            raise TemplateError(f"{where}: 计算数值溢出或不是有限值: {exc}") from exc
        except ArithmeticError as exc:
            raise TemplateError(f"{where}: 计算数值无效: {exc}") from exc
        _finite_numbers(out, where)
        _finite_numbers({"expected": expected, "crit": crit}, where)
        if expected < 0 or (crit is not None and crit < 0):
            raise TemplateError(f"{where}: Buff/乘区组合导致负伤害")
        total += expected
        _finite_numbers(total, f"{where}.total_damage")
        by_skill[h["skill"]] = by_skill.get(h["skill"], 0.0) + expected
        by_character[h["character"]] = by_character.get(h["character"], 0.0) + expected
        row = {
            "t": h["t"], "skill": h["skill"], "hit_name": h.get("hit_name", ""),
            "character": h["character"], "count": count,
            "result_weight": result_weight,
            "damage_system": h["damage_system"],
            "expected": expected, "crit": crit,
            "buffs": used, "zones": out["zones"],
            "snapshot_at": ts if h.get("snapshot") else None,
        }
        if is_v2:
            row.update({"id": h["id"], "damage_type_ids": list(h["damage_type_ids"]),
                        "matched_effect_refs": refs})
        if include_attribute_inputs:
            row['attribute_inputs'] = out['attribute_inputs']
        if explanations is not None:
            ctx, raw_rate, evidence = out.pop("explanation_capture")
            reference = explanations.add_hit(h, ctx, raw_rate, evidence,
                out["formula_explanation"], ts,
                {"expected": expected, "crit": crit, "count": count, "result_weight": result_weight},
                chars[h["character"]]["stats"])
            if reference is not None:
                row["explanation_ref"] = reference
        hit_results.append(row)

    loop = t.get("loop_duration")
    coverage = []
    if loop:
        for b in t.get("buffs", []):
            span = max(0.0, min(b["end"], loop) - b["start"])
            coverage.append({"id": b["id"], "source": b.get("source", ""), "uptime": span / loop})

    result = _result_envelope(t, total=total, hit_results=hit_results, by_skill=by_skill,
                             by_character=by_character, coverage=coverage, applied=applied)
    if explanations is not None:
        result["damage_explanations"] = explanations.finish()
        result["damage_explanation_fingerprint"] = digest(result["damage_explanations"])
    return result


def _result_envelope(t, *, total, hit_results, by_skill, by_character, coverage, applied):
    """Keep standard result metadata identical across normal and verified-empty paths."""
    loop = t.get("loop_duration")
    result = {
        "summary": {
            "total_damage": total,
            "dps": total / loop if loop else None,
            "by_skill": by_skill,
            "by_character": by_character,
        },
        "hits": hit_results,
        "buffs_coverage": coverage,
        "patches_applied": applied,
        "assumptions": [
            "期望口径（暴击按期望值合并，暴击大数字另列）",
            "帧率未纳入（rotation.md 附录精神，默认原生系统）",
            "社区共识公式，粗粒度不追游戏内舍入",
        ] + t.get("meta", {}).get("assumptions", []),
    }
    if t["version"] == 2:
        result.update({"template_version": 2, "id_registry_version": t["id_registry_version"]})
    _finite_numbers(result, "result")
    return result


def _verified_empty_result(template):
    """Internal only: mechanics must establish execution coverage before calling.

    The public calculate() still uses the ordinary nonempty template validator.
    """
    t = _copy_json(template, "template", TemplateError)
    validate_template(t, allow_empty_hits=True)
    if t["hits"] or not t.get("loop_duration"):
        raise TemplateError("Verified-empty projection requires empty hits and a positive input window")
    result = _result_envelope(t, total=0.0, hit_results=[], by_skill={}, by_character={}, coverage=[], applied=[])
    result["damage_explanations"] = ExplanationBuilder().finish()
    result["damage_explanation_fingerprint"] = digest(result["damage_explanations"])
    return result
