# 把已核实事实组装成普通原生输入

已经知道某个攻击的次数、时点、附着与触发条件，但不想反复手工拼接身份和来源绑定时，可以使用现有作者辅助。它只生成可查看、可修改的普通输入；不代查游戏事实，不运行计算，不确认来源，也不写候选。直接手写同一输入或用脚本编辑文件仍然有效。

## 选择表达

读取当前入口的 `handler_matrix`，再沿 `payload_schema_refs` 获取需要的字段。每项都提供原生字段、现有处理器、局限和手写等价路径。不要因为一类材料化处理器不支持某个行为，就推断整个 App 无法表达它。

| 已核实的事实 | 请求 kind | 交付的普通字段 |
|---|---|---|
| 一个动作有明确有限偏移的多次命中，同时需要附着或命中后收益、产球 | `attack_bundle` | 命中原型、`hit_patterns`、附着器、所提供的规则与资源 |
| 动态属性、条件收益、显式目标状态、资源事件 | `mechanics_fragment` | 所给 `mechanics_model` 字段与 `rotation_model.events` |
| 有限序列、周期后台及生命周期，需要当前拓扑的材料化处理器 | `materialization_entries` | 当前计划/拓扑绑定、所给 sources 和 entries |
| 星月能力直伤或明确替换某条手写普通命中 | `special_damage` | 所给特殊事件、精确删除的普通命中；模式/自动来源拥有的命中须先明确其拥有者 |
| 当前组合后的技术绑定过期 | `binding_refresh` | 当前报告的精确指纹或显式重新绑定的材料化外层；改变的来源恢复为待核 |
| 将多个已核实片段一起预览、一起采用 | `batch` | 按顺序组成一份普通编辑请求；采用时一次原子写入，不把它等同于多个独立CLI调用 |
| 移动某个动作并联动已经明确绑定的相对时点 | `action_move` | 动作时点与可确定的关联修改；绝对时点或未声明关系只提示核对，不猜测联动 |

不知道该选哪项时，先用同一入口的 `kind=catalog` 查看 `intent_routes`，按要表达的事情找到现有配方和观察位置，再读相应 schema。目录和配方是可选辅助，直接编辑等价输入仍然有效。

`mechanics_fragment`直接使用现有 schema，新可选字段不用另建语言：目标状态用 `target_status_events/target_status_rules`；没有元素模拟时命中可明确 `target_id`；某个派生分项的收益筛选可引用实际回执的 `filter.event_refs`；冷却事件的 `restore_rebase` 表示恢复次数后按声明重新安排顺序恢复队列。它们都不是按角色名自动选择的算法。参数及适用条件按当前 schema 和回执核对。

资料未齐可继续编辑草稿；辅助返回的 `execution_validation` 只检查结构，不表示游戏事实正确或任务完成。`assumed/confirmed`不会被自动提升；新来源绑定保持未核对。若辅助指出已有ID冲突，可用普通编辑明确替换该条，而不是新建同收益的第二条。

## 同一个入口，两个调用位置

CLI：

```text
equipment materialization --plan 当前方案.json --request 作者请求.json --output 作者响应.json
```

原生 Agent：`gs_validate_agent_materialization(expected_revision=当前修订, request=作者请求)`。外层和请求中的修订必须指向当前候选；先读工具实际 schema。`kind=catalog`可在空白板读取；其他配方生成原生 v3 输入，白板可以通过已有导入/转换路径准备。没有 request 的旧工具仍是原有 lint，不生成修改。

响应 `edit_request` 可以直接交给 `equipment edit --request` 或原生 `gs_edit_input` 的 request 参数。App仍校验当前修订与输入指纹；生成后输入变化应重新生成，不能删除这些检查。`fragment`和`preview_input`是可查看的普通输入，没有辅助专用的隐形运行状态。采用后继续正常组合、核对及计算。

绑定恢复还需要当前真实组合报告。CLI加 `--report 当前组合报告.json`；原生工具复用候选当前 report。指定 `payload.binding_ids` 仅回填这些来源的技术指纹；来源指纹变了会回到 `unreviewed`，仍需核对事实。`payload.materialization=true`只重建当前计划/拓扑外层，保留来源与条目的原有核实状态。它不保证原时序或节点关系在编辑后仍正确。

## 可运行的小例

