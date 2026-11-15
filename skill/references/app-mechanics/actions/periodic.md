# 周期攻击来源与多通道动作档案

仅当当前软件同时提供 `runtime_contract.actions.periodic_attack_profile` 和 `plan_contract.periodic_attack_contract` 时采用。字段以本次自描述 schema 为准；旧程序没有此契约时不能写入研究草案字段。仍按[分阶段工作流](../../workflow/index.md)按需整理事实并建立action_checkpoint和event_topology，再进入numeric_model转换输入。

当前工作源码的自描述若提供 `application_cadence`、`derive_count=through_instance_end_inclusive` 和 `replacement_policy=replace_previous`，可以使用以下紧凑路径；正式旧版没有这些字段时仍按旧合同处理：

- 若同时提供独立 `periodic_channels` 游戏动作清单，先核对通道、组件、首跳、间隔、次数和生命周期策略，再读取动作物化回执。派发档案自己的 channels 只能证明内部自洽，不能证明真实动作没有漏通道或漏跳。无独立清单保持 `unknown`；少跳、重复跳、调度或生命周期不一致保持 `incomplete/diagnostic`。
- 生命周期中的刷新、暂停／恢复、退场和终结语义必须逐项落在当前 capability。App 当前支持固定有限窗口中的 fixed、等间隔和由独立受审清单给出的 `explicit_offsets`，以及严格次数派生、编译期 `replace_previous`、动作开始／结束刷新和退场继续。统一投影器虽能验证明确轴控制的 reset／extend、两种暂停恢复和退场停止，但这些控制尚无 App 输入链，仍须 pending。运行期状态刷新、提前销毁 hook 或通道资源事件也不能靠 Agent 展开若干命中伪装成引擎已执行；资源模板只有封闭引用校验，没有消费者。
- `finite_sequence` 的受审 step 是唯一时点权威，最多64个来源组件和256个 step，不使用周期生命周期字段。仅在有限档案 kind、compiler、scope 与来源 profile ID 全部闭合配对时，来源的 `periodic_timer_not_exact` 才可作为非阻断诊断；普通周期和 `explicit_offsets` 仍须精确 timer，不能借有限序列例外放行。

