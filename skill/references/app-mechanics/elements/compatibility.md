# 元素与数值计算的兼容入口

## 连续冻结实验参考模型

只有 capabilities 明确提供 `gauge-freeze-reference-v1` 时使用。先将 `elemental.reference_contract` 的完整来源、假设、指纹存入本地机制说明；这是连续冰水风参考计算，证据等级固定为 `experimental_reference`。原始观测、第三方模拟器和工程约定分别列出，确认采用不把它们升级为游戏实测事实。Agent可以复核后接受契约，无需逐次向用户询问，但必须在报告中说明模型选择及未支持项。

范围为普通单目标、明确可冻结、零冻结抗性、无环境附着及hitlag。公开初态只能是从未预热的冻结状态；底下可以明确已有普通冰或水。沿用profiles、附着ICD、固定EM与数据库扩散伤害来源，在模型中增加：

- `ruleset="gauge-freeze-reference-v1"`。
- `reference_acceptance={contract_id,contract_fingerprint,acknowledged:true,source}`，前两项逐字来自当前capabilities，source记录本轴采用理由；不从旧文件抄过期指纹。GUI中展示契约后可填写理由确认。
- `target.initial_state={ordinary_aura:null或明确冰/水底,frozen:null,freeze_history_seconds:0,status,source}`，不再同时填写旧 `initial_aura`。
- `target.freeze={freezable:true,resistance:0,status,source}`。未知可冻结性或非零抗性不能伪装成该输入。
- `contact_profiles=[{id,freeze_interaction:"none"|"unknown"|"may_reduce",status,source}]`。
- 对每个命中描述、模式或无伤害附着事件声明 `contact={kind:"none",status,source}`，或 `contact={kind:"profile",profile_id}`。这里none只表示有依据认为该接触不会碎冰或削冻，**不是“没有附着”**；不能为通过校验统一填写none。

接触检查包括首次造成冻结的一击、无附着命中、被附着ICD拒绝的命中和不纳入伤害报表的命中；未命中不算接触。需要相关接触证据时缺失或may_reduce将pending。碎冰、削冻和其他元素仍未支持。当前能力提供target_state_conditions时，按[目标条件来源](../sources/target-conditions.md)使用有限逐击条件暴率；其他布尔条件不自动视为已从元素底验证。

引擎递推普通底、独立冻结量F、冻结历史x及自然到期；新旧冻结量取max且不清空历史。风按水→冰→冻结消耗，同父命中的冰底与冻结层聚合一次冰扩散，逐层消耗仍留档。水与冻结可生成水、冰两种扩散。伤害仍采用按角色/目标/元素区分的数据库序列，未套用其他模拟器的跨角色0.1秒限制；这是显式工程约定。计数敏感、跨元素互相改变伤害或消耗资源的多反应包不自动猜执行顺序，返回pending；只有经校验的幂等收益路径可执行。

用 `elemental.trace` 的 `reactions[]`、`layer_consumptions[]`、`package_id` 与 `elemental_state_transition` 查看同一步多反应、逐层消费和自然状态变化，不能把消费层数当作额外出伤次数。新语义ID形如 `父ID:reaction:swirl:cryo`、`父ID:reaction_damage:swirl:cryo`，以实际流水为准，不沿用v2的 `:0`。状态含ordinary_aura、frozen、freeze_history_seconds、freeze_decay_gu_per_second；无普通底不等于未冻结。自然到期在同刻用户动作前处理，不需要Agent虚构“解冻动作”。

App效果视图展示反应包与自然状态变化，伤害视图只列实际伤害。结果与Excel均保留参考等级、契约和接受记录；Excel的“元素状态流水”为只读结果，改变动作、初态、接触、规则契约或附着档案须重新计算、导出，不在Excel中手改状态后称作新模拟。



## 有效兼容和历史边界

旧resolved v1/v2仍是数值core入口，手填reaction只选择伤害分支，不证明元素底或ICD已模拟。特殊反应model v1与能力直伤profile v1不是同一版本：前者仍有显式兼容绑定，后者仅识别迁移缺项，不执行。旧static_layer_bridge与benchmark保持历史，不作为当前原生操作推荐；未重跑不能声称已迁移。
