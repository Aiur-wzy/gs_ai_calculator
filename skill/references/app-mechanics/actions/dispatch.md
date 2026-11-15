# 受审动作派发档案

仅在运行 capabilities 同时提供 `runtime_contract.action_dispatch_profile` 与 `plan_contract.action_dispatch_source_contract` 时使用本页。该入口负责把**已经审核并与当前数据库快照一致的动作语义**转换为现有单次或周期命中档案；它不替 Agent 选择动作，也不从空目录、候选攻击或工程夹具推断真实角色覆盖。

动作派发档案不能证明自己没有漏段。当前自描述若提供`gameplay_action_manifest`与`action_materialization_receipt`，按同一选择器把独立清单作为`event_topology`的额外证据：`finite_sequence`核对一次动作内每个step，`periodic_channels`核对每个通道及发生次数。`complete`只表示该受审清单全部物化；`unknown`表示没有独立清单，Agent仍可按资料声明本场景事件，但不能称公共自动资料或角色机制完整；当前统一Agent材料化首批接口允许该声明经通用验证进入正式结果，故`unknown`本身不阻断。`incomplete/diagnostic`表示已知分量或发生实例缺失，高影响缺件在补齐或明确排除前阻断正式计算。当前引擎不负责发现清单自身遗漏，角色事件盘点仍由Agent负责。`cast_sequence`表示多次玩家输入，不能拿一次动作内的`finite_sequence`替代。角色／技能选择器只用于资料寻址，禁止据此选择专用算法。Agent输入与当前执行合同见[机制材料化](../execution/materialization.md)和当前capabilities中的拓扑/执行绑定资源；开发仓库docs路径不是原生gs_read_skill可读资源。

静态周期档案与Agent周期入口是不同合同。使用前核实际source snapshot/版本/指纹；旧周期资料不能机械重签。事实核实后可按现行periodic_channel handler v2输入，历史来源不因此恢复自动执行权。

## 查询优先级

完成角色、有效天赋等级、技能仓、技能、槽位、变体和连段阶段的身份核对后，先查询 `action_dispatch_profile`，再决定是否需要手工建立 `action_event_profiles`。完整选择器为：

- `version_line`
- `character_id`
- `skill_depot_id`
- `skill_id`
- `slot`
- `effective_level`
- `constellation`
- `variant`
- `cast_stage`（有连段阶段时传整数；统一runtime请求无阶段时省略此可选字段，不传`null`）

示例只表示请求形状，使用工程占位身份，不代表生产角色覆盖；实际字段和值以当前 capabilities 和已核实资料为准：

```json
{
  "action": "action_dispatch_profile",
  "version_line": "release",
  "character_id": 90000001,
  "skill_depot_id": 9001,
  "skill_id": 9002,
  "slot": "skill",
  "effective_level": 10,
  "constellation": 0,
  "variant": "default"
}
```

查询结果按完整身份精确匹配，不采用邻近等级、邻近命座、同槽技能、同名技能或首个候选作为回退。非零命座必须传已经核实的有效等级；不能由档案存在推断命座自动加级。

## 三类输入及处理顺序

### 1. 受审自动派发

只有以下条件同时成立时，才把该动作记为受审自动派发来源 `automation_origin=reviewed_dispatch_profile`：

1. `action_dispatch_profile.status=ready`，且只匹配一份 reviewed profile；
2. 数据版本、上游提交、normalization profile 与当前快照一致；
3. 每个 emission 的攻击候选、来源指纹、周期 timer、伤害分类及附着身份均通过 fresh source verification；
4. dispatch、timing、damage、application 四组执行证据已确认；
5. 当前 App 原生合同可以无损表达该 profile 的 `cast_stage`、命中策略、生命周期和场景校准；
6. 原生方案保存精确 `profile_id` 与 `expected_profile_fingerprint`，compose 后完整物化清单及执行回执均通过。

满足时，在原生方案使用最小 `action_dispatch_sources` 声明，让 App 私有、原子地物化原型、绑定和攻击来源。不要把 report 中生成的 prototype、hit pattern、application event 或 reaction 复制回原生输入；不要再为同一动作建立手工档案或逐实例命中。

### 2. 可复用手工档案

精确派发查询 pending，但当前资料已足够按既有合同建立一份有来源的 `action_event_profile` 或周期档案时，可以保留可复用手工档案。它只定义一次原型、附着与调度，并绑定多个明确动作；编译器仍可把实例记为 `automatic_generated`，但其来源必须是 `automation_origin=reusable_plan_profile`，并另计 `reusable_profile_actions` / `reusable_prototype_hit_rows`，不能写成受审数据库自动派发。

建立前记录派发查询的具体 issue 和工作单；后续公共 catalog 覆盖后，重新查询并迁移，不能长期把同一分量按每个动作复制。

### 3. 逐实例手工补充

