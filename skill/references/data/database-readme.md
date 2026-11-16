# 数据库 README：字段语义与关系范式

<!-- database-readme-contract
schema_version=1
database_release_id=db-7.2.50-r6
game_version=7.2.50
upstream_commit=f11cf545a7eee32a29999f960ebfba305f896ed3
runtime_indexer_version=7
normalization_version=2
last_reviewed=2026-11-16
-->

## 活动库与资料贡献边界

Agent可以读取并利用本说明中的全部拉表资料，但活动数据库是只读的已发布版本。实验中查明的角色appendix、通用范式、纠错或共建内容应整理为库外提案，逐项写明source、适用版本、目标实体、完整内容、证据和不确定性；不得调用旧annotation／semantic写入口直接改库。用户同意精确提案后，由数据库发布流程在隔离候选中应用，并发布完整新版本。

资料进入数据库版本只表示“可被查询和审计”，不表示可直接执行。动作、命中、附着、周期和生命周期等内容仍由Agent材料化为当前方案输入；单次方案材料化也不会自动回写公共数据库。

本页是数据库面向 Agent 的只读说明书。它帮助从角色、技能和装备身份定位上游原文、运行配置、项目 appendix 与共建知识；它不是数据库外键定义，不要求新增跨表 join，也不授予引擎执行权。

每个数据库版本都必须复核并更新上面的合同元数据、当前能力快照、关系范式和示例。版本不匹配时，本页只能作为历史说明，不能替代当前数据库的 `capabilities`、`snapshot_id`、来源指纹或查询回执。

## 1. 数据分层与权威

| 层 | 典型内容 | 权威与用途 |
|---|---|---|
| 上游派生目录 | `character`、`skill`、`talent`、`weapon`、`artifact_set` | 公共实体、中文原文、成长和基础数值的默认权威 |
| 上游运行原文 | `runtime_source` | 固定提交中的原始 JSON、SHA、大小和来源族；只证明保存了什么原文 |
| 项目运行索引 | `runtime_definition`、`runtime_reference`、`runtime_object_reference`、`runtime_parameter_binding`、`runtime_material_reference` | 项目按受审规则从原文建立的可追溯身份与候选关系；不是游戏执行结果 |
| 项目知识 | `annotation`、`character_note`、`mechanic_semantic_*` | appendix、共建事实、描述与审核历史；即使 approved 也固定没有直接执行权 |
| 当前方案材料化 | 白板中的动作、拓扑、事件、参数、状态和强类型绑定 | Agent 为当前场景声明并由引擎校验、执行；不写回公共数据库 |

`ready`、`resolved`、`approved`分别只说明该数据集可读、某条身份关系唯一解析、某条知识经过资料审核。三者都不等于“技能已经自动派发”或“真实游戏机制已经完整复现”。

系统计算链会使用本数据库，但路径分层：公共身份、角色基础面板、装备成长／属性和精确等级参数等确定性数据可由App查询／Resolver／Composer直接查库并解析；数据库README不是这些数值的替代来源。raw关系、Gadget／Bullet身份、appendix、opaque字段，以及角色／场景派生的动作、命中、附着、Buff、资源、有限多段、周期后台或生命周期，不因入库而直接取得执行权。除独立审核、版本绑定且由capabilities明确发布的强类型规则包／动作档案外，Agent核实采用事实后，将其材料化为版本化、强类型、绑定当前方案的执行输入。纯公式／状态层不查询live SQLite或解释原文，但可以消费随版本发布的受审静态规则资源。

数据库生成的派生机制候选或预填只是便利入口，不能成为已支持派生机制域的唯一入口。若事实已经明确、但当前只能依赖数据库来源才能表达，应报告`engine_agent_input_gap`，而不是把它归类为数据库缺失或要求数据库把角色说明直接编译成规则。

## 2. 常用目录字段

### `character`

- `id`：上游角色数字 ID，是公共角色身份。
- `version_line`：项目版本线标签；当前 `release` 不承诺该条目已在正式服可获得。
- `skill_depot_id`：角色默认技能组 ID。多技能组角色仍应通过运行查询核对候选，不自行假设唯一。
- `hp_90/atk_90/def_90` 与 100 级字段：项目按上游成长表投影的基础面板。
- `ascension_prop/ascension_value`：突破成长属性，不表示任何战斗中条件效果。

### `skill`

