"""公开的动作/能量检查契约；与伤害模板版本相互独立。"""

from __future__ import annotations

import copy

from genshin_engine.particle_quantities import PARTICLE_COUNT, PARTICLE_MODE_FIELDS
from genshin_engine.resource_source_identity import RESOURCE_IDENTITY_SCHEMA

ACTION_TYPES = {
    "action.elemental_skill": "元素战技", "action.elemental_burst": "元素爆发",
    "action.normal_attack": "普通攻击", "action.charged_attack": "重击",
    "action.plunging_attack": "下落攻击", "action.dash": "冲刺",
    "action.jump": "跳跃", "action.switch": "切换角色", "action.wait": "等待",
}
ELEMENTS = ["pyro", "hydro", "cryo", "electro", "anemo", "geo", "dendro"]
STATUSES = ["confirmed", "assumed", "pending"]


def obj(properties, required=()):
    return {"type": "object", "additionalProperties": False,
            "properties": properties, "required": list(required)}


def arr(items):
    return {"type": "array", "items": items}


NUMBER = {"type": ["number", "null"]}
INTEGER = {"type": ["integer", "null"]}
OPTIONAL_PARTICLE_COUNT = {**PARTICLE_COUNT, "type": ["number", "null"]}
STRING = {"type": "string", "minLength": 1}
EVIDENCE = {"status": {"enum": STATUSES}, "source": STRING}
REPEAT = obj({"mode": {"enum": ["each_cycle", "once", "cycles"]},
              "cycles": arr({"type": "integer", "minimum": 0})}, ["mode"])
RULE = obj({"id": STRING, "kind": {"enum": ["energy_spawn", "energy_direct",
                                            "cooldown_change"]}}, ["id", "kind"])
OUTCOME = obj({"rule_id": STRING,
               "status": {"enum": ["triggered", "not_triggered", "pending"]},
               "event_ids": arr(STRING), "reason": STRING}, ["rule_id", "status"])
COOLDOWN = obj({"group": STRING, "duration": NUMBER,
                "start": {"enum": ["action_start", "occupation_end"]},
                "charges": INTEGER,
                "recovery": {"enum": ["sequential", "parallel"]},
                "capacity_source": obj({
                    "contract": {"const": "action-capacity-source-v1"},
                    "binding_id": {"type": "string", "minLength": 1, "maxLength": 1024},
                    "source_fingerprint": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                }, ["contract", "binding_id", "source_fingerprint"])}, ["group"])
FINGERPRINT = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
UPSTREAM_COMMIT = {"type": "string", "pattern": "^[0-9a-f]{40}$"}
CAST_SEQUENCE_SOURCE_BINDING = obj({
    "contract": {"const": "cast-sequence-source-binding-v1"},
    "profile_id": STRING,
    "character_id": {"type": "integer", "minimum": 1, "maximum": 9007199254740991},
    "selected_skill_id": {"type": "integer", "minimum": 1, "maximum": 9007199254740991},
    "selected_stage": {"type": "integer", "minimum": 1, "maximum": 100},
    "version_line": {"enum": ["release", "beta"]},
    "snapshot_id": FINGERPRINT,
    "upstream_commit": UPSTREAM_COMMIT,
    "content_fingerprint": FINGERPRINT,
    "resource_fingerprint": FINGERPRINT,
    "sequence_cooldown_seconds": {"type": "number", "minimum": 0},
    "sequence_cooldown_charges": {"const": 1},
    "sequence_cooldown_recovery": {"const": "sequential"},
    # check_rotation has no database handle.  This explicit marker prevents a
    # saved receipt from being mistaken for a fresh source re-query.
    "verification_mode": {"const": "saved_metadata_only"},
}, ["contract", "profile_id", "character_id", "selected_skill_id", "selected_stage", "version_line",
    "snapshot_id", "upstream_commit", "content_fingerprint", "resource_fingerprint",
    "sequence_cooldown_seconds", "sequence_cooldown_charges",
    "sequence_cooldown_recovery", "verification_mode"])
