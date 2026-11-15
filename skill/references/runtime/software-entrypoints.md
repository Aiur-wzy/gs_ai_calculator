# 软件能力说明

> 2026-10-28：活动数据库写入口已经弃用。能力清单仍保留旧命令名用于兼容诊断，但`lifecycle=deprecated`、`available=false`时绝不能调用或把错误解释为临时故障；数据库内容变更必须走带来源提案、用户同意、完整候选发布与迁移。

> 软件 = 详细化自由排轴拉表（ADR-0003 与 Excel 模式划清边界）。当前提供App内原生DSH Agent与CLI领域接口；原生也可使用实际DSH工具开放的文件、Shell与公开CLI。它们复用App领域服务，普通文件权限不等于候选同步或数据库事实纠错批准。
> **能力清单以运行时 `capabilities` 自描述输出为唯一数据源（ADR-0002）——本文件不复制命令细节，只教获取、解读与组合。**

## 1. 按入口获取能力

```powershell
py -3.12 -m genshin_app capabilities
```

在独立 CLI 中用此命令查当前版本。完整响应的 `data.commands` 给出命令的名称、参数、输出和示例。已在同一版本查过的合同可直接复用。

已知问题范围时优先定向发现：`capabilities --command "equipment edit"`读取一条命令，`--section runtime_request_contract --pointer /actions/fragment`读取一个action。section和command不混用，pointer相对选中范围；返回的local_references补齐本地schema引用，不能忽略未解析／外部引用。它只是能力说明，不代表当前原生会话已经取得工具或写权限。

### 三种入口的资源根

