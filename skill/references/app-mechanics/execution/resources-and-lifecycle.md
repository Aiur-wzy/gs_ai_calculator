# 资源、多跳与生命周期

有限生命周期hook/timer派发`dispatch_rule`或`resource_operation`时，先读当前`mechanics_contract.state_runtime.dispatch_routing`。目标trigger使用condition及唯一保留event_id；按该公开合同，用`engine hash-input`／`gs_hash_input`的canonical_json对`[kind,ref_id]`求哈希，加合同给出的前缀，或从验证错误details.expected_selector读取精确所需绑定。不要猜内部字符串、以手工condition触发或读实现源码。此身份不授执行权限，仍需唯一同源hook/timer、提供者与真实派发对象；Agent材料化已有对应family时优先让其完成结构编译。

当运行时同时提供 `mechanics_contract.state_runtime` 和 `state_runtime_source_contract` 时，已明确建立时点、周期、末跳与残留的领域可使用 `mechanics_model.state_runtime`：一次声明有限生命周期及timer，路由至同源收益规则或共享资源操作，避免逐跳伪造手工条件事件。以实际schema为准；时间字段带 `_us` 时为微秒。领域结束停止后续发放，已发放Buff按自身TTL到期，不能把两者混成同一持续时间。

角色资料确认重施只延长同一个已生效实例，并保持所声明的计时节奏时，用 `restart="refresh_duration"`。同一实例的累计、定时任务序号与节奏继续，后续次数仍受 `max_ticks` 限制。延长到期不会重新触发建立或结束。检查 `result.state_runtime.issued_trace` 中的 `lifecycle_duration_refresh` 与实际截止，及累计回执中同一实例的余额。真正到期或显式结束才运行结束操作与声明的重置；结束后再次建立为新实例。若资料只确认保留累计，攻击相位、阈值子击和其他刷新规则仍需核实，避免一律绑定到同一个生命周期。

所有生命周期、timer、hook和资源依赖先核对完整来源与当前整轴context；`source_only`不批准新增状态图。排除来源要检查 `state_runtime_source_adoptions` 的依赖闭包，不能只删除一个启动事件留下孤立收益。计算后分别检查 `result.state_runtime.issued_trace`（已调度）与 `execution_trace`（真正执行）；发出指令不等于收益已激活，pending不等于false。已有有限生命周期不代表连续夜魂资源或全部游戏时钟均支持，按能力清单继续补缺口。

若实际 schema 同时开放 `state_runtime` 第2版与 `resource_threshold_triggers` 第3／4版，可将达到资源阈值关联到 `start_lifecycle` 或 `end_lifecycle`；对应领域端点引用同一绑定。v3接已支持的原生操作与普通timer，v4另接当前真实反应已提交的资源操作尾，须有反应资源合同及其实际原对象/资源span；不能用反应JSON或未提交收入冒充许可。不提前手算过线时刻并伪造领域动作。阈值前后值读实际账，领域开始不等于立即发放收益；后续正间隔tick按声明派发。游戏频率、延迟、资格仍须有依据，队列可执行不等于连续游戏时钟已还原。

检查阈值联动回执的 `lifecycle_started`、`lifecycle_kept`、`lifecycle_refreshed`、`lifecycle_ended` 或 `no_active_instance`，再检查 `state_control_completed` 和 `state_batch_completed`。签发、领取、应用单步和完成整批分别判断。`state_delivery_failed` 只保留真实已执行前缀。来源操作的 `phase` 与内部continuation位置的阶段不同，不凭同名字段推断前后关系，不手填位置、序号或签名。改阈值或依赖后重算并分别核验。

### 多跳和资源

机制派生的 `resources[kind=particles]` 支持有限非负数量。使用平均数量时标记 `count_mode="average"`，可附完整的 `count_distribution` 作为数量依据。按实际触发时刻派生产球和到账，检查能量回执。

提供 `shared_pool_source_contract` 时，跨来源补充的整数资源用 `mechanics_model.shared_pools` 一次声明池、初值/容量、补充操作和消费者，不逐击手算份额。池的初值与容量分开；`grant` 增量封顶，`set` 设置余额；操作按明确的 pre/post 和 order 执行，反应后的补充不能追溯增强父击。当前每池一个消费者，`resource_gate.consumer_id` 把已消费回执接到同源收益规则，多项收益共用一次消费；旧 `quota` 不同时挂在该规则。