- `id`：上游技能 ID。
- `character_id`：该目录技能所属角色；这是目录归属，不证明运行 Ability 的释放入口。
- `slot`：项目归一的 `normal | skill | burst`。
- `desc_template`、`cooldown`、`energy_cost`、`duration`：上游技能表和中文原文投影；点按、长按、命座或状态变体可能不同。
- `proud_skill_group_id`：等级参数组身份；必须结合有效天赋等级读取具体参数行。

### `talent`、`skill_param`、`extra_info`

- `talent`保存命座或天赋目录与原文身份。
- `skill_param`保存等级参数投影；参数位置相同不代表用途相同。
- `extra_info`保存附属技能和固有天赋等角色补充目录；它不是自由文本 appendix。

### `annotation` 与 `mechanic_semantic_*`

- `target_type + target_id`是事实归属，例如某个 skill、talent、weapon 或 artifact_set。
- `status=approved`表示资料审核，不等于引擎规则。
- `status=pending`表示可供 Agent 发现、研究和材料化的候选，`execution_authority=none`。
- 队伍、排轴和 benchmark 不能成为角色通用事实身份；应引用角色侧事实。

## 3. 运行资料字段

### `runtime_source`

| 字段 | 含义 |
|---|---|
| `source_path` | 固定上游提交内的相对路径 |
| `source_family` | `ability`、`talent`、`avatar`、`gadget_excel` 等来源族 |
| `upstream_commit` | 该原文所属的完整上游提交 |
| `source_sha256` / `source_size_bytes` | 原始来源身份 |
| `payload_blob` / `payload_codec` | 数据库保存的完整原文字节及压缩方式 |

### `runtime_definition`

| 字段 | 含义 |
|---|---|
| `definition_id` | 项目按来源和节点内容生成的稳定身份，不是上游数字 ID |
| `definition_kind` | `ability`、`modifier`、`excel_record`、`gadget_record` 等索引节点种类 |
| `exact_key_json` | 在该命名空间中的精确查询键；不能当展示名 |
| `owner_definition_id` | 原文包含关系，例如 modifier 属于哪个 Ability；不证明游戏派发或触发 |
| `source_path` / `json_pointer` | 节点在完整原文中的位置 |
| `variant_key` / `namespace_key` | 变体与命名空间身份；歧义时不能默认选 `Default` |
| `node_sha256` | 节点内容身份，用于防止跨版本或陈旧引用 |

### `runtime_reference` 与 `runtime_object_reference`

| 字段 | 含义 |
|---|---|
| `reference_id` | 项目生成的关系身份 |
| `from_definition_id` | 引用所在节点 |
| `field_name` | 归一化后的关系字段名；原始键仍由 source/pointer 保留 |
| `relation_kind` | `ability_operation`、`modifier_operation`、`gadget_id_reference`、`gadget_config_name`、`gadget_ability_mount` 等 |
| `target_kind` / `target_key_json` | 期望目标种类与精确键 |
| `resolution_status` | 关系解析结果，见下一节 |
| `resolved_definition_id` | 仅在唯一解析时出现的目标节点 |
| `candidate_ids_json` | 歧义候选；不得自行选第一项 |
| `presence_status` | 原字段为 present、missing、null 或 empty；这些状态不可互相等价 |
| `legacy_reference_id` | 新对象图与旧审计关系的可追溯桥，不是执行绑定 |

`runtime_reference`保存通用引用图；`runtime_object_reference`保存受审的对象身份图。两张图的范围不同，不能因为新对象图已解析就把旧通用闭包改写为完整。

### 关系状态

| 状态 | Agent 应如何理解 |
|---|---|
| `resolved` | 在本快照和该关系规则下唯一定位目标；不证明触发、时序或执行 |
| `missing` | 关系形状受支持，但当前快照没有目标 |
| `ambiguous` | 存在多个候选，必须补充身份证据 |
| `source_empty` | 原字段缺失、null 或空；不能默认成 false、0 或空执行集合 |
| `unsupported_reference_form` | 原文保留，但当前规则不解释这种出现位置或结构 |
| `external_asset` | 旧通用图只记录外部身份；应检查是否另有专用对象图 |
| `invalid_reference_form` | 值形状不满足受审关系合同 |

## 4. 关系范式

以下都是 Agent 的查找路线，不是 SQL join 要求，也不是引擎自动执行路线。

### A. 角色到技能与等级参数