| 当前操作 | 查询注册表和作者字段 |
|---|---|
| App 原生工具 | 当前候选为原生方案时，`gs_read_context` 读 `resource="capabilities", pointer="/engine_v2"`。`plan_schema` 可查外层字段。用 `{"resource":"capability_index","pointer":"/plan_contract/native_details/authoring_sections"}` 定位动作、机制和效果绑定；完整作者 schema 在目录 `native_details.references.expanded_schema`。 |
| CLI 连接当前 App 候选 | 用 `agent candidate read --request 请求.json` 加实际连接与会话参数。原生候选可用 `{"resource":"capabilities","pointer":"/engine_v2"}`。详情见[输入编辑](../app-mechanics/input-editing.md#三种编辑方式)。 |
| 独立 CLI | 完整 `capabilities` 响应的 `data.plan_contract.engine_v2` 和 `data.plan_contract.schema`。定向查注册表用 `capabilities --section plan_contract --pointer /engine_v2`，返回的 `source_pointer` 标明原位置。 |

原生或连接 App 的读取指针相对所选资源根。查不到时，先列该资源根或从能力目录复制实际指针。白板或原生表示能读哪些详情，以当次目录及资源为准。

PowerShell 读取时优先用 Python 的 `json`，或支持该参数的 PowerShell `ConvertFrom-Json -AsHashtable`。能力包可合法包含 `ICD` 与 `icd` 等大小写不同的键；默认 `ConvertFrom-Json` 对象转换可能拒绝它们，不应据此把合法 JSON 判为损坏，也不要改键名破坏契约。最小读取示例：

```powershell
py -3.12 -m genshin_app capabilities | ConvertFrom-Json -AsHashtable
```

若当前 PowerShell 没有 `-AsHashtable`，将原始输出保存为 UTF-8，再用 `json.load(open(path, encoding="utf-8-sig"))` 读取；复杂脚本写入 `.py` 文件执行，避免内联引号和字面 `\n` 造成额外错误。

长轴计算、封口及首次读回可能各耗时数分钟。用原生 App 工具时继续等待真实完成回执。通过 DSH 的 PowerShell 启动长 CLI 命令时，如当前工具公开支持后台作业，可使用该入口并回读原作业。收到仍在运行或结果未知的回执，先查同一作业、候选修订和已保存文件，再决定是否重试。

### 多次调用时复用一个 CLI 进程

当前版本若提供 `call_session`，可用 `capabilities --section call_session` 取得持久调用合同，用 `capabilities --command session` 取得启动参数。它让一批查询或修改共用一次启动校验；仍使用普通 CLI 的处理器，不启动模型或 GUI。一次性命令继续可用。

在对应入口及实际 `--data-dir` 后启动 `session`，保持 stdin 打开；收到 `genshin-cli-session-ready-v1` 后，每行发送一份请求，例如：

```json
{"id":"query-1","argv":["capabilities","--command","db context"]}
```

按响应的 `id` 对应请求，读取 `exit_code` 和 `result` 内的业务状态；本入口按顺序串行，不能并发写同一输入流。源码环境可用 `genshin_app.cli_client.CliSessionClient` 的 `call`／`batch` 代管进程和两个输出流；具体方法及批次中断回执从 `call_session.python_client` 读取。批次不是原子事务，失败前的成功操作可能已经生效。用完关闭 stdin 或客户端；源码、Skill或数据库更新后退出重开，不长期占用升级入口。

`call(argv, timeout=秒)` 返回 `in_flight` 时，用 `receive()` 继续收取同一请求。组合、计算、交付有前后依赖时用 `batch(..., stop_on_failure=True)`，必要时根据该操作的接纳结果传入 `accepted` 判定。只有拿到对应文件发布回执才汇报已写出，断流后先核对目标和回执。保存原题可先用 `workflow workspace --audit work.json --task-file task.txt`，有方案后再用 `workflow bind --audit work.json --plan plan.json` 关联。

无论采用哪种入口，都要保留宿主工具返回的在途进程编号、退出码和完整错误流。外层脚本结束、暂时没有文字输出，都不能证明其后台命令已结束。先回读原进程再决定是否重提，避免两批请求同时写同一文件；同名文件冲突先核对已有产物，不直接加覆盖参数。

`data.display_contract` 提供当前语言与中文枚举 `labels`。当前仅简体中文，多语言选择尚未实现；展示标签不能替换模板里的机器枚举或 ID。公共角色/装备中文身份按 [数据接口](../data/query.md) 查询，新模板显式补中文展示字段。

需要原生方案时另读 `data.plan_contract` 的完整 schema、示例、`default_input_mode` 和 engine_v2 注册表，按 [装备工作流](../app-mechanics/loadouts.md) 构造固定面板＋套装选择方案。当前默认 panel 模式不要求五槽，旧完整装备模式继续兼容。`equipment validate/compose/calc/export` 与 APP 共用应用服务；compose 成功仍可能 pending，要看 state 和 missing_inputs。结果回执随受支持格式保存；全未命中零结果的格式例外见下文。

原生会话如提供 `candidate_cli`，优先使用其中 `launch.argv_prefix`、`working_directory` 和必要的 `environment`；这是当前实际安装入口，不需要扫描往期输出寻找程序。`connection_file`仅给出本会话连接位置，不公开鉴权内容；CLI不能用它选择其他会话或批准正式同步。DSH沙箱拒绝本机通信时按原生权限流程处理该命令，不能改传输绕过。

源码版使用 Python 3.12；打包版使用 `GenshinCalc.exe capabilities`（不加 `-m`，可指定可执行文件绝对路径）。以下子命令均接在对应入口后。

源码CLI子进程固定在项目根目录运行，输入／输出使用绝对路径；本地辅助脚本避免与标准库同名（如`inspect.py`、`json.py`）。Python会优先搜索当前工作目录，同名文件可能遮蔽标准库并造成看似App内部异常；保留原回执，先核对调用目录／模块命名，不能未经复验就判定数据库或引擎故障。此项是源码实验环境要求，不让Agent读取产品实现去猜协议。

## 2. 解读返回包络

- 成功时读取 stdout 的 `code=0` 和 `data`。组合、计算与交付仍分别查看其中实际状态。
- 失败时读取 stderr 的 `code`、`message`、`reason`、`details` 及提供的恢复指引。先保留退出码及原始两个流。Windows PowerShell 的 `2>&1 | Out-String` 可能将错误对象、定位信息和 JSON 混在一起，解析时分别捕获原始 stdout 和 stderr。例如在获准使用 Python 的环境中，可用 `subprocess.run` 的 `capture_output=True` 读取两个独立字节流。
- 全局参数：`--data-dir`（源码版默认 `./data`，打包版默认 LOCALAPPDATA 下的 GenshinCalc/data）、`--pretty`（缩进美化，仅调试时用）；全局参数放在子命令之前
- `python -m genshin_db` 是兼容旧入口，行为一致

## 3. 命令速查（示意，权威以 capabilities 为准）

| 命令 | 用途 |
|---|---|
| `db update [--force]` | **已弃用**；固定拒绝修改活动库，旧参数只供兼容解析 |
| `db status` | 库版本/表行数/annotation 统计 |
| `db serve [--port N]` | 前台启动本地数据服务（查询走 HTTP，见 `data-access.md`） |
| `gui --open 输入.json` | 使用 App 的普通打开入口加载白板、候选输入草稿、原生方案、裸数值模板或完整计算包。计算包打开为只读报告，普通打开不会发起新计算。 |
| `shortcut create --input 输入.json --output 打开方案.lnk` | Windows 本机快捷入口；全局 `--data-dir` 与计算时保持一致 |
| `engine calc <模板.json> [--patch 补丁.json] [--output 结果.json]` | 引擎计算（自由排轴） |
| `engine check-rotation <输入.json> [--preflight] [--format json\|xlsx] [--output 文件] [--overwrite]` | 动作、冷却和能量检查。显式预检仅验证已声明的资源和动作，使用独立检查文件时可离线运行。原生 v3 默认完整执行。 |
| `db context --input 请求.json --cache-dir .skill-tmp/genshin` | 有界资料查询、内容失效与按实体文件索引；本地不必启动HTTP服务 |
| `equipment enable-mechanics --plan 原生.json --output 新原生.json` | 显式升级v3草稿，保留输入；不自动解释机制 |
| `equipment compose/calc --plan 原生.json --output 输出.json` | 组合来源/装备与执行；v3同时产生机制和采样过程 |
| `equipment diagnose --plan 输入.json --request 请求.json` | 按需逐Q／资源／执行软对照或明确ER副本试算；普通groups/issues须提供已有calculated |
| `agent candidate read/edit/compose/calculate --connection 当前连接.json --session-id 实际ID` | 当前运行提供时，转发至同一活动候选；不另开Agent或同步正式板 |
| `diagnostics show --id 实际诊断ID` | 只读已落盘安全系统故障；不启动模型或GUI |
| `equipment edit --plan 输入.json --request 修改.json --output 新输入.json` | 同一输入服务的patch/replace/import/preview；保存未闭合草稿，详见[共用编辑](../app-mechanics/input-editing.md) |
| `equipment review --calculated 结果.json --request 审查.json` | 能力提供时，只读分页审查已有结果/计算包；可能离线重放核验，不生成新的计算结果 |
| `equipment review-batch --calculated 结果.json --request 审查.json` | 能力提供时，同一结果最多16页共用一次核验；大聊天响应定向读历史资源，见[审查与补正](../app-mechanics/review/corrections.md) |
| `equipment workbook --plan 原生.json --output 公式.xlsx` | 同源活公式工作簿；编辑范围见回执support |
| `equipment deliver --plan 原生.json --output-dir 交付目录` | 运行时提供时，一次fresh交付原生/完整计算包/结果与公式Excel，默认短回执；见[完整交付](../outputs/app-and-excel.md) |
| `workflow init/bind/export --plan 原生.json --audit audit.json` | 单一来源/问题记录；export另给output-dir，生成兼容交付视图 |

## 4. 引擎排轴工作流（核心用法）

首次计算后继续[覆盖审查与循环补正](../app-mechanics/review/corrections.md)，按需读取 `calculation_review_contract` 和 `external_damage_contract`。事件/规则优先在原生修正后重算；外部独立数值只能经明确补充入口，不能改result。含补充时 `calculation_totals` 是App展示合计，`summary/hits`保留引擎部分，两者不要混读。

新排轴按[工作重点](../workflow/index.md)组织资料，实际工件表示和输入满足时即可进行本节转换，不要求完成固定focus顺序。原生方案保存候选的给定面板与装备、rotation_model动作、mechanics_model规则及来源；是否可执行由真实输入、能力和审核条件决定。执行契约见[声明式机制](../app-mechanics/execution/rules.md)；已有resolved v1/v2或单次数值计算仍可用[旧模板](../app-mechanics/resolved-template.md)，无需取得开发仓库。

若能力提供 `limited_hit_benefit_contract`，按[有限次数收益来源](../app-mechanics/sources/limited-hit-benefits.md)把已就绪父档案接入自动施加、次数和当前属性采样。完整字段从能力读取，资料与当前确认分别保存，不以focus限制操作，也不手抄逐击收益。

若提供 `mechanism_source_contract`，按[受审机制来源](../app-mechanics/execution/materialization.md)查询通用资料候选。来源引用、预览或接受值没有直接执行权；Agent核实采用内容后提交当前能力支持的自包含强类型材料化输入，再通过compose/calc/deliver执行。不要复制内部编译回执伪造权限；同一收益与兼容专用入口保持唯一交付。

若 `data.plan_contract.damage_reporting_contract` 提供完整全未命中窗口支持，读取引擎生成的 `result.damage_reporting`，把明确的总伤0/DPS0与循环 `unproven` 分开解释；不把空草稿或未知命中当零结果，不手填逐击证明。可用JSON、两类Excel和deliver，CSV与独立空resolved模板不支持；动作/接触改变须重算，详情见[零结果交付边界](../outputs/app-and-excel.md#已验证全未命中的零结果)。

1. **构造输入**：默认把给定面板保存到 `manual_template.characters[].stats`，对应 `equipment_inputs` 使用 `mode=panel`、`panel_policy=static_included`、武器和套装类型/件数，原生方案经 compose/calc。面板已含常驻属性，武器不加成长，不需五件主副词条；条件效果仍须审核。纯数值快照和旧完整装备兼容边界见 [装备工作流](../app-mechanics/loadouts.md)，不能删真实输入绕过 pending。动作、能量与附着/反应输入按运行时对应 schema 保存，旧模板不接受任意新字段
2. **组合和计算**：原生方案用equipment compose查看missing_inputs并完成来源部件审核，再equipment calc。旧裸模板继续engine calc，不把原生v3传给旧core。
3. **修正与重算**：字段、单位和引用错误直接修正并保留方案修订；动作、时点、机制解释、来源或match含义改变时，按授权修订对应输入与后继依赖并重新计算，不机械退focus或暗改用户固定项。窗口、次数、收益值由引擎派生，不直接改结果。旧裸模板可用结构化补丁，非法整批拒绝；补丁不绕过schema、来源、授权或接纳条件。
4. **结果**：原生calc的data.result含summary/hits/buffs_coverage；v3另含mechanics_status/mechanics_issues/mechanics_trace/sampling_trace/rotation_check。旧engine calc的data就是数值结果。通过稳定事件/规则ID读取相关过程，区分完整显式输入窗口的伤害覆盖与额外重复窗口的状态检查；一个输入窗口可以包含多个游戏轮。
5. **留存与导出**：--output保存大结果，按需读取；来源问题一次写audit，再workflow export。同时需要App与Excel且能力提供equipment deliver时，优先一次[完整交付](../outputs/app-and-excel.md)，不要为各产物重复执行calc/export/workbook。仅需公式表或旧版本时使用equipment workbook，并按support检查可改范围。

当前 `engine check-rotation` 提供显式动作/冷却/能量检查，契约从 `data.plan_contract.rotation_contract` 读取；输入、状态与原生 ER 绑定见 [action-energy.md](../app-mechanics/actions/rotation-energy.md)。原生结果另含 `rotation_check`；旧数值路径的计划伤害与v3失败后的已执行前缀按实际damage_interpretation分别解读，不能用前缀计算完整轴DPS。运行时提供元素契约时，按[元素运行时](../app-mechanics/elements/runtime.md)使用受支持的附着/ICD/元素底与反应计算，读取before/after及未处理原因；旧手工hit.reaction或没有相关输入的结果不代表已模拟。未支持的语义不能藏入被忽略的extensions。

## 5. 从机制事实到原生输入

完整排轴使用原生方案保存给定面板、动作和可执行规则。先说清实际想发生什么，再按[计算意图导航](../app-mechanics/authoring-guide.md)读取对应作者schema、片段、完整示例和结果观察入口。动作、命中、附着、反应、Buff与资源各自有身份和时点；一种伤害分类不能替代其触发与附着规则。

[完整原生小例](../app-mechanics/native-causal-example.md)把一次施放、有限多段、普通ICD、命中后收益和粒球到账串在一起，并给出可以独立手算的预期及反例。先核来源和输入，再看实际执行；不要只复制它的虚构参数。

维护已有数值模板或只做公式计算时，[resolved兼容说明](../app-mechanics/resolved-template.md)仍提供完整旧路径。它的预填反应与快照规则仅属于该路径，不能当成新原生轴已经模拟了元素底、动态领域或分域取样。

## 6. 常见错误速查

| 报错关键词 | 原因与处理 |
|---|---|
| `form='resolved'` | 先核实际入口与文件角色；原生方案应交给equipment或候选计算，不能传给仅接收数值模板的engine calc。兼容数值路径才要求该form |
| `补丁应用后模板校验失败` | 补丁把模板改非法了（如删了必需字段）——按错误信息修补丁 |
| `未知 zone` | Buff effect 的 zone 不在枚举内（base_add/base_mult/dmg_bonus/crit_rate/crit_dmg/em/reaction_*/lunar_*/stellar_*/def_*/res_shred） |
| 旧数值模板的反应字段错误 | 仅在明确维护resolved输入时按兼容合同修复；原生模拟先核附着、初态、规则与执行记录，不改手填倍率绕过 |
| `switch_conflict` | 切人使用动作start；读取differences、expected_switches和actual_switches定位。v3由动作派生切人投影 |
| `mechanics_pending` / `unsupported_dependency` | 查看实际缺项/依赖路径，修声明或明确未支持；不手改派生结果变ready |
| `数据库不存在` | 安装或迁移与当前App兼容的已发布数据库版本；旧`db update`不再建库 |
| `accepted_database_is_immutable` | 调用了已弃用的活动库写入；整理source和变更内容，走提案、用户同意、完整新库发布与迁移 |

## 7. 边界提醒

- 同源App＋Excel优先复用引擎和活公式生成器；单独简单分析可用手工Excel。没有支持的能力时明确范围，不能仅更换输出文件扩展名冒充交付
- 服务生命周期：`db serve` 只前台运行；重复启动会拒绝（单实例，ADR-0007）——发现已运行就直接用（见 `data-access.md` 定位三态）

## 8. 双击打开方案

原生 JSON 保存并验证后，用本次实际 App 的 `shortcut create` 导出 `.lnk`，全局 `--data-dir` 使用计算该方案时的数据库目录。输出回执列出原始文件、数据库目录、启动程序及参数；指向输入 JSON，不指向计算结果、回执或 Excel。便携版需要完整目录内的 `GenshinCalc.exe` 与 `GenshinCalcGUI.exe`；前者用于 Agent CLI，后者是无控制台的桌面入口。

快捷方式每次直接打开原 JSON 的当前内容，在独立窗口显示排轴页。它不复制 JSON、不自动计算，也不升级方案或改写数据库。原生方案仍保留换装、动作和机制输入；裸数值模板仍只有其原有能力。其他已打开窗口的未保存输入不受影响。

这是本机绝对路径入口：移动快捷方式本身仍引用原位置；JSON、App 或数据库目录移动/改名后，需要在新位置重新生成。跨电脑交付仍需 JSON 和完整 App，并在接收电脑生成快捷方式。不要承诺把 `.lnk` 和 JSON 一起移动就能自动适配。

Windows 之外，或受控实验未暴露该命令时，交付原 JSON 及 `gui --open` 使用说明，如实注明快捷入口未生成。生成成功不等于已完成真实桌面双击验收；不要为了交付检查自动弹出 GUI 占用用户屏幕。

## 9. App 分层时间轴

原生方案默认显示「技能释放」，裸数值模板默认显示「伤害与计算」。查看机制覆盖时，先点「计算并更新视图」，再切到「效果生效」；可按角色、来源及事件类别筛选。触发点、效果可用区间、次数消耗与反应声明分别呈现；伤害数值和逐击计算明细只放在伤害视图。角色面板可收起，事件详情在右侧；视图切换、缩放、平移与筛选不会改输入。

持续条表示本次执行的规则状态，不保证每次命中都满足受益条件。次数归零、刷新、过期及快照后的实际收益须结合逐击计算明细判断。旧流水缺少实例定位时只显示激活点；展开模板中的逐击 Buff 投影不等于真实覆盖区间。反应点须区分手工声明与当前受支持元素层实际生成的反应；核对elemental流水、规则集和before/after，不从视图有一个反应标签就推断全轴已模拟。调整方案后须重新计算，不能用旧视图证明新输入的收益。

## 10. App原生Agent

原生模式使用DeepSeek API与DSH运行时；在完整基础组合实际提供文件和Shell时，可以调用公开CLI完成同一任务。Key通过Windows Credential Manager管理，由App凭据设置经受控私有通道提供；当前生产不从启动环境读取DEEPSEEK_API_KEY。模型、脚本、Renderer、方案和普通日志不接收已保存Key。能力看真实工具目录与接纳manifest，不把源码变化当成旧包已更新。

当前`workflow=artifact`以[工件规范](../workflow/index.md)执行。DSH唯一管理消息、推理、loop、目标、问题、压缩、完整持久历史及冷恢复；App管理候选、数据库／引擎、领域批准、CAS和文件副作用。打开历史只读不发模型请求，用户发送要求才正常运行；模型轮结束不等于业务已完成。

领域工具及schema以当前实际下发清单为准。先读capability_index，按tool_contracts／runtime_contracts定向取精确合同；五个focus只是资料读序，不锁写入、验证或计算。Skill全部Markdown按需可读，DSH Surface代际变化后只重读所需缺页，不全量灌上下文。完整规范见[V2运行与恢复](native-agent.md)。

候选与正式白板分开。save_current／save_as保存正式文档；未完成候选可通过DSH任务文件或App输入草稿卡保存，分别核对实际文件或completed回执。模型运行不自动锁住全部用户文件操作；已捕获快照可以保存旧修订，不能冒充最新输入。文件回流需显式导入候选，导入不恢复旧接纳、批准或static准入；精确草稿审核与正式结果同步分别进行。

预算通过实际设置界面或有CLI权限的`agent budget show|set|reset`维护；修改只影响下一轮，报告generation和旧／新值。通用上下文／loop沿DSH默认与公开扩展，App容量读取当前有效policy，不套早期30分钟收尾。健康在途请求不因短观察窗口中断；真实错误、用户取消和明确准入不足按实际回执处理，未知副作用先查账而不重放。

每轮有独立中文回复；可折叠思考和工具活动不代替回复。普通问答、研究、编辑和草稿保存不因未完成计算而整体锁死；真正的用户批准、输入完整性、版本兼容、计算接纳与正式同步仍严格执行。原生运行、预算与恢复见可读取的[V2运行规范](native-agent.md)、[工件规范](../workflow/index.md)和当前工具合同；DSH配置以实际运行设置为准。开发仓库对接/复用/维护手册不通过gs_read_skill读取，不能假装已读。