- `application_cadence={kind:"every_n_landed_hits", first_attempt_ordinal, period_hits, scope:"channel_generation", status, source}` 只决定周期通道在本 generation 的哪些**实际命中**发起附着尝试。每个通道、每个 generation 单独计 landed ordinal；漏击不推进位置，gate 跳过的已命中接触仍造成伤害。新 generation 从位置 1 重开。
- 该 cadence 不是 ICD，只能表达有独立正向证据的特殊前置尝试门。普通“若干 hit 或计时后附着”必须由精确 application profile 的共享 `timer_counter` 表达，不能改造成 every-N gate：普通规则是 `reset_seconds=2.5, hit_interval=3, first_hit=1`；特殊规则只在逐组件证据支持时覆盖 `hit_interval`，必要时再覆盖计时。每个 landed 且 attempted 的接触都推进 `(actor,counter_tag,target)` 的共享计数；命中计数满足或计时到期任一条件允许附着，计数触发不移动计时锚点。存在特殊 gate 时，先得到 `application_attempted`，再由 counter/rule 得出 `application_allowed`。替换默认不重置共享 hit counter、timer anchor 或 reset deadline；有限 `[1,0]` 序列也不能自动解释为无限 `timer_counter`。
- 第一次 fresh compile 先读取每个分量的公开 `application_rule_audit`，保存 `base_icd_rule` 与 `base_icd_rule_fingerprint`。需要特殊 X 时，局部适配必须接受该新鲜基线指纹、绑定精确 `component_id`、提交确认了 `icd_rule` 的结构化证据；App 派生稳定的有效 `rule_group`，不覆盖数据库组。同一 counter 且有效计时/计数语义相同的分量继续共享；同一 counter 出现不同 X 或计时时必须停止并核实，不能靠不同适配记录强行拆开。
- 周期次数可以显式给出；或以 `count:null` 配受支持的 `derive_count` 让 App 按首击、数据库 interval 和 inclusive 生命周期终点在整数微秒上严格派生。尾差不能被 interval 整除时必须 pending，不能四舍五入、截断或由 Agent 自算后冒充系统派生。
- `replace_previous` 还必须同时声明 `no_refresh_replacement_or_early_despawn=false`、`no_early_despawn=true`。当前实现只在 compose-time 用已知动作建立点裁掉旧 generation 在替换同刻及以后的计划事件，并在回执记录 generation、replacement pair、cutoff 与 cancelled hits；它尚未把周期事件接到 `StateRuntime.dispatch_hit` 或动态 stale-event 取消，也没有替换终结 hook。
- 若受审机制由轴内另一动作刷新当前周期状态，可使用自描述中的 `periodic_refresh_triggers[]`，不逐 hit 重抄新一轮事件。目标必须二选一：手工档案写 `action_event_profile_id`；由 `action_dispatch_sources` 自动物化的档案写公开 `action_dispatch_ability_id`，不要猜隐藏的 `dispatch.<hash>` profile ID。每条触发只接受 confirmed 的唯一 once 动作及其 confirmed 能力；当前触发种类限 `action_start` 的 `pre/post`，或带显式 post order 的 `action_end`。声明固定为 `operation:"replace_active"`、`snapshot_policy:"preserve_active_generation"`、`status:"confirmed"` 并保留来源依据。
- 外部刷新只在旧实例活跃时替换；未活跃时记录 `inactive_noop`，不会凭刷新声明启动状态。替换原子覆盖 profile 全通道，新 generation 重开命中调度和 `application_cadence`，保留旧 generation 已发生结果、原快照归属与共享 ICD 连续性。compose 后必须检查 `declared_target -> resolved_profile_id`、触发 `(微秒, order)`、取消数、新代首末时点与回执指纹。若来源动作同时是该 profile 的隐式建立动作、触发同位置冲突、新实例越过窗口或生命周期未完整声明 `replace_previous`，保持 pending，不改成逐击手工事件规避。
- `condition_event`、资源阈值和运行期 StateRuntime 动态刷新尚未支持。只有action start/end已在当前有限动作检查点上编译；不要把状态变化、命座被动或资源事件伪装成等待动作来取得确定性结果。
- 周期来源现按精确 slot、数据库 classification、matching slots、ability action type 与原型 damage type 一致性支持已审 `skill` 或 `burst`，不再按角色或 Q 常量分支。真实 E/Q 能否使用仍取决于当前数据库是否能解析完整 candidate 和证据；生产 catalog、真实茜特菈莉/万叶档案与动态染色选择仍须单独审核。

## 资料整理：先发现，保存一次

本接口从同快照的精确参数目标、创建物和能力挂载关系中发现候选。资料相关性不能证明角色释放动作会启动该创建逻辑；`action_creation_association=unproven`、`dispatch_semantics_status=not_proven` 须保留。不要按角色名猜文件、把同名配置合并，或将全部挂载能力当成一次技能的命中。

1. 对实际角色、爆发、技能组及命座查询，不传 origin。例如：

   ```json
   {"action":"periodic_attack_profile","character_id":10000002,"slot":"burst","constellation":0,"talent_level":10,"version_line":"release"}
   ```

2. 阅读 `creation_candidates` 与完整受阻路径，明确选择真实 `origin_reference_id`，带它再次查询攻击通道。它是 legacy 创建引用 ID，不是 definition ID；Gadget ID 由接口同事务取得，不需重复填写。只有一个候选也不能跳过归属和条件核对。
3. 明确选择本次使用的 1–8 个 `candidate_ids`，取得一个完整来源包。两通道共享创建、挂载及参数父资料，不分别反复读取相同原文。保存到该角色事实目录的小文件，机制说明中记录采用通道与未支持分支。来源完整查询可用 `db runtime --input request.json --output facts/character-periodic.json --summary`，不要把原文塞满对话。

接口只投影已受审的局部形状；真实范围由当前查询与编译回执决定。动态 `calibrated_multi_application_v2` 可保留精确命座与1–15级有效技能等级；旧 `calibrated_multi_contact_v1` 仍限C0、有效10级和冻结接触。范围放宽不证明命座分支已激活、完整通道已覆盖或动作会创建该周期实例。命座、变体、精确创建叶或控制字段不符合时，不改实际配置强行通过。C0 参数表同时为命座子分支赋值，不代表该分支已激活。缺失资料、歧义、未知与确无相关来源分别记录；升级提示或超限不是角色没有该机制。