```text
character.id
  → character.skill_depot_id / Avatar.skillDepotId
  → AvatarSkillDepot.skills、subSkills、energySkill
  → AvatarSkill.id
  → ProudSkill.group + effective level
  → paramList / description
```

首选 `action_facts`、`action_level_context`、`action_parameter_at_level` 等角色入口，不要求 Agent 自己拼表。技能目录归属完整不代表 Ability 或实际命中已经绑定。

### B. 技能到 Ability 候选

```text
character + skill_depot + slot/skill_id
  → action_facts
  → direct_skill_ability（若原文明确）
  → parameter_modification_target（若 Talent 只修改某个 Ability）
  → ability_candidates
```

`ModifyAbility`目标只证明参数修改对象，不能自动当作玩家动作的释放入口。候选应保存关系类型、来源 pointer 和节点指纹；需要更深结构时，对明确的 `definition_id` 使用 `definition` 或有界 `closure`。

### C. Ability 到 Gadget/Bullet

```text
Ability / Modifier
  → CreateGadget.gadgetID 或 TriggerBullet.bulletID
  → runtime_object_reference(gadget_id_reference)
  → GadgetExcel gadget_record
  → jsonName
  → gadget_config_entry（仅当同快照存在 BinOutput/Gadget）
  → abilities[].abilityName（显式挂载，仍不等于实际派发）
```

已知 `gadget_id` 后使用 `gadget_source_profile`。若要证明某次创建来源，同时传该创建叶真实的 `origin_reference_id`。不要按相同数字、文件名或角色名猜 Gadget。

### D. 动画与动作模式

```text
Avatar.combatConfigHash / animatorConfigPathHash
  → Avatar runtime原文中的资源身份候选
  → AnimatorConfig / AnimPattern（只有同快照来源存在时）
  → 动画状态、模式和资源关系候选
```

动画 normalized time 不是游戏秒数。前摇、后摇、取消点、卡肉、播放速度和真实命中仍需版本适用的校准或实测。Animator/AnimPattern 缺失时，Agent应登记 `unavailable_current_snapshot`，而不是按路径名称补造内容。

### E. 公共战斗根配置

```text
runtime source capability
  → ConfigGlobalCombat.json（若同快照存在）
  → public_root_catalog / 定向原文
```

GlobalCombat是全局来源，不应强行绑定某个角色。取得原文也不表示元素反应、附着或伤害公式已经编译为项目引擎规则。

### F. 角色 appendix

```text
实际角色ID → 窄只读附录导航（本次目录提供时）
  或 character／skills／talents／extra_info 的真实ID配方
  → target_type + target_id
  → approved/pending 分类目录
  → 精确分类正文
  → Agent核实并材料化到当前事件拓扑
```

所选角色的approved与pending附录默认分开读取各分类／页正文，完整配方见[角色附录](character-appendices.md)。当前提供`gs_get_character_appendix_index({db_character_id:实际角色ID})`，版本线／数据库身份由会话注入；精确自描述可按需读`appendix_navigation_capabilities`。它只整理现有归属、目标和分类，正文仍沿`gs_query_appendix`，索引与分类计数不代表正文已读或知识可执行。公开角色详情没有related_targets字段，内部同名查询算法不是可调用工具。appendix按角色侧实体保存，不按队伍保存；同一事实不因在不同队伍使用而复制。

### G. 名称引用候选的 Agent 材料化

`modifierName`和`abilityName`具有明确的**对象身份语义**：前者给出一个或一组Modifier的精确名称，后者给出Ability的精确名称。它们本身不一定具有完整的**操作语义**；只有父节点的`$type`、作用域和结构也属于受审形状时，数据库关系才能说明这是添加、移除、触发、修改或其它操作。

因此，`unsupported_reference_form`不等于原文无法读取。Agent按下列方式使用：

1. 从角色、技能、天赋、装备或已证明的衍生物入口到达owner；不得只凭文件名或英文名绑定角色。
2. 保存字段值、父`$type`、source path、JSON pointer、owner、目标候选和当前解析状态。分别判断对象身份是否`unique | missing | ambiguous`，不要把字段存在写成关系成立。
3. 若父操作已受审，结合操作类型确定施加／移除／触发／修改、受益对象和条件；若父操作未受审，定向读取原文、appendix和公开资料，核实字段在该结构中的实际作用。仍不能证明时保持pending。
4. 对Gadget、Bullet或产球对象另查专用对象图。对象ID解析只证明衍生物身份；首跳、周期、次数、附着、刷新、替换和销毁仍作为生命周期事实分别核实。
5. 将采用结论材料化到当前方案的`event_topology`、状态／周期声明和`numeric_model`强类型执行绑定，保留来源、适用版本、不确定性及覆盖理由，再交引擎做通用结构和数值校验。

