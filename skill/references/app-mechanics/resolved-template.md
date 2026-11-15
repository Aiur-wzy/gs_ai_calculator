# 引擎 resolved 模板契约

维护旧 resolved v1/v2 模板或做单次数值估算时读本文。`engine calc` 使用调用者给定的完整数值。需要验证动作、能量、附着或动态领域时，直接用原生方案和[当前机制接口](execution/rules.md)。各入口的[能力路径](../runtime/software-entrypoints.md#三种入口的资源根)不同，公式见[伤害](../mechanics/damage-formula.md)。

本文只描述resolved core入口。数据库数值由App查询／Resolver／Composer在进入该入口前解析。引擎使用已解析输入，不在计算过程中查询live SQLite。

Agent材料化和统一覆盖不是让模型直接编辑本页resolved结果的捷径。有限序列、周期、附着、资源、Buff／状态等派生机制先进入原生方案的版本化强类型输入，由Composer／runtime依据当前`family + handler_version`能力安装，再投影为resolved计算事件；统一override也必须先绑定稳定拓扑和自动基线。Agent不能直接修改总伤、DPS或显示结果。自动来源、Agent材料化、override和外部补充对同一coverage只能有一个有效所有者；最终task-capability只能由App依据完整受信回执和fresh有效图消解。当前Buff／state的v2强类型处理器可执行，旧v1只兼容保存、没有执行权；任何族若当前版本未声明可执行，必须保持partial／unsupported，不能因文件能保存就写入resolved结果。

本文描述仍受支持的resolved v1/v2数值接口。新排轴可使用原生plan_version=3的 [机制声明](execution/rules.md)，由动作、规则、来源和给定面板经equipment calc生成数值投影；原生版本3不等于resolved version=3。不要把mechanics_model放进旧resolved模板，或将其传给只计算旧数值模板的engine calc。

本文 v1 示例继续可用。v2 使用 `version=2`、`id_registry_version=1` 和 `type_definitions.damage_types`。每个 hit 填稳定 `id` 和已登记 `damage_type_ids`，每个 effect 填 `id`、`benefit_type_id` 及 `applies_to`。App 原生工具及连接 App 的 `agent candidate read` 读 `capabilities` 资源的 `/engine_v2`；独立 `capabilities` 命令的完整响应读取 `data.plan_contract.engine_v2`。原生配装方案用 App 计算或独立 `equipment compose/calc`。原生轴微调保存在方案中，resolved 保留编译后的范围，见[装备参考](loadouts.md)。

伤害分类优先使用运行时标准 ID，例如 `damage.elemental_skill`、`damage.elemental_burst`、`damage.normal_attack`；元素另写 `element`。确需额外分类时读取注册表和 schema，在 `type_definitions.damage_types` 中声明 `custom.damage.<名称>`，再一致引用；`damage.<自造名称>` 不合法，自定义标签也不会添加新伤害算法。

v3动态收益、窗口、有限次数和展开后的命中由引擎派生，不手改resolved里的结果。排轴任务修改动作或规则含义时，按[工作重点](../workflow/index.md)修订受影响的动作／机制输入及后继依赖，再转换计算，不要求focus回退；单纯字段／引用表达错误直接修正并重新校验。机制采样以self/provider/enemy域和self_snapshot_event为准；下面旧snapshot字段的整组采样行为继续保留，仅适用于旧路径。人工Buff与自动来源不能双算，特殊match仍是原生方案中的本轴微调。

## 1. 可运行的最小示例

保存为UTF-8 JSON，调用`engine calc`。示例数字是演示参数，不是角色推荐配装。

```json
{
  "version": 1,
  "form": "resolved",
  "meta": {
    "name": "固定与百分比加攻演示",
    "version_line": "release",
    "assumptions": ["示例面板未计入下列Buff；基础攻击由调用者给定"]
  },
  "enemy": {"level": 90, "resistance": {"default": 0.1}},
  "characters": [{
    "id": "actor",
    "name": "示例角色",
    "description": "用于演示固定与百分比攻击力加成",
    "level": 90,
    "stats": {"atk": 2000, "base_atk": 900, "em": 0,
              "crit_rate": 0, "crit_dmg": 0.5, "dmg_bonus": {}}
  }],
  "switches": [{"t": 0, "character": "actor"}],
  "buffs": [{
    "id": "panel_buff", "name": "攻击力加成", "source": "示例增益", "scope": "team",
    "description": "五秒内获得固定攻击力与按基础攻击力计算的比例加成",
    "start": 0, "end": 5,
    "effects": [
      {"name": "固定攻击力", "zone": "stat_add", "stat": "atk", "value": 800},
      {"name": "基础攻击力比例加成", "zone": "stat_pct", "stat": "atk", "value": 0.2}
    ]
  }],
  "hits": [{
    "t": 1, "character": "actor", "skill": "skill", "hit_name": "示例技能首段",
    "description": "一次按攻击力结算的物理伤害",
    "damage_system": "classic", "element": "physical",
    "scaling_stat": "atk", "multiplier": 2
  }],
  "loop_duration": 20
}
```

该例有效攻击为`2000+900*0.2+800=2980`；无暴击期望伤害为`2980*2*0.5*0.9=2682`。同一加成如果已包含在输入面板里，不得再加入Buff。

## 2. 字段与默认值

| 对象 | 字段 |
|---|---|
| 根对象 | `version:1`、`form:"resolved"`、`enemy`、非空`characters`和`hits`必填；`meta`、`switches`、`buffs`、`loop_duration`可选 |
| meta | `name`方案名，`version_line`数据标签；`assumptions`为字符串数组，结果中保留这些假设。当前上游含beta内容，标签不能代替核实来源版本 |
| enemy | `level`正整数；`resistance`为元素→抗性数值字典，默认`{"default":0.1}`；可填`defense_reduction`、`defense_ignore`，缺省0 |
| character | 唯一且非空的`id`、正整数`level`、`stats`字典；`name`中文展示名、`description`中文说明可选 |
| stats | `atk/hp/def/em`是结算面板；`base_atk/base_hp/base_def`用于比例属性Buff；`crit_rate`缺省0、`crit_dmg`缺省0，`dmg_bonus`为元素或`all`→增伤数值字典 |
| switch | `{t,character}`；默认前台为characters[0]；同一时刻的多条切人按输入顺序最后一条生效 |
| buff | 唯一非空`id`、`start/end`、非空`effects`；`name`、`source`、`description`为可选中文展示文本；`scope`缺省team，可选frontline；`target`可选，限定角色ID |
| effect | `{zone,stat,value}`；含义见下表，数字必须有限；`name`、`description`为可选中文展示文本 |
| hit | `t`、`character`、非空`skill`、`damage_system`、`element`必填；`hit_name`中文事件名称、`description`中文说明可选；`count`正整数缺省1；其他字段见下文 |

展示字段在 v1、v2 和原生方案内通用，只接受字符串（可省略或为空，不能填 null/对象），不参与伤害公式或事件匹配；新交付应补齐中文名称，说明按需要填写。已有英文机器 ID、skill 分组键和引用保持稳定，用展示字段解释它们。旧自由文本不做猜测性翻译，缺少中文资料时明确标记缺口。注册表 `label` 是中文显示名，`id/zone/stat/unit/value_semantics` 仍是机器契约。

面板的攻击/生命/防御/精通不得为负。暴击率采用0–3的宽松输入范围（0.5表示50%），计算时上限截为1。JSON布尔必须是真正的`true/false`，不能写成字符串；数值不能写`NaN`、`Infinity`或百分号字符串。可选列表省略时为空，但不要写null。

元素常用值：`physical/pyro/hydro/electro/cryo/anemo/geo/dendro`。基础属性与目标ID都由调用者给出；缺失计算必需字段会报错，不使用模型记忆补齐。

## 3. 面板和乘区Buff

| zone | stat | value含义/合并方式 |
|---|---|---|
| stat_add | atk/hp/def | 固定面板增量，加算；如加攻击800填800 |
| stat_pct | atk/hp/def | 基础属性的比例增量，加算；20%填0.2，需相应base_atk/base_hp/base_def |
| em | all | 精通平值增量，加算；同时影响精通结算基础伤害和反应精通区 |
| base_add | all | 倍率后的基础伤害加算（羽毛区），不是固定面板加攻 |
| base_mult | all | 基础伤害乘算，多个效果相乘；1.2表示乘1.2，不是攻击提高20% |
| dmg_bonus | 元素或all | 对应元素增伤，加算；20%填0.2 |
| crit_rate / crit_dmg | all | 暴击率/暴击伤害增量，加算 |
| reaction_bonus | 反应type或all | 反应加成，加算 |
| reaction_flat | all | 反应基础伤害的固定增量，加算 |
| lunar_base / stellar_base | all | 对应体系的基础区比例增量，加算后取1+总量，20%填0.2 |
| lunar_mult / stellar_mult | all | 对应体系的独立乘算，多个效果相乘 |
| lunar_elevate / stellar_elevate | all | 对应体系的擢升增量，加算 |
| def_reduction / def_ignore | all | 敌方减防/无视防御增量，各自加算并分别以90%/100%封顶 |
| res_shred | 元素或all | 敌方抗性减少量，加算，可使抗性降至负值 |

Buff有效区间为`start <= t < end`。team对全队生效；frontline仅对该时刻前台角色生效；target与scope叠加判断。敌方减益不受scope/target限制。

面板计算为 `有效属性 = 已输入面板 + 基础属性*sum(stat_pct) + sum(stat_add)`。与当前 hit 结算属性匹配且生效比例合计非零时，填对应基础属性。旧 resolved 数值接口只收已算出的跨角色转化平值。原生任务使用[动态取样及属性接口](execution/attributes-and-sampling.md)表达支持的转换，按当前属性来源核验。

旧模板中的base_add/base_mult仍按原含义执行，不会自动变成面板Buff。若旧模板用它们表达加攻，必须依据实际机制显式改成stat_add/stat_pct，并补基础属性。

## 4. hit与四伤害体系

`scaling_stat`可选atk/hp/def/em，缺省atk；需要倍率结算的hit应在stats中提供对应属性。`multiplier`为非负数，2.02表示202%。

| damage_system | 用法 |
|---|---|
| classic | 必须有multiplier。无reaction为普通直伤；增幅反应`{"type":"amplify","factor":1.5}`或factor=2；激化type为aggravate/spread |
| transformative | 必须有reaction；按等级和精通计算额外反应伤害，不吃角色攻击/传统增伤/暴击；无需multiplier |
| lunar / stellar | 必须有reaction；普通直伤需multiplier；反应本体用`reaction_base:true`，按等级基数计算，无需multiplier |

内置reaction.type：

- transformative：burning、swirl、superconduct、electro_charged、bloom、overload、shattered、burgeon、hyperbloom。
- lunar：lunar_bloom、lunar_electro、lunar_crystallize。
- stellar：stellar_swirl、stellar_swirl_anemo、stellar_swirl_cryo_lv2、stellar_swirl_cryo_lv3、stellar_superconduct。旧 resolved 数值估算可给星超导填非负整数 `layers`，由调用者负责该数值。任务要求动态领域时，用原生[能力直伤事件](elements/special-reactions.md#直接能力伤害)和实际领域结算。

剧变/月曜/星烁可显式提供正且有限的`reaction.factor`覆盖内置系数，需声明依据；覆盖系数也不等于完整实现新的游戏反应。使用等级反应基数的事件（剧变、激化、月曜/星烁反应本体）当前只支持70/80/90/95/100级；其他等级直接拒绝，不能擅自插值。

`snapshot:true`使该hit使用`cast_t`（缺省hit.t）时刻的Buff集合，cast_t不能晚于hit.t。当前锁的是整组Buff，包括敌方减益。仅拆分后续 hit 再全部标快照不会使敌方减抗动态到期；需要更细取样而运行时不能表达时，明确记录近似或未支持，不能称已经校验。

## 5. 轴、假设和结构化补丁

- 所有事件时间非负，Buff end>start；loop_duration若给出必须为正。
- `count`只是相同条件命中的合并，不会替你展开跨时刻Buff变化、ICD或有限次数收益消耗。不同命中条件应拆成多个hit；不能把有限次平值加算直接加给一整组总倍率。
- 总伤是全部hit之和；DPS除loop_duration。调用者应确保hit落在合理循环范围内，当前不是动作合法性模拟器。
- 覆盖率输出是Buff在循环内的时长占比，不是伤害贡献比例。
- 假设放`meta.assumptions`，例如反应命中次数、简化的Buff窗口和面板数据来源；输出会保留，便于复核。

补丁文件是操作数组，支持add/remove/replace；列表追加用`-`，索引从0开始；包含`/`或`~`的键按JSON Pointer使用`~1`/`~0`。补丁失败整批拒绝，不改原模板。

```json
[
  {"op": "replace", "path": "/characters/0/stats/atk", "value": 2200},
  {"op": "add", "path": "/meta/assumptions/-", "value": "攻击面板调整为2200"}
]
```

不要把一般模板错误当成缺数据库：resolved计算不依赖数据库连接。按返回的字段路径或补丁操作序号修正，再计算。
