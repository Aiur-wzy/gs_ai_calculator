// 属性面板（G2）：选中 hit / buff / switch 后的全字段编辑
"use strict";

// ---------- 控件构造 ----------
function field(labelText, inputEl) {
  const div = document.createElement("div");
  div.className = "fld";
  const lab = document.createElement("label");
  lab.textContent = labelText;
  inputEl.setAttribute("aria-label", labelText);
  div.append(lab, inputEl);
  return div;
}

function numIn(value, onSet, { optional = false } = {}) {
  const inp = document.createElement("input");
  inp.type = "number";
  inp.step = "any";
  inp.value = value ?? "";
  if (optional) inp.placeholder = "（留空删除）";
  inp.addEventListener("change", () => {
    if (optional && inp.value.trim() === "") { onSet(null); RT.renderAll(); return; }
    const v = parseFloat(inp.value);
    if (!Number.isFinite(v)) { setStatus("数值必须为有限数字"); RT.renderAll(); return; }
    onSet(v);
    RT.renderAll();
  });
  return inp;
}

function textIn(value, onSet, { optional = false } = {}) {
  const inp = document.createElement("input");
  inp.type = "text";
  inp.value = value ?? "";
  if (optional) inp.placeholder = "（留空删除）";
  inp.addEventListener("change", () => {
    if (optional && inp.value.trim() === "") { onSet(null); RT.renderAll(); return; }
    onSet(inp.value.trim());
    RT.renderAll();
  });
  return inp;
}

function selectIn(options, value, onSet) {
  const sel = document.createElement("select");
  options.forEach((o) => {
    const opt = document.createElement("option");
    opt.value = typeof o === "string" ? o : o.value;
    opt.textContent = typeof o === "string" ? o : o.label;
    sel.appendChild(opt);
  });
  sel.value = value;
  sel.addEventListener("change", () => { onSet(sel.value); RT.renderAll(); });
  return sel;
}

function checkIn(value, onSet) {
  const inp = document.createElement("input");
  inp.type = "checkbox";
  inp.checked = !!value;
  inp.addEventListener("change", () => { onSet(inp.checked); RT.renderAll(); });
  return inp;
}

function textAreaIn(value, onSet) {
  const inp = document.createElement("textarea");
  inp.rows = 3;
  inp.value = value;
  inp.placeholder = "每行一条假设";
  inp.addEventListener("change", () => { onSet(inp.value); RT.renderAll(); });
  return inp;
}

function datalistIn(listId, options, value, onSet) {
  const inp = textIn(value, onSet);
  inp.setAttribute("list", listId);
  let dl = document.getElementById(listId);
  if (!dl) {
    dl = document.createElement("datalist");
    dl.id = listId;
    (document.getElementById("genshin-legacy-editor")||document.body).appendChild(dl);
  }
  if (!dl.childElementCount) {
    options.forEach((o) => {
      const opt = document.createElement("option");
      opt.value = o;
      dl.appendChild(opt);
    });
  }
  return inp;
}

function deleteBtn(label, onDelete) {
  const b = document.createElement("button");
  b.className = "danger";
  b.textContent = label;
  b.addEventListener("click", () => { onDelete(); RT.renderAll(); });
  return b;
}

// ---------- 面板渲染 ----------

RT.renderPropPanel = () => {
  const panel = $("#prop-panel");
  panel.innerHTML = "";
  const s = RT.sel();
  panel.classList.toggle("hidden", !s);
  if (!RT.tpl()) { panel.innerHTML = '<div class="hint">无模板</div>'; return; }
  if (!s) { panel.innerHTML = '<div class="hint">点击时间轴上的事件、增益或切人标记进行编辑</div>'; return; }
  if (s.kind === "hit") renderHitPanel(panel, s.index);
  else if (s.kind === "buff") renderBuffPanel(panel, s.index);
  else if (s.kind === "switch") renderSwitchPanel(panel, s.index);
};

