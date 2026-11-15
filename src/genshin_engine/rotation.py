"""确定性的动作、冷却与元素能量检查；不读取数据库，不修改伤害模板。"""

from __future__ import annotations

import copy
import json
import math
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from genshin_engine import action_admission_audit as admission_audit
from genshin_engine import action_capacity_audit as capacity_audit
from genshin_engine import automatic_energy as auto_energy
from genshin_engine.attribute_budget import HARD_LIMITS, attribute_array_capacity_issue
from genshin_engine.energy_credit import plan_energy_gain
from genshin_engine.execution_control import (
    ExecutionCancelled,
    check_execution_cancelled,
)
from genshin_engine.particle_quantities import (
    as_number,
    average_scope,
    close_quantity,
    quantity,
    quantity_model_issue,
    uses_average,
)
from genshin_engine.rotation_schema import ROTATION_SCHEMA, rotation_capabilities

TICKS = 1_000_000
EPS = 1e-8
UNCHECKED = ["体力消耗", "元素附着与反应", "攻击是否命中", "距离与位置", "伤害稳态与 Buff 刷新"]


def _issue(code, message, path="", severity="error", **details):
    return {"code": code, "message": message, "path": path, "severity": severity, **details}


def _union_diagnostic_errors(value, choices, candidates, path):
    """Select errors by *present* discriminators; absence is never a match."""
    if isinstance(value, dict):
        scored = []
        for choice, errors in zip(choices, candidates, strict=True):
            matches, conflicts = 0, 0
            for key, constraint in choice.get('properties', {}).items():
                if key not in value:
                    continue
                allowed = ([constraint['const']] if 'const' in constraint else constraint.get('enum'))
                if allowed is not None:
                    if value[key] in allowed:
                        matches += 1
                    else:
                        conflicts += 1
            scored.append((matches, -conflicts, errors))
        best = max((row[:2] for row in scored), default=(0, 0))
        matching = [row[2] for row in scored if row[:2] == best]
        if best[0] > 0 and len(matching) == 1:
            return matching[0]
    # Common missing/unknown fields are more useful than arbitrary first-branch errors.
    common = [row for row in candidates[0] if all(row in group for group in candidates[1:])]
    if common:
        return common
    return [_issue('schema', '值不符合任何受支持的结构。', path,
                   branches=[group[:8] for group in candidates[:8]])]


def _matches_schema_type(value, kind, number):
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return number
    expected = _SCHEMA_TYPES.get(kind)
    return expected is not None and isinstance(value, expected)


_SCHEMA_TYPES = {"null": type(None), "object": dict, "array": list, "string": str, "boolean": bool}


def _schema_errors(value, schema, path, issues):
    check_execution_cancelled()
    if "oneOf" in schema:
        candidates = []
        for choice in schema["oneOf"]:
            errors = []
            _schema_errors(value, choice, path, errors)
            if not errors:
                return
            candidates.append(errors)
        issues.extend(_union_diagnostic_errors(value, schema['oneOf'], candidates, path))
        return
    types = schema.get("type")
    number = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    if types and not (_matches_schema_type(value, types, number) if isinstance(types, str)
                      else any(_matches_schema_type(value, item, number) for item in types)):
        issues.append(_issue("schema", "字段类型不符合契约。", path))
        return
    if "const" in schema and (value != schema["const"] or isinstance(value, bool)):
        issues.append(_issue("schema", "不支持的版本或事件类型。", path))
    if "enum" in schema and value not in schema["enum"]:
        issues.append(_issue("unsupported", "字段值不在当前支持范围内。", path))
    if isinstance(value, str) and len(value) < schema.get("minLength", 0):
        issues.append(_issue("schema", "文本不可为空。", path))
    if isinstance(value, str) and (len(value) > schema.get("maxLength", math.inf)
            or ("pattern" in schema and not re.fullmatch(schema["pattern"], value))):
        issues.append(_issue("schema", "文本长度或格式不符合契约。", path))
    if number and (value < schema.get("minimum", -math.inf) or value > schema.get("maximum", math.inf)):
        bounds = {key: schema[key] for key in ("minimum", "maximum", "default") if key in schema}
        issues.append(_issue("schema",
            f"数值超出支持范围：实际{value}，允许{schema.get('minimum', '不限')}..{schema.get('maximum', '不限')}。",
            path, actual=value, **bounds,
            **({"repair_hint": schema["description"]} if "description" in schema else {})))
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                issues.append(_issue("schema", "缺少必要字段。", f"{path}.{key}"))
        for key, child in value.items():
            if not isinstance(key, str):
                issues.append(_issue("schema", "JSON 对象键必须是文本。", path))
            elif key in props:
                _schema_errors(child, props[key], f"{path}.{key}", issues)
            elif schema.get("additionalProperties") is False:
                issues.append(_issue("unknown_field", "未知字段不会被静默忽略。", f"{path}.{key}"))
            elif isinstance(schema.get("additionalProperties"), dict):
                _schema_errors(child, schema["additionalProperties"], f"{path}.{key}", issues)
    if isinstance(value, list):
        maximum = schema.get('maxItems', 10000)
        if len(value) > maximum:
            capacity = attribute_array_capacity_issue(value,schema,path)
            if capacity is None:
                issues.append(_issue("limit", f"单个数组超过 {maximum} 项。", path))
                return
            issues.append(capacity)
            # Continue checking a legitimate declaration inside the published
            # hard bound. A smaller requested budget must not hide bad rows.
            if len(value) > HARD_LIMITS[capacity['dimension']]:
                return
        for index, child in enumerate(value):
            _schema_errors(child, schema.get("items", {}), f"{path}[{index}]", issues)


def validate_rotation_model(model):
    """只检查 JSON 结构；允许未知数值保留草稿，不依赖 resolved template。"""
    issues = []
    try:
        json.dumps(model, allow_nan=False)
        _schema_errors(model, ROTATION_SCHEMA, "rotation_model", issues)
        if not issues and model.get("damage_report") is not None:
            from genshin_engine.damage_report_window import validate_report
            issues.extend(validate_report(model["damage_report"], period=model.get("period")))
    except ExecutionCancelled:
        raise
    except (TypeError, ValueError, OverflowError, RecursionError):
        issues.append(_issue("schema", "输入不是有限、可序列化的 JSON。", "rotation_model"))
    return {"status": "invalid" if issues else "valid", "issues": issues}


class _Input:
    def __init__(self, model):
        self.model = model
        self.issues = []
        self.assumptions = list(model.get("assumptions", []))

    def error(self, code, message, path, **kw):
        self.issues.append(_issue(code, message, path, **kw))

    def pending(self, message, path):
        self.error("missing_information", message, path, severity="pending")

    def number(self, obj, key, path, *, integer=False, positive=False):
        value = obj.get(key)
        if value is None:
            self.pending("数值未知，不能按 0 处理。", f"{path}.{key}")
            return None
        if value < 0 or (positive and value <= 0) or (integer and not isinstance(value, int)):
            self.error("value", "需要符合范围的非负数值。", f"{path}.{key}")
            return None
        return value

    def time(self, obj, key, path, *, positive=False):
        value = self.number(obj, key, path, positive=positive)
        if value is None:
            return None
        try:
            ticks = Decimal(str(value)) * TICKS
            if ticks != ticks.to_integral_value() or ticks > 10**15:
                raise InvalidOperation
            return int(ticks)
        except (InvalidOperation, ValueError, OverflowError):
            self.error("time_precision", "时间须精确到微秒且不超过 10 亿秒。", f"{path}.{key}")
            return None

    def evidence(self, obj, path):
        if obj.get("status") not in ("confirmed", "assumed") or not obj.get("source", "").strip():
            self.pending("需确认输入状态并说明来源或情景依据。", path)
        elif obj["status"] == "assumed":
            self.assumptions.append(f"{obj.get('id', path)}：{obj['source']}")

    def repeat(self, obj, path):
        repeat = obj["repeat"]
        if repeat["mode"] == "cycles":
            cycles = repeat.get("cycles", [])
            if not cycles or len(cycles) != len(set(cycles)):
                self.error("repeat", "指定轮次必须非空且不可重复。", path)
        elif "cycles" in repeat:
            self.error("repeat", "只有 cycles 模式可以包含轮次列表。", path)


def _key(character, group):
    return json.dumps([character, group], ensure_ascii=False, separators=(",", ":"))


def _unique(items, ctx, path):
    result = {}
    for item in items:
        if item["id"] in result:
            ctx.error("duplicate_id", "ID 不可重复。", path)
        result[item["id"]] = item
    return result