池、操作和消费者的 `source_id` 都要指向已登记的机制来源分项；提供者与规则 owner 一致，消费者和收益规则同源。**每项来源须采用当前整份方案指纹的 `manual_context` 核对**，填写实际依据；旧 `source_only` 或自动收益规则的身份不能授权新资源图。显式 excluded 会在执行副本移除依赖，未核实则保持 pending。未知容量、补充量或消费资格不能编造，资料到资源的自动编译尚待独立受审事实支持。

消费按实际执行的经典伤害命中判断前后台、筛选及具体活跃实例。实际命中但隐藏报告仍消费；未命中与扩散派生伤害不消费。match 不能返还资源，也不能让旧快照借新激活实例的余额获益。检查 `result.shared_pools.trace` 的请求、实际接受、前后余额、来源与事件身份，资源不足时应停止相应收益。App 与 Excel 的共享资源流水为只读回执，修改池或动作后重新核对并计算。完整字段从运行时 schema 读取。

若本次机制能力提供 `resource_reactions.contract=ordinary-reaction-resource-v1`，先查 `resource_reactions.rulesets`。在目录列出的 v3 或 v4 中，经典命中实际产生的融化、蒸发或扩散可补充共享池常量。将既有 `shared_pools.operations` 的 `grant` 操作列在 `resource_reaction_contract.operation_ids`，由实际 `elemental_reaction/post` 决定发生、筛选和收入。每条操作明确反应种类，其他选择器按对应规则集及 schema。独立无伤害附着和其他反应不在这一公开范围。触发还依赖领域或范围时，逐项表达或保留未解决的条件。

反应发生、操作提交与实际接受量分别检查：未匹配没有收入；满池可能已尝试但接受0；冷却拒绝也不是再次补充。来源采用需要完整 `manual_context`；排除来源会移除对应执行成员，pending不会降为旧方式执行。`equipment review` 的 `resource_reactions` 视图按实际父事件、反应角色或资源来源角色、来源及时间分页；详细值能定位原资源账。“反应资源联动”只读页不再加资源或伤害。后置收入不会追溯父击、既有输出快照或反应创建时精通。保存一致性只证明所存输入与实际记录一致，不能当作游戏资料批准；动作失败时保留的已执行前缀不提供整轴DPS。

当能力明确提供共享池 v2 时，小数资源用 `unit_scale` 定义每单位原子数，容量、初值、操作金额和命中成本全部使用整数原子，不能混入显示单位数值。`spend` 是显式扣除资源，支持固定请求或 `all_available`，以及整额成功 / 允许扣尽现有余额两种策略。以回执 `accepted` 为实际扣量，`unfulfilled` 为未扣足，`wasted` 为补充溢出，不将设置余额下降当作消费。持续消耗应由有限生命周期的定时器调用同一池操作，不逐跳手算。v2 不自动证明消耗时钟与具体游戏机制一致，来源仍须核实；累计、阈值和伤害投影仅在相应能力实际提供时采用。

若当前共享池v2的schema提供`random_effect`，可为固定正数的`spend/all_or_nothing`声明`kind=waive_spend`，或为`grant`声明`kind=extra_grant`及正数`extra_amount`。默认`mode=expected`：符合条件次数×`chance`，不提供`outcomes`，不枚举或抽样。小数数量使用已有`unit_scale`，余额、容量、操作与消费者数量同时换算；例如每单位100原子，成本100、免消耗概率0.34时，期望扣66原子，不能四舍五入。

用户需要特定触发情景时，由Agent询问用户确认次数或安排，再使用`mode=custom`：保留实际`chance`，用`outcomes[{run_id,key,success}]`指定事件结果。身份从实际资源流水或错误details取得，不猜测。缺少已触发结果、多余未执行结果或与0%/100%矛盾的结果会被拒绝。方案变化后重新核对身份。引擎不为用户搜索情景或计算情景发生率。

