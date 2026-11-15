# 高级运行资料查询与同快照小资源

## 资料阶段

只需定位角色高级动作资料时，`action_activation_catalog`优先使用 `catalog_view:"summary"`，再按返回的 `next_options`读取对应分页。可选视图是 `summary/roots/materials/level_profiles/issues`；旧调用省略视图仍返回 `all`。分页为 `page>=1`、`page_size=1..50`（默认20）。保存快照和下一请求，不为找一个组件反复读取整包。`summary`的数量不代表对应动作已完整输入或可自动执行。

原文与参数仍用 `db context` 分实体缓存。需要机制的结构关系时，用 `db runtime --input request.json --output runtime-result.json --summary`：完整查询写入文件，控制台仅回状态、数量与文件回执，按问题定向读取文件。`--summary`必须同时给`--output`或受支持的batch专用`--output-dir`；旧版本不提供该选项时省略。请求字段以本次 `runtime_contract` 为准。常用请求：

同一数据目录的独立 CLI 资料查询按 `capabilities` 的 `database_call_concurrency` 等候。并发发出仍会排队，通常先用同快照批量或顺序读取，等待可取消，无需为查询复制数据库。查询占用和资料缺失分别处理。

```json
{"action":"roots","version_line":"release","characters":[{"id":10000002,"constellation":0,"talent_levels":{"normal":10,"skill":10,"burst":10}}]}
```

```json
{"action":"assemble","version_line":"release","affix_id":2150071}
```

运行capabilities列出 `action_facts` 时，可一次查询特定角色动作资料：

```json
{"action":"action_facts","character_id":10000063,"slot":"skill","talent_level":10,"constellation":0}
```

slot（normal/skill/burst）与skill_id二选一；多候选技能组明确skill_depot_id。talent_level是包含命座加级后的有效等级，不再次叠加；旧action_facts的C0缺省10，其他命座缺省会返回待确认。若capabilities列出 `talent_level_profile`，优先用该入口的 `base_level:10`、实际constellation与技能槽自动核对静态命座加级；对应 `action_parameter_at_level` 可按同一已核实有效等级读取param_index。两个新入口不用talent_level，避免将13当作基础等级再加3。未知操作仍pending，不能将candidate_effective_level当已确认值。

把等级贡献表、命座原文、records、参数绑定及source指针一次保存到对应角色小文件。等级/原始参数ready只证明该接口范围，不证明命座全部效果、ModifyAbility最终倍率、点按/长按或完整动作派发。旧自动攻击、周期来源或有限收益仍有各自资格限制，不能因为新等级入口ready就改写其资格字段绕过限制。`action_dispatch.status=pending`及`execution_support=not_evaluated/static_level_only`必须如实保留。

若本次capabilities列出 `action_level_context`，需要同时核对等级、参数父链与命座关系时优先用它，避免分别查询后自行拼接：

```json
{"action":"action_level_context","character_id":10000016,"slot":"skill","base_level":10,"constellation":6}
```

只传基础等级和实际命座；不传talent_level/effective_level，不为取得ready而改成C0。保存完整结果到该角色的技能小文件，复用其中level_profile、facts、footprints及target_material。摘要分别检查static_level_status、source_assembly_status和footprint_status；任一pending都保留原因，不用另一个ready覆盖。footprints列出全部已激活命座操作，包括Add/Unlock，不能只看facts中的ability_candidates。`target_identified`只表示找到目标；%N或-%N只定位原表输入，未执行数值修改或判定该操作与本次攻击无关。source_authority=not_granted、operation_execution=not_evaluated、action_dispatch=pending和whole_skill_coverage=partial不是可忽略的提示，不能据此放宽旧自动攻击的资格。此入口也支持本次白名单中的同快照batch；需要参数时配合action_parameter_at_level，完整资料只缓存一次。

把相关source/snapshot/引用、未解析项与参数加入对应实体机制说明；不要反复读取整份运行配置。定义和fragment只按需要展开，闭包截断或未知引用不能记作已闭合。不要从混淆字段名、动画归一化数值、粒子baseEnergy等自行推导未经核实的游戏语义。

只需要技能的基础冷却、次数、耗能和中文原文时，若capabilities提供 `action_resource_profile`，优先查询：

```json
{"action":"action_resource_profile","character_id":10000002,"slot":"burst"}
```

选择规则仍为slot与skill_id二选一，多技能组须明确skill_depot_id；这个接口不接收天赋等级或命座，也不以能力派发/ProudSkill资料为前置。将完整结果保存到角色技能小文件，摘要只看 `projection_summary` 的四项status/value及原文可用状态，具体依据读文件中的identity_sources、projection.origin和descriptions。缺字段为pending/null，不能默认0耗能或1次充能；原文缺失不等于已知数值失效。

**基础数值可读不证明当前动作适用。** `variant_applicability.status`、execution_support与gameplay_interpretation保持not_evaluated；例如点按/长按可有不同CD，不拿基础行覆盖变体。先核对原文、配置和动作模式，命座修改、减CD、退款或额外次数另行说明。另有 `plan_contract.action_resource_source_contract` 的版本，可按[动作基础资源来源](../app-mechanics/actions/admission.md)绑定已有常规Q与扣能事件，减少重复手填。没有该契约的App不自行添加字段。

