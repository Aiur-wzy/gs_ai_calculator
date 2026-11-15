# 固有天赋资料与有限反应增伤

这是兼容入口。若运行时提供`mechanism_source_contract`且目录有符合当前主体及动作资格的受审资料，先按[通用机制来源](../execution/materialization.md)读取其无执行权候选；只有capabilities发布匹配的Agent材料化机制族且能够忠实表达时才迁移采用，否则保留兼容入口或明确pending。同一收益不能同时保留两种来源；资料ready、Agent采用与本轴可执行分别核对。

这是一条有限自动路径，不表示完整角色机制已覆盖。查询使用当前 `runtime_contract` 的 `passive_source_profile`；执行另需 `plan_contract.passive_source_contract`。未提供相应能力时，不自行发明字段或把资料ready当作自动执行。装备收益仍走[反应收益来源](reaction-benefits.md)，攻击派发仍走[元信息](attack-metadata.md)与[动作档案](../actions/event-profiles.md)。

## 动态属性入口

若caps另提供 `passive_attribute_source_contract`，明确选择其 `inherent-ordinary-swirl-attribute-v2` 场景，使用 `attribute-ledger-v2` 与 `gauge-swirl-dynamic-v3`；父动作使用 `calibrated_single_application_v2`。从新caps复制场景，核对 `stat_basis=attribute_ledger_nonextra` 与 `same_proc_em_policy=no_same_proc_em_changes`。这不会升级旧契约，下文固定面板限制仅属于旧profile。

新来源的提供者资格仍为C0/90级/显式突破6，完整机制仅部分覆盖。引擎从数据库保存的原始属性表达式与已审事实生成增伤读取，每次普通冰/水扩散读取实际NONEXTRA；增益持续期间保留该次触发值，下次有效反应刷新。总精通用于扩散伤害，NONEXTRA用于此转化，初始总面板不能代替两者。明确的较早普通常量加成可以参与，已认证排除项不进入NONEXTRA；未知分类、未证实的转换链或同次命中/反应后普通精通写入保持待完善。不能把比例转化手改常量以通过此入口。

先核对原始描述和组件分类，再按[属性账流程](../execution/rules.md)接受真实组件和完整上下文；随后完成来源确认并复算。两种确认互不代替。因果说明不重复嵌套组件校验指纹，完整来源校验仍由 `attribute_source_adoption` 执行；不得删除该校验。App还会检查后续编译器实际加入的精通写入，早期预览通过不等于最终可执行。检查 `sampling_trace`、`attribute_ledger` 和实际接收者伤害，不只看规则是否生成。Excel保存本轮真实取样，修改相关面板或来源后必须回App重算。

## 先发现来源入口，再建立动作检查点与事件拓扑

资料阶段先查询候选，完整响应保存为角色目录中的独立小文件，由角色机制说明和索引链接：

```json
{"action":"passive_source_profile","character_id":10000047,"version_line":"release"}
```

核对候选原文和 Avatar→Depot→固有开启项→ProudSkill→Talent→Ability 来源链后，才选择精确 `proud_skill_id`。即使仅一个候选也不自动选择；多Depot须明确 `skill_depot_id`。以下只是已知来源的查询形状，不是默认队伍或通用ID：

```json
{"action":"passive_source_profile","character_id":10000047,"proud_skill_id":472201,"character_level":90,"ascension":6,"version_line":"release"}
```

