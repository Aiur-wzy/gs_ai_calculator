// 排轴编辑器（G2）：状态 + 工具栏。单一真实形态 = 模板 JSON（engine.md §4）
"use strict";

window.RT = {
  state: { template: null, path: "", selection: null, enums: null, plansDir: "",
    revision: 0, templateJson: "null", loadRequest: 0, saveRequest: 0, acceptedSaveRequest: 0, fileSha: null },
};

RT.tpl = () => RT.state.template;
RT.sel = () => RT.state.selection;

RT.syncRevision = (force = false) => {
  let json = window.PL ? PL.currentInputJson() : JSON.stringify(RT.tpl());
  if (force || json !== RT.state.templateJson) {
    if (!force && window.PL) { PL.onEdit(); json = PL.currentInputJson(); }
    RT.state.templateJson = json;
    RT.state.revision += 1;
    if (window.RS) RS.invalidate();
  }
};

RT.select = (kind, index, redraw = true) => {
  RT.state.selection = { kind, index };
  if (redraw) RT.renderAll();
  else {
    RT.updateTimelineSelection();
    RT.renderPropPanel();
  }
};

RT.renderAll = () => {
  RT.renderTeamPanel();
  RT.renderPropPanel();
  RT.syncRevision();
  RT.renderTimeline();
  if (window.RC) RC.render();
  if (window.MC) MC.render();
  if(window.WS?.ready)WS.mount($("#rotation-main"),{key:"rotation",left:"#team-panel",right:".timeline-inspector",repaint:()=>window.TV?.requestPaint?.()});
};

RT.replaceTemplate = (template, path, fileSha = null) => {
  RT.cancelDrag?.();
  RT.state.template = template;
  RT.state.path = path;
  RT.state.fileSha = fileSha;
  RT.state.selection = null;
  $("#tpl-path").value = path;
  RT.syncRevision(true);
  RT.renderAll();
  if (window.DS) DS.ensure(DS.line(template)).then(() => {
    if (RT.tpl() === template) RT.renderAll();
  });
};

RT.charIds = () => RT.tpl().characters.map((c) => c.id);

RT.charColor = (id) => {
  const palette = ["#5b8cff", "#e06c75", "#61af72", "#d19a66", "#c678dd", "#56b6c2"];
  const i = RT.charIds().indexOf(id);
  return palette[(i < 0 ? 0 : i) % palette.length];
};

RT.round2 = (v) => Math.round(v * 100) / 100;

// ---------- 工具栏 ----------

RT.refreshPlans = async () => {
  const r = await callApi("list_plans");
  if (r.code !== 0) { setStatus(r.message); return; }
  RT.state.plansDir = r.data.dir;
  const sel = $("#plan-select");
  sel.innerHTML = '<option value="">— 方案库 —</option>';
  r.data.plans.forEach((name) => {
    const o = document.createElement("option");
    o.value = name;
    o.textContent = name;
    sel.appendChild(o);
  });
};

RT.loadTemplate = async (path) => {
  const request = ++RT.state.loadRequest;
  const r = await callApi("load_template", path);
  if (request !== RT.state.loadRequest) return;
  if (r.code !== 0) { setStatus(r.message); return; }
  RT.replaceTemplate(r.data.template, r.data.path, r.data.file_sha ?? null);
  setStatus(`已加载模板（伤害事件 ${RT.tpl().hits.length} 条，增益 ${(RT.tpl().buffs || []).length} 条）`, {path:r.data.path});
  RT.renderAll();
};