数据库中的名称引用、候选目标或`resolved`状态**不得直接复制为引擎执行规则**。Agent应最大程度复用数据库提供的身份、来源和候选，只有在完成上述语义核实后才构造当前方案输入；这项转换不写回公共数据库，也不把一次场景判断冒充通用规则。

### H. opaque字段与未知`$type`的Agent研究入口

opaque表示“原始键和值已完整保存，但项目尚未证明其规范名称或操作语义”，不表示数据损坏或不可读取。本次固定来源为`f11cf545a7eee32a29999f960ebfba305f896ed3`。混淆token会随快照整体变化，不能继承旧版7个字段／2个类型的标签、次数或pointer，也不能把某个旧token的新拼写当成已证明语义。旧224708示例保留于仓库历史审计`docs/pm/audits/2026-11-07-r9-readme-old-opaque-example-archive.md`，不作为当前读取配方。

本版受审归一化profile明确翻译36个字段与35个类型；其它保留的原始键不因此自动获得规范含义。以下是从实际候选库保存原文字节重新读取的**代表性研究入口**，不是全部未知字段清单，也不声称它们是本次新增机制：

| token | 当前库原文次数／来源文件数 | 原值与父类型 | 可达上下文与Agent用途 |
|---|---:|---|---|
| `BLDKFMFJDLI` | 1／1 | `true`；父`$type=GAOFBDHAABB`也未映射 | 米提亚C2领域监听对象；只说明存在这个布尔分支，不能据名字或邻接减抗对象推断其作用 |
| `BFALOOLJIAE` | 2／2 | Danica样本为202、Mitya样本为102；父`$type=KCMKPCNGFKG`未映射 | 两个角色运行对象中的整数；允许比较上下文，不得当作倍率、技能ID、频率或枚举默认值 |
| `$type=AAMMODEMOCN` | 1／1 | 未知操作类型 | 非角色水母运行对象；只能研究其完整节点，不能根据相似结构命名或执行 |

代表`source_path + JSON pointer`（当前库`runtime_source`均有完整原文和源SHA）：

- `BLDKFMFJDLI`：`BinOutput/Ability/Temp/AvatarAbilities/ConfigAbility_Avatar_Mitya.json`，`/0/Default/PGMKHCKJIKM/UNIQUE_Mitya_C2_Domain_Listen/BGPBLBIFCEG/0/BLDKFMFJDLI`。
- `BFALOOLJIAE`：`BinOutput/Ability/Temp/AvatarAbilities/ConfigAbility_Avatar_Danica.json`，`/13/Default/HBNCGKIFKJH/0/BFALOOLJIAE`；另一个样本在`ConfigAbility_Avatar_Mitya.json`的`/28/Default/HBNCGKIFKJH/0/BFALOOLJIAE`。
- `$type=AAMMODEMOCN`：`BinOutput/Ability/Temp/AnimalAbilities/ConfigAbility_Animal_JellyFish_N_01.json`，`/9/Default/HBNCGKIFKJH/1/$type`。

这些次数只统计当前已导入原文，不推断未导入的Monster或其它上游文件。维护审计`docs/pm/audits/2026-11-07-r9-database-readme-facts.json`保留双根身份、样本值、源SHA和pointer。原始大范围token扫描只是定位候选；动态名称、不同来源族和未受审schema都可能有大批原始键，不能把扫描计数当作字段解释覆盖率或引擎缺陷数量。

Agent可以按以下边界利用这些内容：

