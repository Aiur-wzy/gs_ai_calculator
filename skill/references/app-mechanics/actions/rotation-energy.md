# 动作、冷却与能量检查接口

原生App从`capability_index`按返回的resource/pointer读合同，不能给pointer套CLI的`data.plan_contract`包络；外部CLI按实际capabilities命令返回读。本文业务片段不等于工具参数，是否需要request等外层以本次schema为准。

工作流程见[rotation-audit.md](../../workflow/coverage-and-review.md)。动作初编辑不以本接口做门禁，可以保存未知CD、能耗、初始能量和循环状态；任意focus都可补资源输入并校对。正式结果按真实动作／资源输入及检查结果接纳，不因focus标签跳过失败。当前接口提供显式动作、冷却和确定性元素能量检查；受限元素模拟见[元素运行时](../elements/runtime.md)，不等于完整游戏仿真。检查器消费已声明事实，不识别技能原文、不自动排E/Q、不分配怪物球或补Buff。

原生v3另使用 [声明式机制](../execution/rules.md) 在同一事件队列执行触发、刷新、次数和资源规则，减少手填。下文基础动作模型仍适用；v2/独立检查继续沿用明确的手工资源事件，不必为了单次检查升级所有旧文件。

本次提供`energy_generation_contract`时，受支持的技能命中产球优先按[产球来源](../sources/energy-generation.md)绑定；数量、元素、共享规则和逐动作关联由系统生成。仅有资料查询契约不代表可以自动派发；手工怪物掉落与未支持来源仍单独声明，不重复交付同源资源。

capabilities提供动作基础资源绑定契约时，适用的C0常规固定耗能Q可采用[动作基础资源来源](admission.md)。自动扣能写在公开来源绑定 `action_resource_sources[].energy_delivery="action_start"`，按该来源完成fresh确认；不是写入原生 `rotation_model.abilities[].energy.delivery`。后者是编译后的执行副本字段，原生方案禁止回填。自动来源已拥有的扣能不再重复写 `energy_spend`，其余字段省略方式见来源合同。

来源适用范围外，仍可用本页公开手工路径：能力明确填写 `energy.required/amount`，每个实际扣能动作在 `rotation_model.events[]` 声明一条 `type="energy_spend"`；初始能量、产球、到账、返能和资源修改仍按真实规则分别输入。不改命座或技能身份来迁就自动来源，也不把“来源不支持”解释为整个手工模型不可计算。以下数字必填规则以有效来源绑定拥有的目标字段省略为例外，不能把省略值写成null、0或缓存值。

能力属于多段施放时，先按[多段施放与续段窗口](cast-sequences.md)核实阶段、窗口与冷却；ready来源可复用，否则使用当前允许的有据手工cast_sequence，不因缺公共档案拒绝所有建模。首段的普通冷却从 `action_start` 立即运行；后续段只由同角色、同组、正确下一阶段和未过期窗口接纳，不能因为基础冷却正在运行就拒绝，也不能把后续段原始 `cdTime` 建成独立冷却。

## 获取契约与调用

读取本次 `rotation_contract`：原生方案按 `native_authoring.schema` 填写 `rotation_model`，并核对 `compiler_owned_fields` 与 `manual_fallback`；其中未开放的执行字段不能照抄。`schema` 的范围为 `engine_execution_and_independent_rotation_check`，供编译后的执行模型和独立 `engine check-rotation` 使用，不代表原生方案可直接填写其中全部字段。外部CLI从 `capabilities` 的 `data.plan_contract` 读取，原生App按上文resource/pointer读取；字段是否提供以本次自描述为准。动作类别、事件类型、证据要求、时间精度与周期证明仍使用各自返回的合同；`action.*` 与伤害分类 `damage.*` 是不同 ID 注册表。

```text
engine check-rotation input.json
engine check-rotation input.json --format json --output check.json
engine check-rotation input.json --format xlsx --output check.xlsx
```

