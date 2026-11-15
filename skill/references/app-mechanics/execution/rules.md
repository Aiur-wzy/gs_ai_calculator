# 声明式规则与来源绑定

原生App从`capability_index`按返回的resource/pointer读合同，不能给pointer套CLI的`data.plan_contract`包络；外部CLI按实际capabilities命令返回读。本文业务片段不等于工具参数，是否需要request等外层以本次schema为准。

需要自动执行增益触发、刷新、层数、次数、动态取值或确定性能量事件时读本文。原生方案按 `mechanics_contract.native_authoring.schema` 填写 `mechanics_model`，完整方案结构查 `plan_contract.schema`；原生工具的 `mechanics_capabilities` 资源对应 `/native_authoring/schema`。`mechanics_contract.schema` 标记为 `engine_execution`，保留给编译后的执行模型，不表示其中每个字段都能原样写回原生方案。排序和支持范围仍查同一合同。

原生执行预算写在顶层 `calculation_options`，不能写入 `mechanics_model.execution_options`。属性输入采用作者形态；通用schema的绝对上限不代表本次有效预算，具体见[属性与取样](attributes-and-sampling.md)。

排轴任务可按当前需要把已盘点事件转换到以下字段，不要求处于`numeric_model`focus。`event-topology-v1`是非执行草稿，不能把缺数值的半成品直接塞进`mechanics_model`来绕过schema。触发、范围、时序和资源安排应有稳定ID及拓扑关系；本文用于补数值、来源和可执行绑定。发现漏事件直接补拓扑，大改动作更新动作／映射及后继（不机械回退focus），不能在机制字段中暗改动作。

## 一个原生输入，两类声明

`kind=genshin-plan`、`plan_version=3` 显式包含 `rotation_model` 和 `mechanics_model`；null 可保存为待补草稿。`manual_template` 继续保存给定面板、敌人和有稳定 ID/分类的伤害原型，`equipment_inputs` 保存武器/套装。`rotation_model` 管动作、CD、能量初态与外部事件；`mechanics_model` 管明确声明的触发与状态。旧 v1/v2 不接受夹带机制模型。

当前能力提供elemental时，可在mechanics_model选择性开启 `elemental_model`，给命中添加附着档案引用，反应由引擎判断；实际支持元素、扩散／共存模式、反应选择器与回执读[元素运行时](../elements/runtime.md)和当前规则集，不把早期“仅水冰火”示例当全部能力。它仍不证明角色全部命中自动还原。提供 `action_event_contract` 时，可按[共用动作档案](../actions/event-profiles.md)自动实例化已声明有限动作，其余来源核对实际输入。

新建从运行时 `examples.panel_plan` 开始，或读取用户现有文件，再显式升级。需要看完整连接关系时用[原生小例](../native-causal-example.md)，局部用途按[意图导航](../authoring-guide.md)查当前作者schema，不用凭字段名猜：

```text
equipment enable-mechanics --plan panel-plan.json --output mechanics-plan.json
equipment validate --plan mechanics-plan.json
```

升级保留已有输入，不自动产出游戏规则。保留方案 plan_id，编辑输入时更新 revision；不要另建没有装备来源的“最终数值轴”作为交付权威。

### 来源部件必须接到规则

先 compose 获取来源与摘要，按 [装备参考](../loadouts.md) 审核 `effect_bindings`。每条机制规则由唯一部件交付：

```json
{
  "part_id": "conditional-bonus",
  "label": "条件增伤",
  "state": "resolved",
  "rule": {"kind": "mechanics", "rule_id": "bonus-rule"},
  "delivery": {"kind": "mechanics"},
  "conditions": [],
  "dependencies": {"mode": "source_only", "context_fingerprint": null,
                   "note": "具体触发与数值依赖在机制声明中执行"},
  "reason": null
}
```

若上层 `binding_id="source-bonus"`，对应机制规则必须写 `source_id="source-bonus:conditional-bonus"`；`owner` 必须等于该 binding 的 `provider_character_id`。来源 `coverage` 和 source_fingerprint 仍需核对，不能只添加一条裸 rule 就算接通。多项同源收益放在该规则的 `effects` 中，避免重复交付。资料不能确认时，部件保留 pending。

