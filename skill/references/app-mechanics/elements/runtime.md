# 元素执行输入与逐击回执

游戏附着原理见[反应](../../mechanics/reactions.md)。先按当前规则集声明目标ID、初态与`freeze.freezable`；不可冻结默认coexist，确需互相抵消才设`unfreezable_resolution=mutual_cancel`，不能将不可冻结共存称为Frozen。星月替换/领域/贡献者的独立合同见[特殊反应](special-reactions.md)，旧连续冻结参考另见[兼容范围](compatibility.md)。

## 逐攻击分量资料

为每个可能参与反应的**实际攻击分量**建立附着清单，放入对应角色的分实体小文件。清单至少包含：动作与攻击分量身份、元素、GU、`application_mode=sequence|no_icd|no_application|pending`、`icd.counter_tag`、`icd.rule_group`、有限sequence或`timer_counter(reset_seconds, hit_interval, first_hit=1)`、作用目标、证据等级、资料版本与来源。初段/持续段/终结段、同技能不同 projectile、召唤物、命座追加攻击，以及附魔后的普通/重击/下落必须按各自事实分行；只有证据证明共享时才共用counter身份。

| `application_mode` | 清单含义与执行门槛 |
|---|---|
| `sequence` | 已证明该分量按counter及rule group执行有限序列/计时；必须给出真实绑定，不能由动作类别或规则模板存在推断 |
| `no_icd` | 已证明每次符合命中条件的接触都可尝试附着；必须有正向证据 |
| `no_application` | 已证明该命中分量不产生附着尝试；它与“被ICD阻止”不同 |
| `pending` | 是否附着或采用哪套ICD仍未核实；不得计算成允许/拒绝，也不得标为原生验证 |

缺少`attenuationTag`、攻击到tag的绑定或共享范围，不是`no_icd`证据，也不能默认`sequence`；必须保持`pending`，主动查项目原文与可靠资料并登记待回填字段。`standard`规范化为2.5秒/3 hit的timer-counter；它与有限sequence都是`application_mode=sequence`下的ICD执行规则，不是新的application mode。

角色身份、技能槽、伤害分类和“普通攻击/E/Q”名称都不是ICD规则。不同角色的同类动作、同一角色的不同能力，甚至同一技能的不同攻击分量都可能使用不同tag、rule group、GU或无ICD；不能从动作分类套通用模板，也不能把某个角色的例外推广成项目规则。运行时状态键至少包含施加角色、`icd.counter_tag`和目标：不同角色即使都叫ElementalBurst也不共享计数，也不要求使用同一rule_group；同一角色内哪些组件共享仍须逐项有据核实。`icd.rule_group`只决定该状态采用什么序列与重置。上游`attenuationTag`映射counter_tag、`attenuationGroup`映射rule_group，`attackTag`只描述攻击分类，不能代替counter_tag。名称相同的default规则模板也不能自动视为共享状态。旧输入的`group/shared_group`仅按当前兼容合同读取，新资料和报告使用这两个明确身份。

两个伤害组件可以保留不同profile ID，却由同一施加角色、同一counter_tag和同一目标共用计数；伙伴实体或组件名不自动产生新施加者。以来源证明的身份和规则填入，不为每个组件一律复制独立standard。有限`sequence=[1,0]`／重置与无限每2hit计数器不是同一规则，不能看见“2hit”就互换。首发延迟、周期和两通道相位分开核实，“每2秒”本身不证明首发1秒或另一通道晚0.1秒。

资料顺序固定为：先查本地数据库接口、项目保存的上游原文/配置和已固定提交的实现证据；若缺少实际攻击绑定、共享范围、GU或例外，再做针对性外查。外查记录必须写清实际问题、已查本地位置、适用版本、URL或固定提交、采用与未采用结论，以及需要补入数据库的字段；不能只写“查过资料”。冲突未解决时保持`application_mode=pending`并指出影响的命中。`action_facts` 能装配动作相关参数，但 ready 只代表资料关系，不能当作动画派发、命中时点、GU或ICD已验证。原始 `elementDurability` 不直接当GU，未知不默认1U、`no_icd`或`sequence`。

### 逐攻击分量核实工作单

