---
name: genshin-calculator
description: 原神角色与装备资料查询、动作排轴、元素/能量与伤害计算，使用原生App或CLI并交付App方案与Excel。
---

# 原神拉表计算

用于资料问答、配装比较、排轴和伤害计算。只问资料时直接查证。要计算或交付时，按[实际入口](references/runtime/software-entrypoints.md)选择当前 App、独立 CLI 或 Excel。

排轴要求复杂时先保留原题，随后核对实际采用的角色、武器和套装。查询标为未上线或无法核实上线时，集中告知用户涉及哪些实体，取得答复后使用。已经确认的实体和使用范围可以沿用。技能和数值的正确性仍按资料核对。见[资料查询](references/data/query.md)。

## 拉表原则

- **紧凑排轴和最优 DPS 是高优先级原则。** 用户指定其他目标或固定安排时依其要求。以最早合法衔接为基线，主动去掉无收益等待，比较有依据的改进。施放占用、取消点、切人和下次命中分别安排。等待确有收益时说明条件并核验结果。比较范围和未验证的时序如实写明。见[排轴](references/mechanics/rotation.md)。
- 分清用户锁定项和授权可调项。用户只锁动作顺序时，按授权安排未锁时点。用户给成品面板时直接采用，核清已含的常驻属性。按预算构造面板时保留等式和上限，充能占用输出词条；逐次 Q 核对余额，首轮满能量只用于初态。先让轴合法，再比较不同词条与时序的收益。见[配装](references/workflow/focus/loadout.md)。
- 涉及角色机制、配装或计算时，选定角色后按[附录配方](references/data/character-appendices.md)读完全部可读的 approved 和 pending 正文。核清哪些事实适用于本场景，把它们填进实际规则或事件，再到结果中检查。天赋默认10级，仅已激活且明确加3级的对应技能用13级。人工表的等级或近似值仅作对照。
- 按用户截止统计，否则以末次主动动作施放阶段的最后直接命中为截止。伤害与分母用同一窗口，动作和能量按要求完整检查。见[统计窗口](references/app-mechanics/review/report-window.md)。
- 优先使用能真实执行的来源和规则。发现缺项时补对应输入，改动后重算受影响状态。同一组件只结算一次，结果由引擎计算。现有资料确有缺失时按合同补源；偏离数据库既有事实或用户锁定项须取得对具体差异的批准。公共知识更新走提案、同意和完整新版本。
- 可修问题按[工作法](references/workflow/index.md#遇到失败怎样继续)继续处理。每次向用户汇报，先用通俗中文说清做到哪里、结果是否可用、下一步需要谁做什么。需要用户操作时，紧接着说从哪里进入、点哪个实际按钮及之后会发生什么。细节和错误码放在后面。按[白板交接说明](references/workflow/whiteboard.md#向用户交接)核对计算、审核、同步和保存各自的真实状态。

## 按当前问题读取

| 问题 | 入口 |
|---|---|
| 本次任务、五个可交错工作重点 | [工作法](references/workflow/index.md)；[白板接续](references/workflow/whiteboard.md) |
| 原题容易遗漏、资料摘要丢条件、已算通但任务没完成 | [原题—解释—执行对照](references/workflow/task-evidence.md) |
| 配装、动作、事件、数值或结果检查 | [按当前问题选工作卡](references/workflow/index.md#五张工作卡) |
| 游戏规律、公式、附着/ICD、时序/取样、词条 | [伤害](references/mechanics/damage-formula.md)、[反应](references/mechanics/reactions.md)、[排轴](references/mechanics/rotation.md)、[配装](references/mechanics/artifacts.md) |
| 怎样填写机制、查结果或修复错误 | [App机制适配索引](references/app-mechanics/index.md)，使用当前工具给出的参数合同 |
| 角色/装备/附录与高级字段 | [数据查询](references/data/query.md)、[数据库说明](references/data/database-readme.md)、[角色附录](references/data/character-appendices.md) |
| 原生运行/恢复或软件CLI | [DSH运行](references/runtime/native-agent.md)、[软件入口](references/runtime/software-entrypoints.md) |
| 联网与上下文 | [研究账](references/data/research.md)、[按需复用](references/workflow/context-use.md) |
| 保存App与Excel | [同源交付](references/outputs/app-and-excel.md)、[Excel](references/outputs/excel.md) |

完整资源见[资料索引](references/index.md)。角色附录按默认配方读取，其余只取当前决定需要的部分。按实际分页读齐所需内容，复用同一资料版本，用户需要的明细可直接展开。篇幅和读取次数由任务决定。
