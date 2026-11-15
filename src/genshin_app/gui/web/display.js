"use strict";
// Human-facing labels never replace stable identifiers in a calculation template.
window.DS = {catalogs:new Map(), requests:new Map(), labels:{}};
DS.configure = display => { DS.labels = display?.labels || {}; };
DS.hasChinese = value => /[\u3400-\u9fff]/u.test(String(value ?? ""));
DS.label = (group, value, fallback = "中文名称待补") => {
  if (value == null || value === "") return "—";
  const labels = DS.labels[group];
  return labels && Object.hasOwn(labels, value) ? labels[value] : (DS.hasChinese(value) ? String(value) : fallback);
};
DS.options = (group, values) => (values || []).map((value, i) => ({value,
  label:DS.label(group, value, `选项 ${i + 1}（中文名称待补）`)}));
DS.line = template => template?.meta?.version_line || "release";
// NFKC and separator handling match the database contract. The two case-fold
// expansions below cover names using German sharp s or Greek final sigma.
DS.normalize = value => String(value ?? "").normalize("NFKC").toLowerCase()
  .replace(/ß/g, "ss").replace(/ς/g, "σ").replace(/[\s_-]+/g, "");
DS.ensure = async (line = "release", force = false) => {
  if (!force && DS.catalogs.has(line)) return DS.catalogs.get(line);
  if (!force && DS.requests.has(line)) return DS.requests.get(line);
  if (force) DS.catalogs.delete(line);
  const request = callApi("get_display_catalog", line).then(r => {
    if (DS.requests.get(line) !== request) return;
    if (!r.code && r.data?.version_line === line) DS.catalogs.set(line, r.data);
    DS.requests.delete(line);
    return r.code || r.data?.version_line !== line ? null : r.data;
  });
  DS.requests.set(line, request); return request;
};
DS.character = (id, template = window.RT?.tpl(), plan = window.PL?.state.plan) => {
  const chars = template?.characters || [], index = chars.findIndex(c => c.id === id), c = chars[index];
  if (DS.hasChinese(c?.name)) return DS.prose(c.name, `角色 ${index + 1}（中文名待补）`);
  const records = DS.catalogs.get(DS.line(template))?.characters || [];
  const ref = plan?.manual_template === template ? plan.equipment_inputs?.[id]?.character_ref : null;
  if (ref?.version_line && ref.version_line !== DS.line(template)) {
    return DS.hasChinese(c?.name) ? DS.prose(c.name, `角色 ${index+1}（中文名待补）`)
      : `角色 ${index+1}（资料版本线不符）`;
  }
  const exact = ref?.db_character_id == null ? null : records.find(r => r.id === ref.db_character_id);
  if (exact) return exact.display_name || exact.name;
  if (ref) return `角色 ${index+1}（数据库中文名待补）`;
  const aliases = [c?.name, id].filter(Boolean).map(DS.normalize);
  const matches = records.filter(r => [r.id, r.name, r.display_name, ...(r.aliases || [])]
    .some(a => aliases.includes(DS.normalize(a))));
  if (matches.length === 1) return matches[0].display_name || matches[0].name;
  if (DS.hasChinese(id)) return DS.prose(id, `角色 ${index + 1}（中文名待补）`);
  return `角色 ${index < 0 ? "未定位" : index + 1}（中文名待补）`;
};
DS.characters = (template = window.RT?.tpl(), plan = window.PL?.state.plan) =>
  (template?.characters || []).map(c => ({value:c.id, label:DS.character(c.id, template, plan)}));
