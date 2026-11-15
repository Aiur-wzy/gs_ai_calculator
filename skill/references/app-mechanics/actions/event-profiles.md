# 共用动作档案与自动命中装配

本页描述 `calibrated_single_contact_v1` 的受限单次战技路径。若当前接口另提供 `periodic_attack_contract`，周期/独立启动的多通道爆发按[周期攻击来源](periodic.md)处理，共用动作绑定和综合确认，但不沿用本页 E-only 校准边界。

运行时 `profile_kinds` 另提供 `calibrated_single_application_v2` 时，可为明确的 `gauge-swirl-dynamic-v3` 选择单次伤害与附着档案。动态 v2 接受精确命座、1–15级有效天赋及唯一的普攻/战技/爆发槽，仍限ATK单倍率、真实动作占用和有据校准；它不带 `contact_profile_id`，连null也不可填写，执行模型也不提供冻结接触字段。其回执为 `calibrated-action-events-v2`。旧 `calibrated_single_contact_v1` 的C0/10级/E槽范围保持不变，不能把旧指纹、范围或接触证明搬到v2。当前整份元素模型只有一个ruleset，旧冻结档案与新v3档案混合会待完善；旧产球和限次来源也不能据此推定已支持新父档案。

只在当前 `plan_contract.action_event_contract` 提供此能力时使用，先完成分实体资料、动作检查点和事件拓扑。此功能把一份已核对的命中原型用于轴上每次明确释放，省去逐动作编写 `hit_patterns`；不会自行选择动作、补多轮或完整解释角色技能。

## 先确定适用范围

首版 `calibrated_single_contact_v1` 仅支持 C0、有效天赋10级、元素战技的明确变体、classic单ATK倍率、单目标单次接触。时间是有依据的校准或明确假设，不能把上游modifier时长直接当作按键到出伤的时刻。当前要求冻结参考规则集、明确安全接触，无攻速变化、卡肉、提前取消或形态改变；发射必须在动作占用内且严格早于输入窗口结束。不适用时保留原有明确机制表达，不改命座、技能语义或动作来强行使用自动入口。

数据库的精确倍率来源与动作校准是不同依据。`dispatch_mode=calibrated`、`skill_coverage=partial`、`resource_coverage=not_generated` 必须如实报告；倍率ready不证明派发时间、变体、附着或完整循环正确。产球、其他伤害、天赋和装备触发继续逐来源核对。

动作档案自身不产球。若另提供`energy_generation_contract`，在父档案接受后按[产球来源](../sources/energy-generation.md)关联，省去同源资源规则和逐动作输入；其他未覆盖来源继续独立记录，不能从父档案ready推定全部机制已执行。

## 一次定义，共用到明确动作

同时提供`attack_metadata_contract`时，优先按[精确攻击来源](../sources/attack-metadata.md)统一取得元信息，替代下面第1、2步中手工倍率来源和application来源的重复定义。原型省略element/multiplier，application只保留输出槽位ID，contact和校准仍明确。两种来源方式不能重复接管同一原型或附着档案；后面的动作绑定、例外与综合接受流程共用。

1. 沿既有 [倍率来源](../sources/hit-parameters.md) 保存一份 `manual_template.hits` 原型和唯一 `hit_parameter_sources`。原型t=0，仅作模板；省略multiplier、reaction和旧snapshot/cast_t，count省略或1，声明ATK属性及明确伤害分类。身份取自该来源，不重复抄写角色和等级。需要额外倍率、HP加算或多段时不能硬塞进单命中档案。
2. 沿 [元素运行时](../elements/runtime.md) 定义一次application和contact档案。GU、共享ICD关系须有依据；数据库可查的序列仍通过elemental_sources交付。不同释放不能更换group来重置ICD。接触unknown不能默认安全。
3. 在 `action_event_profiles` 引用原型、application/contact，声明变体、非负微秒偏移（零偏移仍须满足因果order）、证据精度、校准status/source、default_landed及self_sampling。`default_landed`不能默认为true；不明确时使用null并补事实。`action_start`快照是动作pre收益之后、post收益之前的现有相位，不能解释成全部施放收益已生效。
4. `action_event_bindings` 只需把能力绑定到档案。引擎对该能力的每个实际once动作生成一个命中。需要三轮就显式给出三轮全部动作及时间；不使用each_cycle代替输入。
5. 个别动作落空或需要同刻排序时，在 `action_event_overrides` 写action_id及landed/order、独立status/source。这些是本轴例外，不修改公共档案或在数值输入中偷偷改动作检查点。相同时间存在事件时必须检查因果次序，不靠自动加1隐藏冲突。

首次 `accepted_profile_fingerprint=null`。compose返回 `action_event_compilations` 和 `required_confirmation`；先核对真实来源、完整校准语义与依赖，再接受当前档案指纹并重新compose。来源确认不提升pending状态，也不需要新增用户审批。相同语义可跨轴复用；数据库源、应用/接触内容、GU、用途或校准变化须复核，不能机械刷新指纹。

若运行schema另提供`self_sampling="action_capture"`，按[动作面板取样](../execution/action-captures.md)为每个实际动作声明唯一取样，档案自动关联。缺少取样或先后关系不成立时保持待完善，不能退回`current`以取得结果。旧`current/action_start`含义不变；不能把校准取样当作上游原生事件已经自动推断。

## 防重复与检查

绑定动作的命中交付权由档案负责，不再为同一动作写手工hit_event、pattern或application_event。原型不能独立出伤，也不能被旧Buff的hit_ids直接指向；普通收益优先按伤害类型声明，必要例外仅引用本次组合后已进入`resolved_template.hits`的实际命中ID，并按微调契约留痕。删除最后一个动作后，无用档案/绑定应明确整理，不能留下会独立出伤的原型。

`applies_to.hit_ids`是组合期静态外键，不接收运行时才产生的星／月直伤ID，不能把私有生成ID填进同样做静态外键检查的`filter.event_ids`。若caps提供 `runtime_damage_match`，可用原生微调 `mode=runtime_adjust` 的 `runtime_events[{event_ref,operation}]`：event_ref采用 `runtime-damage-match-v1`，给角色、damage_system、origin和整数微秒，再引用实际来源动作或公开来源事件，必要时限定反应/元素/目标。引擎在真实命中消费前解析，结束时须唯一；零/多个候选按诊断补正，不选第一条。它仍受来源审核、当前实例和配额/gate约束，不能返还消费或批准未知目标条件。检查runtime_match_resolution和实际matched_effect_refs，改轴重算。

每个实例有稳定的派生命中ID，移动动作只改变其派生时点；中文命名不携带程序前缀。检查记录中的matched_actions、generated_hits和实际result.hits，区分计划派发、声明落空与门槛中断后的未验证事件。未通过冷却/能量检查时，不把完整计划派发数当作已执行伤害。

保存原生档案、绑定、例外和共享来源，不复制report里的生成patterns或倍率回原生。缓存resolution不是事实来源。按“动作派发来源”页和 `result_receipt.action_events` 核对输入、来源与执行模型；App和Excel应来自同次计算。工作簿不重新运行派发或元素状态机，改校准、动作、绑定或来源后回App重新生成。
