# 指定装备、原生方案与轴微调

需要构造装备或面板输入时读本页。按[当前入口](../runtime/software-entrypoints.md#三种入口的资源根)查看 schema、示例与注册表。原生方案表示下，App 和连接 App 的 CLI 可定向读取 `capabilities` 的 `/examples/panel_plan` 和 `/engine_v2`。独立 CLI 完整响应中，这些字段在 `data.plan_contract` 下。单独换装或查询时按用户目标处理相关输入。工作顺序见[工作法](../workflow/index.md)。

当前默认使用用户/题面给定的初始面板，Agent 选择或遵循指定武器、圣遗物套装类型与件数，APP 保存重开并继续编辑同一方案。给定面板已含两件套等常驻属性；所选武器用于被动和显示，不自动加入成长。当前不要求 Agent 逐件构造主副词条。旧完整装备模式继续兼容；App自动选择／分配属于未来D19；用户明确给出主词条及有效词条预算时，Agent可为该任务手工查询并构造面板，与随机掉落概率、背包、最优搜索分别设计。

原生方案中的角色、增益与收益支持中文 `name/description`；伤害事件使用 `hit_name/description`，字段同 [引擎模板](resolved-template.md)。效果部件 `label`、条件说明、来源 `note` 与轴微调 `reason` 也应中文可读。显示查询不改方案；显式修改已保存方案的名称或说明仍按现有上下文指纹机制复核依赖与微调确认，不能篡改指纹跳过复核。

## 可选的聚合词条参考面板

用户已给最终面板时直接采用，不逆推词条，也不为使用工具重复加花羽、武器或套装。明确要求按主属性/有效词条预算构造时，可以调用原生`gs_reference_panel`，参数为`request`对象；完整字段读实际`tool_contracts /tools/gs_reference_panel`或`panel_reference_contract`，不要从开发者文件猜签名。

外部CLI的完整请求在`capabilities → data.plan_contract.panel_reference_contract.request_schema`，JSON文件直接放该request正文，不套原生工具的`request`外层。按实际schema传主属性、聚合`substats`及可选`current_panel`；不要靠连续报错猜字段。这里只读辅助，不自动改写或批准面板。

参考输入包含实际角色/等级、武器等级/突破、圣遗物星级/等级与沙杯冠主属性、可选套装件数，以及带明确每词值来源的聚合副词条。花羽固定值查相应模板，不要当副词条；百分比用ratio，允许题面授权的连续配平。套装只自动采用无`open_config`的显式`add_props`固定属性；非零`param_list`本身不代表条件效果，也不能据此漏掉固定属性。其它有据常驻项用`static_contributions`声明，条件Buff/武器被动不由正文自动执行。不能用另一ID把同一固定贡献再加一遍。

返回参考面板、各贡献/来源、逐属性缺项、预算提醒和与`current_panel`的差异。`current_panel`比较的是本次明确提交的面板，不是工具替你确认了候选里每个人；检查实际候选是否采用了同一输入。部分未知不补0，超预算不豁免题目。结果是`advisory_only`，不修改候选、不提供正式接纳、不要求必须调用；保留原面板或修正时给出依据，改用户固定项仍须批准。

当前支持可选`panel_provenance`时，原生放在`equipment_inputs[角色ID]`，白板放在对应`team[]`成员。`origin=user_panel|agent_constructed|unknown`说明数值来自谁的设定，`entered_by=user|agent|unknown`说明谁录入；Agent代用户录入仍是user_panel。Agent按预算构造时可附已用的`reference_request`和说明，供默认反馈核对，不强制逆推用户面板。缺记录按unknown，不能因为数值出现在GUI就认定用户锁定。

组合/计算提供`default_assistance`时，先看其中实际验证范围、面板差异和计划Q与已执行Q。只有已声明构造依据才复算；`construction_outdated`表示角色、等级、配装或输入已变化，旧构造依据不再适配，需更新或明确保留原因。反馈是软提示，不直接改面板，也不替代计算接纳与用户授权。

仅在题面要求按主属性／词条预算构造时，还须按[配装原理](../mechanics/artifacts.md)核对主副词条互斥、有限升级次数下的聚合可实现性；不需要生成五件具体装备，但不能把不逐件分配理解为可任意堆同一种副词条。参考工具的算术／总预算提醒不证明这一游戏内可实现性；用户直接给定面板则不启动反推或新增硬门禁。

原生比较带实际候选/数据库绑定，输入或数据库变化后旧参考不再代表当前状态，应重新调用；它不建立新的审核票。外部CLI可用`equipment panel-reference --input request.json [--output reference.json]`，GUI只读接口复用同一服务。它们不是背包、随机掉落、逐件副词分配或自动最优配装器。

## 资料与来源

通过数据库能力自描述查询武器与套装目录；仅旧完整装备模式需要单件目录。默认图鉴筛选，`catalog_scope=all` 可查所有源配置。同名保留 ID，图鉴标识不证明当前可获得。

- 武器：读取详情原文、全部实际效果槽、所选精炼的完整原文/参数及 approved 注释。给定成品面板不因查询成长再叠加；明确需基础值、武器对比或预算构造时可查指定等级／突破成长；已含常驻效果记录包含依据，不再与 `add_props` 或正文重复相加。
- 圣遗物：直接查询所选套装详情、达到件数阈值的原文/参数和注释；四件同时审核两件效果。固定面板模式不需单件 ID、主词条池、强化等级或副词条查询，已含两件常驻属性不再加算。
- 角色：保留给定面板及来源，读取技能、命座、固有天赋完整上下文。`static` 取给定面板；规则需要 `base` 时只接受明确提供的 `base_atk/base_hp/base_def`，不能反推白值。公式未用的 HP/DEF 不为凑完整装备而编造。

旧完整装备模式继续读取实际已有逐件输入；当前任务明确要求从基础／成长构造面板时，也可查询角色基础和武器指定等级／突破成长。它是兼容路径，不是当前固定面板任务的准备步骤。

原生`gs_weapon_stats`接受`{"weapon_id":13407,"level":90,"ascension":6}`，版本线取当前绑定，不传refinement。它复用数据库成长公式：properties.value是规范成长值，unit=ratio保留小数，calculation保留原始项，source_refs标注来源。0.306268表示30.6268%额外充能，不是最终充能面板；精炼不改变成长。等级／突破以返回available_states修正，不能把角色100级自动用于武器或把精炼行空addProps当成成长缺失。`gs_character_panel_basis`仍只给基础白值，不给副属性或成品面板。

所有装备响应携带完整 `data_version`，包含 built_at。同 commit 重建也可能变化；关键计算前重新 compose 读取当前资料及相关注释。缺原文、缺数值和明确无被动分别处理，不把 null 填成 0，不从 open_config 执行脚本。

数据库README登记为 `deferred_nonproduction_candidate` 的装备异常分支，普通任务默认不选、不补齐、不为其消耗联网研究预算。该标记只排除具体异常分支：不得因此排除共享同一旧属性池的正常圣遗物、正式“游医”效果或具名武器「一心传」名刀。用户明确点名、上游恢复正式图鉴／引用／完整数值，或真实方案确实消费该分支时，才报告现有证据与缺项并重新审核；重新审核前不把它包装成正式装备，也不删除原始资料。

## 构造与复核

以下是结构化表达步骤，适用于任意focus。真实输入变更须在授权内提交新修订并复核依赖，不机械回退；不能为通过compose暗改用户约束。

原生文件是 `kind=genshin-plan`，支持 `plan_version=1/2/3`，不是 core 的 resolved 模板。v1 禁止 rotation_model；v2 必须显式提供 rotation_model，null表示未检查；v3另明确 mechanics_model，用于声明触发/次数/采样，两个模型可为null草稿。旧版本不接受mechanics_model。新循环任务用 [声明式机制](execution/rules.md)；保存角色、装备、动作、规则和轴微调，resolution/resolved_template为派生结果。

**先按输入模式选运行时示例。** `default_input_mode=panel` 是当前默认；`basis.mode=manual` 属于旧完整装备模式，不能用来表示给定最终面板。

| 题目与来源 | 原生输入 | 缺项处理与交付边界 |
|---|---|---|
| 当前固定面板＋套装选择（默认） | 给定面板写入 `manual_template.characters[].stats`；对应 `equipment_inputs[cid]` 使用 `mode=panel`、`panel_policy=static_included`，保存武器与 `artifact_sets=[{set_id,count}]`、来源及数据版本 | 不需 `basis/artifacts/manual_contributions` 或五件主副词条，是完整有效输入。读取套装阈值及武器效果并审核已含/条件部分；具体缺证据的效果仍 pending |
| 明确只有面板，未选择武器/套装，也不要求选择 | 保留面板；可让这名角色不加入 `equipment_inputs`，全队均如此时为 `{}`；其余字段按 schema | 明确数值快照来源与范围，不虚构装备；没有武器/套装选择与条件效果审核的快照不冒称已经完成这部分 |
| 继续已有完整装备模式，或用户明确要求使用已有逐件输入 | 保留原有基础来源、武器、五槽、明确副词条及效果审核；手工基础使用有依据的 `basis.mode=manual` | 按旧模式处理真实缺项，不能清空已有输入绕过 pending；不把此路径扩成当前 Agent 必须自动分配词条的要求 |

多轮能量闭合不要求反推五件圣遗物。题面给出有效词条总预算时，充能词条按 [分阶段工作流](../workflow/index.md) 占用预算并替换其他词条；题面给出固定成品面板且明确允许补充循环充能时，只按不足量单列追加充能词条，核算为 0 词也要报告，不能借此重构其余主副词条。

固定面板角色使用方案角色 ID 作 `equipment_inputs` 键，角色等级只取 `manual_template.characters[].level`。记录 `character_ref`（可为 null）、`weapon`（可为 null）、`artifact_sets`、`source_refs`、`data_version`（可为 null）；来源说明给定面板已含常驻属性。武器对象保留 `weapon_id/refinements/selected_by`，`level/ascension` 可选且不用于增加成长。套装 `set_id` 不重复，每项 `count` 为1–5，合计不超过5。完整 schema 和示例以 capabilities 为准。

混合方案分别保留每人的输入模式。固定面板的武器/套装更换只改变效果选择，不自动重新分配面板；要比较不同静态面板，由用户/题面另给对应面板并记录。已有完整装备模式转为固定面板须有明确给定面板及转换依据，不能作为隐藏缺项的修错步骤。

来源检查不仅包括装备：角色技能、固有天赋、已拥有命座与共鸣同样需在本轴核对，按 [动作与来源核对](../workflow/coverage-and-review.md) 记录。能力返回的 `effect_requirements` 不是全队机制已完整纳入的认证。

1. 优先读取用户最新保存方案；新建时读取固定面板示例及完整 schema。`examples.pending_plan` 的旧完整基础字段不适用于默认 panel 模式。角色用方案内字符串 ID；装备使用数据库原整数 ID。保留 plan_id，提交输入修改时 revision+1；另建独立比较方案生成新 UUID。
2. 按上表填写给定面板、武器、套装与来源，百分数存小数；武器效果槽按真实 source_index/affix_group_id 逐一选择精炼。仅旧完整装备模式另填基础/突破/静态贡献及五槽：副词条 null 为未知，[] 为明确不计，五槽 null 为待选，empty 必须有原因。
3. 运行 compose 获取 `effect_requirements`、原文、来源摘要和 `proposed_binding`。若实际能力提供 `static_inclusion_contract`，先检查[静态已含来源](sources/static-inclusions.md)：最终自动采用的来源无需另建 excluded、Buff 或规则，数值不再加算；已有显式 binding 保留原意。其余有依据的已含常驻部件仍用 excluded 和明确 reason/note记录，未知部分保持待核，条件部件继续建模。旧规则支持constant、读取base/static的stat_scale和explicit_hit；v3可用 `rule={kind:mechanics,rule_id}`、`delivery={kind:mechanics}` 交给机制层计时与计算。规则source_id必须为 `binding_id:part_id`、owner对应provider_character_id，仍走相同覆盖审核。panel模式再用delivery.kind=panel会pending。
4. 覆盖整段机制后填 coverage.state=complete、reviewed_by/note 及 compose 返回的 source_fingerprint；binding 自身也保存同一摘要。part 为 resolved/pending/excluded，excluded 必须是当前场景有依据的不计，不为消除错误批量排除所有被动。护摩生命加成、生命转攻击、低血量额外转攻击需分别处理。excluded 不再使用规则时可将 rule/delivery 设 null；保留 stat_scale 时仍须搭配 dependencies.mode=stat_rule。交叉约束也见 capabilities 的 semantic_constraints。
5. 只有规则自身能确定依赖时用source_only/stat_rule；人工折算常数依赖面板、装备或轴时用manual_context，按compose返回的当前指纹复核。旧stat_scale的static只读给定面板，不含时变Buff。v3需要动态来源时使用当前能力支持的机制value与属性账表达式，检查sampling_trace及实际属性回执，不再逐击人工折值。转换链、同次回调和不同取样域分别核对实际契约；已支持的有向依赖可交引擎计算，未知来源、依赖环或未支持属性仍保持待核。
6. 首次启用装备管理时核对 `manual_effect_dependencies`：原手工 Buff 需明确不依赖配装（source_only）或绑定当前 context_fingerprint（manual_context）。班尼特加攻等可迁入带明确基数的 binding；迁移旧输出按精确 ID 撤销，不按名字删除。

已选择的数据版本与当前库不同会 pending。用户已授权更新资料时，在输入副本中同步当前完整 data_version，数据库基础按当前字段重取，然后复核被动及条件；APP 的“应用新资料”走相同服务。不要修改保存文件中的哈希骗过来源校验。

## 分类与仅在必要时的 match

v2 每个 hit 有稳定 id、damage_type_ids；每个 effect 有稳定 id、benefit_type_id 和 applies_to。优先采用注册表已有 `damage.elemental_skill`、`damage.elemental_burst`、`damage.normal_attack` 等标准类型；冰元素由 `element=cryo` 表达，不需另外发明伤害类型。确有额外分类需要时，先读注册表与 schema，在 `type_definitions.damage_types` 声明 `custom.damage.<名称>` 后引用，不能自行使用 `damage.<自定义名>`。分类、元素和 damage_system 分开。收益 ID 必须与 zone/stat 注册表一致；多标签命中同一收益只计算一次。已有数值体系以外的机制继续待处理。

旧Buff常规范围用applies_to的all/types/hits/none；null未知、none明确无关联。v3机制用触发过滤、受益者和effects.filter。两者均执行声明，不理解原文或自动补机制。

`rotation_tuning.buff_hit_matches` 默认空。只有具体轴中常规规则无法表达必要例外时才新增：

- `effect_ref={buff_id,effect_id}` 精确到一项收益。同一项最多一条。
- adjust 提供 include_hit_ids/exclude_hit_ids，按原候选增删；原范围未知时不能 adjust。
- replace 提供完整 hit_ids，可有依据地为空；只解决关联，不能替代数值、时间、目标或快照。
- 填 reason/source_refs，先 pending compose 获取 match_previews 的原候选、最终候选和 basis_fingerprint，核对后 confirmed。使用生成收益时从 contribution_report.generated_outputs 取得实际身份。

v3机制match使用effect_ref.buff_id=规则ID、effect_id=规则收益ID，hit_id用实际展开ID。预览的base_hit_ids/final_hit_ids可为null，因为实际收益需运行触发与次数后才知道；确认basis_fingerprint后看执行trace。include/replace不能绕过触发、CD、有效时间和次数池。

新增/拆分事件、改变分类、来源或作用范围后需复核受影响例外；普通排序不应改变关联。撤销删除记录即可恢复常规规则。仅修改当前方案，不写公共数据库或永久规则。core 收到的是组合后的范围；matched_effect_refs 表示公式实际读取的收益，不能当作各 Buff 的独立边际收益。

## 共用命令与交付

以下示例以 `python -m genshin_app` 为入口，便携版用其 exe 替换；全局 `--data-dir` 放命令前。读取 capabilities 中实际命令定义后执行。

```text
equipment validate --plan plan.json
equipment compose --plan plan.json --output composed-plan.json
equipment calc --plan composed-plan.json --output result.json
equipment export --plan composed-plan.json --format xlsx --output result.xlsx
equipment workbook --plan composed-plan.json --output formula.xlsx
equipment compose --plan composed-plan.json --export-resolved resolved-v2.json
```

validate 只检查结构。compose 的 code=0/state=pending 表示草稿已生成，继续看 missing_inputs；calc/export 在 pending 时失败，不产出结果。原生文件可在 APP 打开、换装、保存，Agent 继续时重读文件。裸 resolved-v2 只是当时数值快照，后续换装使用原生方案。

新输出默认不覆盖；明确覆盖使用 --overwrite，同一路径还会比较读取 SHA。compose 同时请求 plan 和 resolved 而 pending 时全部不写；多文件 I/O 失败按错误中的 published_paths 核对实际产物，勿称全成或全未写。保留冲突文件，再重新读取用户最新修订。

写入路径旁的 `.文件名.plan.lock` 是保留的跨进程协调文件：操作结束会释放操作系统锁并关闭句柄，进程退出也释放所有权，文件路径仍保留以供下一次协调。文件存在不代表仍被占用或导出失败；不要为“清理残留”删除它，尤其不能在其他进程可能写入时删除再创建，从而破坏互斥。

JSON/CSV/旧xlsx导出包含计划身份、指纹、假设和收益追溯。`equipment export --format xlsx`仍为结果快照；新 `equipment workbook` 为同源活公式，支持范围与验证见 [Excel参考](../outputs/excel.md)。

最终交付按实际模式核对给定面板及已含属性口径、武器/各精炼槽、套装类型/件数、条件效果与来源。固定面板＋套装方案不要求五件主副词条，也不归类为缺失装备草稿；仅旧完整装备模式另核对五槽与面板贡献。保存重开并重算确认原生文件确实产生所交结果；裸 resolved 仅作为当次快照，不冒充原生方案的来源。APP 与 Excel 使用相同面板、武器/套装、条件效果、动作/命中、时长和假设，旧结果说明绑定当次计算回执，不能随尚未应用的草稿变更。

原生v2/v3每人的ER由本次组合面板覆盖，rotation_check.panel_bindings保存声明值与采用值；换装后使用新检查结果。初态、技能CD与扣能门槛由Agent声明，v3产球/直接回能可由已声明资源规则生成，不从成品面板或中文名称猜规则。

## 固定面板与构造任务


> 适用：配装对比、词条收益计算、"刷什么提升大"类面板规划问题。
> 与 damage-formula.md 配合：本文解决"属性从哪来、怎么权衡"，公式文档解决"属性怎么变成伤害"。

当前默认由用户/题面直接给初始面板，已含两件套等常驻属性；Agent 只选择套装类型/件数并核对条件 Buff，武器只用于被动和显示，不加成长。固定面板＋套装选择是完整输入，不要求逐件主副词条或从面板反推配装。下列主副词条、白值和增量方法供解释明确给定的收益问题及兼容旧完整装备模式使用，不触发自动构造五件装备。

App自动选择／优化属于未来D19；用户明确要求按主词条与有效词条预算构造面板时，Agent可以为当前任务查基础／成长并手工分配，保留依据且不改变固定面板默认；与随机掉落概率、物品背包、最优搜索分开，当前不声称已实现或把它列为排轴前置条件。


## 可选面板复算

若当前capabilities公开面板参考工具，可提交相同构造条件作可选复算并读差异；工具不可用时按可查值核对，不伪造调用。参考只作提醒，不自动覆盖输入，不把不一致直接当作用户给错；未知、近似和未计条件项分别列明。

原生实际目录有`gs_reference_panel`时，从`tool_contracts`的`/tools/gs_reference_panel`读取当前schema，外层参数是`request`。构造至少给真实`db_character_id`和90/100级；完整参考另给武器身份/等级/突破、圣遗物星级/等级与沙杯冠主属性、套装件数、聚合副词条的数量/每词值/来源。花羽固定值由对应模板查询；不手动额外加第二次。

可用`static_contributions`补明确采用的常驻属性，ID不得占用工具自动展开的同源项。`current_panel`可只给要比较的已知属性或null及`dmg_bonus`子项；不是候选写入。套装总件数至多五件，合法3+2/2+2不受额外限制。`budget`超限只提醒，未用该工具不锁后续工作。

读取`reference_panel`、`differences`、`missing_inputs`、`warnings`与比较绑定。未知不补0，当前输入/DB改变后旧比较无效；helper不证明动作、逐Q能量或角色机制完整。外部CLI同服务入口为`equipment panel-reference --input reference-request.json`；GUI API为`equipment_reference_panel`，均只作参考。