命令接在 `py -3.12 -m genshin_app` 或便携版 `GenshinCalc.exe` 后；全局 `--data-dir` 放子命令前。新结果默认不覆盖；明确覆盖时加 `--overwrite`。捆绑示例为 `examples/rotation-energy-check.json`，是合成能量机制示例，不是角色推荐轴。

| 输入 | 当前处理 |
|---|---|
| 独立检查文件 | `kind="genshin-rotation-check"`、`version=1`、`template`（合法 resolved 伤害模板）、`rotation_model`（模型或 null）；顶层仅接受这些字段 |
| 原生方案 v2 | `kind="genshin-plan"`、`plan_version=2`，必须有 `rotation_model`；先用指定数据库重新 compose，ready 后检查 |
| 原生方案 v3 | 显式包含rotation_model和mechanics_model；先组合来源部件，再执行动作/机制/能量，共用当前面板ER；完整执行过程优先看equipment calc结果 |
| 原生方案 v1 | 禁止夹带 `rotation_model`；作为旧方案返回未配置检查，不从伤害推导动作 |
| 裸 resolved v1/v2 | 保留原伤害语义，动作/能量结果为 `not_checked` |

原生方案的每个角色 ER 都取**本次组合后的面板**，包括人工管理的角色；模型中旧的 `energy_recharge` 会被覆盖，面板缺值则保持未知。返回 `panel_bindings` 记录 `declared_value`、实际 `value` 和来源字段，`resolved_rotation_model` 是实际检查输入。独立文件直接使用其声明 ER，调用者负责核对与数值模板/装备来源的一致性。

## 模型 v1 的组织

| 字段/对象 | 语义与核对点 |
|---|---|
| `schema_version=1`、`period`、`max_cycles` | period是整个显式输入窗口长度，一个窗口可以包含题面两轮。max_cycles可省略，默认3，当前范围3–20，是重复窗口审计／收敛预算，不是题面轮数；不会把repeat=once动作重复执行。时间以秒输入，精度至微秒，不静默舍入 |
| `characters`、`initial_frontline` | 每人有稳定 id、element、energy_recharge、energy_capacity、initial_energy；必须明确初始前台，初始能量不默认满 |
| `abilities` | 每种能力有 id、character、action_type_id、可选 variant；明确 `cooldown`/`energy`，没有则写 null，漏写表示未知 |
| `ability.cooldown` | group、duration、start（action_start/occupation_end）、charges、recovery（sequential/parallel）；变体共享组由 Agent 明确 |
| `ability.cast_sequence` | 可选的 group、stage、max_stages、continuation_window、cooldown_start=sequence_start；末段 window 为 null。它是有序续段状态，不是 charges |
| `ability.energy` | 手工输入为 required（检查门槛）、amount（实际扣除量）；爆发即使零消耗也显式声明这两个数。有效来源拥有的字段按来源要求留空；原生不填写执行副本专属 delivery |
| `initial_cooldowns` | character/group/ready_in；每个 CD 组必须说明初态，`ready_in=[]` 表示全部次数就绪 |
| `actions` | id、ability_id、start、occupation_end、order、repeat；可声明 switch_allowed_at；切人动作还需 switch_to |
| `events` | 按下表分开记录生成、入账、直接回能、扣能与冷却修改；不是 Buff 或伤害事件的容器 |
| `energy_recharge_windows` | id、character、start/end、delta、repeat；在该角色基础 ER 上加比率增量，需有情景/机制依据 |
| `action_hit_links` | action_id/hit_id 关联伤害来源；一个动作可有多个 hit，每个 hit 只关联一个动作。未关联或聚合多段会保留待处理 |
| `gap_explanations` | start/end/reason，解释一个周期内的空档；跨周期状态仍由检查器连续运行 |

角色、能力、动作、能量事件与ER窗口的status/source表示输入提供者的证据声明：confirmed是调用者确认、assumed是情景假设、pending是未确认。引擎验证字段与约束，不会验证网址是否证明真实游戏机制；来源旧版本和未确认内容另留记录。假设下通过按determinacy=conditional解释。数值null是未知，不填零消除待处理。