上例仅演示部件结构，不代表某角色事实或完整可运行方案。完整 binding 按 compose 的 `proposed_binding` 和原文补全。机制部件的条件只用已审核的 always；时间区间、命中条件等写入机制触发/过滤或 condition_events，避免部件区间被忽略。人工预折算的常数仍按其实际依赖审核，不套 source_only 绕过变化。

v3 保留手工 Buff 时，为 `manual_template.buffs[].extensions` 填写 `owner`（提供者角色 ID）和 `source_id`（来源分项 ID，或明确独立的手工来源）。缺失会保留 pending，不能换个 Buff 名称掩盖同源重复交付。组合器生成的 Buff 自动携带提供者与 binding:part 身份；旧 v1/v2 不增加这一要求。

若手工区间只是本轴的计算采样，而非游戏效果的实际寿命，当前 `mechanics_contract.buff_state_timeline.presentation` 提供只读约定时，可在该 Buff 的 `extensions.presentation` 写 `{contract:"buff-presentation-v1",purpose:"calculation_sampling",reason:"本次采样的具体依据"}`。它只改变展示分类：默认不占持续 Buff 轴，高级筛选可查看，逐击贡献、完整状态、保存和导出均保留；不改变伤害、匹配或状态，也不自动判断冰底等条件。真实持续效果和采样窗口应拆为不同 Buff，不按短时长或 ID 猜用途，不提供通用隐藏开关。未标记或无效标记照常显示；改标记后重新计算，不改已封口结果。


## 交给引擎的机械工作

| 声明 | 实际字段与范围 |
|---|---|
| 触发点 | `trigger.event`：action_start/action_end/switch/hit/condition；`phase`：pre/post。元素事件另有 `elemental_reaction`（仅 post）和 `reaction_damage`，实际支持的反应及阶段查本次合同；可按动作、事件、角色、动作类型、伤害类型、元素、显式 condition 进一步过滤 |
| 受益者 | `beneficiaries.mode`：self/team/characters/frontline/next_switch；scope 明确 locked 或 current_frontline。接棒受益者由实际切人确定 |
| 持续与CD | timing 的 delay/duration/cooldown/cooldown_anchor；anchor 为 success 或 attempt |
| 层数与刷新 | stacking 的 max/add/policy，枚举以本次schema为准。refresh保持已有层数、仅整体刷新；add_and_refresh按add加层至max并整体刷新；independent各层独立到期。不能仅因max/add已填就认定实际叠层，须核对执行账中的层数与刷新。 |
| 有限次数 | quota 的 capacity/scope/refresh/consume；scope 为 per_beneficiary 或 team，refresh 为 reset/add/keep。当前消费限定 hit/pre，按单目标逐次命中扣除 |
| 收益 | effects 每项 id/benefit_type_id/value/domain，可另带 filter；分类从运行时注册表取得，不按中文名猜乘区 |
| 初态 | initial_state.rules 为每条规则恰好声明一次 cooldown_remaining 和 instances；空 instances 表示未激活，未知不能写成已激活 |

同一来源的触发/冷却、次数消费与各收益匹配是不同条件。次数应覆盖实际会消耗的命中，而非只挑高收益命中。引擎负责生成时段、选择合法受益者和扣次数，Agent 只检查规则解释是否正确，不再逐击手填份额。

反应伤害筛选 `event="reaction_damage", reaction_types=["stellar_swirl"]` 覆盖星扩散的即时风伤与已支持的两级冰风旋伤害分量；执行流水保留各分量的精确类型，不要求把流水中的子类型填入公开枚举。只需要某种元素或某位贡献者时，继续添加 `reaction_elements`、`characters` 等条件。它不会匹配普通扩散、星超导或月反应，也不会自动推断某件装备或天赋应适用于哪些分量；适用范围仍需查资料后声明。

