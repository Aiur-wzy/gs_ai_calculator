# 数据库接口使用指引

> 2026-10-28：活动数据库只读。`db update/relabel/localize`、annotation写端点和semantic import／review均已弃用。Agent发现的新资料应形成带source和完整内容的库外提案；用户同意后由完整数据库版本发布流程纳入。本文后续出现的旧直写示例仅作历史说明，不得执行。

本地数据库提供角色原文、数值和机制补充。**先检查当前 capabilities：有 `db catalog` 时可直接按中文名定位ID，再用 `db context` 定向查询并复用实体缓存；基础值有 `db panel-basis` 时直接查询。只有需要 HTTP 查询时才定位服务。机制说明按实体整理，后续按需读取。** 拉表按[工作重点](../workflow/index.md)针对当前问题读取并构造App输入，不把五个focus当成读取或写入的强制顺序。

需要理解高级字段或继续追踪角色→技能→Ability→Gadget等候选关系时，按需读取[数据库字段与关系README](database-readme.md)。该文档只说明字段、可能关系、证据边界与查询路线，不要求数据库建立JOIN，也不能代替Agent材料化或引擎校验。

公共实体、数据库ID和基础数值默认以当前数据库为准；查不到时先核对名称、版本、数据集状态、完整性和请求合同，不能直接编ID。兼容且完整性有效的当前快照若以精确查询证明实体或必要字段确实`not_found`，Agent可查证来源后建立当前方案缺失型临时输入，完整列出数据库缺失、采用值、逐字段证据、不确定性、影响和风险，无需逐项请求用户批准。数据库已有事实但Agent判断错误／冲突时，必须展示字段级diff并取得用户对精确payload的明确同意；未获同意时继续使用数据库事实或保持pending。查询服务`unavailable`、pair／完整性失败和请求错误只登记系统故障，不能冒充缺失。用户直接给出的成品面板可直接录入；Agent修改用户已给面板、锁定配装或其它既有输入仍须批准。临时对象不伪造数据库ID、不写回公共库、不跨方案复用，数据库或来源变化后失效。当前caps没有对应临时输入合同时，只能保留设计建议，不能假装已写入方案。

公共装备使用 capabilities 中的 equipment 端点。当前固定面板模式只需武器详情/实际精炼原文、套装详情/已满足阈值及相关技能，不为给定最终面板反推单件／主副词条或重复叠加成长；面板已含两件套等常驻属性。用户明确要求从基础／主词条／预算构造面板时，可查指定成长并手工分配，旧完整装备模式按已有逐件输入继续。默认图鉴目录，同名按原 ID 区分；版本状态见 meta/version.datasets。完整读取顺序和方案应用见 [equipment.md](../app-mechanics/loadouts.md)。资料查询不创建玩家方案，也不自动把原文转成可执行规则。

### 查询已选装备的资料附录

判断武器被动或套装触发前，按已选装备的类型与实际 ID 查附录。装备详情中的 `annotations` 只带已审核资料。原生用 `gs_list_appendix_categories` 找分类，再用 `gs_query_appendix` 读需要的正文。独立 CLI 可用 `capabilities --section appendix_query_contract` 查参数，再执行 `db appendix --input 请求.json`。请求字段示例为 `{"target_type":"weapon","target_id":11401,"status":"pending","version_line":"release"}`。将 ID 和版本线替换为当前选择。

在独立 CLI 中省略 `category` 只列分类及计数，带返回的 `category` 读正文。按需用 `page`、`page_size` 继续读。`pending` 和 `approved` 分开查，已从同一快照读过的正文可以复用。保留来源状态，采用后在原生输入里表达实际触发与后果。

## 1. 定位服务（使用 HTTP 时）

> 首次准备（仅一次）：包为 src 布局，需在仓库根执行过 `pip install -e .`（editable 安装）才能直接调用；未安装时报 ModuleNotFoundError。
> 统一 CLI 入口为 `python -m genshin_app`（app.md CLI 定稿）；旧入口 `python -m genshin_db` 为兼容薄转发，行为一致。

