"""Explicit, versioned D18 inputs shared by native plans and the event runner."""
from __future__ import annotations

import copy
import hashlib
import json

from genshin_engine.rotation_schema import EVIDENCE, STRING, arr, obj

RULESET = "gauge-basic-v1"
SWIRL_RULESET = "gauge-swirl-v2"
DYNAMIC_SWIRL_RULESET = "gauge-swirl-dynamic-v3"
MULTI_AURA_RULESET = "gauge-multi-aura-v4"
FREEZE_RULESET = "gauge-freeze-reference-v1"
ORDINARY_SWIRL_RULESETS = (SWIRL_RULESET, DYNAMIC_SWIRL_RULESET, MULTI_AURA_RULESET)
SWIRL_RULESETS = (*ORDINARY_SWIRL_RULESETS, FREEZE_RULESET)
REFERENCE_CONTRACT = {
    "contract_id": FREEZE_RULESET + ":1", "evidence_tier": "experimental_reference",
    "assumptions": ["普通单目标、可冻结、零冻结抗性，无环境附着与hitlag。",
                    "公共初态无冻结量与冻结历史；连续ODE自然衰减及历史恢复。",
                    "新旧冻结量取max且不重置历史；风按水、冰、冻结逐层消费。",
                    "同父同元素聚合一次反应与伤害尝试；保留EC04按角色/目标/元素伤害序列，不引入模拟器0.1秒GCD。",
                    "目标状态条件收益、碎冰及其他削冻、范围外元素交互尚不支持。"],
    "sources": [
        {"id": "gcsim_freeze", "commit": "8792c73d64439feb74fa139c3b745022952b3dc9",
         "url": "https://raw.githubusercontent.com/genshinsim/gcsim/8792c73d64439feb74fa139c3b745022952b3dc9/pkg/reactable/freeze.go",
         "sha256": "bfc3ac3418ae2746b33113eb485e489687902e7926d9625c862d888fc69fcbc9"},
        {"id": "gcsim_state", "commit": "8792c73d64439feb74fa139c3b745022952b3dc9",
         "url": "https://raw.githubusercontent.com/genshinsim/gcsim/8792c73d64439feb74fa139c3b745022952b3dc9/pkg/reactable/reactable.go",
         "sha256": "22398e96eccd3c15c0606cf79a63f1dc7a82dbdb9c4317dd613111990d70d594"},
        {"id": "gcsim_swirl", "commit": "8792c73d64439feb74fa139c3b745022952b3dc9",
         "url": "https://raw.githubusercontent.com/genshinsim/gcsim/8792c73d64439feb74fa139c3b745022952b3dc9/pkg/reactable/swirl.go",
         "sha256": "f1cbf33c5512f1c9bf0da71910c93907f2561105661978c6bc12d683ca559f20"},
        {"id": "freeze_history_code", "commit": "8fccd1c1577dd75fde8610dfb0e1ed8ad0bf42b2",
         "url": "https://raw.githubusercontent.com/ellimiku/freeze_extension_analysis/8fccd1c1577dd75fde8610dfb0e1ed8ad0bf42b2/main.py",
         "sha256": "fdbc8371d9c843753656788c135de9fb06cabc0fc81c9872b90fb301bd57f36a"},
        {"id": "freeze_history_observations", "commit": None,
         "url": "https://tickets.deeznuts.moe/ticket-archive/attachments_881008110699937792_939917731388600362_transcript-freeze-extensions.html",
         "sha256": "4f66eaacb25899c2753fd1c5f7a9d2e435bd47209841fdc17272fa149e1d016e"},
    ],
    "rule_evidence": [
        {"rule": "history_growth_recovery", "level": "observed", "source_ids": ["freeze_history_observations", "freeze_history_code"],
         "limits": "2021原始记录，未重新客户端测量；不是微秒精度承诺。"},
        {"rule": "max_merge_and_layer_order", "level": "reference_implementation", "source_ids": ["gcsim_freeze", "gcsim_state", "gcsim_swirl"],
         "limits": "模拟器实现不是游戏源码，弱续冻与冰冻穿透缺完整独立实测。"},
        {"rule": "continuous_ode_tick_aggregation_sampling", "level": "engineering_convention", "source_ids": [],
         "limits": "同父聚合并保留EC04序列；不引入模拟器0.1秒GCD；不支持计数敏感/交叉写入包。"},
    ],
    "time": "连续分段积分，零点向上量化到微秒；到期先于同刻用户事件",
}
REFERENCE_CONTRACT["contract_fingerprint"] = hashlib.sha256(json.dumps(
    REFERENCE_CONTRACT, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
REFERENCE_ACCEPTANCE = obj({"contract_id": STRING, "contract_fingerprint": STRING,
                            "acknowledged": {"type": "boolean"}, "source": STRING},
                           ["contract_id", "contract_fingerprint", "acknowledged", "source"])
CONTACT = {"oneOf": [obj({"kind": {"const": "none"}, **EVIDENCE}, ["kind", "status", "source"]),
                      obj({"kind": {"const": "profile"}, "profile_id": STRING}, ["kind", "profile_id"])]}
CONTACT["oneOf"][0]["x-label"] = "直接声明不会削冻或碎冰"
CONTACT["oneOf"][0]["properties"]["kind"]["x-const-label"] = "不会削冻或碎冰"
CONTACT["oneOf"][1]["x-label"] = "引用接触档案"
CONTACT_PROFILE = obj({"id": STRING, "freeze_interaction": {"enum": ["none", "unknown", "may_reduce"]},
                       **EVIDENCE}, ["id", "freeze_interaction", "status", "source"])
CONTACT_PROFILE["properties"]["freeze_interaction"]["x-enum-labels"] = {
    "none": "不会削冻或碎冰", "unknown": "尚未核实", "may_reduce": "可能削冻或碎冰"}
ELEMENTS = ("pyro", "hydro", "cryo")
MULTI_AURA_ELEMENTS = (*ELEMENTS, "electro", "dendro")
MAX_ICD_SEQUENCE_LENGTH = 64
MAX_ICD_RESET_SECONDS = 10**9
SWIRL_DAMAGE = obj({
    "icd": obj({"kind": {"enum": ["sequence", "unknown"], "x-enum-labels": {"sequence": "有限伤害序列", "unknown": "未知"}},
                "reset_seconds": {"type": "number", "exclusiveMinimum": 0, "maximum": MAX_ICD_RESET_SECONDS, "x-label": "伤害计数重置秒数"},
                "sequence": {"type": "array", "items": {"type": "integer", "enum": [0, 1]},
                             "minItems": 1, "maxItems": MAX_ICD_SEQUENCE_LENGTH, "x-label": "有限伤害序列"}}, ["kind"]),
    **EVIDENCE,
}, ["icd", "status", "source"])
SWIRL_DAMAGE["x-label"] = "普通扩散伤害规则"
SWIRL_DAMAGE["properties"]["icd"]["x-label"] = "扩散伤害冷却"
APPLICATION = obj({"profile_id": STRING, "target_id": STRING,
                   "landed": {"type": "boolean"},
                   # Spatial and atomic-parent identities are generic queue
                   # metadata.  They let area state machines and one-parent
                   # AoE admission deduplicate without inferring anything
                   # from a character, action name or target ID.
                   "area_id": STRING,
                   "atomic_parent_id": STRING,
                   # A landed damage contact may intentionally skip its
                   # elemental-application attempt (for example, a periodic
                   # controller that applies on every second contact).  This
                   # is separate from ``landed`` and from ICD admission: a
                   # skipped attempt still deals damage and consumes no ICD
                   # position.  Omission preserves the historic ``True``
                   # behaviour for existing plans.
                   "attempted": {"type": "boolean"}},
                  ["profile_id", "target_id", "landed"])
PROFILE = obj({
    "id": STRING, "element": {"enum": [*ELEMENTS, "physical", "anemo", "electro", "geo", "dendro"]},
    "application": {"enum": ["apply", "none", "unknown"]},
    "gauge_units": {"type": "integer", "enum": [1, 2, 4]},
    "icd": obj({"kind": {"enum": ["none", "standard", "sequence", "timer_counter", "unknown"]},
                # ``group`` is the v1 compatibility spelling.  It used one
                # string both as the shared counter identity and as the name
                # of the sequence source.  New producers should provide the
                # two independent identities below instead.
                "group": STRING, "counter_tag": STRING, "rule_group": STRING,
                "reset_seconds": {"type": "number", "exclusiveMinimum": 0, "maximum": MAX_ICD_RESET_SECONDS},
                "hit_interval": {"type": "integer", "minimum": 1, "maximum": 64},
                "sequence": {"type": "array", "items": {"type": "integer", "enum": [0, 1]},
                             "minItems": 1, "maxItems": MAX_ICD_SEQUENCE_LENGTH}}, ["kind"]),
    **EVIDENCE,
}, ["id", "element", "application", "status", "source"])
INITIAL_AURA = obj({
    "element": {"enum": list(ELEMENTS)}, "remaining_gu": {"type": "number", "exclusiveMinimum": 0},
    "decay_gu_per_second": {"type": "number", "exclusiveMinimum": 0}, "source": STRING,
}, ["element", "remaining_gu", "decay_gu_per_second", "source"])
INITIAL_AURA["type"] = ["object", "null"]
INITIAL_AURA["x-null-label"] = "明确无元素底"
MULTI_INITIAL_AURA = copy.deepcopy(INITIAL_AURA)
MULTI_INITIAL_AURA["type"] = "object"
MULTI_INITIAL_AURA["properties"]["element"] = {"enum": list(MULTI_AURA_ELEMENTS)}
MULTI_INITIAL_AURA["properties"]["source_owner"] = STRING
INITIAL_AURAS = {"type": "array", "items": MULTI_INITIAL_AURA, "maxItems": len(MULTI_AURA_ELEMENTS)}
FRESH_AURA = copy.deepcopy(INITIAL_AURA)
FRESH_AURA["properties"]["element"] = {"enum": ["cryo", "hydro"]}
FRESH_STATE = obj({"ordinary_aura": FRESH_AURA, "frozen": {"type": "null", "x-null-label": "初始无冻结量"},
                   "freeze_history_seconds": {"type": "number", "const": 0}, **EVIDENCE},
                  ["ordinary_aura", "frozen", "freeze_history_seconds", "status", "source"])
ELEMENTAL_SCHEMA = obj({
    "schema_version": {"const": 1}, "ruleset": {"enum": [RULESET, SWIRL_RULESET, FREEZE_RULESET, DYNAMIC_SWIRL_RULESET, MULTI_AURA_RULESET]},
    "target": obj({"id": STRING, "kind": {"const": "ordinary"},
                   "knowledge": {"enum": ["known", "unknown"]}, "initial_aura": INITIAL_AURA,
                   "initial_auras": INITIAL_AURAS,
                   "initial_state": FRESH_STATE,
                   "freeze": obj({"freezable": {"type": "boolean"}, "resistance": {"type": "number"},
                                  "mode": {"enum": ["coexist", "mutual_cancel"]},
                                  "unfreezable_resolution": {"enum": ["coexist", "mutual_cancel"]}, **EVIDENCE},
                                 ["freezable", "resistance", "status", "source"]),
                   **EVIDENCE}, ["id", "kind", "knowledge", "status", "source"]),
    "profiles": arr(PROFILE),
    "swirl_damage": SWIRL_DAMAGE,
    "reference_acceptance": REFERENCE_ACCEPTANCE,
    "contact_profiles": arr(CONTACT_PROFILE),
}, ["schema_version", "ruleset", "target", "profiles"])
ELEMENTAL_SCHEMA["properties"]["target"]["properties"]["freeze"]["properties"]["mode"].update(
    deprecated=True,
    description="仅用于旧v4输入迁移；新输入使用unfreezable_resolution。")
ELEMENTAL_SCHEMA["properties"]["target"]["properties"]["freeze"]["properties"]["unfreezable_resolution"].update(
    description="仅freezable=false时适用；省略时采用coexist并记录default_scenario。")


def elemental_capabilities():
    return {
        "schema_version": 1, "ruleset": RULESET, "rulesets": [RULESET, SWIRL_RULESET, FREEZE_RULESET, DYNAMIC_SWIRL_RULESET, MULTI_AURA_RULESET], "schema": copy.deepcopy(ELEMENTAL_SCHEMA),
        "reference_contract": copy.deepcopy(REFERENCE_CONTRACT),
        "freeze_contract": {"ruleset": FREEZE_RULESET, "initial_state": "只接受frozen=null、freeze_history_seconds=0",
                            "contact": "descriptor.contact；无附着、ICD拒绝和首次造冻同击也检查接触",
                            "reactions": "reactions[]按固定语义ID；layer_consumptions保留逐层消费，非独立伤害",
                            "state": "ordinary_aura、frozen、freeze_history_seconds、freeze_decay_gu_per_second",
                            "unsupported": "目标状态条件收益、碎冰/削冻、非零冻结抗性、hitlag、多目标"},
        "mode": "optional mechanics_model.elemental_model; absent preserves declared reactions",
        "supported": ["普通单目标", "明确无附着/未命中", "标准/局部timer+hit共享 ICD、有限二元 ICD 序列与明确无 ICD",
                      "水火冰底量衰减与同元素刷新", "蒸发/融化方向、消耗与伤害联动",
                      "实际反应后的已声明收益/回能触发", "v2普通冰/水单底原目标扩散与独立伤害ICD",
                      "显式v3普通火/冰/水单底原目标扩散与常量普通精通变化取样",
                      "显式接受假设后的实验参考冻结：连续冰水风、续冻、自然解冻与双层扩散",
                      "v4固定多底优先、可冻结真实Frozen状态与不可冻结coexist/mutual_cancel场景",
                      "v4普通火/水/冰/雷扩散、普通结晶晶片/拾取/全队盾与普通绽放核生命周期",
                      "v4与显式星月替换状态机的原子桥接、月感电按owner残量及五秒窗口核验"],
        "unsupported": ["旧v1/v2冻结；参考模式范围外共存底和传播；火扩散仅v3", "多目标空间实体、其他护盾与固有元素", "自动推断未知附着", "非二元 ICD 序列的衰减语义",
                        "普通超载/感电/超导/燃烧/超绽放/烈绽放/激化系/碎冰状态机与Frozen+火/岩未审交互",
                        "原始 elementDurability 到 GU 的未核实转换", "预热后的初始 ICD 状态"],
        "icd_contract": {
            "shared_key": ["actor", "counter_tag", "target_id"],
            "identity": "counter_tag只决定共享计数身份；rule_group只标识规范化执行规则的来源。不得按伤害类型名称猜测二者。",
            "legacy": "旧输入只含group时，group同时投影为counter_tag和rule_group；新输入必须同时给出counter_tag与rule_group且不再填写group。",
            "state_compatibility": "state.icd[].group仅是counter_tag的弃用兼容别名；规则来源只读rule_group。",
            "standard": {"kind": "timer_counter", "reset_seconds": 2.5,
                         "hit_interval": 3, "first_hit": 1},
            "timer_counter": {"required": ["counter_tag", "rule_group", "reset_seconds",
                                               "hit_interval"],
                              "first_hit": 1,
                              "semantics": "计时到期或命中计数满足任一条件即允许；计数触发不移动计时锚点。"},
            "sequence": {"required": ["counter_tag", "rule_group", "reset_seconds", "sequence"],
                         "max_length": MAX_ICD_SEQUENCE_LENGTH, "values": [0, 1],
                         "reset_seconds": "有限正数，精确到微秒且最多10亿秒；上游浮点量化由资料投影负责",
                         "max_reset_seconds": MAX_ICD_RESET_SECONDS},
            "semantics": "sequence首次或计时到期后的首次命中取首项，有限序列耗尽后拒绝到计时重置；timer_counter首次/计时到期命中直接允许，此后第1+N*hit_interval次允许。两者计数触发均不移动时间锚点，未命中或显式未尝试不计数。",
            "consistency": "在同一actor + counter_tag + target_id运行时状态中必须绑定相同的规范化执行签名；来源rule_group可以不同并全部保留供审计。不同角色的同名counter_tag互不共享。同一rule_group名称只标识规则来源，不单独证明状态共享；有限sequence不得因图样重复而推断为无限timer_counter。",
            "evidence": "序列只决定附着资格，不解释伤害、削韧或GU衰减；真实数据库参数不自动证明具体攻击已正确绑定。",
            "trace": "每次尝试分别报告application_allowed与reaction_occurred，并公开profile_status/source、counter_tag、rule_group、计数位置、计时锚点及共享键。",
            "verification": "必须按具体攻击组件核实附着、counter_tag、rule_group、GU与段数；角色间及同角色不同攻击段都可能不同。引擎不按action_type、伤害类型或角色名推断。无法核实时声明unknown并停止确定性整轴。",
        },
        "application_gate_contract": {
            "field": "application.attempted",
            "default": True,
            "semantics": "landed=true且attempted=false时伤害接触仍发生，但本次不尝试附着、不改变元素底且不消费ICD计数；它不能代替miss。",
            "trace": "elemental_trace分别报告application_attempted、application_allowed与reason。",
            "source": "该门控必须由已确认的具体攻击分量周期/序列规则派生，不能按角色、动作类别或伤害名称猜测。",
        },
        "profile_evidence": "confirmed/assumed 只声明证据状态；不会把原始字段、动作类别、伤害类别或自然语言自动转为已验证游戏规则。每个实际攻击组件须单独绑定并保留来源。",
        "swirl_contract": {"ruleset": SWIRL_RULESET, "required": "swirl_damage；v1禁止填写",
                           "shared_key": ["actor", "target_id", "swirl", "reaction_element"],
                           "icd": "全局sequence或unknown；不接受自选group；独立于攻击附着计数",
                           "em": "提供者须明确有限固定EM；可能写入EM的未核实路径返回pending",
                           "damage": "reaction-post后同步生成独立transformative/swirl伤害；不再次附着，不匹配普通hit监听",
                           "ids": ["parent:reaction:0", "parent:reaction_damage:0"],
                           "trace": "elemental_trace含reaction_damage_attempt，state含reaction_damage_icd；反应成立不等于伤害允许"},
        "dynamic_swirl_contract": {
            "ruleset": DYNAMIC_SWIRL_RULESET, "inherits": "v2普通单底因果顺序、伤害ICD和原目标不再次附着；不改变旧v2或冻结参考",
            "elements": ["pyro", "hydro", "cryo"], "required": "swirl_damage；原始来源需独立覆盖三个元素",
            "em": "明确基础总EM，加当前已生效的有限常量普通贡献；不是NONEXTRA别名",
            "allowed_writes": "manual全伤害范围常量EM；无filter/quota/match的规则常量EM；保留目标、前台、到期和层数语义",
            "unsupported_writes": "属性表达式写EM、命中筛选、限次、微调关联及不明确的贡献路径；潜在路径也返回pending",
            "provider_sample": "reaction_created；每次读取，不缓存动态值；有限非负有效EM",
            "trigger_sample": "既有sample=trigger在监听触发时采样，可能晚于reaction_created；不据此自动认证真实来源",
            "audit": "派生hit的extensions.elemental.provider_stat_sample含base_value/value/dependencies/t/phase/basis",
            "source_scope": "只支持明确声明普通单目标场景；不放宽已有固有/装备来源资格或完整游戏机制证明",
        },
        "multi_aura_contract": {
            "ruleset": MULTI_AURA_RULESET,
            "initial_state": "target.initial_auras为显式有限层；不得与initial_aura/initial_state混用",
            "freeze_target": "target.freeze.freezable是目标能否冻结的独立事实；不得由水冰共存处理反推",
            "unfreezable_resolution": "仅freezable=false时生效；省略默认coexist（default_scenario），Agent显式选择记为manual_override。旧mode仅作legacy_mode_migration",
            "freezable_boundary": "freezable=true的运行时水冰接触进入reviewed FreezeState；无法确定先后顺序的水冰共存初态仍pending，不会把共存冒充冻结",
            "freezable_state": "状态公开frozen、freeze_history_seconds、freeze_decay_gu_per_second；首次冻结、续冻max刷新、底层较强余量、自然衰减和history恢复均由同一子状态机负责",
            "freezable_anemo": "固定先消费底层ordinary Hydro/Cryo，再消费Frozen层；候选逐层报告rank/status/skip_reason与trigger GU余量，同元素层可聚合为一次扩散反应",
            "deadline": "ElementalRuntime.next_state_deadline统一向事件队列提供自然到期点；旧deadline在刷新/消费后按revision失效",
            "selection": "外来元素使用版本化固定反应优先路由，不按剩余GU大小选底；未受审多底组合pending",
            "coexist_routes": {"anemo": ["hydro", "cryo"], "geo": ["hydro", "cryo"],
                               "pyro": ["hydro", "cryo"], "electro": ["hydro", "cryo"],
                               "geo_electrocharged_future": ["electro", "hydro"]},
            "elements": list(MULTI_AURA_ELEMENTS),
            "swirl": "火/水/冰/雷原目标单层扩散；伤害ICD与附着ICD独立",
            "crystallize": "岩接触火/水/冰/雷时执行目标级共享1秒反应门控；成功造片时取样触发者等级/精通。CrystallizeWorldState共享战场晶片上限、到期和唯一全队普通结晶盾；拾取必须走显式状态操作。",
            "ordinary_bloom": "水草反应生成普通草原核；支持6秒到期、第6核顶替最早核与同owner+target+reaction的0.5秒两段伤害限制。丰穰核、超绽放、烈绽放及真正多目标AoE仍pending。",
            "special_bridge": "星月替换在普通提案后、普通副作用提交前路由；只提交普通或替换之一，pending整步回滚。各反应可使用独立状态机，但共享proposal/replacement/atomic commit/rollback/trace合同。",
            "lunar_electro": "雷云刷新延长寿命但保留逐目标跳数相位；贡献者为扣除前同元素owner正残量与最近5秒施加的交集，每个水/雷owner记录分别扣0.4U。正残量超过5秒的证据冲突返回pending。",
            "world_boundary": "当前主运行时仍以单目标为执行单位；普通草原核范围伤害、雷云区域实体搜索、星扩散packet目标聚合和月笼空间/弹道需要世界实体协调器。",
            "unsupported": "Frozen+Pyro/Geo等本轮未接纳组合原子pending；普通感电/超导/超载/燃烧/激化/碎冰、丰穰核、超绽放、烈绽放返回pending；多目标传播未实现",
        },
        "ordering": "命中前置收益后判定附着，本次增幅交付伤害；命中后置之后派发反应事件。反应收益不追溯修改父命中。",
        "reaction_trigger": "event=elemental_reaction；v2增加swirl、reaction_elements、owner_presence，以及reaction_damage专用post事件。",
        "safety": "参考模式额外要求明确假设确认和冻结相关接触证据；启用模拟时每个命中必须有 application 引用，不得同时填写 reaction/count 聚合；未知或未支持分支停止给出整轴确定伤害。",
    }