提供 `limited_hit_benefit_contract` 时，受审来源优先按[有限次数收益来源](../sources/limited-hit-benefits.md)生成规则，复用精确父档案和数据库参数，不再重复手工交付同源规则或加算值。本轴match不能退还次数或绕过提供者属性依赖；来源参考范围与公式表再生成要求见该参考。

切人采用动作 start；occupation_end 表示占用结束，不是换人时点。输入事件按微秒时间和唯一 order 排序，触发 pre/post 必须明确；施放门槛先于动作触发检查。`action_end`/结束采样使用 `{action_id}:end` 身份。具体动画时长、取消时点仍需要来源或情景假设。

## 执行与查看

若实际capabilities提供 `attribute_source_contract`，可显式启用属性账。初始总精通只从当前角色面板读取；非额外精通、base及已含组成须另有依据，不能从两个数字相等推断。App会为缺分类的实际EM收益补来源身份并标unknown；只有依赖该未知分类的读取才失败，不能为了过校验把它改成included。

先compose核对 `attribute_source_adoption` 和原始效果。确认后可调用 `equipment adopt-attributes --plan ... --note ... --output ...`（聊天对应 `gs_adopt_attributes`），由软件填当前组件和整体上下文指纹。它保留已有分类、保留unknown，不替Agent验证游戏事实。初始included来源与动态来源使用同一组件标识，已含属性不再次加算；没有可定位来源时，明确基底假设及缺口，不能造一个新ID躲过重复检查。换配装、面板或事实后重新核对。属性账的总量、NONEXTRA、base及历史取样回执用于复核；公式Excel保留此轮取样，改相关输入需App重算。

```text
equipment compose --plan mechanics-plan.json --output composed-plan.json
equipment calc --plan composed-plan.json --output result.json --summary
equipment workbook --plan composed-plan.json --output formula.xlsx
```

compose 的 ready 只说明声明和来源依赖可用。当前calc支持 `--output ... --summary`，完整计算结果落文件，控制台仅摘要和文件回执；按capabilities判断，旧版本省略summary。完整calc结果的 `data.result` 包含 mechanics_status、mechanics_issues、mechanics_trace、sampling_trace 和 rotation_check。先按问题的 rule_id/event_id 找少量相关记录，再核对触发受益者、quota 前后余额、effect_applied/effect_rejected 原因及采样值；机械流水无需抄入 audit。

显式输入窗口可包含多个游戏轮。`rotation_model.period`保存完整执行边界；伤害统计依[统计截止](../review/report-window.md)选择用户要求或最后主动伤害，伤害与分母同窗。附加cycles检查不会替代未输入的动作或增加伤害。动作或资源失败后的前缀结果不能当完整轴或稳定DPS；检查summary与实际执行范围。跨游戏轮状态由执行器继承，不另填每轮满能量或重置次数。`status=confirmed` 是输入提供者确认，不表示引擎验证了真实帧数或游戏事实。

本轴特殊关联仍保存在 `rotation_tuning.buff_hit_matches`：机制项用 effect_ref.buff_id=规则ID、effect_id=该规则收益ID，命中使用实际展开ID。先 compose 取得 basis_fingerprint 后审核；它只改变候选匹配，不能绕过时间、CD或次数。常规规则能表达时保持空列表。

元素、特殊反应、目标状态与属性依赖按当前[元素合同](../elements/runtime.md)、[属性合同](attributes-and-sampling.md)及规则版本使用，不把旧普通扩散来源的局部限制套到整个原生引擎。若提供target_state_conditions，可采用[目标条件收益](../sources/target-conditions.md)。具体传播、多目标、随机、取样或生命周期组合能否执行仍查本次capabilities和实际诊断；数据库关系不自动补全角色行为，也不替Agent优化动作。条件value=null保留未知，不假作触发成功；不为导出成功删除未能表达的机制。

活公式工作簿使用同次原生计算的依赖，支持范围从导出回执的 support 读取。改变事件结构、层数、次数、match 或时间需要重新执行生成；不是工作簿内完整重演状态机。详见 [Excel 模式](../../outputs/excel.md)。