def _semantic(model, template):
    ctx = _Input(model)
    if any(auto_energy.automatic(a) for a in model["abilities"]):
        for path in auto_energy.namespace_conflicts({"rotation_model": model, "template": template}):
            ctx.error("automatic_energy_namespace", "自动扣能审计命名空间不可用作输入身份或事件引用。", path)
    if any(capacity_audit.bound(a) for a in model["abilities"]):
        for path in capacity_audit.namespace_conflicts({"rotation_model": model, "template": template}):
            ctx.error("action_capacity_namespace", "容量消费审计命名空间不可用作输入身份或事件引用。", path)
    if any(admission_audit.bound(a) for a in model["abilities"]):
        for path in admission_audit.namespace_conflicts({"rotation_model": model, "template": template}):
            ctx.error("action_admission_namespace", "动作接纳证书命名空间不可用作输入身份或事件引用。", path)
    period = ctx.time(model, "period", "rotation_model", positive=True)
    chars = _unique(model["characters"], ctx, "characters")
    abilities = _unique(model["abilities"], ctx, "abilities")
    actions = _unique(model["actions"], ctx, "actions")
    events = _unique(model["events"], ctx, "events")
    windows = _unique(model.get("energy_recharge_windows", []), ctx, "energy_recharge_windows")
    for eid in actions.keys() & events.keys():
        ctx.error("duplicate_id", "动作与状态事件必须使用不同 ID。", eid)
    if not 1 <= len(chars) <= 4:
        ctx.error("party_size", "仅支持单人控制的 1–4 人队伍。", "characters")
    if not actions:
        ctx.pending("未提供独立动作，不能以空轴认证。", "actions")
    if not model.get("initial_frontline"):
        ctx.pending("未声明初始前台角色。", "initial_frontline")
    elif model["initial_frontline"] not in chars:
        ctx.error("reference", "初始前台不在队伍中。", "initial_frontline")
    for cid, char in chars.items():
        ctx.evidence(char, f"characters.{cid}")
        if char.get("element") is None:
            ctx.pending("角色元素未知，不能按无元素处理。", f"characters.{cid}.element")
        ctx.number(char, "energy_recharge", cid, positive=True)
        cap = ctx.number(char, "energy_capacity", cid)
        energy = ctx.number(char, "initial_energy", cid)
        if cap is not None and energy is not None and energy > cap:
            ctx.error("initial_energy", "初始能量超过上限。", cid)
    groups = {}
    sequence_groups = defaultdict(list)
    capacity_pool_members = defaultdict(list)
    unknown_cooldown_owners = set()
    for aid, ability in abilities.items():
        ctx.evidence(ability, f"abilities.{aid}")
        if ability["character"] not in chars:
            ctx.error("reference", "技能所属角色不存在。", aid)
        if "cooldown" not in ability:
            ctx.pending("需声明冷却规则，无冷却时显式写 null。", aid)
            unknown_cooldown_owners.add(ability["character"])
        cooldown = ability.get("cooldown")
        sequence = ability.get("cast_sequence")
        if sequence is not None:
            if sequence["stage"] > sequence["max_stages"]:
                ctx.error("cast_sequence", "连携段数不能超过总段数。", f"{aid}.cast_sequence.stage")
            window = None
            if sequence["stage"] < sequence["max_stages"]:
                window = ctx.time(sequence, "continuation_window", f"{aid}.cast_sequence", positive=True)
            elif sequence.get("continuation_window") is not None:
                ctx.error("cast_sequence_window", "末段没有下一段，continuation_window 必须为 null。",
                          f"{aid}.cast_sequence.continuation_window")
            sequence_groups[_key(ability["character"], sequence["group"])].append(
                (ability, window)
            )
        if admission_audit.bound(ability) and cooldown is None:
            ctx.error("action_admission_source", "动作接纳来源必须交付完整冷却门禁。", aid)
        if cooldown is not None:
            duration = ctx.time(cooldown, "duration", aid)
            charges = ctx.number(cooldown, "charges", aid, integer=True, positive=True)
            if cooldown.get("start") is None or cooldown.get("recovery") is None:
                ctx.pending("需说明冷却起点和次数恢复模式。", aid)
            if charges is not None and charges > 100:
                ctx.error("limit", "最多支持 100 次技能充能。", aid)
            group = _key(ability["character"], cooldown["group"])
            capacity_pool_members[group].append(ability)
            old = groups.get(group)
            current = {"charges": charges, "recovery": cooldown.get("recovery"),
                       "duration": duration, "character": ability["character"]}
            if old and (old["charges"], old["recovery"]) != (charges, cooldown.get("recovery")):
                ctx.error("cooldown_group", "同一冷却组的次数与恢复模式必须一致。", aid)
            groups[group] = current
        if "energy" not in ability:
            ctx.pending("需声明能量规则，无消耗时显式写 null。", aid)
        if ability.get("energy") is not None:
            ctx.number(ability["energy"], "required", aid)
            ctx.number(ability["energy"], "amount", aid)
            if auto_energy.automatic(ability):
                energy = ability["energy"]
                if (ability["action_type_id"] != "action.elemental_burst"
                        or energy.get("required") != energy.get("amount")
                        or energy.get("amount") is None or energy["amount"] <= 0
                        or (ability.get("cooldown") or {}).get("charges") != 1):
                    ctx.pending("自动扣能仅支持明确正固定成本、单充能的普通爆发。", aid)
        elif ability["action_type_id"] == "action.elemental_burst":
            ctx.pending("爆发必须声明能量门槛与实际消耗，零消耗也须显式声明。", aid)
        if admission_audit.bound(ability):
            energy_rule = ability.get("energy") or {}
            if (ability["action_type_id"] != "action.elemental_burst"
                    or ability.get("variant") != "default"
                    or cooldown is None
                    or cooldown.get("charges") != 1
                    or cooldown.get("start") != "action_start"
                    or cooldown.get("recovery") != "parallel"
                    or energy_rule.get("delivery") != "action_start"
                    or energy_rule.get("required") != energy_rule.get("amount")
                    or type(energy_rule.get("amount")) not in (int, float)
                    or isinstance(energy_rule.get("amount"), bool)
                    or not math.isfinite(energy_rule["amount"])
                    or energy_rule["amount"] <= 0):
                ctx.error("action_admission_scope",
                          "动作接纳首片只接受default、单次数、action_start并行冷却、正固定耗能的元素爆发。", aid)
            if capacity_audit.bound(ability):
                ctx.error("action_admission_capacity_conflict",
                          "动作接纳首片不能与旧容量来源绑定同一能力。", aid)
        _unique(ability.get("rules", []), ctx, f"{aid}.rules")
    sequence_specs = {}
    for group, members in sequence_groups.items():
        first_sequence = members[0][0]["cast_sequence"]
        expected = {
            "max_stages": first_sequence["max_stages"],
            "cooldown_start": first_sequence["cooldown_start"],
            "action_type_id": members[0][0]["action_type_id"],
        }
        stages = {}
        for ability, window in members:
            sequence = ability["cast_sequence"]
            current = {
                "max_stages": sequence["max_stages"],
                "cooldown_start": sequence["cooldown_start"],
                "action_type_id": ability["action_type_id"],
            }
            if current != expected:
                ctx.error("cast_sequence_group", "同一连携组的总段数、冷却策略与动作类别必须一致。",
                          ability["id"])
            stage = sequence["stage"]
            if stage in stages:
                ctx.error("cast_sequence_stage", "同一连携组不能重复定义相同段数。", ability["id"])
            stages[stage] = ability
            if stage == 1:
                cooldown = ability.get("cooldown")
                if cooldown is None or cooldown.get("start") != "action_start":
                    ctx.error("cast_sequence_cooldown",
                              "sequence_start 连携的首段必须声明从 action_start 开始的普通冷却。",
                              ability["id"])
            elif ability.get("cooldown") is not None:
                ctx.error("cast_sequence_cooldown",
                          "续段使用连携窗口接纳，不能再次消费普通冷却。", ability["id"])
        required_stages = set(range(1, first_sequence["max_stages"] + 1))
        if set(stages) != required_stages:
            ctx.error("cast_sequence_stage", "连携组必须完整且只定义 1..max_stages。", group,
                      expected_stages=sorted(required_stages), actual_stages=sorted(stages))
        first_ability = stages.get(1)
        first_cooldown = first_ability.get("cooldown") if first_ability else None
        source_rows = [(ability, ability["cast_sequence"].get("source_binding"))
                       for ability, _ in members]
        if any(binding is not None for _, binding in source_rows):
            if any(binding is None for _, binding in source_rows):
                ctx.error("cast_sequence_source_binding",
                          "同一连携组的来源绑定必须覆盖全部阶段，不能把受审阶段与无来源阶段混合。", group)
            else:
                common_keys = ("contract", "profile_id", "character_id", "version_line",
                               "snapshot_id", "upstream_commit", "content_fingerprint",
                               "resource_fingerprint", "sequence_cooldown_seconds",
                               "sequence_cooldown_charges", "sequence_cooldown_recovery",
                               "verification_mode")
                reference = {key: source_rows[0][1][key] for key in common_keys}
                if reference["sequence_cooldown_seconds"] <= 0:
                    ctx.error("cast_sequence_source_binding",
                              "来源绑定的基础冷却必须是正数。", group)
                selected_skill_ids = set()
                for ability, binding in source_rows:
                    if {key: binding[key] for key in common_keys} != reference:
                        ctx.error("cast_sequence_source_binding",
                                  "同一连携组的来源档案、快照、指纹与冷却策略必须完全一致。",
                                  ability["id"])
                    if binding["selected_stage"] != ability["cast_sequence"]["stage"]:
                        ctx.error("cast_sequence_source_binding",
                                  "来源绑定的阶段编号必须与当前连携阶段一致。", ability["id"])
                    if binding["selected_skill_id"] in selected_skill_ids:
                        ctx.error("cast_sequence_source_binding",
                                  "同一连携组不能把同一个数据库技能身份绑定到多个阶段。", ability["id"])
                    selected_skill_ids.add(binding["selected_skill_id"])
                if first_cooldown is not None and (
                        first_cooldown.get("duration") != reference["sequence_cooldown_seconds"]
                        or first_cooldown.get("charges") != reference["sequence_cooldown_charges"]
                        or first_cooldown.get("start") != "action_start"
                        or first_cooldown.get("recovery") != reference["sequence_cooldown_recovery"]):
                    ctx.error("cast_sequence_source_binding",
                              "首段普通冷却必须与保存的受审秒数、次数、起点和恢复方式完全一致。",
                              first_ability["id"])
        if first_cooldown is not None:
            sequence_specs[group] = {
                "group": first_sequence["group"],
                "cooldown_group": _key(first_ability["character"], first_cooldown["group"]),
                "cooldown_group_id": first_cooldown["group"],
            }
    capacity_bindings = {}
    for group, members in capacity_pool_members.items():
        marked = [a for a in members if capacity_audit.bound(a)]
        if not marked:
            continue
        first = marked[0]["cooldown"]
        marker = first["capacity_source"]
        projection = (first.get("charges"), first.get("recovery"), first.get("start"), first.get("duration"))
        for ability in members:
            cd = ability["cooldown"]
            if ability.get("variant") != "tap":
                ctx.error("action_capacity_variant", "当前容量来源合同只接受显式 tap 点按；不转换旧 variant。", ability["id"])
            if (cd.get("capacity_source") != marker or
                    (cd.get("charges"), cd.get("recovery"), cd.get("start"), cd.get("duration")) != projection):
                ctx.error("action_capacity_pool", "来源绑定冷却池的全部能力必须使用同一来源标记及冷却配置。", ability["id"])
        prior = capacity_bindings.setdefault(marker["binding_id"], group)
        if prior != group:
            ctx.error("action_capacity_pool", "同一容量来源绑定不可跨越角色或冷却池。", marker["binding_id"])
    initial_cd = {}
    for item in model.get("initial_cooldowns", []):
        group = _key(item["character"], item["group"])
        if group in initial_cd or (group not in groups and item["character"] not in unknown_cooldown_owners):
            ctx.error("cooldown_initial", "初始冷却组重复或不存在。", group)
        values = item.get("ready_in")
        if values is None:
            ctx.pending("初始次数恢复时间未知；全就绪需显式写空数组。", group)
            values = []
        ticks = [ctx.time({"t": value}, "t", group) for value in values]
        if None not in ticks and ticks != sorted(ticks):
            ctx.error("cooldown_initial", "初始恢复队列必须按时间递增。", group)
        if groups.get(group, {}).get("charges") is not None and len(ticks) > groups[group]["charges"]:
            ctx.error("cooldown_initial", "恢复中的次数超过技能最大次数。", group)
        initial_cd[group] = ticks
    for group in groups:
        if group not in initial_cd:
            ctx.pending("没有声明本组初始冷却/次数状态。", group)
    runtime = []
    for aid, action in actions.items():
        ctx.evidence(action, f"actions.{aid}")
        ctx.repeat(action, aid)
        start = ctx.time(action, "start", aid)
        end = ctx.time(action, "occupation_end", aid)
        cancel = ctx.time(action, "switch_allowed_at", aid) if "switch_allowed_at" in action else end
        if start is not None and period and start >= period:
            ctx.error("action_window", "动作开始必须位于 [0, period)。", aid)
        if None not in (start, end, cancel) and not start <= cancel <= end:
            ctx.error("occupation", "动作时长或允许切出时刻无效。", aid)
        ability = abilities.get(action["ability_id"])
        if not ability:
            ctx.error("reference", "动作引用了不存在的技能定义。", aid)
            continue
        if ability["action_type_id"] == "action.switch":
            if action.get("switch_to") not in chars:
                ctx.error("reference", "切人动作需指定队伍中的目标角色。", aid)
        elif "switch_to" in action:
            ctx.error("action_type", "只有切人动作可以设置 switch_to。", aid)
        rules = {rule["id"]: rule for rule in ability.get("rules", [])}
        outcomes = {}
        for outcome in action.get("rule_outcomes", []):
            ruleid = outcome["rule_id"]
            if ruleid in outcomes or ruleid not in rules:
                ctx.error("rule_outcome", "规则结论重复或未挂载到当前技能。", aid)
                continue
            outcomes[ruleid] = outcome
            if outcome["status"] == "pending":
                ctx.pending("已挂载规则的本次触发结论待补。", f"{aid}.{ruleid}")
            elif outcome["status"] == "not_triggered":
                if not outcome.get("reason") or outcome.get("event_ids"):
                    ctx.error("rule_outcome", "不触发结论必须有理由且不能同时关联产出事件。", aid)
            else:
                ids = outcome.get("event_ids", [])
                if not ids:
                    ctx.pending("规则已声明触发，但没有生成事件。", aid)
                if len(ids) != len(set(ids)):
                    ctx.error("rule_outcome", "同一事件在规则中重复关联。", aid)
                for eid in ids:
                    ev = events.get(eid)
                    if not ev or ev["type"] != rules[ruleid]["kind"] or ev.get("action_id") != aid:
                        ctx.error("rule_outcome", "规则产出事件的类型或动作引用不匹配。", aid)
        for ruleid in rules.keys() - outcomes.keys():
            ctx.pending("已挂载规则缺少本次实例结论。", f"{aid}.{ruleid}")
        generated_ids = [eid for outcome in outcomes.values() if outcome["status"] == "triggered"
                         for eid in outcome.get("event_ids", [])]
        if len(generated_ids) != len(set(generated_ids)):
            ctx.error("rule_outcome", "一个生成事件不能同时当作多个规则各自产出的事件。", aid)
        rule_kinds = {rule["kind"] for rule in rules.values()}
        for eid, event in events.items():
            if event.get("action_id") == aid and event["type"] in rule_kinds and eid not in generated_ids:
                ctx.pending("已挂载同类规则，但产出事件未关联到本次规则结论。", f"{aid}.{eid}")
        runtime.append({"id": aid, "kind": "action", "time": start, "order": action["order"],
                        "end": end, "cancel": cancel, "data": action})
    spends = defaultdict(list)
    credits = defaultdict(list)
    for eid, ev in events.items():
        ctx.evidence(ev, f"events.{eid}")
        ctx.repeat(ev, eid)
        time = ctx.time(ev, "credit_at" if ev["type"] == "energy_credit" else "t", eid)
        if ev.get("action_id") and ev["action_id"] not in actions:
            ctx.error("reference", "事件引用的动作不存在。", eid)
        action = actions.get(ev.get("action_id"))
        if action:
            if ev["repeat"] != action["repeat"]:
                ctx.error("repeat", "动作派生事件须与动作使用相同重复策略。", eid)
            if time is not None and action.get("start") is not None:
                action_key = (int(Decimal(str(action["start"])) * TICKS), action["order"])
                if (time, ev["order"]) <= action_key:
                    ctx.error("causality", "事件必须在其来源动作之后。", eid)
        kind = ev["type"]
        if kind in ("energy_spawn", "energy_credit"):
            ctx.number(ev, "count", eid)
        if kind == "energy_spawn":
            if ev.get("element") is None:
                ctx.pending("球的元素未知。", eid)
            if "lost_count" in ev:
                ctx.number(ev, "lost_count", eid)
            if ev["source_kind"] in ("skill", "weapon") and not action:
                ctx.pending("技能/武器产球缺少来源动作。", eid)
        elif kind == "energy_credit":
            spawn = events.get(ev["spawn_id"])
            if not spawn or spawn["type"] != "energy_spawn":
                ctx.error("reference", "到账须引用一条产球事件。", eid)
            else:
                credits[ev["spawn_id"]].append(ev)
                if ev["repeat"] != spawn["repeat"]:
                    ctx.error("repeat", "到账与产球必须采用同一重复策略。", eid)
                if (time is not None and spawn.get("t") is not None
                        and (time, ev["order"]) <= (int(Decimal(str(spawn["t"])) * TICKS), spawn["order"])):
                    ctx.error("causality", "能量到账必须在产球之后。", eid)
        elif kind == "energy_direct":
            if not ev.get("amounts"):
                ctx.pending("直接回能数值未提供。", eid)
            for cid in ev.get("amounts", {}):
                if cid not in chars:
                    ctx.error("reference", "直接回能目标不存在。", eid)
                ctx.number(ev["amounts"], cid, eid)
        elif kind == "energy_spend":
            spends[ev["action_id"]].append(ev)
            need = ctx.number(ev, "required", eid)
            amount = ctx.number(ev, "amount", eid)
            ability = abilities.get(action["ability_id"]) if action else None
            if not ability or ability["character"] != ev["character"] or not ability.get("energy"):
                ctx.error("energy_spend", "消耗事件与动作角色或能量规则不匹配。", eid)
            elif (need, amount) != (ability["energy"].get("required"), ability["energy"].get("amount")):
                ctx.error("energy_spend", "消耗事件与技能定义的门槛/扣除量不一致。", eid)
            if (action and time is not None and action.get("occupation_end") is not None
                    and time > int(Decimal(str(action["occupation_end"])) * TICKS)):
                ctx.error("energy_spend", "能量消耗必须发生在对应动作占用期间。", eid)
        elif kind == "cooldown_change":
            if _key(ev["character"], ev["group"]) not in groups:
                ctx.error("reference", "冷却修改组不存在。", eid)
            if ev["operation"] != "reset":
                ctx.number(ev, "amount", eid, integer=ev["operation"] in ("restore", "restore_rebase"))
                if ev["operation"] == "reduce":
                    ctx.time(ev, "amount", eid)
            elif "amount" in ev:
                ctx.error("cooldown_change", "reset 操作不使用 amount。", eid)
        runtime.append({"id": eid, "kind": kind, "time": time, "order": ev["order"], "data": ev})
    for aid, action in actions.items():
        ability = abilities.get(action["ability_id"])
        if ability and ability.get("energy") is not None:
            if auto_energy.automatic(ability):
                if spends[aid]:
                    ctx.error("duplicate_delivery", "自动扣能动作不能同时关联手工扣能事件。", aid)
            elif not spends[aid]:
                ctx.pending("每次有能量需求的施放必须对应一条消耗事件。", aid)
            elif len(spends[aid]) != 1:
                ctx.error("duplicate_spend", "一次施放不能重复关联多条消耗。", aid)
    particle_inputs = {}
    for eid, ev in events.items():
        if ev["type"] == "energy_spawn" and ev.get("count") is not None:
            related = credits[eid]
            problem = quantity_model_issue(ev, related)
            if problem:
                ctx.error(*problem, eid)
            if all(item.get("count") is not None for item in related):
                credited = sum(quantity(item["count"]) for item in related)
                lost = quantity(ev.get("lost_count") or 0)
                produced = quantity(ev["count"])
                total = credited + lost
                balanced = close_quantity(total, produced)
                if not balanced and total > produced:
                    ctx.error("particle_count", "到账和丢失球数之和超过产出。", eid)
                elif not balanced and total < produced:
                    ctx.pending("仍有球未安排到账；丢失球必须显式计入 lost_count。", eid)
                if uses_average(ev, related):
                    # Never grow the supply to accommodate a rounded request.
                    # Ignore only a representational remainder in a group that
                    # has accounted for its entire declared supply.
                    particle_inputs[eid] = min(credited, max(0, produced - lost))
    for wid, window in windows.items():
        ctx.evidence(window, wid)
        ctx.repeat(window, wid)
        if window["character"] not in chars:
            ctx.error("reference", "临时充能效率目标不存在。", wid)
        start, end = ctx.time(window, "start", wid), ctx.time(window, "end", wid)
        delta = window.get("delta")
        if delta is None:
            ctx.pending("临时充能效率数值未知。", wid)
        if None not in (start, end) and end <= start:
            ctx.error("window", "临时充能效率区间必须有正时长。", wid)
    for index, gap in enumerate(model.get("gap_explanations", [])):
        start, end = ctx.time(gap, "start", f"gap_explanations.{index}"), ctx.time(gap, "end", f"gap_explanations.{index}")
        if None not in (start, end) and (end <= start or (period is not None and end > period)):
            ctx.error("gap", "空档说明应位于单轮 [0, period] 内且有正时长。", f"gap_explanations.{index}")
    if template is not None:
        if not isinstance(template, dict) or not isinstance(template.get("characters"), list):
            ctx.error("template", "需传入包含角色数组的伤害模板。", "template")
        else:
            template_ids = {char.get("id") for char in template["characters"] if isinstance(char, dict)}
            if set(chars) != template_ids:
                ctx.error("template_characters", "检查队伍必须与伤害模板角色一致。", "characters")
            if (template.get("loop_duration") is not None and template["loop_duration"] != model.get("period")
                    and not (model.get("damage_report", {}).get("mode") == "explicit"
                             and template["loop_duration"] == model["damage_report"].get("end"))):
                ctx.error("period_conflict", "伤害模板周期与动作检查周期不一致。", "period")
            _check_links(template, actions, abilities, model, ctx)
    # 保持输出为有限 JSON；荒谬的大数不进入浮点能量运算。
    for path, obj in [("characters", char) for char in chars.values()]:
        for key in ("energy_recharge", "energy_capacity", "initial_energy"):
            if obj.get(key) is not None and obj[key] > 1e9:
                ctx.error("limit", "能量或充能倍率超过数值检查器上限 10 亿。", f"{path}.{key}")
    for eid, event in events.items():
        for key in ("count", "lost_count", "required", "amount"):
            if event.get(key) is not None and event[key] > 1e9:
                ctx.error("limit", "事件数值超过检查器上限 10 亿。", f"{eid}.{key}")
        if any(value is not None and value > 1e9 for value in event.get("amounts", {}).values()):
            ctx.error("limit", "直接回能数值超过检查器上限 10 亿。", eid)
    for wid, window in windows.items():
        if window.get("delta") is not None and abs(window["delta"]) > 1e9:
            ctx.error("limit", "充能效率区间数值超过检查器上限 10 亿。", wid)
    return ctx, {"period": period, "chars": chars, "abilities": abilities, "actions": actions,
                 "events": events, "groups": groups, "initial_cd": initial_cd,
                  "runtime": runtime, "windows": windows, "average_particle_inputs": particle_inputs,
                  "sequence_groups": sequence_groups,
                  "sequence_specs": sequence_specs}


