# App结果、覆盖率与交付状态

请求正式计算后读取同源`completion`（`calculation-completion-v1`），区分诊断文件已生成、统计窗完整、必需验证完整和正式接纳。只有实际支持的当前结果可发布最终数字；`compose ready`／CLI成功／文件存在均不能代替。新[统计窗口](report-window.md)与完整执行周期分开维护，窗口外伤害只作审计，不混入总量。

## 按需读取逐击解释与特殊状态

当前能力提供`damage-explanation-v1`时，用实际`explanation_ref`定位该击的来源、实际取样输入、Buff／修正项、公式检查点、可用的参考属性折算及最终值。解释只记录公式真实消费的属性，不补造整个人物全属性；取样时刻、实际伤害时刻和其他提供者分别核对。基础加算是已有基础值的一部分，不能把不同提供者的倍率直接相加，参考为零或不适用时不强算等效倍率。固定值覆盖保留自动基线与有效补正，外部补充无面板时不编公式。

沿已有审查接口读取`view="damage_explanations"`，先取默认`identity`／`outcome`，需要时用`sections`定向取`panel`、`modifiers`、`formula`、`normalization`、`evidence`；仍按事件／动作／来源筛选和分页。原生用当前公开的`gs_review_result`／`gs_review_result_batch`，CLI用`equipment review`／`review-batch`，先读真实合同，不把整份解释表、属性账和流水一次塞进上下文。解释一致性不证明角色机制正确；旧结果缺解释或可选证据partial须说明范围，不能从总伤反求缺项。

`special-state-timeline-v1`把星超导领域累计与最近完成结算的增益分别按真实业务时钟切段；`special_states`视图按区间相交查询。累计清零、领域到期与结算增益到期不同；不手填每段层数，也不因图中有持续条就断言所有命中获益。该合同不自动完成所有目标条件：双冰等仍核对当前输入和逐击回执，改轴后人工条件需重新校验。

时间轴可预览已明确声明时点和来源的星／月能力直伤，标记待计算，不预测层数或伤害。计算后核对实际请求、命中和真实父技能／组件；一次声明只计一次，未接纳、未命中、抑制或未验证不能称为已成功出伤。伤害分类“星超导”不是原技能身份；完整性检查仍需定位E／Q和实际后台组件。

## 6. 同源交付与外查记录

交付原生方案及其 result/receipt，保存重开并复算。给定面板、已含常驻属性、武器/精炼、套装类型/件数均来自同一plan，条件效果仍需审核。v3 机械流水与采样已在结果中，来源审核使用 binding，报告无需再抄逐击时段/次数表。

需要同源活公式 Excel 时，调用 `equipment workbook --plan ... --output ...`，读取 support 的可编辑范围。验证一个受支持面板/倍率数值的真实公式联动；动作、次数、层数或match编辑需重新执行生成。仅局部覆盖时明确范围。旧 `equipment export --format xlsx` 仍是结果快照；手工 Excel 路径见 [Excel 模式](../../outputs/excel.md)。

星/月直伤的期望值和暴击值使用引擎实际交付的 result_weight 与次数，不能漏乘权重。多人权重若依赖本次配装，工作簿修改相关输入后会提示重新回 App 计算；不要把旧权重当固定游戏常数。零伤事件仍保留自己的命中、资源和触发语义，不因本次伤害为零而删除。正式结果导出要通过原始输入、结果指纹和必要证明的一致性检查；裸结果快照的检查范围不能冒充完整方案接纳，也不能把旧结果重新签为新结果。

### 一份事实记录，多个交付视图

各工作重点在分实体说明和索引中记录来源、外查及问题。建立动作检查点、事件拓扑和原生plan后，再初始化audit并迁入已有采用记录；说明文件保留事实及audit记录ID，不长期双写。此后的问题出现时及时追加，不等到预算尾声重建：

```text
workflow init --plan plan.json --audit audit.json
workflow export --plan plan.json --audit audit.json --output-dir delivery
```

audit 的结构以 `workflow_contract` 为准：records 分 lookup/issue/decision，scope 分 included/excluded/pending。lookup 保留本地查过什么、gap_type、source_refs、实际 adopted_refs 或 not_adopted_reason；issue/decision 的 description 记录问题或语义取舍。来源的原测试版本和定位写入 source_refs，不把查阅日期当当前版本复验。记录引用规则/动作/字段，机械 trace 无需复制。

导出自动生成issues.json、lookups.jsonl、web_findings.json、rotation.md；最后一项是来源与范围报告，不能覆盖动作检查点。输入变更后标stale并保留旧记录；字段转换修正不改变动作检查点，游戏含义改变则按影响生成新的事件拓扑或动作检查点修订，随后复核并运行`workflow bind --plan plan.json --audit audit.json`再导出。bind只是重新绑定，不自动证实来源或补遗漏；不要改指纹伪装审核。机械过程与来源记录通过同一输入指纹联系，不把audit当第二份执行轴。

gap_type 区分 missing（本地缺失）、not_found（已有但未找到）、not_adopted（读取未正确采用）、interface（获取难用）、conflicting（证据冲突）、not_required（本次不需要）。是否适合补系统或保留外查写 decision/description 即可；未知采用位置不补猜。

外查可用于补充机制事实。每次记录：实际问题；已读本地端点/资料及缺口；URL、查阅时间和适用版本；采纳事实/未解决项；落入哪个动作、参数、规则或假设；建议“补入系统”“保留外查”或“证据不足”及理由。区分资料确实缺失、已有但未检索到、已读取但未采用、接口不易用；不自动将外部文字写成公共 approved 规则。

当前独立实验和benchmark衡量Agent、Skill、App、数据库与引擎组成的整体系统，可以查询并采用本队成型轴、给定配装总伤／DPS、整轴动作次数、实战成绩、人工表、视频和相似答案。记录来源、版本、输入口径、采用位置及App复算或外部补充状态；找到现成答案不是污染事件，也不会自动获得数据库审核或引擎执行资格。人工表与视频不是严格标准答案，不要求本方案匹配其等级、装备、动作或环境，也不以全队总DPS接近作为通过标准；解释可比与不可比部分，其他未覆盖机制单独检查，没有统一5–10%通过线。只有题面明确声明为历史封存的答案隔离实验时，才沿用该题面的旧限制，并保持其原始记录不变。

## 覆盖率必须说明测量对象

旧resolved core的覆盖率按时长跨度/轴长计算；当前mechanics结果可能提供hit_projection并以actual_uptime=null说明没有实际时长值，另有持续Buff状态切片及逐命中资格。这三者不能互相改名。命中数、伤害贡献、持续时长分别核对，不能由表名推断。

## 快照和完整输入窗口

旧resolved的snapshot=true读取cast_t整组Buff，连敌方减抗也一起锁定，是兼容入口的表达限制，不是游戏快照通则。需要分域采样时读[属性与取样](../execution/attributes-and-sampling.md)。结果汇总显式输入窗口的实际执行前缀；多个游戏轮必须输入完整动作，不能自动复制首轮或把检查窗口叫第一轮。