CAST_SEQUENCE = obj({
    "group": STRING,
    "stage": {"type": "integer", "minimum": 1, "maximum": 100},
    "max_stages": {"type": "integer", "minimum": 2, "maximum": 100},
    "continuation_window": NUMBER,
    # The first supported policy matches skills whose base cooldown starts on
    # the first cast while later stages remain admissible through a separate
    # continuation window.  Keeping the policy explicit prevents a future
    # completion-anchored skill from being silently treated the same way.
    "cooldown_start": {"const": "sequence_start"},
    "source_binding": CAST_SEQUENCE_SOURCE_BINDING,
}, ["group", "stage", "max_stages", "continuation_window", "cooldown_start"])
ACTION_ADMISSION_SOURCE = obj({
    "contract": {"const": "static-action-admission-source-v1"},
    "binding_id": {"type": "string", "minLength": 1, "maxLength": 1024},
    "model_ref": obj({
        "id": {"type": "string", "minLength": 1, "maxLength": 1024},
        "revision": {"type": "integer", "minimum": 1, "maximum": 2147483647},
    }, ["id", "revision"]),
    "source_fingerprint": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    "static_certificate_fingerprint": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
}, ["contract", "binding_id", "model_ref", "source_fingerprint",
    "static_certificate_fingerprint"])
ABILITY = obj({"id": STRING, "character": STRING,
               "action_type_id": {"enum": list(ACTION_TYPES)}, "variant": STRING,
               "effective_level": {"type": "integer", "minimum": 1,
                                   "maximum": 20},
               "cooldown": {"oneOf": [COOLDOWN, {"type": "null"}]},
               "cast_sequence": CAST_SEQUENCE,
               "energy": {"oneOf": [obj({"required": NUMBER, "amount": NUMBER,
                                          "delivery": {"const": "action_start"}}),
                                      {"type": "null"}]},
               "action_admission_source": ACTION_ADMISSION_SOURCE,
               "rules": arr(RULE), **EVIDENCE}, ["id", "character", "action_type_id"])
ACTION = obj({"id": STRING, "ability_id": STRING, "start": NUMBER,
              "order": {"type": "integer"}, "occupation_end": NUMBER,
              "switch_allowed_at": NUMBER, "switch_to": STRING,
              "rule_outcomes": arr(OUTCOME), "repeat": REPEAT, **EVIDENCE},
             ["id", "ability_id", "order", "repeat"])
EVENT_COMMON = {"id": STRING, "type": STRING, "t": NUMBER,
                "order": {"type": "integer"}, "repeat": REPEAT,
                "action_id": STRING, "resource_identity": RESOURCE_IDENTITY_SCHEMA, **EVIDENCE}


def event(kind, fields, required=()):
    return obj({**EVENT_COMMON, "type": {"const": kind}, **fields},
               ["id", "type", "order", "repeat", *required])


EVENTS = [
    event("energy_spawn", {"particle_type": {"enum": ["particle", "orb"]},
                           "element": {"enum": [*ELEMENTS, "clear", None]},
                           "count": OPTIONAL_PARTICLE_COUNT, "lost_count": OPTIONAL_PARTICLE_COUNT,
                           **PARTICLE_MODE_FIELDS,
                           "source_kind": {"enum": ["skill", "weapon", "enemy", "other"]}},
          ["particle_type", "source_kind"]),
    event("energy_credit", {"credit_at": NUMBER, "spawn_id": STRING, "count": OPTIONAL_PARTICLE_COUNT},
          ["spawn_id"]),
    event("energy_direct", {"amounts": {"type": "object", "additionalProperties": NUMBER}}),
    event("energy_spend", {"character": STRING, "required": NUMBER, "amount": NUMBER},
          ["action_id", "character"]),
    event("cooldown_change", {"character": STRING, "group": STRING,
                              "operation": {"enum": ["reset", "reduce", "restore", "restore_rebase"]},
                              "amount": NUMBER}, ["character", "group", "operation"]),
]
# 到账事件只有 credit_at，避免 t 与 credit_at 出现两份互相冲突的时刻。
EVENTS[1]["properties"].pop("t")

from genshin_engine.damage_report_window import REPORT_SCHEMA