1. 对活动数据库中存在的样本读取完整definition或有界closure，保存原始token和值、source path、JSON pointer、父`$type`、owner、source/node SHA和版本身份；这些内容可直接作为检索、版本diff和研究证据。
2. 已受审的相邻字段仍按其既有语义使用；opaque字段只标记为未解释分支，不能因为同一对象内存在已知`ApplyModifier`、`RemoveModifier`等操作就继承其语义。
3. Agent可结合角色原文、appendix、公开资料、跨版本对照和实测核实实际游戏行为。证据足以支持当前任务时，将结论连同适用版本、不确定性和覆盖理由材料化为当前方案的事件、状态、周期、目标或数值输入；不直接执行opaque节点。
4. 仍不能证明时保持pending并报告缺口。不得仅凭`true`、`"Caster"`、向量形状、文件名、角色名或重复次数猜测字段名、默认值、false分支、单位、目标或触发时点。
5. 当前方案中的一次材料化不反写公共数据库，也不升级normalization。正式映射必须作为数据库维护变更，具有新旧同节点或可信明文schema证据、明确父类型与作用域、多个正反例、值域合同、全量无碰撞／指针可逆验证、独立holdout和新的exact-commit profile。

### I. 装备非正式候选与延期边界

本版把下列异常分支登记为 `deferred_nonproduction_candidate`。这是项目维护说明中的**疑似非正式／旧测试／占位资料**标记，不是上游明文的废弃字段，也不写成数据库实体状态。原始行、原文、参数和来源定位全部保留；正常配装与普通资料研究默认跳过，不主动联网补文、不反推归属、不为提高非空率补造数值。

| 当前标记范围 | 现有证据 | 默认处理 |
|---|---|---|
| 套装 `15000` 及其75条五部位／星级／初始词条变体 | `disableFilter=1`、无图鉴行、无套装名、3／5件效果无 `EquipAffixId`；单件名称、部位、星级和属性池仍存在 | 整个簇作为非正式候选延期；不继续研究3／5件效果，不进入普通套装选择 |
| 当前质量报告中的30条无名称且无任何武器／套装入站引用的效果行 | 仍保留 `openConfig`、参数和来源，但没有可证明的拥有者 | 标记 `unreferenced_internal_candidate`；不按相似名称猜归属，不执行 |
| 武器 `10009`～`10012` | 上游名称／结构明确带测试或占位特征，且缺基础攻击初值 | 标记非正式候选并排除自动配装 |
| 共享主词条池 `1000` 中缺逐级值的六种非火元素抗性分支 | 池中有属性枚举，但等级表只给出火抗成长 | 只把这六个数值分支标记 `legacy_unused_value_branch`；不把引用该池的703条正常圣遗物配置标废弃 |
| 1星、无套装的测试型圣遗物 `23300`、`23316`、`23318`、`23339`、`23340` | 分别承载速度、物抗、减伤、受治疗、冷却缩减样本值，但缺少正式单位／作用域／叠加合同 | 样本实体作为非正式候选延期；原始语义仍可用于日后研究，当前不建立引擎映射 |
| 缺名称／正文的武器配置中同时具备 `isDisuse`，或无图鉴、通用测试图标且无被动关系的记录 | 多项结构证据共同指向内部或废弃配置；仅缺TextMap本身不足以证明废弃 | 仅对满足组合证据的具体记录延期；其余保持 `source_missing`，不得批量判废弃 |

以下对象明确**不在整体废弃标记内**：

- 正常圣遗物即使共享主词条池 `1000`，其已有生命、攻击、防御、充能、精通等有效分支继续可用；
- 武器 `11420`「一心传」名刀保留为具名装备，只把缺失的攻击百分比初值记为 `source_incomplete / not_auto_calculable`；
- `FIGHT_PROP_HEALED_ADD` 在正式“游医”效果中已有“受到的治疗效果提高20%”的来源证据；测试圣遗物样本延期不等于该属性类型被废弃；
- 其它五种属性枚举仍保留宽泛语义，当前只是不建立未经审核的单位、乘区、叠加与作用对象规则。

只有出现下列任一条件才重新开启该项审核：用户明确选择受影响对象；新上游快照为其增加正式图鉴、正常入站引用或完整数值；同版本取得可靠的正式用途证据；正常游戏方案实际消费该分支。重新审核前，Agent不应把这些候选当成系统缺口反复研究，也不能删除原始记录。

## 5. 当前版本能力快照

本节记录 `db-7.2.50-r6` 的能力快照。该稳定发行保留 r5 的数据库内容，文件 SHA-256 为 `27e6e2a85b8f11e0c646fc6ead9aa40671e5ceedcecae17b479f5e8017f13dca`，上游快照及运行索引不变；不能据 App 独立升级推断数据库内容又有更新。当前活动库及发行身份以 App 回执为准。原始版本为 `CNCBWin7.2.50`，上游更新于 `2026-11-04T12:47:50-03:00`。默认查询线仍为 `release`，这条资料线不代表其中所有实体均已正式上线，独立 beta 线继续延期。

