import type { MechanicsEditor, MechanicsEditorPorts } from "./contracts";
export function installMechanicsView(MC: MechanicsEditor, ports: MechanicsEditorPorts): void {
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
  MC.render = (): any => {
    const box = $("#mechanics-editor");
    if (!box) return;
    box.classList.toggle("hidden", !MC.shown);
    if (!MC.shown) return;
    box.innerHTML = "";
    box.append(UI.button("← 返回排轴", (): any => {
      MC.shown = false;
      MC.render();
      $("#btn-mechanics")?.scrollIntoView?.({
        block: "center"
      });
    }), UI.el("h3", "机制规则与过程"));
    if (!MC.caps) {
      box.append(UI.el("p", "正在加载机制接口…"));
      return;
    }
    if (!MC.model()) {
      box.append(UI.el("p", "启用后可声明触发、次数、叠层和采样，引擎与动作、能量共同执行。"), UI.button("启用机制模型", MC.enable));
      return;
    }
    box.append(UI.el("p", "每条机制应关联装备效果或人工来源分项。先声明规则，再到角色装备的“效果处理”核实来源和整段覆盖。未知依据保留待核实。"), UI.button("添加机制及待核实来源", MC.newRule));
    ["rules", "fixed_effect_sources", "hit_events", "hit_patterns", "condition_events", "initial_state", "resource_ownership", "shared_pools", "attribute_model", "state_runtime", "assumptions", "elemental_model", "application_events"].filter((key: any): any => MC.caps.schema.properties[key]).forEach((key: any): any => {
      const card = UI.el("details", null, "eq-card mc-section-card"),
        value = MC.model()[key];
      card.append(UI.el("summary", `${MC.label(key)}${Array.isArray(value) ? `（${value.length}）` : ""}`), UI.button(`编辑${MC.label(key)}`, (): any => MC.edit(key)));
      if (["rules", "fixed_effect_sources"].includes(key)) for (const rule of value || []) card.append(UI.el("p", `${DS.prose(rule.name_zh, "机制中文名待补")} · ${DS.character(rule.owner, RT.tpl(), PL.state.plan)} · ${MC.label(rule.status)}`));
      if (key === "shared_pools") card.append(UI.el("p", "资源以整数记账；带单位换算的池以整数原子表示小数资源。支持按实际命中消费或明确的资源扣量操作。相关来源须核对当前整份方案；修改资源或动作后须重新核对并计算。"));
      if (key === "state_runtime") card.append(UI.el("p", "声明领域建立、结束与定时刷新；时间字段使用微秒，1 秒为 1000000 微秒。领域结束仅停止后续发放，已发放收益按自身持续时间到期。来源需核对当前整份方案。"));
      if (key === "attribute_model" && value) card.append(UI.el("p", value.contract === "attribute-ledger-v3" ? "精通与攻击总量来自当前面板；初始已含组成不会再加一次。百分比攻击按受益者基础攻击力折算，缺失白值须补全。非额外分类仅支持精通；未知仍须核实。" : "总精通来自当前面板；非额外精通和已含组成按依据声明。未知分类不会被自动认定。"), UI.button("核对并采用属性来源", MC.adoptAttributes));
      card.append(UI.details("查看原始声明", value ?? []));
      box.append(card);
    });
    MC.renderElementalVerificationTasks(box, PL.state.report);
    MC.renderElementalSources(box);
    MC.renderHitParameterSources(box);
    MC.renderReactionDamageSources(box);
    MC.renderReferenceModel(box);
    extensions.AE?.render(box);
    extensions.SC?.render(box);
    box.append(UI.details("当前能力与限制", {
      supported: MC.caps.supported,
      unsupported: MC.caps.unsupported
    }));
  };
}