角色间差异很大，不能把“查ICD”作为一个笼统待办。附着清单中每个`pending`或冲突分量都建立一项工作单，至少写明：

- `attack_identity`：角色数据库ID与中文名、技能槽/技能ID、精确candidate/component、初段/持续段/终结段或projectile、命座、变体、目标、资料线和游戏版本；
- `known_fields`：已经确认的元素、GU、`application_mode`、counter tag、rule group、序列/重置与共享范围，逐字段附本地原始指针、快照/提交或资料链接，未知字段不得塞默认值；
- `missing_fields`与`conflicts`：分别列出缺哪一个字段、哪些来源互相矛盾、影响轴内哪些命中与反应，不能只写“资料不完整”；
- `local_checks`：先检查的数据库查询、原始配置路径/JSON pointer、项目固定源码与已有注释；记录实际查过的位置和结果；
- `research_questions`：围绕该攻击分量拟定精确检索问题，例如“该分量是否尝试附着”“与哪几段共享counter”“规则是几hit/几秒”“GU是多少”，附角色/技能/组件的中英文名与内部ID作为关键词；也可以查询整队排轴、拉表、DPS答案和相似案例来发现或交叉检查规则，但必须记录其输入口径、版本、采用字段和当前方案中的落点，不能把结果数值直接冒充该攻击分量的已验证ICD／GU证据；
- `evidence_decision`：逐来源记录URL或固定提交、适用版本、访问/检索日期、支持字段、采用/拒绝及原因。优先原始配置与可追溯实现，其次可复现测试/可靠机制资料；汇总页或无版本说法不能单独把pending升为confirmed；
- `database_patch`：保留应回填的公共攻击组件身份、字段值、证据、版本范围和复核状态，形成库外内容提案。经用户同意、隔离候选和完整新DB发布后才可公共入库，不能调用旧写入口补活动库；合法场景材料化不等待公共发布。

App返回`research_report_template`时直接在原对象上填写，不删减成自由文本：模板已经包含`report_version=1`和固定的`execution_authority=none`；`actual_queries`记录实际使用的检索式，`sources_checked`逐项记录定位、版本、访问日期、支持字段、`adopted|rejected|inconclusive`及理由，另列`adopted_findings`、`rejected_findings`、`remaining_unknowns`与`database_patch_plan`。该模板可直接写回对应来源的`research_report`，但它只是审计记录，不是执行凭据；只有逐攻击分量的结构化证据经采用后，才能把对应字段从pending提升为可执行。

完成外查后，把报告按攻击分量写入对应原生来源的`research_report`：直接附着来源使用`elemental_sources[].research_report`，单次攻击元信息使用`attack_metadata_sources[].research_report`，周期通道使用`periodic_attack_sources[].channels[].research_report`。一个报告只对应一个具体分量，`execution_authority`固定为`none`；即使报告完整，也不会替代`verification`或让pending直接可执行。已采纳且具有公共价值的结论应进入`database_patch_plan`，按完整版本发布规范维护；当前有据结论沿已支持的强类型合同材料化，公共新库发布后再按新来源复核，不要求先入库才继续本次计算。

Agent看到阻塞反应判断的工作单后应主动完成本地核查和必要的定向联网核实，无需等用户再次提醒。外查结束必须报告具体查了什么、补齐了什么、仍缺什么；若证据不足或互相冲突，保持`pending`并将受影响的反应和总伤标为未确定。`assumed`可以描述场景假设，但不能替代实际攻击绑定，也不能让引擎执行猜测的sequence/no_icd/no_application。若工作单只识别到profile而没有角色、技能、攻击段或轴内使用点，不得直接外查或填写证据；先沿命中、模式、无伤害附着、动作/能力引用反查具体用途，无法唯一定位时把身份歧义作为阻塞项。

按当前工件明确动作、命中偏移、技能变体和初态；**受支持的逐击反应结果交给引擎，不预先手填答案**。附着清单未覆盖的反应相关命中不能标成原生模拟完成。反应反馈说明动作效果不符合目标时，在用户授权内直接修改相应动作／拓扑并复核后继，不要求机械回退focus，也不在数值字段中偷偷改轴。


## 原生转换