- 已保存全部 530 个角色、武器与套装实体的上线状态证据：446 个已上线、2 个明确未上线、82 个未知。状态仅针对实体，不证明具体技能数值已经与正式服逐项一致。涉及未上线、未知或冲突实体时，按资料使用规则先询问用户；未知仍如实标注未知，使用流程按未上线处理，不得因同名正式实体存在而替换身份。
- 当前项目知识包含三条 `pending` 附录：71（哥伦比娅山月草露可等价消费）、72（奈芙尔转核后的普通草核清理）、73（哥伦比娅重施元素战技保留有效涟漪的引力）。按目标读取正文及适用条件；这些记录不是引擎的角色专属执行逻辑，仍须由 Agent 形成方案输入。重施技能的其它时序不能由这三条标题推断。

- 171个角色目录、442个技能；442/442技能有目录`character_id`。目录归属不等于完整动作派发。
- 2754份运行原文、214076条定义、169462条通用运行引用；148903条唯一解析、9549条`unsupported_reference_form`、165条`missing`、156条`ambiguous`、10689条`external_asset`。
- 9549条未受审出现位置中，4010条字段为`abilityName`、5539条为`modifierName`。这表示当前关系提取器未证明该父操作／位置的操作语义，不等于其值无法读取；按G节逐项核实，不沿用旧版按owner推断的统计作为当前结论。
- 10262条Gadget／Bullet数字引用中10253条唯一定位、9条missing；全部427条产球Material引用唯一定位。这只证明对象身份。
- 上游快照提供 719 份 Gadget 配置和 16,082 个配置入口。32,606 条 GadgetExcel 配置名关系中，24,386 条为 resolved、436 条为 missing、7,784 条为 source_empty。20,695 条 Gadget→Ability 挂载中，20,663 条为 resolved、32 条为 missing。按 C 节从实际创建引用追查配置和挂载，逐段命中仍需核实。
- `global_combat`现为ready并保存1份公共战斗根配置；`animator`与`animation_pattern`仍unavailable。GlobalCombat原文与Gadget配置的收录增加研究材料，不自动证明时序、生命周期或引擎规则。
- 归一化版本2、indexer7；本提交精确profile为`upstream-f11cf545-reviewed-schema-v1`，mapping fingerprint `cb52e3904c2c67dddc9f9d0a48301b760e44f032d530a2e82b39cc35c899a648`。旧token不跨版本复用；H节的新样本为Agent研究入口，仍没有执行权。
- 正式`action_dispatch_profiles`与`gameplay_action_manifests`当前均为空；不能宣称真实角色端到端自动派发覆盖大于0。角色appendix是局部知识候选，不代替完整动作档案。
- 装备目录291把武器、68套套装、4412条圣遗物配置。质量报告缺名称47、缺描述129、缺效果关系2、缺数值配置708、未映射属性类型5；这些是质量分类计数，不是同样数量的正式装备完全不可用。I节的非正式候选继续延期；已复核占位套15000的75条配置、30条无名称无入站效果、测试实体与主词条池1000的703条引用。正常共享实体与正式例外不受影响。
- 星月资格目录revision2对真实新库13项身份复核：12项confirmed、米提亚特殊领域生命周期保持pending。静态已含来源V4仅提供非数值来源判定；它不自动给用户成品面板叠加属性。各项资格和局部pending须读取当前capabilities／来源回执，不从README取得授权。

这些缺口只降低相应自动化能力。角色、技能原文、等级参数、装备、面板、通用元素规则以及Agent显式材料化后的计算仍可分别使用。

## 6. Agent最短读取流程

