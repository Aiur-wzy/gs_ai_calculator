# 逐击目标条件与固定装备来源

在转换阶段，只有本次capabilities提供 `upstream_target_state_contract` 和 `mechanics_contract.target_state_conditions` 时使用。动作检查点、装备、面板及目标场景应已确定；条件收益由目标状态决定，不要预先把每跳答案写成静态Buff。

## 数据库自动来源

读取相关套装完整中文原文及结构证据，核对当前实际四件套和给定面板：两件常驻已含，四件条件暴率未含。首次投影仅支持普通可冻结目标参考模型下的经典命中。当前精确上游的冰套经典两段条件可用；这不是整能力或所有套装的通用解释器。

原生 `effect_bindings[].upstream_recipe={schema_version:1,scenario:...}`，`parts=[]`。从当前 `upstream_target_state_contract.default_scenario` 获取字段，不混入旧爆发来源的距离字段或 `ability_refs`。scenario指定 `equipment-target-state-classic-v1`、固定单人单目标Monster、经典命中、常驻included、条件暴率not_included及独立status/source。默认pending，不能凭确认指纹改成已经核实。

compose会重新装配完整来源，返回 `upstream_compilations`、全部分支、来源指纹和 `required_confirmation.accepted_assumptions_fingerprint`。检查原始数值、分支与实验参考假设；范围确实适用时由Agent记录依据，接受本次指纹再compose。不需要额外请求用户审批。来源或解释变化后复核；未知结构不能删掉后继续。

`projection_status=ready`仅指当前分支投影；`whole_source_status=partial`、`coverage_certificate.source_complete=false`必须保留。当前Moon/Star分支未解释，来源拥有者的月曜/星烁命中会阻止完整计算。不要把图中两条收益可用写成整个来源完整支持，不要复制编译的fixed来源到原生手工模型造成重复交付。

## 运行含义与必要微调

当前能力另提供 `general_profile.profile_id=target-state-observation-v2` 时，可由Agent把已核来源材料化为通用固定条件收益。使用 `damage_scope=declared_damage_systems`，显式列出classic/transformative/lunar/stellar中的实际范围及需要的recipient_ids，不能把旧数据库经典分支证书当成星/月全支持。谓词可核对当前冰/水/火/雷/草/冻结元素底和已支持状态；可用种类、条件树和输入字段以当次schema为准。

收益必须是实际注册表支持的有限常数，按真实乘区填self或enemy；减抗/减防属于enemy。相同命中前样本供普通、星/月和反应伤害按各自公式消费，伤害公式不支持的收益不会因为列入damage_systems就生效。条件HP/DEF/ATK/EM只属于本击最终面板，可在v4属性回执中核查，不进入可分享普通面板或下一条转换。条件ER也不能直接变成伤害或回能，须走明确资源链。

固定来源没有虚构持续时间、触发动作、层数或initial_state实例。每次命中前，在自然衰减/到期和此前有序事件之后，读取不可变目标样本。此次附着尚未发生；同刻下一事件可以看到上一事件的改变。角色面板快照不冻结目标条件，无附着或ICD拒绝的实际命中仍检查条件，未命中不采用收益。

Frozen元素底与ElementFreeze状态是两个概念。首次参考模型只对普通可冻、零抗性目标，将正冻结量F投影到两者；不涵盖先天Frozen、盾、免疫怪或一般状态系统。冰共鸣与其他来源不因字段相似而自动启用。

确有已冻结的例外时，`rotation_tuning.buff_hit_matches[].effect_ref={fixed_source_id,effect_id}`，与旧buff_id引用二选一。先compose取当前basis，按通常adjust/replace流程确认。include可覆盖已知false，并留下自动值及人为覆盖；不能绕过未知状态、提供者、伤害系统或命中筛选。通常保持微调为空，不用include补缺失的目标模型。

手工固定来源只在已有有据规则确需表达时使用：原生 `mechanics_model.fixed_effect_sources` 按capabilities有限schema声明，唯一来源分项使用 `rule={kind:fixed_effect_source,fixed_source_id}`、`delivery={kind:fixed_effect_source}`，owner和source_id须对应provider及binding_id:part_id。旧freeze-reference合同仍只接受其暴率常数；新general合同按实际注册表及self/enemy范围接受常量。不执行文本或任意脚本；部件只用always条件。

## 复核与交付

查看 `result.fixed_effect_sources`、`target_condition_trace` 和 `result_receipt.target_conditions`。逐击核对取样时点、目标层、automatic_value、override、applied、reason；null代表未知或不适用，不能解释成false。未知导致结果pending时保留诊断，不发布完整DPS。

capabilities公开条件检查次数和单账本字节上限；超限会停止并返回pending，而非截掉后续记录继续给完整伤害。缩小需验证的输入范围或降低重复声明，不能把前缀当完整轴。

正常App和Excel导出包含只读“目标条件收益”。活公式可重算支持的面板数值，但固定来源参数、条件树及目标流水不可在Excel重演；修改动作、附着、目标、装备或来源后回App重新生成。只验工程序列不等于真实队伍机制或DPS已验证。
