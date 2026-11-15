# 受审机制资料与通用来源绑定

2026-10-28现状：数据库受审机制资料没有直接执行权。`mechanic_semantics`只生成无执行权候选和预览，显式legacy会被拒绝；候选、preview或`approved`都不能直接执行。capabilities公开`agent-execution-materialization-v1`时，Agent可把核实后的事实提交为自包含强类型输入，由注册处理器验证、装配并签发跨层回执；执行阶段不重新查询或解释数据库。

当 `mechanism_source_contract` 和运行资料查询提供所需能力时使用本文。原生方案表示下从 `capabilities` 的 `/mechanism_source_contract` 读取，独立 CLI 完整响应中在 `data.plan_contract` 下。数据库给出规则语义与历史资格证据，App 为当前轴生成候选并核对来源。按[计算补正](../review/corrections.md)复核自动输出，需要手工表达时依当前合同处理同范围的自动来源。没有匹配的已发布材料化族时保持pending／unsupported。

## 当前路线与旧字段范围

1. 读catalog/profile只取得知识/来源预览；2. Agent核实当前采用事实；3. 用实际开放的自包含材料化/手工合同编事件；4. 读取真实验证和执行回执。资料preview/ready/qualification都不授予执行权。

原生入口按capability_index返回的资源读，不使用CLI的data.plan_contract外层。后文候选字段、accepted_fingerprint和旧来源资格表只解释查询/溯源及其历史形状；即使部分字段可兼容保存，也不能用“确认”这些字段激活旧知识执行链。当前可执行输入从材料化合同获得。

## 材料化输入的公共作者接口

需要减少拼接工作时，可使用[通用作者辅助](../authoring-assistance.md)：现有 `equipment materialization --request`／`gs_validate_agent_materialization(request=...)`生成普通可提交修改，并列出真实处理器能力和手写等价路径。它不代核事实、不写候选，不是新的执行语言；有限偏移命中可直接用 `attack_bundle`，不必为简单情况创建材料化拓扑。

不要根据`minimal_payload`猜整个扩展文档。先读当前材料化capabilities的`input_schema`（完整envelope、source、entry公共字段），再按`families[family]`的精确处理器版本选择payload和运行能力。来源与事实仍由Agent核实，不把技术指纹当作事实批准。

- 原生Agent：读取`agent_execution_materialization`资源可取得当前文档或新空骨架；填入当前候选扩展后用`gs_validate_agent_materialization`校对。资源`agent_execution_materialization_receipt`读取实际lint，骨架本身不代表已完成。
- CLI：`equipment materialization --plan 当前方案.json --output 骨架响应.json`返回`data.document`新空骨架，绑定当前plan/topology，不改方案、查库或执行。只填写来源、覆盖、节点和所选处理器payload；完整document另存后用`equipment materialization --plan 当前方案.json --input 完整文档.json --output 校验响应.json`校对。
- 现有原生方案尚无拓扑时，先用`equipment topology --plan 当前方案.json --output 拓扑骨架响应.json`取得新空拓扑及App按真实动作生成的`current_action_checkpoint`。完整根／组件／执行绑定格式在当前`event_topology_contracts`；这只是格式例，不是角色事件。自己填真实来源、节点与关系，再用同命令加`--input 完整拓扑文档.json`校对；除`validation`外还必须检查`context_status`及`action_checkpoint_matches`。合法拓扑响应另给`execution_bindings_scaffold`空绑定骨架；按实际输入对象补`bindings`，parameter_slots不能替代需要消费的真实命中／附着输入对象。不能把动作检查点的零示例哈希当成当前输入。
- CLI校验与原生工具共用同一lint。命令成功只代表接口已执行，必须读`data.validation.status/issues`；同样不能把空entries或错误来源当作完整技能。
- 输入或拓扑变更后核对绑定。需要重建时显式生成新骨架、审查并迁移自己的sources/entries；不要猜私有指纹、不伪造receipt、不把旧文档的stale标志直接改为ready。完整文档写回`extensions.agent_execution_materialization`后仍需fresh compose和计算。

这是一条通用作者输入路径，不从角色ID选择算法。不能无损表达的机制按当前接口反馈具体缺项，不用逐击手工展开隐藏生命周期缺失。

## 资料阶段：先发现，再保存精确资料