在原生v3的 `mechanics_model` 添加 `elemental_model`：

- `schema_version=1`；按本次实际提供能力选择规则集。旧方案不自动升级。
- `target={id,kind:"ordinary",knowledge,initial_aura,status,source}`。已知无底使用 `knowledge=known, initial_aura=null`；未知初态明确unknown，不冒充无底。已有底须给 element、remaining_gu、decay_gu_per_second、source。
- `profiles[]` 含 id、element、application（apply/none/unknown）、status、source。apply给整数 `gauge_units=1|2|4` 与 `icd={kind}`；`kind=standard|sequence|timer_counter` 按当前schema提供 `counter_tag` 与 `rule_group`。standard规范化为2.5秒/3 hit；timer-counter另给精确微秒reset与1～64的hit interval；有限sequence另给1～64项整数0/1序列。每个landed且attempted接触推进timer-counter，miss与明确未尝试不推进，计数放行不移动timer anchor。不同动作中已核实共享的来源沿用同一counter_tag，不每动作或generation重置；规则相同但counter不同仍是独立状态。旧schema若只提供`group`，按兼容回执核对，不能继续用一个字段混写两种含义。
- 每个 `hit_events` 或 `hit_patterns` 项加 `application={profile_id,target_id,landed}`，元素与对应伤害一致。无附着用none档案；未命中用landed=false，不能继续保留该次命中伤害或触发。
- 无伤害附着用 `application_events=[{id,action_id,t,order,application}]`；t是绝对时间，必须在来源动作之后，不为挂元素伪造伤害。

开启模拟后只交未预填reaction的classic逐次命中，不用count跨越附着/ICD时点聚合。既有多跳模式可沿用；一次模板模式所有跳共享档案，有不同附着机制时拆模式。完整字段与边界以本次schema为准。

v2还需要 `swirl_damage={icd:{kind:"unknown"},status,source}`。这是独立伤害计数档案，不是风攻击的附着ICD；不提供自选group，不按每个动作重开。精通需在风提供者面板中显式给出有限固定 `em`（可以为0）；未核实的动态精通写入或缺等级基数会pending。

动态v3沿用上述字段，允许手工Buff及机制规则的普通常量精通贡献，按实际目标、前后台、叠层和到期取值。潜在贡献含表达式、收益筛选、有限次数或改变精通可读范围的match时仍不接受；不能改写成常数以隐藏未支持关系。`extensions.elemental.provider_stat_sample` 保存反应产生时的基础精通、各贡献路径及最终值。父命中post才加上的精通不影响本次扩散，后续扩散重新取值；敌方抗性在扩散出伤前读取。既有表达式 `sample=trigger` 取监听触发时，可能晚于反应产生，不能混当同一快照。增幅伤害自己的快照也与扩散取样分开。

这项动态能力不自动批准天赋的 NONEXTRA 属性读取或风套/固有天赋来源。后者仍须各自的受审资料和执行资格，不能因为扩散计算成功就删除其pending。

不提供elemental_model时沿用旧的声明反应模式。不能因为遇到冻结或特殊扩散不支持而静默删除模型或将其说成“无反应”，再把结果当成相同模型已成功计算。


## 逐击回执与对抗复核

每个附着尝试至少分开读取四层结果：元素伤害是否命中、`application_allowed`是否通过ICD、通过后实际写入/消耗了什么元素状态、最终是否发生何种反应。`application_allowed=true`只表示本次尝试有资格处理附着；命中前没有可反应元素底、存在同元素底或规则消费后仍可能没有反应。反过来，`application_allowed=false`的元素伤害仍可造成伤害，但不能用手填反应覆盖拒绝结果。

正式交付前按本轴实际规则至少做这些对抗检查，并保存逐击身份与序列位置，不能只检查最终反应次数：

- 同一真实共享组跨两次施放继续计数，不因动作ID或重新施放重置；
- 已核实不同counter身份的攻击交错时互不推进，包括同角色的不同攻击分量；
- 无ICD分量连续命中均允许，且不污染其它counter；
- 分别触发计数允许、计时重置、有限序列耗尽，确认计数不会用取模回绕；
- 在有对应元素底与无对应元素底两种状态下复放同一次允许附着，证明“允许”和“反应”没有混为一列；
- 交换同刻或相邻附着顺序后，检查元素底和后续反应按因果改变，而不是保持预填答案。
- 两种伤害共用有据counter时，第二次近邻附着可被阻断但该次合法伤害仍保留；换目标独立计数，不因伤害数多就重复增加领域合格层数。