只有当前原生合同确实无法表达、或场景事实只能绑定具体 action/ordinal 时，才保留逐实例输入。每项必须标记 `manual_input_required`，说明：

- 对应动作、攻击分量和实例；
- 自动派发或可复用档案无法承载的具体字段；
- 依据、适用版本与影响范围；
- 公共资料或原生合同的回填目标。

模拟元素模式下不得用手填 reaction 标签替代引擎，也不得重复输入引擎已经生成的扩散或增幅伤害。引擎暂不支持而由 Agent 算出的数值仍走“外部计算补充”，不伪装成动作派发完成。

## Appendix candidate 不是派发档案

实验查明的局部动作耗时、命中频率、附着、染色／转化、天赋／命座组件或生命周期先按[角色动作资料附录](../../data/character-appendices.md)形成库外知识提案，经过完整数据库发布后入库，身份固定到角色侧实体和实际组件，状态保持 `pending`、`execution_authority=none`。队伍、轴和 benchmark 不进入事实身份；同一角色事实只保存一次。查询 reviewed profile 前，要分别读取 approved 基础事实与显式 pending 候选；后者用于定位、建模和复核，不能填补上文六项 ready 条件。

角色级 App 填入模板说明一份候选怎样映射为该角色的动作、组件、染色／转化、天赋和命座选项；跨动作被动作为角色级 overlay 供多个动作模板引用，不能因实验动作而归到单一技能。模板不包含某支队伍的轴答案。benchmark manifest只证明模板在指定动作检查点和场景中可以被引用、表达和消费。两者都不能证明角色完整动作，也不能作为通用算法的默认值。正向fixture可支撑后续审核；已知错误、撤销或缺条件的历史方案只作为`negative_regression`。只有候选经过独立事实审核，且另行满足完整选择器、四组执行证据、fresh来源核对与无损物化，才能新建或提升reviewed profile；不得把approved annotation或角色模板直接复制成可执行profile。

## 未知自动来源保持pending

以下限制针对该自动来源资格，不是整项任务或Agent工作的停止条件；可以查证后改用现有Agent材料化／手工输入，并保留实际来源和未解决项。

下列任一情况必须保持 pending，不能先生成“看起来合理”的前缀：

- catalog 为空、精确档案缺失、匹配多份或 review 未完成；
- profile、数据库快照、候选来源或 timer 指纹变化；
- `no_icd` / `no_application` 缺少结构化正向核实；
- `cast_stage`、场景指定或仍有歧义的同刻 `order`、实例级 `landed` 不能绑定到具体原生动作；父动作及其绑定`energy_spend`之外的零偏移顺序不得自行推断；
- 周期次数、首击、间隔、生命周期、目标范围或父动作不完整；
- 同批任一派发声明失败，导致无法证明整批私有物化完整。

pending 回执应保留组件、已知字段、缺失字段、来源位置和核实目标。不要删除 `cast_stage`、`order`、`landed` 或未知分量来换取 ready，也不要退回“标准三击 ICD”“默认 1U”“默认命中”。

## 覆盖与验收

工程测试中的 synthetic profile 只证明合同和编译链可以工作。实际目录为空时，该目录的真实角色受审派发覆盖为0；查询当前目录，不将历史条数写成永久支持范围；查询返回 missing/pending 是正确结果。即使某个动作生成了一个命中，也不能据此宣称该动作全部攻击分量已覆盖。

当前单个catalog bundle还有最多256个profile、2 MiB的边界，而选择器包含精确等级与命座。Agent不能为追求覆盖而改用邻近选择器，也不能宣称把全部组合塞入单bundle即可投入生产；正式资料建设须采用项目已裁定的分片、数据库索引或拓扑/参数解耦方案。未裁定或当前分片不可定位时保持pending。

盘点事件时，另建不依赖计算结果的dispatch manifest，逐动作列出预计component、channel、ordinal、父动作及资料状态，不以进入某个focus为前置。计算后按manifest对账，并读取`action_automation_audit`：

- 自动生成动作与命中，并分开核对 `reviewed_dispatch_actions` 与 `reusable_profile_actions`；
- 场景校准动作；
- 可复用原型行；
- 逐实例手填命中和动作连接；
- 自动附着尝试、手填 reaction、未知接触及工作单；
- 去重后的派发档案决策数。

`automatic_action_ratio`可能同时包含受审派发和可复用手工档案，只是执行自动化指标；评估E18节约时优先读`reviewed_dispatch_action_ratio`、`dispatch_manifest`和Agent事件拓扑清单。App回执中的`dispatch_manifest`只验证已选profile的expected components全部物化，不能证明该profile或角色已列出游戏内全部攻击分量。独立游戏组件manifest存在时作为额外证据；不存在时由Agent的来源盘点和明确工作单承担完整性声明。只有已声明分量ready或有具体pending工作单、且没有重复交付时，才能描述本次受支持范围；真实角色覆盖、完整队伍覆盖和发行状态分别报告。