function renderHitPanel(panel, i) {
  const h = RT.tpl().hits[i];
  if (!h) { RT.state.selection = null; return; }
  const E = RT.state.enums;
  const metadataSource = () => {const plan=window.PL?.state.plan;return window.PA?.owns(plan,h.id)||plan?.attack_metadata_sources?.find(source=>
    plan.action_event_profiles?.some(profile=>profile.id===source.action_event_profile_id&&profile.prototype_hit_id===h.id));};
  const parameterSource = () => metadataSource() || window.PL?.state.plan?.hit_parameter_sources?.find(s => s.hit_id === h.id);
  const protectParameterField = control => { control.disabled = !!parameterSource(); return control; };
  const parameterLocked = () => { if (!parameterSource()) return false;
    setStatus(metadataSource()?"此原型由数据库攻击来源提供元素和倍率，请先在攻击来源中核对或移除绑定":"此命中已绑定数据库倍率，请先在倍率来源中核对或解除绑定"); return true; };
  panel.appendChild(hdr(`伤害事件 ${i + 1}`));
  panel.appendChild(DS.raw("事件标识与原文", {id:h.id,skill:h.skill,hit_name:h.hit_name,description:h.description}));
  if (RT.tpl().version === 2 && window.EF) {
    const types = UI.el("div", null, "eq-checks");
    const registry = RT.state.enums.engine_v2?.damage_types || [];
    [...registry, ...(RT.tpl().type_definitions?.damage_types || [])].forEach((t, i) => types.append(UI.check(DS.prose(t.label, `伤害类型 ${i + 1}（中文名待补）`),
      (h.damage_type_ids || []).includes(t.id), yes => {
        h.damage_type_ids = yes ? [...new Set([...(h.damage_type_ids || []), t.id])]
          : (h.damage_type_ids || []).filter(id => id !== t.id);
        if (!h.damage_type_ids.length) h.damage_type_ids = null;
        RT.renderAll();
      })));
    panel.appendChild(types);
  }

  panel.appendChild(field("时刻（秒）", numIn(h.t, (v) => { h.t = Math.max(0, v); })));
  panel.appendChild(field("角色", protectParameterField(selectIn(DS.characters(), h.character, (v) => { if (!parameterLocked()) h.character = v; }))));
  panel.appendChild(field("技能", selectIn(DS.options("skills", [...new Set([h.skill,"normal","charged","plunging","skill","burst","additional"])]), h.skill, (v) => { h.skill = v; })));
  panel.appendChild(field("段名", textIn(DS.hasChinese(h.hit_name) ? DS.prose(h.hit_name) : "", (v) => {
    if (v === null) delete h.hit_name; else h.hit_name = v;
  }, { optional: true })));
  panel.appendChild(field("事件说明", textIn(DS.prose(h.description || ""), v => {
    if (v === null) delete h.description; else h.description = v;
  }, {optional:true})));
  panel.appendChild(field("伤害体系", protectParameterField(selectIn(DS.options("damage_systems", E.damage_systems), h.damage_system, (v) => {
    if (parameterLocked()) return;
    h.damage_system = v;
    delete h.reaction_base;
    if (v === "classic") delete h.reaction;
    else h.reaction = { type: E.reaction_types[v][0] };
    if (v !== "transformative") h.multiplier = h.multiplier ?? 1;
  }))));
  const elementControl=selectIn(metadataSource()?[{value:"",label:"由数据库攻击来源编译"}]:DS.options("elements", [...new Set([...E.elements,h.element])]),h.element,(v)=>{if(!metadataSource())h.element=v;else parameterLocked();});
  elementControl.disabled=!!metadataSource();panel.appendChild(field("元素",elementControl));

  const needMult = h.damage_system === "classic"
    || (["lunar", "stellar"].includes(h.damage_system) && !h.reaction_base);
  if (needMult) {
    panel.appendChild(field("倍率依据", protectParameterField(selectIn(
      DS.options("stats", E.scaling_stats),
      h.scaling_stat ?? "atk", (v) => { if (!parameterLocked()) h.scaling_stat = v; },
    ))));
    const multiplier = protectParameterField(numIn(parameterSource() ? null : h.multiplier, (v) => { if (!parameterLocked()) h.multiplier = v; }));
    multiplier.placeholder = parameterSource() ? "由数据库来源编译" : "倍率待填写";
    panel.appendChild(field("倍率", multiplier));
    if (parameterSource()) panel.appendChild(UI.el("p", "倍率由数据库来源提供；原生方案不保存手填数值。角色、体系及倍率属性需先解除来源后修改。", "hint"));
    if(metadataSource()&&(window.AM||window.PA))panel.appendChild(UI.button("查看或编辑数据库攻击来源",()=>{
      const plan=window.PL?.state.plan,source=metadataSource();if(!source)return;
      if(window.PA?.owns(plan,h.id)){
        const index=plan.action_event_profiles.findIndex(profile=>profile.id===source.action_event_profile_id);
        if(index>=0)PA.editProfile(index);
      }else if(window.AM)AM.edit(source.action_event_profile_id);
    }));
    else if (parameterSource() && window.MC) panel.appendChild(UI.button("查看或编辑数据库倍率来源", () => {
      const index = PL.state.plan?.hit_parameter_sources?.findIndex(s => s.hit_id === h.id);
      if (index >= 0) MC.editHitParameterSource(index);
    }));
  }
  if (["lunar", "stellar"].includes(h.damage_system)) {
    panel.appendChild(field("反应本体伤害", checkIn(h.reaction_base, (v) => {
      if (v) { h.reaction_base = true; delete h.multiplier; }
      else { delete h.reaction_base; h.multiplier = h.multiplier ?? 1; }
    })));
  }
  panel.appendChild(field("段数", numIn(h.count ?? "", (v) => {
    if (v === null) delete h.count; else h.count = Math.max(1, Math.round(v));
  }, { optional: true })));
  panel.appendChild(field("快照锁面板", checkIn(h.snapshot, (v) => {
    if (v) { h.snapshot = true; h.cast_t = h.cast_t ?? h.t; } else { delete h.snapshot; delete h.cast_t; }
  })));
  if (h.snapshot) {
    panel.appendChild(field("施放时刻（秒）", numIn(h.cast_t ?? h.t, (v) => { h.cast_t = Math.max(0, v); })));
  }

  // 反应标注
  if (h.damage_system === "classic") {
    panel.appendChild(field("增幅/激化反应", checkIn(!!h.reaction, (v) => {
      if (v) h.reaction = { type: "amplify", factor: 2.0 };
      else delete h.reaction;
    })));
  }
  if (h.reaction) {
    const types = E.reaction_types[h.damage_system];
    panel.appendChild(field("反应类型", selectIn(DS.options("reaction_types", types), h.reaction.type, (v) => {
      h.reaction.type = v;
      if (v === "amplify" && !h.reaction.factor) h.reaction.factor = 2.0;
    })));
    panel.appendChild(field("反应系数", numIn(h.reaction.factor ?? "", (v) => {
      if (v === null) delete h.reaction.factor; else h.reaction.factor = v;
    }, { optional: true })));
    if (h.damage_system === "stellar") {
      panel.appendChild(field("叠层数", numIn(h.reaction.layers ?? "", (v) => {
        if (v === null) delete h.reaction.layers; else h.reaction.layers = Math.max(0, Math.round(v));
      }, { optional: true })));
    }
  }

  panel.appendChild(deleteBtn("删除事件", () => {
    if (parameterLocked()) return;
    RT.tpl().hits.splice(i, 1);
    RT.state.selection = null;
  }));
}

