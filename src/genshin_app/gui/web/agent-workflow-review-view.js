(function (global) {
  "use strict";

  const WORKFLOW_STAGES = Object.freeze([
    Object.freeze({id:"build_setup", label:"初始配装", short:"配装"}),
    Object.freeze({id:"action_checkpoint", label:"动作检查点", short:"动作"}),
    Object.freeze({id:"event_topology", label:"事件拓扑", short:"事件"}),
    Object.freeze({id:"numeric_binding", label:"数值范式", short:"数值"}),
    Object.freeze({id:"strict_calculation", label:"严格计算", short:"计算"}),
  ]);
  const STAGE_ALIASES = Object.freeze({
    setup:"build_setup", build:"build_setup", loadout:"build_setup", loadout_draft:"build_setup", build_setup:"build_setup",
    actions:"action_checkpoint", action:"action_checkpoint", action_skeleton:"action_checkpoint", action_checkpoint:"action_checkpoint",
    topology:"event_topology", events:"event_topology", event_topology:"event_topology", event_topology_draft:"event_topology",
    numeric:"numeric_binding", binding:"numeric_binding", numeric_model:"numeric_binding", numeric_binding:"numeric_binding", numeric_model_ready:"numeric_binding",
    calculate:"strict_calculation", calculated:"strict_calculation", calculation:"strict_calculation", calculation_closure:"strict_calculation", strict_calculation:"strict_calculation",
  });
  const READ_RESOURCES = new Set(["baseline", "candidate", "original_board", "candidate_board"]);
  const DRAFT_FIELDS = Object.freeze({
    team:"队伍", actions:"动作", title:"白板名称", duration:"轴长（秒）", initial_frontline:"初始前台角色", notes:"备注",
    id:"标识", name:"名称", db_character_id:"数据库角色编号", level:"等级", constellation:"命座", weapon:"武器", weapon_id:"武器编号",
    refinement:"精炼等级", ascension:"突破阶段", artifact_sets:"圣遗物套装", set_id:"套装编号", pieces:"件数", count:"件数",
    panel:"成品与基础面板", panel_basis_mode:"基础值来源", atk:"攻击力", hp:"生命值", def:"防御力", base_atk:"基础攻击力",
    base_hp:"基础生命值", base_def:"基础防御力", em:"元素精通", crit_rate:"暴击率", crit_dmg:"暴击伤害",
    energy_recharge:"元素充能效率", dmg_bonus:"伤害加成", all:"全伤害", physical:"物理", pyro:"火元素", hydro:"水元素",
    electro:"雷元素", cryo:"冰元素", anemo:"风元素", geo:"岩元素", dendro:"草元素", character:"动作角色",
    action_type_id:"动作类型", label:"动作名称", start:"释放时间（秒）", variant:"动作形式",
  });
  const required = (options, name) => {
    const value = options[name];
    if (typeof value !== "function") throw new TypeError(`workflow_review_${name}_missing`);
    return value;
  };
  const firstDefined = (...values) => values.find(value => value !== undefined && value !== null);
  const stageId = value => STAGE_ALIASES[String(value || "").toLowerCase()] || null;
  const receiptFrom = (key, ...sources) => {
    for (const source of sources) {
      if (!source || typeof source !== "object") continue;
      for (const container of [source, source.summary, source.workflow_state, source.extensions,
        source.extensions?.workflow_state, source.report, source.result, source.result?.report]) {
        if (container && typeof container === "object" && container[key] && typeof container[key] === "object") return container[key];
      }
    }
    return null;
  };
  const topologyReceipt = (...sources) => receiptFrom("event_topology_coverage_receipt", ...sources)
    || receiptFrom("event_topology_receipt", ...sources);
  const executionBindingReceipt = (...sources) => receiptFrom("event_topology_execution_binding_receipt", ...sources)
    || receiptFrom("event_topology_binding_receipt", ...sources);
  const acceptanceValue = value => typeof value === "object" && value !== null
    ? firstDefined(value.status, value.state, value.kind, value.accepted) : value;
  const acceptanceStatus = raw => {
    const value = acceptanceValue(raw);
    if (value === true) return "accepted";
    if (value === false) return "rejected";
    const text = String(value || "").toLowerCase();
    if (["accepted", "passed", "ready", "applicable", "can_accept"].includes(text)) return "accepted";
    if (["rejected", "failed", "blocked", "diagnostic", "diagnostic_only", "not_applicable", "executed_prefix"].includes(text)) {
      return text === "executed_prefix" ? "diagnostic_only" : text;
    }
    return "not_requested";
  };
  const workflowState = (session = null, candidate = null, documentValue = null, result = null) => {
    candidate = candidate || session?.candidate || null;
    documentValue = documentValue || session?.base?.document || null;
    const summary = candidate?.summary && typeof candidate.summary === "object" ? candidate.summary : {};
    const report = candidate?.report || summary.report || {};
    const workflow = candidate?.workflow_state || summary.workflow_state || documentValue?.workflow_state || documentValue?.extensions?.workflow_state || {};
    let stage = stageId(firstDefined(candidate?.workflow_stage, candidate?.stage, summary.workflow_stage, summary.stage, workflow.stage, session?.workflow_stage));
    if (!stage && result) stage = "strict_calculation";
    if (!stage && session?.workflow === "calculate") stage = "strict_calculation";
    if (!stage) {
      const teamCount = firstDefined(summary.character_count, summary.team_count, summary.characters, documentValue?.team?.length, 0);
      const actionCount = firstDefined(summary.action_count, summary.actions, documentValue?.actions?.length, 0);
      stage = !teamCount || !actionCount ? "build_setup" : "action_checkpoint";
    }
    const index = Math.max(0, WORKFLOW_STAGES.findIndex(row => row.id === stage));
    const stageStatus = String(firstDefined(candidate?.stage_status, summary.stage_status, workflow.stage_status, candidate?.ready ? "complete" : "in_progress") || "in_progress");
    const saveable = Boolean(firstDefined(candidate?.saveable, summary.saveable, workflow.saveable, candidate?.ready, documentValue?.kind === "genshin-whiteboard", false));
    const interpretation = firstDefined(candidate?.damage_interpretation, summary.damage_interpretation, report.damage_interpretation,
      result?.damage_interpretation, result?.report?.damage_interpretation);
    const acceptanceInput = firstDefined(candidate?.calculation_acceptance, summary.calculation_acceptance, report.calculation_acceptance,
      result?.calculation_acceptance, result?.report?.calculation_acceptance,
      interpretation?.kind === "executed_prefix" ? "diagnostic_only" : undefined, candidate?.diagnostic_attempt ? "diagnostic_only" : undefined);
    let calculationAcceptance = acceptanceStatus(acceptanceInput);
    if (candidate?.can_accept === false && stage === "strict_calculation" && calculationAcceptance === "not_requested") calculationAcceptance = "rejected";
    const energyStatus = String(firstDefined(candidate?.energy_status, summary.energy_status, workflow.energy_status, index < 3 ? "not_checked" : undefined) || "unknown");
    const topology = topologyReceipt(candidate, documentValue, result);
    const binding = executionBindingReceipt(candidate, documentValue, result);
    return Object.freeze({stage, index, stageStatus, saveable, calculationAcceptance, energyStatus, interpretation,
      topologyReceipt:topology, executionBindingReceipt:binding,
      diagnostic:["rejected", "failed", "blocked", "diagnostic_only", "not_applicable"].includes(calculationAcceptance)});
  };
  const stageStatusText = value => ({complete:"本阶段完成", ready:"本阶段完成", in_progress:"正在完善", partial:"仍有缺项", blocked:"存在阻断", failed:"未通过"}[value] || "状态待确认");
  const acceptanceText = value => ({accepted:"可应用", rejected:"已拒绝", failed:"未通过", blocked:"存在阻断", diagnostic_only:"仅诊断，不可应用", not_applicable:"不可应用", not_requested:"尚未请求"}[value] || "尚未请求");
  const energyText = value => ({not_checked:"未检查", passed:"已通过", ready:"已通过", failed:"未通过", blocked:"存在阻断", unknown:"待后续检查"}[value] || "待后续检查");
  const topologyGateText = (receipt, index) => {
    if (!receipt) return index < 2 ? "尚未开始" : "尚未返回回执";
    const status = String(receipt.status || receipt.stages?.topology?.status || "").toLowerCase();
    return ({numeric_ready:"结构完整", topology_ready:"结构完整", complete:"结构完整", ready:"结构完整", passed:"结构完整",
      draft:"正在建立", in_progress:"正在建立", partial:"待完善", incomplete:"待完善", blocked:"存在阻断", invalid:"无效", failed:"未通过"}[status] || "状态待确认");
  };
  const executionBindingFacts = receipt => {
    const counts = receipt?.counts && typeof receipt.counts === "object" ? receipt.counts : {};
    const coverage = receipt?.coverage && typeof receipt.coverage === "object" ? receipt.coverage : {};
    const allMissing = Array.isArray(coverage.missing_node_ids) ? coverage.missing_node_ids : [];
    return Object.freeze({status:String(receipt?.status || "").toLowerCase(), expected:firstDefined(counts.expected_nodes, counts.expected, 0),
      bound:firstDefined(counts.bound_nodes, counts.bound, 0), resolved:firstDefined(counts.resolved_bindings, counts.resolved, 0),
      missing:Object.freeze(allMissing.slice(0, 20)), missingTruncated:Boolean(coverage.missing_node_ids_truncated) || allMissing.length > 20,
      issue:Array.isArray(receipt?.issues) ? receipt.issues[0] : null});
  };
  const bindingGateText = (receipt, index) => {
    if (!receipt) return index < 3 ? "尚未建立" : "尚未返回回执";
    const facts = executionBindingFacts(receipt);
    const status = ({complete:"完整", incomplete:"待完善", invalid:"无效", failed:"未通过", blocked:"存在阻断"}[facts.status] || "状态待确认");
    return `${status} · 已绑定 ${facts.bound}/${facts.expected} · 已解析 ${facts.resolved}`;
  };
  const bindingIssueText = issue => {
    const value = typeof issue === "string" ? issue : issue?.message || issue?.code || "存在未说明的问题";
    const text = String(value); return text.length > 512 ? text.slice(0, 512) + "…" : text;
  };
  const damageDisclosurePath = row => /(^|\/)(result|damage_(?:total|value|amount)|calculation_totals|dps|expected|noncrit|crit|hit_damage)(\/|$)/i.test(String(row?.path || row?.kind || ""));
  const topologySummary = candidate => topologyReceipt(candidate) || candidate?.topology_receipt || candidate?.summary?.event_topology
    || candidate?.event_topology || candidate?.event_topology_draft || candidate?.summary?.event_topology_draft || candidate?.workflow_state?.event_topology || null;
  const topologyValue = (value, path = "") => {
    if (value == null) return "未填写";
    if (value?.preview) return "结构内容较大；本阶段不展开原文或参数值";
    if (Array.isArray(value)) return `结构列表 · ${value.length} 项`;
    if (typeof value === "object") {
      if (value.node_id) return `事件节点 ${value.node_id} · ${value.kind || "类型待补"} · ${value.status || "状态待补"} · 调度 ${value.schedule?.kind || "待补"} · 参数槽 ${value.parameter_slots?.length || 0}`;
      if (value.edge_id) return `拓扑关系 ${value.edge_id} · ${value.kind || "类型待补"} · ${value.status || "状态待补"}`;
      if (value.contract_id) return `${value.contract_id} · ${value.status || value.declaration?.completeness || "结构草稿"} · 节点 ${value.counts?.nodes ?? value.nodes?.length ?? 0} · 拓扑关系 ${value.counts?.edges ?? value.edges?.length ?? 0}`;
      return "结构化内容；本阶段不披露资料原文或参数值";
    }
    const leaf = String(path).split("/").filter(Boolean).at(-1) || "";
    return ["status", "kind", "node_id", "edge_id", "topology_id", "completeness", "disposition", "stage", "required_for"].includes(leaf)
      ? String(value) : "本阶段隐藏参数值";
  };

  function create(options = {}) {
    const document = options.document, ui = options.ui, state = options.state, nodes = options.nodes;
    if (!document || !ui || !state || !nodes) throw new TypeError("workflow_review_renderer_ports_missing");
    const selected = required(options, "selected"), readModel = required(options, "readModel");
    const active = required(options, "active"), refresh = required(options, "refresh"), notice = required(options, "notice");
    const errorText = required(options, "errorText"), clone = required(options, "clone"), text = required(options, "text");
    const defaultWorkflow = required(options, "defaultWorkflow"), checkpointFrozen = required(options, "checkpointFrozen");
    const hasUnsettledAppAction = required(options, "hasUnsettledAppAction"), appliedCandidate = required(options, "appliedCandidate");
    const reviewCandidate = required(options, "reviewCandidate"), onApply = required(options, "onApply");
    const loadCandidate = required(options, "loadCandidate"), readCandidate = required(options, "readCandidate");
    const catalogName = required(options, "catalogName"), actionTypeName = required(options, "actionTypeName");
    const draftName = (group, id, session) => catalogName(group, id, session);

    const isBuild = session => session?.kind === "whiteboard" && session?.workflow === "build";
    const isStaged = session => session?.kind === "whiteboard" && session?.workflow === "staged";
    const isAuthoring = session => isBuild(session) || isStaged(session);
    const workflow = () => readModel(selected())?.workflow
      || (nodes.kind?.value === "whiteboard" ? nodes.workflow?.value || defaultWorkflow("whiteboard") : "calculate");
    const canApply = (session, candidate) => {
      const value = workflowState(session, candidate), draft = candidate?.application_kind === "whiteboard_draft";
      const validKind = !isAuthoring(session) || ["whiteboard_draft", "calculated_result"].includes(candidate?.application_kind);
      return !checkpointFrozen() && !hasUnsettledAppAction(session) && validKind && Boolean(candidate?.ready)
        && candidate.can_accept !== false && !candidate.stale && !value.diagnostic && !appliedCandidate(session, candidate)
        && (!draft || (value.saveable && candidate.has_calculation === false && (candidate.diff_total ?? candidate.diff?.length ?? 0) > 0));
    };
    const captureFence = (session, candidate) => Object.freeze({
      viewEpoch:state.viewEpoch, sessionEpoch:session?.epoch, sessionId:session?.session_id,
      revision:candidate?.revision, receipt:candidate?.receipt_fingerprint,
    });
    const fenceCurrent = (session, fence) => {
      const current = selected(), candidate = session?.candidate;
      return current?.session_id === fence.sessionId && state.viewEpoch === fence.viewEpoch
        && session?.epoch === fence.sessionEpoch && candidate?.revision === fence.revision
        && candidate?.receipt_fingerprint === fence.receipt && !active();
    };
    const draftLabel = path => String(path || "").split("/").filter(Boolean).map((key, index, parts) => /^\d+$/.test(key)
      ? `第 ${Number(key) + 1} ${parts[index - 1] === "team" ? "名角色" : parts[index - 1] === "actions" ? "个动作" : "项"}`
      : DRAFT_FIELDS[key] || "其他设置").join(" · ") || "白板设置";
    const draftValue = (value, path, session) => {
      if (value == null) return "未填写";
      if (Array.isArray(value)) return value.length ? value.slice(0, 50).map((row, index) => draftValue(row, `${path}/${index}`, session)).join("\n")
        + (value.length > 50 ? `\n另有 ${value.length - 50} 项，请展开完整输入核对。` : "") : "暂无";
      const key = String(path).split("/").at(-1);
      if (typeof value === "object") {
        if (value.action_type_id) return `${value.label || actionTypeName(value.action_type_id) || "动作"} · ${value.start} 秒 · ${session?.base?.document?.team?.find(actor => actor.id === value.character)?.name || value.character || "角色待补"}${value.variant ? ` · ${value.variant}` : ""}`;
        if (value.db_character_id) return `${value.name || "角色"} · ${value.level} 级 · ${value.constellation} 命\n武器：${draftValue(value.weapon, path + "/weapon", session)}\n套装：${draftValue(value.artifact_sets, path + "/artifact_sets", session)}\n面板：${Object.entries(value.panel || {}).filter(([, row]) => row != null && typeof row !== "object").map(([field, row]) => `${DRAFT_FIELDS[field] || "其他属性"} ${draftValue(row, path + "/panel/" + field, session)}`).join("、") || "尚未填写"}`;
        if (value.weapon_id) return `${value.name || value.weapon_name || draftName("weapons", value.weapon_id, session) || `武器编号 ${value.weapon_id}`} · ${value.level} 级 · 精炼 ${value.refinement ?? "待定"}`;
        if (value.set_id) return `${value.name || value.set_name || draftName("artifact_sets", value.set_id, session) || `套装编号 ${value.set_id}`} · ${value.pieces ?? value.count ?? "待定"} 件`;
        return Object.entries(value).map(([field, row]) => `${DRAFT_FIELDS[field] || "其他设置"}：${draftValue(row, path + "/" + field, session)}`).join("\n");
      }
      if (["weapon_id", "set_id", "db_character_id"].includes(key)) {
        const name = draftName({weapon_id:"weapons", set_id:"artifact_sets", db_character_id:"characters"}[key], value, session);
        return name ? `${name}（编号 ${value}）` : `编号 ${value}`;
      }
      if (key === "panel_basis_mode") return value === "database" ? "数据库自动同步" : value === "manual" ? "手动输入" : String(value);
      if (key === "action_type_id") return actionTypeName(value) || "动作类型待补";
      if (["crit_rate", "crit_dmg", "energy_recharge"].includes(key) || String(path).includes("/dmg_bonus/")) return typeof value === "number" ? `${Number((value * 100).toFixed(3))}%` : String(value);
      return typeof value === "number" ? value.toLocaleString("zh-CN", {maximumFractionDigits:3}) : String(value);
    };

    const renderStageTracker = (value, compact = false) => {
      const root = ui.el("section", null, `ac-stage-flow${compact ? " compact" : ""}`); root.setAttribute("aria-label", "排轴工作阶段");
      const steps = ui.el("ol", null, "ac-stage-steps");
      for (const [index, row] of WORKFLOW_STAGES.entries()) {
        const item = ui.el("li", row.short, index < value.index ? "complete" : index === value.index ? "current" : "pending"); item.title = row.label;
        if (index === value.index) item.setAttribute("aria-current", "step"); steps.append(item);
      }
      const meta = ui.el("div", null, "ac-stage-meta");
      meta.append(ui.el("span", WORKFLOW_STAGES[value.index]?.label || "阶段待确认"), ui.el("span", stageStatusText(value.stageStatus)),
        ui.el("span", `能量：${energyText(value.energyStatus)}`), ui.el("span", `草稿保存：${value.saveable ? "允许" : "待完善"}`));
      const gates = ui.el("div", null, "ac-stage-gates"); gates.setAttribute("aria-label", "拓扑结构、执行绑定与计算接纳");
      gates.append(ui.el("span", `拓扑结构：${topologyGateText(value.topologyReceipt, value.index)}`),
        ui.el("span", `执行绑定：${bindingGateText(value.executionBindingReceipt, value.index)}`),
        ui.el("span", `计算接纳：${acceptanceText(value.calculationAcceptance)}`));
      if (value.executionBindingReceipt) {
        const facts = executionBindingFacts(value.executionBindingReceipt);
        if (facts.missing.length) gates.append(ui.el("p", `执行绑定缺失节点：${facts.missing.join("、")}${facts.missingTruncated ? "（仅显示前几项）" : ""}`, "ac-stage-detail ac-warning"));
        if (facts.issue) gates.append(ui.el("p", `执行绑定首个问题：${bindingIssueText(facts.issue)}`, "ac-stage-detail ac-warning"));
        gates.append(ui.el("p", "角色特有语义仍由 Agent 声明；执行绑定回执未验证这些游戏事实。", "ac-stage-detail ac-note"));
      }
      if (value.diagnostic) root.classList.add("diagnostic"); root.append(steps, meta, gates); return root;
    };

    async function exploreCandidate(session, candidate, resource, pointer = "") {
      if (!session || active() || !READ_RESOURCES.has(resource) || typeof pointer !== "string") return false;
      const fence = captureFence(session, candidate), rootResource = resource;
      const body = ui.el("div", null, "ac-review-body"), location = ui.el("p", null, "ac-path"), error = ui.el("p", null, "ac-error"), nav = ui.el("div", null, "ac-actions");
      let request = 0, closed = false;
      const invalidate = () => { if (closed) return true; closed = true; request++; return true; };
      const dialog = global.AppDialogKernel?.create?.({title:"查看完整候选输入", className:"ac-review-dialog", onRequestClose:invalidate}) || null;
      if (!dialog) { notice("当前还有窗口正在处理，请先完成或关闭后再查看完整候选输入。", true); return false; }
      const close = () => { invalidate(); return dialog.finish?.("cancel") ?? true; };
      const load = async (nextResource, nextPointer, offset = 0) => {
        const token = ++request; error.textContent = ""; body.replaceChildren(ui.el("p", "正在读取…")); nav.replaceChildren();
        if (nextResource !== rootResource || !READ_RESOURCES.has(nextResource) || typeof nextPointer !== "string" || !Number.isSafeInteger(offset) || offset < 0) {
          error.textContent = "候选资源引用越界或分页位置无效，已拒绝读取。"; body.replaceChildren(); return;
        }
        const names = {baseline:"原输入", candidate:"候选输入", original_board:"原白板输入", candidate_board:"候选白板输入"};
        location.textContent = `${names[nextResource]} · ${nextPointer || "根节点"}`;
        if (!fenceCurrent(session, fence)) { error.textContent = "候选已更新、会话已切换或又开始运行，请返回并重新审核。"; body.replaceChildren(); return; }
        const result = await readCandidate(session, candidate, nextResource, nextPointer, offset, 50);
        if (closed || token !== request || !fenceCurrent(session, fence)) return;
        if (result?.stale) return;
        const response = result?.response;
        if (!response || response.code) { error.textContent = errorText(response, "此项暂时无法展开。"); body.replaceChildren(); return; }
        const data = response.data;
        if (!data || typeof data !== "object") { error.textContent = "候选资源响应格式无效。"; body.replaceChildren(); return; }
        body.replaceChildren();
        if (data.total != null) body.append(ui.el("p", `共 ${data.total} ${data.kind === "text" ? "字符" : "项"}，当前从 ${offset + 1} 开始`));
        if (Array.isArray(data.items)) for (const row of data.items) {
          const entry = ui.el("section", null, "ac-value-entry"); entry.append(ui.el("strong", String(row.key)));
          const ref = row.value?.reference;
          if (ref && typeof ref === "object") {
            if (ref.resource === rootResource && typeof ref.pointer === "string") entry.append(ui.el("p", `内容较大 · ${row.value.length ?? "未知"} 项或字符`), ui.button("展开此项", () => load(ref.resource, ref.pointer)));
            else entry.append(ui.el("p", "嵌套引用越过当前候选资源边界，已拒绝展开。", "ac-warning"));
          } else entry.append(ui.el("pre", text(row.value), "ac-readable"));
          body.append(entry);
        } else body.append(ui.el("pre", data.kind === "text" ? data.text : text(data.value), "ac-readable"));
        if (nextPointer) nav.append(ui.button("上一级", () => load(nextResource, nextPointer.slice(0, nextPointer.lastIndexOf("/")))));
        if (offset) nav.append(ui.button("回到第一页", () => load(nextResource, nextPointer, 0)));
        if (Number.isSafeInteger(data.next_offset) && data.next_offset >= 0) nav.append(ui.button("下一页", () => load(nextResource, nextPointer, data.next_offset)));
      };
      dialog.appBody.append(location, body, error, nav, ui.button("返回差异审核", close)); dialog.focusInitial?.();
      await load(resource, pointer); return dialog;
    }

    const renderDraftCell = (cell, value, row, session, candidate) => {
      const summary = ui.el("p", null, "ac-draft-value"); cell.append(summary);
      if (value?.preview && READ_RESOURCES.has(value.resource) && typeof value.pointer === "string") {
        const fence = captureFence(session, candidate); summary.textContent = "正在读取白板修改摘要…";
        cell.append(ui.button("展开完整值", () => exploreCandidate(session, candidate, value.resource, value.pointer)));
        (async () => {
          const result = await readCandidate(session, candidate, value.resource, value.pointer, 0, 50);
          if (!fenceCurrent(session, fence)) { summary.textContent = "候选已变化，请重新审核。"; return; }
          if (result?.stale) { summary.textContent = "候选已变化，请重新审核。"; return; }
          const response = result?.response;
          if (!response || response.code || !response.data || typeof response.data !== "object") { summary.textContent = "摘要暂不可用，请展开完整值核对。"; return; }
          const data = response.data;
          const raw = data.kind === "array" ? (data.items || []).map(item => item.value)
            : data.kind === "object" ? Object.fromEntries((data.items || []).map(item => [item.key, item.value]))
              : data.kind === "text" ? data.text : data.value;
          summary.textContent = draftValue(raw, row.path, session) + (data.next_offset != null ? `\n仅列出首批内容，共 ${data.total} 项；请展开完整值核对。` : "");
        })();
      } else {
        summary.textContent = draftValue(value, row.path, session);
        if (value && typeof value === "object") { const raw = ui.el("details"); raw.append(ui.el("summary", "原始字段"), ui.el("pre", text(value), "ac-readable")); cell.append(raw); }
      }
    };

    const renderDiff = (box, candidate, session = selected()) => {
      const value = workflowState(session, candidate), building = isBuild(session) || candidate.application_kind === "whiteboard_draft", topology = value.stage === "event_topology";
      const summary = candidate.summary;
      if (building) {
        box.append(ui.el("h4", `${WORKFLOW_STAGES[value.index].label}草稿`), ui.el("p", topology
          ? "本阶段只审核真实事件的结构、生命周期和绑定状态；尚未形成正式伤害结果，伤害数值也不会在这里披露。"
          : "尚未计算。应用后会更新当前阶段草稿；缺少后续事件、数值或能量检查时仍可保存并继续聊天。", "ac-note"));
        if (summary && typeof summary === "object") {
          const names = {characters:"角色数", actions:"动作数", character_count:"角色数", team_count:"角色数", action_count:"动作数", duration:"轴长（秒）"};
          for (const [key, label] of Object.entries(names)) if (Object.hasOwn(summary, key)) box.append(ui.el("p", `${label}：${text(summary[key])}`));
        }
      }
      if (topology) {
        const info = topologySummary(candidate) || summary;
        const fields = {node_count:"事件节点", event_count:"事件节点", component_count:"预期组件", expected_components:"预期组件", materialized_components:"已建立组件", binding_count:"拓扑关系", unresolved_count:"待完善组件", status:"拓扑状态"};
        box.append(ui.el("h4", "拓扑结构"));
        if (info && typeof info === "object") for (const [key, label] of Object.entries(fields)) if (Object.hasOwn(info, key)) box.append(ui.el("p", `${label}：${text(info[key])}`));
        if (info?.counts) { const counts = info.counts; box.append(ui.el("p", `动作 ${counts.actions ?? "—"} · 来源 ${counts.sources ?? "—"} · 事件节点 ${counts.nodes ?? "—"} · 拓扑关系 ${counts.edges ?? "—"} · 参数槽 ${counts.parameter_slots ?? "—"}`)); }
        if (info?.stages) box.append(ui.el("p", `拓扑结构检查：${info.stages.topology?.status || "待确认"} · 参数槽状态：${info.stages.numeric_binding?.status || "待确认"}`));
        if (!info || typeof info !== "object") box.append(ui.el("p", "后端尚未返回结构统计；可审核下方事件与绑定差异。", "ac-note"));
        const binding = value.executionBindingReceipt; box.append(ui.el("h4", "执行绑定"));
        if (binding) {
          const facts = executionBindingFacts(binding); box.append(ui.el("p", bindingGateText(binding, value.index)));
          if (facts.missing.length) box.append(ui.el("p", `缺失节点：${facts.missing.join("、")}${facts.missingTruncated ? "（仅显示前几项）" : ""}`, "ac-warning"));
          if (facts.issue) box.append(ui.el("p", `首个问题：${bindingIssueText(facts.issue)}`, "ac-warning"));
          box.append(ui.el("p", "这里只核验事件节点与当前可执行输入的覆盖和新鲜度；角色特有语义仍由 Agent 声明，未由 App 或引擎验证。", "ac-note"));
        } else box.append(ui.el("p", "尚未返回执行绑定回执。事件拓扑关系不能替代执行输入绑定。", "ac-warning"));
        box.append(ui.el("h4", "计算接纳"), ui.el("p", acceptanceText(value.calculationAcceptance)));
      }
      if (summary && !building && !topology) {
        box.append(ui.el("h4", value.diagnostic ? "严格计算诊断" : "候选说明"));
        if (value.diagnostic) box.append(ui.el("p", "严格计算未通过。本次内容只用于定位动作、能量、绑定或数值缺项，不构成可应用计算结果。", "ac-warning"));
        if (typeof summary === "object" && !Array.isArray(summary)) {
          const names = {total_damage:"总伤害", dps:"每秒伤害", reported_hits:"伤害事件数", hit_count:"伤害事件数", engine_damage:"引擎伤害", external_damage:"外部补充伤害", duration:"轴长（秒）", loop_duration:"轴长（秒）"};
          if (!value.diagnostic) for (const [key, label] of Object.entries(names)) if (Object.hasOwn(summary, key)) box.append(ui.el("p", `${label}：${summary[key] == null ? "未知" : typeof summary[key] === "number" ? summary[key].toLocaleString("zh-CN", {maximumFractionDigits:2}) : text(summary[key])}`));
          const disclosed = value.diagnostic ? Object.fromEntries(Object.entries(summary).filter(([key]) => !Object.hasOwn(names, key) && !/(damage|dps|hit)/i.test(key))) : summary;
          if (Object.keys(disclosed).some(key => !names[key])) { const raw = ui.el("details"); raw.append(ui.el("summary", value.diagnostic ? "诊断状态与原始字段" : "其他计算摘要与原始字段"), ui.el("pre", text(disclosed), "ac-readable")); box.append(raw); }
        } else box.append(ui.el("pre", text(summary), "ac-readable"));
      }
      if (candidate.changes?.length && !topology) { box.append(ui.el("h4", "本轮修改说明")); for (const change of candidate.changes) if (change.message) box.append(ui.el("p", change.message)); }
      const nativeDiff = candidate.diff, intentDiff = candidate.intent_diff;
      const diff = Array.isArray(intentDiff) && intentDiff.length ? [...intentDiff, ...(Array.isArray(nativeDiff) ? nativeDiff : [])] : nativeDiff;
      const visibleDiff = Array.isArray(diff) && topology ? diff.filter(row => typeof row === "string" || !damageDisclosurePath(row)) : diff;
      if (Array.isArray(visibleDiff)) {
        const table = ui.el("table", null, "ac-diff"), head = ui.el("tr"); ["项目", "原输入", "候选", "说明"].forEach(row => head.append(ui.el("th", row))); table.append(head);
        for (const row of visibleDiff.slice(0, 200)) {
          const tr = ui.el("tr");
          if (typeof row === "string") tr.append(ui.el("td", topology ? "结构项已变化" : row));
          else {
            const boardRow = !topology && (building || session?.kind === "whiteboard" && String(row.path).startsWith("/extensions/whiteboard_handoff/board/"));
            const boardPath = String(row.path || "").replace(/^\/extensions\/whiteboard_handoff\/board/, "");
            const name = ui.el("td", row.label || (boardRow ? draftLabel(boardPath) : row.path || row.kind || "计算输入"));
            if (boardRow && row.path) { const raw = ui.el("details"); raw.append(ui.el("summary", "字段位置"), ui.el("p", row.path, "ac-path")); name.append(raw); }
            tr.append(name);
            for (const item of [row.before, row.after]) {
              const cell = ui.el("td");
              if (topology) cell.textContent = topologyValue(item, row.path);
              else if (boardRow) renderDraftCell(cell, item, {...row, path:boardPath}, session, candidate);
              else if (item?.preview && READ_RESOURCES.has(item.resource) && typeof item.pointer === "string") cell.append(ui.el("p", `${item.preview}${item.count == null ? "" : `（${item.count} 项或字符）`}`), ui.button("展开完整值", () => exploreCandidate(session, candidate, item.resource, item.pointer)));
              else cell.textContent = text(item);
              tr.append(cell);
            }
            tr.append(ui.el("td", row.reason || row.message || ""));
          }
          table.append(tr);
        }
        box.append(table);
        if (topology && visibleDiff.length < diff.length) box.append(ui.el("p", `事件拓扑阶段已隐藏 ${diff.length - visibleDiff.length} 项伤害数值差异；进入数值范式或严格计算阶段后再披露。`, "ac-note"));
        if (candidate.diff_truncated || candidate.intent_diff_truncated || visibleDiff.length > 200) box.append(ui.el("p", `已显示 ${Math.min(200, visibleDiff.length)} 项差异，共 ${(candidate.diff_total || 0) + (candidate.intent_diff_total || 0) || visibleDiff.length} 项。`, "ac-warning"));
      } else if (diff && !topology) box.append(ui.el("h4", "输入差异"), ui.el("pre", text(diff), "ac-readable"));
      else if (diff && topology) box.append(ui.el("p", "拓扑差异缺少可分项结构，已暂缓显示以避免提前披露伤害数值。", "ac-warning"));
      if (session && !topology) {
        const browse = ui.el("div", null, "ac-actions"), original = session.workflow === "staged" && session.kind === "whiteboard" ? "original_board" : "baseline";
        browse.append(ui.button("查看完整原输入", () => exploreCandidate(session, candidate, original)), ui.button("查看完整候选输入", () => exploreCandidate(session, candidate, "candidate"))); box.append(browse);
      }
      const issues = candidate.issues || [];
      if (issues.length) { box.append(ui.el("h4", "仍需处理或核实")); for (const issue of issues) box.append(ui.el("p", typeof issue === "string" ? issue : issue.message || text(issue), "ac-issue")); }
      if (candidate.issues_truncated) box.append(ui.el("p", `已显示 ${issues.length} 项提示，共 ${candidate.issues_total} 项；可请 Agent 继续分组说明未展示的检查。`, "ac-warning"));
    };

    async function reviewCore(session) {
      if (!session || state.busy) return false;
      const initial = captureFence(session, session.candidate || {}), selectedId = session.session_id;
      state.busy = "review"; refresh();
      const result = await loadCandidate(session);
      if (state.busy === "review") state.busy = null;
      if (state.viewEpoch !== initial.viewEpoch || selected()?.session_id !== selectedId || session.epoch !== initial.sessionEpoch || active()) { refresh(); return false; }
      if (result?.stale) { refresh(); return false; }
      const response = result?.response;
      if (!result?.ok) { notice(result?.invalid ? "候选响应格式无效。" : errorText(response, "无法读取候选。"), true); refresh(); return false; }
      refresh();
      if (state.selected !== selectedId) return false;
      const candidate = clone(result.candidate), value = workflowState(session, candidate), stage = WORKFLOW_STAGES[value.index];
      const title=value.diagnostic ? "审核严格计算诊断" : isBuild(session) ? `审核白板构建草稿 · ${stage.label}` : `审核${stage.label}候选`,
        dialog=global.AppDialogKernel?.create?.({title,description:"核对 Agent 提交的候选差异。只有你明确应用后才会写入当前文档。",className:"ac-review-dialog"})||null;
      if(!dialog){notice("当前还有窗口正在处理，请先完成或关闭后再审核候选。",true);return false;}
      const error = ui.el("p", null, "ac-error"), body = ui.el("div", null, "ac-review-body");
      dialog.appBody.append(
        ui.el("p", `${session.kind === "whiteboard" ? "创作白板" : "原生方案"} · ${session.title || "排轴与计算"} · 候选修订 ${candidate.revision ?? "未知"}`),
        renderStageTracker(value), ui.el("p", isBuild(session) ? "按当前阶段审核并应用到未保存白板；同一聊天会保留上下文，可继续补齐后续阶段。" : "Agent 默认遵循已有输入，按你的明确要求可调整角色、配装、面板或动作。严格计算只有通过接纳门后才能应用。"));
      renderDiff(body, candidate, session); dialog.appBody.append(body, error);
      const apply = ui.button(value.diagnostic ? "诊断不可应用" : "应用审核过的候选", async () => {
        apply.disabled = true; dialog.setBusy?.(true);
        let result = false;
        try { result = await onApply(session, candidate, error); }
        finally { dialog.setBusy?.(false); }
        if (result) dialog.finish?.("applied"); else apply.disabled = Boolean(active()) || !canApply(session, candidate);
      }, "primary");
      apply.disabled = Boolean(active()) || !canApply(session, candidate);
      dialog.setActions?.(ui.button("返回聊天", () => dialog.finish?.("back")), apply);return dialog;
    }
    const review = () => { const session = selected(); return session ? reviewCandidate(session, () => reviewCore(session)) : Promise.resolve(false); };

    return Object.freeze({workflow, isBuild, isStaged, isAuthoring, workflowState, renderStageTracker, canApply,
      exploreCandidate, draftLabel, draftName, draftValue, renderDraftCell, renderDiff, review});
  }

  global.AgentWorkflowReviewView = Object.freeze({create, WORKFLOW_STAGES, STAGE_ALIASES, DRAFT_FIELDS,
    firstDefined, stageId, receiptFrom, topologyReceipt, executionBindingReceipt, acceptanceValue, acceptanceStatus,
    workflowState, stageStatusText, acceptanceText, energyText, topologyGateText, executionBindingFacts, bindingGateText,
    bindingIssueText, topologySummary, damageDisclosurePath, topologyValue});
})(window);