开发态数据目录默认工作目录的 `data/`；打包软件默认 `%LOCALAPPDATA%/GenshinCalc/data/`，显式 `--data-dir` 优先。使用当前软件实际目录，避免启动另一个数据副本。以下 `data/` 代表该目录，打包版将 `python -m genshin_app` 替换为软件 EXE 路径。按序判断：

1. `data/service.json` 存在且 `GET {base_url}/v1/meta/version` 返回 200 → **直接使用**。
2. 文件存在但探测不通 → 重新读取并再探测一次，仍不可用则走第 3 步，由服务处理失效发现文件。
3. 文件不存在 → **自行启动服务**：后台执行
   `python -m genshin_app --data-dir data db serve`
   （单实例约束：若已有服务在运行，该命令会以退出码 1 拒绝并打印已有服务地址——这不是错误，直接读已有 service.json 使用即可），随后轮询 `data/service.json` 出现（通常 <2 秒）。
4. 启动报「数据库不存在」→ 安装或迁移一个与当前App兼容的已发布数据库版本；旧`db update`不会建库。

`service.json` 字段：

| 字段 | 含义 |
|---|---|
| `base_url` | 服务地址，如 `http://127.0.0.1:54321` |
| `port` / `pid` / `started_at` | 端口 / 进程号 / 启动时间（排查用） |
| `token_path` | 本地令牌文件名（`local-token`，位于数据目录下） |

## 2. 调用方式

- 只读 GET 及 `POST /v1/context/query` **免令牌**。旧注释写入和管理端点即使携带本地令牌也会返回弃用错误；令牌不构成活动库修改授权。本地 `db context` 可直接只读查询指定数据目录。
- 返回结构：成功 `{"code": 0, "data": ...}`；失败 `{"code": <HTTP 状态码>, "error": "描述"}`。
- 分页按具体端点／runtime action的合同填写，不能把某目录的`page_size`上限当全局值。HTTP普通列表常用page／page_size；机制目录等action可以有更小上限，例如当前机制目录50。读实际schema的default/minimum/maximum，按next／total续取所需页；没有“传超大页即可全量导出”的通用接口。
- **`version_line` 贯穿所有查询**：先看 `meta/version.version_lines` 中存在的线。当前默认库含 beta 内容，仍沿用 `release` 查询标签，不能仅凭这个标签断言正式服归属；只有库中确有 beta 线时才切换查询。annotation 的 `version_line=null` 表示跨线共享。
- **能力清单唯一数据源**：`GET /v1/meta/capabilities` 返回端点/参数/鉴权要求的自描述清单。下方配方覆盖常用场景；端点行为以运行时 capabilities 为准，不要凭记忆猜测参数名。

## 3. 常用查询配方

