# 精确攻击来源与自动元信息

仅在当前capabilities提供`runtime_contract.actions.attack_metadata_profile`及`plan_contract.attack_metadata_contract`时使用。本入口与[动作档案](../actions/event-profiles.md)配合，不替代资料整理、完整动作检查点与事件拓扑或来源完整性检查。

## 资料阶段查询

在对应角色的小文件保存以下查询结果，按问题读取候选和未决项：

```json
{"action":"attack_metadata_profile","version_line":"release","character_id":10000002,"slot":"skill","constellation":0,"talent_level":10}
```

用`db runtime --input request.json --output facts/skill-attack.json --summary`落完整文件。多技能组须明确skill_depot_id；slot/skill_id二选一。当前动态来源入口接受精确命座、1–15级有效天赋及唯一的普攻/战技/爆发槽；旧冻结接触v1仍限C0、有效10级元素战技。范围放宽只表示查询和动态装配可保留该精确身份，不会自动证明命座新增分量、完整派发、变体或动作时序。不能为适配入口改变角色命座或动作检查点。未指定candidate_id时是候选发现；系统不会替你选首项。按完整父链、变体及原文选择确切候选，再以返回的真实candidate_id定向查询，不能编造ID或用别的技能同值候选。

检查四类投影及其逐字段来源：parameter（参数位置与标量）、element（元素）、gauge（原始GU）、attenuation（有限附着序列）。相同元信息的互斥条件两侧可能合为一个候选，**不能据此生成两击**。额外倍率比率、生命值项、未知控制树或冲突参数写者未支持时，保留pending与原因，不删除原文来凑可执行档案。

候选返回的attenuation只说明rule group的序列与重置，不证明实际counter tag、哪些攻击分量共享状态，也不证明同一技能的其它分量相同。附着清单必须逐攻击分量补齐 `element/GU/application_mode/icd.counter_tag/icd.rule_group/目标/证据`，其中`application_mode`只能为`sequence|no_icd|no_application|pending`；上游`attenuationTag`映射counter_tag、`attenuationGroup`映射rule_group，`attackTag`只用于攻击分类，不能作为ICD绑定证据。普通、重击、下落、战技和爆发分类也只能用于检索。数据库缺少`attenuationTag`、实际绑定或共享范围时先保持pending，查项目保存的上游原文和固定版本实现证据，再定向外查并记录采用/未采用结论；字段缺失不能推断为`no_icd`或`sequence`。

