# 从计算意图找到输入与观察位置

本页从同目录 `authoring-intents.json` 生成，只做导航；schema、权限和执行支持以本次App能力为准。条目不是必须逐项完成的阶段或必读许可。已有正确输入可直接继续，未读本页不影响工具权限。

先说明想发生什么，再取当前格式，参考片段组成完整输入，最后到实际执行中查证。完整例子用虚构角色，不授予真实角色机制的执行资格。文件中的片段指针相对例子的第一段完整JSON；不是工具参数，也不能把局部片段当完整方案提交。

原生或连接当前候选的CLI，使用下列 `resource` 与 `pointer`；独立CLI使用 `capabilities --section plan_contract --pointer 指针`。尚为白板时先按实际能力切到原生输入或读取独立能力说明，不能把 unavailable 当数据库缺失。

## 输入导航

### 安排动作、占用与切人

先明确施放占用；后台结束不自动变成下一动作的开始。

- 格式：`resource=capabilities`，`pointer=/schema/properties/rotation_model/anyOf/0/properties/actions/items`。
- 写法与范围：[相关说明](actions/rotation-energy.md)；[完整例子](native-causal-example.md)中片段 `/rotation_model/actions`。
- 已有结果：`equipment review` 或 `gs_review_result`，请求体 `{"view":"actions","limit":20}`。参数外层按当前工具schema，沿返回的分页读取所需项。

### 一个动作产生多段命中

多个时点用序列或周期输入，count只合并同条件数值。

- 格式：`resource=capabilities`，`pointer=/schema/properties/mechanics_model/anyOf/0/properties/hit_patterns/items`。
- 写法与范围：[相关说明](execution/resources-and-lifecycle.md)；[完整例子](native-causal-example.md)中片段 `/mechanics_model/hit_patterns`。
- 已有结果：`equipment review` 或 `gs_review_result`，请求体 `{"view":"mechanics","limit":20}`。参数外层按当前工具schema，沿返回的分页读取所需项。

### 让实际附着与元素底决定反应

先绑定每段附着器与初态，不预填反应倍率或领域层数。

- 格式：`resource=capabilities`，`pointer=/schema/properties/mechanics_model/anyOf/0/properties/elemental_model`。
- 写法与范围：[相关说明](elements/runtime.md)；[完整例子](native-causal-example.md)中片段 `/mechanics_model/elemental_model`。
- 已有结果：`equipment review` 或 `gs_review_result`，请求体 `{"view":"mechanics","limit":20}`。参数外层按当前工具schema，沿返回的分页读取所需项。

### 命中后触发增益与刷新

触发者、受益者、有效时点和取值对象分别说明。

- 格式：`resource=capabilities`，`pointer=/schema/properties/mechanics_model/anyOf/0/properties/rules/items`。
- 写法与范围：[相关说明](execution/rules.md)；[完整例子](native-causal-example.md)中片段 `/mechanics_model/rules`。
- 已有结果：`equipment review` 或 `gs_review_result`，请求体 `{"view":"damage_explanations","limit":20}`。参数外层按当前工具schema，沿返回的分页读取所需项。

### 生成微粒、到账并扣除爆发能量

产生与到账分别计时；先核资源完整性，再比较充能投入。

- 格式：`resource=capabilities`，`pointer=/schema/properties/rotation_model/anyOf/0/properties/events/items`。
- 写法与范围：[相关说明](actions/admission.md)；[完整例子](native-causal-example.md)中片段 `/rotation_model/events`。
- 已有结果：`equipment review` 或 `gs_review_result`，请求体 `{"view":"actions","limit":20}`。参数外层按当前工具schema，沿返回的分页读取所需项。

### 把一条已核实来源接到执行规则

组合得到当前来源指纹；只确认核实过的具体部件。

- 格式：`resource=capabilities`，`pointer=/schema/properties/effect_bindings/items`。
- 写法与范围：[相关说明](loadouts.md)；[完整例子](native-causal-example.md)中片段 `/effect_bindings`。
- 已有结果：`equipment review` 或 `gs_review_result`，请求体 `{"view":"mechanics","limit":20}`。参数外层按当前工具schema，沿返回的分页读取所需项。

### 按任务范围统计并解释是否完成

显式截止优先；接纳只证明声明的输入，不等于全角色资料完整。

- 格式：`resource=capabilities`，`pointer=/schema/properties/rotation_model/anyOf/0/properties/damage_report`。
- 写法与范围：[相关说明](review/report-window.md)；[完整例子](native-causal-example.md)中片段 `/rotation_model/damage_report`。
- 已有结果：`equipment review` 或 `gs_review_result`，请求体 `{"view":"summary","limit":20}`。参数外层按当前工具schema，沿返回的分页读取所需项。

## 读回后判断

输入存在、来源核对、引擎执行、任务符合游戏机制分别核对。结果中找不到收益时，先辨别未输入、条件不成立、尚未执行到、输入被拒和真正的执行缺陷；不是统一填更多增伤或排除来源。

没有可执行资料时，继续查游戏事实或用已有通用接口表达；未知范围如实保留。这里的示例和导航不自动创建来源、不确认资料、不更改已有结果，也不要求把所有副本都写成某种固定工作表。