| 需求 | 调用序列 |
|---|---|
| 旧英文名/编号 → 中文展示身份 | `GET /v1/display/resolve?q={名称或编号}&entity_type=character`，携带实际 `version_line`；仅 `status=resolved` 的唯一候选可自动采用 `display_name`。`ambiguous/missing` 保留原始身份，不能取首项猜测 |
| 角色、武器、套装的中文名称与别名 | `GET /v1/display/catalog` → `characters/weapons/artifact_sets`；读取 `display_name`、`display_name_status`、`aliases` 和来源。此精简目录不含技能正文，机制仍读下面的聚合接口 |
| 角色名 → character_id | 有 `db catalog` 时用下节的直接查询，按返回ID消歧；旧服务可用 `GET /v1/search?q={名称}` → `data.characters[].id`（也返回技能/命座命中） |
| **计算所需资料** | 优先有界上下文查询（下节）；也可 `GET /v1/characters/{id}?sections=skills,talents,extra_info&levels=10`。保留身份、正文/参数、来源和 approved annotations；旧服务不支持投影时落盘完整详情再按对象读取 |
| 某技能各等级倍率 | 角色详情 `skills[].params` 已有保留等级；默认拉表取10级，对应加级命座已激活的技能取13级。必要时用 `proud_skill_group_id` → `GET /v1/skill-params/{groupId}?level=10`（加级技能用13）。该端点主要是参数，计算时按 `context_refs` 取回完整上下文 |
| 主技能详情 | `GET /v1/skills/{id}` 返回 items 数组；用 `?character_id=` 消歧。每项包含正文、参数、技能/参数组 approved 注释；角色的固有天赋/命座条件按 `context_ref` 另读 |
| 命座、固有天赋、附属技能 | 读取角色聚合的 `talents` / `extra_info`，按类别和来源 ID 定位；不要假设这些对象都有独立详情端点 |
| **机制补充（默认必读）** | 按[附录配方](character-appendices.md)发现当前所选角色全部可读approved／pending附录并分批读正文。聚合已读approved可复用；角色层空数组不排除技能／天赋等子项pending。导航不等于阅读，不自动采用所有内容 |
| 记录新机制结论 | 资料阶段先记实体机制说明的来源与处理结论，形成原生方案后关联audit采用位置；需要晋升为公共资料时输出库外change proposal，完整列出source、内容、适用版本、证据和未知项，等待用户同意及新数据库版本发布。不得直接调用annotation写接口 |
| 感知数据更新 | `GET /v1/meta/version` → 比对本地缓存（见 §4 失效规则） |

### 实验动作资料回填

实验中查明角色动作耗时、实际攻击／效果分量、命中频率、周期生命周期、逐分量附着、染色／元素转化，或天赋／命座对这些组件的修改时，按[角色动作资料附录](character-appendices.md)建立 annotation appendix candidate。候选按角色及其技能、天赋、命座或组件去重；队伍、轴和 benchmark 不是事实身份。同一角色在多个队伍中重复出现时只保存一份角色事实。新候选一律 `status=pending`、`execution_authority=none`；已有 approved stable-facts 原样保留，不因新候选批量替换或降级。

角色聚合详情默认只带approved注释。当前任务默认读取角色附录正文，导航、真实ID fallback、approved／pending全部分类和分页统一按[附录配方](character-appendices.md)完成；公开角色查询没有`related_targets`字段，不要索要内部函数。HTTP只读入口为`GET /v1/annotations?target_type=<实际类型>&target_id=<原始ID>&status=pending&version_line=release`，approved另查或复用已读聚合正文。查询状态与采用结论分开：pending保持候选身份，核实来源后材料化，不改名绕过校验；公共审核状态只能经内容提案与完整新DB发布改变，也不自动建立reviewed action dispatch profile。

结构化 App 资料分两层保存：角色级填入模板只引用候选稳定身份，并列出该角色的动作组件、染色／转化、天赋、命座、周期／附着选项和剩余手填字段；跨多个动作触发的角色被动保存为 character overlay，由相关动作模板共同引用。实验或 benchmark manifest 只引用模板 revision，并保存队伍配置、动作检查点、场景选择与核对项。数据库注释保存角色机制事实和来源，不复制整份方案 JSON；角色模板不含队友、绝对轴时点或某队期望反应。已知错误或撤销方案只作为 negative regression。神鹤万心既有 12 条 approved stable-facts 继续作为基础，新回填只补角色侧时序、ICD、频率、天赋／命座组件及生命周期缺口。

### 直接查询中文身份和基础面板

以当前软件 capabilities 的 `catalog_contract` / `panel_basis_contract` 为准；旧发行程序没有这些能力时沿用已有接口，不猜命令。`db catalog --input 请求.json [--output 结果.json]`接受`kind=character|weapon|artifact_set`、`query`，以及可选`page`、`page_size`和`version_line`。版本线只写在请求里，单页最多30项。名称匹配可能多条，同名保留原始ID，不自动取首项；查不到时先核对规范名，不能编ID。返回精简身份及同事务版本信息，正文另用`db context`按ID缓存。

