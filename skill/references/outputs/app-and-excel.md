# 按目标一次计算交付App方案与Excel

只在本次capabilities提供 `equipment deliver` 时采用。输入必须按实际完整性、绑定、动作／资源与通用规则严格接纳；不要求机械依次切换五个工作重点，交付不替代真实校验，也不能把`diagnostic_attempt`发布成正式结果。

```text
--data-dir DATA equipment deliver --plan frozen.plan.json --output-dir delivery/candidate-r1
```

全局参数接在实际软件入口之后、子命令之前。命令仅一次fresh装配/执行，返回短回执；完整来源和流水落文件。不要再为同一最终候选依次执行calc、export、workbook生成重复产物。探索阶段只需数值时仍可calc；只需一张公式表或旧版无此能力时用workbook。

用户只要App方案时，在当前capabilities提供roles选择的版本使用`--roles native_plan,calculated`，不为了保存App文件强制生成Excel。默认仍为既有四件；CLI选项名与manifest role分别按实际回执解释，不能猜名称。未算成功可先保存原生输入或候选输入草稿，明确没有正式计算资格，不能把草稿塞成calculated。

## 查找和使用文件

以回执及输出目录index.json指向的生成代为准，读取manifest中本次所选role的路径，不按目录修改时间猜“最新版”：

| role | 用途 |
|---|---|
| native_plan | 可载入App的原生方案；保留输入和来源确认，缓存为pending/null，打开后仍fresh核验 |
| calculated | 版本化历史计算包；内部calculated保留完整plan/report/result/result_receipt/display_context等，按问题定向读取 |
| result_workbook | 已计算结果快照Excel，便于核对明细 |
| formula_workbook | 同次活公式Excel，可编辑范围和重生成要求见support |

index、manifest、完整计算包都不能当原生方案导入。旧calc --output只保存结果及部分回执，也不是完整calculated。不要手工把input_view落成原生文件：它缺schema必需缓存字段；使用工具交付的native_plan。

两类Excel使用同一数值和中文目录快照。完整包是历史记录，并不代表当前DB；来源变更后重新计算。按实际输入窗口解释伤害，多轮仍要输入多轮动作，不把附加循环检查当作自动增加伤害。

正式结果若携带Agent材料化、统一覆盖或D2临时实体证明，各独立导出入口必须先重新验证result/receipt和所有权，再保留完整审计：JSON保留回执，CSV在首个数据行携带一份完整审计JSON，XLSX和公式工作簿增加只读“计算审计”页。`equipment deliver`仍只生成上表列出的四个role；需要CSV时使用独立导出入口，不应为此伪造第五个deliver产物。

## 状态与验证

文件完整与轴成功分别检查。index_committed=true只表示索引指向已发布产物；硬失败或已执行前缀只能留下`diagnostic_attempt`并返回非零退出码。检查`calculation_acceptance`、calculation_status、rotation/mechanics、完整窗口标记与DPS；不可接纳时不得生成或引用正式总伤、DPS和成绩Excel。null不补零，未执行伤害不计入总轴。unproven循环或partial公式编辑能力也不应被误读为文件缺失。

App 候选回流时，先核当前计算与用户目标，再准备供用户审核的精确候选。用户确认同步后，查看实际应用回执。保存正式文档是单独操作，文件完成以相应回执为准。草稿保存、计算接纳、审核、同步和文件分别报告。若失败，按[恢复路线](../workflow/index.md#遇到失败怎样继续)从实际状态继续。

### 已验证全未命中的零结果

先按[当前入口](../runtime/software-entrypoints.md#三种入口的资源根)读取伤害报告合同。原生及连接 App 的读取使用 `capabilities` 的 `/damage_reporting_contract`，独立 CLI 的完整响应使用 `data.plan_contract.damage_reporting_contract`。支持 `verified-empty-damage-v1` 时，完整输入窗口内非空、计入报告的实际接触全部明确未命中，可由引擎产生总伤和DPS均为数值0、`hits=[]`的结果，以及只读 `damage_reporting` 覆盖证明。证明覆盖本次显式输入窗口，实际动作、扣能和收益激活记录仍保留，稳态另核。

通过原生入口可交付JSON、结果Excel、公式Excel及四文件deliver。两表的“伤害报告证明”页只读；公式support中的 `zero_damage_projection=readonly_verified_execution` 表示不在Excel重新判断命中，既有来源和提供者属性锁仍适用。该结果不支持CSV或独立空resolved模板导出；保留原生方案和完整回执，不补造零伤命中行。

空草稿、仅隐藏伤害、独立无伤附着及失败/未验证前缀不满足此契约。未知 `landed` 不能改成false来逃过pending；有依据的未命中声明仍须实际执行核验。动作或接触改变后回App重算，不逐击填写或修改证明、统计和指纹。

既有来源未确认、无法形成完整计算结果或任何必需产物生成失败时，不会以半套文件冒充完整交付。查看短错误中的提交状态和路径；unknown不能当未提交，不盲目重试覆盖。完整错误和未支持范围保留，不删机制来凑导出成功。

正常用新候选目录。--overwrite仅替换同协议index，旧生成代保留；不覆盖未知用户index，不清理孤立代。产物超限时不手动删来源/流水或改哈希，记录限制后选择已有单产物入口，并明确尚未完成全套交付。

交付包含Excel时继续执行[Excel检查](excel.md)：核对逐击、公式缓存、中文、来源和受支持数值扰动；只要App文件时不额外生成工作簿。文件同源不等于游戏机制已证实，工作簿也不自动重演元素或能量状态机。

Windows入口继续使用已有shortcut create，为索引选中代的native_plan生成快捷方式，使用本次data-dir；不要指向计算包。旧快捷方式固定引用旧代，发布新代后需重新生成，不能误称旧链接已跟随新index。生成及校验不要求自动打开用户桌面。
