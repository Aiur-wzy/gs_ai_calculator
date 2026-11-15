# 已有命中的数据库倍率来源

仅在 capabilities 提供 `hit_parameter_source_contract` 与 `action_parameter` 时使用。适用于event_topology中已规划的classic命中或命中原型，系统负责从同一数据库快照读数，不新增动作、跳数、时点或附着。

## 资料整理

用文件请求查询并缓存到相应角色的小文件：

```json
{"action":"action_parameter","character_id":10000002,"slot":"skill","constellation":0,"talent_level":10,"param_index":0,"version_line":"release"}
```

`slot` 与 `skill_id` 二选一；多技能组时显式提供 `skill_depot_id`。`param_index` 从0开始。核对 `scalar_projection`、`level`、`parameter_descriptions.matched`、`unresolved` 和精确来源。中文原文按 `{paramN}` 引用关联，同一句可对应多个参数；不能用描述行号推定参数序号。缺失文本如实登记。

显式有效等级路径按精确角色、命座、技能和等级记录读取标量；并无统一C0/10级门。有效等级缺失或激活加级关系未核实才保持pending。另有level-bound-parameter-v1按base_level及已核实静态加级关系定位；读取13级原表不自动证明命座关系。默认10/13原则不因接口支持更广等级而改变。

`ready`只证明这个数值的来源。Agent仍须判断它是攻击力倍率、HP倍率、治疗系数、冷却、羽毛加算还是其他参数，并在角色机制说明中写明用途依据。不能把任意合法数字当成单属性伤害倍率。`action_dispatch`与一般修改操作仍未执行。

## 数值阶段的转换

原生v3顶层增加 `hit_parameter_sources`，例如：

```json
{
  "hit_id":"已存在的命中ID",
  "version_line":"release",
  "selection":{"character_id":10000002,"slot":"skill","constellation":0,"talent_level":10},
  "param_index":0,
  "scaling_stat":"atk",
  "purpose":"single_stat_damage_multiplier",
  "status":"confirmed",
  "source":"根据该技能的完整中文参数原文，已核对本参数是本次命中的攻击力伤害倍率；记录实际依据。",
  "expected_snapshot_id":null,
  "expected_node_sha256":null
}
```

关联命中必须显式使用相同的 `scaling_stat`，并**省略** `multiplier` 字段，不能写0或null。同actor必须使用同数据库角色、资料线与命座；与已有装备角色关联一致。同一技能在同一轴内的有效等级须一致。模式原型绑定一次即可，展开命中复用其来源。

用途尚待核实时用 `status:pending`；有依据的近似用 `assumed` 并明确依据。`confirmed`是提供者声明，系统没有认证其游戏语义。首次compose读取 `hit_parameter_compilations`，复核参数、用途、等级、身份和来源后，才将本次 `required_confirmation` 的两项写回；不能照抄旧确认。来源变动后重新复核。每次compose/calc均重新查库，原生文件保存来源声明，执行模板才得到倍率，缓存不能作为输入真值。

界面通过“数据库技能倍率来源”编辑；普通命中编辑不覆盖已绑定的倍率。计算回执保留声明用途及未验证状态。公式Excel将此倍率设为固定来源，修改参数选择、等级或资料后需回App重算并重新导出。不要直接修改工作簿倍率后保留旧来源认证。
