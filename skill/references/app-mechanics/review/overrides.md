# 统一计算覆盖

需要精确接管自动结果、且当前App capabilities公开`calculation_event_overrides`时按需读取本文；focus不限制读取或调用，实际输入、授权和执行目标仍须满足合同。普通自动结果没有异常时不要加载本文，也不要为了“更保险”重填自动事件。

## 当前可执行范围

`calculation-event-overrides-v1`当前执行六种mode：

- `add_event`：首批只支持新的`mechanics_hit_event`；受审binding必须绑定同一`hit_id`的已有manual hit，拓扑节点精确锚定其action，payload中的hit／action必须一致，且目标稳定身份尚未被自动基线占用。其他缺席事件域保持fail closed；
- `replace_event`：以同一实体ID完整替换既有事件；
- `suppress_event`：排除精确既有事件；
- `replace_input`：只改目标域白名单内已存在的非身份参数；
- `replace_state_transition`：只对capabilities公开的状态输入集合，以完整transition对象替换精确既有行并保留稳定ID；当前覆盖Buff、rotation event、mechanics application／condition event及有限state-runtime生命周期／计时器，替换后由引擎重放后继；
- `replace_computed_value`：只对已经执行生成的单个`result.hits`事件，以受审固定值替换该事件的expected；必须声明`exact | approximate`、计算方法、不确定性和`state_effects=none`，App随后从全部有效命中重新派生角色／技能小计、总伤和DPS。

`provisional_entity`目前仍只有统一override声明合同；公共实体走独立D2产品链。App返回`unsupported`时不要伪造成功。数据库精确`not_found`／字段缺失可准备Agent查证回执，不逐项请求批准；纠正数据库已有事实必须准备字段级diff并请求用户批准；`unavailable`只登记系统故障；用户成品面板走独立方案输入。不得用说明文字冒充已执行。

replace／suppress的当前执行域以capabilities的`execution_targets`为准；现阶段包括manual hit／buff、rotation event及mechanics rule／hit event／hit pattern／application event／condition event。`rotation_action`即使仍可出现在声明schema中，也没有发布执行目标，不得提交后把`unsupported`解释为引擎已接管。

## 使用顺序

1. 先让App完成自动compose，读取`calculation_override_basis`、capabilities中的`execution_targets`、目标候选和异常摘要。不要自行猜数据库、引擎、拓扑或绑定指纹，也不要仅因mode出现在schema中就推断某个事件域可执行。
2. 只接管已定位的最小范围。事件新增／替换／抑制必须**同时**提供稳定`topology_node_id`和`execution_binding_id`及必要的action／component／occurrence／target身份；不能按中文名或数组位置定位。绑定必须属于当前拓扑、指向同一bound节点、使用兼容target kind，并以pointer＋fingerprint证明精确自动目标；两个各自存在但彼此无关的ID也会被拒绝。replace／suppress mechanics hit只接受exact event binding；add还必须满足上方manual hit、同`hit_id`、精确action anchor和一致payload条件。
3. 保存原因、来源、影响域、未知项和失效条件。替换／抑制必须用回执给出的自动owner写`supersedes`；新增事件不得释放既有owner。
4. 首次提交令`accepted_fingerprint=null`，让App返回当前精确差异、`target_candidates`和`required_confirmation`。检查自动目标、payload、影响域及来源后，才把该确认指纹写回同一项。
5. fresh compose。只有receipt为`applied`、basis仍新鲜、有效图通过验证，且Service按`calculation-override-binding-overlay-v1`对effective／suppressed目标重新定位和复核指纹、最终接纳没有高影响未解项，覆盖才进入正式结果。不要自行构造overlay或receipt。
6. 基线、数据库、引擎合同、拓扑、绑定、自动目标或声明任一变化都要重新review；不能沿用旧确认指纹。

## 不变量

- 不写`result`、`summary`、总伤、DPS、trace、calculated或receipt；`replace_computed_value`也只能绑定单个既有伤害事件，不能提交汇总值或状态后继。事件值改变后由App重新派生所有汇总。
- 状态迁移只替换capabilities公开集合中的完整既有输入行；找不到精确拓扑／执行绑定、稳定ID改变或后继无法按当前引擎重放时保持fail closed。
- 计算值必须提供来源、方法、不确定性与精度；`approximate`从事件传播到角色／技能小计、总伤、DPS、GUI、JSON和Excel，不得伪装成精确。它不改变元素底、Buff、资源或任何状态；需要状态变化时改用状态／事件输入。
- `replace_input`不能修改ID、拓扑关系或任意未列入白名单的字段。动作／角色／装备身份或拓扑结构要改时，按授权修改对应原生输入并重建受影响依赖，不能滥用该覆盖入口或以focus标签代替权限。
- 声明了拓扑或绑定身份但当前文档缺失、为空、陈旧、彼此不关联、动作锚不匹配或不能证明目标pointer／fingerprint时，保持失败，不去掉身份或换用邻近节点绕过检查。
- 同一`coverage_key`只能有一个有效所有者；不能让自动来源、旧manual／external入口和统一覆盖重复交付。
- 覆盖receipt的scope只是`declared_coverage_transaction_only`：它证明本次局部接管事务和有效执行输入，不证明角色专属事件清单、游戏语义或全局所有权完整。仍需按事件拓扑清单核对技能、天赋、命座、武器和套装来源。
- 自动coverage只覆盖本次声明有效图中已有执行证据的通用维度。中等提示可以由Agent拓扑审计补足；高影响未解项必须阻断正式接纳。
