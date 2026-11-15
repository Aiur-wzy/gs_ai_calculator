import type { MechanicsEditor, MechanicsEditorPorts } from "./contracts";
export function installMechanicsController(MC: MechanicsEditor, ports: MechanicsEditorPorts): void {
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
  MC.model = (): any => PL.state.plan?.mechanics_model;
  MC.touch = (): any => {
    RC.invalidate();
    RT.syncRevision();
    PL.renderStatus();
    MC.render();
    RT.renderTimeline?.();
  };
  Object.assign(MC.words, {
    attribute_model: "属性账与取样基底",
    contract: "契约版本",
    initial_basis: "初始基底声明",
    nonextra: "非额外精通",
    base: "基础量",
    panel_total: "明确全部属于普通精通",
    explicit: "明确数值",
    included_components: "初始面板已含来源",
    effect_classifications: "动态属性来源分类",
    component: "来源组件",
    nonextra_inclusion: "是否计入非额外精通",
    included: "计入",
    excluded: "不计入",
    effect_ref: "收益引用",
    manual_buff: "手工区间增益",
    part_id: "来源部件",
    source_fingerprint: "来源指纹",
    basis: "取值基底",
    total: "总量"
  });
  MC.adoptAttributes = (): any => {
    if (PL.state.draft || !MC.model()?.attribute_model) {
      setStatus("请先应用装备草稿，并填写属性账声明");
      return;
    }
    const original = PL.state.plan,
      baseline = PL.currentInputJson(),
      epoch = PL.state.sourceEpoch,
      path = RT.state.path,
      sha = PL.state.fileSha;
    const errors = UI.el("div");
    let note = "",
      closed = false,
      busy = false;
    const invalidate = (): any => {
      if (closed) return;
      closed = true;
    };
    const dialog = extensions.AppDialogKernel?.create?.({
      title: "核对属性来源",
      description: "先核对初始面板、基底和每项分类。软件仅对齐实际来源身份；未知分类仍保持未知，已含属性不重复加算。",
      onRequestClose: (): any => {
        invalidate();
        return true;
      }
    }) || null;
    if (!dialog) {
      setStatus("当前还有窗口正在处理，请先完成或关闭后再核对属性来源");
      return;
    }
    const close = (): any => {
      invalidate();
      dialog.finish("attribute-review-close");
    };
    const current = (): any => !closed && PL.state.plan === original && PL.currentInputJson() === baseline && !PL.state.draft && PL.state.sourceEpoch === epoch && RT.state.path === path && PL.state.fileSha === sha;
    const apply = UI.button("核对完毕，采用当前声明", async (): Promise<any> => {
      if (busy) return;
      if (!current()) {
        errors.append(UI.el("p", "方案已变化，请重新打开核对。", "eq-error"));
        return;
      }
      if (!note.trim()) {
        errors.append(UI.el("p", "请填写基底与分类的核对依据。", "eq-error"));
        return;
      }
      const submittedNote = note;
      busy = true;
      apply.disabled = true;
      dialog.setBusy(true);
      let response;
      try {
        response = await callApi("prepare_attribute_review", UI.clone(original), submittedNote.trim());
      } catch (error) {
        response = {
          code: 1,
          message: String(error)
        };
      }
      busy = false;
      if (!current()) return;
      dialog.setBusy(false);
      apply.disabled = false;
      if (submittedNote !== note) {
        errors.append(UI.el("p", "核对依据已修改，请再次确认。", "eq-error"));
        return;
      }
      if (response.code || !response.data?.plan || response.data.attribute_source_adoption?.contract !== "attribute-source-adoption-v1") {
        errors.append(UI.error(response.code ? response : {
          code: 1,
          message: "未获得可核对的属性来源回执"
        }));
        return;
      }
      const saved = PL.state.savedJson;
      close();
      PL.replace(response.data.plan, path, sha);
      PL.state.savedJson = saved;
      PL.state.report = response.data;
      PL.renderStatus();
      MC.render();
      setStatus(response.data.state === "ready" ? "属性声明已采用为未保存候选，请重新计算" : "属性声明已采用；其他资料仍待补全，请查看缺项后重新计算");
    });
    dialog.appBody.append(UI.details("本次待核对的声明", MC.model().attribute_model), UI.field("核对依据", UI.area(note, (v: any): any => {
      note = v;
    })), errors);
    dialog.appFooter.append(UI.button("← 返回机制列表", close), apply);
    dialog.appFooter.hidden = false;
  };
  MC.edit = (key: any): any => {
    if (PL.state.draft) {
      setStatus("请先应用或放弃装备草稿");
      return;
    }
    const baseline = PL.currentInputJson(),
      model = UI.clone(MC.model()),
      form = UI.el("div"),
      errors = UI.el("div");
    let closed = false,
      busy = false,
      request = 0;
    const invalidate = (): any => {
      if (closed) return;
      closed = true;
      request++;
    };
    const dialog = extensions.AppDialogKernel?.create?.({
      title: `编辑${MC.label(key)}`,
      className: "mc-dialog",
      onRequestClose: (): any => {
        invalidate();
        return true;
      }
    }) || null;
    if (!dialog) {
      setStatus("当前还有窗口正在处理，请先完成或关闭后再编辑机制");
      return;
    }
    const draw = (): any => {
      form.innerHTML = "";
      MC.form(form, MC.caps.schema.properties[key], model[key] ?? MC.default(MC.caps.schema.properties[key]), (v: any): any => {
        model[key] = v;
      }, MC.label(key), draw);
    };
    model[key] ??= MC.default(MC.caps.schema.properties[key]);
    draw();
    const close = (): any => {
        invalidate();
        dialog.finish("mechanics-edit-close");
      },
      apply = UI.button("应用修改", async (): Promise<any> => {
        if (busy) return;
        if (closed || baseline !== PL.currentInputJson() || PL.state.draft) {
          setStatus("方案已变化，请重新打开编辑");
          return;
        }
        const token = ++request;
        busy = true;
        dialog.setBusy(true);
        apply.disabled = true;
        const r = await callApi("validate_mechanics_model", model);
        if (token !== request || closed) return;
        busy = false;
        dialog.setBusy(false);
        apply.disabled = false;
        if (baseline !== PL.currentInputJson() || PL.state.draft) {
          setStatus("校验期间方案已变化，请重新打开");
          return;
        }
        if (r.code || r.data.status === "invalid") {
          errors.innerHTML = "";
          errors.append(r.code ? UI.error(r) : UI.details("待修正的字段", r.data.issues));
          return;
        }
        PL.state.plan.mechanics_model = model;
        close();
        MC.touch();
      });
    dialog.appBody.append(form, errors);
    dialog.appFooter.append(UI.button("← 返回机制列表", close), apply, UI.button("放弃并关闭", close));
    dialog.appFooter.hidden = false;
  };
  MC.enable = async (): Promise<any> => {
    if (!(await RC.ensurePlan())) return;
    const baseline = PL.currentInputJson(),
      r = await callApi("enable_mechanics", UI.clone(PL.state.plan));
    if (baseline !== PL.currentInputJson() || PL.state.draft) {
      setStatus("方案已变化，请重试");
      return;
    }
    if (r.code) {
      UI.error(r);
      return;
    }
    const saved = PL.state.savedJson;
    PL.replace(r.data.plan || r.data, RT.state.path, PL.state.fileSha);
    PL.state.savedJson = saved;
    MC.touch();
    setStatus("已启用机制方案，请明确动作、规则与初始状态，再预览和计算");
  };
  MC.newRule = (): any => {
    if (PL.state.draft) {
      setStatus("请先应用或放弃装备草稿");
      return;
    }
    const model = MC.model(),
      owner = RT.tpl()?.characters[0]?.id;
    if (!owner) return;
    const bid = UI.id("binding"),
      pid = UI.id("part"),
      rid = UI.id("rule"),
      rule = MC.default(MC.caps.schema.properties.rules.items);
    Object.assign(rule, {
      id: rid,
      name_zh: "待编辑机制",
      owner,
      source_id: `${bid}:${pid}`,
      status: "pending",
      source: "待核实机制依据"
    });
    Object.assign(rule.timing, {
      duration: 10,
      cooldown: 0
    });
    Object.assign(rule.stacking, {
      max: 1,
      add: 1
    });
    model.rules.push(rule);
    model.initial_state.rules.push({
      rule_id: rid,
      cooldown_remaining: 0,
      instances: []
    });
    PL.state.plan.effect_bindings.push({
      binding_id: bid,
      provider_character_id: owner,
      owner: {
        kind: "manual",
        note: "本方案明确声明的机制"
      },
      source_refs: [{
        kind: "manual",
        note: "待核实机制依据"
      }],
      source_fingerprint: null,
      coverage: {
        state: "unreviewed",
        reviewed_by: null,
        source_fingerprint: null,
        note: null
      },
      parts: [{
        part_id: pid,
        label: "待编辑机制来源",
        state: "pending",
        rule: {
          kind: "mechanics",
          rule_id: rid
        },
        delivery: {
          kind: "mechanics"
        },
        conditions: [],
        dependencies: {
          mode: "source_only",
          context_fingerprint: null,
          note: "以机制模型声明为准"
        },
        reason: null
      }]
    });
    MC.touch();
    MC.edit("rules");
  };
  MC.exportWorkbook = async (): Promise<any> => {
    RT.syncRevision();
    if (!PL.state.plan || !RS.isCurrent() || RS.state.pending) {
      setStatus("请先计算当前原生方案，再导出活公式工作簿");
      return;
    }
    const r = await callApi("export_formula_workbook", UI.clone(PL.state.plan), RS.state.snapshot.name, RS.agentCandidateSessionId?.() ?? null);
    if (r.code) {
      UI.error(r);
      return;
    }
    setStatus("活公式工作簿已导出；请查看工作簿内的支持范围和失效提示", {
      path: r.data.path
    });
  };
  MC.init = (): any => {
    $("#btn-mechanics")?.addEventListener("click", (): any => {
      MC.shown = !MC.shown;
      if (MC.shown && extensions.RC) {
        RC.shown = false;
        RC.render();
      }
      MC.render();
    });
    $("#btn-exp-formula")?.addEventListener("click", MC.exportWorkbook);
    return callApi("mechanics_capabilities").then((r: any): any => {
      if (r.code) {
        UI.error(r);
        return;
      }
      MC.caps = r.data;
      MC.render();
    });
  };
}