RT.saveTemplate = async (options = {}) => {
  if (!RT.tpl()) { setStatus("没有可保存的模板"); return false; }
  const template = RT.tpl(), submitted = UI.clone(template), json = JSON.stringify(template);
  const request = ++RT.state.saveRequest;
  let path = $("#tpl-path").value.trim();
  if (!path) {
    const name = (RT.tpl().meta && RT.tpl().meta.name) || "untitled";
    path = `${RT.state.plansDir}/${name}.json`;
  }
  const r = await callApi("save_template", path, submitted, path === RT.state.path ? RT.state.fileSha : null);
  options.onResponse?.(r);
  if (r.code !== 0) { setStatus(r.message); return false; }
  options.onWritten?.({path:r.data.path,fileSha:r.data.file_sha,current:RT.tpl()===template&&JSON.stringify(template)===json});
  if (request < RT.state.acceptedSaveRequest || RT.tpl() !== template) {
    setStatus("所提交的模板副本已保存，当前文档已切换或已有更新的保存结果，未改动当前文件路径。", {path:r.data.path});
    return false;
  }
  RT.state.acceptedSaveRequest = request;
  RT.state.path = r.data.path;
  RT.state.fileSha = r.data.file_sha ?? null;
  $("#tpl-path").value = r.data.path;
  setStatus(JSON.stringify(template) === json ? "模板已保存" : "所提交的模板副本已保存；后续修改仍需保存。", {path:r.data.path});
  RT.refreshPlans();
  return true;
};

RT.newTemplate = async () => {
  const request = ++RT.state.loadRequest;
  const r = await callApi("new_template");
  if (request !== RT.state.loadRequest) return;
  if (r.code !== 0) { setStatus(r.message); return; }
  RT.replaceTemplate(r.data.template, "");
  setStatus("已创建新模板（保存时写入方案库）");
  RT.renderAll();
};

RT.addHit = () => {
  if (!RT.tpl()) return;
  const c = RT.charIds()[0];
  RT.tpl().hits.push({
    t: 1.0, character: c, skill: "skill", hit_name: "新事件",
    multiplier: 1.0, damage_system: "classic", element: "physical",
  });
  if (RT.tpl().version === 2) Object.assign(RT.tpl().hits[RT.tpl().hits.length-1], {id:UI.id("hit"),damage_type_ids:null});
  RT.select("hit", RT.tpl().hits.length - 1);
  setStatus("已添加事件");
};

RT.addBuff = () => {
  if (!RT.tpl()) return;
  const buffs = RT.tpl().buffs ??= [];
  const ids = new Set(buffs.map((b) => b.id));
  let n = buffs.length + 1;
  while (ids.has(`buff_${n}`)) n += 1;
  buffs.push({
    id: `buff_${n}`, name: `新增益 ${n}`, source: "", scope: "team", start: 0, end: 5,
    effects: [{ zone: "dmg_bonus", stat: "all", value: 0.2 }],
  });
  if (RT.tpl().version === 2) Object.assign(buffs[buffs.length-1].effects[0], {
    id:UI.id("effect"),benefit_type_id:"benefit.dmg_bonus.all",applies_to:null});
  RT.select("buff", buffs.length - 1);
  setStatus("已添加增益");
};

RT.addSwitch = () => {
  if (!RT.tpl()) return;
  if(window.PL?.state.plan?.rotation_model){setStatus("当前切人轨由动作模型生成，请在动作与能量中编辑切人动作");return;}
  RT.tpl().switches = RT.tpl().switches || [];
  RT.tpl().switches.push({ t: 0, character: RT.charIds()[0] });
  RT.select("switch", RT.tpl().switches.length - 1);
  setStatus("已添加切人标记");
};

RT.init = async () => {
  const r = await callApi("get_enums");
  if (r.code !== 0) { setStatus(`枚举加载失败: ${r.message}`); return; }
  RT.state.enums = r.data;
  DS.configure(r.data.display);
  await DS.ensure("release");

  await RT.refreshPlans();
  $("#plan-select").addEventListener("change", (e) => {
    if (e.target.value) RT.loadTemplate(`${RT.state.plansDir}/${e.target.value}`);
  });
  $("#btn-new").addEventListener("click", RT.newTemplate);
  $("#btn-load").addEventListener("click", () => {
    const p = $("#tpl-path").value.trim();
    if (p) RT.loadTemplate(p); else setStatus("请输入模板路径或从方案库选择");
  });
  $("#btn-save").addEventListener("click", RT.saveTemplate);
  $("#btn-add-hit").addEventListener("click", RT.addHit);
  $("#btn-add-buff").addEventListener("click", RT.addBuff);
  $("#btn-add-switch").addEventListener("click", RT.addSwitch);

  window.addEventListener("resize", () => { if (RT.tpl()) RT.renderTimeline(); });
  RT.renderTeamPanel();
  RT.renderTimeline();
  RT.renderPropPanel();
};
