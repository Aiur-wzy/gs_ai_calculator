import type { MechanicsEditor, MechanicsEditorPorts } from "./contracts";
export function installMechanicsReactionSources(MC: MechanicsEditor, ports: MechanicsEditorPorts): void {
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
  MC.reactionCompilation = (report: any): any => (report?.reaction_damage_compilations || report?.plan?.resolution?.reaction_damage_compilations || [])[0];
  MC.renderReactionCompilation = (box: any, record: any): any => {
    if (!record) {
      box.append(UI.el("p", "请读取当前数据库，核对冰水原目标来源与伤害序列。"));
      return;
    }
    box.append(UI.el("p", "数据库只提供伤害冷却的来源与参数。普通原目标归属和次序由当前规则集承担，尚未执行一般能力、特殊扩散或传播。"));
    (record.profiles || []).forEach((profile: any, i: any): any => {
      const p = profile.projection || {};
      box.append(UI.el("h4", `${MC.label(profile.selection?.element || `来源 ${i + 1}`)}扩散`), UI.el("p", `伤害序列：${p.damage_sequence?.join("、") ?? "尚不能执行"}；重置：${p.reset_cycle_seconds_rounded ?? "未知"} 秒`), UI.el("p", `序列状态：${MC.label(profile.execution?.sequence_status || "pending")}；完整能力仍未执行。`));
      MC.renderElementalIssues(box, profile.issues, "来源未解释项");
    });
    if (record.issue) box.append(UI.el("p", record.issue.message || "扩散伤害来源不可用", "eq-error"));
    MC.renderElementalIssues(box, record.assembly_issues, "组合校验问题");
    box.append(UI.details("查看扩散来源原文与完整证据", record));
  };
  MC.swirlRulesets = ["gauge-swirl-v2", "gauge-freeze-reference-v1", "gauge-swirl-dynamic-v3"];
  Object.assign(MC.words, {
    "gauge-freeze-reference-v1": "连续冻结实验参考模型",
    reference_acceptance: "参考模型假设确认",
    contract_id: "约定编号",
    contract_fingerprint: "约定内容指纹",
    acknowledged: "已接受所列假设",
    experimental_reference: "实验参考",
    contact_profiles: "接触档案",
    contact: "接触方式",
    freeze_interaction: "冻结交互",
    may_reduce: "可能削冻或碎冰",
    ordinary_aura: "普通元素底",
    frozen: "冻结量",
    freeze: "冻结",
    freezable: "目标可被冻结",
    resistance: "冻结抗性",
    freeze_history_seconds: "冻结历史量（秒）",
    freeze_decay_gu_per_second: "当前冻结衰减速率",
    elemental_state_transition: "元素状态变化",
    layer_consumptions: "逐层消耗",
    reference_contract: "参考模型约定",
    profile: "已声明接触档案",
    frozen_expired: "冻结自然到期",
    ordinary_aura_expired: "普通元素底自然到期",
    freeze_history_recovered: "冻结历史已恢复",
    freeze_contact_unknown: "接触的削冻或碎冰性质待核实",
    freeze_package_effect_ambiguous: "同次多反应的收益顺序尚未明确"
  });
  MC.elementalStateText = (state: any): any => {
    if (!state || state.knowledge === "unknown") return "未知";
    const number = (value: any): any => Number.isFinite(value) ? String(Number(value.toFixed(6))) : "未知";
    const aura = state.ordinary_aura ?? state.aura,
      parts: any = [];
    parts.push(aura ? `${MC.label(aura.element)}底 ${number(aura.remaining_gu)} GU` : "无普通元素底");
    if (Object.hasOwn(state, "frozen")) parts.push(state.frozen ? `冻结量 ${number(state.frozen.remaining_gu)} GU` : "未冻结", `冻结历史 ${number(state.freeze_history_seconds)} 秒`);
    return parts.join("；");
  };
  MC.renderReferenceModel = (box: any): any => {
    const original = PL.state.plan,
      model = original?.mechanics_model?.elemental_model;
    if (model?.ruleset !== "gauge-freeze-reference-v1") return;
    const contract = MC.caps?.elemental?.reference_contract,
      accepted = model.reference_acceptance;
    const card = UI.el("section", null, "eq-card");
    card.append(UI.el("h4", "连续冻结：实验参考范围"), UI.el("p", "本模型含有来源的规则假设和反应聚合约定。接受表示按这些约定计算，不表示所有机制已经游戏实测。"));
    if (!contract) {
      card.append(UI.el("p", "未取得当前参考约定，不能确认。"));
      box.append(card);
      return;
    }
    (contract.assumptions || []).forEach((text: any): any => card.append(UI.el("p", text)));
    const current = accepted?.acknowledged === true && accepted.contract_id === contract.contract_id && accepted.contract_fingerprint === contract.contract_fingerprint;
    card.append(UI.el("p", current ? "已接受当前约定；重算仍会核对指纹。" : "参考约定尚未确认或已经变化。"));
    let reason = accepted?.source || "";
    card.append(UI.field("本方案采用参考模型的依据", UI.area(reason, (value: any): any => {
      reason = value;
    })), UI.button("接受所示参考约定", (): any => {
      if (PL.state.plan !== original || PL.state.draft || original.mechanics_model?.elemental_model !== model) {
        setStatus("方案已变化，请重新打开机制列表");
        return;
      }
      if (!reason.trim()) {
        setStatus("请说明本方案采用参考模型的依据");
        return;
      }
      model.reference_acceptance = {
        contract_id: contract.contract_id,
        contract_fingerprint: contract.contract_fingerprint,
        acknowledged: true,
        source: reason
      };
      MC.touch();
      setStatus("已记录参考模型假设；尚未执行计算或验证真实游戏机制");
    }), UI.details("查看完整来源与约定", contract));
    box.append(card);
  };
  MC.editReactionDamageSource = (): any => {
    if (PL.state.draft) {
      setStatus("请先应用或放弃装备草稿");
      return;
    }
    const original = PL.state.plan,
      model = original?.mechanics_model?.elemental_model;
    if (original?.plan_version !== 3 || !MC.swirlRulesets.includes(model?.ruleset) || !model.swirl_damage) {
      setStatus("请先明确受支持的普通扩散或冻结参考模型及扩散伤害档案");
      return;
    }
    const sourceElements = model.ruleset === "gauge-swirl-dynamic-v3" ? ["cryo", "hydro", "pyro"] : ["cryo", "hydro"];
    const sourceLabel = sourceElements.length === 3 ? "冰水火" : "冰水";
    const old = original.reaction_damage_sources?.[0],
      row = UI.clone(old || {
        reaction_type: "swirl",
        version_line: "release",
        source: "",
        expected_snapshot_id: null,
        expected_profile_fingerprint: null
      });
    const baseline = PL.currentInputJson(),
      epoch = PL.state.sourceEpoch,
      path = RT.state.path,
      sha = PL.state.fileSha;
    const previewBox = UI.el("div"),
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
      title: "数据库扩散伤害来源",
      className: "mc-dialog",
      description: `仅用于普通单目标${sourceLabel}扩散；来源应用不会修改动作或升级机制证据状态。`,
      onRequestClose: (): any => {
        invalidate();
        return true;
      }
    }) || null;
    if (!dialog) {
      setStatus("当前还有窗口正在处理，请先完成或关闭后再编辑扩散伤害来源");
      return;
    }
    const current = (): any => !closed && PL.state.plan === original && PL.currentInputJson() === baseline && !PL.state.draft && PL.state.sourceEpoch === epoch && RT.state.path === path && PL.state.fileSha === sha;
    const valid = (): any => {
      if (current()) return true;
      setStatus("方案或数据库状态已变化，请重新打开扩散伤害来源");
      return false;
    };
    const close = (): any => {
      invalidate();
      dialog.finish("reaction-damage-source-close");
    };
    const receipt = (): any => row.expected_snapshot_id && row.expected_profile_fingerprint ? {
      expected_snapshot_id: row.expected_snapshot_id,
      expected_profile_fingerprint: row.expected_profile_fingerprint
    } : null;
    const supported = (record: any): any => {
      const profiles = record?.profiles;
      if (record?.issue || record?.assembly_issues?.length || profiles?.length !== sourceElements.length) return false;
      if (JSON.stringify(profiles.map((p: any): any => p.selection?.element).sort()) !== JSON.stringify(sourceElements)) return false;
      if (!profiles.every((p: any): any => p.execution?.sequence_status === "ready" && p.snapshot?.snapshot_id === record.required_confirmation?.expected_snapshot_id)) return false;
      const valid = (p: any): any => {
        const q = p.projection || {},
          seq = q.damage_sequence,
          s = q.reset_cycle_seconds_rounded;
        return Number.isFinite(s) && s > 0 && s <= 1e9 && Array.isArray(seq) && seq.length > 0 && seq.length <= 64 && seq.every((v: any): any => v === 0 || v === 1);
      };
      return profiles.every(valid) && profiles.every((p: any): any => p.projection.reset_cycle_seconds_rounded === profiles[0].projection.reset_cycle_seconds_rounded && JSON.stringify(p.projection.damage_sequence) === JSON.stringify(profiles[0].projection.damage_sequence));
    };
    const same = (record: any, confirmation: any): any => record?.required_confirmation?.expected_snapshot_id === confirmation.expected_snapshot_id && record?.required_confirmation?.expected_profile_fingerprint === confirmation.expected_profile_fingerprint;
    const candidate = (remove: any): any => {
      const plan = UI.clone(original);
      plan.reaction_damage_sources = remove ? [] : [UI.clone(row)];
      plan.mechanics_model.elemental_model.swirl_damage.icd = {
        kind: "unknown"
      };
      return plan;
    };
    const refresh = (): any => {
      dialog.setBusy(busy);
      previewBox.innerHTML = "";
      errors.innerHTML = "";
      previewBox.append(UI.el("p", busy ? `正在核对当前${sourceLabel}来源…` : receipt() ? "已记录来源确认，计算仍会重新读取。" : "来源尚未确认，可保存待完善草稿。"));
      MC.renderReactionCompilation(previewBox, preview?.revision === revision ? preview.record : null);
      if (preview?.revision === revision) MC.renderElementalIssues(previewBox, preview.issues, "方案待完善事项");
      actions.innerHTML = "";
      const add = (label: any, fn: any, disabled: any = false): any => {
        const button = UI.button(label, fn);
        button.disabled = busy || disabled;
        actions.append(button);
      };
      add("读取并预览扩散来源", (): any => run("preview"));
      add("确认所示扩散来源", (): any => run("confirm"), !preview?.record?.required_confirmation || preview.revision !== revision || !supported(preview.record));
      add("应用扩散来源绑定", (): any => run("apply"));
      if (old) add("移除扩散来源绑定", (): any => run("remove"));
      actions.append(UI.button("放弃并关闭", close));
    };
    const changed = (): any => {
      row.expected_snapshot_id = null;
      row.expected_profile_fingerprint = null;
      revision++;
      request++;
      busy = false;
      preview = null;
      refresh();
    };
    const run = async (mode: any): Promise<any> => {
      if (busy || !valid()) return;
      const remove = mode === "remove",
        shown = preview?.revision === revision ? preview.record : null;
      if (mode === "confirm" && (!shown?.required_confirmation || !supported(shown))) {
        setStatus(`请先读取并核对当前${sourceLabel}来源`);
        return;
      }
      const proposed = candidate(remove),
        confirmation = mode === "confirm" ? UI.clone(shown.required_confirmation) : receipt();
      if (mode === "confirm") Object.assign(proposed.reaction_damage_sources[0], confirmation);
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
        record = MC.reactionCompilation(report);
      if (response.code || !report || !["ready", "pending"].includes(report.state) || !remove && !record) {
        refresh();
        errors.append(UI.error(response.code ? response : {
          code: 1,
          message: "未取得当前扩散来源回执，请重新读取"
        }));
        return;
      }
      preview = {
        revision,
        record,
        issues: report.missing_inputs || []
      };
      if (!remove && confirmation && !same(record, confirmation)) {
        row.expected_snapshot_id = null;
        row.expected_profile_fingerprint = null;
        revision++;
        preview.revision = revision;
        refresh();
        errors.append(UI.el("p", "扩散来源已变化，未确认或应用；请复核新显示的内容。", "eq-error"));
        return;
      }
      if (mode === "confirm") {
        if (!supported(record) || record.status !== "ready") {
          refresh();
          errors.append(UI.el("p", "当前伤害序列仍不能执行，确认未写入。", "eq-error"));
          return;
        }
        Object.assign(row, confirmation);
        revision++;
        preview.revision = revision;
        refresh();
        return;
      }
      if (mode === "preview") {
        refresh();
        return;
      }
      original.reaction_damage_sources = UI.clone(proposed.reaction_damage_sources);
      model.swirl_damage.icd = {
        kind: "unknown"
      };
      close();
      MC.touch();
      setStatus(remove ? "已移除来源；伤害冷却保持未知，实际扩散前需补充规则" : "扩散来源已应用；普通范围证据及方案其他缺项仍需核实，尚未写入文件");
    };
    dialog.appBody.append(UI.el("p", `仅用于普通单目标${sourceLabel}扩散。应用时移除手填伤害序列，原生保存未知占位，计算从数据库重新编译；不会修改动作或升级机制证据状态。`), UI.field("扩散来源资料线", UI.select([{
      value: "release",
      label: "发布资料线"
    }, {
      value: "beta",
      label: "测试资料线"
    }], row.version_line, (v: any): any => {
      row.version_line = v;
      changed();
    })), UI.field("普通扩散适用范围与来源复核依据", UI.area(row.source, (v: any): any => {
      row.source = v;
      changed();
    })), previewBox, errors, actions);
    dialog.appFooter.append(UI.button("← 返回机制列表", close));
    dialog.appFooter.hidden = false;
    refresh();
  };
  MC.renderReactionDamageSources = (box: any): any => {
    const rows = PL.state.plan?.reaction_damage_sources || [],
      card = UI.el("section", null, "eq-card");
    card.append(UI.el("h4", `数据库扩散伤害来源（${rows.length}）`), UI.el("p", "扩散发生与扩散出伤分别记录，伤害冷却不使用附着序列。"), UI.button(rows.length ? "编辑扩散伤害来源" : "添加扩散伤害来源", MC.editReactionDamageSource));
    box.append(card);
  };
}