def _check_links(template, actions, abilities, model, ctx):
    for field in ("hits", "switches"):
        records = template.get(field, [])
        if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
            ctx.error("template", "伤害与切人记录必须是对象数组。", f"template.{field}")
            return
        for item in records:
            time = item.get("t")
            if time is not None and (not isinstance(time, (int, float)) or isinstance(time, bool)
                                     or not math.isfinite(time)):
                ctx.error("template", "伤害与切人时间必须是有限数值。", f"template.{field}.t")
                return
    hits = {hit.get("id"): hit for hit in template.get("hits", []) if isinstance(hit, dict)}
    linked = set()
    for link in model.get("action_hit_links", []):
        hid, aid = link["hit_id"], link["action_id"]
        action, hit = actions.get(aid), hits.get(hid)
        if not action or hit is None or hid in linked:
            ctx.error("action_hit_link", "动作/伤害引用不存在或一个伤害重复关联。", hid)
            continue
        linked.add(hid)
        ability = abilities.get(action["ability_id"])
        if ability and hit.get("character") != ability["character"]:
            ctx.error("action_hit_link", "伤害角色与来源动作角色不一致。", hid)
        if hit.get("t") is not None and action.get("start") is not None and hit["t"] < action["start"]:
            ctx.error("action_hit_link", "伤害先于来源动作。", hid)
        if hit.get("count", 1) > 1:
            ctx.pending("聚合伤害不能确认真实多段时序，请拆分或另行保留未检查结论。", str(hid))
    for hid in hits.keys() - linked:
        ctx.pending("伤害未关联独立施放动作。", str(hid))
    switches = [(item.get("t"), item.get("character")) for item in template.get("switches", [])]
    if any(time is None or not isinstance(cid, str) for time, cid in switches):
        ctx.error("template", "切人记录缺少时间或角色身份。", "template.switches")
        return
    # 伤害核心没有独立 initial_frontline 字段，默认使用角色数组首项。
    # 当动作账本另选初态时，必须有明确的 t=0 标记桥接两层。
    initial = model.get("initial_frontline")
    first_id = template["characters"][0].get("id") if template["characters"] else None
    switches.sort(key=lambda value: value[0])
    if initial is not None and initial != first_id and (not switches or switches[0] != (0, initial)):
        ctx.error("initial_frontline_conflict", "动作初始前台不同于伤害模板默认前台，需要明确 t=0 前台标记。", "initial_frontline")
    switch_actions = [a for a in actions.values()
                      if abilities.get(a["ability_id"], {}).get("action_type_id") == "action.switch"
                      and a.get("start") is not None
                      and (a["repeat"]["mode"] != "cycles" or 0 in a["repeat"].get("cycles", []))]
    derived = [(a["start"], a.get("switch_to")) for a in sorted(switch_actions, key=lambda a: (a["start"], a["order"]))]
    # 旧模板中 t=0 的初始前台标识不要求额外制造切人动作。
    if switches and switches[0] == (0, initial):
        switches = switches[1:]
    if switches != derived:
        differences = []
        ordered_actions = sorted(switch_actions, key=lambda a: (a["start"], a["order"]))
        for index in range(max(len(switches), len(derived))):
            expected = derived[index] if index < len(derived) else None
            actual = switches[index] if index < len(switches) else None
            if expected != actual:
                action = ordered_actions[index] if index < len(ordered_actions) else None
                differences.append({"index": index, "action_id": action["id"] if action else None,
                                    "expected": {"t": expected[0], "character": expected[1]} if expected else None,
                                    "actual": {"t": actual[0], "character": actual[1]} if actual else None,
                                    "occupation_end": action.get("occupation_end") if action else None})
        ctx.error("switch_conflict", "切人记录须使用切人动作的 start（不是 occupation_end）；请按 differences 修正时间、角色或遗漏动作。",
                  "switches", differences=differences, expected_switches=[{"t": t, "character": c} for t, c in derived],
                  actual_switches=[{"t": t, "character": c} for t, c in switches])


