"use strict";
// External values are native declarations, never generated hits or mechanics.
window.XD = {};
XD.commandMenu = () => window.AppContextMenu?.forDocument?.(document, window) || null;
XD.menuError = error => setStatus(`操作未完成：${String(error?.message || error || "未知错误")}`);
XD.openCommandMenu = (target, label, items) => {
  const menu = XD.commandMenu();
  if (menu) return menu.open({target, trigger:target, returnFocus:target, label, items, onError:XD.menuError});
  const first = items.find(item => item?.type !== "separator" && item.danger !== true && item.enabled !== false && typeof item.run === "function");
  if (first) return first.run(); setStatus("操作菜单暂不可用，请重新加载界面"); return false;
};
XD.bindCommandMenu = (target, label, items) => XD.commandMenu()?.bind(target, {label, items, onError:XD.menuError});
XD.attachCommandMenu = (target, label, items) => {
  const more = UI.button("⋯", () => XD.openCommandMenu(more, label, items()), "rc-row-menu");
  more.setAttribute("aria-label", `${label}更多操作`); more.setAttribute("aria-haspopup", "menu");
  target.tabIndex = 0; target.append(more); XD.bindCommandMenu(target, label, items); return target;
};
XD.withItem = (plan, reference, run) => {
  if (PL.state.plan !== plan || PL.state.draft) { setStatus("外部伤害方案已变化，请重新打开操作菜单"); return false; }
  const rows = XD.items(PL.state.plan), matches = rows.map((row, index) => ({row, index})).filter(item =>
    reference.id ? item.row?.id === reference.id : item.row === reference.row);
  if (matches.length !== 1) { setStatus("外部伤害补充已变化或移除，请重新打开操作菜单"); return false; }
  return run(matches[0]);
};
XD.itemMenuItems = (plan, row) => {
  const reference = {id:row?.id || null, row};
  return [{id:"edit", label:"查看或编辑此外部补充", run:() => XD.withItem(plan, reference, located => XD.edit(located.index))}];
};
XD.caps = () => window.PL?.state.caps?.external_damage_contract;
XD.items = plan => plan?.external_damage_supplements || [];
XD.hash = row => row?.required_confirmation?.accepted_fingerprint;
XD.confirmable = row => ["ready", "pending"].includes(row?.status)
  && /^[a-f0-9]{64}$/.test(XD.hash(row) || "") && row.accepted_fingerprint === XD.hash(row)
  && Array.isArray(row.issues) && row.issues.every(issue =>
    XD.caps()?.confirmable_issue_codes?.includes(issue.code));