免消耗前仍须有完整库存；库存不足不能用本次未来收益补足。指定免消耗成功表示执行成功、消费0，仍占用success冷却，不增加消费累计。额外获得受容量限制，分别核对`requested/accepted/wasted`。结果区分期望次数和指定情景；容量、阈值与后续连段按所选数量口径计算，不把它说成全场随机机制的精确期望或每次实战都能执行。分支模拟不属于产品能力。

若能力提供 `resource_counters`，依赖实际消耗的累计用同一原子池和有限生命周期声明，不逐时点手填。累计只引用同池明确 `spend` 的实际 `accepted`，不把 `set`、`grant`、命中消费者或请求扣量当成实际消耗。按依据选择创建/建立后的窗口、创建/结束/不重置以及按池键/全池累计；来源与完整依赖子图仍需当前 `manual_context`。检查共享池、累计和生命周期三份实际回执：窗口外可真实扣池但不计累计；已签发调度不等于实际执行，失败后不能补造后续重置或新代步骤。

原始消费累计是审计账，不直接等于有上限的游戏层数。仅累计能力不支持自动转换为伤害、属性或阈值触发；只有相应派生读数与投影能力实际提供时才使用。尚未支持时采用既有补正接口并标出边界，避免与自动收益重复交付。`never` 在同次执行的多代和检查窗口保留历史，不能为通过循环检查逐轮清零。

能力另提供 `resource_counter_views` 时，受审计数可声明单位换算、封顶或整档读数，保留原始累计不变。schema1的 `kind=counter_view` 仅用于规则的provider域、sample=current和经典伤害收益；不用于普通面板、精通、充能或敌方，也不会自行触发规则。使用完整caps/schema定义，不把角色文本中的“伤害提升”直接认定为某个乘区。view的cap/step与表达式本身需独立来源和完整上下文；已审counter不能代替这些事实。核对 `resource_counter_views.trace` 的原始原子数、单位、读数、单次值、层数及最终合成值；它们不是多份额外伤害。Excel保留真实取样后的最终收益，不能编辑历史读数后声称重新执行了累计。改变相关动作、来源或参数后回App重算。

若本次caps明确支持schema2/self_frame，需要按输出者取样的累计收益可声明 `resource_counter_views.schema_version=2`、`effects[].domain=self`、`value.sample=self_frame`。先核对游戏来源实际取样规则，不因有接口就假定所有技能都快照：消费者未设self_snapshot_event时读取当前累计；显式引用则必须指向实际已执行的自身捕获，系统冻结读数、受益成员与层数，缺失/未来/取消不得改成当前值绕过。当前限次或共享消费门仍按同一实例核验，不能把历史值和新实例额度拼接。支持classic（含增幅），剧变、普通属性及trigger/event资源取样仍不在此范围。

这类历史值由引擎从完整动作轴与实际资源账生成，优先声明一次规则和真实捕获关系，不逐击手抄值或建立大量短时窗。变更捕获、表达式、单位、层数、消费门或match微调后重新审核完整manual_context并重算。核对`resource_counter_views`分页中的frame_selection/capture_context/gate_qualification，以及`resource_counter_frames`分页的捕获流水；界面与Excel中的“累计输出快照”不增加伤害。保存回执counter_self_frames只证明保存输入与实际流水一致，不证明当前数据库新鲜或游戏规则已核实；不得手工编造proof。失败前缀保留已发生伤害，整轴DPS为空。

单个命中用 `hit_events={hit_id,action_id,order}` 关联模板命中。相同原型的稳定多跳用 `hit_patterns={id,prototype_hit_id,action_id,offsets,orders}`；offsets 是相对动作开始的实际命中偏移，展开 ID 为 `{pattern_id}:1`、`:2` 等。偏移来自资料/假设，不用持续时间反推段数。不要同时手写同一跳和其派生事件，跨不同倍率/属性模式使用不同原型。原型的 count 不替代逐跳次数消费。