1. `gs_search_catalog`按中文名取得真实角色ID。
2. `gs_query_context`读取该角色的角色、技能、天赋、extra_info和approved annotation；保存查询资源，不复制整份内容。
3. 对实际使用的每个动作调用 `gs_query_runtime` 的 `action_facts`或`action_level_context`。
4. 只对明确候选读取 `definition`或有界`closure`，定位创建引用、参数来源和未解析关系；遇到名称引用时按“对象身份→父操作语义→当前方案材料化”处理；遇到opaque token时按“H”节保存原值与定位证据并核实语义，不把候选行或未知节点直接交给引擎。
5. 已知 Gadget ID 后再查 `gadget_source_profile`；依赖前一结果的请求不要预猜后放入同一个batch。
6. 按默认附录配方发现所选角色的全部可读分类，再分别分页读取approved与pending正文，复用同快照已读内容。
7. 本地资料缺失或冲突时，提出具体研究问题再联网查询；记录版本、URL、采用位置和未采用结论。
8. 将采用结论材料化为当前动作、事件、状态、参数和绑定，再交引擎校验。原文、候选或README本身都不执行。
9. 对角色／场景派生机制读取能力回执中的逐域`agent_input=supported | partial | unsupported`状态；`partial`或`unsupported`时保存事实和待发布输入，不以数据库命中冒充可执行，也不把引擎接口缺口改写为资料缺失。角色面板、装备数值等确定性查询仍按App来源回执直接使用。

已知且彼此独立的运行查询可以最多8项batch。完整大响应保存在会话resource或角色小文件中；对话只保留状态、计数、稳定ID、来源指纹和缺项。

## 7. 禁止推断

- 不因Ability或文件名含角色英文名，就建立公共 `character_id` 绑定。
- 不把`owner_definition_id`解释为技能实际施放入口。
- 不把`ModifyAbility`目标当作动作派发入口。
- 不把Gadget显式挂载的所有Ability平铺为一次技能的全部攻击。
- 不用持续时间除以间隔猜命中次数，也不由timer存在猜首跳。
- 不从相同数字、同名配置、相邻版本或另一个track拼接关系。
- 不把缺字段解释为0、false、无附着或无效果。
- 不把`resolved`、`ready`、`approved`或查询成功写成引擎已支持。
- 不因opaque值是`true`、`"Caster"`、数字或向量，也不因它重复出现或位于角色命名节点中，就为字段／`$type`猜测规范名称或执行语义。
- 不让旧版本README覆盖当前capabilities和来源指纹。

## 8. 版本维护检查

每次数据库版本、上游提交、normalization、indexer或关系索引变化时，维护者必须：

1. 更新页首合同元数据；
2. 复核所有表和关键字段含义；
3. 重跑角色→技能、技能→Ability、Ability→Gadget、Gadget→配置的覆盖统计；
4. 更新四个可选来源族及其它source capabilities状态；
5. 核对关系种类、解析状态和公开查询action是否新增、删除或改变语义；
6. 对`unsupported_reference_form`按字段、父`$type`、值形状以及owner优先目标结果重算`unique | missing | ambiguous`统计；新受审父类型只能通过提交绑定的normalization／indexer规则加入，不能按字段名批量放行；
7. 重新扫描相对上次采用快照新增、删除或改变形状的opaque字段和未知`$type`，分别记录固定上游树与活动数据库次数、值形状、父类型、owner／角色可达性、代表pointer和映射证据；同步更新“H”节，不得把未导入来源的样本伪报为App Agent可读取；
8. 用至少一个直接技能、一个后台周期技能、一个有限序列、一个无伤害附着、一个Buff／资源／状态输入、一个未受审名称引用、一个opaque token和一个缺失来源反例复验读取与Agent材料化流程，并确认数据库候选没有直接取得引擎执行权；同一机制走数据库预填和Agent直接输入时应收敛到同一有效执行图，数据库不可用时已发布的Agent输入仍可执行；
9. 检查appendix目标在新库中的可解析性，保留未决项；
10. 重新核对“I”节候选是否获得正式图鉴、正常入站引用、完整数值或其它正式用途证据；未变化时继续延期且不重复研究，发生变化时逐项解除标记并记录理由，禁止按整类静默继承；
11. 在数据库更新审计记录README版本、opaque登记、装备延期标记变更摘要与验证结果。

版本合同测试会比较本页的数据库发行ID、indexer、normalization和已登记上游提交。未同步本页时，数据库维护验证不得通过。

## 当前原生Agent读取入口

通过`capability_index`发现当前工具，`tool_contracts`／`runtime_contracts`按pointer读取精确合同；关系范式只导航，不建立数据库JOIN或执行规则。角色附录按[默认读取配方](character-appendices.md)使用真实导航或角色／技能／天赋／附属项ID，再分别读approved／pending；character直接0条不能推断整角色无附录。实体小资源按版本与来源复用，需要时才展开其他原文，具体配方见[数据查询](query.md)。
