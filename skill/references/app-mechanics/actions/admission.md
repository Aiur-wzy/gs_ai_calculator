# 动作基础资源与接纳来源

当前 capabilities 提供 `action_admission_source_contract` 时优先使用 v2 路径；只有旧客户端或不在 v2 首片范围内、且实际仍提供旧合同时，才使用后文 `action_resource_sources` 路径。两者不能共同拥有同一能力。

## action_admission v2

v2 把受审静态基础资源与一次实际动作是否通过门禁分开：目录和静态证书只允许编译门禁字段，只有运行时 `action-admission-execution-v1` 的 `committed` 回执才证明该动作已经通过前台、占用、能量、冷却与次数检查并原子提交。它不证明命中、附着、产球、伤害或完整游戏时序。

### 当前首片边界

- 只支持 `release`、C0、`slot=burst`、`variant=default`、`action_type_id=action.elemental_burst`、单能力、单次数和正值固定耗能 Q。
- 不支持 E、切人、多次数、共享能力别名、动态冷却修改或与旧容量来源组合；不能通过改角色身份、删除规则或手填相同数值绕过限制。
- v2 独占该能力的 `cooldown.group/duration/start/recovery/charges` 与 `energy.required/amount/delivery`。原生能力仍明确声明 `variant` 与 `action_type_id` 并接受交叉核对，但 `cooldown`、`energy` 必须留空；同一动作不再保留手工 `energy_spend`。

### 目录、场景与确认

1. 核对所选能力及每次实际动作，目录或能量待填不阻止保存动作。需要来源时即可读取`view=all`的完整稳定分页并按2A分组，不要求进入指定focus：已审核完全匹配、已审核部分匹配优先可选；pending、revoked、stale、conflict和not-evaluated分开保留准确数量、原始ID、revision与原因。目录不返回可执行资源值，也没有执行权。
2. 白板 v3 的 `action_admission_intents[].context` 与 `initial_ready_in` 默认都是 `null`，可以保存和交接，但不能形成完整 calculated result。只有用户在界面显式选择普通开放世界预设时，才同时写入固定场景

   `{"contract":"static-action-admission-context-v1","mode":"ordinary_open_world","external_cooldown_effects":"none","external_energy_cost_effects":"none"}`

   和 `initial_ready_in=[]`。`[]` 明确表示全部就绪；缺失队列不能解释成全部就绪，也不能由 Agent 静默选择预设。
3. 保存精确 `model_ref` 与 selection，先 fresh preview，核对技能原文、基础 CD/次数/耗能、受审本地语义、未知边界、动作集合、场景和初始队列，再复制本次 `accepted_fingerprint`。应用前重新 fresh；资料、current head、方案或指纹变化均须重新审核。
4. 原生方案只保存来源引用和确认，不保存编译器私有 `action_admission_source`。保存重开后再次 fresh preview；白板 handoff 传播 intent 和未确认 proposal，不传播 accepted fingerprint 或私有 marker。

### 计算与成功动作资格

计算后分别检查 `action_admission_compilations` 的静态证书与 `rotation_check.action_admission_execution` 的运行时账本。每个受管父动作必须是 `committed/rejected/not_committed/not_verified` 之一；只有 committed 有 certificate reference。`rejected` 表示实际门禁失败，`not_committed` 表示门禁通过但审计预算或受控基础设施在提交前失败，`not_verified` 表示前序失败后没有继续验证。任何失败都不能留下半扣能、半入冷却或伪成功 observer。

需要“成功施放后触发”的资料时显式选择资格版本。`successful_action_v2` 必须同时要求 `cast_identity` 与 `action_admission`，只消费同一已接纳 `action_start` 的 certificate reference；`successful_action_v1` 保持原义，不自动升级。角色只作为受审数据行的精确身份，算法不得写角色 ID 分支。

工作流仍是 App first：先让 App 自动装配、门禁和计算，再按[计算补正](../review/corrections.md)审查命中、来源接收、Buff、反应、能量及未覆盖链。首次 ready 或 committed 不是终点；Agent 可以在保留动作稿的前提下多轮补正原生表达、重算和复核。

当前指纹、current-head 证明和离线校验只建立本地包内的内部一致性，能发现局部改写或引用漂移，不能识别原生方案、计算包与证明被一致地整包替换。问题 1 的外部信任根已延期，不得把本地审核描述为外部权威。

## 旧 action_resource_sources 路径

先查当前 `action_resource_source_contract`。原生方案表示下从 `capabilities` 的 `/action_resource_source_contract` 读取，独立 CLI 完整响应中在 `data.plan_contract` 下。原生 v3 的 `action_resource_sources` 会把数据库基础冷却、默认基础次数和固定耗能填入执行副本，保存的原生方案仍保留来源。按当前 schema 填写。

这里的“默认基础次数”不能代替装备、命座及场景共同限定的**最终最大次数**。当 capabilities 另提供容量目录和 `action_capacity_sources` 时，最大次数按[受审技能最大次数来源](capacity.md)处理：容量来源独占 `cooldown.charges`，动作资源来源不能再拥有同一能力；基础冷却秒数、起点、恢复方式、固定耗能和开场恢复队列仍须分别核对。当前两个合同尚不能拆分同一条 `action_resource_sources` 的字段所有权，所以选用容量来源的能力不得同时绑定旧动作资源来源，也不得把两边次数相加。

## 工作步骤