从已选择角色、武器或套装的数据库身份查询 `mechanism_catalog`。例如已知角色ID10000063时，可使用以下发现请求；它只是查询示例，不是队伍或机制的默认选择：

```json
{"action":"mechanism_catalog","target_type":"character","target_id":10000063,"version_line":"release","page":1,"page_size":20}
```

根据目录返回的来源主体、审核和修订选取相关条目，再请求 `mechanism_profile`，传入实际 `mechanism_id`；省略 `revision` 读取当前head，显式传入则要求它仍是当前修订。新计算同时核对机制描述及所引用的全部事实，旧修订不能绕过替换、删除或撤销。遇到 `mechanism_current_review_pending`，按诊断中的记录身份、期望/当前修订和原因更新对应实体资料，旧缓存保留作历史；不要换回旧revision或修改审核状态以取得ready。版本线沿本次资料选择，标签不承诺内容只属于正式服。目录支持发现的主体种类不等于App已能执行所有主体。

```text
db runtime --input mechanism-request.json --output facts/角色/机制来源.json --summary
```

Agent可复用App只读query资源；实际DSH文件能力允许时，也可在任务目录保存响应小文件与采用摘要，不覆盖App query正文或赋予文件数据库权威。说明链接原文并概括触发、取样、范围和缺项，不重复塞进上下文。已知独立查询可用[同快照batch](../../data/runtime-query.md#同快照批量与资料小文件)，依赖发现结果的精确选择仍等结果返回。

核对 `source_binding`、中文原文、`facts`、`resolved_parameters`、`source_proof`及`compiler_result.execution_qualification`。区分：

| 返回信息 | 能证明什么 |
|---|---|
| `review_status=approved` | 当前读取的资料及相关事实通过其记录的审核；不是本轴资格 |
| `source_status/materialization_status` | 原节点、参数用途、事实和结构可一致物化 |
| `qualification_status=consistent` | 旧资料资格/一致性信息；不授予本方案执行权，实际以当前材料化/静态执行合同为准 |
| 模板`status=ready`、`execution_status=not_evaluated` | 局部资料预览形状；不是已执行、不是整技能支持，也不能复制为执行回执 |

资料缺失、撤销、过期或未审核时保留问题。拉表任务不能为了获得ready自行修改数据库审核状态，也不能把原文重新包装成已批准规则。允许继续使用已有且范围适合的兼容来源；更换表达前明确停用同一收益的旧交付。

## 数值阶段：生成候选并材料化强类型输入

按实际需要核对动作、事件与每项资料的父事件资格，不要求先完成或进入某个focus。旧接触资格仍需所要求的[动作档案](../actions/event-profiles.md)及[攻击元信息](../sources/attack-metadata.md)；`successful_action_v1`直接引用已有施放能力，不为它创建无关命中、倍率或附着档案。

把`mechanism_sources[]`写入方案只会生成无执行权候选和`mechanism_materialization_required`，不能靠接受指纹解除pending。需要继续计算时，Agent必须审查候选和来源：capabilities公开`agent-execution-materialization-v1`时，在当前文档的`extensions.agent_execution_materialization`提交自包含强类型输入并运行`gs_validate_agent_materialization`；否则使用当前caps实际开放的manual／mechanics／event／parameter入口，或保持pending。材料化输入必须精确绑定节点、来源、coverage、处理器和版本；App回执须贯穿Composer、runtime、Service、接纳和导出。复制preview、候选指纹或审核状态都不能伪造材料化回执。

注册机制族采用逐族、逐处理器版本的能力真值；读取当前App返回的`family + handler_version`，不要因族名存在就推断所有版本可执行。周期必须使用`handler_version=2`并把`schedule`、`lifecycle`、`dispatches`分开：当前支持等间隔／明确偏移、显式次数或inclusive终点推导、单次或多次建立、replace／reset／extend／ignore、提前取消、退场继续或停止、保持或重置相位的暂停／恢复，以及damage／contact／application派发。立即直接能量可作为独立`resource`子条目随finite／periodic父派发逐次发放；每个通道相位最多256次。非立即资源、无限时钟和任意条件语言保持精确`unsupported`，不是让Agent另开功能开关。父finite／periodic派发未安装时，它所声明的application、resource、buff、state、规则包或嵌套派发子条目都不得退化成独立事件；当前只有damage／contact／application和立即直接能量具备逐跳交付语义，其余种类会让父派发精确拒绝，但仍受同一防孤儿门禁保护。多组dispatch固定为每次发生全部派发；不同频率必须拆通道。独立`buff@2`必须是一条闭合D20收益规则＋一条来源part，至少含一个typed benefit且不能携带资源交付。独立`state@2`只接受state-runtime v1有限时钟和`dispatch_rule` hook；每条规则由唯一保留路由交付，使用零delay、`refresh/max=1/add=1`，不得填写quota、resource gate、resources或next-switch目标。`resource_operation` hook和state-runtime v2保持unsupported。旧`buff@1/state@1`只兼容读取和保存，没有执行权；不要降版本绕过强类型校验。只有规则包能够忠实表达时才使用；不能用自由规则掩盖未知状态语义，也不得按角色／技能ID选择算法。若现有机制族不能无损表达，登记通用机制族缺口并保持精确范围pending／unsupported。

周期首跳落在输入窗后或在首跳前已被明确取消时，本窗0跳也有真实生命周期证明。App仍核对模板与子条目，但不生成占位命中、附着或回能；未来候选不等于已验证交付。另有真实命中时可继续完成本窗计算；全空动作不能因此作为已接纳零伤。读取边界的active／paused／inactive与future_delivery_verified，不能把窗口结束当作到期或稳态已证明。

当前版本的紧凑能力表：`application@2` supported；`finite_sequence@1` partial（`cancel_remaining`需要显式中断运行时）；`periodic_channel@2` supported；`resource@1` partial（仅立即直接能量增加）；`mechanics_rule_bundle@1` supported；`buff@1/state@1` unsupported且仅存储；`buff@2` supported；`state@2` supported但仅限上述有限时钟和`dispatch_rule`子集。若工具返回与此不同，以本轮工具回执为准并把Skill漂移作为问题报告，不能按本文强行提交。

需要粒球时，用 `mechanics_rule_bundle@1` 带入 `rules[].resources` 的粒球规则和对应 `resource_ownership`，也可直接编辑原生方案的相同字段。按[资源、多跳与生命周期](resources-and-lifecycle.md#多跳和资源)声明平均数量、触发和到账，计算后核对能量流水。

候选声明字段：

- `id/actor`：本地来源标识及实际提供者；`version_line/mechanism_id/revision`：已选资料。原生`revision=null`使用当前head，与公开查询中省略revision对应。
- `scenario`：真实目标、所选资格要求的场景值、状态和依据。仅填写本来源要求的条件；默认未知不能当作满足。条件效果是否已经计入面板须单独核对，与常驻属性已含约定分开。
- `calibration`：受支持的施放触发机制要求动作开始到效果生效的偏移、精度和依据；反应触发无需人为填写生效时点。零偏移是明确选择，null是未知。精度不授予时间容差。
- `accepted_fingerprint`：初次保持null。不要输入facts、完整descriptor、规则、初始次数池或逐击加成值。

运行接口支持资格v2时，成功施放来源使用`cast_binding.ability_ids`关联已有能力；同一能力的实际动作由系统遍历。`selection`只补确实缺失的技能组、技能、命座或有效天赋等级，不能把来源“仅支持C0”当成角色就是C0。角色、装备持有者与来源主体分别核对。战技须有显式正冷却、次数与依据；这是本轴声明，不是系统认证游戏CD。爆发还核对真实技能身份、基础资源和实际扣能。准入失败不触发收益。

`target_dependency.mode=none`的纯角色属性来源使用`scenario.target_id=null`，不补造敌方附着模型。带次数消费或目标条件的来源仍需该资格要求的模型。来源只认领受审的准确装备效果；四件效果覆盖不能替代两件常驻已含的核对，也不能消除其他待补部件。

运行接口支持资格v3、`actual_contact_v1`时，复用已接受的单次元信息或多通道周期档案；不填写`cast_binding`、额外生效校准或逐击触发ID。系统遍历提供者的所有相关实际命中，按资料检查前后台、内部冷却、叠层、续期及到期。`callback_bridge.hit_filter`是完整来源触发范围，`parent.hit`只是当前支持的派发子集；相关命中超出子集时应补资料或使用明确的兼容表达，不能删除真实攻击来取得ready。套装效果与武器精炼按角色实际装备核对，不默认精炼1。

命中后生效不会追溯增加触发本击伤害；已经取样的自身技能也不会自动吃到后来获得的自身增伤。`add_and_refresh`表示增加层数并刷新全部已有层的共同寿命，到上限后仍可续期。若来源要求`no_provider_transformative_damage=true`，它只适用于提供者本窗口没有转化反应伤害的参考范围，App还会检查完整实际反应流水；隐藏报表或伤害为零不能证明没有该触发。不要将这个局部限制推广成游戏中转化反应绝不触发该装备。

成功施放、收益激活和输出取样是三个不同时间。回调证据不自动证明自身爆发是否吃到其触发的装备Buff。需要延迟自身取样且接口支持时按[动作面板取样](action-captures.md)声明；不要把`action_start`一律解释成全部施放Buff已生效，也不要删去提供者伤害以规避未知关系。`fixed_zero`资格不接受额外校准；其余以运行契约为准。

数据库候选可提供系数、持续时间、受益条件、次数上限、属性读取和反应触发证据；只有Agent采用后写入的强类型输入才由引擎执行。当前受限规则的实际身份、命座/等级/突破、父动作形状、属性贡献和消费者范围以资料资格及本次App检查为准，不能把某一来源的ATK、C0或长按限制套用到所有机制。未知实际时序、距离、初态和命中仍需有依据的声明，系统不会从数值资料推断。

compose后查看`mechanism_compilations`的来源、父图、`scope`、候选指纹和问题。知识候选本身会保持`mechanism_materialization_required`；不要写回`required_confirmation`试图取得旧执行权。将采用内容转换为当前方案强类型材料化输入后，停用同范围旧交付并fresh compose；材料化、统一override和外部补充对同一coverage只能有一个有效所有者。task-capability的解决状态只能由App在完整受信回执、唯一所有权和fresh有效图上私有派生，Agent不得手填resolved。无法无损表达的范围保持pending／unsupported。

## 执行与更改

同一真实收益只能选一份交付。`mechanism_sources`与旧`passive_sources`、`limited_hit_benefit_sources`或人工规则不能重复叠加；不同本地ID或不同资料线不能把同一游戏来源变成两个可叠加来源。系统不会凭未标注常量的名字或数值猜测它属于哪个机制，Agent须核对旧方案来源后明确迁移。

监督式候选不会直接加入可执行规则；候选中的任一来源不合格时不得部分复制其剩余片段来规避问题。属性取样检查仍须包含同批其他输入的潜在贡献；不能通过拆来源、把转换写成常量或删除真实Buff绕过固定NONEXTRA、筛选贡献等限制。提供者当前值、触发时取样与受益者输出快照分开核对。

合法移动或增删相同档案的动作实例，仍须重新组合与计算检查顺序/覆盖，但不要求重复接受未变的资料语义。资料、主体、场景、校准或必要固定面板变化时重新复核。要改动作检查点含义时按授权修改相应动作／拓扑工件并复核依赖（无需focus回退），不在输入转换中悄悄修改动作。

本轴例外使用现有 `rotation_tuning.buff_hit_matches`，从实际返回的规则与收益ID引用，不自行构造保留前缀ID。微调不能绕过来源资格、改变元素状态、退还次数或激活待核规则；通常不需要为自动来源逐击填写match。

## 结果与交付

读取 `mechanism_summary`、`mechanics_trace`、`sampling_trace`和相关命中，核对实际激活、刷新/到期、次数和属性来源。`whole_source_status=partial`及资料的未覆盖分支须保留，不把计算成功写成全队机制已验证。需要多轮伤害仍须输入对应完整窗口的动作。

按[一次完整交付](../../outputs/app-and-excel.md)保存原生、完整计算包和两类Excel。只读「通用机制来源」页记录资料、资格和本次执行；离线导出只验证保存材料一致，不代表当前数据库仍批准。

资格v3交付保存完整周期来源与装备身份，使用同一引擎重放已保存的执行输入，核对实际触发、层数及收益。它不联网更新审核，也不替代游戏时点证据。

公式Excel使用本次展开的逐击收益及取样，不重演任意机制状态机。`formula_workbook_support`给出的固定来源参数、属性依赖、动作/场景/次数及match发生变化时，回App重新计算生成。不要改只读来源、流水或缓存来掩盖差异。