def _origins(repeat, cycles):
    if repeat["mode"] == "each_cycle":
        return range(cycles)
    if repeat["mode"] == "once":
        return [0]
    return repeat["cycles"]


def _expand(data, model, ctx):
    period = data["period"]
    count = model.get("max_cycles", 3)
    expanded = []
    finite_until = 0
    for event in data["runtime"]:
        for origin in _origins(event["data"]["repeat"], count):
            offset = origin * period
            item = {**event, "time": event["time"] + offset, "origin": origin,
                    "run_id": f"{origin}:{event['id']}"}
            if event["kind"] == "action":
                item.update(end=event["end"] + offset, cancel=event["cancel"] + offset)
            if event["data"]["repeat"]["mode"] != "each_cycle":
                finite_until = max(finite_until, item.get("end", item["time"]) + 1)
            if origin < count:
                expanded.append(item)
    expanded.sort(key=lambda event: (event["time"], event["order"]))
    if len(expanded) > 40000:
        ctx.error("limit", "展开事件数超过 40000。", "events")
    seen = set()
    for event in expanded:
        key = (event["time"], event["order"])
        if key in seen:
            ctx.error("ambiguous_order", "同刻 order 冲突；禁止依赖数组或 ID 隐式决定顺序。", event["id"])
        seen.add(key)
    er_windows = []
    for window in data["windows"].values():
        for origin in _origins(window["repeat"], count):
            offset = origin * period
            start = int(Decimal(str(window["start"])) * TICKS) + offset
            end = int(Decimal(str(window["end"])) * TICKS) + offset
            if window["repeat"]["mode"] != "each_cycle":
                finite_until = max(finite_until, end)
            if origin < count:
                er_windows.append({**window, "start": start, "end": end, "origin": origin})
    for cid, char in data["chars"].items():
        bounds = {0}
        relevant = [w for w in er_windows if w["character"] == cid]
        bounds.update(w["start"] for w in relevant)
        bounds.update(w["end"] for w in relevant)
        for time in bounds:
            if char["energy_recharge"] + sum(w["delta"] for w in relevant if w["start"] <= time < w["end"]) <= 0:
                ctx.error("energy_recharge", "临时区间合成后的充能效率必须大于 0。", cid)
    return expanded, er_windows, finite_until


def _base_result(status, issues=None, assumptions=None):
    return {"schema_version": 1, "status": status, "issues": issues or [],
            "assumptions": sorted(set(assumptions or [])),
            "determinacy": "conditional" if assumptions else "confirmed_inputs",
            "unchecked_constraints": list(UNCHECKED),
            "action_check": {"status": status, "checked_constraints": ["前台", "动作占用", "冷却与技能次数"]},
            "energy_check": {"status": status, "opening_status": status, "cycle_status": status,
                             "checked_cycles": 0, "completed_cycles": 0,
                             "failed_cycle": None, "proven_cycle": None,
                             "proof_scope": {"kind": "none", "damage_verified": False}},
            "event_log": [], "cycles": [], "characters": [], "gaps": [],
            "damage_interpretation": "计划伤害；本检查不认证元素反应、Buff 完整性或稳态 DPS。"}


def check_rotation(template, rotation_model=None):
    """返回动作与能量检查，不修改输入、不调用伤害计算。

    None 明确表示未配置。结构错误 invalid，资料缺失 pending，执行失败 failed；
    有限轮次未失败仍可能 unproven；周期动作实际执行且完整边界稳定才 passed。
    passed 只认证 proof_scope 中的周期动作与能量，不认证有限开场动作循环或 DPS。
    """
    if rotation_model is None:
        return _base_result("not_checked")
    validation = validate_rotation_model(rotation_model)
    if validation["status"] == "invalid":
        return _base_result("invalid", validation["issues"])
    model = copy.deepcopy(rotation_model)
    ctx, data = _semantic(model, template)
    if ctx.issues:
        state = "invalid" if any(x["severity"] == "error" for x in ctx.issues) else "pending"
        return _base_result(state, ctx.issues, ctx.assumptions)
    expanded, windows, finite_until = _expand(data, model, ctx)
    if ctx.issues:
        return _base_result("invalid", ctx.issues, ctx.assumptions)
    from genshin_engine.snapshot_capture import SnapshotCapturePending
    try:
        return _simulate(model, data, expanded, windows, finite_until, ctx)
    except SnapshotCapturePending as exc:
        # Pure Rotation uses the same finite-value guard as Mechanics. A
        # retained prefix is diagnostic only, never an accepted partial gain.
        if hasattr(exc, "rotation_check_prefix"):
            return exc.rotation_check_prefix
        raise