每个动作和事件的 `repeat` 显式指定 `{"mode":"each_cycle"}`、`{"mode":"once"}` 或 `{"mode":"cycles","cycles":[0,2]}`；轮次从 0 开始。动作开始在 `[0,period)` 内；其后续事件可以延迟跨过周期末。一次性怪物掉球不能随着循环复制。动作派生事件与动作、入账与产球必须使用匹配的重复策略。

## 能量事件与规则结论

| `type` | 主要字段 |
|---|---|
| `energy_spawn` | t、particle_type（particle/orb）、element（元素/clear）、非负有限 count、source_kind（skill/weapon/enemy/other），可加 lost_count；技能/武器需 action_id。平均数量用 count_mode="average" |
| `energy_credit` | **credit_at**、spawn_id、非负有限 count，继承产球数量口径。按实际到账时刻取前台和各角色 ER |
| `energy_direct` | t、amounts（角色ID→基础直接回能）；当前项目政策由引擎在到账时乘接收者实际ER一次，不预乘、不套粒球元素／前后台系数，详见[资源结算](../execution/resources-and-lifecycle.md) |
| `energy_spend` | 手工模式用t、action_id、character、required、amount；每次有能量需求的动作恰好一条，发生在其占用期间。采用已确认action_start资源绑定的动作禁止再写此事件 |
| `cooldown_change` | t、character、group、operation（reset/reduce/restore）；reduce 的 amount 是秒，restore 的 amount 是次数，reset 不填 amount |

所有事件都保留 id、order、repeat、status/source。动作及外部输入事件按 `(微秒时间, order)` 唯一排序；同刻必须用不同 order 明确先后，不靠数组或 ID 排序。到期冷却在该时刻输入前恢复。产球必须在来源动作之后、入账必须在对应产球之后；同刻可用 order 表达。总到账数加明确丢失数等于产球数，未去向明确的球不能算作“无影响”。

切人动作start就是切换前台的时间，occupation_end是占用结束，switch_allowed_at是前一动作允许切人的时间。旧双份输入冲突会返回switch_conflict的expected_switches/actual_switches/differences，含动作ID和预期/实际项，按具体差异修正。新v3从动作派生切人投影，不再要求Agent重复维护一份切人清单。

手工构造原生动作模型时，角色换场必须在`rotation_model.actions`显式写一条`action.switch`能力对应的动作及`switch_to`目标；只写`manual_template.switches`不会反向生成切人动作。检查相邻角色施放之间是否真的有切换，而不是把“模板中有切人表”当作前台已经改变。后台持续命中继续归原提供者，不为其伪造切人。

自主排轴用真实占用／允许取消时点安排最早合法后继，不以后台最后一跳作为occupation_end；必要等待写入现有gap_explanations并复核检查回执中的gaps。检查通过不证明空档已经最少，接口也没有“空隙必须为零”的门禁。动作时点变化后同步关联命中、资源与条件，fresh重算，不只移动UI标记或沿用旧条件表。

v3资源规则负责派生明确的产球/到账/直接回能，resource_ownership标明被替代的手工事件；同一来源不能两种方式同时交付。v3动作扣能发生在动作开始同刻：手工模式spend放start同刻的后续order，自动action_start模式不分配spend或新order。系统扣能流水为只读历史审计，不触发机制且不二次扣除。其他扣能时机若运行时拒绝则保留未支持，不假移时间让它通过。具体可用契约随capabilities读取。

自动模式看rotation_check.automatic_energy_delivery：planned_actions只统计返回父动作，三种提交状态之和等于该分母；max_cycles_planned_actions与unreported_actions区分检查提前证明后未再访问的窗口。不要据审计行数称为队伍能量循环已证明。旧独立检查在显式spend处扣能，旧mechanics在动作接纳处扣能；同刻退款下本就可能不同。新自动模式统一接纳时扣能，不能把旧语义差异误解为后来返能能救活当前爆发。