任何一项依赖的角色/攻击分量事实仍未知时，报告具体缺口和受影响命中；不要用标准ICD、1U或“同类技能通常如此”填平测试。


## 用数据库规则减少机械抄写

若能力契约包含 `attenuation_profile` 与 `elemental_source_contract`，已核实适用的上游模板可用 `db runtime` 请求 `{"action":"attenuation_profile","group":"实际组名"}`，配合 `--output` 和 `--summary` 保存完整资料。缺省 `channel=application` 读取 durabilitySequence；`channel=damage` 读取另一独立字段，不能互换。检查execution.status，不把顶层资料ready当作可执行。将来源加入本地对应技能小文件；group模板名不等于实际攻击共享组。

原生v3按当前schema在elemental_sources显式指定profile、版本线、`application_mode`、上游attenuation来源、`counter_tag`与`rule_group`、逐段结构化`verification`及确认字段；旧`attenuation_group/shared_group`字段只在兼容入口出现。缺少application_mode的旧输入迁移为pending，不自动解释为sequence。上游`attenuationTag`只能映射counter_tag，`attenuationGroup`只能映射rule_group，`attackTag`不能填入counter_tag。对应profile.icd保留unknown占位。首次确认字段为null，compose后读取elemental_compilations的原文来源/投影及required_confirmation，核对适用关系后确认。动作派发首次fresh compile还会公开`application_rule_audit`中的数据库ICD基线和指纹；若具体分量是特殊每-X，Agent须核实后针对该component提交局部`icd_rule=timer_counter`，接受当前基线指纹并把`icd_rule`列入confirmed_fields。App复查基线并派生有效规则，不允许用自由文本、角色名或邻近组件取得执行权。普通每-X不得用`application_cadence`制造attempted=false；只有独立证据证明接触不尝试时才另加gate，两层叠加须分别确认。规则节点确认只证明rule group参数，不能代替具体攻击→counter绑定；sequence/no_icd/no_application仍需含定位、适用版本、复核日期、组件范围和确认字段的逐段证据。App每次重新读取数据库并编译，无需手抄整份序列。确认不匹配或非二元规则会pending；不能仅替换指纹绕过资料复核。GU、命中时点和真实共享关系仍由事实证据提供。

不要同时交手填ICD和同profile的数据库来源。不要把编译得到的sequence写回原生输入，或修改旧resolution充当重新编译。序列耗尽后拒绝至计时重置，首项可以为0；不要假定所有序列无限重复或每次动作从头开始。


## 普通扩散：范围复核与自动伤害来源

capabilities 同时提供 `reaction_damage_profile`、`reaction_damage_source_contract` 和相应扩散规则集时采用本节。先核对本轴普通单目标、单底及已知风附着：旧v2限冰水与固定精通；动态v3允许冰水火及上节限定的精通贡献。都不包含冻结、传播或特殊模式。在对应机制说明里记录采用依据。**普通模式是人为选择并核实的适用范围**，数据库中有普通分支不能证明实际场景不会触发Alter或Star。Agent可以完成这项复核，无需为每次compose额外请求用户批准；未知关键条件不能用确认指纹替代。

旧v2/冻结查询冰水两个来源，动态v3必须另查独立火来源；分别保存到文件后定向读取：

```json
{"action":"reaction_damage_profile","reaction_type":"swirl","element":"hydro"}
```

另一次将element改为cryo，动态v3再查询pyro。`execution.sequence_status=ready` 只表示原目标攻击→伤害组→二元序列与计时可编译；`ability_execution=pending`、owner/dispatch未评价是独立边界。冰原文没有attacker，不能手补Self；引擎的原目标/提供者归属来自显式有限规则集。普通、Alter、独立Star资料分别保留，不把Alter改名为Star。原始耐久不当GU，BaseReactionDamage原始初值0不当运行伤害。