function renderBuffPanel(panel, i) {
  const b = RT.tpl().buffs[i];
  if (!b) { RT.state.selection = null; return; }
  const E = RT.state.enums;
  panel.appendChild(hdr(`增益 ${i + 1}`));
  panel.appendChild(DS.raw("增益标识与原文", {id:b.id,name:b.name,source:b.source,description:b.description}));
  panel.appendChild(field("增益名称", textIn(DS.buff(b), v => { b.name = v; })));
  if(window.PL?.state.plan?.plan_version===3){
    panel.appendChild(field("增益提供者",selectIn([{value:"",label:"待明确"},...DS.characters()],b.extensions?.owner||"",v=>{
      b.extensions??={};b.extensions.owner=v;
    })));
    panel.appendChild(field("来源分项标识",textIn(b.extensions?.source_id||"",v=>{b.extensions??={};b.extensions.source_id=v;})));
    panel.appendChild(UI.el("p","与装备或机制同源时填写同一来源分项标识；独立手工来源须明确命名，不能用新名称掩盖重复收益。","hint"));
  }
  panel.appendChild(field("来源说明", textIn(DS.prose(b.source || ""), (v) => {
    if (v === null) delete b.source; else b.source = v;
  }, { optional: true })));
  panel.appendChild(field("增益说明", textIn(DS.prose(b.description || ""), v => {
    if (v === null) delete b.description; else b.description = v;
  }, {optional:true})));
  panel.appendChild(field("生效范围", selectIn(DS.options("buff_scopes", E.buff_scopes), b.scope ?? "team", (v) => { b.scope = v; })));
  panel.appendChild(field("目标角色", selectIn(
    [{ value: "", label: "（全体/范围决定）" }, ...DS.characters()],
    b.target ?? "",
    (v) => { if (v) b.target = v; else delete b.target; },
  )));
  panel.appendChild(field("开始（秒）", numIn(b.start, (v) => { b.start = Math.max(0, Math.min(b.end - 0.1, v)); })));
  panel.appendChild(field("结束（秒）", numIn(b.end, (v) => { b.end = Math.max(b.start + 0.1, v); })));

  panel.appendChild(hdr("乘区加成"));
  b.effects.forEach((ef, j) => {
    panel.appendChild(field(`收益 ${j+1} 名称`, textIn(DS.hasChinese(ef.name) ? DS.prose(ef.name) : "", v => {
      if(v===null)delete ef.name;else ef.name=v;
    }, {optional:true})));
    panel.appendChild(field(`收益 ${j+1} 说明`, textIn(DS.prose(ef.description||""), v => {
      if(v===null)delete ef.description;else ef.description=v;
    }, {optional:true})));
    panel.appendChild(DS.raw(`收益 ${j+1} 标识与原文`,{id:ef.id,name:ef.name,description:ef.description,zone:ef.zone,stat:ef.stat}));
    if (RT.tpl().version === 2 && window.EF) {
      const card = UI.el("div", null, "eq-part");
      card.append(DS.raw("收益标识", ef.id),
        UI.field("收益类型", UI.select((E.engine_v2?.benefit_types || []).map((t,i) => ({value:t.id,label:DS.prose(t.label,`收益类型 ${i+1}（中文名待补）`)})),
          ef.benefit_type_id, id => { const t = E.engine_v2.benefit_types.find(t => t.id === id);
            Object.assign(ef, {benefit_type_id:id, zone:t.zone, stat:t.stat}); RT.renderAll(); })),
        UI.field("收益值（比例用小数）", UI.input(ef.value, v => {ef.value=v;RT.renderAll();}, true)));
      EF.scope(card, ef, () => RT.renderAll());
      card.append(UI.button("删除收益", () => { if (b.effects.length > 1) { b.effects.splice(j,1);RT.renderAll(); } }));
      panel.appendChild(card); return;
    }
    const row = document.createElement("div");
    row.className = "effect-row";
    const panelZone = (zone) => ["stat_add", "stat_pct"].includes(zone);
    const zones = DS.options("buff_zones", E.buff_zones);
    row.appendChild(selectIn(zones, ef.zone, (v) => {
      ef.zone = v;
      if (panelZone(v) && !["atk", "hp", "def"].includes(ef.stat)) ef.stat = "atk";
      if (!panelZone(v) && ["atk", "hp", "def"].includes(ef.stat)) ef.stat = "all";
    }));
    row.appendChild(panelZone(ef.zone)
      ? selectIn(DS.options("stats", ["atk", "hp", "def"]), ef.stat, (v) => { ef.stat = v; })
      : selectIn(DS.options("elements", [...new Set(["all", ...E.elements,ef.stat])]), ef.stat, (v) => { ef.stat = v; }));
    row.appendChild(numIn(ef.value, (v) => { ef.value = v; }));
    const del = document.createElement("button");
    del.className = "danger mini";
    del.textContent = "×";
    del.addEventListener("click", () => {
      if (b.effects.length === 1) { setStatus("至少保留一个增益效果"); return; }
      b.effects.splice(j, 1); RT.renderAll();
    });
    row.appendChild(del);
    panel.appendChild(row);
  });
  const addEf = document.createElement("button");
  addEf.textContent = "+ 添加效果";
  addEf.className = "mini";
  addEf.addEventListener("click", () => {
    b.effects.push({ zone: "dmg_bonus", stat: "all", value: 0.1 });
    if (RT.tpl().version === 2) Object.assign(b.effects[b.effects.length-1], {
      id:UI.id("effect"),benefit_type_id:"benefit.dmg_bonus.all",applies_to:null});
    RT.renderAll();
  });
  panel.appendChild(addEf);

  panel.appendChild(deleteBtn("删除增益", () => {
    RT.tpl().buffs.splice(i, 1);
    RT.state.selection = null;
  }));
}

function renderSwitchPanel(panel, i) {
  const sw = RT.tpl().switches[i];
  if (!sw) { RT.state.selection = null; return; }
  if(window.PL?.state.plan?.rotation_model){panel.appendChild(hdr("切人动作的派生标记"));
    panel.appendChild(UI.el("p","当前切人轨由动作模型生成。请在“动作与能量”中修改切人动作及初始前台。","hint"));return;}
  panel.appendChild(hdr(`切人标记 #${i}`));
  panel.appendChild(field("时刻（秒）", numIn(sw.t, (v) => { sw.t = Math.max(0, v); })));
  panel.appendChild(field("切换至", selectIn(DS.characters(), sw.character, (v) => { sw.character = v; })));
  panel.appendChild(deleteBtn("删除标记", () => {
    RT.tpl().switches.splice(i, 1);
    RT.state.selection = null;
  }));
}

function hdr(text) {
  const h = document.createElement("h3");
  h.textContent = text;
  return h;
}
