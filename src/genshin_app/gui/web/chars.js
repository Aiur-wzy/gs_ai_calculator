// 队伍面板（G2）：meta / 敌人 / 角色面板的内联编辑
"use strict";

RT.renderTeamPanel = () => {
  const panel = $("#team-panel");
  panel.innerHTML = "";
  if (!RT.tpl()) { panel.innerHTML = '<div class="hint">加载或新建模板</div>'; return; }
  const t = RT.tpl();
  const E = RT.state.enums;

  // ---- 方案 meta ----
  panel.appendChild(hdr2("方案"));
  const meta = t.meta || {};
  // Editable values are document data; localized fallbacks belong only in
  // read-only presentation and must never become saved text after another edit.
  panel.appendChild(field("名称", textIn(meta.name ?? "", (v) => {
    if (v === null) { if(t.meta)delete t.meta.name; } else (t.meta??={}).name = v;
  }, { optional: true })));
  panel.appendChild(field("计算假设", textAreaIn((meta.assumptions || []).join("\n"), (v) => {
    const lines = v.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
    if (lines.length) (t.meta??={}).assumptions = lines; else if(t.meta)delete t.meta.assumptions;
  })));
  panel.appendChild(DS.raw("方案名称与计算假设", {name:meta.name, assumptions:meta.assumptions}));
  panel.appendChild(field("版本线", selectIn(DS.options("version_lines", ["release", "beta"]), meta.version_line ?? "release",
    (v) => { (t.meta??={}).version_line = v; DS.ensure(v).then(() => RT.renderAll()); })));
  panel.appendChild(field("循环时长（秒）", numIn(t.loop_duration ?? "", (v) => {
    if (v === null) delete t.loop_duration;
    else if (v <= 0) setStatus("循环时长必须为正数");
    else t.loop_duration = v;
  }, { optional: true })));

  // ---- 敌人 ----
  panel.appendChild(hdr2("敌人"));
  panel.appendChild(field("等级", numIn(t.enemy.level, (v) => { t.enemy.level = Math.max(1, Math.round(v)); })));
  [["defense_reduction", "敌人减防"], ["defense_ignore", "无视防御"]].forEach(([key, label]) => {
    panel.appendChild(field(label, numIn(t.enemy[key] ?? "", (v) => {
      if (v === null) delete t.enemy[key]; else t.enemy[key] = v;
    }, { optional: true })));
  });
  const resistance=t.enemy.resistance || { default: 0.1 };
  renderDictRows(panel, resistance, ["default", ...E.elements], "抗性",()=>{t.enemy.resistance=resistance;});

  // ---- 角色 ----
  panel.appendChild(hdr2("角色"));
  t.characters.forEach((c, i) => {
    const box = document.createElement("div");
    box.className = "char-box";
    const head = document.createElement("div");
    head.className = "char-head";
    const dot = document.createElement("span");
    dot.className = "char-dot";
    dot.style.background = RT.charColor(c.id);
    const name = textIn(c.name ?? "", (v) => {
      if (!v || !DS.hasChinese(v)) { setStatus("请填写角色中文名称"); return; }
      c.name = v;
    });
    name.setAttribute("aria-label", `角色 ${i + 1} 中文名称`);
    name.className = "char-id";
    head.append(dot, name);
    const del = document.createElement("button");
    del.className = "danger mini";
    del.textContent = "删";
    del.addEventListener("click", () => {
      const used = t.hits.some((h) => h.character === c.id)
        || (t.switches || []).some((s) => s.character === c.id)
        || (t.buffs || []).some((b) => b.target === c.id);
      if (used) { setStatus(`角色 ${DS.character(c.id)} 仍被事件、标记或增益引用，无法删除`); return; }
      if (t.characters.length <= 1) { setStatus("至少保留一个角色"); return; }
      t.characters.splice(i, 1);
      RT.renderAll();
    });
    head.appendChild(del);
    box.appendChild(head);
    box.appendChild(DS.raw("角色标识与原名称", {id:c.id, name:c.name, description:c.description}));
    box.appendChild(field("角色说明", textIn(c.description ?? "", v => {
      if (v === null) delete c.description; else c.description = v;
    }, {optional:true})));

    if (window.PL) box.appendChild(UI.button("装备与面板", () => PL.begin(c.id), "mini"));
    const managed = window.PL?.state.plan?.equipment_inputs?.[c.id];
    if(window.EQ?.summaryCard)box.appendChild(EQ.summaryCard(EQ.summaryCharacter(c.id),"当前装备",false));
    if (managed) {
      del.disabled = true;
      if(managed.mode==="panel"){
        box.appendChild(hdr3("初始面板"));
        EQ.renderPanel(box,c,managed,()=>{RT.syncRevision();RT.renderAll();});
        panel.appendChild(box);return;
      }
      box.appendChild(UI.el("p", "装备管理：最终面板由明确输入生成。请到装备编辑中调整。", "hint"));
      box.appendChild(DS.statRows(PL.state.plan.resolved_template?.characters?.find(item => item.id === c.id)?.stats));
      panel.appendChild(box);
      return;
    }
    box.appendChild(field("等级", numIn(c.level, (v) => { c.level = Math.max(1, Math.round(v)); })));
    const s = c.stats;
    box.appendChild(field("攻击力", numIn(s.atk, (v) => { s.atk = v; })));
    [["hp", "生命值"], ["def", "防御力"], ["base_atk", "基础攻击力"],
     ["base_hp", "基础生命值"], ["base_def", "基础防御力"], ["energy_recharge", "元素充能效率（1 为 100%）"]].forEach(([key, label]) => {
      box.appendChild(field(label, numIn(s[key] ?? "", (v) => {
        if (v === null) delete s[key]; else s[key] = v;
      }, { optional: true })));
    });
    box.appendChild(field("元素精通", numIn(s.em ?? 0, (v) => { s.em = v; })));
    box.appendChild(field("暴击率", numIn(s.crit_rate ?? 0, (v) => { s.crit_rate = v; })));
    box.appendChild(field("暴击伤害", numIn(s.crit_dmg ?? 0, (v) => { s.crit_dmg = v; })));
    const damageBonus=s.dmg_bonus || {};
    box.appendChild(hdr3("元素伤害加成"));
    renderDictRows(box, damageBonus, ["all", ...E.elements], "加成",()=>{s.dmg_bonus=damageBonus;});
    panel.appendChild(box);
  });

  const add = document.createElement("button");
  add.className = "mini";
  add.textContent = "+ 添加角色";
  add.addEventListener("click", () => {
    let n = t.characters.length + 1;
    while (RT.charIds().includes(`char${n}`)) n += 1;
    t.characters.push({
      id: `char${n}`, name:`新角色 ${n}`, level: 90,
      stats: { atk: 2000, em: 0, crit_rate: 0.05, crit_dmg: 0.5, dmg_bonus: {} },
    });
    RT.renderAll();
  });
  panel.appendChild(add);
};