若能力声明 `rules=[{id,kind}]`，每次动作要给 `rule_outcomes`：triggered 提供匹配的 event_ids；not_triggered 给 reason；pending 明确待补。已经挂载的产球/直接回能/冷却修改规则不能通过省略事件混成通过。这个契约只核对**已声明规则**；数据库关系推断潜在漏项尚未实现，仍由 Agent 做全部来源覆盖检查。

正式计算前核对产球是施放还是命中触发、数量、同源间隔、接球归属和飞行时间。平均模式可附 `count_distribution: [{count, probability}]`，其中各分支是整数粒数，概率之和为 1，加权均值须等于 count。包含零粒分支。按每个实际机会记录产球，到账和丢失总量须守恒。平均数量用于能量入账，按粒发生的离散效果需有独立规则。检查 `rotation_check.energy_check.particle_quantity`，报告采用的口径和不足的情景。

## 解释返回结果

| `status` | 对用户如何解释 |
|---|---|
| `not_checked` | 没有模型，动作与能量未检查 |
| `invalid` | 字段、身份、顺序或规则冲突；先修输入 |
| `pending` | 关键数值、证据、规则结论或关联待补，不能把未知当零 |
| `failed` | 已找到不可执行事件；查看 issues 的事件、轮次、时点与缺口 |
| `unproven` | 已检查轮次内没有失败，尚未证明周期边界稳定 |
| `passed` | 在已声明假设与受支持约束内，有限事件影响已结束且完整周期边界重复 |

读取 `action_check` 与 `energy_check` 的独立状态，以及 `opening_status/cycle_status/checked_cycles/proven_cycle`。失败之后的事件标 `not_verified`，不再冒充已执行；不会借能量、删爆发或自动延后动作。返回 `event_log`、`cycles`、`characters`、`gaps` 和 `issues` 供逐条定位，JSON 可保存完整输入与面板绑定，xlsx 导出可读的动作清单、能量流水、周期边界和问题表。

基础周期状态包括能量、前台、CD/次数、占用、在途粒球/事件和临时ER；v3还继承已声明的机制冷却、活跃层、次数与延迟事件。首轮满能量不重置，未来仍有一次性事件时不能提前证明重复。旧纯动作检查不覆盖增益状态；v3只覆盖实际声明的机制。两者都不认证体力、实际命中/距离、元素底/反应、随机概率或完整游戏机制。

v3只汇总完整显式输入的origin0事件，该输入可包含多个游戏轮。新`damage-report-window-v1`按`0≤t≤T`统计（显式用户截止优先，否则末次主动施放的直接命中），所以T=period时包括恰好在右端的origin0命中；不采用新统计合同时仍保留旧`[0,period)`口径。动作开始仍须早于period，右端延迟事件可在后一个内部检查窗才实际执行，其来源仍是origin0，不能混成下一轮来源或重复计入。要计算多轮伤害，就输入相应完整秒数内的动作；额外cycles只用于执行/状态检查，不能代替缺少的动作或增加其伤害。出现失败时先确认输出覆盖，不能把局部总伤或未验证窗口称为稳定实战DPS。

统计截止能量见[计算辅助](../review/corrections.md)的能量视图 `energy.report_cutoff`（以本次工具自描述为准）。它从保存的实际流水取截止及之前的到账和扣除，包含恰好在截止时的原方案事件。完整验证或内部检查结束时的 `final_energy` 可以包括此后的到账，汇报时标明对应时点。只收到部分记录时，截止余额仍待核实。

检查发现字段／引用表达错误时直接修正；需要调整动作、时点、接球或机制事实时，按[工作重点](../../workflow/index.md)修订相应输入及依赖，漏事件补拓扑，动作大改更新动作与映射并重新校验后继，不机械退focus或暗改用户固定动作。