ROTATION_SCHEMA = obj({
    "schema_version": {"const": 1}, "period": NUMBER,
    "damage_report": REPORT_SCHEMA,
    'direct_energy_policy': {'const': 'energy-direct-base-er-v1'},
    "max_cycles": {"type": "integer", "minimum": 3, "maximum": 20, "default": 3,
        "description": "可选周期收敛审计的检查窗口预算，默认3、允许3..20；不是题目的游戏轮数。"
                       "once仅展开一次；两轮实际伤害需要显式输入两轮动作，不能用此参数代替。"},
    "initial_frontline": STRING,
    "characters": arr(obj({"id": STRING, "element": {"enum": [*ELEMENTS, None]},
                            "energy_recharge": NUMBER, "energy_capacity": NUMBER,
                            "initial_energy": NUMBER, **EVIDENCE}, ["id"])),
    "abilities": arr(ABILITY), "actions": arr(ACTION), "events": arr({"oneOf": EVENTS}),
    "initial_cooldowns": arr(obj({"character": STRING, "group": STRING,
                                   "ready_in": arr(NUMBER)}, ["character", "group"])),
    "energy_recharge_windows": arr(obj({"id": STRING, "character": STRING,
                                         "start": NUMBER, "end": NUMBER, "delta": NUMBER,
                                         "repeat": REPEAT, **EVIDENCE},
                                        ["id", "character", "repeat"])),
    "action_hit_links": arr(obj({"action_id": STRING, "hit_id": STRING},
                                 ["action_id", "hit_id"])),
    "gap_explanations": arr(obj({"start": NUMBER, "end": NUMBER, "reason": STRING},
                                 ["reason"])),
    "assumptions": arr(STRING),
}, ["schema_version", "characters", "abilities", "actions", "events"])
ROTATION_SCHEMA["$schema"] = "https://json-schema.org/draft/2020-12/schema"
ROTATION_SCHEMA["title"] = "动作、冷却与元素能量检查模型 v1"


