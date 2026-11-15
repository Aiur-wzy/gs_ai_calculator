import type { MechanicsEditor, MechanicsEditorPorts } from "./contracts";
export function installMechanicsHitParameterSources(MC: MechanicsEditor, ports: MechanicsEditorPorts): void {
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
  MC.hitParameterCompilation = (report: any, id: any): any => (report?.hit_parameter_compilations || report?.plan?.resolution?.hit_parameter_compilations || []).find((r: any): any => r.hit_id === id);
  MC.renderHitParameterCompilation = (box: any, record: any): any => {
    if (!record) {
      box.append(UI.el("p", "尚无当前草稿的技能参数预览。"));
      return;
    }
    const scalar = record.scalar_projection,
      descriptions = record.parameter_descriptions;
    box.append(UI.el("p", `数据库原始数值：${scalar?.value ?? "缺失"}；有效天赋等级：${record.level?.effective_level ?? "待核实"}`), UI.el("p", `倍率属性：${MC.label(record.scaling_stat)}；用途状态：${MC.label(record.interpretation_status || "pending")}`), UI.el("p", "来源确认只核对当前数据库。用途由方案声明，引擎尚未验证游戏语义；治疗、冷却和加算贡献不能当作直接伤害倍率。"));
    if (record.parameter_contract === "level-bound-parameter-v1") {
      box.append(UI.el("p", `基础等级：${record.level?.base_level ?? "待核实"}；已核实的命座加级：${record.level?.extra_level ?? "待核实"}。其它命座修改尚未执行。`));
      MC.renderElementalIssues(box, record.level_profile?.issues, "有效等级依据待核实");
    }
    if (descriptions?.matched?.length) {
      box.append(UI.el("h4", "对应参数的完整中文原文"));
      descriptions.matched.forEach((row: any): any => box.append(UI.el("p", row.text)));
    } else box.append(UI.el("p", "没有找到引用此参数的中文原文；请补充用途依据，保持待核实直到完成检查。", "eq-error"));
    if (descriptions?.unresolved?.length) box.append(UI.el("p", `有 ${descriptions.unresolved.length} 条描述缺文或无法解析；现有原文不能代表技能资料完整。`, "eq-error"));
    if (record.source) box.append(UI.el("p", `数值来源：${record.source.source_path}${record.source.parameter_pointer || record.source.json_pointer || ""}`));
    if (record.actor_identity === "explicitly_declared") box.append(UI.el("p", "角色身份仅由本方案声明，尚无装备角色关联用于交叉核对。"));
    if (record.issue) box.append(UI.el("p", record.issue.message || "技能参数来源不可用", "eq-error"));
    MC.renderElementalIssues(box, scalar?.issues, "参数尚不能自动采用");
    box.append(UI.details("查看完整参数、描述与来源证据", record));
  };
  MC.editHitParameterSource = (index: any): any => {
    if (PL.state.draft) {
      setStatus("请先应用或放弃装备草稿");
      return;
    }
    const original = PL.state.plan,
      hits = original?.manual_template?.hits || [],
      bindings = original?.hit_parameter_sources || [];
    if (!original || original.plan_version !== 3) {
      setStatus("数据库技能倍率来源需要原生第三版方案");
      return;
    }
    const others = new Set(bindings.filter((_: any, i: any): any => i !== index).map((s: any): any => s.hit_id)),
      available = hits.filter((h: any): any => h.damage_system === "classic" && !others.has(h.id));
    if (!available.length) {
      setStatus("请先建立未绑定来源的常规伤害命中或原型");
      return;
    }
    const initial = hits.find((h: any): any => h.id === bindings[index]?.hit_id) || available[0],
      ref = original.equipment_inputs?.[initial.character]?.character_ref;
    const supportsLevels = PL.state.caps?.hit_parameter_source_contract?.level_contract === "level-bound-parameter-v1";
    const row = index === undefined ? {
      hit_id: initial.id,
      version_line: ref?.version_line || "release",
      selection: {
        character_id: ref?.db_character_id ?? null,
        slot: "skill",
        constellation: 0,
        ...(supportsLevels ? {
          base_level: 10
        } : {
          talent_level: 10
        })
      },
      ...(supportsLevels ? {
        parameter_contract: "level-bound-parameter-v1",
        expected_level_fingerprint: null
      } : {}),
      param_index: 0,
      scaling_stat: initial.scaling_stat || "",
      purpose: "single_stat_damage_multiplier",
      status: "pending",
      source: "",
      expected_snapshot_id: null,
      expected_node_sha256: null
    } : UI.clone(bindings[index]);
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
      preview: any = null,
      manualMultiplier: any = null;
    const invalidate = (): any => {
      if (closed) return;
      closed = true;
      request++;
    };
    const dialog = extensions.AppDialogKernel?.create?.({
      title: "数据库技能倍率来源",
      className: "mc-dialog",
      description: "只给已有命中或原型取数，不生成攻击；用途、倍率属性与等级关系仍需单独核实。",
      onRequestClose: (): any => {
        invalidate();
        return true;
      }
    }) || null;
    if (!dialog) {
      setStatus("当前还有窗口正在处理，请先完成或关闭后再编辑技能倍率来源");
      return;
    }
    const current = (): any => !closed && PL.state.plan === original && PL.currentInputJson() === baseline && !PL.state.draft && PL.state.sourceEpoch === epoch && RT.state.path === path && PL.state.fileSha === sha;
    const valid = (): any => {
      if (current()) return true;
      setStatus("方案或数据库状态已变化，请重新打开倍率来源编辑");
      return false;
    };
    const close = (): any => {
      invalidate();
      dialog.finish("hit-parameter-source-close");
    };
    const levelBound = (): any => row.parameter_contract === "level-bound-parameter-v1";
    const confirmed = (): any => !!row.expected_snapshot_id && !!row.expected_node_sha256 && (!levelBound() || !!row.expected_level_fingerprint);
    const same = (record: any, receipt: any): any => !!record?.required_confirmation && record.required_confirmation.expected_snapshot_id === receipt.expected_snapshot_id && record.required_confirmation.expected_node_sha256 === receipt.expected_node_sha256 && (!levelBound() || !!receipt.expected_level_fingerprint && record.required_confirmation.expected_level_fingerprint === receipt.expected_level_fingerprint);
    const supported = (record: any): any => record?.scalar_projection?.status === "ready" && Number.isFinite(record.scalar_projection.value) && record.scalar_projection.value >= 0 && (!levelBound() || record.parameter_contract === "level-bound-parameter-v1" && record.level_profile?.status === "ready" && !!record.required_confirmation?.expected_level_fingerprint);
    const candidate = (remove: any): any => {
      const plan = UI.clone(original),
        sources = plan.hit_parameter_sources || [],
        hit = plan.manual_template.hits.find((h: any): any => h.id === row.hit_id);
      if (remove) {
        sources.splice(index, 1);
        hit.multiplier = manualMultiplier;
      } else {
        if (index === undefined) sources.push(UI.clone(row));else sources[index] = UI.clone(row);
        delete hit.multiplier;
        if (!Object.hasOwn(hit, "scaling_stat")) hit.scaling_stat = row.scaling_stat;
      }
      plan.hit_parameter_sources = sources;
      return plan;
    };
    const refresh = (): any => {
      dialog.setBusy(busy);
      previewBox.innerHTML = "";
      errors.innerHTML = "";
      previewBox.append(UI.el("p", busy ? "正在读取当前技能参数…" : confirmed() ? "草稿已记录数值来源确认；用途状态仍需单独核实。" : "数值来源尚未确认；可以先应用为待核实草稿。"));
      if (preview?.revision === revision) {
        MC.renderHitParameterCompilation(previewBox, preview.record);
        MC.renderElementalIssues(previewBox, preview.issues, "方案待完善事项");
      } else previewBox.append(UI.el("p", "请读取当前数据库，检查完整中文原文及用途；不会采用旧报告中的数值或确认信息。"));
      actions.innerHTML = "";
      const add = (label: any, fn: any, disabled: any = false): any => {
        const b = UI.button(label, fn);
        b.disabled = busy || disabled;
        actions.append(b);
      };
      add("读取并预览当前技能参数", (): any => run("preview"));
      add("确认所示数值来源", (): any => run("confirm"), !preview?.record?.required_confirmation || preview.revision !== revision || !supported(preview.record));
      add("应用倍率来源绑定", (): any => run("apply"));
      if (index !== undefined) add("解除来源并采用手填倍率", (): any => run("remove"), manualMultiplier === null || manualMultiplier < 0);
      actions.append(UI.button("放弃并关闭", close));
    };
    const changed = (): any => {
      row.expected_snapshot_id = null;
      row.expected_node_sha256 = null;
      if (levelBound()) row.expected_level_fingerprint = null;
      revision++;
      request++;
      busy = false;
      preview = null;
      refresh();
    };
    const update = (key: any, value: any): any => {
      if (row[key] === value) return;
      row[key] = value;
      changed();
    };
    const updateSelection = (key: any, value: any): any => {
      if (value === null) delete row.selection[key];else row.selection[key] = value;
      changed();
    };
    const run = async (mode: any): Promise<any> => {
      if (busy || !valid()) return;
      const removal = mode === "remove",
        shown = preview?.revision === revision ? preview.record : null;
      if (removal && (manualMultiplier === null || !Number.isFinite(manualMultiplier) || manualMultiplier < 0)) {
        setStatus("解除来源前请明确填写非负手动倍率，不会从旧报告补值");
        return;
      }
      if (mode === "confirm" && (!shown?.required_confirmation || !supported(shown))) {
        setStatus("请先读取并检查当前技能参数来源");
        return;
      }
      const proposed = candidate(removal),
        receipt = mode === "confirm" ? UI.clone(shown.required_confirmation) : confirmed() ? {
          expected_snapshot_id: row.expected_snapshot_id,
          expected_node_sha256: row.expected_node_sha256,
          ...(levelBound() ? {
            expected_level_fingerprint: row.expected_level_fingerprint
          } : {})
        } : null;
      if (mode === "confirm") Object.assign(proposed.hit_parameter_sources.find((s: any): any => s.hit_id === row.hit_id), receipt);
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
        record = MC.hitParameterCompilation(report, row.hit_id);
      if (response.code || !report || !["ready", "pending"].includes(report.state) || !removal && !record) {
        refresh();
        errors.append(UI.error(response.code ? response : {
          code: 1,
          message: "未取得当前参数校验回执，请重新读取"
        }));
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
        if (levelBound()) row.expected_level_fingerprint = null;
        revision++;
        preview.revision = revision;
        refresh();
        errors.append(UI.el("p", "数据库参数来源已变化，尚未确认或应用；请检查新显示的原文和数值后重新确认。", "eq-error"));
        return;
      }
      if (mode === "confirm") {
        if (!supported(record)) {
          refresh();
          errors.append(UI.el("p", "本参数当前仍不能自动采用，确认未写入草稿。", "eq-error"));
          return;
        }
        Object.assign(row, receipt);
        revision++;
        preview.revision = revision;
        refresh();
        setStatus("数值来源确认已记录；用途仍以显式核实状态为准");
        return;
      }
      if (mode === "preview") {
        refresh();
        return;
      }
      original.hit_parameter_sources = UI.clone(proposed.hit_parameter_sources);
      const hit = original.manual_template.hits.find((h: any): any => h.id === row.hit_id),
        submitted = proposed.manual_template.hits.find((h: any): any => h.id === row.hit_id);
      if (removal) hit.multiplier = submitted.multiplier;else {
        delete hit.multiplier;
        if (!Object.hasOwn(hit, "scaling_stat")) hit.scaling_stat = submitted.scaling_stat;
      }
      close();
      MC.touch();
      MC.hitParameterDiagnostics = {
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
      setStatus(removal ? "已解除来源并采用明确填写的倍率；尚未写入文件" : report.state === "pending" ? "已应用待完善的倍率来源，请查看校验问题；尚未写入文件" : "倍率来源已应用；请重新计算或保存");
    };
    const draw = (): any => {
      form.innerHTML = "";
      const hit = hits.find((h: any): any => h.id === row.hit_id);
      if (index === undefined) form.append(UI.field("命中或连续命中原型", UI.select(available.map((h: any): any => ({
        value: h.id,
        label: `${DS.character(h.character, RT.tpl(), original)} · ${h.hit_name || h.skill || h.id} · ${h.id}`
      })), row.hit_id, (v: any): any => {
        const previous = hits.find((h: any): any => h.id === row.hit_id),
          next = hits.find((h: any): any => h.id === v);
        row.hit_id = v;
        row.scaling_stat = next.scaling_stat || "";
        if (previous?.character !== next.character) {
          const targetRef = original.equipment_inputs?.[next.character]?.character_ref;
          row.selection = {
            character_id: targetRef?.db_character_id ?? null,
            slot: "skill",
            constellation: 0,
            ...(levelBound() ? {
              base_level: 10
            } : {
              talent_level: 10
            })
          };
          row.version_line = targetRef?.version_line || "release";
          row.param_index = 0;
          row.source = "";
          row.status = "pending";
        }
        changed();
        draw();
      })));else form.append(UI.el("p", `命中或原型：${row.hit_id}；角色：${DS.character(hit.character, RT.tpl(), original)}`));
      if (hit.scaling_stat) form.append(UI.el("p", `本命中已声明倍率属性：${MC.label(hit.scaling_stat)}（本页不会改动）`));else form.append(UI.field("明确选择本命中的倍率属性", UI.select([{
        value: "",
        label: "请选择，不默认攻击力"
      }, ...['atk', 'hp', 'def', 'em'].map((v: any): any => ({
        value: v,
        label: MC.label(v)
      }))], row.scaling_stat, (v: any): any => update("scaling_stat", v))));
      if (supportsLevels || levelBound()) form.append(UI.field("参数等级模式", UI.select([{
        value: "level",
        label: "基础等级与静态命座加级"
      }, {
        value: "legacy",
        label: "旧版零命10级兼容模式"
      }], levelBound() ? "level" : "legacy", (v: any): any => {
        delete row.selection.base_level;
        delete row.selection.talent_level;
        if (v === "level") {
          row.parameter_contract = "level-bound-parameter-v1";
          row.expected_level_fingerprint = null;
          row.selection.base_level = 10;
        } else {
          delete row.parameter_contract;
          delete row.expected_level_fingerprint;
          row.selection.talent_level = 10;
        }
        changed();
        draw();
      })));
      form.append(UI.field("数据库角色编号", UI.input(row.selection.character_id, (v: any): any => updateSelection("character_id", v), true)), UI.field("数据库资料线", UI.select([{
        value: "release",
        label: "发布资料线"
      }, {
        value: "beta",
        label: "测试资料线"
      }], row.version_line, (v: any): any => update("version_line", v))), UI.field("技能定位方式", UI.select([{
        value: "slot",
        label: "技能槽"
      }, {
        value: "skill_id",
        label: "明确技能编号"
      }], Object.hasOwn(row.selection, "skill_id") ? "skill_id" : "slot", (v: any): any => {
        delete row.selection.slot;
        delete row.selection.skill_id;
        row.selection[v] = v === "slot" ? "skill" : null;
        changed();
        draw();
      })));
      if (Object.hasOwn(row.selection, "skill_id")) form.append(UI.field("数据库技能编号", UI.input(row.selection.skill_id, (v: any): any => updateSelection("skill_id", v), true)));else form.append(UI.field("技能槽", UI.select([{
        value: "normal",
        label: "普通攻击"
      }, {
        value: "skill",
        label: "元素战技"
      }, {
        value: "burst",
        label: "元素爆发"
      }], row.selection.slot, (v: any): any => updateSelection("slot", v))));
      form.append(UI.field("技能组编号（多技能组角色须填写）", UI.input(row.selection.skill_depot_id, (v: any): any => updateSelection("skill_depot_id", v), true)), UI.field("已激活命座数", UI.input(row.selection.constellation, (v: any): any => updateSelection("constellation", v), true)), levelBound() ? UI.field("基础天赋等级（1至10，留空采用10级）", UI.input(row.selection.base_level, (v: any): any => updateSelection("base_level", v), true)) : UI.field("有效天赋等级（C0 留空采用10级）", UI.input(row.selection.talent_level, (v: any): any => updateSelection("talent_level", v), true)), UI.field("参数序号（从0开始）", UI.input(row.param_index, (v: any): any => update("param_index", v), true)), UI.field("倍率用途核实状态", UI.select([{
        value: "pending",
        label: "待核实"
      }, {
        value: "assumed",
        label: "情景假设"
      }, {
        value: "confirmed",
        label: "已人工或Agent核实"
      }], row.status, (v: any): any => update("status", v))), UI.field("作为单属性直接伤害倍率的依据", UI.area(row.source, (v: any): any => update("source", v))));
      if (index !== undefined) form.append(UI.field("解除绑定后采用的手填倍率（不自动恢复）", UI.input(manualMultiplier, (v: any): any => {
        manualMultiplier = v;
        revision++;
        request++;
        busy = false;
        preview = null;
        refresh();
      }, true)));
    };
    dialog.appBody.append(UI.el("p", "只给已有命中或原型取数，不生成攻击。应用绑定会移除手填倍率并由数据库编译；不会改变原有倍率属性、动作、时点或次数。"), UI.el("p", "新等级模式从基础等级和已激活命座读取原始参数；其它命座效果尚未执行。旧兼容模式限零命10级。有效13级须来自已核实的加级关系，不能直接填写代替依据。"), form, previewBox, errors, actions);
    dialog.appFooter.append(UI.button("← 返回机制列表", close));
    dialog.appFooter.hidden = false;
    draw();
    refresh();
  };
  MC.renderHitParameterSources = (box: any): any => {
    const sources = PL.state.plan?.hit_parameter_sources || [],
      card = UI.el("section", null, "eq-card");
    card.append(UI.el("h4", `数据库技能倍率来源（${sources.length}）`), UI.el("p", "从精确技能参数读取已有命中的倍率；用途与倍率属性需要单独核实。"), UI.button("添加数据库技能倍率来源", (): any => MC.editHitParameterSource()));
    sources.forEach((row: any, index: any): any => card.append(UI.el("p", `${row.hit_id} · 参数 ${row.param_index} · ${MC.label(row.scaling_stat)} · 用途${MC.label(row.status)} · ${row.expected_snapshot_id && row.expected_node_sha256 ? "已记录来源确认，本次计算仍会复核" : "来源待核实"}`), UI.button(`编辑倍率来源 ${index + 1}`, (): any => MC.editHitParameterSource(index))));
    const check = MC.hitParameterDiagnostics;
    if (check && check.plan === PL.state.plan && check.input === PL.currentInputJson() && check.epoch === PL.state.sourceEpoch && check.path === RT.state.path && check.sha === PL.state.fileSha) {
      card.append(UI.el("p", `最近一次倍率来源校验：${check.state === "ready" ? "通过声明输入检查" : "方案待完善"}；后续计算仍会重新读取。`));
      MC.renderElementalIssues(card, check.issues, "方案待完善事项");
      if (check.record) MC.renderHitParameterCompilation(card, check.record);
    }
    box.append(card);
  };
}