选定前不传等级/突破；选定后未知可省略，不推算补齐，公开查询不传null。此接口不收 `talent_level`、`constellation`、App的status/source或确认值；固有ProudSkill记录level不是角色等级，也不适用普通技能10/13原则。请求与结果可沿[同快照batch](../../data/runtime-query.md#同快照批量与资料小文件)落文件；依赖候选结果的选择必须等发现完成，不能在同批预猜。

| 返回项 | 应作出的判断 |
|---|---|
| `source_status` | 选中资料关系是否齐备；不表示已解锁或可执行 |
| `unlock.status` | 缺输入pending、未达门槛locked、超阶段上限invalid，或`eligible_under_declared_progression` |
| `descriptions.status` | 中文全/部分/缺失；保留缺文字段与来源，不自行补造解锁原文 |
| `source_fingerprint` / `unlock_fingerprint` | 前者绑定资料，后者另含给定等级/突破及判断；两者不是App接受值 |

门槛判断仅核对明确开启阈值、阶段记录及等级上限，不验证最低升级流程或账号状态。90级不能推断突破6；资料ready可同时解锁pending、locked或缺文。保存机制说明后按[分阶段流程](../../workflow/index.md)建立action_checkpoint和event_topology，实际需要时即可填写原生来源，不受focus限制。

## 原生来源与场景

原生v3使用独立 `passive_sources[]`，不伪装装备 `effect_bindings`。按当前schema填写 `id`、`actor`、`proud_skill_id`、`constellation`、完整 `scenario`、`accepted_fingerprint`；多Depot时另填 `skill_depot_id`。草稿可用null确认/命座，不能据此执行。角色ID和资料线取该actor的 `equipment_inputs[actor].character_ref`，等级取 `manual_template.characters[].level`，不在来源重复抄写。

对应装备输入的 `progression` 保存显式突破依据，例如实际已核实突破6时：

```json
{"ascension":6,"status":"confirmed","source":"填写本方案实际采用的角色突破依据"}
```

未知时用 `ascension:null,status:pending` 并保留待补说明，不能照抄示例作为证据。数据库只判断数字；App另核对声明、来源归属及执行范围。首次受限执行仅支持C0/90级/显式突破6、提供者 `mode=panel,panel_policy=static_included`；这不是数据库发现/查询的限制，也不表示更低突破在游戏中没有该天赋。

从caps复制完整 `default_scenario`，不删掉未知字段以求ready。当前profile为 `inherent-ordinary-swirl-bonus-v1`，普通冰水原目标、单人固定队伍、已接受 `gauge-freeze-reference-v1`、无命中停滞。实际核实后填写：

| 场景字段 | 当前可采用值及必须说明的事实 |
|---|---|
| `target_id` | 实际唯一元素目标ID |
| `origin_target_range` | `within_source_range`：原目标满足监听范围 |
| `recipient_range` | `all_party_within_source_range`：所有实际接收者满足受益范围 |
| `initial_source_effects` | `none`：窗口开始前无该来源活跃增伤，不能把未知当空 |
| `recipient_bonus_policy` | `not_included`：所有接收者初始面板未计此条件元素增伤 |
| `stat_basis` | `fixed_panel_nonextra_em_equals_total_em`：明确声明提供者固定EM等于该原始属性口径 |
| `event_order_policy` | `declared_atomic_hit_order`：实际原子命中/反应顺序已有校准依据 |

`status`为assumed或confirmed且 `source`有真实依据；unknown/pending可保存，确认hash不能升级未知。原始监听range20与接收者谓词40是不同语境，不能互相替代，也不凭数字宣称单位是米。

固定面板相等是一项明示假设，系统不能从匿名总精通反推 `FIGHT_PROP_NONEXTRA_ELEMENT_MASTERY`。存在潜在精通写入、比例转化或来源不明的已烘焙精通时，当前自动路径保持pending；不能把转换结果改写为常数来绕过。给定面板仍包含两件套等常驻属性，不再次加算；此条件元素增伤须单独未含，不能与装备常驻已含约定混为一谈。

## 一次来源确认与实际触发

先按当前动作检查点和事件拓扑完成元信息与动作档案的综合接受，再compose读取 `passive_compilations`。本片逐事件核对提供者的已接受C0/E风附着父链：精确攻击候选→档案→实际ability/action→生成命中/application。没有实际动作、混入手工或武器风附着、来源或身份矛盾时保持pending；同名或同时间不能替代亲缘证明。不要为开通天赋补造动作，也不能把后台触发能力当作Q/下落来源已经支持。

阅读完整来源、分支、`unlock`、`stat_basis`、`scope`及缺项；条件确实符合后，Agent按本次 `required_confirmation.accepted_fingerprint` 写回来源的 `accepted_fingerprint`，无需逐项请求用户批准。不抄旧hash、不编辑缓存或回填报告中的rules/parts。更换来源、突破、命座、精通口径、接收者/场景或相关父来源后fresh复核；动作移动仍须重新计算和校验作用范围，不意味着所有来源确认都要无意义重做。

自动生成两条冰/水规则后，不再手填每次增伤数值、持续区间或同源Buff/match。两个部件原子交付，未支持分支仍保留；另一固有来源未就绪也不能把部分执行副本冒充完整方案。

## 评审与边界

- 提供者实际普通扩散触发时取样其固定EM，只有反应对应元素产生增伤；冰/水分别刷新和到期，同元素刷新不叠成多份。持续时间/系数从来源取，保留原浮点，不手抄漂亮常数。
- 这类天赋允许提供者后台触发，区别于风套动态减抗的在场门槛；仍需本片允许的实际父链。其他角色扩散不能替该提供者触发。
- 受益者的增伤属于其 `self` 取样域：已在效果建立前完成的输出快照不会被后来增伤追溯改写。提供者触发取样与受益者输出快照是两次不同取样，按流水核对。
- 附着ICD阻止反应与伤害ICD仅阻止派生扩散伤害不同；后者不能直接当作没有反应。不要按冻结图标或预期动作手填反应答案。
- 对照 `passive_compilations`、来源回执、规则事件与只读“固有天赋来源”表。Excel不重演反应/收益状态机；来源、突破、条件或结构变化后回App重算并重新生成。

当前完整来源仍partial；动态NONEXTRA账本、多提供者覆盖、火雷/星或传播、未知空间/既有初态及未受审Q/下落/武器父链均不在此执行范围。显式来源清单和已核提供者风附着不等于全队所有天赋已经自动发现、解锁和执行。查询成功、门槛符合、局部ready和实验参考结果分别说明，不能合并为“完整游戏机制已验证”。