`db panel-basis --input 请求.json [--output 结果.json]`接受`db_character_id`、可选`level`（默认90）、`version_line`及`weapon={weapon_id,level,ascension}`。角色90/100级分别读取已有数据库列；武器白值必须有明确等级与突破。其它等级、缺失武器或资料仍返回缺项，不沿用90级或补零。输出是基础生命/攻击/防御与武器基础攻击，不是成品面板；不追加副属性、武器被动、套装或词条。用户已给成品面板时仍遵守常驻加成已包含的约定。

两个入口均无需HTTP服务；全局`--data-dir`仍放在`db`之前。查询不建库、不更新库、不改方案，但复用既有数据库协调锁，数据目录需要允许锁文件访问。指定输出时默认不覆盖，`--overwrite`也不能覆盖请求、数据库、协调锁和SQLite配套文件。内置Agent沿用`gs_search_catalog`与`gs_character_panel_basis`，版本线由固定会话提供，不能在工具参数中另行覆盖。

### 原生武器成长与工具合同

实际目录提供`gs_weapon_stats`时传`weapon_id/level/ascension`三个必填整数，版本线来自当前绑定；不传refinement或外部DB路径。返回properties.value/unit、原始calculation、source_refs、data_version与当前DB指纹。ratio直接作为比例，flat为平值；未知保持null／unknown，不补0。角色90／100级基础、武器等级／突破、副属性和精炼被动分别查询，不能从精炼效果的空addProps／level缺省推断成长丢失。`gs_character_panel_basis`只提供基础攻击等白值。

先读`capability_index`；具体工具读`tool_contracts`的`/tools/工具名`，运行资料动作读`runtime_contracts`的`/actions/动作名`。它们通过`gs_read_context`按pointer分页读取，不需加载所有合同。`gs_query_runtime`使用`{"request":{"action":"实际动作名"}}`，具体嵌套字段另看对应action；GUI方法名不能直接当action。错参按path／contract_ref修正，工具未发布或服务unavailable是系统问题，不是数据库not_found。

### 有界上下文与精确选择

能力提供 `limited_hit_benefit_profile` 时，按[有限次数收益来源](../app-mechanics/sources/limited-hit-benefits.md)用父动作的直接伤害参数查询并保存完整原文；不要把收益系数的参数位置误作父身份。资料按实体保存一次，需要执行时再做来源场景确认，查询ready不等于整技能可执行。

能力提供 `mechanism_catalog/mechanism_profile` 时，按[受审机制来源](../app-mechanics/execution/materialization.md)由已选实体发现记录，再保存精确修订的完整资料；审核、物化、历史执行资格与实际App绑定分别判断。它可与原文小文件并存，不要求为每个动作重复查询，也不授权拉表任务改写数据库审核状态。2026-10-27后这些资料一律没有直接执行权；Agent必须将采用内容材料化为当前方案，旧自动编译能力只按legacy边界处理。

软件 capabilities 的 `context_contract` / 数据库 capabilities 的 `context_query` 存在时，可一次请求至多4角色、4武器、8套装定义。它只返回显式资料，不提供队伍轴或自动选择。

```json
{
  "version_line": "release",
  "characters": [{"id": 10000002, "sections": ["skills", "talents", "extra_info"], "levels": [10]}],
  "weapons": [{"id": 11401, "sections": ["effects"], "refinement": 5}],
  "artifact_sets": [{"id": 15001, "sections": ["effects"], "set_thresholds": [2, 4]}]
}
```

以上只是字段形状；按当前队伍替换实体，选择已激活命座对应等级。保存请求后运行：