## 冻结前：补齐数据库不能证明的事实

对每种动作保存：实例建立与 inclusive 结束、周期首击、显式次数或受支持的次数派生、独立攻击发生偏移、各自记录精度及来源；持有者和目标存活、无提前中断，以及不替换或 `replace_previous` 的精确适用条件。每个通道独立记录`application_mode=sequence|no_icd|no_application|pending`、可选罕见代内附着 cadence、`counter_tag`、规则组、共享范围、`timer_counter` 的计时与 X、接触类型、默认命中和自身属性采样；同一创建物的多个通道也不能互相套用。未知保持未知；需要外查时按[逐攻击分量核实工作单](../elements/runtime.md#逐攻击分量核实工作单)记录实际检索、来源采用/拒绝及公共资料回填计划。

Agent 不得自行用“持续时间÷间隔”猜次数，不能从配置常数猜生命周期或把 OriginOwner 当快照证明。只有自描述合同明确提供次数派生策略且首击、interval、inclusive 终点与精度均有据时，才让 App 严格派生；否则显式给出有据次数或保持 pending。周期回调与独立启动攻击分别建通道；不能把后者擅自接到前者移除回调。测量精度是来源声明，不是引擎对游戏误差的保证。

同一接触语义可复用已有接触档案，不必为每通道复制。共享 ICD 不按每次动作重新建组，也不因规则表都叫 default 就把所有角色合组。`no_icd`表示有正向证据证明每次符合条件的命中均可尝试附着；`no_application`表示该伤害通道不尝试附着；两者都不能由tag或规则缺失推断。漏击只表达这次接触没有命中，不能代替对象取消或提前销毁。

## 数值阶段：一次来源与一次档案确认

先检查本次自描述是否提供`plan_contract.periodic_source_assembly_contract`。支持时优先使用公共多通道装配，避免分别手写原型、档案、来源映射和能力绑定：

1. 外部Agent按自身权限将请求存为小文件；原生模式使用当前实际工具的request及受控资源/finding，不能假装可写任意路径。按返回schema填写已有能力、档案ID、来源选择、生命周期与逐通道校准；每个实际候选ID只在对应通道声明一次，接触档案须已存在。逐通道明确附着模式；只有`sequence`填写`counter_tag`，`no_icd`与`no_application`须提供逐分量正向证据，`pending`保留核实工作单。不要复制查询中的倍率、元素、间隔、规则序列或原文。
2. 使用`equipment assemble-periodic --plan frozen.plan.json --request periodic.request.json --output assembled.plan.json`。新文件是原生草稿，stdout是短摘要；需要完整预览时再compose并落文件。输入动作检查点保持不变，不先创建伤害原型或猜测技术ID。
3. 将返回的`assembly.channels`映射留在本地，仅在需要本轴ordinal例外或match时读取。它提供候选对应的通道/原型/附着槽身份；不按数组位置或中文名猜ID。候选重排不改变对应关系，同档案增减候选或修改既有内容会报冲突，不能借重试覆盖用户编辑。
4. 检查`source_summary`和完整周期/动作回执。全部请求通道已装配不代表完整技能已覆盖；记录未选候选，遗漏信息尚未取得时保持未知。origin可null形成待补草稿，但不会自动选择首项或派发生效事件；本入口需要每个候选ID明确非空。
5. 新档案仍未接受，按下文核对资料及校准后一次综合确认。装配不增加动作、资源来源、取样标记或Buff规则；改轴或补事实按用户授权修改相应工件并重验依赖，不要求focus回退，不在结构录入时偷改时间、次数或取样模式。

旧程序缺少装配契约，或需要修改已有档案时，使用现有周期编辑/原生字段路径。下面说明这些字段的归属，支持新装配入口时不再重复手写四套关系：

- 冻结参考模型使用 `calibrated_multi_contact_v1`；动态元素模型使用 `calibrated_multi_application_v2`。二者都保存生命周期及各攻击通道，周期调度提供首击与显式次数，或在自描述支持时提供严格次数派生；间隔由所选数据库候选提供。动态 v2 可为周期通道附一次紧凑 `application_cadence`，逐通道直接派生 application 且禁止冻结 contact。独立启动通道只提供单次偏移。任何版本都不支持手填 interval 作为来源失败的后备值。
- 每档案一条 `periodic_attack_sources`，包含精确选择、origin，以及逐通道的candidate、`application_mode`、`counter_tag`、结构化证据和可选`research_report`。完成外查后把报告写入`periodic_attack_sources[].channels[].research_report`，使用`report_version=1`和`execution_authority=none`；报告不会自行解除pending。`counter_tag`只表示共享计数身份，规则组由所选数据库候选的`attenuationGroup`只读编译；旧`shared_group`仅作兼容输入。原生可用 null 保存尚待选择的 origin/candidate；HTTP 请求省略未知项，不发送 null。
- 每通道唯一拥有一个已有伤害原型和附着输出 ID。原型保留角色、体系、单 ATK、count=1 及明确伤害类别；当前工作源码可接受与精确来源一致的 `damage.elemental_skill` 或 `damage.elemental_burst`，旧发行仍按其自描述范围。原型省略由来源提供的 element/multiplier，附着档案整项不写原生；不再额外写同原型的 `hit_parameter_sources` 或同输出的 `elemental_sources`。
- `action_event_bindings` 将能力连接到档案，按轴中每个实际 once 动作展开。需要多少轮就输入多少秒的完整动作；不使用自动轮次代替动作输入。后台命中可在动作占用结束后发生；提前切人、未知取消或未声明替换的同档案实例重叠仍使来源 pending。只有完整、已核实的 `replace_previous` 合同才允许 App 计划取消旧代未来事件；它不等于运行期任意刷新已经受支持。
- 仅本轴漏击或同刻顺序例外写入带该 kind 的 `action_event_overrides`，逐项指定 action/channel/ordinal 与 landed/order、状态和依据。不能在这里改时间、次数或倍率，也不自行给所有同刻事件加顺序掩盖因果缺口。

首次 `accepted_profile_fingerprint=null`。compose 返回 `periodic_attack_compilations` 与 `action_event_compilations`：逐通道核对完整原文、原始分支、数值投影、附着模式/计数身份、cadence、次数派生、replacement、单独校准和未支持范围后，接受当前档案指纹再 compose。每个pending或冲突通道必须返回具体身份、已知/缺失字段、本地检查、定向检索问题、机器可读研究报告模板与数据库回填目标，不能等到origin、候选或用途全部ready后才给工单。只需一次综合接受，不逐通道抄内部参数或附着 SHA；接受并不提升 pending 事实状态，也不需要新增用户审批步骤。

任一来源或实际动作不满足条件时，整个相关档案不交付部分可执行事件。若根因要求改动作或机制解释，先回相应阶段生成新的动作检查点或事件拓扑修订，不能在录入阶段偷改。只采用部分通道时明确列出未覆盖攻击，不称完整技能总伤。

## 检查与交付

核对每个实际动作、generation、通道与接触序号的映射；普通/特殊每-X规则检查 `application_attempted=true` 的每个 landed 接触是否都推进共享计数，并核对 `hit_interval`、计时锚点、`application_allowed` 与长序列。仅对有独立 gate 证据的通道再检查 `application_cadence_position` 和 `application_attempted`。确认漏击与明确未尝试均不推进 ICD、gate 跳过不删除伤害、新 generation 只重开自己的发生节律而共享 ICD 按资料连续；有替换时逐项核对 replacement pair、cutoff、cancelled hits 和旧代没有双发。后续轮次的绝对时点不得越出完整窗口，不要只检查第一轮。改通道路由 ID 会改变派生命中身份，原有 match 微调须重新检查，不能直接沿用孤儿引用。

原生只保存声明与选择，不复制数据库 query、编译后的附着档案、自动倍率或 generated patterns。结果与两类 Excel 查看“周期攻击来源”“动作派发来源”和 `result_receipt.periodic_attacks`；Excel 不重演回调与元素状态机，修改来源、次数、时点、共享关系或生命周期后回 App 重新生成。

本路径不自动生成产球，也不自动补全未被当前来源解析的战技、爆发或天赋父链；动态染色选择、任意运行期刷新和终结 hook 仍不在本片。装备、天赋、能量与其他动作仍按[完整性核对](../../workflow/coverage-and-review.md)逐项检查。局部 query ready、档案被接受或 App/Excel 一致，都不等于真实游戏机制完整验证。