def _simulate(model, data, expanded, windows, finite_until, ctx, observer=None):
    result = _base_result("unproven", assumptions=ctx.assumptions)
    chars, abilities = data["chars"], data["abilities"]
    period = data["period"]
    max_cycles = model.get("max_cycles", 3)
    energy = {cid: c["initial_energy"] for cid, c in chars.items()}
    queues = copy.deepcopy(data["initial_cd"])
    queue_entry_ids = {}
    queue_serials = {}
    for group, queue in queues.items():
        queue_entry_ids[group] = [
            {"entry_id": f"initial:{group}:{index}", "origin": "initial", "serial": index}
            for index in range(len(queue))
        ]
        queue_serials[group] = len(queue)
    frontline = model["initial_frontline"]
    busy_end = cancel_at = 0
    spawned = {}
    average_spawns = set()
    average_inputs = data.get("average_particle_inputs", {})

    def insufficient_energy(before, need):
        if average_spawns:
            return before < need and not close_quantity(before, need)
        return need - before > EPS
    cursor = 0
    fail_kind = None
    previous_boundary = None
    cycle_start_energy = dict(energy)
    occupied = []
    off_field = {1: 1, 2: 0.8, 3: 0.7, 4: 0.6}[len(chars)]
    admitted_spends = {}
    active_action = None
    cast_sequences = {}
    has_cast_sequences = bool(data.get("sequence_groups"))
    ledger = (auto_energy.Ledger(expanded, abilities, max_cycles * period)
              if any(auto_energy.automatic(a) for a in abilities.values()) else None)
    capacity_ledger = (capacity_audit.Ledger(expanded, abilities, max_cycles * period)
                       if any(capacity_audit.bound(a) for a in abilities.values()) else None)
    admission_ledger = (admission_audit.Ledger(expanded, abilities, max_cycles * period)
                        if any(admission_audit.bound(a) for a in abilities.values()) else None)
    termination = None
    periodic_action_ids = sorted(aid for aid, action in data["actions"].items()
                                 if action["repeat"]["mode"] == "each_cycle")
    result["energy_check"]["proof_scope"] = {
        "kind": "checked_windows", "period_seconds": model["period"],
        "periodic_action_ids": periodic_action_ids,
        "finite_action_ids": sorted(set(data["actions"]) - set(periodic_action_ids)),
        "damage_verified": False,
    }

    def sort_queue(group):
        ordered = sorted(
            zip(queues[group], queue_entry_ids[group]),
            key=lambda pair: (pair[0], pair[1]["serial"]),
        )
        queues[group] = [ready for ready, _ in ordered]
        queue_entry_ids[group] = [identity for _, identity in ordered]

    def assert_queue_identity(group):
        """Keep numeric public queues and private stable identities atomic."""
        identities = queue_entry_ids[group]
        assert len(queues[group]) == len(identities)
        serials = [identity["serial"] for identity in identities]
        assert len(serials) == len(set(serials))
        assert all(serial < queue_serials[group] for serial in serials)
        assert list(zip(queues[group], serials)) == sorted(zip(queues[group], serials))

    def append_queue(group, ready, origin):
        serial = queue_serials[group]
        queue_serials[group] += 1
        entry_id = f"action:{origin}:{serial}"
        queues[group].append(ready)
        queue_entry_ids[group].append({
            "entry_id": entry_id, "origin": origin, "serial": serial,
        })
        sort_queue(group)
        assert_queue_identity(group)
        return entry_id

    def queue_index(group, entry_id):
        return next((index for index, identity in enumerate(queue_entry_ids[group])
                     if identity["entry_id"] == entry_id), None)

    def restore(time):
        for group in queues:
            retained = [(ready, identity) for ready, identity
                        in zip(queues[group], queue_entry_ids[group]) if ready > time]
            queues[group] = [ready for ready, _ in retained]
            queue_entry_ids[group] = [identity for _, identity in retained]
            assert_queue_identity(group)
        # A continuation window is independent from the base cooldown queue.
        # Expiry only removes permission to cast the next stage; it does not
        # create or restart cooldown because sequence_start committed that on
        # the first successful cast.
        for group, state in list(cast_sequences.items()):
            if state["expires_at"] <= time:
                cast_sequences.pop(group)

    for initial_group in queues:
        sort_queue(initial_group)
        assert_queue_identity(initial_group)

    def gain(cid, raw, row, er=None):
        prepared = plan_energy_gain(cid, energy[cid], chars[cid]["energy_capacity"], raw,
                                    frontline=frontline, energy_recharge=er)
        energy[cid] = prepared["after"]
        row.setdefault("energy", []).append(prepared)

    def retain_pending(exc, row, ev, logged, *, boundary_time=None):
        # A capture can fail during ER sampling as well as on_event.
        from genshin_engine.attribute_ledger import AttributeLedgerError
        from genshin_engine.shared_pools import SharedPoolError
        from genshin_engine.snapshot_capture import SnapshotCapturePending
        from genshin_engine.state_runtime import StateRuntimeError
        issue = getattr(exc, "issue", None)
        controlled_pool = (isinstance(exc, SharedPoolError) and getattr(observer, "shared_pools", None) is not None
                           and isinstance(issue, dict)
                           and issue.get("code") in ("pool_event_limit", "pool_receipt_limit", "pool_time_limit", "counter_limit", "counter_missing_control", "counter_view_limit", "counter_view_value",
                              "threshold_limit", "threshold_dispatch_limit", "threshold_condition_unknown", "threshold_dispatch_failed", "threshold_dispatch_unclaimed"))
        controlled_reaction_resource = (isinstance(exc, SharedPoolError) and getattr(observer, "resource_reactions", None) is not None
            and isinstance(issue, dict) and issue.get("code", "").startswith("resource_reaction_"))
        controlled_attribute = isinstance(exc, AttributeLedgerError) and getattr(observer, "attribute_runtime", None) is not None
        controlled_state = (isinstance(exc, StateRuntimeError) and getattr(observer, "state_runtime", None) is not None
                            and isinstance(issue, dict) and issue.get("code") in ("state_event_limit", "state_trace_limit", "state_context_limit", "state_capture_limit", "state_dispatch_pending", "state_position_limit", "state_pending_scan"))
        if not ((ledger is not None or capacity_ledger is not None or admission_ledger is not None
                 or isinstance(exc, SnapshotCapturePending) or controlled_pool
                 or controlled_reaction_resource or controlled_attribute or controlled_state)
                and isinstance(issue, dict) and issue.get("severity") == "pending"):
            return
        if row is not None:
            row["status"] = "pending"
            if not logged:
                result["event_log"].append(row)
        result["status"] = "pending"
        result["issues"].append(copy.deepcopy(issue))
        result["action_check"]["status"] = "not_verified"
        result["energy_check"].update(status="not_verified", cycle_status="not_verified",
            proven_cycle=None, checked_cycles=cycle + 1)
        result["energy_check"]["proof_scope"]["kind"] = "checked_windows"
        result["cycles"].append({"cycle": cycle, "status": "not_verified", "incomplete": True,
            "start_energy": cycle_start_energy, "end_energy": dict(energy), **activity,
            "boundary_state": None, "periodic_action_coverage": False})
        result["characters"] = [{"id": c, **chars[c], "final_energy": energy[c]} for c in chars]
        stopped = ({"kind": "pending", "code": issue.get("code"), "phase": "cycle_boundary",
                    "parent_run_id": None, "cycle": cycle, "t": boundary_time / TICKS}
                   if boundary_time is not None else
                   {"kind": "pending", "code": issue.get("code"),
                    "parent_run_id": ev["run_id"], "cycle": cycle, "t": time / TICKS,
                    "order": ev["order"], "system_priority": ev.get("system_priority", 0)})
        if ledger is not None:
            ledger.finish(result, stopped)
        if capacity_ledger is not None:
            capacity_ledger.finish(result, stopped)
        if admission_ledger is not None:
            admission_ledger.finish(result, stopped)
        exc.rotation_check_prefix = result

    for cycle in range(max_cycles):
        boundary = (cycle + 1) * period
        cycle_failed = False
        activity = {"scheduled_action_count": 0, "executed_action_count": 0,
                    "burst_action_count": 0, "energy_spend_count": 0,
                    "burst_energy_spend_count": 0}
        periodic_executed = set()
        while cursor < len(expanded) and expanded[cursor]["time"] < boundary:
            ev = expanded[cursor]
            cursor += 1
            if ev["time"] < cycle * period:
                continue
            time, payload, kind = ev["time"], ev["data"], ev["kind"]
            row = {"id": ev["id"], "run_id": ev["run_id"], "cycle": cycle,
                   "source_cycle": ev["origin"], "type": kind, "t": time / TICKS,
                   "order": ev["order"], "frontline": frontline, "status": "passed",
                   "source": payload.get("source"), "action_id": payload.get("action_id")}
            if 'resource_cause' in payload:
                row['resource_cause'] = copy.deepcopy(payload['resource_cause'])
            if kind == "action":
                activity["scheduled_action_count"] += 1
            if fail_kind:
                row["status"] = "not_verified"
                auto_energy.mark_parent(row, ev, abilities, "not_verified")
                capacity_audit.mark_parent(row, ev, abilities, "not_verified")
                admission_audit.mark_parent(row, ev, abilities, "not_verified")
                result["event_log"].append(row)
                continue
            restore(time)
            failure = None
            logged = False
            audit = None
            audit_size = None
            capacity_receipt = capacity_size = None
            admission_receipt = admission_size = None
            budget_issue = None
            if kind == "action":
                ability = abilities[payload["ability_id"]]
                cid, action_type = ability["character"], ability["action_type_id"]
                automatic_action = auto_energy.automatic(ability)
                capacity_action = capacity_audit.bound(ability)
                admission_action = admission_audit.bound(ability)
                deferred_cooldown = None
                inserted_cooldown_entry = None
                row.update(character=cid, action_type_id=action_type, action_id=ev["id"],
                           occupation_end=ev["end"] / TICKS)
                if frontline != cid:
                    failure = ("action", "not_frontline", "施放角色不在前台。")
                elif time < busy_end and (action_type != "action.switch" or time < cancel_at):
                    failure = ("action", "action_overlap", "动作占用冲突或尚不能切出。")
                # 新机制路径必须在发出动作触发前检查施放门槛；旧检查语义不变。
                if not failure and (observer is not None or automatic_action or capacity_action
                                    or admission_action) and ability.get("energy"):
                    need = max(ability["energy"]["required"], ability["energy"]["amount"])
                    if (insufficient_energy(energy[cid], need) if average_spawns else energy[cid] + EPS < need):
                        row.update(before=energy[cid], required=need, deficit=need-energy[cid])
                        failure = ("energy", "insufficient_energy", "动作开始时能量不足，未触发该动作的机制或伤害。")
                cooldown = ability.get("cooldown")
                sequence = ability.get("cast_sequence")
                sequence_key = (_key(cid, sequence["group"]) if sequence else None)
                sequence_state = cast_sequences.get(sequence_key) if sequence else None
                sequence_spec = data.get("sequence_specs", {}).get(sequence_key) if sequence else None
                if not failure and sequence:
                    stage = sequence["stage"]
                    if stage == 1:
                        if sequence_state is not None:
                            row["cast_sequence"] = {
                                "group": sequence["group"], "stage": stage,
                                "max_stages": sequence["max_stages"],
                                "expected_stage": sequence_state["next_stage"],
                                "expires_at": sequence_state["expires_at"] / TICKS,
                            }
                            failure = ("action", "cast_sequence_stage",
                                       "连携仍在进行中，当前应施放下一段而不是重新从首段开始。")
                    elif sequence_state is None:
                        row["cast_sequence"] = {
                            "group": sequence["group"], "stage": stage,
                            "max_stages": sequence["max_stages"],
                            "expected_stage": 1,
                        }
                        failure = ("action", "cast_sequence_unavailable",
                                   "续段窗口不存在或已经过期，不能直接施放该段。")
                    elif sequence_state["next_stage"] != stage:
                        row["cast_sequence"] = {
                            "group": sequence["group"], "stage": stage,
                            "max_stages": sequence["max_stages"],
                            "expected_stage": sequence_state["next_stage"],
                            "expires_at": sequence_state["expires_at"] / TICKS,
                        }
                        failure = ("action", "cast_sequence_stage",
                                   "连携段数与当前运行状态不一致。")
                if not failure and cooldown:
                    group = _key(cid, cooldown["group"])
                    queue = queues[group]
                    row["charges_before"] = cooldown["charges"] - len(queue)
                    if len(queue) >= cooldown["charges"]:
                        row["next_ready_at"] = min(queue) / TICKS
                        failure = ("action", "cooldown_unavailable", "技能仍在冷却，或次数尚未恢复。")
                    else:
                        start = time if cooldown["start"] == "action_start" else ev["end"]
                        duration = int(Decimal(str(cooldown["duration"])) * TICKS)
                        prior_tail = max(queue, default=0) if cooldown["recovery"] == "sequential" else 0
                        prior_entry_id = (queue_entry_ids[group][-1]["entry_id"]
                                          if cooldown["recovery"] == "sequential"
                                          and queue and prior_tail > start else None)
                        ready = (max([start, *queue]) if cooldown["recovery"] == "sequential" else start) + duration
                        if automatic_action or capacity_action or admission_action:
                            deferred_cooldown = (ready, group, prior_tail, prior_entry_id, start, duration)
                        else:
                            entry_id = append_queue(group, ready, ev["run_id"])
                            inserted_cooldown_entry = {
                                "group": group, "entry_id": entry_id,
                                "start": start, "end": ev["end"], "duration": duration,
                                "prior_tail": prior_tail, "prior_entry_id": prior_entry_id,
                                "recovery": cooldown["recovery"],
                            }
                            row["next_ready_at"] = ready / TICKS
                            if cooldown["start"] == "occupation_end":
                                ev["cooldown_entry"] = inserted_cooldown_entry
                if capacity_action and (not failure or failure[1] == "cooldown_unavailable"):
                    rejected = failure is not None
                    try:
                        capacity_receipt, capacity_size = capacity_ledger.prepare(
                            ev, ability, cycle, queue, None if rejected else ready, rejected=rejected)
                    except capacity_audit.AuditLimit as exc:
                        budget_issue = exc.issue
                        failure = ("pending", exc.issue["code"], exc.issue["message"])
                    else:
                        if rejected:
                            capacity_ledger.commit(capacity_receipt, capacity_size)
                            capacity_audit.mark_parent(row, ev, abilities, "rejected", failure[1], capacity_receipt)
                if not failure and automatic_action:
                    audit = auto_energy.audit_row(ev, ability, cycle, energy[cid])
                    try:
                        audit_size = ledger.reserve(audit)
                    except auto_energy.AuditLimit as exc:
                        budget_issue = exc.issue
                        failure = ("pending", exc.issue["code"], exc.issue["message"])
                if not failure and admission_action:
                    energy_amount = (ability.get("energy") or {}).get("amount", 0)
                    frontline_after = payload.get("switch_to", frontline)
                    try:
                        admission_receipt, admission_size = admission_ledger.prepare(
                            ev,
                            ability,
                            cycle,
                            frontline_before=frontline,
                            frontline_after=frontline_after,
                            energy_before=energy[cid],
                            energy_after=max(0, energy[cid] - energy_amount),
                            charges_before=cooldown["charges"] - len(queue),
                            charges_after=cooldown["charges"] - len(queue) - 1,
                            next_ready_at=min([*queue, ready]) / TICKS,
                        )
                    except admission_audit.AuditLimit as exc:
                        budget_issue = exc.issue
                        failure = ("pending", exc.issue["code"], exc.issue["message"])
                if not failure:
                    if deferred_cooldown is not None:
                        ready, group, prior_tail, prior_entry_id, start, duration = deferred_cooldown
                        entry_id = append_queue(group, ready, ev["run_id"])
                        inserted_cooldown_entry = {
                            "group": group, "entry_id": entry_id,
                            "start": start, "end": ev["end"], "duration": duration,
                            "prior_tail": prior_tail, "prior_entry_id": prior_entry_id,
                            "recovery": cooldown["recovery"],
                        }
                        row["next_ready_at"] = ready / TICKS
                        if cooldown["start"] == "occupation_end":
                            ev["cooldown_entry"] = inserted_cooldown_entry
                    activity["executed_action_count"] += 1
                    if sequence:
                        stage = sequence["stage"]
                        base_queue = queues[sequence_spec["cooldown_group"]]
                        sequence_row = {
                            "group": sequence["group"],
                            "stage": stage,
                            "max_stages": sequence["max_stages"],
                            "cooldown_start": sequence["cooldown_start"],
                            "admission": ("sequence_start" if stage == 1
                                          else "continuation_window"),
                            "base_cooldown": {
                                "group": sequence_spec["cooldown_group_id"],
                                "running": bool(base_queue),
                                "ready_at": min(base_queue) / TICKS if base_queue else None,
                            },
                        }
                        if stage < sequence["max_stages"]:
                            window = int(Decimal(str(sequence["continuation_window"])) * TICKS)
                            expires_at = time + window
                            cast_sequences[sequence_key] = {
                                "next_stage": stage + 1,
                                "expires_at": expires_at,
                                "source_run_id": ev["run_id"],
                            }
                            sequence_row.update(
                                next_stage=stage + 1,
                                continuation_expires_at=expires_at / TICKS,
                            )
                        else:
                            cast_sequences.pop(sequence_key, None)
                            sequence_row["completed"] = True
                        row["cast_sequence"] = sequence_row
                    if payload["repeat"]["mode"] == "each_cycle":
                        periodic_executed.add(ev["id"])
                    if action_type == "action.elemental_burst":
                        activity["burst_action_count"] += 1
                    if (observer is not None or automatic_action or admission_action) and ability.get("energy"):
                        before = energy[cid]
                        energy[cid] = max(0, before - ability["energy"]["amount"])
                        admitted_spends[(ev["origin"], ev["id"])] = (before, energy[cid])
                        row["energy_admission"] = {"before": before, "after": energy[cid],
                                                   "amount": ability["energy"]["amount"]}
                        if ability["energy"]["amount"] > 0:
                            activity["energy_spend_count"] += 1
                            if action_type == "action.elemental_burst":
                                activity["burst_energy_spend_count"] += 1
                    if action_type == "action.switch":
                        # 明确取消上一动作余下后摇；不修改后台伤害事件。
                        if occupied and time < occupied[-1][1]:
                            occupied[-1][1] = time
                            if active_action is not None:
                                row["cancelled_action"] = {"id": active_action["id"], "origin": active_action["origin"],
                                                           "ability_id": active_action["data"]["ability_id"]}
                                entry = active_action.get("cooldown_entry")
                                entry_index = (queue_index(entry["group"], entry["entry_id"])
                                               if entry else None)
                                if entry is not None and entry_index is not None:
                                    old_ready = queues[entry["group"]][entry_index]
                                    shift = max(entry["end"], entry["prior_tail"]) - max(time, entry["prior_tail"])
                                    new_ready = max(time, old_ready-shift)
                                    identity = queue_entry_ids[entry["group"]].pop(entry_index)
                                    queues[entry["group"]].pop(entry_index)
                                    if new_ready > time:
                                        queues[entry["group"]].append(new_ready)
                                        queue_entry_ids[entry["group"]].append(identity)
                                        sort_queue(entry["group"])
                                    else:
                                        active_action.pop("cooldown_entry", None)
                                    # A switch may itself consume the same
                                    # sequential pool before it cancels the
                                    # preceding occupation. Rebase that newly
                                    # inserted dependent on the moved tail.
                                    child = inserted_cooldown_entry
                                    if (child is not None
                                            and child["group"] == entry["group"]
                                            and child["recovery"] == "sequential"
                                            and child["prior_entry_id"] == entry["entry_id"]):
                                        child_index = queue_index(child["group"], child["entry_id"])
                                        if child_index is not None:
                                            child_ready = max(child["start"], new_ready) + child["duration"]
                                            queues[child["group"]][child_index] = child_ready
                                            child["prior_tail"] = new_ready
                                            sort_queue(child["group"])
                                            row["next_ready_at"] = child_ready / TICKS
                                    assert_queue_identity(entry["group"])
                                    row["cancelled_cooldown"] = {"group": entry["group"], "before": old_ready/TICKS, "after": new_ready/TICKS}
                        frontline = payload["switch_to"]
                    busy_end, cancel_at = ev["end"], ev["cancel"]
                    occupied.append([time, busy_end])
                    active_action = ev
                    row["frontline_after"] = frontline
                    if automatic_action:
                        auto_energy.mark_parent(row, ev, abilities, "committed")
                        ledger.commit(audit_size)
                    if capacity_action:
                        capacity_ledger.commit(capacity_receipt, capacity_size)
                        capacity_audit.mark_parent(row, ev, abilities, "committed", receipt=capacity_receipt)
                    if admission_action:
                        admission_ledger.commit(admission_receipt, admission_size)
                        admission_audit.mark_parent(
                            row, ev, abilities, "committed", receipt=admission_receipt
                        )
                    if automatic_action:
                        result["event_log"].extend([row, audit])
                        logged = True
                elif automatic_action:
                    auto_energy.mark_parent(row, ev, abilities, "not_committed", failure[1])
                if failure and capacity_action and "capacity_admission" not in row:
                    capacity_audit.mark_parent(row, ev, abilities,
                        "not_committed" if budget_issue else "not_checked", failure[1])
                if failure and admission_action:
                    admission_audit.mark_parent(
                        row,
                        ev,
                        abilities,
                        "not_committed" if budget_issue else "rejected",
                        failure[1],
                    )
            elif kind == "energy_spawn":
                spawn_key = (ev["origin"], ev["id"])
                available = (average_inputs[ev["id"]] if ev["id"] in average_inputs else
                             quantity(payload["count"]) - quantity(payload.get("lost_count", 0)))
                spawned[spawn_key] = available
                row.update(count=payload["count"], element=payload["element"],
                           lost_count=payload.get("lost_count", 0), particle_type=payload["particle_type"])
                if ev["id"] in average_inputs or uses_average(payload):
                    average_spawns.add(spawn_key)
                    result["energy_check"]["particle_quantity"] = average_scope()
                    row.update(count_mode="average", remaining_count=as_number(available))
                    if "count_distribution" in payload:
                        row["count_distribution"] = copy.deepcopy(payload["count_distribution"])
            elif kind == "energy_credit":
                spawn = data["events"][payload["spawn_id"]]
                spawn_key = (ev["origin"], payload["spawn_id"])
                available = spawned.get(spawn_key)
                requested = quantity(payload["count"])
                if available is None or (available < requested and not close_quantity(
                        available, requested, scale=spawn["count"])):
                    failure = ("energy", "unavailable_particles", "当前产球组尚未产生或可到账数量不足。")
                else:
                    settled = min(available, requested)
                    count = payload["count"] if settled == requested else as_number(settled)
                    prepared_gains = {}
                    begin = getattr(observer, "begin_energy_credit", None)
                    finish = getattr(observer, "finish_energy_credit", None)
                    if begin is not None:
                        begin(ev)
                    try:
                        # Source identity belongs to the actual delayed event,
                        # irrespective of capture declarations. Resolve every
                        # receiver before consuming particles or publishing a
                        # balance; finite ER alone does not prevent gain overflow.
                        for cid in sorted(chars):
                            char = chars[cid]
                            base = 2 if spawn["element"] == "clear" else 3 if spawn["element"] == char["element"] else 1
                            if spawn["particle_type"] == "orb":
                                base *= 3
                            er = char["energy_recharge"] + sum(w["delta"] for w in windows
                                if w["character"] == cid and w["start"] <= time < w["end"])
                            if observer is not None:
                                er += observer.energy_recharge(cid, time, frontline, ev)
                            raw = count * base * (1 if cid == frontline else off_field) * er
                            prepared_gains[cid] = plan_energy_gain(cid, energy[cid], char["energy_capacity"], raw,
                                frontline=frontline, energy_recharge=er)
                    except Exception as exc:
                        if finish is not None:
                            finish(ev, committed=False)
                        retain_pending(exc, row, ev, logged)
                        raise
                    spawned[spawn_key] -= settled
                    row.update(spawn_id=payload["spawn_id"], count=count,
                               element=spawn["element"], particle_type=spawn["particle_type"])
                    if spawn_key in average_spawns:
                        row.update(count_mode="average", remaining_count=as_number(spawned[spawn_key]))
                        if settled != requested:
                            row["declared_count"] = payload["count"]
                    for cid in chars:
                        energy[cid] = prepared_gains[cid]["after"]
                        row.setdefault("energy", []).append(prepared_gains[cid])
                    if finish is not None:
                        finish(ev, committed=True)
            elif kind == "energy_direct":
                # Freeze every receiver's pre-credit ER before any balance is
                # published. Direct amounts are base quantities (project rule).
                prepared_gains = {}
                begin = getattr(observer, "begin_energy_credit", None)
                finish = getattr(observer, "finish_energy_credit", None)
                if begin is not None:
                    begin(ev)
                try:
                    for cid, amount in sorted(payload["amounts"].items()):
                        char = chars[cid]
                        er = char["energy_recharge"] + sum(w["delta"] for w in windows
                            if w["character"] == cid and w["start"] <= time < w["end"])
                        if observer is not None:
                            er += observer.energy_recharge(cid, time, frontline, ev)
                        prepared_gains[cid] = plan_energy_gain(cid, energy[cid], char["energy_capacity"],
                            amount * er, frontline=frontline, energy_recharge=er)
                        prepared_gains[cid].update(base_amount=amount, theoretical_amount=amount * er,
                            settlement_contract="energy-direct-base-er-v1")
                except Exception as exc:
                    if finish is not None:
                        finish(ev, committed=False)
                    retain_pending(exc, row, ev, logged)
                    raise
                for cid, prepared in prepared_gains.items():
                    energy[cid] = prepared["after"]
                    row.setdefault("energy", []).append(prepared)
                if finish is not None:
                    finish(ev, committed=True)
            elif kind == "energy_spend":
                cid = payload["character"]
                before = energy[cid]
                admitted = admitted_spends.get((ev["origin"], payload["action_id"]))
                if observer is not None and admitted is not None:
                    row.update(character=cid, before=admitted[0], required=payload["required"],
                               amount=payload["amount"], deficit=0, after=admitted[1],
                               committed_at_action_start=True)
                    result["event_log"].append(row)
                    continue
                deficit = max(0, payload["required"] - before, payload["amount"] - before)
                row.update(character=cid, before=before, required=payload["required"],
                           amount=payload["amount"], deficit=deficit, after=before)
                if insufficient_energy(before, max(payload["required"], payload["amount"])):
                    failure = ("energy", "insufficient_energy", "爆发/动作能量不足；不赊账，不自动移动或删除动作。")
                else:
                    energy[cid] = max(0, before - payload["amount"])
                    row["after"] = energy[cid]
                    if payload["amount"] > 0:
                        activity["energy_spend_count"] += 1
                        ability = abilities[data["actions"][payload["action_id"]]["ability_id"]]
                        if ability["action_type_id"] == "action.elemental_burst":
                            activity["burst_energy_spend_count"] += 1
            elif kind == "cooldown_change":
                group = _key(payload["character"], payload["group"])
                operation = payload["operation"]
                active_entry = active_action.get("cooldown_entry") if active_action else None
                if operation == "reset":
                    queues[group] = []
                    queue_entry_ids[group] = []
                    assert_queue_identity(group)
                    if active_entry and active_entry["group"] == group:
                        active_action.pop("cooldown_entry", None)
                elif operation in ("restore", "restore_rebase"):
                    restored_count = int(payload["amount"])
                    restored_ids = {identity["entry_id"]
                                    for identity in queue_entry_ids[group][:restored_count]}
                    removed = queues[group][:restored_count]
                    # Rebase the remaining sequential spans; do not replace
                    # them with a guessed shared duration. Parallel timers
                    # are independent and retain their original deadlines.
                    rebase = (max(0, removed[-1] - time)
                              if removed and operation == "restore_rebase"
                              and data["groups"][group]["recovery"] == "sequential" else 0)
                    queues[group] = queues[group][restored_count:]
                    queue_entry_ids[group] = queue_entry_ids[group][restored_count:]
                    if rebase:
                        old_tail, new_tail = removed[-1], time
                        rebuilt = []
                        for ready, identity in zip(queues[group], queue_entry_ids[group]):
                            is_active = (active_entry and active_entry["group"] == group
                                         and active_entry["entry_id"] == identity["entry_id"])
                            floor = active_entry["start"] if is_active else 0
                            span = max(0, ready - max(old_tail, floor))
                            if is_active:
                                active_entry["prior_tail"] = new_tail
                            shifted = max(new_tail, floor) + span
                            rebuilt.append(shifted)
                            old_tail, new_tail = ready, shifted
                        queues[group] = rebuilt
                    assert_queue_identity(group)
                    if (active_entry and active_entry["group"] == group
                            and active_entry["entry_id"] in restored_ids):
                        active_action.pop("cooldown_entry", None)
                else:
                    delta = int(Decimal(str(payload["amount"])) * TICKS)
                    queues[group] = [max(time, ready - delta) for ready in queues[group]]
                    sort_queue(group)
                    restore(time)
                    if active_entry and active_entry["group"] == group:
                        # prior_tail is the historical sequential scheduling
                        # anchor. A reduction moves that timestamp with the
                        # queue; restore deliberately does not reschedule the
                        # remaining queue and therefore leaves it unchanged.
                        if active_entry["prior_tail"]:
                            active_entry["prior_tail"] = max(
                                time, active_entry["prior_tail"] - delta
                            )
                        if queue_index(group, active_entry["entry_id"]) is None:
                            active_action.pop("cooldown_entry", None)
                row["ready_at"] = [ready / TICKS for ready in queues[group]]
            if not failure and observer is not None:
                try:
                    derived_events = observer.on_event(ev, row, frontline, dict(energy))
                except Exception as exc:
                    # Known controlled pending must retain an already committed admission.
                    retain_pending(exc, row, ev, logged)
                    raise
                if hasattr(observer, "filter_pending"):
                    try:
                        expanded[cursor:] = observer.filter_pending(expanded, cursor)
                    except Exception as exc:
                        retain_pending(exc, row, ev, logged)
                        raise
                elif hasattr(observer, "keep_pending"):
                    expanded[cursor:] = [item for item in expanded[cursor:] if observer.keep_pending(item)]
                if derived_events:
                    expanded[cursor:cursor] = derived_events
                    expanded[cursor:] = sorted(expanded[cursor:], key=_event_sort_key)
                    if len(expanded) > 40000:
                        failure = ("action", "limit", "机制派生事件超过 40000 项。")
            if failure:
                fail_kind, code, message = failure
                cycle_failed = True
                row["status"] = "pending" if fail_kind == "pending" else "failed"
                if budget_issue:
                    result["issues"].append({**budget_issue, "event_id": ev["id"], "cycle": cycle, "t": time / TICKS})
                    termination = {"kind": "pending", "code": code, "parent_run_id": ev["run_id"],
                                   "cycle": cycle, "t": time / TICKS}
                else:
                    result["issues"].append(_issue(code, message, ev["id"], event_id=ev["id"],
                                                    cycle=cycle, t=time / TICKS))
            if not logged:
                result["event_log"].append(row)
        restore(boundary)
        current = _boundary(boundary, cycle + 1, energy, queues, frontline, busy_end,
                            cancel_at, spawned, expanded[cursor:], windows, period)
        sequence_boundary = {
            group: {
                "next_stage": state["next_stage"],
                "expires_in": state["expires_at"] - boundary,
            }
            for group, state in sorted(cast_sequences.items())
        }
        if has_cast_sequences:
            current["cast_sequences"] = copy.deepcopy(sequence_boundary)
        entry = active_action.get("cooldown_entry") if active_action else None
        entry_index = (queue_index(entry["group"], entry["entry_id"])
                       if entry else None)
        if entry is not None and entry_index is not None:
            group = entry["group"]
            assert_queue_identity(group)
            active_ready = queues[group][entry_index]
            active_serial = queue_entry_ids[group][entry_index]["serial"]
            # A cancellable active entry is the latest action-created entry
            # among equal ready values. Therefore its relative tie rank is
            # determined by the public numeric queue and need not enter the
            # periodic proof state as an absolute, non-converging ID.
            assert active_serial == max(
                identity["serial"] for ready, identity
                in zip(queues[group], queue_entry_ids[group]) if ready == active_ready
            )
        cooldown_anchor = ({
            "group": entry["group"],
            "ready_in": queues[entry["group"]][entry_index] - boundary,
            "end_in": entry["end"] - boundary,
            "prior_tail_in": max(0, entry["prior_tail"] - boundary),
        } if (entry is not None and entry_index is not None and busy_end > boundary) else None)
        if observer is not None and not fail_kind:
            try:
                current["mechanics"] = observer.boundary(boundary, cycle + 1, expanded[cursor:])
            except Exception as exc:
                if ledger is not None or capacity_ledger is not None or admission_ledger is not None:
                    # A later empty window can fail at its boundary. Keep the
                    # committed history without relabeling the last old action.
                    retain_pending(exc, None, None, True, boundary_time=boundary)
                raise
            # Preserve the released mechanics boundary schema. Plain rotation
            # keeps its historical public shape; both paths compare the same
            # private anchor below when proving convergence.
            current["mechanics_cooldown_anchor"] = copy.deepcopy(cooldown_anchor)
        proof_state = (current, cooldown_anchor, sequence_boundary)
        periodic_coverage = bool(periodic_action_ids) and periodic_executed == set(periodic_action_ids)
        result["cycles"].append({"cycle": cycle, "status": "failed" if cycle_failed else "not_verified" if fail_kind else "passed",
                                 "start_energy": cycle_start_energy, "end_energy": dict(energy),
                                 # Failed actions stop observer delivery. Its clock is
                                 # a real prefix, not the unexecuted window boundary.
                                 "boundary_state": None if fail_kind and observer is not None else current,
                                 **({"incomplete": True} if fail_kind else {}), **activity,
                                 "periodic_action_coverage": periodic_coverage and not fail_kind})
        cycle_start_energy = dict(energy)
        if cycle == 0:
            result["energy_check"]["opening_status"] = "failed" if fail_kind == "energy" else "not_verified" if fail_kind else "passed"
        result["energy_check"]["checked_cycles"] = cycle + 1
        if not fail_kind:
            result["energy_check"]["completed_cycles"] = cycle + 1
        if fail_kind:
            result["energy_check"]["failed_cycle"] = cycle
            result["status"] = "pending" if fail_kind == "pending" else "failed"
            result["energy_check"].update(status="failed" if fail_kind == "energy" else "not_verified", cycle_status="failed" if fail_kind == "energy" else "not_verified")
            result["action_check"]["status"] = "failed" if fail_kind == "action" else "not_verified"
            # 后续所有计划事件保留为未验证，不能靠跳过失败施放构造虚假循环。
            for ev in expanded[cursor:]:
                if ev["time"] < max_cycles * period:
                    tail_row = {"id": ev["id"], "run_id": ev["run_id"],
                        "cycle": ev["time"] // period, "source_cycle": ev["origin"],
                        "type": ev["kind"], "t": ev["time"] / TICKS, "order": ev["order"],
                        "status": "not_verified"}
                    auto_energy.mark_parent(tail_row, ev, abilities, "not_verified")
                    capacity_audit.mark_parent(tail_row, ev, abilities, "not_verified")
                    admission_audit.mark_parent(tail_row, ev, abilities, "not_verified")
                    result["event_log"].append(tail_row)
            break
        if (cycle >= 2 and (cycle * period) >= finite_until and proof_state == previous_boundary
                and periodic_coverage):
            result["status"] = "passed"
            result["energy_check"].update(status="passed", cycle_status="passed", proven_cycle=cycle,
                proof="显式周期动作已完整执行；相邻边界的能量、前台、冷却、占用、在途球/事件与临时 ER 状态相同，且有限事件影响已结束。仅证明所列周期动作与能量，不证明有限动作重复或持续 DPS。")
            if average_spawns:
                result["energy_check"]["proof"] += " " + average_scope()["message"]
            result["energy_check"]["proof_scope"]["kind"] = "periodic_action_energy"
            result["action_check"]["status"] = "passed"
            break
        previous_boundary = proof_state
    if result["status"] == "unproven":
        result["energy_check"].update(status="unproven", cycle_status="unproven")
        result["action_check"]["status"] = "passed"
    result["characters"] = [{"id": cid, "element": char["element"],
        "energy_recharge": char["energy_recharge"], "energy_capacity": char["energy_capacity"],
        "initial_energy": char["initial_energy"], "final_energy": energy[cid]} for cid, char in chars.items()]
    horizon = result["energy_check"]["checked_cycles"] * period
    result["gaps"] = _gaps(occupied, horizon, model.get("gap_explanations", []), period)
    if ledger is not None:
        if result["energy_check"]["proven_cycle"] is not None:
            termination = {"kind": "cycle_proof", "checked_cycles": result["energy_check"]["checked_cycles"]}
        ledger.finish(result, termination)
    if capacity_ledger is not None:
        if result["energy_check"]["proven_cycle"] is not None:
            termination = {"kind": "cycle_proof", "checked_cycles": result["energy_check"]["checked_cycles"]}
        capacity_ledger.finish(result, termination)
    if admission_ledger is not None:
        if result["energy_check"]["proven_cycle"] is not None:
            termination = {"kind": "cycle_proof", "checked_cycles": result["energy_check"]["checked_cycles"]}
        admission_ledger.finish(result, termination)
    return result


