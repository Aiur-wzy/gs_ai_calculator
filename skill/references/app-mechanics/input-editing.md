# 共用输入编辑、文件回流与派生视图

需要批量改候选、处理原生高级输入、移动辅助切人、从文件导入或比较当前版本时读本页。具体工具参数读`input_edit_contract`和当次`gs_patch_candidate`／`gs_patch_whiteboard`合同；CLI按`capabilities`提供的`equipment edit`操作同一输入服务，不另设一套游戏规则。

## 当前表示只有一份主要游戏输入

- 尚未转换的白板以team、panel、actions为主要输入，可由角色添加与白板patch逐步构造。
- 转换或导入原生方案后，以完整原生plan为主要输入。角色面板在manual_template，装备在equipment_inputs，动作在rotation_model，规则、属性依赖、周期、生命周期等按各自原生合同保存。白板是派生视图，不要求Agent再手写action_map、辅助动作名单和第二组时点。
- 不适合简化白板展示的高级字段保留在原生输入，可按原路径查询；不先降级成白板再重建。用户正式白板基线与隔离候选仍分开，改候选不代表已改正式板。

游戏片段缺字段、引用待补或数值尚未知时可以保存／继续编辑，回执标为未验证。JSON无法解析、系统身份不合法或伪造App批准不能当成功；实际compose／calc仍核验真正消费的输入。

## 三种编辑方式

`mode=patch`保留JSON Pointer，支持稳定ID精确定位。比如修改一个已存在动作的时点：

```json
{"expected_revision":7,"reason":"按已核实占用微调切人","operations":[
  {"op":"replace","selector":{"collection":"/rotation_model/actions","id":"实际动作ID"},"path":"/start","value":3.2}
]}
```

selector只在指定数组内寻找唯一ID，找不到或重复不猜索引；一次多项修改全部成功才提交。普通动作与辅助切人分别有ID，局部改时不需要配对移动无关动作。已声明的相对延迟按引擎原有规则随动；显式绝对命中／资源时点保持原值，Agent复核受影响后继，不能指望App猜测平移。

`mode=replace`提交整份游戏输入，`input`为原生plan或当前白板。`mode=import`使用`file_path`读取一次完整稳定快照，可附`expected_file_sha`；原生路径按本轮DSH工作目录及真实文件服务解释，不另绕到Python宿主重新读取。大输入优先文件回流，避免塞进工具参数或聊天。`preview=true`只返回影响摘要，不提交。所有模式使用当前expected_revision，可另附expected_input_fingerprint。

```text
equipment edit --plan 当前输入.json --request 编辑请求.json --output 新输入.json
```

独立CLI文件操作返回新文件，不自动关联或覆盖任意GUI会话。需要改当前GUI候选时调用该会话的公开编辑／导入工具。当前运行上下文提供`candidate_cli.connection_file`和真实`session_id`且capabilities列出对应命令时，也可通过同一个活动owner操作：

```text
agent candidate read --connection 当前连接.json --session-id 实际会话ID
agent candidate edit --connection 当前连接.json --session-id 实际会话ID --request 修改.json
agent candidate compose --connection 当前连接.json --session-id 实际会话ID --request 组合.json
agent candidate calculate --connection 当前连接.json --session-id 实际会话ID --request 计算.json
```

这些子命令接在实际 App CLI 入口后。`edit` 走同一输入编辑合同，由 CLI 读取获准文件并提交稳定快照。`compose` 和 `calculate` 请求只含当前 `expected_revision`。`compose` 用当前候选和固定数据库生成组合报告，正式接纳另查计算回执。

`read` 无请求时返回当前输入与摘要。读资料时使用与 `gs_read_context` 相同的资源根，例如 `{"resource":"report","pointer":"/effect_requirements","offset":0,"limit":50}`。按 `next_offset` 或返回的 `reference` 继续。成功的资源页在 `data.items`。失败时按[返回包络](../runtime/software-entrypoints.md#2-解读返回包络)读取原始 stderr 的 `details`，先检查当前修订再继续。写入结果未知时先读当前候选和状态。

会话标识和连接文件使用 App 实际提供的值。此入口操作活动候选，正式白板同步仍走用户精确审核与应用回执。

## 文件和候选分别核验

DSH文件权限允许时，可以在工作目录编写资料小文件、原生JSON及辅助脚本。文件写好后先回读确认，再用同一编辑服务显式接纳到候选。文件SHA冲突与候选修订冲突是两件事；半写JSON、重复键、非有限值和读取期间变化不能成为新候选。不自动选择“最后写的”覆盖另一份输入。

无变化回执为no_op，不递增修订或使已有计算失效；真实输入变更后旧计算失效，需要重算。导入保留作者的source_fingerprint、profile_content_hash等合法来源字段并按其合同核对；App计算缓存、审核回执、临时事实批准和writer权限不能从文件继承。旧证明不能手填新指纹重签。

## 计算与辅助盘点的边界

普通手工输入与CLI文件可达的通用规则，原生也可通过完整原生输入表达；不能因为自动来源没有覆盖某角色就把整个机制判为无法建模。辅助拓扑、预期组件清单和游戏理解提示是软反馈，不让同样的真实输入多过一套GUI门槛。

但实际选择了强类型材料化、统一覆盖、数据库来源或临时公共事实时，其执行、来源、唯一所有权和授权要求继续生效。不能删拓扑来绕过已经采用的材料化绑定，不能用required=false或伪造accepted代替真实消费核验。公开执行资格与纯公式数值分别解释；引擎接受已声明输入，也不证明角色技能没有遗漏。

普通保存不需要先算成功。只需App方案时可选择原生输入与计算包产物，不强制生成Excel；未完成则明确保存输入草稿。正式同步仍由用户审核确切候选、文档CAS与真实应用回执完成，不由文件或Shell批准替代。