先把[完整虚构例子](native-causal-example.md)的第一段 JSON 保存为 `causal-draft.json`。下面仅把已经写出的教学事实拆成待编辑方案与作者请求，没有增加角色事实。保存为 `prepare-author.py`，运行 `python -X utf8 prepare-author.py`：

```python
import copy
import json
from pathlib import Path

full = json.loads(Path("causal-draft.json").read_text(encoding="utf-8-sig"))
plan = copy.deepcopy(full)
plan["manual_template"]["hits"] = [plan["manual_template"]["hits"][1]]
plan["effect_bindings"] = []
model = plan["mechanics_model"]
model["hit_patterns"], model["rules"] = [], []
model["initial_state"]["rules"], model["resource_ownership"] = [], []
model["elemental_model"]["profiles"] = [model["elemental_model"]["profiles"][1]]
request = {
    "kind": "attack_bundle", "version": 1, "request_id": "teaching.author",
    "expected_revision": plan["revision"],
    "note": "重新组装明确声明的虚构教学输入",
    "payload": {
        "prototype_hit": full["manual_template"]["hits"][0],
        "hit_pattern": full["mechanics_model"]["hit_patterns"][0],
        "application_profile": full["mechanics_model"]["elemental_model"]["profiles"][0],
        "mechanics_model": {key: full["mechanics_model"][key]
                            for key in ("rules", "initial_state", "resource_ownership")},
    },
}
for filename, value in (("author-base.json", plan), ("author-request.json", request)):
    Path(filename).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
```

下面的脚本展示 Windows 下 UTF-8 与两层状态的处理。开发环境已安装本项目时可直接执行；便携版把 `APP` 换成实际 CLI EXE 的单项路径数组，不能把整条命令拼成一个字符串。保存为 `apply-author.py`，在相同目录运行 `python -X utf8 apply-author.py`：

```python
import json
import subprocess
import sys
from pathlib import Path

APP = [sys.executable, "-X", "utf8", "-m", "genshin_app"]

def call_app(arguments):
    run = subprocess.run(APP + arguments, capture_output=True,
                         text=True, encoding="utf-8", errors="strict", check=False)
    try:
        reply = json.loads(run.stdout.strip() or run.stderr.strip())
    except json.JSONDecodeError as error:
        raise RuntimeError(f"进程退出{run.returncode}，未收到JSON：{run.stderr}") from error
    if run.returncode != 0 or reply.get("code") != 0:
        raise RuntimeError(f"命令未成功：退出码={run.returncode}，App回执={reply}")
    return reply["data"]

response = call_app(["equipment", "materialization", "--plan", "author-base.json",
                     "--request", "author-request.json"])
assert response["plan_modified"] is False
assert response["execution_validation"]["status"] == "structurally_valid"
Path("author-edit.json").write_text(
    json.dumps(response["edit_request"], ensure_ascii=False, indent=2), encoding="utf-8")
call_app(["equipment", "edit", "--plan", "author-base.json", "--request", "author-edit.json",
          "--output", "author-adopted.json"])
print("普通输入已写入 author-adopted.json，尚未计算。")
```

两次命令成功只证明作者辅助与编辑完成。接着把 `author-adopted.json`作为完整例子中 `equipment compose` 的输入，核对教学来源、计算，并对照四次命中 67.5/90/45/90 和真实粒球到账。换成游戏输入后，不能照抄教学核对结论。

计算成功也分层看：进程退出码、外层 `code` 表示调用情况；内层 `state/validation/issues/task_admission`说明输入或执行状态。CLI成功响应在stdout，错误也可能以JSON写入stderr；两个通道都要保留。不要忽略内层待补项，也不要把纯诊断提示叫作网络失败。中文读取失败先修编码，不能用空字符串替换真实诊断。

组合与计算会附带精简的 `default_assistance`：本次验证范围、面板比较、计划Q与实际执行Q、伤害计数观察。实际计算后还会加入收益应用与公式消费摘要；它只能解释已声明输入，不能从“某效果已消费”证明该效果的游戏范围没有漏写。它是软提醒，不会自行更改输入或阻断接纳。`presentation.truncated`及`omissions`只表示本次展示省略，完整资料可以从 `default_assistance`资源按路径续读，也可用 `gs_diagnose_result` 的 assistance 视图／CLI `equipment diagnose`；不是“未显示的检查已通过”。