def _event_sort_key(event):
    return (event["time"], event.get("system_priority", 0), event["order"], event.get("causal", ()))


def _boundary(time, next_cycle, energy, queues, frontline, busy_end, cancel_at,
              spawned, pending, windows, period):
    # 只有已开始轮次留下的尾部事件进入队列；未来标准轮次的事件集合由固定输入给定。
    tails = [(ev["id"], ev["kind"], ev["time"] - time, ev["order"], ev["origin"] - next_cycle)
             for ev in pending if ev["origin"] < next_cycle and ev["kind"] not in ("elemental_state_deadline", "state_runtime_event")]
    er = [(w["id"], w["start"] - time, w["end"] - time, w["delta"], w["origin"] - next_cycle)
          for w in windows if w["origin"] < next_cycle and w["end"] > time]
    return {"energy": {cid: round(value, 8) for cid, value in energy.items()},
            "frontline": frontline,
            "cooldown_ready_in": {g: [x - time for x in q] for g, q in queues.items()},
            "occupation_remaining": max(0, busy_end - time),
            "switch_block_remaining": max(0, cancel_at - time),
             "in_flight": sorted((eid, origin - next_cycle, as_number(count))
                                 for (origin, eid), count in spawned.items() if count),
            "pending_events": sorted(tails), "temporary_er": sorted(er), "time_unit": "microseconds"}


def _gaps(occupied, horizon, explanations, period):
    merged = []
    for start, end in sorted(occupied):
        if start >= horizon:
            continue
        end = min(end, horizon)
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    gaps, cursor = [], 0
    for start, end in [*merged, [horizon, horizon]]:
        while start > cursor:
            origin = cursor // period
            segment_end = min(start, (origin + 1) * period)
            local_start = (cursor - origin * period) / TICKS
            local_end = (segment_end - origin * period) / TICKS
            reason = next((e["reason"] for e in explanations
                           if e.get("start") == local_start and e.get("end") == local_end), None)
            gaps.append({"start": cursor / TICKS, "end": segment_end / TICKS,
                         "duration": (segment_end - cursor) / TICKS, "cycle": origin,
                         "reason": reason, "status": "explained" if reason else "needs_explanation"})
            cursor = segment_end
        cursor = max(cursor, end)
    return gaps


__all__ = ["ROTATION_SCHEMA", "check_rotation", "rotation_capabilities", "validate_rotation_model"]