原生v3顶层添加最多一项：

```json
{"reaction_damage_sources":[{"reaction_type":"swirl","version_line":"release","source":"本轴普通单目标冰水扩散的适用范围及资料复核依据","expected_snapshot_id":null,"expected_profile_fingerprint":null}]}
```

实际source写具体采用依据，不照抄占位说明。模型的 `swirl_damage.icd` 保留 `{"kind":"unknown"}`，status/source明确普通规则依据。先compose，读取 `reaction_damage_compilations` 中相应两份或三份来源、范围、诊断和 `required_confirmation`；确认同快照、全部序列/计时一致且本轴适用后，将该次两个确认值写回来源声明，再compose/calc。动态v3使用新的三元素编译指纹，旧双元素确认不可复用。App会同快照重查并只填执行副本；无需Agent抄序列或逐次计数。资料、范围或关键节点变化时重新复核，不只换指纹，也不直接改缓存resolved结果。

**不要再输入同源扩散的transformative伤害事件、同源手填reaction，或与数据库来源并存的手填扩散ICD。** 无底风不留风底；扩散消耗已有底，伤害被独立序列抑制时反应仍成立。查看反应流水与伤害资格分别判断，不因没出伤撤销反应，也不把原目标扩散伤害重新作为附着事件。


## 反应触发与反馈

已有来源确实依赖反应时，可声明 `trigger={event:"elemental_reaction",phase:"post",reaction_types:["vaporize"或"melt"]}`，v2还接受swirl。需要筛选扩散出的元素时用 `reaction_elements`；原有 `elements` 仍指触发元素，例如风。`owner_presence=frontline|any` 检查规则提供者；离场只阻止新触发，不取消已存在收益。反应后置收益不追溯修改父直伤，已支持的零延迟反应收益可以影响紧随其后的扩散伤害。派生产球/回能仍由原队列处理，不另外手抄同源资源。

v2的 `reaction_damage` 是独立事件，不匹配普通hit监听；首批只有专用post触发。数据库序列编译不会自动发现并执行风套、天赋或武器被动，仍核对已声明规则覆盖与来源。

选择器 `event_ids` 使用基础反应ID：v1命中h0对应 `h0:reaction`，模式pattern第1跳对应 `pattern:1:reaction`；v2及动态v3为 `h0:reaction:0` / `pattern:1:reaction:0`，对应独立伤害为 `h0:reaction_damage:0`。回执前置检查轮次的 `0:h0:reaction:0` 是实例ID，不能填写进选择器；父命中h0也不能代替反应ID。不要创建占用这些派生ID的用户事件。

calc后按相关事件查看 `elemental_trace` 或结果的 `elemental.trace`：before/after、allowed/reason、实际反应、消耗和归属。v2另看 `reaction_damage_attempt`、独立伤害流水与状态中的 `reaction_damage_icd`；一次扩散成立不保证该次允许出伤。分别看 `input_window_state` 与 `final_checked_state`，后者可能含附加循环检查。日志有逐步状态并不代表存在公开advance/checkpoint接口。

未知档案、未知ICD/初态、未支持元素组合会保留诊断与此前流水，但确定总伤和展开模板返回null。把原因列为未解决项，不用已知前缀声称完整DPS。confirmed/assumed是证据声明，不代表当前游戏客户端已经复测。

完整秒数仍靠完整动作输入；额外cycles只检查状态，不补未输入的伤害。`buff_hit_matches`不能改写反应、ICD或元素底。


## App与Excel同源

仍用正常 `equipment compose/calc/workbook` 入口。当前可用 `equipment calc --plan plan.json --output result.json --summary` 保留完整结果到文件并仅回显短摘要；根据异常事件ID定向读取元素流水，避免整份trace反复进入上下文。Excel是本次状态机执行后固定事件结构的数值表；回执中 `elemental_profiles_initial_aura_icd_reactions=regenerate_required`。自动扩散伤害及来源序列同属本次固定结构。更改动作、命中、初态、档案、ICD、源资料或反应关系后，回原生方案重跑并重新导出。不要仅编辑旧表的反应因子或展开模板，便声称已模拟新的元素状态。