普通水冰扩散可查询 `{"action":"reaction_damage_profile","reaction_type":"swirl","element":"hydro"}`（冰用cryo；运行接口提供动态v3时另查火pyro），保存完整结果并查看短摘要中的 `execution.sequence_status` 与 `ability_execution`。前者ready不表示整个原生能力可执行；`attenuation_profile`的damage与默认application是独立通道。按[元素运行时的来源流程](../app-mechanics/elements/runtime.md#普通扩散范围复核与自动伤害来源)复核范围并绑定旧v2/冻结的两份或动态v3的三份来源，避免手抄伤害序列。未知提交、结构变更、特殊分支不得用旧确认绕过。

提供 `passive_source_profile` 时，固有天赋先用 `{"action":"passive_source_profile","character_id":10000047,"version_line":"release"}` 发现候选，再明确选择 `proud_skill_id`；仅有一个候选也不自动选。多技能组须明确 `skill_depot_id`。选定后才传实际 `character_level` 和 `ascension`，未知字段省略，不传null、不由90级推断突破6。`roots`发现固有来源不等于已检查解锁，`action_facts`也不替代完整固有来源查询。详见[固有天赋来源](../app-mechanics/sources/passives.md)。

分别保存并核对 `source_status`、`unlock.status`、`descriptions.status`：资料ready可能同时缺解锁声明或中文。`eligible_under_declared_progression`只表示已给数字通过本接口的门槛/阶段上限，不证明最低等级流程或角色配置完整合法。`source_fingerprint`保护资料，`unlock_fingerprint`另含等级/突破；两者不能互换，也不能当App来源已接受。固有记录的level不套用普通技能10/13原则。

需要追查已取得的创建物数字引用时，若提供 `gadget_source_profile`，按[创建物资料](gadget-sources.md)查询精确 Gadget ID 及可选同快照创建引用，保存原行→配置→明确挂载的完整链。它只补资料身份，不推断技能派发、周期首末或命中次数；旧索引需要升级时明确记录，不能自行从 checkout 补入执行来源。

同时提供 `periodic_attack_profile` 与 `periodic_attack_contract` 时，优先按[周期攻击来源](../app-mechanics/actions/periodic.md)从所选Q的参数目标反向发现相关创建叶，选定后取得共用完整来源；无需自行搜索文件或重复抄Gadget ID。资料关联仍不证明动作派发；实际输入和资格满足时采用有限多通道档案，不以focus为门，时序／生命周期与来源数值分开。

提供 `energy_generation_profile` 时，按[产能来源](../app-mechanics/sources/energy-generation.md)以真实legacy引用读取GenerateElemBall→Material完整关系及有限参考单位，保存到角色事实文件。系统投影出的数量、元素和能量类别不再自行猜算；局部ready不执行父条件、随机分支或到账事件，也不增加原生方案字段。

### 同快照批量与资料小文件

capabilities列出`action=batch`时，将**已经明确所需的独立查询**合并为最多8项，减少重复查库验证。按本次白名单支持动作事实、动作参数、动作基础资源、攻击元信息、固有天赋来源、创建物精确来源、衰减档案、普通扩散伤害档案和精确affix装配；当前白名单也支持已知definition_id的`definition`与`fragment`，沿用单查相同sections／relative_pointer／max_bytes及完整性规则；通用角色原文仍用context，动作资源与固有来源查询自身附带相关中文可用状态。不递归batch、不枚举全库、不自动按前项结果推导后项。固有天赋发现和依赖发现结果的精确选择分两次，不能预先猜ID填后一项。下例只是请求形状：

```json
{"action":"batch","version_line":"release","items":[
  {"id":"skill-facts","request":{"action":"action_facts","character_id":10000002,"slot":"skill","talent_level":10,"constellation":0}},
  {"id":"skill-p0","request":{"action":"action_parameter","character_id":10000002,"slot":"skill","talent_level":10,"constellation":0,"param_index":0}}
]}
```

顶层统一版本线；若之前context已给出runtime快照，在顶层填写实际`snapshot_id`，不得在子项重复填写版本或快照。快照改变时报错，应复核受影响资料，不能删去旧snapshot要求以强行取得新旧混合事实。

```text
db runtime --input runtime-batch.json --output-dir facts/runtime-r1 --summary
```

只从返回的`index.json`逐项定位本代小文件，不扫描目录自行选旧文件。每项保留完整内容、请求、状态和统一snapshot；索引记录路径与文件SHA。将相关索引项链接到角色/装备机制说明，不复制成大文档。`--output`与`--output-dir`二选一；默认不覆盖已有索引，明确更新同目录时使用`--overwrite`，查询期间发生外部修改仍会拒绝。旧代保留，不自动删除或修复用户改过的小文件。

`complete/ok`只表示所请求资料已取得；内部`pending`、`action_dispatch`和`ability_execution`仍需分别检查。`partial`在CLI退出1，但指定输出时仍可交付成功、错误和未执行项的索引。按具体缺项修请求；总预算不足时减少项数或分批，不截断原文或把未执行项当空结果。致命的损坏/快照错误不发布新索引。规范payload最多4MiB，实际交付文件含索引另有8MiB上限，两者不是同一字节口径。

这些文件是本次查询的证据，不是跨会话自动验证缓存。后续先核对输入和快照，缺项才定向重查；当前artifact工作重点不是权限或查询门槛，资料与真实执行资格分别维护。

## 请求范围与定向发现

具体action先读当前`runtime_request_contract`的`/actions/<action>`；原生用`gs_read_context(resource="runtime_contracts", pointer="/actions/<action>")`，外部CLI可用`capabilities --section runtime_request_contract --pointer /actions/<action>`。部分公共命令和schema可分别按`--command`或`--section`选取，返回的`local_references`保留所需本地`$ref`，不必为一个字段重读完整能力包。

目录分页按各action自己的schema；当前`mechanism_catalog`默认为每页20项，上限50，不使用通用100项假设。`rotation_model.max_cycles`是附加状态核验窗的预算（当前3…20、默认3），不是实际动作轮数，也不会自动补多轮动作或伤害。超范围回执含actual、范围、default和修复提示；改合法预算后重试，不把错参判作游戏机制无法实现。