读取候选后同时读取回执中的逐段核实工作单或按[元素运行时](../elements/runtime.md#逐攻击分量核实工作单)建立同等记录。工作单应直接引用当前角色、技能、candidate、原始pointer、已知字段与缺失字段，并给出下一步应查的具体问题；不能把“数据库没有attenuationTag”缩写成一句泛泛的“Agent自行核实”。查询结果只在逐字段证据闭合后才能把`pending`改为确定模式。自由文本`source`、任意非空共享组或`status=assumed`都不能单独使缺少实际绑定的候选变成可执行sequence。

原始GU由攻击durability按版本化参考规则换算，不预扣底税；实际底税交给元素引擎一次处理。不要把50写成1.6 GU，也不要拿modifier默认100补攻击字段缺失。字段来源、旧明文映射证据和社区单位参考是三种不同依据，局部就绪不证明当前客户端和完整技能已被实测验证。

## 数值阶段转换

本次`plan_contract`有`attack_source_assembly_contract`且本场景符合其旧C0/有效10级/E/冻结contact范围时，可由共享入口建立新档案。动态v2或其它范围使用下述原路径。按该schema准备一个请求，选择动作检查点中已有能力及精确候选，明确伤害分类/单ATK用途、已有接触档案、偏移/精度、共享计数组、命中与取样声明。提供者与变体由能力一致地取得；无需自己命名命中原型、附着输出槽或串写交叉引用。能力变体不明确或存在旧手工交付时先处理相应缺项，不能让装配器悄悄删除旧命中。

CLI以`equipment assemble-attack --plan plan.json --request attack.json --output assembled.plan.json`保存原生草稿并返回短摘要；省略`--output`仅预览完整提案。GUI可从动作档案列表“从数据库建立动作档案”进入。新档案不会自动接受来源；继续compose并阅读下面的综合确认。已有相同装配可幂等重试，不同内容占同一档案时使用普通编辑流程，不更换ID规避重复交付检查。原地更新须显式`--overwrite`，不同候选轴另存。

快捷入口当前输出calibrated_single_contact_v1并要求contact_profile_id，只支持其旧C0/10级元素战技范围；原路径calibrated_single_application_v2支持精确命座/有效等级的唯一normal/skill/burst槽且不使用contact_profile_id，二者范围不等价。不得重复添加入口已生成的原型、档案或来源；未提供该capability时才按原路径手工组织。

每个受支持动作档案只增加一项`attack_metadata_sources`，字段以本次schema为准：action_event_profile_id、version_line、selection、candidate_id、`application_mode`、`counter_tag`、逐段`verification`、可选逐分量`research_report`、purpose、status、source。`research_report`保存时使用`report_version=1`和`execution_authority=none`，只记录实际查询、来源取舍、剩余未知和数据库补丁计划，不能替代`verification`。`rule_group`由所选数据库候选的`attenuationGroup`只读编译并在回执中审阅，不允许用方案字段覆盖；直接`elemental_sources`才按其schema显式选择rule_group。用途仍是显式单ATK倍率声明；来源文字只作说明，不能代替结构化证据。旧schema的`shared_group`只按兼容入口读取，`attenuation_group`仅属于旧的直接附着来源。rule_group是规则模板，不能据此把全队default攻击合用计数，也不能每次释放新建counter_tag绕过ICD。界面或回执只有规则模板、没有实际攻击绑定时，必须显示“共享身份待核实”，并列出精确攻击身份、已知来源、缺失字段和检索/回填目标，不能用ready或`assumed`掩盖。

该来源独占已有档案的原型和附着输出槽位：

- 原型保留身份、t=0、classic、scaling_stat=atk和明确伤害分类，省略element与multiplier。
- action_event_profile保留application_profile_id，但该附着档案整项不写入原生elemental_model.profiles；系统仅在执行副本生成。
- 同原型不再建立hit_parameter_sources，同附着槽位不再建立elemental_sources；即使数字相同也不能重复交付。
- contact档案、参考契约接受、校准、变体、landed、实际动作、能量和其他来源照常明确。新入口不自动推导这些条件。

一次compose会返回`attack_metadata_compilations`和既有`action_event_compilations`。核对完整来源bundle、单位、参数关系、共享身份及校准后，只接受已有动作档案的`accepted_profile_fingerprint`，再compose。无需额外手抄两个下游来源SHA；它们由同次新鲜资料在私有副本接线。资料ready不等于档案已接受，接受也不会把用途pending升级成confirmed。

首次候选可为null保持草稿；不明确候选或范围未支持时不能输出成功DPS。来源、映射、单位或完整父链改变会使旧确认失效，核对并修订受影响的拓扑、动作、数值或绑定后fresh重算，不机械退focus，也不能无条件刷新指纹。

## 交付与核对

原生只保留综合来源和动作档案，不复制query、内部标准来源、生成附着档案或hit_patterns。检查三个明确动作只生成三个实例、共享ICD未因动作重置、原始2 GU在普通空冰底下仅扣一次税为1.6。该算例检验换算接线，不证明真实角色所有命中或机制。

App和两种Excel应来自同次计算，查看“攻击元信息来源”“动作派发来源”及`result_receipt.attack_metadata`。来源和派发结构固定，修改候选、单位、共享组或动作后回App重算。仍报告dispatch=calibrated、skill/whole_source=partial，产球、天赋、完整脱手多段及未支持机制分别列出。
