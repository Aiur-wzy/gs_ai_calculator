# 星月替换、能力直伤与来源资格

本页只说明App表达，游戏机制和公式分别见[反应](../../mechanics/reactions.md)、[伤害](../../mechanics/damage-formula.md)。不把游戏机制、当前实现范围与数据来源资格混为一体。

## 先读取实际合同

App 原生从 `capability_index` 的 `/special_reactions` 取真实资源及指针，连接当前 App 的 `agent candidate read` 使用同一组路径。`special_reaction_capabilities` 可定向查看 `/input_contract/schemas`、`/qualification_schema`、`/engine_catalog/profiles` 和实际处理器目录。查看静态 schema 后，执行仍需当前模型、绑定与来源资格。

外部 CLI 从 `capabilities` 的 `data.special_reaction_contract` 读取同一份完整合同，不用数据库 HTTP 查询端点代替 App 引擎合同。技术哈希可使用 `engine hash-input --input request.json`，请求为 `{"kind":"direct_stellar_damage_profile","value":完整profile对象}`（月直伤选择对应 kind），与原生 `gs_hash_input` 复用同一纯函数；输入 schema 见 `data.input_hash_contract`。此命令不批准事实、不注册能力、不修改候选。目录内已有的替换机制哈希直接取目录，不用能力 profile 的算法重新生成。

反应替换模型与能力直伤profile是不同合同。当前模型v2的`handler_bindings`必须从当次注册表复制精确family、版本、能力与指纹，不按名称拼哈希。模型v1是仍有消费者的兼容声明，不等于旧能力直伤profile v1仍可执行。

## 声明场景和替换

先建元素初态和真实附着。当前原生输入含 `special_reaction_model` 时，按合同在 `mechanics_model.elemental_model.ruleset` 采用 `gauge-multi-aura-v4`。遇到 `special_reaction_ruleset` 时，沿[元素接口](runtime.md)补真实依赖；用户要求动态结算时继续完成动态方案。按实际队伍分别启用所需替换，填写真实资格提供者、精确 `native_reaction_qualification.<type>` 和来源。反应资格提供者与伤害贡献者分别核对。

用稳定 `area_id`、`parent_event_id`、动作、目标和时点连接每次附着。保留同一父攻击的真实子事件和附着档案。引擎由这些输入计算反应候选、替换和最终状态。结果中核实际反应是否发生以及附着和状态怎样变化。待核参数按实际范围保留。

当前App主路径以单目标为主；多目标世界实体是否真正接通从实际capability/执行报告核对。知道Gadget身份或接口类型不代表选敌、弹道、遮挡、范围消费、跨目标额度均已还原；未知与已执行范围分开。

## 直接能力伤害

使用`direct_stellar_damage`或`direct_lunar_damage`专用事件；实际支持的reaction types从schema和`engine_catalog/damage`读取。1.0.2 的星烁能力直伤支持 `stellar_superconduct`（冰／雷）与 `stellar_swirl`（冰／风）。星超导取该区域已生效领域增益；能力星扩散使用能力自己的倍率，不读取自然风旋等级、不生成风旋。反应型星扩散仍由其状态机产生，两类分量不能互相代填。

常规极星辉域每 4 秒结算一次。仅当方案要求模拟稳定的 384 快帧时，在星超导 mechanics 同时指定 status=assumed、settlement_clock=experimental_384f、settlement_interval_seconds=3.340596 和 override_reason。保留实测来源或估算依据。这个选项只改变领域结算和对应层数增益的到期时间，领域刷新保留已经排好的下一次结算。技能冷却、信标和角色动画分别建模。

领域增强通过替换模型的 `mechanics` 输入：星超导 `initial_grant_layers` 只赋予新建领域到首次结算前的增益，不增加附着计数；`settlement_bonus_layers` 在窗口结算时额外增加增益层数，合计封顶12。刷新既有领域不再次发初始增益。星扩散 `initial_vortex_count` 和 `initial_upgrade_cooldown_seconds` 控制新风旋初始升级及全队共用冷却，后续正常反应继续计数。省略这些可选项保持旧行为。实际数值须查明来源；与受审目录不一致时明确采用有据的 assumed profile，不能只填 confirmed。

