# 排轴资料索引

先确认角色、形态、技能段、目标和资料版本。涉及角色机制时按[角色附录配方](character-appendices.md)读取已经选定的角色。需要外部证据时，按眼前要决定的事情查下表。只引用能回答当前问题的资料，记录出处、采用值和适用条件。

| 想确认的事 | 从哪里开始 | 读什么，怎样用于候选 |
|---|---|---|
| 命中时间、接招或切人 | [动作估时指南](action-timing.md)。优先 [gcsim 角色帧表](https://docs.gcsim.app/reference/characters/) | 选正确形态与后接操作，分别读取首击、各段命中和可接帧，再查原录像和固定提交。奥黛塔 EE 后以第二个 E 的可切人帧定衔接，后台照自身时序继续 |
| gcsim 缺新角色或缺特定手法 | [强度研究院计算工作室](https://cs.gensri.wiki/)与[计算数据原表入口](https://www.gensri.wiki/data/) | 从对应版本的轴或原表找起手、命中和接招各自的含义。安娜裁灭二到三的 1.9 秒只支持示例轴的命中差。需要更早切人或改变连段时另查输入和恢复时点 |
| 进入后台后为何少一跳 | [gcsim 逐帧样本](https://docs.gcsim.app/guides/building_a_simulation_basic_tutorial/)与[对应角色实现](https://github.com/genshinsim/gcsim/tree/main/internal/characters) | 核首次延迟、交替间隔、末跳边界、重施后的旧实例处理。查看样本的等待与换人延迟、命中和资源事件，确认同样设置后再改本方案 |
| 某段有没有挂元素或产球 | [梅信心角色机制](https://meropide.com/chs/characters/阿罗夏/theorycraft/)、[强度研究院机制页](https://www.gensri.wiki/wiki/)、[Wiki 附着表](https://genshin-impact.fandom.com/wiki/Internal_Cooldown/Data) | 先锁定技能段，再读对应附着组、计数、产球和触发条件。查条目引的原始记录，并与本库攻击节点交叉核对。协同攻击触发间隔和命中后能否附着分别记录 |
| 第二轮的 Q 能否按时放 | [KQM 能量证据](https://library.keqingmains.com/evidence/combat-mechanics/energy)与[本项目动作与能量检查](../app-mechanics/actions/rotation-energy.md) | 先读实际 Q 扣能、接球时前后台、溢出及剩余余额。再核缺的产球事件、概率、触发冷却和固定返能，按本任务的平均或指定情景重算。其他版本和配置的充能建议仅用于对照 |
| 增速会改变哪些事件 | [帧率与时钟](frame-rate-timing.md)、[KQM 计帧](https://library.keqingmains.com/combat-mechanics/frames) | 按具体计时器或动作找证据。保持计时起点和帧率口径一致，重算被提前的命中和受影响的后继事件 |
| 版本冲突与未上线数据 | [Gachabase 版本对比示例](https://gi.gachabase.net/diff/characters/10000147/danica?cur=beta_7.2.52_49468064&lang=chs&prev=beta_7.2.51_49319249)、[达妮卡社区记录](https://t.me/GenshinLeakflow/19677)及[官方公告](https://www.hoyolab.com/) | 比较同一实体两份固定版本，说明差异属于文案或数值还是实测。社区转发沿署名回原记录；正式上线状态按实体核实并依现有确认规则处理 |
| 只有原始录像或展示 | 从 gcsim Frames、KQM 证据或角色研究帖进入作者的 YouTube、哔哩哔哩录像 | 标出录像有效帧率、起手帧、命中帧、接招帧。部分画面缺失时保留能看到的事件，必要时用于时序区间或顺序参考 |

记录能重现当前决策的最小证据：来源版本、角色形态、动作组件、起算点与时点、适用条件、采用或待核的原因。相同来源经多个页面转载时保留最接近原始记录的入口。网站打不开时，继续核本地资料、已有同版本证据或其他公开来源，候选可保存。若某项未知会决定能否多打一个动作、触发反应或通过能量检查，明确保留这个差异。