资源规则 `resources` 可声明 particles 或 energy_direct。粒球填写数量、元素、粒球类别和旅行时间，随机数量默认按每次合格触发的分支概率取加权平均。直接回能填写基础 `amounts`。两种规则都声明 `delay` 和 `source_part`。`resource_ownership` 选择 `action_id`，或 `source_ref={"contract":"resource-trigger-source-v1","kind":"current_trigger"}` 绑定正在执行的真实事件／条件；不借不相关动作，不填写run身份。声明接管旧event_ids，粒子迁移时连同到账一起处理。规范来源分项和实际pre/post发生共同决定收益身份；改rule名或source描述不能绕去重。同动作独立来源用已接纳source_id/source_part证明，手工资源可携带resource_identity；不把真实独立收益删掉通过检查。

直接回能采用用户指定的项目政策 `energy-direct-base-er-v1`：amounts是基础量，在实际到账前冻结每个接收者ER，基础量×ER一次后再按能量上限记到账/溢出。粒球仍只乘一次ER；初始余额和扣能不乘。这是当前软件采用的计算口径，不代表外部游戏机制资料已证明直接回能普遍受ER加成；遇到相反资料保留来源并在结果中说明差异，不因此把当前工具判为不可用，也不擅自把基础量除以ER来绕过政策。

新建方案可保存政策声明；compose保持作者原件不变，在独立编译轴和执行回执中绑定当前政策。检查energy_settlement_policy及能量流水base_amount/energy_recharge/theoretical_amount/gain/overflow。没有final开关。旧结果保持冻结；旧直接回能材料缺政策标识而需要当前引擎重放时，按明确不兼容诊断重新材料化，不能猜它是否预乘ER或把旧值直接加标识重签。需要不同最终到账量时沿现有受控覆盖合同处理，不以普通输入暗改已确定口径。

### 真实反应触发的直接回能

原生或连接 App 的 CLI 读 `mechanics_capabilities` 的 `/resource_trigger_source/direct_reaction_energy`。独立 CLI 用 `capabilities --section plan_contract --pointer /mechanics_contract/resource_trigger_source/direct_reaction_energy`。同一机制资源的 `resource_reactions` 描述共享池的独立路径。

1. 让元素模型实际产生所需反应。规则的 `trigger` 使用 `event=elemental_reaction`、`phase=post`，按游戏来源选 `reaction_types`，需要时筛选 `elements` 和 `characters`。继续保留真实附着、落点和该来源的触发间隔。
2. 在同一规则的 `resources` 用 `kind=energy_direct` 写基础 `amounts`、`delay` 和 `source_part`。`resource_ownership` 用同一 `rule_id`、`source_part` 和 `source_ref={"contract":"resource-trigger-source-v1","kind":"current_trigger"}` 绑定实际发生；如替换了旧手写回能，在 `replaces_event_ids` 标出旧项。按当前作者 schema 与来源绑定补齐其余字段。
3. 对照 `rotation_check.event_log` 的到账记录，用 `resource_cause.run_id` 找实际反应，再核触发者、反应目标、到账者、ER、余额与溢出。未命中或没发生所选反应的对照事件不应到账。共享池的 `resource_reaction_contract.operation_ids` 只列共享池操作，直接回能沿本节规则声明。

提供 `resource_execution_budget` 时，按capability以 `calculation_options.resource_limits` 稀疏设置本次work/slot/byte/position/索引预算。检查保存的policy、真实usage与termination；明确小预算不被私自提高，取消或超限不等于收入未曾发生，不能重放已提交父步骤。新 `generic.resource_operation` handler仅安装其公开闭合池/操作合同；来源采用、实际反应与生命周期授权分别检查，不将已注册handler解释成任意hook均支持。

## 概率次数的默认取整口径（1.0.3）

默认计算符合条件次数×概率。可线性折算的资源数量、回能和伤害次数保留小数；额外动作和Buff等离散收益，默认对累计期望次数向下取整，不要对每次0.34分别取整成0。Agent可以向用户确认其他指定次数或具体安排；引擎不搜索情景、不枚举随机路径。

资源变化直接触发Buff时，共享规则按累计期望触发数取整，记录`expected_discrete_count`的期望数、已应用次数和本次新增次数。原有条件、CD、叠层上限和持续时间继续执行。动作仍需Agent按合法时点写入排轴，引擎不自行生成额外动作。公开结果需说明这是期望次数的确定性近似，不能保证每次实战循环。