def rotation_capabilities():
    """所有入口复用同一份可序列化能力说明。"""
    from .action_admission_audit import capabilities as action_admission_capabilities
    from .automatic_energy import capabilities as automatic_energy_capabilities
    from .damage_report_window import report_contract
    return {
        "rotation_schema_version": 1, "schema": copy.deepcopy(ROTATION_SCHEMA),
        "damage_reporting": report_contract(),
        "automatic_energy_delivery": automatic_energy_capabilities(),
        "action_admission_execution": action_admission_capabilities(),
        "action_types": [{"id": key, "name_zh": value} for key, value in ACTION_TYPES.items()],
        "event_types": [item["properties"]["type"]["const"] for item in EVENTS],
        "limits": {"time_precision_seconds": 0.000001, "min_cycles": 3,
                   "max_cycles": 20, "default_max_cycles": 3, "max_expanded_events": 40000,
                   "max_numeric_energy_input": 1_000_000_000, "energy_tolerance": 1e-8},
        "energy_rules": {
            "id": "kqm-energy-20260913", "source": "https://library.keqingmains.com/combat-mechanics/energy",
            "verified_at": "2026-09-13", "scope": "单人控制的 1–4 人队伍；显式确定性事件",
            "particle": {"same": 3, "different": 1, "clear": 2},
            "orb": {"same": 9, "different": 3, "clear": 6},
            "off_field": {"1": 1, "2": 0.8, "3": 0.7, "4": 0.6},
            "energy_recharge_applies_to": ["energy_credit", "energy_direct"],
            "direct_energy_contract": "energy-direct-base-er-v1",
            "frontline_sample_time": "credit_at",
            "particle_quantity_contract": {"id": "particle-quantity-v1", "fractional_counts": True,
                "count_mode": "分数数量按平均产球情景处理。均值恰为整数时可显式设置 count_mode=average。",
                "count_distribution": "可选作者声明的 count/probability 分支，核对概率和及显式 count 的加权均值；原文仍须查证。",
                "scope": "每次实际触发的平均数量，不表示逐粒触发次数、随机循环成功率或期望DPS。"},
        },
        "states": ["not_checked", "invalid", "pending", "failed", "unproven", "passed"],
        "ordering": "输入事件与动作按 (微秒时间, order) 唯一排序；到期冷却在同刻输入前恢复。",
        "repeat": "each_cycle 每轮；once 仅第 0 轮；cycles 指定从 0 开始的轮次。事件可延迟越过 period。",
        "field_semantics": {
            "characters": "必须与伤害模板队伍一致；ER 是倍率（2=200%）；初始能量不默认填满。",
            "max_cycles": ROTATION_SCHEMA["properties"]["max_cycles"]["description"],
            "initial_cooldowns.ready_in": "每个冷却组显式声明；数组是各次恢复距离起点的秒数，[] 表示全部就绪。",
            "actions": "开始位于 [0,period)；占用为半开区间；动作类别与伤害类别独立。",
            "switch_allowed_at": "仅表示允许切人取消后摇的绝对轮内时刻；省略时必须等到 occupation_end。",
            "switch_to": "切人技能所属角色须是切人前的前台；switch_to 指向切人后的角色。",
            "switches": "旧伤害模板 switches.t 必须等于对应切人动作 start，不是 occupation_end；switch_conflict 提供逐条 expected/actual。新机制路径从动作派生此投影。",
            "energy_spawn": "skill/weapon 需 action_id；count/lost_count 接受有限非负数。count_mode=average 标明平均产球，可附 count_distribution 核对概率与均值。丢失数量显式写 lost_count。",
            "energy_credit": "用 credit_at，不接受 t；同一来源轮的 spawn_id 可分批到账，数量守恒。",
            "energy_spend": "旧模式每次有能量需求的动作恰好一条；energy.delivery=action_start时禁止手工消耗事件，由动作接纳提交并记只读审计。required是门槛，amount是扣除。",
            "energy_direct.amounts": "角色 ID 到基础直接回能量的映射；按项目规则在实际到账前冻结接收者ER，乘一次后提交并记录基础量/ER/理论量/到账/溢出。无自由final绕过字段；旧结果按保存回执读取，不重算重签。",
            "energy_recharge_windows": "delta 为 ER 的加算倍率；半开区间可跨期，按到账时刻取值。",
            "cooldown_change": "reset 恢复本组全部次数；restore 恢复最早的 N 次且不改其他计时；restore_rebase 恢复最早的 N 次，并把顺序恢复队列余下计时平移到本次恢复时刻重新起算（并行恢复不改其他计时）；reduce 将所有当前恢复时刻前移 amount 秒。只认证此显式语义。",
            "cast_sequence": "同一角色的多段施放按 group/stage 串联。sequence_start 表示首段立即进入普通冷却；每个非末段必须声明正数 continuation_window，下一段在该窗口内仍可施放；末段关闭序列且 continuation_window 必须为 null。source_binding 保存受审来源身份与指纹，但 verification_mode=saved_metadata_only 明确表示动作检查不会重新查询数据库。首片续段状态默认跨普通动作与切人保留，只能用于已经确认具有该行为的技能。",
            "rules": "检查输入已经挂载的产球/直接回能/冷却改变与动作实例对应；不推测漏挂的游戏机制或内部触发CD。",
            "action_hit_links": "一个实际命中只能归属一次动作；跨条件 count 聚合不能认证具体时序。",
            "gap_explanations": "按单轮 [start,end] 给出空档目的，end 可以等于 period；不自动填充动作。",
        },
        "evidence": "status/source 缺失或 pending 时不认证；assumed 只在列明假设下检查。",
        "cycle_proof": "至少 3 个检查窗口；有限事件及其延迟影响结束后，完整边界状态与在途事件重复，且当前窗口实际执行了全部显式 each_cycle 动作才通过。once/cycles 动作执行结束后的空窗口不能证明循环；通过仅涵盖 proof_scope.periodic_action_ids，不认证持续 DPS。",
        "cycle_receipt": {
            "scheduled_action_count": "本窗口内已进入检查的动作数，包括失败及失败后未验证的动作。",
            "executed_action_count": "本窗口成功通过前台、占用、冷却及本路径施放门槛的动作数；不从命中推断动作。",
            "burst_action_count": "上述已执行动作中元素爆发的次数；零扣能爆发也计入。",
            "energy_spend_count": "本窗口内实际执行的正值扣能次数；失败或零扣能不计，原子施放扣能与随后回执只计一次。",
            "burst_energy_spend_count": "本窗口内归属元素爆发的实际正值扣能次数；旧路径延迟扣能按实际扣能窗口计入。",
            "periodic_action_coverage": "本窗口未失败且实际执行了全部非空 each_cycle 动作集合；这不是伤害覆盖率。",
            "proof_scope": "energy_check.proof_scope：none 未执行；checked_windows 仅检查有限窗口；periodic_action_energy 证明所列周期动作及能量。finite_action_ids 不在周期证明内，damage_verified 恒为 false。",
        },
        "unsupported": ["联机/死亡分配", "随机概率与小数期望球", "独立初始在途球状态", "初始已处于多段技能中段", "分支连携或切人取消连携", "体力", "元素附着与反应模拟",
                        "数据库关系缺失推断", "自动识别产球机制", "自动生成/刷新 Buff", "伤害稳态验证"],
        "old_template_behavior": "rotation_model 为 null 时返回 not_checked；不从 hits 推断动作。",
    }
