# 上游来源如何接入App执行输入

## 数值阶段的原生转换

提供 `passive_source_contract` 时，固有天赋使用[独立passive_sources](passives.md)，不写入装备 `upstream_recipe`。角色引用、显式突破与精确固有记录共同定位来源；App在同次fresh装配中核对实际攻击父链和固定面板口径，再生成有限规则。查询ready、解锁数值符合与App执行ready分别报告。

提供 `upstream_reaction_benefit_contract` 时，普通扩散的装备收益走[反应收益来源](reaction-benefits.md)。沿实际装备装配，一条来源产生常驻加成及冰水分别刷新的减抗；不走爆发能力映射，不手写逐次反应时窗，未知初态/范围/命中顺序保留待补。

当前提供`attack_metadata_profile`和`attack_metadata_contract`时，优先按[精确攻击来源](attack-metadata.md)按需读候选；实际动作档案与来源资格满足时，以一条综合来源接入，省去参数位置、元素/GU及衰减配置的重复手抄，不以numeric_model标签为门。候选原文仍需审查，时点、变体、接触和共享身份不由此自动证明。

当前能力提供 `upstream_target_state_contract` 时，固定装备的逐击目标条件收益走[目标条件来源](target-conditions.md)。这类来源不使用下面的爆发触发场景；局部经典分支ready与整来源partial分开。保留未执行分支与范围证书，不要求Agent逐击手填暴率。

capabilities提供`hit_parameter_source_contract`时，按[技能倍率来源](hit-parameters.md)将已有命中或原型绑定到精确参数，避免重复抄数。显式有效等级和level-bound合同分别核对精确等级/加级证据，不设统一C0/10门；来源可读不证明参数用途，仍须核对完整中文原文与属性。它不替代动作规划或创建命中。

对于需要执行且已支持的装备效果，可在原生v3对应 `effect_binding` 使用：

```json
{
  "schema_version":1,
  "scenario": {
    "profile_id":"equipment-effects-fixed-single-player",
    "profile_version":1,
    "accepted_assumptions_fingerprint":null,
    "single_player_fixed":true,
    "all_targets_monsters":true,
    "single_unique_provider":true,
    "no_external_state_changes":true,
    "max_party_distance":20,
    "static_policy":"included"
  },
  "ability_refs":[{"ability_id":"本地爆发能力ID","skill_id":10019}]
}
```

该对象放在 `upstream_recipe`，不要放 `extensions`。owner须是实际所选装备的affix；parts保持空，原始mechanics_model不再重复交付这一来源。受支持的结构由compose从当前DB重新编译到报告中的只读分项和规则，后续calc/export/workbook仍用正常入口。

先按实际情况填写profile场景并compose，读取 `upstream_compilations` 中的所有诊断与精确语义假设。如果当前轴符合所列前提，可由Agent记录采用依据，并把本次 `assumptions_fingerprint` 明确写入 `scenario.accepted_assumptions_fingerprint`，再compose；不要无条件填全部true或照抄旧确认。场景、事实或动作变动时在授权内修改相关工件并重验后继，不机械回退focus，也不暗改用户锁定动作。未知结构仍pending，不能删掉其中未知链后声称整来源支持。

动态爆发触发需要：装备提供者绑定数据库角色；`ability_refs`将其各个本地爆发能力映射到同角色数据库 `slot=burst` 技能。基础耗能、CD、次数须与对应资料一致；特殊改耗能/CD机制未支持时登记缺口。引擎只在输入动作真正通过门槛后触发，不从伤害事件或技能名称猜施放。


## 初始面板与评审

如果同时提供 `action_event_contract`，受限校准档案可以按动作检查点和事件拓扑自动生成命中模式，详见[共用动作档案](../actions/event-profiles.md)。它复用本页的来源编译结果，不把资料查询ready解释为完整角色派发；部分覆盖和明确校准依据须留存。

给定初始面板包含两件套等常驻属性的约定保持。普通面板不能表示“仅爆发/战技/下落增伤已含”：本轮这类来源选择 `included` 时保持pending；只有明确该分类部件未计入当前数值，才用 `execute` 单独执行。不能重复叠加，也不能用全伤害加成替代分类加成。

检查正常结果的 `upstream_semantics`、source指纹、逐项来源和机制流水；`assumed_in_scope`表示受限适配假设下可执行，完整游戏语义尚未验证。自动规则、手工规则、已含面板三种交付不能重复。动作和命中仍由动作检查点与事件拓扑提供，动画、召唤物和一般全局变量不因导入原始配置就自动实现。有限元素层可按[元素运行时](../elements/runtime.md)消费有证据的附着档案，自动判断蒸发/融化及v2普通冰水原目标扩散；数据库可提供经确认的伤害序列，但不自动推断GU、命中时点或场景是否进入Alter/Star。连续冻结需要独立参考规则集、契约确认及接触证据，结果保留实验参考等级；旧v1/v2冻结、传播及其他未支持范围保持pending。工作簿中的编译来源数值为固定来源；更换装备、源规则或元素状态输入后回App重算并重新生成。

## 静态命座参数进入已有命中

当 `plan capabilities` 的 `hit_parameter_source_contract.level_contract` 提供 `level-bound-parameter-v1`，可对已有命中/原型使用该显式新契约：`selection.base_level` 默认10，实际命座加级由数据库关系读取；不要填写 `talent_level:13` 代替证据。以当前 `hit_parameter_compilations.required_confirmation` 同时确认快照、参数节点与完整 `expected_level_fingerprint`。省略原生命中的手填倍率，保持倍率用途、属性及依据明确；修改命座、基础等级或加级依赖后重新核对。

静态有效等级就绪只批准读取该级原始参数。其它命座改动、动作通道、次数及触发没有随之自动执行；旧攻击/周期/收益来源编译器的零命资格不能因此绕过。未知加级关系保留待核实，不采用候选等级。可从App预览和Excel“技能参数来源”页审查贡献与原文。