1. 在资料阶段查询 `action_resource_profile`，完整结果保存到该角色的小文件。核对技能中文原文、精确技能组、命座与动作模式；投影ready只代表资料可用，不证明游戏适用。命座参数属于原生用途声明，查询接口本身不接受命座或天赋等级。

   响应可额外包含受审 `cast_sequence`。这是独立的只读连段事实投影，供资料冻结和支持该契约的引擎输入使用；它不把 v2 首片的支持范围扩展为任意 E，也不允许旧 `action_resource_sources` 自动执行多段技能。按[多段施放与续段窗口](cast-sequences.md)读取每段值和 `resource_policy`，不得从主技能/`subSkills` 的同名或原始 `cdTime` 自行补推。

   当前运行时另提供 `action_activation_catalog` 时，可按角色、精确技能组、命座及明确等级/突破一次读取技能、被动、命座和相关根目录，连同 `action_capacity_source_profile` 的当前受审来源保存到同一角色资料文件；后续复用该文件，不为每次动作重复查询。以 capabilities 的实际字段为准，旧客户端未提供时不提交新查询名。

   来源中的次数增量只证明该来源的作用，不等于最终最大次数，更不等于开场就绪次数。`capacity=null`、`execution_authority=false` 或弱关联根不授予默认挂载、总容量或动作执行资格；不要自行把基础值与已读增量求和后当作系统自动批准。目录可用与完整动作派发是不同状态，现有执行入口的范围仍见下文。
2. 保留既有能力及每次实际动作，按需补齐资源输入，不以focus切换为前置。运行时提供自动模式时，在公开来源绑定填写 `action_resource_sources[].energy_delivery="action_start"`，仅用于该来源适用的普通固定耗能Q；**不再创建对应手工 `energy_spend`**。它只交付已给动作的扣能，不补轮次或改变动作。旧版或明确 `existing_events` 模式仍要求每动作恰一条spend，v3放动作开始同刻的后续order。
3. 在结构转换阶段添加一条来源绑定。能力保留 `cooldown.group/start/recovery` 和 `energy={}`，省略 `cooldown.duration/charges` 及能力上的 `required/amount`；旧手工spend模式还须省略对应事件的两个数字。目标字段不能同时手填，即使数字恰好相同也拒绝。原生绝不能写执行副本专属的 `ability.energy.delivery`。
4. 首次三个 `expected_*` 为null；运行compose并读取该来源的完整中文资料、目标路径、issues与 `required_confirmation`。只在核对依据后复制当次snapshot/resource/scope三个指纹；不能从旧resolution或别的技能移植确认。`status=assumed/confirmed`是用途依据声明，确认指纹不会把pending自动升级。
5. 再compose、calc。任一来源pending均不执行半份计划。检查本次 `compiled_rotation_model`、`action_resource_compilations` 和完整能量流水；自动模式还核对父动作和系统审计一一对应，以及提交/失败/未验证/未报告的统计。保存时保留原生省略，不把执行副本或自动审计覆盖原生输入。Excel只读保存来源和提交瞬间的能量；改动作、初态、回能、ER或来源后重新检查并生成工作簿。

## 最小来源形状

```json
{
  "ability_id": "actor.q",
  "version_line": "release",
  "selection": {"character_id": 10000002, "slot": "burst", "constellation": 0},
  "purpose": "ordinary_fixed_burst_base_resources",
  "variant": "与既有能力的variant完全一致",
  "status": "assumed",
  "source": "资料文件位置及本动作适用固定基础耗能的依据",
  "energy_delivery": "action_start",
  "expected_snapshot_id": null,
  "expected_resource_fingerprint": null,
  "expected_scope_fingerprint": null
}
```

上例须以本次capabilities已经提供自动模式为前提。旧版省略energy_delivery并保留既有扣能事件，不自行向旧schema塞新字段。省略/显式existing_events使用旧scope；自动模式、动作集合或来源改变后重新复核本次确认，不能移植旧scope。

自动扣除发生在动作接纳时、该动作自身收益之前。同刻位于动作之后的返能不能救活该次能量不足；失败动作没有成功扣能审计。审计 `phase=action_admission`、`delivery=automatic_action_start` 没有order，不是可触发或可编辑的事件，不能拿其ID做rule、快照或资源替换。before/after只表示提交瞬间；之后可能还有退款/产球。数据库不证明游戏扣能帧，此相位属于已声明模型约定，不能据ready提升为游戏验证。

旧方案转自动时先检查每个标准旧spend及所有引用，明确删除清单后再迁移；不可用resource_ownership先删除冲突来掩盖双交付。部分缺失、重复、延迟或有引用时保留问题，不自动修复。切回existing_events可保留绑定和缺事件草稿，再明确填写事件顺序；不把审计复制成输入。v3其他扣能时机仍未支持，不能通过改时间伪装。

当前来源仅支持C0、单次充能、常规固定耗能爆发：资源门槛与扣除量均等于基础耗能。E、C>0、额外次数、特殊或未决资源模式不属于该自动来源范围，不改写命座或删规则来取得ready；这不禁止按[动作与能量接口](rotation-energy.md)的公开手工能力和资源事件路径建模，有效性由实际输入和检查回执决定。角色身份、元素及明确技能组须匹配其他来源。共享技能别名不能借不同CD组绕过冷却；仅核对已明确身份的关系，不推断所有手工能力。

初始能量、容量、ER、产球、退款、冷却修改仍需明确提供。已有正确退款、粒球和冷却修改继续执行，不因绑定被删除或额外扣能。数值来源scope确认映射身份和用途；完整输入指纹及执行检查另负责时间/order/repeat，时序错误不会因指纹确认而变为可执行。

若改轴才能解决失败，按用户授权修订动作及受影响的绑定、事件和后继状态，再重新检查，不要求focus回退。GUI在“动作与能量→数据库动作资源”提供预览、确认、应用；绑定数字只读。解绑并恢复手填需要明确数字，也可明确移除来源并保留缺项为待补状态。此功能不自动判断所有爆发、动作派发、命中时序或产球。