// 数值字典行编辑（抗性 / dmg_bonus 共用）
function renderDictRows(container, dict, keyOptions, label, onChange=()=>{}) {
  Object.entries(dict).forEach(([k, v]) => {
    const row = document.createElement("div");
    row.className = "effect-row";
    row.appendChild(selectIn(DS.options("elements", [...new Set([...keyOptions,k])]), k, (nv) => {
      if (!nv || nv === k) return;
      if (nv in dict) { setStatus("该属性已经存在"); RT.renderAll(); return; }
      dict[nv] = dict[k];
      delete dict[k];
      onChange();
    }));
    row.appendChild(numIn(v, (nv) => { dict[k] = nv; onChange(); }));
    const del = document.createElement("button");
    del.className = "danger mini";
    del.textContent = "×";
    del.addEventListener("click", () => { delete dict[k]; onChange(); RT.renderAll(); });
    row.appendChild(del);
    container.appendChild(row);
  });
  const add = document.createElement("button");
  add.className = "mini";
  add.textContent = `+ 添加${label}`;
  add.addEventListener("click", () => {
    const k = keyOptions.find((o) => !(o in dict));
    if (!k) { setStatus("键已用尽"); return; }
    dict[k] = 0;
    onChange();
    RT.renderAll();
  });
  container.appendChild(add);
}

function hdr2(text) {
  const h = document.createElement("h3");
  h.className = "side-hdr";
  h.textContent = text;
  return h;
}

function hdr3(text) {
  const h = document.createElement("div");
  h.className = "sub-hdr";
  h.textContent = text;
  return h;
}