XD.description = "外部计算值只补充引擎尚未覆盖的新增伤害，不是引擎机制；不会产生增益、附着、扣能、次数或触发。已有命中应修正原模型，不能在这里重复计入。";
XD.preview = (box, row, item) => {
  box.append(UI.el("h4", "本次外部伤害复核"), UI.el("p", XD.description));
  if (!row) { box.append(UI.el("p", "尚未读取当前方案与来源；填写后先预览，再核对计算依据与无重复说明。")); return; }
  box.append(UI.el("p", `${item.label || "未命名补充"} · ${item.t ?? "待填"} 秒 · 外部期望伤害 ${item.expected ?? "待填"} · ${item.precision ? "近似值" : "精确值"}`),
    UI.el("p", row.status === "ready" ? "当前确认与预览一致；完整执行仍须计算核对。" : "本项待复核或确认；其他方案待办仍保留。"));
  for (const issue of row.issues || []) box.append(UI.el("p", issue.message || issue.code, "eq-error"));
  box.append(UI.details("复核身份与定位", row));
};
XD.edit = index => {
  if (PL.state.draft) { setStatus("请先应用或放弃装备草稿"); return; }
  const original = PL.state.plan, caps = XD.caps(), previous = XD.items(original)[index];
  if (!original || !caps?.default_item) { setStatus("外部伤害补充需要原生方案与当前接口"); return; }
  if (index !== undefined && !previous) return;
  if (index === undefined && XD.items(original).length >= (caps.limits?.max_items ?? Infinity)) {
    setStatus("外部补充项已达到接口数量上限，请编辑或移除已有项目"); return;
  }
  const row = UI.clone(previous || caps.default_item);
  if (!previous) row.id = UI.id("external-damage");
  const baseline = PL.currentInputJson(), epoch = PL.state.sourceEpoch, path = RT.state.path,
    sha = PL.state.fileSha, capsJson = JSON.stringify(PL.state.caps);
  let closed = false, revision = 0, request = 0, shown = null;
  const current = () => !closed && PL.state.plan === original && !PL.state.draft
    && PL.currentInputJson() === baseline && PL.state.sourceEpoch === epoch && RT.state.path === path
    && PL.state.fileSha === sha && JSON.stringify(PL.state.caps) === capsJson;
  const form = UI.el("div"), preview = UI.el("div"), errors = UI.el("div"), actions = UI.el("div");
  const invalidate = () => { if (closed) return true; closed = true; request++; return true; };
  const dialog = window.AppDialogKernel?.create?.({title:"外部计算伤害补充", description:XD.description,
    onRequestClose:invalidate}) || null;
  if (!dialog) {
    const reason = window.AppDialogKernel?.getLastCreateResult?.()?.reason;
    setStatus(reason === "active_busy" ? "当前窗口正在处理请求，请等待完成或主动关闭后再编辑外部伤害补充"
      : "当前还有窗口正在处理，请先完成或关闭后再编辑外部伤害补充");
    return;
  }
  const close = () => { invalidate(); return dialog.finish?.("cancel") ?? true; };
  const update = (key, value) => {
    if (JSON.stringify(row[key]) === JSON.stringify(value)) return;
    row[key] = value; row.accepted_fingerprint = null; revision++; request++; shown = null;
    errors.innerHTML = ""; preview.innerHTML = ""; XD.preview(preview, null, row);
  };
  const precisionValue = (field, value) => ({
    kind:"approximate",
    method:field === "method" ? value : (row.precision?.method || ""),
    uncertainty:field === "uncertainty" ? value : (row.precision?.uncertainty || ""),
    state_effects:"none",
  });
  const candidate = remove => {
    const plan = UI.clone(original), items = UI.clone(XD.items(original));
    // Keep positions and all unknown fields. The schema, not this editor, decides validity.
    if (previous) { if (remove) items.splice(index, 1); else items[index] = UI.clone(row); }
    else if (!remove) items.push(UI.clone(row));
    plan.external_damage_supplements = items; return plan;
  };
  const run = async mode => {
    if (!current()) { setStatus("方案、文件、草稿、接口或来源已变化，请重新打开外部伤害编辑"); return; }
    errors.innerHTML = "";
    const remove = mode === "remove", prior = shown?.revision === revision ? shown.row : null;
    if (mode === "confirm" && !XD.confirmable(prior)) {
      errors.append(UI.el("p", "请先预览当前内容并处理无法确认的项目；未自动批准外部值。", "eq-error")); return;
    }
    const plan = candidate(remove), accepted = mode === "confirm" ? XD.hash(prior) : row.accepted_fingerprint;
    if (mode === "confirm") plan.external_damage_supplements[previous ? index : plan.external_damage_supplements.length - 1].accepted_fingerprint = accepted;
    const attempt = ++request, version = revision; let response;
    try { response = await callApi("compose_equipment_plan", UI.clone(plan)); }
    catch (error) { response = {code:1, message:String(error)}; }
    if (attempt !== request || version !== revision || !current()) {
      if (!closed) setStatus("旧外部伤害响应已忽略，请重新预览"); return;
    }
    const report = response?.data, review = report?.external_damage_review, records = review?.items;
    const expectedIds = plan.external_damage_supplements.map(item => item.id);
    const empty = expectedIds.length === 0 && review == null;
    const valid = report && ["ready", "pending"].includes(report.state) && (empty || (review?.contract === "external-damage-v1"
      && ["ready", "pending"].includes(review.status) && Array.isArray(review.issues) && Array.isArray(records)
      && records.length === expectedIds.length && new Set(records.map(item => item.id)).size === records.length
      && records.every(item => expectedIds.includes(item.id) && ["ready", "pending"].includes(item.status) && Array.isArray(item.issues))));
    if (response?.code !== 0 || !valid) {
      errors.append(UI.error(response?.code ? response : {message:"接口未返回完整且对应的外部伤害复核清单，未应用修改"})); return;
    }
    const record = records?.find(item => item.id === row.id);
    preview.innerHTML = ""; if (!remove) XD.preview(preview, record, row);
    shown = {revision, row:record};
    if (!remove && accepted && (XD.hash(record) !== accepted || !XD.confirmable(record))) {
      row.accepted_fingerprint = null; revision++; shown.revision = revision;
      errors.append(UI.el("p", "当前输入、来源或补充值的复核已失效；请核对新预览后重新确认，或再次应用未确认草稿。", "eq-error")); return;
    }
    if (mode === "preview") return;
    if (mode === "confirm") {
      row.accepted_fingerprint = accepted; revision++; shown.revision = revision;
      errors.append(UI.el("p", "已记录本次复核；尚未应用或写入文件。")); return;
    }
    // Never trust report.plan or copy calculated totals, events or rules into native input.
    original.external_damage_supplements = UI.clone(plan.external_damage_supplements);
    original.resolution = {state:"pending"}; original.resolved_template = null;
    close(); RC.touch(); RS.invalidate();
    setStatus(remove ? "外部补充已移除，请重新计算并保存" : "外部补充草稿已应用；请重新计算，随后保存方案");
  };
  const options = (items, value, label) => {
    const result = [{value:"", label:"尚未选择"}, ...items.map(item => ({value:item.id, label:label(item)}))];
    if (value && !items.some(item => item.id === value)) result.push({value, label:`失效引用：${value}`});
    return result;
  };
  form.append(UI.field("补充显示名", UI.input(row.label, value => update("label", value))),
    UI.field("伤害归属角色", UI.select(options(original.manual_template.characters, row.character,
      item => DS.character(item.id, original.manual_template, original)), row.character, value => update("character", value))),
    UI.field("技能或伤害类别名称", UI.input(row.skill, value => update("skill", value))),
    UI.field("发生时点（秒）", UI.input(row.t, value => update("t", value), true)),
    UI.field("外部期望伤害（非负）", UI.input(row.expected, value => update("expected", value), true)),
    UI.field("数值精度", UI.select([
      {value:"exact", label:"精确值"},
      {value:"approximate", label:"近似值（会标记整个结果）"},
    ], row.precision ? "approximate" : "exact", value => update("precision", value === "approximate"
      ? {kind:"approximate", method:"", uncertainty:"", state_effects:"none"} : null))),
    UI.field("近似方法（选择近似值时必填）", UI.area(row.precision?.method || "",
      value => update("precision", precisionValue("method", value)))),
    UI.field("不确定性说明（选择近似值时必填）", UI.area(row.precision?.uncertainty || "",
      value => update("precision", precisionValue("uncertainty", value)))),
    UI.field("关联实际动作（可不关联）", UI.select(options(original.rotation_model?.actions || [], row.action_id,
      item => `${item.start} 秒 · ${window.AE?.actionName(item.id, original) || item.id}`), row.action_id || "", value => update("action_id", value || null))),
    UI.field("覆盖范围唯一键", UI.input(row.coverage_key, value => update("coverage_key", value))),
    UI.field("补充伤害事件唯一标识", UI.input(row.event_id, value => update("event_id", value))),
    UI.field("为何需要外部补充", UI.area(row.reason, value => update("reason", value))),
    UI.field("计算过程与假设", UI.area(row.calculation_note, value => update("calculation_note", value))),
    UI.field("与引擎及其他补充不重复的依据", UI.area(row.non_overlap_note, value => update("non_overlap_note", value))),
    UI.field("来源引用（每行一项）", UI.area(Array.isArray(row.source_refs) ? row.source_refs.join("\n") : "",
      value => update("source_refs", value.split(/\r?\n/).map(line => line.trim()).filter(Boolean)))));
  actions.append(UI.button("预览外部伤害复核", () => run("preview")), UI.button("确认本次外部计算", () => run("confirm")),
    UI.button("应用外部补充草稿", () => run("apply")));
  if (previous) actions.append(UI.button("移除此外部补充", () => run("remove")));
  actions.append(UI.button("放弃并关闭", close));
  dialog.appBody.append(UI.el("p", "面板、装备、轴、敌人、规则或来源改变后需重新复核；外部值不会随 Excel 配装变化自动重算。"),
    UI.details("原生记录（未知字段保留，校验由接口执行）", row), form, preview, errors, actions);
  XD.preview(preview, null, row);
  dialog.focusInitial?.();
};
XD.render = box => {
  if (!PL.state.plan || !XD.caps()) return;
  const plan = PL.state.plan, card = UI.el("section", null, "eq-card");
  card.append(UI.el("h4", "外部计算伤害补充"), UI.el("p", XD.description), UI.button("添加外部伤害补充", () => XD.edit()));
  XD.items(plan).forEach(row => {
    const item = UI.el("article", null, "eq-card");
    item.append(UI.el("p", `${row.label || "未命名补充"} · ${row.t ?? "待填"} 秒 · ${row.expected ?? "待填"} · ${row.precision ? "近似" : "精确"} · ${row.accepted_fingerprint ? "已记录确认，仍需当前来源复核" : "未确认草稿"}`));
    XD.attachCommandMenu(item, "外部伤害补充操作", () => XD.itemMenuItems(plan, row)); card.append(item);
  });
  box.append(card);
};
XD.renderResult = (box, result) => {
  const rows = result?.external_damage?.items;
  if (!Array.isArray(rows) || !rows.length) return;
  const section = UI.el("section", null, "result-block");
  section.append(UI.el("h3", `外部计算伤害明细（${rows.length}）`), UI.el("p", XD.description));
  const table = UI.el("table", null, "grid"), header = table.insertRow();
  for (const text of ["时刻（秒）", "角色", "技能", "补充名称", "外部期望伤害", "精度", "依据"]) header.insertCell().textContent = text;
  for (const row of rows) {
    const tr = table.insertRow();
    for (const value of [row.t, DS.character(row.character, RS.resultTemplate(), RS.state.snapshot?.template),
      DS.skill(row.skill), row.label, RS.fmtInt(row.expected), row.precision ? "近似" : "精确"]) tr.insertCell().textContent = value;
    const detail = UI.el("details"); detail.append(UI.el("summary", "计算与无重复说明"), UI.el("p", row.reason),
      UI.el("p", row.calculation_note), UI.el("p", row.non_overlap_note), UI.el("p", (row.source_refs || []).join("\n")), UI.details("补充原文", row));
    tr.insertCell().append(detail);
  }
  section.append(table); box.append(section);
};