```text
db context --input request.json --cache-dir .skill-tmp/genshin --output context.json
```

全局 data-dir 放子命令前。输出的 `cache.entity_files` 给出每个实体/选择的文件路径；同一实体不同选择保留不同文件。默认复用缓存，`--refresh` 重读。输出不能覆盖请求或数据库。HTTP 使用同一请求 POST 到 `/v1/context/query`，成功 data.items 中逐项 status=ok/error；实体缺失不影响其他项，非法参数整体报错。

GET 详情也支持选择：角色/技能 sections、levels（CSV）；武器 sections/refinement；套装 sections/set_thresholds。选择不剥离已选对象的正文、参数索引和来源；角色身份与关联 approved 注释始终保留。levels 只裁剪战斗技能/附属技能参数，固有天赋等级不裁剪。`selection.not_requested` 是未选章节，`selection.missing` 是请求过但当前源没有的值，不能互相代替。非法、空或重复选择需修请求；缺13级时不要回退成自造倍率。

**数据口径速记**（避免误用）：

- 面板字段（`hp_90`/`atk_90`/`def_90`/`hp_100`/...）是**预计算好的整级面板**（基础值×成长曲线+突破加值），直接可用，不要再乘曲线。
- `ascension_prop`/`ascension_value` 是突破属性名称与原数值；百分比属性以比例存储，元素精通等固定值不能按百分比解释。
- `desc_template` 是上游完整描述去掉颜色标签后的文字，不是 Agent 总结。`desc_source` 提供配置表/原记录 ID/文本哈希/语言/commit；`desc_status=present` 仅表示有原文，不保证隐藏机制齐全。`textmap_missing` 表示当前文本源未命中，`source_empty` 表示源条目无描述引用；旧库 `legacy_unverified` 不代表已有正文不可读。
- `extra_info` 的 `info_type=sub_skill` / `inherent_skill` 分别为附属技能/固有天赋，已有库内文本。新版提供独立名称、描述、来源、`source_id`、参数组、`params` 和各级 `description_variants`；旧版可读 `content`。附属技能可能是内部动作，不一定属于某个能从名称推断的主技能。
- `param_desc` 中 `{paramN:格式}` 指向 `param_list` 的第 N 项，**不是第 N 条参数说明**；不能过滤零值后重新编号。持续时间、冷却、能量等可能仅在参数说明中，不能只看 `skill.duration`。
- 主技能通常保留10/13级；两级都不存在的参数组会保留其他实际等级。拉表按 [主入口的拉表原则](../../SKILL.md#拉表原则) 以10级为基础，仅对已激活命座明确加3级的技能取13级，不因人工参考使用9级而额外查级。参数组中的13级条目不证明命座已激活；按原文分别记录受影响技能及有效等级。固有/附属等非普通可升级技能按其实际参数语义读取，不强行套用13级。纯资料查询仍按用户所问等级回答，未提供时说明缺值。
- `effect_type` / `buff_zone` 是机器粗标，不能替代原文中的受益对象、条件、持续时间；人工 annotation 也要核对来源、适用版本和生效条件。
- 元素使用中文；武器类型按返回的 `weapon_type` 枚举解释，不假定所有枚举已经中文化。

**中文显示资料**：显示目录当前 `locale=zh-CN`。`storage_status=legacy_read_only` 表示旧库由只读查询兼容返回，不要求为显示而升级数据库；`chinese_name_missing` 是明确缺口，不能把占位名称当原名。生成别名带来源，仅精确规范化并唯一命中时用于展示，角色/武器/套装的原始 ID 不改写。同名配置、旅行者等歧义保留原始身份；别名不是技能规则。需要持久补齐时，把别名、source、完整内容和影响对象写入库外 change proposal；用户同意后由新数据库版本发布流程纳入。旧 `db localize` 只返回弃用诊断，普通资料查询不触发写库。

**判断技能机制的最小证据**：先读完整正文，再将参数描述的索引与选定等级数值对应，最后检查相关命座、固有天赋和机制补充。例如“持续切割、结束时绽放”的正文结合“持续时间”的参数可以解释输出方式和时长；没有命中间隔/次数证据时不能仅凭持续时间补出次数。机制未知保留为待核实项，必要的估算写入计算假设，不能写回成上游原文。

针对可执行临时配装值的现存资料纠错，App查询回执可附`app_loadout_comparison`：它是按实际角色等级列、武器等级／突破成长查询、套装门槛形成的比较投影，原查询与数值来源另存。`contract`、扩展包装、被动／套装的执行处理策略不是数据库原字段；缺值明确标出，不当0或复制拟填值。核对前后值及精确状态选择后，仍由用户批准该payload；不因为投影存在就声称已批准或由数据库自动解释角色机制。

## 4. 临时资料与缓存

角色聚合详情等大报文**禁止全量进上下文**。`db context` 已提供按角色、武器、套装分开的原始缓存；在此基础上维护小索引和每个实体的机制说明，避免将它们重新合并成大文件。运行结构查询支持batch时，按[运行资料批量流程](runtime-query.md#同快照批量与资料小文件)把已知所需的至多8项查询合并为同快照小文件；它不替代context原文，也不自动解释动作。

- **目录**：普通本地任务可使用 `<工作区>/.skill-tmp/genshin/`（不存在则创建）；受控实验使用工具实际允许的 `cache/`、`outputs/` 等目录。以下组织是文件惯例，不是新增 CLI、投影参数或查询接口。
- **原始缓存**：新 `db context` 自动维护请求、meta 和实体选择指纹，按 `cache.entity_files` 返回路径读取，不另抄一份原文。旧服务手工缓存可用 `{date}-{type}-{target}.json`，同时保存请求参数与版本信息。
- **小索引**：记录当前输入快照、角色/武器/套装 ID、所选等级/精炼/件数、原始缓存路径、机制说明路径及未解决项。索引只用于定位和恢复进度，不再次汇总全部正文或参数。
- **分实体机制说明**：例如 `facts/character-10000002.md`、`facts/weapon-11509.md`、`facts/artifact-set-14001.md`，一份文件只整理该实体本轮相关的机制；共鸣等队伍规则另用一份小文件。使用 ID 区分同名，输入选择改变时标明适用版本，避免把不同精炼或等级混用。
- **复用**：同一输入快照和查询选择下优先复用原始缓存与机制说明，后续先看索引和所需实体说明。缺字段按选择定向补查；有疑点时读取对应原始对象，不按行重扫整份大 JSON。context缓存派生文件被删除或损坏时，命令会检查并修复；runtime batch生成代不使用此修复策略，读取其当前index，SHA不符时另存新代并复核。
- **失效**：缓存检查版本线、commit、built_at、schema与 `manual_content_fingerprint`。人工内容指纹覆盖注释/角色备注完整身份与内容，新增、修改、删除及状态变化都会失效，不依赖 MAX(updated_at)。HTTP手工缓存也需比较这些字段；旧服务无该指纹时，关键计算前重新查询相关注释。
- **读取**：计算时保留整个目标技能对象（身份、正文、来源/状态、选定等级参数及说明），并读取该角色的命座、固有天赋、相关附属项和 approved annotations。可分多次读取，但不要把只含倍率的片段当作完整机制。纯倍率问答可只读取参数。
- **保留**：任务未完成或可能续接时保留索引、说明和引用的原始缓存；交付所依赖的来源已另存可追溯记录后，才清理不再需要的临时文件。

### 机制说明的最小内容

每项相关机制保留一个稳定的本地事实编号，以及以下信息；缺值明确标记，不为了填满表格编造规则。

| 内容 | 记录要求 |
|---|---|
| 身份与适用输入 | 中文名称、实体/技能或效果 ID、有效等级/精炼/阈值、输入快照 |
| 行为与数值 | 关键参数及单位、触发条件、前后台要求、受益对象、持续时间、冷却、次数限制、取值/快照方式；只记录已查实或明确采用的假设 |
| 面板与建模处理 | 是否已计入给定面板；本轮纳入、不适用或待处理及原因，不把读取过原文当作已实现效果 |
| 证据状态 | 已核实、待核实或假设，分别说明依据和限制；来源可信不等于本轮一定采用 |
| 来源定位 | 原始缓存文件与技能/效果/参数组等身份，必要时参数索引；外查保留网址、查询问题、日期和支持的具体结论，不复制长篇原文 |
| 采用位置 | 资料阶段只记录候选用途，不虚构尚未产生的动作编号；动作稿形成后补动作/规则引用，形成原生方案后再关联对应规则与 audit 记录，避免长期维护两套不同结论 |

资料覆盖应包含技能、固有天赋、已激活命座、武器、已满足的套装阈值和相关队伍规则。先读原文及参数、再解释机制；缺项区分“数据库没有”“尚未定位”“外查仍未查实”。未采用或场景不适用的机制也保留简短理由，不把所有原文逐字转写成说明。按[工作重点](../workflow/index.md)核对完整性；实际输入和依赖决定计算资格，不新增动作冻结或focus门。

### 外查与事实变更

- 提交外查前明确缺什么、已检查哪些本地对象。若使用异步或审核中介，保存请求编号，按工具契约取得最终结果；**查询已提交、文件已读取都不等于信息已取得或已采用**。
- 收到结果后记录支持了哪些事实、采用或未采用及原因；空结果、拒绝、未完成和证据不足分别保留。影响核心可执行性的未决项不能伪装为已核实，近似值进入方案时明确绑定假设。
- 宿主提供联网工具时，普通任务及当前benchmark可以查询并采用机制资料、队伍成型轴、动作答案和DPS答案。外查记录需能区分系统可提供的资料、系统缺口、采用的公开答案和适合继续外查的信息，并写明版本、适用条件、采用位置及App复算或外部补充状态。只有题面明确指定的历史封存盲测才按其旧答案隔离规则执行。
- 输入或事实变更后，核对并补查受影响实体，更新说明的适用输入与来源；无需重复查询不受影响的全部实体。结论已被候选采用时，修订相应输入和依赖并重新校验，不机械退focus，也不在转换时暗改已声明的机制含义。

## 5. 故障速查

| 现象 | 处理 |
|---|---|
| 连接拒绝 / service.json 消失 | 服务未运行或被关闭 → 走 §1 第 3 步重新启动 |
| `db serve` 退出码 1 且打印已有地址 | 已有服务在运行（单实例约束），直接读 service.json 用 |
| 401 | 兼容保留的旧管理名称未通过本地鉴权；通过后仍会返回410，不存在可用的活动库写入端点 |
| 404 / context逐项not_found | 核对实体ID与版本线；参数级别缺失不等于角色ID改变。按正文身份定位，不用无依据替代值 |
| 410 / `accepted_database_is_immutable` | 调用了已弃用的活动库写入名称；不要重试或更换参数，改为内容提案和完整版本发布 |
| 503 | 数据库文件缺失 → 安装或迁移一个与当前App兼容的已发布数据库版本 |

## 数据查询技巧

- **按名查角色** → 当前capabilities有`db catalog`时直接按中文名查ID，再用`db context`按实体保存原文；旧服务才走`GET /v1/search?q=`与角色详情。详情不要整份挤入上下文，同快照复用小文件。基础白值有`db panel-basis`时直接查询，按90/100级和明确武器选择取数；它不会计算成品面板。
- **拉表取倍率** → 优先读取角色详情 `skills[].params`，按 [拉表原则](../../SKILL.md#拉表原则) 默认取10级，只对已激活命座明确加3级的技能取13级；必要时再查参数端点 → 不为人工表的9级口径补数，也不把所有技能一并升到13级。计算前仍读取完整描述与相关机制上下文。
- **给定面板直接使用** → 默认 panel 模式使用用户/题面 stats，已含两件套等常驻属性，只补条件效果；不查逐件词条或武器成长反推 → 固定面板＋套装选择本身是有效完整输入，重复加入常驻属性会双算。旧完整装备模式需要角色基础时，`atk_90` 等库字段已预计算成长，不再乘曲线。
- **选择版本线** → 先看 `meta/version.version_lines` 再传 `version_line` → 当前默认含 beta 的库仍用 release 标签，不能仅凭标签判断游戏归属。
- **技能只有倍率看不懂** → 取角色聚合中的 `desc_template`、参数说明、固有天赋/命座及 `annotations` → 原文说明输出方式和条件，参数说明确定数值含义，隐藏机制由有来源的补充说明提供；`desc_status` 可区分缺文与旧来源未核实。


## 常见坑

- **男女主技能/命座 id 共享** → `GET /v1/skills/{id}` 返回 items 数组，用 `?character_id=` 消歧 → 数据库主键含 character_id，直接按 id 假定单条会拿错角色。
- **effect_type 是机器粗标** → 复合技能（伤害+治疗+加攻）以 annotation 修正为准 → 启发式分类只按关键词，班尼特 Q 这类技能语义靠人/Agent 判。
- **`db serve` 退出码 1 且打印已有地址** → 不是错误，读已有 service.json 直接用 → 单实例约束（ADR-0007），重复启动必然被拒。
- **查询 404（库刚更新过）** → 重新 search 获取 id → 管道全量重建后内部 id 可能漂移，不要跨更新缓存 id。
- **服务连不上** → 按 data-access.md §1 三态处理（存活直接用 / 失效后重新启动 / 无则自启） → 让服务管理发现文件，避免手动删除另一个刚启动实例的信息。

## 上线状态与资料采用

查看本次实际涉及实体的 `publication`，也可从技能、附录或运行资料回到其归属角色、武器或套装。实体状态为未上线、无法核实或证据冲突时，在把资料用于回答、配装或计算前向用户说明，并合并询问是否采用。明确区分已知未上线与无法核实。工具可以继续查询供核对，采用范围或用户答复按实际会话记录。CLI 用当前宿主提问，原生对话用实际用户提问能力。旧库缺状态时保留未知。

原生 Agent 用 `gs_read_context` 读取 `publication_usage`，`request` 示例为 `{"entities":[{"type":"character","id":10000147}],"purpose":"calculation"}`。只列实际拟采用的实体，武器用 `weapon`，套装用 `artifact_set`，资料问答用 `purpose:"answer"`。若返回 `question`，原样作为 `ask_user_question` 的一项。答复后再读同一范围可核对真实回答引用。默认范围是本任务，明确换题或扩大用途时再给新的 `scope`。等待或拒绝时可以继续无关工作。

CLI 在已保存原题的工作记录上运行 `workflow workspace --audit AUDIT --request REQUEST`，请求文件是 `{"publication_usage":{...同上...}}`。使用宿主的真实提问入口，在工作记录中添加 `decision`，其 `source_refs` 记录 `kind:"host_publication_answer"`、真实答复的 `reference`、所问 `question_id`、`purpose`、`scope`、`entities`（例如 `["character:10000147"]`）及 `answer:"approved"` 或 `"declined"`。这只是宿主回答的引用，App 不认证外部会话文字；没有真实答复时保持待询问。已收到回答时沿用已覆盖范围，只补问新实体或新用途。
技能参数使用 `parameter_references` 对照原句、`paramN`、零起始索引和原始值。描述所在行数不能代替编号。表行等级和命座后的实际等级分别核对。查询来源的完整性、附录的审查状态和执行权限分别读取。