DS.prose = (value, fallback = "中文说明待补（可展开查看原文）") => {
  let text = String(value ?? "").replace(/\\n/g, "\n")
    .replace(/(?<![A-Za-z0-9_])rotation\.md(?![A-Za-z0-9_])/g, "排轴手册");
  if (!text) return "";
  const replacements = new Map(), ambiguous = new Set();
  Object.values(DS.labels).forEach(group => Object.entries(group).forEach(([key, label]) => {
    if (key.length <= 1 || !/[A-Za-z]/.test(key) || typeof label !== "string" || ambiguous.has(key)) return;
    if (replacements.has(key) && replacements.get(key) !== label) {
      replacements.delete(key); ambiguous.add(key);
    } else replacements.set(key, label);
  }));
  // Only translate registered complete terms; never infer the meaning of custom IDs.
  [...replacements].sort((a,b) => b[0].length - a[0].length).forEach(([key, label]) => {
    const escaped = key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    text = text.replace(new RegExp(`(?<![A-Za-z0-9_])${escaped}(?![A-Za-z0-9_])`, "g"), label);
  });
  text = text.replace(/\bBuffs?\b/gi, "增益").replace(/\bDPS\b/g, "每秒伤害")
    .replace(/\bHP\b/g, "生命值").replace(/\bLv\.?\s*(\d+)/g, "$1 级");
  const undecorated = text.replace(/\b[BRNC]\d+\b/g, "").replace(/\{[^}]+\}/g, "")
    .replace(/<\/?(?:b|i|u|br|color)(?:\s*\/|=#[0-9a-fA-F]{6,8})?>/g, "")
    .replace(/\b[A-Z]\b/g, "");
  return /[A-Za-z]/.test(undecorated) ? fallback : text;
};
DS.skill = value => DS.label("skills", value, "技能类别待补");
DS.hit = (hit, index, template = window.RT?.tpl()) => {
  const n = index ?? template?.hits?.indexOf(hit) ?? -1;
  return DS.hasChinese(hit?.hit_name) ? DS.prose(hit.hit_name, `伤害事件 ${n + 1}（中文名待补）`)
    : `${DS.skill(hit?.skill)} · 事件 ${n < 0 ? "未定位" : n + 1}`;
};
DS.buff = (buffOrId, template = window.RT?.tpl()) => {
  const buffs = template?.buffs || [], b = typeof buffOrId === "string" ? buffs.find(b => b.id === buffOrId) : buffOrId;
  const index = buffs.indexOf(b), number = index < 0 ? "未定位" : index + 1;
  const source = [b?.name, b?.source].find(DS.hasChinese);
  return source ? DS.prose(source, `增益 ${number}（中文名待补）`)
    : `增益 ${number}${b?.effects?.[0] ? ` · ${DS.label("buff_zones", b.effects[0].zone)}` : "（中文名待补）"}`;
};
DS.effect = (effect, index = 0) => DS.hasChinese(effect?.name) ? DS.prose(effect.name, `收益 ${index+1}（中文名待补）`)
  : `收益 ${index + 1} · ${DS.label("buff_zones", effect?.zone)}`;
DS.raw = (label, value) => { const box = document.createElement("details"); box.className = "display-raw";
  const summary = document.createElement("summary"), pre = document.createElement("pre");
  summary.textContent = `原始数据 · ${label}`; pre.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  box.append(summary, pre); return box;
};
DS.textBlock = (value, fallback) => { const box = document.createElement("div");
  const text = DS.prose(value, fallback), body = document.createElement("div"); body.textContent = text; box.append(body);
  if (text !== String(value ?? "")) box.append(DS.raw("说明原文", value)); return box;
};
DS.setText = (node, value, fallback) => { node.innerHTML = ""; node.appendChild(DS.textBlock(value,fallback)); };
DS.statRows = values => { const box = document.createElement("div");
  if (!values) { box.textContent = "待完善"; return box; }
  Object.entries(values).forEach(([key,value]) => { const row = document.createElement("p");
    row.textContent = `${DS.label("stats", key)}：${typeof value === "number" ? Number(value.toFixed(5)) : "见原始数据"}`; box.append(row); });
  box.append(DS.raw("面板", values)); return box;
};
DS.targetTruth=value=>value===true?"成立":value===false?"不成立":"未判定";
DS.fixedSource=(id,sources=[])=>DS.prose(sources.find(s=>s.id===id)?.name_zh,"固定条件来源（中文名待补）");

// Both timeline workspaces render the same readable explanation. Raw JSON is
// retained separately for diagnosis; these essential facts are always visible.
DS.renderEffectDescription = description => {
  const section=document.createElement("section");section.className="effect-description";
  section.setAttribute("aria-label","效果说明");
  const facts=document.createElement("dl");facts.className="effect-description-facts";
  for(const [label,value] of [["来源",description.source],["效果",description.effects],
    ["持续",description.duration],["数值",description.values]]){
    const dt=document.createElement("dt"),dd=document.createElement("dd");dt.textContent=label;
    for(const text of (Array.isArray(value)?value:[value]).filter(Boolean)){
      const p=document.createElement("p");p.textContent=text;dd.appendChild(p);
    }
    if(!dd.children.length)dd.textContent="当前记录未提供";
    facts.append(dt,dd);
  }
  section.appendChild(facts);
  for(const [label,values] of [["生效条件与取样",description.conditions],
    ["依据与核实状态",description.evidence],["说明",description.notes]]){
    if(!values?.length)continue;
    const heading=document.createElement("h5");heading.textContent=label;section.appendChild(heading);
    for(const text of values){const p=document.createElement("p");p.textContent=text;section.appendChild(p);}
  }
  if(description.samples?.length){
    const details=document.createElement("details"),summary=document.createElement("summary");
    summary.textContent=`关联命中取值示例（${description.samples.length} 条）`;details.appendChild(summary);
    for(const text of description.samples){const p=document.createElement("p");p.textContent=text;details.appendChild(p);}
    section.appendChild(details);
  }
  return section;
};
