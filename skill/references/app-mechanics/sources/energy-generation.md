# 产能资料：精确物品与参考单位

仅在本次capabilities.runtime_contract提供energy_generation_profile时使用。它将一处GenerateElemBall引用连接到同快照Material原文，并在有限参考模型中读取数量、元素和粒子/晶球能量类别。查询本身不生成轴内产球事件。

## 先取得并保存资料

从相关角色已有definition回执的references中选择有原文依据的GenerateElemBall.configID引用，使用其真实legacy reference_id；references是回执字段，不是独立公开action。不要用definition ID代替，不猜物品ID，不全库遍历寻找答案。

```json
{"action":"energy_generation_profile","origin_reference_id":"替换为本次返回的64位小写引用SHA256","version_line":"release"}
```

```text
db runtime --input energy-request.json --output facts/character-energy-source.json --summary
```

把完整回执保存在角色自己的事实小文件，索引保留请求、快照和来源指纹。可将已经明确的独立查询放进最多8项batch；依赖发现结果的请求留到下一批。后续只按问题读取小文件，不重复抄写原数值或全文查询。

回执保留完整父节点、modifier所属Ability、选定操作、Material原行和原始pointer/SHA。configID属于这条独立Material关系，不能因为是数字就沿Gadget表查询。旧legacy仍标external_asset不等于新资料失效，不能擅改旧图。

## 分清可算单位与实际触发

分别记录source_status、reference_projection.status、whole_source_status和execution_support。局部ready说明该叶的引用和单位可读。随机父内4或5粒两个叶各自可查，仍须查实际分支概率和父条件。两分支各50%时，可在明确触发时刻提交 4.5 粒平均数量。固定自动来源只接管已核实的固定产球，概率分支用手工或机制派生资源表达。

参考模型来自固定开源模拟器的局部实现，首版只支持显式正整数数值、普通元素、标准同异色能量对3/1或9/3，以及单位整除的1至20个产物。系统已给可用count/element/particle_type时读取其回执，不再自行从baseEnergy硬除3。该类型是现有能量模型投影，不证明逐实体收球监听；20是参考实现范围，不是游戏普遍上限。

缺字段、动态表达式、非标准用途、额外有效itemUse、非整商或未知元素等保持pending，不能默认ratio=1、零收益或无色。原始空槽只按当前模型已审范围解释。当前原文可能缺useOnGain；查询不补true，也不声称当前资源可原样运行在参考服务器上。

## 转换为轴时

仅有本资料接口时，实际事件仍按[动作与能量](../actions/rotation-energy.md)及现有声明式资源规则表达；优先复用一条规则和明确的来源所有权，避免逐动作重复输入相同事实。数量来源可引用本次投影，实际触发须另核完整父链、变体、命中、前台、目标与掉落条件。miss与附着ICD不触发附着是两件事，不能混用。

产出时点和收球时点分开，旅行时间不由Born字段或技能结束时间猜出；到账按实际前台和各角色ER计算。不能把baseEnergy写成直接回能，也不能让生成者自动成为唯一收球者。运行时另提供下述执行来源契约时，才使用其字段自动派发；没有该契约的版本仍按上面的手工来源路径处理。

material_index_upgrade_required表示库需升级，现有旧查询可继续；不解释成角色不产球。完整性或快照错误应停止使用该来源。预算超限时定向读原文并记录限制，不删掉未知条件制造ready。资料变更定向复核来源；动作可按授权调整并重新校验受影响的事件和能量账，不要求机械退阶段或冻结整条轴。

## 命中自动产球（仅在接口提供时）

本次`plan_contract.energy_generation_contract`存在时，使用其`default_source`和schema构造原生`energy_generation_sources`。先完成[单次动作档案](../actions/event-profiles.md)及其来源接受，再绑定一个档案与一个精确产球引用。角色、技能、变体和实际命中从父档案取得，不重复填count、element、particle_type、selector、资源rule或逐动作ownership。

1. 保留默认未知场景，逐项核对目标、允许掉落、窗口初态、全队存活、同批拾取与外部控制变化。`hitlag_policy=zero_hitlag_reference`是明确采用不计受击停顿延寿的参考模型，不是声称真实战斗没有停顿；无法接受该近似则保留pending。系统会核对已知冲突，场景声明不能覆盖冲突。
2. 一次填写命中→产球、产球→收取两段时间、记录精度和依据。明确零可填0；缺失仍为null。生成时在场条件与收取时前后台分开，不通过移动到账时点挽救不合法的爆发。
3. 首次`accepted_fingerprint=null`，compose后阅读`energy_generation_compilations`中的完整来源、参考证书、父命中映射与问题；仅支持缺少接受这一项时，采用实际`required_confirmation`再compose。未知结构和随机叶即使单位ready也不能强行接受。来源确认不是新增用户审批，也不提升原本声明的事实等级。
4. 原生不再添加同源产球或收取。迁移已有手工`energy_spawn`时显式列`replaces_spawn_ids`，核对系统展示的关联收取；旧事件保留在原生，执行副本接管。已有共享手工particles规则须处理其完整依赖；不能为通过校验随手删其它动作依赖的规则。
5. 查看实际`energy_generation_summary`及能量流水，区分计划命中、未命中、不在场、未验证、已产球、已收取与在途。附着ICD阻止附着不等于未命中；不能用计划球数代替实际到账。需要多轮仍输入完整多轮动作，系统不补轮。

当前有限触发适配要求零命、受审单次战技初始命中、同提供者固定变体、明确空初态与严格来源隔离；不执行完整Unique、混合变体、实体短按、随机或逐粒收取监听。隔离界由系统依据来源生成，Agent不可手填冷却绕过。`calibrated_reference/whole_source_status=partial`应出现在最终边界说明，不能用单个角色成功概括全队自动化。

改动作时点或增删同档案动作后，系统重新检查实际命中映射和间隔；语义不变不需要重复抄来源确认。来源、父档案语义、场景、校准或迁移清单变化则重新审阅。按[完整交付](../../outputs/app-and-excel.md)一次生成App与Excel，检查“产球来源”页及`result_receipt.energy_generation`；该页只读，Excel不重演能量队列。
