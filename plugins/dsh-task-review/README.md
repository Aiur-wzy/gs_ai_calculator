# DSH CLI 任务对照插件

在项目的 DSH 配置中通过 `insert` 加入本目录 `index.mjs` 的绝对文件 URL。配置提供绝对 `command` 和 `audit` 路径，`args`、`cwd`、`plan` 和 `calculated` 按需提供。插件只查询显式指定的工作资料，原有 CLI 命令仍可直接运行。

在创建方案前，用 `workflow workspace --audit work.json --task-file task.txt` 保存原题。方案就绪后，用 `workflow bind --audit work.json --plan plan.json` 关联。执行任务可用 `decision` 记录声明 `execution_intent: execute`，并在 `source_refs` 引用本轮原题的记录 ID。插件仅在当前消息与这条原题匹配时给出收尾提醒。

`compatibility.json` 记录包版本、目标 DSH 版本和模块校验值。调试日志可查加载、绑定、读取、提示组装和收尾结果。该适配的提示去重在本进程生效。原生 App 使用 DSH 自己的会话事件保存诊断。

维护时修改 App 仓库 `agent-runtime/src` 中的源文件，运行 `python scripts/package_dsh_task_review.py` 同步此包。用 `--check` 验证构建载荷与源码一致。卸载时移除项目配置中对应的 `insert` 项即可。
