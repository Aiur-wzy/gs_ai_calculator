# 创建物资料：精确身份与挂载

仅在本次 `capabilities.runtime_contract` 提供 `gadget_source_profile` 时使用。它把已知创建物 ID 连接到同快照原行、具体配置和明确挂载的 Ability，帮助整理事实；不自动执行技能或生成伤害事件。

## 资料阶段

先从相关角色的 `action_facts`、`definition` 等回执的 `references` 中找到有来源的创建引用。`references` 是回执字段，不是单独的公开 action。不要按名称猜 ID，也不要遍历全部 Gadget 寻找可能答案。已有精确 ID 时：

```json
{"action":"gadget_source_profile","gadget_id":41002001,"version_line":"release"}
```

若要验证某次创建引用，另传本次同快照 legacy references 返回的真实 `origin_reference_id`；不能用 definition ID 代替。先保存完整结果到该角色的独立事实文件，再从短摘要定位所需部分：

```text
db runtime --input gadget-request.json --output facts/character-gadget.json --summary
```

查询回执含完整原行、选定配置、明确挂载及各自原文/指针/SHA，origin 来自 modifier 时还保留 owner Ability。未请求 origin 时只证明这个 ID 的资料，不能据此补造角色或动作归属。同名多个 ID 各保留原身份；同名多个配置或 Ability variant 保留歧义，不默认选择第一个或 Default。

分别记录所请求身份关系、挂载列表、未知控制字段与执行支持。`graph_scope=gadget_identity_v1` 表示独立对象关系；旧 generic closure 仍可能把创建叶标为 external，这是旧图的固定范围，不是可以擅自改写的错误。`ready` 只说明本次直接资料关系完整，`execution.status=not_evaluated` 仍不支持从动作自动还原任意 Gadget 控制逻辑。

挂载字段缺失或 null 不能视为显式空列表；显式 `[]` 也不证明运行时不会动态添加能力。持续时间、timer 间隔、配置中的常量和最后一次攻击不是同一事实。没有可靠派发证据时，不能由持续时间除间隔猜次数，不能把全部挂载能力平铺为一次技能出伤，不能把 OriginOwner 当属性快照证明。

## 限界与缺项

本接口仅从数据库取源，不从 checkout、旧备份或 outputs 自动补齐。旧索引返回 `gadget_index_upgrade_required` 时记录需更新数据库，已有旧查询仍可继续；不要把升级提示改写成角色没有召唤物。

当前只连接有证据的 CreateGadget 和 TriggerBullet 数字字段。GenerateElemBall.configID使用独立Material关系；运行时提供energy_generation_profile时按[产能资料](../app-mechanics/sources/energy-generation.md)读取，不沿本Gadget接口按整数相等自行连接。旧版没有该能力时明确记录资料缺项。

已明确的多个独立 Gadget 查询可用最多 8 项 batch，并保存逐项小文件。选择依赖前一查询结果时先完成发现，再构造下一请求，不预猜 ID。超限时定向用 definition/fragment 阅读原文并记录接口限制，不删未知分支后伪造 ready，也不把拼接片段当已接受的执行来源。

事实整理后，按[工作重点](../workflow/index.md)把采用内容关联到实际动作、事件及强类型App输入，不强制focus顺序。本资料接口本身不增加原生动作字段；实际 capabilities 另提供 `periodic_attack_profile` 与 `periodic_attack_contract` 时，按[周期来源档案](../app-mechanics/actions/periodic.md)利用反向精确发现与有限展开。没有执行契约时不能自行写入研究草案字段。
