# 初始面板已经包含的静态装备来源

仅在实际运行软件的 `static_inclusion_contract` 可用时采用。读取本次能力回执，不根据本文件推断旧程序已有该能力。

用户给定成品面板时，套装的两件常驻等属性已经包含其中。保持 `mode=panel` 和 `panel_policy=static_included`，填写实际套装与件数，先让 App 预览。完整原始资料满足受审静态结构、没有同来源显式 binding 且最终交付没有重复时，系统自动记录“已计入初始面板”。不需要为这些已采用来源补一条 excluded 分项、手写 Buff 或重复加属性。

资料阶段仍读取该套装原文，区分常驻属性与条件收益；四件套触发不能由两件套已含记录代替。`static_inclusion_compilations` 中只有最终 `ready` 且 `source_authority=fresh_database_admission` 的记录表示本次自动采用。`shape_ready` 仅说明材料结构一致，不能用调用者自己计算的 hash 批准来源；数据库、快照、未支持结构或重复来源问题必须保留并处理。

系统核对来源和所选装备，不证明给定面板每一个数值已经算对。常驻比例保留原始比例，例如 0.18 表示 18%；它已包含在面板，不再乘基础攻击力后加一次。静态来源不成为新的动态 Buff，也不授权 NONEXTRA 分类或未知属性推导。

同来源已有显式 binding 时，软件保留它的处理方式和审核状态。若决定使用自动已含来源，应明确删除冗余人工处理后预览、核对其他缺项并重算；不能为了消除报错而删除未知或条件效果。非给定面板的配装推导继续遵循原组合流程。

计算后用只读 `equipment review` 的 `static_inclusions` 视图检查记录，按 `character` 或 `source_id` 精确过滤；该来源没有伤害事件时点，不使用时间或伤害过滤。GUI 与 Excel 的“面板已含来源”保留属性原值、单位、完整出处和采用依据。结果单独导出只核对保存的一致性，不等于离线重新读库；修改面板、装备或政策后回 App 重新计算。

当前App受审profile只为精确审核过的快照提供非数值登记。没有匹配profile、资料形状未知或动态来源时，保留具体降级原因；不意味套装或DB不存在。Agent可从compose的proposed_binding及真实source_fingerprint建立显式分项；已在面板内的常驻分项用excluded和理由排除重复交付，条件效果另建Buff／事件。同provider和完整owner的resolved／pending／excluded均优先于自动登记，不能以自动成功覆写人工决定。

草稿导入或历史保存证书只能作只读证据；S1 shape_ready不授予S2资格。当前DB、面板、配装或最终有效图改变后必须fresh查询／组合，不换标签复用旧准入。
