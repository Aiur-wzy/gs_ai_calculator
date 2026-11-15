import type { MechanicsEditor, MechanicsEditorPorts } from "./contracts";
export function installMechanicsReports(MC: MechanicsEditor, ports: MechanicsEditorPorts): void {
  const {
    UI,
    PL,
    RT,
    RC,
    RS,
    DS,
    EF,
    extensions,
    callApi,
    setStatus,
    $
  } = ports;
  MC.renderSharedPools = (section: any, result: any, template: any): any => {
    const pools = result.shared_pools;
    if (!pools) return;
    const box = UI.el("section", null, "eq-card"),
      table = UI.el("table", null, "grid"),
      head = UI.el("tr"),
      rows = pools.trace || [];
    const scaled = pools.schema_version === 2;
    const reasons: any = {
      consumed: "已消费",
      spent: "已扣除",
      empty: "余额为空",
      granted: "已补充",
      set: "设置余额",
      insufficient: "余额不足",
      ineligible: "不满足资格",
      cooldown: "冷却未结束"
    };
    const snapshot = RS.activeSnapshot();
    const poolDefinitions = snapshot?.executionModel?.shared_pools?.pools || snapshot?.template?.mechanics_model?.shared_pools?.pools || result.mechanism_execution?.model?.shared_pools?.pools || [];
    const names = new Map<any, any>(poolDefinitions.map((p: any): any => [p.id, p.name_zh]));
    box.append(UI.el("h4", "共享资源流水"), UI.el("p", "本次计算的只读消费回执。附加检查轮不增加伤害汇总；修改资源后需回到方案重新计算。"));
    if (scaled) box.append(UI.el("p", "数量均为整数原子；实际单位量 = 原子数 ÷ 每单位原子数。未扣足与补充溢出分别记录。"));
    ["秒数 / 检查轮", "资源池", "操作", "对象", "请求 / 实际", "余额变化", "结果", ...(scaled ? ["每单位原子数", "未扣足 / 溢出"] : [])].forEach((v: any): any => head.append(UI.el("th", v)));
    table.append(head);
    for (const row of rows.slice(0, 250)) {
      const tr = UI.el("tr"),
        kind = row.kind ?? row.type,
        values = [`${row.t ?? "—"} / ${row.cycle ?? "—"}`, names.get(row.pool_id) || row.pool_id, kind === "consume" ? "消费" : row.operation === "spend" ? "扣除资源" : MC.label(row.operation || kind), row.key === "team" ? "全队" : DS.character(row.key, template), `${row.requested ?? "—"} / ${row.accepted ?? "—"}`, `${row.before ?? "—"} → ${row.after ?? "—"}`, reasons[row.reason] || MC.label(row.reason ?? "applied")];
      if (scaled) values.push(row.unit_scale ?? "—", `${row.unfulfilled ?? "—"} / ${row.wasted ?? "—"}`);
      values.forEach((v: any): any => tr.append(UI.el("td", v ?? "—")));
      table.append(tr);
    }
    const scroll = UI.el("div", null, "table-scroll");
    scroll.append(table);
    box.append(scroll);
    if (rows.length > 250) box.append(UI.el("p", `已显示前 250 条，共 ${rows.length} 条。完整来源与回执见下方。`));
    box.append(UI.details("完整共享资源状态、来源与消费回执", pools));
    section.append(box);
  };
  MC.renderResourceCounters = (section: any, result: any): any => {
    const ledger = result.resource_counters;
    if (!ledger) return;
    const box = UI.el("details"),
      table = UI.el("table", null, "grid"),
      head = UI.el("tr"),
      rows = ledger.trace || [];
    const kinds: any = {
      counter_accumulation: "实际消费累计",
      counter_reset: "重置累计",
      counter_window: "累计窗口变化"
    };
    const states: any = {
      accumulated: "已累计",
      outside_window: "累计窗口外",
      no_spend: "无实际消费",
      created: "已创建",
      established: "已建立",
      closing: "正在结束",
      ended: "已结束"
    };
    const amount = (v: any): any => v && typeof v === "object" ? Object.entries(v).map(([k, n]: any): any => `${k === "total" ? "合计" : k}：${n}`).join("；") : v ?? "—";
    box.append(UI.el("summary", `资源累计流水（${rows.length} 条，只读）`), UI.el("p", result.resource_counter_views ? "按实际消费累计，数量为整数原子；单位量 = 原子数 ÷ 每单位原子数。含附加检查流水，不增加伤害汇总；收益取值另见资源读数流水。" : "按实际消费累计，数量为整数原子；单位量 = 原子数 ÷ 每单位原子数。含附加检查流水，不增加伤害汇总；尚未配置累计转伤害。"));
    ["秒数", "累计项 / 过程", "请求 / 实际扣量 / 计入累计", "累计变化", "每单位原子数", "状态"].forEach((v: any): any => head.append(UI.el("th", v)));
    table.append(head);
    for (const row of rows.slice(0, 250)) {
      const tr = UI.el("tr"),
        values = [Number.isFinite(row.time_us) ? row.time_us / 1000000 : "—", `${row.counter_id || "—"} / ${kinds[row.type] || row.type}`, `${row.requested ?? "—"} / ${row.resource_accepted ?? "—"} / ${row.accepted ?? "—"}`, `${amount(row.before)} → ${amount(row.after)}`, ledger.counter_units?.[row.counter_id] ?? "—", states[row.reason || row.phase] || row.reason || row.phase || "—"];
      values.forEach((v: any): any => tr.append(UI.el("td", v)));
      table.append(tr);
    }
    const scroll = UI.el("div", null, "table-scroll");
    scroll.append(table);
    box.append(scroll);
    if (rows.length > 250) box.append(UI.el("p", `已显示前 250 条，共 ${rows.length} 条。`));
    box.append(UI.details("完整资源累计账（含实际提交状态与来源审核）", ledger));
    section.append(box);
  };
  MC.renderResourceCounterViews = (section: any, result: any, template: any): any => {
    if (extensions.CF?.render(section, result, template)) return;
    const ledger = result.resource_counter_views;
    if (!ledger) return;
    const box = UI.el("details"),
      table = UI.el("table", null, "grid"),
      head = UI.el("tr"),
      rows = ledger.trace || [];
    box.append(UI.el("summary", `资源读数流水（${rows.length} 条，只读）`), UI.el("p", "从实际已提交的累计账读取。原始原子数、单位换算、封顶/阶梯与最终层数合成分开；此处不额外增加伤害。"));
    ["秒数", "来源规则 / 收益", "接收角色", "累计 / 读数", "原始原子 / 每单位原子", "当前读数", "单次值 / 层数 / 合成收益"].forEach((v: any): any => head.append(UI.el("th", v)));
    table.append(head);
    for (const row of rows.slice(0, 250)) {
      const tr = UI.el("tr"),
        read = row.reading || {},
        values = [row.t ?? "—", `${row.rule_id || "—"} / ${row.effect_id || "—"}`, DS.character(row.character, template), `${read.counter_id || "—"} / ${read.view_id || "—"}`, `${read.raw_atoms ?? "—"} / ${read.unit_scale ?? "—"}`, row.source_value ?? "—", `${row.single_value ?? row.value ?? "—"} / ${row.layer_count ?? "—"} / ${row.combined_value ?? "—"}`];
      values.forEach((v: any): any => tr.append(UI.el("td", v)));
      table.append(tr);
    }
    const scroll = UI.el("div", null, "table-scroll");
    scroll.append(table);
    box.append(scroll);
    if (rows.length > 250) box.append(UI.el("p", `已显示前 250 条，共 ${rows.length} 条。`));
    box.append(UI.details("完整资源读数、来源、生命周期与实际账游标", ledger));
    section.append(box);
  };
  MC.renderResourceThresholds = (section: any, result: any, template: any): any => {
    const compact = (v: any): any => typeof v === "string" && v.length > 36 ? `${v.slice(0, 18)}…${v.slice(-8)}` : v ?? "—";
    const specs = [["resource_threshold_observers", "资源阈值观察"], ["resource_threshold_triggers", "阈值收益执行"]];
    for (const [field, title] of specs) {
      const ledger = result[field];
      if (!ledger) continue;
      const observer = field === specs[0][0],
        rows = ledger.trace || [],
        box = UI.el("details"),
        table = UI.el("table", null, "grid"),
        head = UI.el("tr");
      table.style.tableLayout = "fixed";
      table.style.width = "100%";
      const lifecycle = rows.some((row: any): any => row.lifecycle_id !== undefined);
      box.append(UI.el("summary", `${title}（${rows.length} 条，只读）`), UI.el("p", "观察到阈值变化、签发调度与实际执行分别记录。领域开始仅表示已开启后续定时逻辑；只有“收益已激活”表示已发放Buff，持续区间见效果轴。包含附加检查流水，此处不额外增加伤害。数量为整数原子，单位见完整资源账。"));
      [lifecycle ? "秒数 / 来源操作阶段" : "秒数 / 阶段", "父事件 / 执行标识", "观察项或绑定 / 来源", "结果", "阈值 / 之前 → 之后", "提供者", ...(lifecycle ? ["目标领域 / 实例"] : [])].forEach((v: any): any => head.append(UI.el("th", v)));
      table.append(head);
      if (lifecycle) box.append(UI.el("p", "阶段列沿用触发它的原资源操作，不代表领域在该阶段已经建立；实际控制提交顺序见生命周期执行流水。"));
      for (const row of rows.slice(0, 250)) {
        const id = row[observer ? "observer_id" : "binding_id"],
          matches = (ledger.source_adoptions || []).filter((r: any): any => r.id === id),
          source = matches.length === 1 && matches[0].status === "ready" ? matches[0] : {};
        const name = source.name_zh ? `${DS.prose(source.name_zh, "来源规则")}（${id}）` : id ?? "—",
          t = row[observer ? "t_us" : "time_us"],
          tr = UI.el("tr");
        const outcomes: any = {
          activated: "收益已激活",
          not_activated: "未激活（条件不满足或冷却中）",
          pending: "待核实",
          invalid: "无效",
          failed: "失败",
          lifecycle_started: "领域已开始",
          lifecycle_kept: "保留原领域",
          lifecycle_ended: "领域已结束",
          no_active_instance: "没有活动领域，无需结束"
        };
        const outcome = observer ? row.crossed === true ? "已跨越阈值" : row.crossed === false ? "未跨越阈值" : "观察结果未知" : row.type === "threshold_dispatch_failed" ? `派发失败：${outcomes[row.outcome] || "结果未知"}${row.message || row.code ? `；${row.message || row.code}` : ""}` : row.type === "threshold_dispatch_completed" ? outcomes[row.outcome] || "执行结果未知" : "执行过程未知";
        const comparison = ({
            enter_gte: "向上达到",
            enter_lte: "向下达到"
          } as Record<string, any>)[row.comparison] || "方向未知",
          owner = row.owner ?? source.owner;
        const quantity = row.input_kind === "counter_value_key" || row.counter_observation && typeof row.counter_observation === "object" ? "消费累计" : "资源余额";
        const values = [`${Number.isSafeInteger(t) ? t / 1000000 : "—"} / ${({
          pre: "事件前",
          post: "事件后"
        } as Record<string, any>)[row.phase] || "阶段未知"}`, `${(observer ? row.event_id : row.source_event_id) ?? "—"} / ${(observer ? row.run_id : row.source_run_id) ?? "—"}`, `${name} / ${row.source_id ?? source.source_id ?? "—"}`, outcome, `${quantity} ${comparison} ${row.threshold_atoms ?? "—"} / ${row.before ?? "—"} → ${row.after ?? "—"}`, owner ? DS.character(owner, template) : "—", ...(lifecycle ? [`${row.lifecycle_id ?? "—"} / ${row.target_generation_uid ?? "—"}`] : [])];
        values.forEach((v: any, index: any): any => {
          const shown = lifecycle && index === 1 ? `${compact(observer ? row.event_id : row.source_event_id)} / ${compact(observer ? row.run_id : row.source_run_id)}` : lifecycle && index === 6 ? `${compact(row.lifecycle_id)} / ${compact(row.target_generation_uid)}` : v;
          const cell = UI.el("td", shown);
          if (shown !== v) cell.title = v;
          cell.style.overflowWrap = "anywhere";
          cell.style.whiteSpace = "normal";
          tr.append(cell);
        });
        table.append(tr);
      }
      const scroll = UI.el("div", null, "table-scroll");
      scroll.append(table);
      box.append(scroll);
      if (rows.length > 250) box.append(UI.el("p", `已显示前 250 条，共 ${rows.length} 条。`));
      box.append(UI.details("完整阈值观察、调度授权、实际结果及来源核对", ledger));
      section.append(box);
    }
  };
  MC.renderAttributeCallbacks = (section: any, result: any, template: any): any => {
    const ledger = result.attribute_callbacks;
    if (!ledger) return;
    const box = UI.el("details"),
      table = UI.el("table", null, "grid"),
      head = UI.el("tr"),
      rows = ledger.trace || [];
    const stages: any = {
      callback_plan: "计划已生成",
      callback_batch_prepared: "批次取样已准备",
      callback_committed: "回调已提交"
    };
    const names = new Map<any, any>((ledger.source_adoption?.members || []).map((r: any): any => [r.rule_id, r.name_zh]));
    box.append(UI.el("summary", `属性回调执行（${rows.length} 条，只读）`), UI.el("p", "计划、取样准备与实际提交分开记录；只有“回调已提交”表示已执行。手工顺序是计算假设，不是数据库已认证的游戏事实。实际取样数值见属性账，此处不增加伤害。"));
    ["秒数 / 来源窗口", "父命中 / 执行标识", "过程", "规则 / 来源", "提供者", "批次 / 属性帧"].forEach((v: any): any => head.append(UI.el("th", v)));
    table.append(head);
    for (const row of rows.slice(0, 250)) {
      const tr = UI.el("tr"),
        origin = Number.isInteger(row.origin) && row.origin >= 0 ? row.origin === 0 ? "完整输入（0）" : `附加检查（${row.origin}）` : "窗口未知";
      const values = [`${Number.isFinite(row.time_us) ? row.time_us / 1000000 : "—"} / ${origin}`, `${row.parent_event_id ?? "—"} / ${row.parent_run_id ?? "—"}`, stages[row.type] || "过程未知", `${names.get(row.rule_id) ? DS.prose(names.get(row.rule_id), "来源规则") + `（${row.rule_id}）` : row.rule_id ?? "—"} / ${row.source_id ?? "—"}`, row.owner ? DS.character(row.owner, template) : "—", `${row.batch ?? "—"} / ${row.frame_id ?? "—"}`];
      values.forEach((v: any): any => tr.append(UI.el("td", v)));
      table.append(tr);
    }
    const scroll = UI.el("div", null, "table-scroll");
    scroll.append(table);
    box.append(scroll);
    if (rows.length > 250) box.append(UI.el("p", `已显示前 250 条，共 ${rows.length} 条。`));
    box.append(UI.details("完整回调计划、依赖足迹、实际提交与来源核对", ledger));
    section.append(box);
  };
  MC.renderAttributeLedger = (section: any, result: any, template: any): any => {
    const ledger = result.attribute_ledger;
    if (!ledger) return;
    const multi = ledger.contract === "attribute-ledger-v3",
      stats: any = {
        em: "元素精通",
        atk: "攻击力"
      },
      bases: any = {
        total: "总量",
        nonextra: "非额外量",
        base: "基础量"
      };
    const box = UI.el("details"),
      table = UI.el("table", null, "grid"),
      head = UI.el("tr"),
      rows = ledger.receipts || [];
    const phases: any = {
      initial: "初始状态",
      pre: "事件前",
      post: "事件后",
      reaction_created: "反应产生时",
      reaction_damage_pre: "反应出伤前",
      capture: "面板取样",
      trigger: "触发取样",
      damage_projection: "出伤属性投影"
    };
    box.append(UI.el("summary", `属性取样账（${rows.length} 条，只读）`), UI.el("p", "显示引擎实际读取的基底与最终值。来源窗口0对应完整输入，附加检查窗口不增加伤害汇总；取样本身不代表已计入伤害，最终以伤害报告为准。未知值保留为空；修改面板、来源分类或动作后需重新计算。"));
    ["秒数 / 来源窗口 / 阶段", "角色", ...(multi ? ["属性"] : []), "取值口径", "基底", "实际取值", "状态"].forEach((v: any): any => head.append(UI.el("th", v)));
    table.append(head);
    for (const row of rows.slice(0, 250)) {
      const q = row.query || {},
        f = q.frame || {},
        origin = Number.isInteger(f.origin) && f.origin >= 0 ? f.origin === 0 ? "完整输入（0）" : `附加检查（${f.origin}）` : "窗口未知",
        tr = UI.el("tr"),
        values = [`${Number.isFinite(f.t_us) ? f.t_us / 1000000 : "—"} / ${origin} / ${phases[f.phase] || MC.label(f.phase) || "—"}`, DS.character(q.actor, template), ...(multi ? [stats[q.stat] || q.stat || "—"] : []), multi ? bases[q.basis] || q.basis || "—" : MC.label(q.basis), row.baseline ?? "—", row.value ?? "—", ({
          known: "已知",
          pending: "待核实",
          invalid: "无效"
        } as Record<string, any>)[row.status] || row.status];
      values.forEach((v: any): any => tr.append(UI.el("td", v ?? "—")));
      table.append(tr);
    }
    const scroll = UI.el("div", null, "table-scroll");
    scroll.append(table);
    box.append(scroll);
    if (rows.length > 250) box.append(UI.el("p", `已显示前 250 条，共 ${rows.length} 条。`));
    if (multi) {
      const ratios: any = [];
      for (const row of rows.slice(0, 250)) for (const item of row.contributions || []) {
        if (item.application?.kind === "recipient_base_fraction" && ratios.length < 100) ratios.push({
          row,
          item
        });
      }
      if (ratios.length) {
        const details = UI.el("details"),
          grid = UI.el("table", null, "grid"),
          heading = UI.el("tr"),
          holder = UI.el("div", null, "table-scroll");
        details.append(UI.el("summary", `比例加算明细（${ratios.length} 条，只读）`), UI.el("p", "展示前250条取样回执中的至多100项比例加算。比例先取样与封顶，再乘受益者基础攻击一次；空值代表尚未获得计算结果。完整值与引用见原始属性账。"));
        ["受益角色 / 属性", "提供者 / 来源部件 / 收益", "实际比例（1为100%）", "使用的基础攻击力", "乘后平值", "输入回执 / 基础攻击力回执"].forEach((v: any): any => heading.append(UI.el("th", v)));
        grid.append(heading);
        for (const {
          row,
          item
        } of ratios) {
          const source = item.component || {},
            tr = UI.el("tr");
          [`${DS.character(row.query?.actor, template)} / ${stats[item.stat] || item.stat || "—"}`, `${DS.character(source.provider, template)} / ${source.part_id || "—"} / ${source.effect_id || "—"}`, item.raw_evaluated_value ?? "—", item.application_base_value ?? "—", item.evaluated_value ?? "—", `${item.input_receipt_id || "常量来源"} / ${item.application_base_receipt_id || "—"}`].forEach((v: any): any => tr.append(UI.el("td", v)));
          grid.append(tr);
        }
        holder.append(grid);
        details.append(holder);
        box.append(details);
      }
    }
    box.append(UI.details("完整属性账与来源组成", ledger));
    section.append(box);
  };
  MC.renderStateRuntime = (section: any, result: any): any => {
    const state = result.state_runtime;
    if (!state) return;
    const box = UI.el("details"),
      table = UI.el("table", null, "grid"),
      head = UI.el("tr"),
      rows = state.execution_trace || [];
    const positioned = state.contract === "finite-state-mechanics-bridge-v2";
    const compact = (v: any): any => typeof v === "string" && v.length > 36 ? `${v.slice(0, 18)}…${v.slice(-8)}` : v ?? "—";
    if (positioned) {
      table.style.tableLayout = "fixed";
      table.style.width = "100%";
    }
    const names = new Map<any, any>((result.mechanism_execution?.model?.state_runtime?.lifecycles || []).map((r: any): any => [r.id, r.name_zh]));
    box.append(UI.el("summary", `生命周期与定时执行（${rows.length} 条，只读）`), UI.el("p", "领域调度与实际收益分开记录；结束领域会停止后续发放，已有收益的残留时间见效果轴。这里的记录不额外增加伤害或Buff。"));
    if (positioned) box.append(UI.el("p", "逐步提交与整批完成分开；签发或领取批次不等于执行成功。失败时仅显示已完成前缀，不将它当作完整轴结果。"));
    ["秒数", "领域 / 定时器", "次数", "实际结果", "操作 / 目标", "来源动作", ...(positioned ? ["控制序号 / 批次 / 完成步数"] : [])].forEach((v: any): any => head.append(UI.el("th", v)));
    table.append(head);
    for (const row of rows.slice(0, 250)) {
      const tr = UI.el("tr"),
        payload = row.payload || {},
        values = [Number.isFinite(row.time_us) ? row.time_us / 1000000 : "—", `${names.get(row.lifecycle_id) || row.lifecycle_id || "—"} / ${row.timer_id || "—"}`, row.ordinal ?? "—", ["state_dispatch_failed", "state_control_failed", "state_delivery_failed"].includes(row.type) ? `失败：${row.message || row.reason || "详见完整回执"}` : row.type === "state_control_completed" ? positioned ? "领域控制已提交" : "累计控制已提交" : row.type === "state_batch_completed" ? "控制批次已完成" : ({
          activated: "收益已激活",
          not_activated: "条件不满足或冷却中",
          resource_operation: "资源操作已处理",
          pending: "待核实",
          failed: "失败"
        } as Record<string, any>)[row.outcome] || MC.label(row.outcome), `${MC.label(payload.kind || (positioned ? row.kind : null)) || "—"} / ${payload.ref_id || (positioned ? row.lifecycle_id : null) || "—"}`, row.source_event_id || row.event_run_id || "—", ...(positioned ? [`${row.control_sequence ?? "—"} / ${row.batch_id ?? "—"} / ${row.step_count ?? row.applied_steps ?? "—"}`] : [])];
      values.forEach((v: any, index: any): any => {
        const shown = positioned && index === 6 ? `${row.control_sequence ?? "—"} / ${compact(row.batch_id)} / ${row.step_count ?? row.applied_steps ?? "—"}` : positioned && index === 5 ? compact(v) : v;
        const cell = UI.el("td", shown ?? "—");
        if (shown !== v) cell.title = v;
        if (positioned) {
          cell.style.overflowWrap = "anywhere";
          cell.style.whiteSpace = "normal";
        }
        tr.append(cell);
      });
      table.append(tr);
    }
    const scroll = UI.el("div", null, "table-scroll");
    scroll.append(table);
    box.append(scroll);
    if (rows.length > 250) box.append(UI.el("p", `已显示前 250 条，共 ${rows.length} 条。`));
    box.append(UI.details("完整生命周期调度与执行回执", state));
    section.append(box);
  };
  MC.renderTrace = (result: any, template: any): any => {
    const section = UI.el("section", null, "eq-card");
    section.append(UI.el("h3", `机制执行：${MC.label(result.mechanics_status)}`), UI.el("p", "下方记录属于本次计算快照；输入变化后须重新计算。能量与动作的检查结论单独列示。"));
    if (result.mechanics_issues?.length) section.append(UI.details("机制问题定位", result.mechanics_issues));
    if (result.elemental?.evidence_tier === "experimental_reference") section.append(UI.el("p", "本次元素状态采用实验参考模型；冻结量合并、事件聚合和取值时点含已接受的假设。"), UI.details("本次参考模型与采用依据", {
      contract: result.elemental.reference_contract,
      acceptance: result.elemental.reference_acceptance
    }));
    extensions.SC?.renderTrace(section, result);
    const rows = (result.mechanics_trace || []).filter((r: any): any => !["target_condition", "snapshot_capture", "snapshot_capture_reference"].includes(r.type)),
      table = UI.el("table"),
      head = UI.el("tr");
    ["秒数", "过程", "角色 / 规则", "收益 / 原因"].forEach((v: any): any => head.append(UI.el("th", v)));
    table.append(head);
    const names = new Map<any, any>((RS.activeSnapshot()?.template?.mechanics_model?.rules || []).map((r: any): any => [r.id, r.name_zh]));
    const hits = new Map<any, any>((result.hits || []).map((h: any): any => [h.id, h]));
    for (const row of rows.slice(0, 250)) {
      let detail = row.reason ? DS.prose(MC.label(row.reason), "详见原始定位") : row.value ?? "";
      if (row.type === "quota") detail = `剩余次数 ${row.before} → ${row.after}${detail ? ` · ${detail}` : ""}`;
      if (row.type === "activation") detail = `当前层数 ${row.layers}；到期秒数 ${(row.expires_at || []).join("、")}；次数池 ${Object.entries(row.quota || {}).map(([cid, n]: any): any => `${cid === "team" ? "全队" : DS.character(cid, template)} ${n}`).join("、") || "无"}`;
      if (row.beneficiary_selection && extensions.EFX) detail += `；${extensions.EFX.selectionDescription(row.beneficiary_selection, (id: any): any => DS.character(id, template)).join("；")}`;
      if (row.type === "expiry") detail = `层数 ${row.layers_before} → ${row.layers_after}`;
      if (row.type === "hit") detail = `${row.reported_damage ? "已报告的命中（伤害汇总统计完整输入窗口）" : "仅参与机制、不计伤害报告"}；生效收益 ${row.effect_count}`;
      if (row.type === "elemental_application") {
        const reactions = Array.isArray(row.reactions) ? row.reactions : row.reaction ? [row.reaction] : [];
        const reaction = reactions.map((r: any): any => `${MC.label(r.type)}${r.reaction_element ? `（${MC.label(r.reaction_element)}）` : ""}${Number.isFinite(r.factor) ? ` ×${r.factor}` : ""}`).join("、");
        detail = `${MC.label(row.reason)}；${MC.elementalStateText(row.before)} → ${MC.elementalStateText(row.after)}${reaction ? `；${reaction}` : ""}`;
      }
      if (row.type === "elemental_state_transition") detail = `${MC.label(row.reason)}；${MC.elementalStateText(row.before)} → ${MC.elementalStateText(row.after)}`;
      if (row.type === "reaction_damage_attempt") detail = `${row.allowed === true ? "允许出伤" : row.allowed === false ? "伤害冷却阻止出伤" : "伤害规则待核实"}；反应已独立记录，出伤受阻不撤销耗底。`;
      if (row.type === "reaction_damage") detail = "独立派生的扩散伤害；提供者参数在反应产生时取值，敌方状态在出伤前取值。";
      const tr = UI.el("tr"),
        values = [row.t, DS.prose(row.type === "quota" ? "次数消费" : MC.label(row.type), "执行记录"), row.type === "hit" && hits.has(row.event_id) ? DS.character(hits.get(row.event_id).character, template) : DS.prose(row.name_zh || names.get(row.rule_id), row.character ? DS.character(row.character, template) : "角色或机制见原始定位"), detail];
      values.forEach((v: any): any => tr.append(UI.el("td", v ?? "—")));
      table.append(tr);
    }
    section.append(table);
    if (rows.length > 250) section.append(UI.el("p", `已显示前 250 条，共 ${rows.length} 条，完整记录见下方。`));
    section.append(UI.details("完整机制流水（含事件定位）", result.mechanics_trace || []), UI.details("属性取值与依赖", result.sampling_trace || []));
    extensions.RR?.render(section, result, template);
    MC.renderSharedPools(section, result, template);
    MC.renderResourceCounters(section, result);
    MC.renderResourceCounterViews(section, result, template);
    MC.renderResourceThresholds(section, result, template);
    MC.renderAttributeCallbacks(section, result, template);
    MC.renderAttributeLedger(section, result, template);
    MC.renderStateRuntime(section, result);
    extensions.REM?.renderResult(section, result, template);
    extensions.SI?.renderResult(section, result, template);
    MC.renderTargetConditions(section, result, template);
    extensions.AE?.renderResult(section, result);
    return section;
  };
  MC.renderTargetConditions = (box: any, result: any, template: any): any => {
    const rows = result.target_condition_trace || [],
      sources = result.fixed_effect_sources || [];
    if (!rows.length && !sources.length) return;
    const details = UI.el("details"),
      table = UI.el("table"),
      head = UI.el("tr");
    details.append(UI.el("summary", `逐击目标条件（${rows.length} 条，只读）`), UI.el("p", "采用每击附着前的独立目标样本；与角色面板快照分开。人工覆盖不算作自动成立，改动动作或附着后需重新计算。"));
    ["秒数／检查窗口", "来源／命中", "命中前目标", "自动条件", "人工覆盖", "最终采用"].forEach((v: any): any => head.append(UI.el("th", v)));
    table.append(head);
    const hits = new Map<any, any>((result.hits || []).map((h: any): any => [h.id, h]));
    for (const row of rows.slice(0, 250)) {
      const sample = row.target_sample,
        state = sample?.status === "known" ? MC.elementalStateText(sample.state) : "未知或不适用";
      const tr = UI.el("tr"),
        hit = hits.get(row.event_id),
        values = [`${row.t} 秒／${row.cycle}`, `${DS.fixedSource(row.fixed_source_id, sources)} · ${hit ? DS.hit(hit, undefined, template) : "事件见原始定位"}`, state, DS.targetTruth(row.automatic_value), row.override === "include" ? "手工计入" : row.override === "exclude" ? "手工排除" : "无", `${row.applied ? "采用" : "未采用"} · ${MC.label(row.reason)}${row.reported_damage === false ? "（不计伤害报告）" : ""}`];
      values.forEach((v: any): any => tr.append(UI.el("td", v)));
      table.append(tr);
    }
    details.append(table);
    if (rows.length > 250) details.append(UI.el("p", "仅展示前 250 条；完整记录可展开核对。"));
    details.append(UI.details("完整条件样本与事件定位", rows), UI.details("固定来源原始声明", sources));
    box.append(details);
  };
}
