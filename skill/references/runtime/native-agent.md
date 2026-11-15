# 原生Agent V2运行与恢复

适用于 `connection_kind=native_api, runtime_generation=dsh_v2`。当前 `workflow=artifact` 使用[工作法](../workflow/index.md)。历史 Python／staged 只用于对应旧环境。

## 实际能力

工具以本次真实目录为准，短上下文的capabilities与实际下发schema应一致。GUI方法不是可直接调用的runtime action；有疑问先按能力索引定向读合同。上下文说有gs工具而实际函数里没有，是系统发布错误，不能据此断言数据库缺失或要求用户改回staged。

当前源码使用DSH官方完整基础组合；文件、Shell、jobs、子Agent等通用能力按本次真实工具目录与DSH权限执行，不能从旧版“只有gs工具”说明推断它们被禁止，也不从文档推断未发布的工具已经可用。任务内可以编写资料小文件、JSON或脚本并调用公开CLI；这些能力不替代App的数据库、候选写入或用户批准服务。捆绑Skill可经`gs_read_skill {}`发现并分页读；有文件权限时也可读取实际工作区中已定位的同版文件。不把目录、所有角色原文或完整Skill每step注入。研究与采用账见[研究规范](../data/research.md)。

角色确定后默认完成[角色附录读取](../data/character-appendices.md)，优先发现实际目录中的窄只读导航，没有则沿真实ID和精确分类工具读取。导航不代表正文已读，pending不升权。DSH续聊／压缩后复用同快照角色资源和采用摘要，必要时定向补正文，不每轮全量重读，也不新增阅读权限阶段。

## 持久化与上下文

同一App对话恢复同一个DSH Session；DSH唯一管理消息、raw reasoning、工具历史、Surface、checkpoint、压缩与通用目标。恢复本身不发模型请求，不从聊天重放未知工具副作用。App另管领域候选、绑定、资料、研究／调用账及真实权限；不复制第二份可恢复聊天。

DSH Surface代际只用于其自身上下文投影。当前Skill读取返回真实content_hash、offset、delivered_chars、next_offset等分页字段，不签发已读代际票或data.dsh_context_receipt。压缩/裁剪可能改变可见正文，不使资料失效或撤权；所需文字仍可见就复用，缺失时定向重读。

原生当前工作卡从同一捆绑Markdown投影进本次DSH系统提示；根据最近的真实操作、失败和任务差异推荐，手动focus仍表示工作意图。不累积所有卡为规则。它与当前领域上下文/工具使用同一接受视图，不代表所有主题资料已读。相关原理、角色资料和App合同继续按需自取，可跨focus读取。单张阅读提示失效不撤销仍然有效的编辑、查询和计算能力。

`gs_read_skill` 默认尽量返回可交付的正文。`max_chars` 可缩小当次读取。正文有 `next_offset` 时按该偏移续读；`truncation_reason` 区分主动请求范围和回执字节边界。单次读取、单条大结果转存与 DSH 历史压力处理各有自己的边界。参见[资料续读](../workflow/context-use.md)。

## 预算与继续

以当前有效policy及DSH公开配置为准，不凭历史30分钟／27分钟规则提前停止。当前App默认准入／收尾容量为7200秒；健康在途请求不是达到该数字即被墙钟打断。压缩、正常retry等真实provider attempts仍显示、可取消并计入预算；不自行重发收费请求、换模型／推理档或绕过未知副作用防重放。设置变更依实际工具和授权，冻结后的本轮预算不暗改。

任务未完成且可以继续时，使用 DSH 现有 goal／loop，按[当前问题的恢复路线](../workflow/index.md#遇到失败怎样继续)处理可修缺项。App 重启或模型轮结束时，新的付费调用仍以现有 DSH 工作流程和授权为准。

当前原生接线在发生实际领域编辑或计算后，可在同一轮结束前提供一次[任务差异复核](../workflow/task-evidence.md)。相同差异不反复催促；用户取消、换题、真实提问/批准优先。此功能没有创建新目标或重开结束目标的权力；普通 CLI 没有该钩子，也能通过工作记录读取相同事实。可选官方 DSH CLI 插件需显式绑定方案和工作记录，不自动改宿主配置。

额外模型审查与 PTC 默认不开启。普通原生调用及文件/脚本不依赖它们。原生 `gs_*` 目前要求独立、可追溯的 DSH 工具调用身份，不能直接切换成 PTC 后假定子调用可用；官方 CLI 的文件/命令工具可以在明确选择的作用域试用官方 PTC。额外审查需明确预算，只审查提供的证据，不替代引擎或用户批准。

## 运行配置与公开CLI

DSH通用配置由官方ConfigEditor管理，不把旧六段稀疏设置当当前完整配置。实际提供本地配置连接时，可经`agent dsh-settings show --connection 当前连接.json`发现profile，再按真实entry_id、schema、expected_fingerprint与operations修改；精确命令见当次capabilities。配置变更走DSH原生审批，不以Shell或当前候选权限替代；重置用unset。字段何时生效依官方条目生命周期，不能统一声称只影响下一次运行。无连接旧入口仅用于首次迁移前的历史设置，不是活动配置权威。凭据继续沿App的Windows安全存储，不写进配置请求、工作文件或聊天。

当前运行上下文提供候选CLI连接时，公开`agent candidate read/edit/compose/calculate`转发到同一活动候选，字段见[共用输入编辑](../app-mechanics/input-editing.md)。独立`equipment edit`只写文件；文件与活动候选的接纳回执分开，不因CLI进程返回成功便声称GUI正式白板已改变。

## 候选、批准和文件

读当前工件修订后写入，CAS与来源身份以App回执为准。可直接编辑候选，也可在DSH工作目录构造完整原生输入后显式导入当前候选；不是只允许在聊天中逐字段构造。文件变化不自动同步到App，写文件与App接纳分别报告；见[共用输入编辑](../app-mechanics/input-editing.md)。未知副作用先查账，不盲重放。旧不兼容候选只读，普通聊天与独立研究仍可继续；不能就地重签旧批准或结果。

用户面板可直接录入；真实DB缺失按受控合同补入并报告，偏离已有DB事实或用户锁定输入须精确批准。DSH普通文件或Shell批准不替代领域批准。可以保存未完成输入并独立回读，无需先计算或同步正式白板。需要用户选路径或操作正式文档时沿App文件卡；queued／presented不等于completed，已捕获旧修订的导出不称为最新输入。正式同步仍需精确审核、CAS与真实应用回执。

每轮返回中文答复，区分已改候选、诊断、accepted／approximate、审核／应用及文件状态；推理和工具活动不能代替答复。细节见[工件规范](../workflow/index.md)、[白板接续](../workflow/whiteboard.md)与实际文件卡合同。
