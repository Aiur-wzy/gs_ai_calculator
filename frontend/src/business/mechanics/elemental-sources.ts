import type { MechanicsEditor, MechanicsEditorPorts } from "./contracts";
export function installMechanicsElementalSources(MC: MechanicsEditor, ports: MechanicsEditorPorts): void {
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
  MC.elementalCompilations = (report: any): any => report?.elemental_compilations || report?.plan?.resolution?.elemental_compilations || [];
  MC.elementalCompilation = (report: any, id: any): any => MC.elementalCompilations(report).find((r: any): any => r.profile_id === id);
  MC.renderElementalIssues = (box: any, issues: any, title: any): any => {
    if (!issues?.length) return;
    box.append(UI.el("p", `${title}（${issues.length} 项）`, "eq-error"));
    issues.slice(0, 8).forEach((issue: any): any => box.append(UI.el("p", issue.message || "仍有未解释的规则参数，请查看完整定位。")));
    box.append(UI.details("查看完整问题定位", issues));
  };
  MC.renderElementalCompilation = (box: any, record: any): any => {
    if (!record) {
      box.append(UI.el("p", "尚无当前草稿的数据库来源预览。"));
      return;
    }
    const p = record.projection,
      s = record.source,
      mode = extensions.AM?.mode ? extensions.AM.mode(record) : record.application_mode || "sequence";
    box.append(UI.el("p", record.assembly_issues?.length ? "规则组合后仍有冲突，尚不能用于计算。" : mode === "sequence" ? record.execution?.status === "ready" ? "规则参数处于已支持范围；确认来源不代表整份方案已经可计算。" : "当前规则参数待核实，不能作为已支持的附着规则。" : mode === "no_icd" ? "已声明每次命中均附着（无 ICD）；不使用计数标签或规则组。" : mode === "no_application" ? "已声明该攻击组件不施加元素；这与资料缺失不同。" : "附着模式待核实，确定性计算已阻止。"), UI.el("p", `元素附着模式：${extensions.AM?.modeText ? extensions.AM.modeText(mode) : mode}${record.deprecated_application_mode ? "；旧 unknown 已规范为 pending" : ""}。`));
    const canonicalRule = record.rule_group?.group || record.rule_group || record.attenuation_group;
    if (typeof canonicalRule === "string") box.append(UI.el("p", `规则条目：${canonicalRule}；${record.version_line === "beta" ? "测试资料线" : "发布资料线"}`));
    const identity = record.application_identity;
    if (identity) box.append(UI.el("p", `附着计数标签（ICD Tag）：${identity.tag || record.counter_tag || record.shared_group || (identity.status === "not_applicable" ? "不适用" : "待核实")}；身份依据：${identity.status === "database_confirmed" ? "数据库确认" : identity.status === "declared" ? "方案有证据声明" : identity.status === "not_applicable" ? "此模式不适用" : "待核实"}。`));
    if (identity?.source) box.append(UI.el("p", `计数标签来源：${extensions.AM?.sourceText ? extensions.AM.sourceText(identity.source) : typeof identity.source === "string" ? identity.source : "查看完整回执"}`));
    if (record.rule_group) box.append(UI.el("p", `附着规则组：${record.rule_group.group || record.attenuation_group || (record.rule_group.status === "not_applicable" ? "不适用" : "待核实")}；状态：${record.rule_group.status === "database_confirmed" ? "数据库确认" : record.rule_group.status === "not_applicable" ? "此模式不适用" : "待核实"}。规则组只提供重置时间和有限序列。`));
    if (p) {
      box.append(UI.el("p", `附着重置：${p.reset_cycle_seconds_rounded ?? "待核实"} 秒；原始值 ${p.reset_cycle_seconds_raw ?? "缺失"} 秒`), UI.el("p", `有限附着序列：${Array.isArray(p.durability_sequence) ? p.durability_sequence.join("、") : "尚不能解释"}（1 允许，0 阻止；耗尽后等待计时重置）`));
    }
    if (s) box.append(UI.el("p", `来源：${s.source_path}${s.json_pointer || ""}`));
    if (record.issue) box.append(UI.el("p", record.issue.message || "数据库来源不可用", "eq-error"));
    MC.renderElementalIssues(box, record.assembly_issues, "规则组合待修正");
    MC.renderElementalIssues(box, record.execution?.issues, "来源参数待核实");
    if (extensions.AM?.renderVerificationTask) {
      const tasks = record.verification_tasks || [record.verification_task].filter(Boolean);
      tasks.forEach((task: any): any => extensions.AM.renderVerificationTask(box, task, identity?.next_action));
    }
    box.append(UI.details("查看来源、快照与参数回执", record));
  };
  MC.renderElementalVerificationTasks = (box: any, report: any): any => {
    const rows = [...(report?.elemental_counter_verification_tasks || report?.plan?.resolution?.elemental_counter_verification_tasks || [])];
    for (const record of MC.elementalCompilations(report)) rows.push(...(record.verification_tasks || [record.verification_task].filter(Boolean)));
    if (!Array.isArray(rows) || !rows.length || !extensions.AM?.renderVerificationTask) return;
    const tasks: any = [],
      seen = new Set();
    for (const task of rows) {
      const key = JSON.stringify(task);
      if (!seen.has(key)) {
        seen.add(key);
        tasks.push(task);
      }
    }
    const card = UI.el("section", null, "eq-card");
    card.append(UI.el("h4", `元素附着身份与共享计数核实（${tasks.length}）`), UI.el("p", "这些工作单来自当前方案的真实组合检查。必须按角色、技能和具体命中分量核实；不能用普攻/战技/爆发类别、同角色其他技能或同名规则类推。", "eq-error"));
    tasks.forEach((task: any): any => extensions.AM.renderVerificationTask(card, task));
    box.append(card);
  };
  MC.editElementalSource = (index: any): any => {
    if (PL.state.draft) {
      setStatus("请先应用或放弃装备草稿");
      return;
    }
    const original = PL.state.plan,
      profiles = original?.mechanics_model?.elemental_model?.profiles || [];
    if (!original || original.plan_version !== 3 || !profiles.some((p: any): any => ["apply", "unknown"].includes(p.application))) {
      setStatus("请先在附着与元素底中建立附着档案");
      return;
    }
    const bindings = original.elemental_sources || [],
      otherIds = new Set(bindings.filter((_: any, i: any): any => i !== index).map((s: any): any => s.profile_id));
    const available = profiles.filter((p: any): any => ["apply", "unknown"].includes(p.application) && !otherIds.has(p.id));
    if (index === undefined && !available.length) {
      setStatus("现有附着档案均已绑定数据库来源，请编辑已有绑定");
      return;
    }
    const row = index === undefined ? {
      profile_id: available[0].id,
      version_line: "release",
      application_mode: "pending",
      source: "",
      expected_snapshot_id: null,
      expected_node_sha256: null
    } : UI.clone(bindings[index]);
    row.application_mode = extensions.AM?.mode ? extensions.AM.mode(row) : row.application_mode === "unknown" ? "pending" : row.application_mode || "sequence";
    let rememberedRule = row.rule_group || row.attenuation_group || "",
      rememberedTag = row.counter_tag || row.shared_group || "";
    const verification = UI.clone(row.verification || {
      locator: "",
      version: "",
      reviewed_at: "",
      component_scope: ""
    });
    let verificationStatus = row.verification?.status === "confirmed" ? "confirmed" : "pending";
    delete row.attenuation_group;
    delete row.shared_group;
    delete row.verification;
    if (row.application_mode === "sequence") {
      row.rule_group = rememberedRule;
      row.counter_tag = rememberedTag;
    } else {
      delete row.rule_group;
      delete row.counter_tag;
    }
    const baseline = PL.currentInputJson(),
      epoch = PL.state.sourceEpoch,
      path = RT.state.path,
      sha = PL.state.fileSha;
    const form = UI.el("div"),
      previewBox = UI.el("div"),
      errors = UI.el("div"),
      actions = UI.el("div", null, "eq-actions");
    let revision = 0,
      request = 0,
      busy = false,
      closed = false,
      preview: any = null;
    const invalidate = (): any => {
      if (closed) return;
      closed = true;
      request++;
    };
    const dialog = extensions.AppDialogKernel?.create?.({
      title: "数据库附着规则来源",
      className: "mc-dialog",
      description: "数据库规则组提供计时和有限附着序列。计数标签决定哪些攻击共享计数；缺少可靠标签时必须逐攻击段查证。",
      onRequestClose: (): any => {
        invalidate();
        return true;
      }
    }) || null;
    if (!dialog) {
      setStatus("当前还有窗口正在处理，请先完成或关闭后再编辑附着规则来源");
      return;
    }
    const current = (): any => !closed && PL.state.plan === original && PL.currentInputJson() === baseline && !PL.state.draft && PL.state.sourceEpoch === epoch && RT.state.path === path && PL.state.fileSha === sha;
    const valid = (): any => {
      if (current()) return true;
      setStatus("方案或数据库状态已变化，请重新打开来源编辑");
      return false;
    };
    const close = (): any => {
      invalidate();
      dialog.finish("elemental-source-close");
    };
    const confirmed = (): any => row.application_mode === "sequence" && !!row.expected_snapshot_id && !!row.expected_node_sha256;
    const same = (record: any, receipt: any): any => !!record?.required_confirmation && record.required_confirmation.expected_snapshot_id === receipt.expected_snapshot_id && record.required_confirmation.expected_node_sha256 === receipt.expected_node_sha256;
    const candidate = (remove: any): any => {
      const plan = UI.clone(original),
        rows = plan.elemental_sources || [];
      if (remove) rows.splice(index, 1);else if (index === undefined) rows.push(UI.clone(row));else rows[index] = UI.clone(row);
      plan.elemental_sources = rows;
      if (!remove) {
        const profile = plan.mechanics_model.elemental_model.profiles.find((p: any): any => p.id === row.profile_id);
        if (profile && row.application_mode === "no_application") {
          profile.application = "unknown";
          delete profile.gauge_units;
          delete profile.icd;
        } else if (profile?.application === "apply") profile.icd = {
          kind: "unknown"
        };
      }
      return plan;
    };
    const refresh = (): any => {
      dialog.setBusy(busy);
      previewBox.innerHTML = "";
      errors.innerHTML = "";
      previewBox.append(UI.el("p", busy ? "正在核对当前声明…" : row.application_mode === "sequence" ? confirmed() ? "草稿已记录来源确认；应用时会再次检查当前数据库。" : "尚未确认数据库来源；可以先应用为待核实草稿。" : row.application_mode === "pending" ? "附着模式仍待核实；应用后会阻止确定性计算。" : "当前模式由逐攻击组件证据声明，不需要附着规则数据库指纹。"));
      if (preview?.revision === revision) {
        MC.renderElementalCompilation(previewBox, preview.record);
        MC.renderElementalIssues(previewBox, preview.issues, "方案仍需完善");
      } else previewBox.append(UI.el("p", row.application_mode === "sequence" ? "请读取当前数据库；不会沿用此前装备预览中的确认信息。" : "请预览当前附着模式声明；不会沿用旧计算结果。"));
      actions.innerHTML = "";
      const add = (label: any, handler: any, disabled: any = false): any => {
        const b = UI.button(label, handler);
        b.disabled = busy || disabled;
        actions.append(b);
      };
      add(row.application_mode === "sequence" ? "读取并预览当前数据库规则" : "预览当前附着模式", (): any => run("preview"));
      if (row.application_mode === "sequence") add("确认所示来源用于此档案", (): any => run("confirm"), !preview?.record?.required_confirmation || preview.revision !== revision || preview.record.execution?.status !== "ready");
      add("应用来源绑定", (): any => run("apply"));
      if (index !== undefined) add("移除此来源绑定", (): any => run("remove"));
      actions.append(UI.button("放弃并关闭", close));
    };
    const update = (key: any, value: any): any => {
      if (row[key] === value) return;
      row[key] = value;
      row.expected_snapshot_id = null;
      row.expected_node_sha256 = null;
      revision++;
      request++;
      busy = false;
      preview = null;
      refresh();
    };
    const updateMode = (value: any): any => {
      if (row.application_mode === value) return;
      row.application_mode = value;
      if (value === "sequence") {
        row.rule_group = rememberedRule;
        row.counter_tag = rememberedTag;
      } else {
        delete row.rule_group;
        delete row.counter_tag;
      }
      row.expected_snapshot_id = null;
      row.expected_node_sha256 = null;
      revision++;
      request++;
      busy = false;
      preview = null;
      drawForm();
      refresh();
    };
    const run = async (mode: any): Promise<any> => {
      if (busy || !valid()) return;
      const selectedProfile = profiles.find((p: any): any => p.id === row.profile_id);
      if (!row.source.trim()) {
        errors.append(UI.el("p", "请填写这一具体攻击组件的附着模式依据。", "eq-error"));
        return;
      }
      if (["sequence", "no_icd"].includes(row.application_mode) && selectedProfile?.application !== "apply") {
        errors.append(UI.el("p", "有限序列或无 ICD 需要已有明确元素与原始 GU 的 apply 档案。", "eq-error"));
        return;
      }
      if (row.application_mode === "sequence" && (!(row.rule_group || "").trim() || !(row.counter_tag || "").trim())) {
        errors.append(UI.el("p", "有限序列模式必须填写附着规则组与附着计数标签（ICD Tag）。", "eq-error"));
        return;
      }
      delete row.verification;
      const needsEvidence = row.application_mode !== "pending",
        complete = ["locator", "version", "reviewed_at", "component_scope"].every((key: any): any => (verification[key] || "").trim());
      if (needsEvidence && verificationStatus !== "confirmed") {
        errors.append(UI.el("p", "有限序列、无 ICD 或无附着结论都需要逐攻击段结构化证据；尚未核实时请选择待核实模式。", "eq-error"));
        return;
      }
      if (needsEvidence && !complete) {
        errors.append(UI.el("p", "请填写证据类型/定位、适用版本或commit、复核日期与具体攻击组件范围。", "eq-error"));
        return;
      }
      if (needsEvidence) row.verification = {
        status: "confirmed",
        ...UI.clone(verification),
        confirmed_fields: row.application_mode === "sequence" ? ["application_mode", "counter_tag", "shared_scope"] : ["application_mode", "shared_scope"]
      };
      const removal = mode === "remove",
        shown = preview?.revision === revision ? preview.record : null;
      if (mode === "confirm" && (!shown?.required_confirmation || shown.execution?.status !== "ready")) {
        setStatus("请先读取并检查当前草稿的规则来源");
        return;
      }
      const proposed = candidate(removal),
        receipt = mode === "confirm" ? UI.clone(shown.required_confirmation) : confirmed() ? {
          expected_snapshot_id: row.expected_snapshot_id,
          expected_node_sha256: row.expected_node_sha256
        } : null;
      if (mode === "confirm") Object.assign(proposed.elemental_sources.find((s: any): any => s.profile_id === row.profile_id), receipt);
      const token = ++request,
        version = revision;
      busy = true;
      refresh();
      let response;
      try {
        response = await callApi("compose_equipment_plan", proposed);
      } catch (error) {
        response = {
          code: 1,
          message: String(error)
        };
      }
      if (token !== request || version !== revision || !current()) {
        if (!closed && !current()) valid();
        return;
      }
      busy = false;
      const report = response.data,
        record = MC.elementalCompilation(report, row.profile_id);
      if (response.code || !report || !["ready", "pending"].includes(report.state) || !removal && !record) {
        refresh();
        errors.append(UI.error(response.code ? response : {
          code: 1,
          message: "未获得可校验的来源回执，请重新读取"
        }));
        return;
      }
      if (!removal && row.application_mode === "sequence" && record.rule_group?.group !== row.rule_group) {
        refresh();
        errors.append(UI.el("p", `数据库回执规则组（${record.rule_group?.group || "缺失"}）与草稿（${row.rule_group}）不一致，未应用。`, "eq-error"));
        return;
      }
      preview = {
        revision,
        record,
        issues: report.missing_inputs || []
      };
      if (!removal && receipt && !same(record, receipt)) {
        row.expected_snapshot_id = null;
        row.expected_node_sha256 = null;
        revision++;
        preview.revision = revision;
        refresh();
        errors.append(UI.el("p", "数据库来源已变化，尚未确认或应用。请检查新显示的来源，再明确确认。", "eq-error"));
        return;
      }
      if (mode === "confirm") {
        if (record.status !== "ready") {
          refresh();
          errors.append(UI.el("p", "当前规则仍待解释，来源确认未写入草稿。", "eq-error"));
          return;
        }
        Object.assign(row, receipt);
        revision++;
        preview.revision = revision;
        refresh();
        setStatus("当前来源已核对，确认记录仅保存在本次编辑草稿中");
        return;
      }
      if (mode === "preview") {
        refresh();
        return;
      }
      original.elemental_sources = UI.clone(proposed.elemental_sources);
      original.mechanics_model.elemental_model.profiles = UI.clone(proposed.mechanics_model.elemental_model.profiles);
      close();
      MC.touch();
      MC.elementalDiagnostics = {
        plan: original,
        input: PL.currentInputJson(),
        epoch: PL.state.sourceEpoch,
        path: RT.state.path,
        sha: PL.state.fileSha,
        ...UI.clone({
          record,
          issues: report.missing_inputs || [],
          state: report.state
        })
      };
      MC.render();
      setStatus(removal ? "已移除来源绑定；附着冷却保留待核实，请明确填写规则" : report.state === "pending" ? "已应用待完善的来源绑定；请查看机制页中的校验问题，尚未写入文件" : "来源绑定已应用到当前方案；尚未写入文件，请重新计算或保存");
    };
    const drawForm = (): any => {
      form.innerHTML = "";
      form.append(UI.field("附着档案", UI.select(available.map((p: any): any => ({
        value: p.id,
        label: `${MC.label(p.element)} · ${p.id}`
      })), row.profile_id, (v: any): any => update("profile_id", v))), UI.field("元素附着模式", UI.select(extensions.AM?.applicationModes || [{
        value: "pending",
        label: "待核实（阻止确定性计算）"
      }, {
        value: "sequence",
        label: "有限序列（计数标签 + 规则组）"
      }, {
        value: "no_icd",
        label: "每次均附着（无 ICD）"
      }, {
        value: "no_application",
        label: "明确不施加元素"
      }], row.application_mode, updateMode)), UI.field("数据库资料线", UI.select([{
        value: "release",
        label: "发布资料线"
      }, {
        value: "beta",
        label: "测试资料线"
      }], row.version_line, (v: any): any => update("version_line", v))));
      if (row.application_mode === "sequence") form.append(UI.field("附着规则组（区分大小写）", UI.input(row.rule_group || "", (v: any): any => {
        rememberedRule = v;
        update("rule_group", v);
      })), UI.field("附着计数标签（ICD Tag）", UI.input(row.counter_tag || "", (v: any): any => {
        rememberedTag = v;
        update("counter_tag", v);
      })), UI.el("p", "附着规则组提供重置时间和有限序列；计数标签决定具体攻击间的共享计数。两者不能互换。"));else form.append(UI.el("p", row.application_mode === "no_icd" ? "无 ICD：每次命中均允许附着，不填写计数标签或规则组。" : row.application_mode === "no_application" ? "无附着：该攻击组件不尝试施加元素，与缺资料不同。" : "待核实：Agent须查询具体攻击段，确定性计算会被阻止。", row.application_mode === "pending" ? "eq-error" : null));
      form.append(UI.field("附着模式与计数关系依据（来源、版本）", UI.area(row.source, (v: any): any => update("source", v))), UI.field("逐段结构化证据状态", UI.select([{
        value: "pending",
        label: "待核实（不生成伪证据）"
      }, {
        value: "confirmed",
        label: "已逐段核实"
      }], verificationStatus, (v: any): any => {
        verificationStatus = v;
        revision++;
        request++;
        busy = false;
        preview = null;
        drawForm();
        refresh();
      })), UI.field("证据类型与定位（URL、本地路径或原始 pointer）", UI.input(verification.locator, (v: any): any => {
        verification.locator = v;
        revision++;
        request++;
        busy = false;
        preview = null;
        refresh();
      })), UI.field("适用游戏版本或固定 commit", UI.input(verification.version, (v: any): any => {
        verification.version = v;
        revision++;
        request++;
        busy = false;
        preview = null;
        refresh();
      })), UI.field("检索/复核日期（YYYY-MM-DD）", UI.input(verification.reviewed_at, (v: any): any => {
        verification.reviewed_at = v;
        revision++;
        request++;
        busy = false;
        preview = null;
        refresh();
      })), UI.field("具体攻击 component_scope 与共享范围", UI.area(verification.component_scope, (v: any): any => {
        verification.component_scope = v;
        revision++;
        request++;
        busy = false;
        preview = null;
        refresh();
      })), UI.el("p", `confirmed_fields 由模式生成：${row.application_mode === "sequence" ? "application_mode、counter_tag、shared_scope" : row.application_mode === "pending" ? "待核实模式不生成证据" : "application_mode、shared_scope"}。`));
    };
    dialog.appBody.append(UI.el("p", "数据库规则组提供计时和有限附着序列。计数标签决定哪些攻击共享计数；缺少可靠 attenuationTag 时，Agent 必须逐攻击段查证，不能按普攻/战技/爆发类别猜测。"), UI.el("p", "同技能不同段、召唤物和命座追加攻击可能使用不同计数标签；同名规则组也不表示它们共享计数。"), UI.el("p", "应用绑定后，来源拥有该档案的附着模式。移除绑定后保留待核实占位，不会从旧计算结果恢复规则。"), form, previewBox, errors, actions);
    dialog.appFooter.append(UI.button("← 返回机制列表", close));
    dialog.appFooter.hidden = false;
    drawForm();
    refresh();
  };
  MC.renderElementalSources = (box: any): any => {
    const sources = PL.state.plan?.elemental_sources || [],
      card = UI.el("section", null, "eq-card");
    card.append(UI.el("h4", `数据库附着规则来源（${sources.length}）`), UI.el("p", "读取规则组参数并核对来源；计数标签（ICD Tag）须来自具体攻击组件的 attenuationTag 或有证据的方案声明。"), UI.button("添加数据库附着规则来源", (): any => MC.editElementalSource()));
    sources.forEach((source: any, index: any): any => card.append(UI.el("p", `${source.profile_id} · ${extensions.AM?.modeText ? extensions.AM.modeText(source) : source.application_mode || "有限序列"} · ${source.rule_group || source.attenuation_group || "无规则组"} · ${source.expected_snapshot_id && source.expected_node_sha256 ? "已有来源确认，需以本次计算复核为准" : source.verification?.status === "confirmed" ? "结构化逐段证据" : "待核实来源"}`), UI.button(`编辑来源 ${index + 1}`, (): any => MC.editElementalSource(index))));
    const check = MC.elementalDiagnostics;
    if (check && check.plan === PL.state.plan && check.input === PL.currentInputJson() && check.epoch === PL.state.sourceEpoch && check.path === RT.state.path && check.sha === PL.state.fileSha) {
      card.append(UI.el("p", `最近一次来源编辑校验：${check.state === "ready" ? "已通过所声明输入的组合检查" : "方案待完善"}；后续计算仍会重新核对来源。`));
      MC.renderElementalIssues(card, check.issues, "方案待完善事项");
      if (check.record) MC.renderElementalCompilation(card, check.record);
    }
    box.append(card);
  };
}