星扩散反应基础系数的显式修正使用 `replacements.stellar_swirl.mechanics.base_factor_adjustment`，保留 `assumed`、`override_reason` 和精确来源。修正对象含 `provider_id`（实际队伍角色 ID）、`source`（文案或记录出处）、`components`（本次适用分项）与 `multiplier`（相对原系数的倍数）。分项可选 `stellar_swirl_anemo`（自然风伤，原系数 0.75）、`stellar_swirl_cryo_lv2`（一级风旋冰伤，原系数 2）、`stellar_swirl_cryo_lv3`（二级风旋冰伤，原系数 3）和 `stellar_swirl`（能力直伤系数，原系数 1）。每个分项只列一次。系数先乘倍数，再按原伤害公式执行，独立的反应加算仍在后面。风旋升级和冷却分别填写。能力直伤可单独选择，保留能力倍率与独立取样规则。

安娜丝塔夏「未挽的悼亡」7.2.52 文案给出星扩散基础反应系数提升 10%。强度研究院的安娜示例在其 V3 模型使用 `multiplier=1.10`，自然风、冰和能力直伤均有相应方案。采用其范围进行试算时明确记作模拟器参考，实机分项与精度待核。伤害详情按执行回执显示原值、提供者、来源与生效值。较早版本文案为 15%，以题目实际采用的版本为准。[版本对比](https://gi.gachabase.net/diff/characters/10000149/anastasya?cur=beta_7.2.52_49468064&lang=chs&prev=beta_7.2.51_49319249) · [计算工作室](https://cs.gensri.wiki/anastasya/)

能力直伤先核对该组件的转换资格。使用 v2 `ability_binding` 绑定实际角色、能力、组件、变体、有效等级、资料线和数据库指纹，并使用专用 profile 内容哈希。填写能力倍率、结算属性、次数、元素和关联领域。出伤时核该领域的真实状态与最近一次完成的结算，运行时由此取层数与基础系数。

绑定两端都要有值：沿事件的父动作找到 `rotation_model.abilities[]` 中实际使用的能力，将该能力的 `character/id/variant/effective_level` 与 profile 的 `ability_binding.character_id/ability_id/variant_id/effective_level`逐项对照。只填 profile、遗漏动作能力的变体或有效等级，仍会被拒；普通动作允许省略不代表专用直伤也能省略。`component_id`是分量身份，不能用它代替父能力身份。出现 `direct_special_ability_binding_mismatch` 时先看回执中两侧实际值，再修改正确的一端；不能只重算哈希或随意把等级改成1。作者辅助可生成普通编辑草稿，但不会猜真实变体与等级，技术绑定刷新也不替代这项事实核对。

- `confirmed`要求完整profile精确命中受审能力目录。实时`reviewed_profile_count`见`engine_catalog/damage`对应直伤子树；目录文件名是`direct-special-damage-profiles.json`，条目数不写死进机制页。
- 未命中时只能按有据的`assumed + authority=explicit_assumption`路径声明，并完整披露来源/未知。其`database_fingerprint`仅记录资料来源，不证明DB登记、独立审核、队伍资格或完整App接纳。必要字段缺失仍须补齐。
- 旧直伤profile v1只作迁移资料，不投影伤害；不得自行把假设改为confirmed、伪造目录条目或改指纹掩盖旧来源。
- 换用新版数据库时，不能只改 `commit` 标签。重新查询该组件及相关角色资格，对照新版的变体、倍率和来源指纹后再更新绑定。

能力直伤不附着、不修改元素底，也不进入多人反应贡献权重。同一动作组件、目标、区域和时点只交付一次。`count`只代表数值次数；若每段独立附着、消耗或触发，拆为独立事件。伤害方式转化时替换同一原组件，不能保留普通伤害后再追加专用直伤。

## 月绽放草露、转换核及消费收益

先从[角色附录正文](../../data/character-appendices.md)核实本场景的取得条件、有效范围、持续时间和倍率。原生读 `special_reaction_capabilities` 的 `/input_contract/lunar_bloom_reserves` 和 `/input_contract/schemas`，CLI 读 `capabilities --section special_reaction_contract --pointer /input_contract`。转换核及扣露条件的作者字段见 `mechanics_capabilities` 下 `/native_authoring/schema/properties/elemental_state_events` 与 `/native_authoring/schema/properties/condition_events`。

- 替代草露通过 `special_reaction_events` 的 `lunar_bloom_reserve_grant` 声明真实来源动作、时点、`pool_id`、数量、容量和有效期。同池再次发放增加余额至容量并刷新有效期。按实际规则确定发放机会。
- 同一数组的 `lunar_bloom_consume` 写消费上限 `dew` 和按序使用的 `reserve_pool_ids`。先扣普通草露，再扣所列池。默认余额不足时整笔拒绝，技能允许消费部分时使用 `spend_policy="up_to_available"`。在 `special_reaction_trace` 检查每池扣量、剩余余额、到期与实际消费是否一致。
- 月绽放能力直伤可用 `dew_consumption={event_id,mode}` 绑定同动作、目标、区域中先执行的消费。`require` 只要求消费成功，`scale_multiplier` 将单位倍率乘实际扣量。档位增益或持续效果可用 `condition_events` 中的 `dew_consumption={event_id,amount}` 取代 `value`，仅在实际扣量恰等于指定数时触发既有机制规则。技能所需的档位和增益仍按证据声明。
- 草核通过 `elemental_state_events` 的 `bloom_core_conversion_open` 声明动作、提供者、目标、区域、转换窗口、替换核寿命及是否转换已有核。窗口内生成的核走替换流程，转换后的核退出原自然爆炸和上限挤爆。窗口外的新核按普通规则生成，仍存活的替换核按自身寿命消失。
- `bloom_core_absorb` 在同一提供者、目标和区域按 `maximum` 吸收实际存活的替换核。若已核实当前动作的月绽放直伤按实际吸收数线性提高倍率，可用 `bloom_core_absorption={event_id,multiplier_bonus_per_core}` 引用同动作、目标、区域的先前吸收，倍率乘以 `1 + 实际吸收数 × 每核系数`。跨动作保留的层数与其他换算需要按各自来源另行材料化。

在 `mechanics_trace` 对照核的转换、吸收和过期，在 `resolved_template.hits[].extensions.elemental` 核对关联直伤的实际扣量或吸收数。一次来源只交付一次。当前草露操作按显式时点单次执行，多轮轴逐次声明。重施时保留累计的接口见[资源与生命周期](../execution/resources-and-lifecycle.md)。

## 贡献者、顺序与完整性

### 定时资源接口（1.0.3）

先查询当前 App 的 `special_reaction_contract/input_contract/stellar_periodic_resources` 和 schema，旧版不一定具备此接口。开启星超导后，可在 `mechanics.periodic_resource` 声明所有者、区域、生成间隔、共同寿命、容量、普通/延长生成窗口和保持节奏的刷新方式。数值及模式切换条件仍由资料证明，不能仅凭字段存在认定某角色全套机制已支持。

`special_reaction_events` 中的 `stellar_resource_operation` 绑定实际来源动作。`grant/consume` 分别获得和消费；`set_mode/end_mode` 管理模式；`observe` 读取库存，或以独立 `claim_id` 领取累计生成/消费的新增计数，`divisor` 用于累计阈值。库存、生成次数、真实消费、到期消失不可混用。普通消费不足会拒绝并回滚。

`trial` 声明 `cost/base_grant/success_grant/success_waives_cost/chance`。不填写`outcome`时默认按期望次数计算，保留小数库存和消费，不枚举、不掷骰。用户需要特定情景时，由Agent询问确认后使用`probability_mode=custom`并提供布尔`outcome`；旧方案已有`outcome`继续沿用指定情景。返还仍记消费，免消耗不记消费；都先检查完整所需库存，不能借未来返还补足本次成本。查看`stellar_resource_receipt.trial`中的计算口径、符合条件次数、期望次数或指定次数及执行记录，不汇报不存在的实战成功概率。

`condition_events.stellar_resource={event_id,amount,measure}` 根据先前真实回执判断。`measure` 默认 `amount`，trial 可分别读 `executed_units/consumed_units/created_units`。例如免消费仍可执行，但不能触发按实际消费累计的收益。`direct_stellar_damage.resource_consumption` 仅绑定真实消费，`scale_multiplier` 将单位倍率乘实际消费量；不把免消费的零扣量当成伤害必为零的游戏规则，须按实际能力另行绑定合法的执行条件。

创建、消费等变化可用普通规则的 `trigger.event=resource_change, phase=post` 监听。必须明确 `resource_operations`；可用 `resource_modes` 限定模式。每一枚实际资源对应一次触发机会，Buff层数与回能沿用规则的持续时间、叠层和共享冷却；回能仍须声明 `resource_ownership`，使用实际触发的 `source_ref`。不要额外手填一组固定时点的同来源回能。

“每消费N个”的规则使用 `resource_every={counter:"consumed",amount:N,scope:"matching"}`，只累计满足该规则其他筛选（例如炉心有效模式）的实际消费。若确实要读取资源账全程累计，使用 `scope:"global"`（默认）。返还产生的新资源不撤销刚发生的消费，免消耗则不增加消费计数。这里描述执行方法，不替角色原文决定累计是否跨模式保留。

资源触发的额外星烁能力伤害使用规则的 `resource_damage` 列表，各项填写 `id/action_id/target_id/area_id/ability_profile`。来源动作必须已经实际执行；同一规则各组件绑定同一次来源动作，并使用 `timing.delay=0`。伤害时刻来自实际资源变化，不能另填同动作组件的手工伤害。倍率、等级、角色资格、领域层数和当时属性仍走既有直伤校验与公式；同一时刻跨过多个阈值会分别记录。结果命中的 `extensions.elemental.resource_trigger` 可追溯对应的资源变化和序号。

结果中的资源库存/模式时间段由执行记录生成，和领域累计/结算层数分开；它们用于核对过程，不自动证明对应Buff或额外伤害已录入。上面的收益都需要在方案中明确声明，数据库原文不自动执行。期望数量经过容量、阈值和冷却的处理属于近似口径，不是完整随机机制模拟。共享池v2的期望次数与指定情景见[资源、多跳与生命周期](../execution/resources-and-lifecycle.md)；完整角色支持仍以当前能力回执为准。

星扩散按游戏规则保存`anemo_trigger`、`cryo_applier`等角色槽provenance，不只有无类别`contributor_ids`就退回全局排序。缺槽/归属保持pending。月感电按元素/owner保存实际残量与最近合格施加；查询真实owner选择与扣量流水，不用当前前台补所有贡献者。

伤害后附着影响后继，不追溯把父伤害标为融化。读取proposal、replacement decision、commit/rollback、候选rank/skip_reason、元素量、状态机事件与实际伤害；普通结晶晶片、扩散及星月反应不得手工重复交付。

审查某次命中的目标状态时，核对普通 `elemental.trace` 与 `special_reactions.trace` 各自覆盖的状态/附着事件，包含特殊反应的后置附着，并遵循真实事件顺序与自然衰减；只看普通流水或UI合并标签可能漏项。读取、合并视图不能修改原结果。当前自动目标条件未覆盖的规则可在必要时按真实本轮流水材料化为有来源的精确match；fresh重算后须验证相关元素过程和条件仍然一致，不把一次旧结果的状态标签无条件沿用到改过的轴。

`duplicate_reaction`提示检查事件所有权和普通hit预填反应；不要为绕过它将特殊直伤写进普通hit。预期反应只作断言，真实不符时修输入/来源或必要的受控覆盖，再fresh重算，不直接改结果。

## 数据匹配、历史与研究

资格来自当前已接纳数据库随包提供的目录，旧版数据库可以使用对应的兼容目录。读取当前队伍投影和逐角色的原因。换库后若方案仍引用旧资料，用资料更新入口刷新装备来源，再组合核对实际形态和提供者。若新库已把特定角色的资格列为待核，回执会说明待核依据。能力直伤采用假设 profile 时也需要通过角色资格检查。

旧static_layer_bridge及旧benchmark保留历史，不推荐为当前原生执行路径；没有实际重跑不能声称已迁移。相关旧数值/元素参考范围见[兼容入口](compatibility.md)。

资料不足时针对GU、ICD、资格事实、范围或贡献者定向研究，登记查询项、版本/链接、采用与未采用结论及待补字段。来源未知、系统失败、算法不支持分别报告，不因此自动删除影响结果的分量。

满辉中非月兆角色E/Q提供的20秒同项增伤，应材料化为同一个逻辑效果实例的刷新／替换并更新实际提供者，不能每次新建相加的独立规则。按[属性取样](../execution/attributes-and-sampling.md)表达有据的TOTAL／NONEXTRA及动态／快照；精确取样未核实时明确假设。技能原文确定20秒和不叠加，不自动批准其所有隐藏属性分类。此收益与专属天赋、命座、装备和共鸣逐项分开，按各自乘区与实际目标条件消费。

## 有限额外多人雷击诊断

原生及连接 App 的 CLI 读 `special_reaction_capabilities` 的 `/extra_damage_contract`，也可从 `mechanics_capabilities` 读 `/special_damage_echoes`。独立 CLI 的完整响应在 `data.special_reaction_contract.extra_damage_contract`。按完整 schema 填 `/mechanics_model/special_damage_echoes`，保留真实多人反应父事件和实际贡献者。

当前仅支持明确一次已触发的有限分支：用真实自然 `lunar_electro/thundercloud` 的 `area_id/target_id/t` 精确关联父雷击；不猜私有事件ID，不预填贡献者、层数、倍率或伤害。来源必须指向同 owner、同规范 source_id 的现有 `source_rule_id`，经当前完整 `manual_context` 审核且在父时点真实激活。一个实际父包的同一提供者及来源分项最多一次，别名不增加支付次数；无父或多父匹配、来源未激活／过期保持 pending。

必须写明 `status=assumed`、`precision.kind=approximate`、来源、方法和未知／排除范围，并复制 schema 的全部 `semantics` 常量。声明的 probability 只记录所选分支的概率，不由引擎随机、不乘概率生成小数次。引擎在父包全部结束后，以其实际贡献者和当时面板重算同一多人公式，独立排列暴击结果；正常伤害收益及合法次数／共享池消费者仍生效。

此首期子集明确不产生新的 GU 消耗、附着、雷云相位变更、递归或前后置触发／资源回调。上述未知须先核实；必要时只能把本分支作为明确排除这些状态反馈的近似诊断，不宣称完整角色天赋已还原。零触发主分支无需建立该字段。有状态反馈不可用时报告具体缺项，不偷偷改自然雷击或以无回调为由跳过正常伤害消费。复核结果中的 `special_damage_echoes`、实际父与独立多人权重，并 fresh 保存回读；不编辑旧结果数值。

无游戏回调不等于没有属性取样阶段：每个真实额外贡献分量的普通属性阶段、出伤投影及后置属性上下文均由引擎建立，Agent不手填私有属性帧。若回执在内部`registration.frame`报告缺字段，应保留输入和失败时点报告软件问题，不能为了通过而在方案中伪造`frame_kind`或私有投影引用。

## 概率次数的默认取整口径（1.0.3）

默认计算符合条件次数×概率。可线性折算的资源数量、回能和伤害次数保留小数；额外动作和Buff等离散收益，默认对累计期望次数向下取整，不要对每次0.34分别取整成0。Agent可以向用户确认其他指定次数或具体安排；引擎不搜索情景、不枚举随机路径。

资源变化直接触发Buff时，共享规则按累计期望触发数取整，记录`expected_discrete_count`的期望数、已应用次数和本次新增次数。原有条件、CD、叠层上限和持续时间继续执行。动作仍需Agent按合法时点写入排轴，引擎不自行生成额外动作。公开结果需说明这是期望次数的确定性近似，不能保证每次实战循环。

定时资源变体在触发反应时仅安排生成窗口，实际资源创建时才建立或刷新领域；不同时生成普通棱晶。能力目录要求该变体时必须声明对应所有者的`periodic_resource`，不能漏填后静默使用普通领域路径。`trial`一次代表一次概率机会：若原文写每消费一枚判定一次，应逐枚提交，不能把多枚消费批次误写成只判定一次。米提亚的已登记能力为星超导；具体天赋与命座参数仍从当前数据库读取并由Agent编译为通用规则，登记不代表已自动填入全部收益。
