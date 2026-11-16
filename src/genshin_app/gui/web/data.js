// 数据浏览与追加描述（G4）：角色列表/详情/倍率 + annotation 全状态只读查看（ADR-0017）
"use strict";

window.DV = {
  // 元素配色独立于 RT（排轴模板可能未加载，不能依赖其状态）
  ELEMENT_COLOR: { 火: "#e06c75", 水: "#5b8cff", 雷: "#c678dd", 冰: "#56b6c2",
                   风: "#61af72", 岩: "#d19a66", 草: "#98c379" },
  WEAPON_NAME: new Map([["WEAPON_SWORD_ONE_HAND", "单手剑"], ["WEAPON_CLAYMORE", "双手剑"],
    ["WEAPON_POLE", "长柄武器"], ["WEAPON_BOW", "弓"], ["WEAPON_CATALYST", "法器"]]),

  state: {
    versionLine: "release",
    versionLines: ["release"],
    chars: [], total: 0,
    detail: null,          // 当前角色聚合详情
    annTarget: null,       // annotation 面板当前目标 {target_type, target_id, label}
    anns: [], annPage:1, annTotal:0, annStatus:"all", annManager:false,
    updateRequest: 0,
    databaseUpdate: null,
    databaseUpdateRunning: false,
  },
};

DV.el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
};

// ---------- 数据拉取 ----------

DV.refreshList = async () => {
  const request = DV.state.listRequest = (DV.state.listRequest || 0) + 1;
  const versionLine = DV.state.versionLine;
  const q = $("#data-q").value.trim();
  const element = $("#data-element").value;
  const rarity = parseInt($("#data-rarity").value, 10) || 0;
  // 分页拉全：页面上限 MAX_PAGE_SIZE=100，角色总数可超一页，逐页累加直到取完
  const fetchPage = (p) => callApi("list_characters",
    q, element, rarity, DV.state.versionLine, p, 100);
  const first = await fetchPage(1);
  if (request !== DV.state.listRequest || versionLine !== DV.state.versionLine) return;
  if (first.code !== 0) { setStatus(first.message); return; }
  let items = first.data.items;
  const total = first.data.total;
  for (let p = 2; items.length < total; p++) {
    const r = await fetchPage(p);
    if (request !== DV.state.listRequest || versionLine !== DV.state.versionLine) return;
    if (r.code !== 0 || !r.data.items.length) { setStatus(r.message || "分页加载中断"); break; }
    items = items.concat(r.data.items);
  }
  DV.state.chars = items;
  DV.state.total = total;
  DV.renderList();
  DV.renderToolbarState();
};

DV.openCharacter = async (charId) => {
  const request = DV.state.detailRequest = (DV.state.detailRequest || 0) + 1;
  const line = DV.state.versionLine;
  const r = await callApi("character_detail", charId, DV.state.versionLine);
  if (request !== DV.state.detailRequest || line !== DV.state.versionLine) return;
  if (r.code !== 0) { setStatus(r.message); return; }
  DV.state.detail = r.data;
  const c = r.data.character;
  DV.state.annTarget = { target_type: "character", target_id: c.id, label: c.name };
  DV.state.annPage=1;DV.state.annManager=false;
  DV.renderDetail();
  $("#data-detail").scrollTop = 0;
  await DV.refreshAnnotations();
};

DV.refreshAnnotations = async () => {
  const t = DV.state.annTarget;
  if (!t) return;
  const request=DV.state.annRequest=(DV.state.annRequest||0)+1;
  const r = await callApi("list_annotations",
    t.target_type, t.target_id, DV.state.annManager?DV.state.annStatus:"all", DV.state.annManager?"":DV.state.versionLine,DV.state.annPage||1,100);
  if (t !== DV.state.annTarget || request!==DV.state.annRequest) return;
  if (r.code !== 0) { setStatus(r.message); return; }
  DV.state.anns = r.data.items;
  DV.state.annTotal=r.data.total;
  DV.renderAnnotations();
};

// ---------- 左栏：角色列表 ----------

DV.renderList = () => {
  const box = $("#data-list");
  box.innerHTML = "";
  const hint = DV.state.chars.length === DV.state.total
    ? `共 ${DV.state.total} 个角色（${DS.label("version_lines",DV.state.versionLine)}）`
    : `已加载 ${DV.state.chars.length}/${DV.state.total} 个角色（${DS.label("version_lines",DV.state.versionLine)}，分页中断）`;
  box.appendChild(DV.el("div", "hint", hint));
  DV.state.chars.forEach((c) => {
    const item = DV.el("div", "char-item");
    if (DV.state.detail && DV.state.detail.character.id === c.id) item.classList.add("active");
    const dot = DV.el("span", "char-dot");
    dot.style.background = DV.ELEMENT_COLOR[c.element] || "#888";
    item.appendChild(dot);
    item.appendChild(DV.el("span", null, `${c.name}`));
    item.appendChild(DV.el("span", "muted", ` ${c.element} ${"★".repeat(c.rarity)}`));
    item.addEventListener("click", () => DV.openCharacter(c.id));
    box.appendChild(item);
  });
};

// ---------- 右栏：详情 ----------

DV.statRow = (label, value) => {
  const row = DV.el("div", "fld");
  row.appendChild(DV.el("label", null, label));
  row.appendChild(DV.el("span", null, value));
  return row;
};

DV.renderDetail = () => {
  const box = $("#data-detail");
  box.innerHTML = "";
  const d = DV.state.detail;
  if (!d) { box.innerHTML = '<div class="hint">从左侧选择角色</div>'; return; }
  const c = d.character;

  box.appendChild(DV.el("h3", "side-hdr",
    `${DS.prose(c.name,"角色中文名待补")}（${c.element} · ${"★".repeat(c.rarity)} · ${DV.WEAPON_NAME.get(c.weapon_type) || "武器类型待补"}）`));

  box.appendChild(DS.raw("角色资料标识", {id:c.id,weapon_type:c.weapon_type}));
  // 面板
  box.appendChild(DV.el("div", "sub-hdr", "面板"));
  const grid = DV.el("div", "stat-grid");
  [["生命值", c.hp_90, c.hp_100], ["攻击", c.atk_90, c.atk_100], ["防御", c.def_90, c.def_100]]
    .forEach(([label, v90, v100]) => {
      const cell = DV.el("div", "stat-cell");
      cell.appendChild(DV.el("div", "stat-label", label));
      cell.appendChild(DV.el("div", null, `90级 ${Math.round(v90)}`));
      if (v100 != null) cell.appendChild(DV.el("div", "muted", `100级 ${Math.round(v100)}`));
      grid.appendChild(cell);
    });
  const asc = DV.el("div", "stat-cell");
  asc.appendChild(DV.el("div", "stat-label", "突破属性"));
  asc.appendChild(DV.el("div", null, `${c.ascension_prop || "—"} ${DF.ascension(c.ascension_prop, c.ascension_value)}`));
  if (c.ascension_value != null) {
    const original = DV.el("details", "data-ascension-source");
    original.append(DV.el("summary", null, "原始值"), DV.el("span", null, String(c.ascension_value)));
    asc.appendChild(original);
  }
  grid.appendChild(asc);
  box.appendChild(grid);

  const skills = d.skills || [], talents = d.talents || [], extra = d.extra_info || [];
  const section = (title, records, render) => {
    const group = DV.el("section", "data-section");
    group.appendChild(DV.el("h3", "sub-hdr", `${title}（${records.length}）`));
    if (!records.length) group.appendChild(DV.el("div", "hint", "暂无记录"));
    records.forEach((record) => group.appendChild(render(record)));
    box.appendChild(group);
  };
  section("战斗技能", skills, DV.renderSkill);
  section("附属技能", extra.filter((e) => e.info_type === "sub_skill"), DV.renderExtra);
  section("固有天赋", extra.filter((e) => e.info_type === "inherent_skill"), DV.renderExtra);
  section("命座", talents, (t) => DV.renderRecord(t, `${t.level}命 · ${t.name}`));
  const other = extra.filter((e) => !["sub_skill", "inherent_skill"].includes(e.info_type));
  if (other.length) section("其他补充", other, DV.renderExtra);

  // annotation 面板
  box.appendChild(DV.el("h3", "sub-hdr", "追加机制说明"));
  const targetBar = DV.el("div", "fld");
  targetBar.appendChild(DV.el("label", null, "目标"));
  const sel = document.createElement("select");
  const targets = [
    { target_type: "character", target_id: c.id, label: c.name },
    ...skills.map((s) => ({
      target_type: "skill", target_id: s.id, label: `技能·${s.name}`,
    })),
    ...talents.map((t) => ({
      target_type: "talent", target_id: t.id, label: `命座·${t.name}`,
    })),
  ];
  extra.forEach((e) => {
    const label = e.name || (e.info_type === "inherent_skill" ? "固有天赋" : "附属技能");
    const addTarget = (type, id, title) => {
      if (Number.isSafeInteger(id) && id > 0 && !targets.some((t) =>
        t.target_type === type && t.target_id === id)) {
        targets.push({target_type: type, target_id: id, label: title});
      }
    };
    if (e.info_type === "sub_skill") {
      addTarget("skill", e.source_id, `附属技能·${label}`);
      addTarget("skill_param", e.proud_skill_group_id, `参数·${label}`);
    } else if (e.info_type === "inherent_skill") {
      addTarget("inherent_skill", e.proud_skill_group_id ?? e.source_id, `固有天赋·${label}`);
    }
  });
  targets.forEach((t) => {
    const o = document.createElement("option");
    o.value = `${t.target_type}:${t.target_id}`;
    o.textContent = t.label;
    if (DV.state.annTarget && DV.state.annTarget.target_id === t.target_id &&
        DV.state.annTarget.target_type === t.target_type) o.selected = true;
    sel.appendChild(o);
  });
  sel.addEventListener("change", () => {
    const [tt, tid] = sel.value.split(":");
    DV.state.annTarget = { target_type: tt, target_id: parseInt(tid, 10),
                           label: sel.selectedOptions[0].textContent };
    DV.refreshAnnotations();
  });
  targetBar.appendChild(sel);
  box.appendChild(targetBar);
  const annBox = DV.el("div");
  annBox.id = "annotation-panel";
  box.appendChild(annBox);
};

DV.renderSkill = (s) => {
  const slot = DS.skill(s.slot);
  const tags = [];
  if (s.cooldown != null) tags.push(`冷却 ${s.cooldown} 秒`);
  if (s.energy_cost != null) tags.push(`元素能量 ${s.energy_cost}`);
  return DV.renderRecord(s, `${slot} · ${s.name}`, tags);
};

DV.renderExtra = (e) => DV.renderRecord(e,
  e.name || ({sub_skill: "附属技能", inherent_skill: "固有天赋"})[e.info_type] || e.info_type);

// 原文只经 textContent / 白名单 DOM 节点渲染，不将上游内容传给 innerHTML。
DV.richText = (text, className = "data-description") => {
  const root = DV.el("div", className);
  const stack = [{tag: null, node: root}];
  DF.tokens(text).forEach((token) => {
    const parent = stack[stack.length - 1].node;
    if (token.type === "text") parent.appendChild(DV.el("span", null, token.value));
    else if (token.type === "open") {
      const node = DV.el(({b: "strong", i: "em", u: "u"})[token.tag] || "span");
      if (token.tag === "color") node.style.color = token.color;
      if (token.tag === "link") node.className = "game-term";
      parent.appendChild(node);
      stack.push({tag: token.tag, node});
    } else if (stack.length > 1 && stack[stack.length - 1].tag === token.tag) stack.pop();
    else parent.appendChild(DV.el("span", null, token.raw));
  });
  return root;
};

DV.DESC_STATUS = {
  present: "上游描述已收录",
  source_empty: "上游未提供这段描述",
  textmap_missing: "描述的文本映射缺失，当前无法显示正文",
  legacy_unverified: "旧版数据尚未核验描述来源",
};

DV.renderSource = (record, original) => {
  const details = DV.el("details", "data-source");
  details.appendChild(DV.el("summary", null, "原文与来源"));
  const status = record.desc_status || "legacy_unverified";
  details.appendChild(DV.el("div", "hint", DV.DESC_STATUS[status] || status));
  const source = record.desc_source || {};
  const fields = [["来源表", source.table], ["条目 ID", source.record_id],
    ["文本 Hash", source.text_map_hash], ["语言", source.language],
    ["上游提交", source.upstream_commit]];
  const meta = DV.el("dl", "data-source-fields");
  fields.forEach(([label, value]) => {
    meta.append(DV.el("dt", null, label), DV.el("dd", null, value == null ? "未记录" : String(value)));
  });
  details.appendChild(meta);
  details.appendChild(DV.el("pre", "data-original", original || "（没有描述原文）"));
  return details;
};

DV.renderParameters = (params, level, forceRaw = false) => {
  const values = params.param_list || [];
  const descriptions = (params.param_desc || []).filter((text) => typeof text === "string" && text.trim());
  if (!values.length && !descriptions.length) return null;
  const block = DV.el("details", `data-parameters${descriptions.length ? "" : " data-raw-params"}`);
  block.appendChild(DV.el("summary", null, (level ? `${level} 级 ` : "")
    + (descriptions.length ? "参数" : `原始参数（${values.length} 个槽位）`)));
  block.open = descriptions.length > 0 || forceRaw;
  let unresolved = false;
  descriptions.forEach((template) => {
    const rendered = DF.format(template, values);
    unresolved ||= rendered.unresolved.length > 0;
    const row = DV.el("div", "data-param-row");
    const divider = rendered.text.indexOf("|");
    if (divider >= 0) {
      row.append(DV.richText(rendered.text.slice(0, divider), "data-param-label"),
        DV.richText(rendered.text.slice(divider + 1), "data-param-value"));
    } else row.appendChild(DV.richText(rendered.text, "data-param-label"));
    block.appendChild(row);
  });
  if (unresolved) block.appendChild(DV.el("div", "data-notice", "部分格式未解析，已保留原文和编号参数。"));
  const raw = descriptions.length ? DV.el("details", "data-raw-params") : block;
  if (descriptions.length) {
    raw.open = unresolved || forceRaw;
    raw.appendChild(DV.el("summary", null, `原始参数（${values.length} 个槽位）`));
  }
  const table = DV.el("table", "data-param-table");
  for (let index = 0; index < values.length; index++) {
    const row = DV.el("tr");
    row.append(DV.el("th", null, `param${index + 1}`), DV.el("td", null, DF.rawValue(values[index])));
    table.appendChild(row);
  }
  raw.appendChild(table);
  if (raw !== block) block.appendChild(raw);
  return block;
};

DV.renderRecord = (record, title, tags = []) => {
  const card = DV.el("article", "skill-item data-record");
  const head = DV.el("div", "skill-head");
  head.appendChild(DV.el("h4", "data-record-title", DS.prose(title,"资料中文名称待补")));
  if (tags.length) head.appendChild(DV.el("span", "muted", tags.join(" · ")));
  card.appendChild(head);
  const variants = record.description_variants || [];
  const levels = [...new Set([...Object.keys(record.params || {}),
    ...variants.filter((v) => v.level != null).map((v) => String(v.level))])].sort((a, b) => a - b);
  let level = levels.includes("10") ? "10" : levels[0];
  const body = DV.el("div", "data-record-body");
  const renderBody = () => {
    body.innerHTML = "";
    const variant = variants.find((v) => String(v.level) === level);
    const selected = variant ? {...record, ...variant} : record;
    const params = record.params?.[level] || {param_list: record.param_list || []};
    const legacyContent = !selected.desc_template
      && (!selected.desc_status || selected.desc_status === "legacy_unverified") && selected.content;
    const original = legacyContent || selected.desc_template || "";
    const rendered = legacyContent ? {text: original, unresolved: []}
      : DF.format(original, params.param_list || []);
    if (original) body.appendChild(DV.richText(DS.prose(rendered.text)));
    else body.appendChild(DV.el("div", "data-notice",
      DV.DESC_STATUS[selected.desc_status] || "当前记录没有描述正文，可查看来源信息。"));
    if (rendered.unresolved.length) body.appendChild(DV.el("div", "data-notice",
      "部分占位符暂未解析，请结合原文与编号参数查看。"));
    const parameters = DV.renderParameters(params, level, rendered.unresolved.length > 0);
    if (parameters) body.appendChild(parameters);
    body.appendChild(DV.renderSource(selected, original));
    if (record.content) {
      const full = DV.el("details", "data-source data-extra-full");
      full.append(DV.el("summary", null, "完整补充记录"), DV.richText(record.content));
      body.appendChild(full);
    }
  };
  if (levels.length) {
    const field = DV.el("label", "data-level-control", "技能等级 ");
    const select = DV.el("select");
    select.setAttribute("aria-label", `${title} · 技能等级`);
    levels.forEach((value) => {
      const option = DV.el("option", null, `${value} 级`);
      option.value = value;
      select.appendChild(option);
    });
    select.value = level;
    select.addEventListener("change", () => { level = select.value; renderBody(); });
    field.appendChild(select);
    card.appendChild(field);
  }
  card.appendChild(body);
  renderBody();
  return card;
};

// ---------- annotation 面板 ----------

DV.STATUS_LABEL = { draft: "草稿", pending: "待审", approved: "已审",
                    rejected: "已拒", orphan: "孤儿" };

DV.renderAnnotations = () => {
  const box = $("#annotation-panel");
  if (!box) return;
  box.innerHTML = "";
  const anns = DV.state.anns;
  box.appendChild(DV.el("div", "hint",
    `${DV.state.annTarget.label}：共 ${DV.state.annTotal??anns.length} 条 · 第 ${DV.state.annPage||1} 页（只读）`));
  box.appendChild(DV.el("div", "data-notice",
    "当前数据库内容只读。新增或修订资料需要整理 source 和完整变更内容，经用户同意后随完整新数据库版本发布。"));
  if(DV.state.annManager){box.append(UI.field("目标类型",UI.select([{value:"",label:"全部目标"},...DS.options("annotation_target_types",window.RT?.state.enums?.annotation_target_types||DV.ANN_TARGETS)],DV.state.annTarget.target_type,v=>{
    DV.state.annTarget.target_type=v;DV.state.annPage=1;DV.refreshAnnotations();})),
    UI.field("状态",UI.select([{value:"all",label:"全部状态"},...Object.entries(DV.STATUS_LABEL).map(([value,label])=>({value,label}))],DV.state.annStatus,v=>{
      DV.state.annStatus=v;DV.state.annPage=1;DV.refreshAnnotations();})));}
  anns.forEach((a) => box.appendChild(DV.renderAnnotationItem(a)));
  const prev=UI.button("上一页",()=>{DV.state.annPage--;DV.refreshAnnotations();}),next=UI.button("下一页",()=>{DV.state.annPage++;DV.refreshAnnotations();});
  prev.disabled=(DV.state.annPage||1)<=1;next.disabled=(DV.state.annPage||1)*100>=DV.state.annTotal;box.append(prev,next);
};

DV.ANN_TARGETS=["character","skill","talent","skill_param","inherent_skill","weapon","artifact_set","artifact","equip_affix"];
DV.openAnnotationManager=()=>{DV.state.annManager=true;DV.state.annPage=1;
  DV.state.annTarget={target_type:"",target_id:0,label:"全库追加机制（含孤立记录）"};
  const detail=$("#data-detail");detail.innerHTML="";const box=UI.el("div");box.id="annotation-panel";detail.append(box);DV.refreshAnnotations();};

DV.renderAnnotationItem = (a) => {
  const item = DV.el("div", "ann-item");
  item.tabIndex=0;
  const head = DV.el("div", "ann-head");
  head.appendChild(DV.el("span", `ann-status st-${a.status}`,
    DV.STATUS_LABEL[a.status] || DS.label("states",a.status)));
  head.appendChild(DV.el("b", null, DS.prose(a.title || "（无标题）")));
  if (a.category) head.appendChild(DV.el("span", "muted", ` ${DS.label("annotation_categories",a.category,"分类中文名待补")}`));
  item.appendChild(head);
  item.appendChild(DS.textBlock(a.content));
  item.appendChild(DS.raw("追加说明",a));
  item.appendChild(DV.el("div", "muted ann-time", `#${a.id} · ${DS.label("annotation_target_types",a.target_type)} ${a.target_id} · ${a.version_line?DS.label("version_lines",a.version_line):"跨版本共享"} · 更新于 ${a.updated_at}`));
  return item;
};

DV.renderToolbarState=()=>{
  const chip=$("#data-filter-summary");if(!chip)return;
  const selector=$("#data-category"),category=selector?.selectedOptions?.[0]?.textContent||selector?.value||"资料",filters=[];
  if(DV.state.versionLine)filters.push(DS.label("version_lines",DV.state.versionLine));
  if(!window.ED||ED.category==="character"){
    if($("#data-element")?.value)filters.push($("#data-element").value);
    if($("#data-rarity")?.value)filters.push(`${$("#data-rarity").value} 星`);
  }else{
    if(ED.filters?.scope&&ED.filters.scope!=="codex")filters.push("全部源配置");
    if(ED.filters?.rarity)filters.push(`${ED.filters.rarity} 星`);
    if(ED.filters?.type)filters.push(ED.slots?.[ED.filters.type]||DV.WEAPON_NAME.get(ED.filters.type)||"已筛选类型");
  }
  chip.textContent=`${category} · ${filters.join(" · ")||"默认范围"}`;
};

DV.renderAnnotationForm = (a) => {
  const notice = DV.el("div", "data-notice");
  notice.appendChild(DV.el("div", "sub-hdr", "追加资料当前只读"));
  notice.appendChild(DV.el("div", null,
    "请将修订整理为带 source 和完整内容的库外提案。用户同意后，它才会随完整新数据库版本发布。"));
  if (a) notice.appendChild(DS.raw("当前追加资料", a));
  return notice;
};

// ---------- 初始化 ----------

DV.loadOverview = async () => {
  const r = await callApi("db_overview");
  if (r.code !== 0) {
    $("#data-list").innerHTML = "";
    $("#data-list").appendChild(DS.textBlock(r.message));
    return;
  }
  const vl = r.data.meta.version_lines || ["release"];
  DV.state.versionLines = vl;
  const vlSel = $("#data-version-line");
  vlSel.innerHTML = "";
  vl.forEach((v) => {
    const o = document.createElement("option");
    o.value = v; o.textContent = DS.label("version_lines",v);
    vlSel.appendChild(o);
  });
  if (!vl.includes(DV.state.versionLine)) DV.state.versionLine = vl[0] || "release";
  vlSel.value = DV.state.versionLine;
  DV.renderToolbarState();
  await DS.ensure(DV.state.versionLine);
  await DV.refreshList();
  DV.renderDetail();
};

DV.checkDatabaseUpdate = async () => {
  if (DV.state.databaseUpdateRunning) return;
  const request = ++DV.state.updateRequest;
  const r = await callApi("check_database_update");
  if (request !== DV.state.updateRequest) return;
  const button = $("#data-update"), details = $("#db-version-details");
  const banner = $("#update-banner");
  if (banner) {
    banner.replaceChildren();
    banner.classList.add("hidden");
  }
  if (r?.code !== 0) {
    DV.state.databaseUpdate = null;
    button.disabled = true;
    DS.setText($("#db-update-status"), r?.message || "暂时无法检查应用和数据库更新。");
    DS.setText(details, "版本检查未完成。当前应用和数据库未发生改变。");
    window.WS?.refresh?.();
    return;
  }
  const data = r.data || {};
  DV.state.databaseUpdate = data;
  const product = Array.isArray(data.update_options);
  button.disabled = data.update_action_available !== true;
  if (product) button.textContent = "查看更新选项";
  DS.setText($("#db-update-status"), data.message || (!data.initialized
    ? "数据库尚未安装。安装后即可查询资料和计算。"
    : data.available ? "发现应用与数据库联合更新" : "当前版本没有可用更新"));
  const mode = data.pair_policy === "exact" ? "正式版精确配对" : "开发版当前组合";
  const actual = [data.actual_database_game_version, data.actual_database_upstream_commit]
    .filter(Boolean).join(" · ") || "无法从当前数据库读取";
  const rows = [
    `模式：${mode}`,
    `App：${data.current_app_version || "未知"}（${data.current_app_release_id || "未声明"}）`,
    `当前组合：${data.current_release_pair_id || "未声明"}`,
    `当前数据库：${data.current_database_release_id || "未声明"}`,
    `实际数据库：${actual}`,
    `启动匹配状态：${data.pair_status || "未检查"}`,
  ];
  if (data.authoritative && data.remote_release_pair_id) {
    if (data.remote_app_release_id) rows.push(`远端应用：${data.remote_app_release_id}`);
    rows.push(`可验证远端版本对：${data.remote_release_pair_id}`);
    if (data.remote_database_release_id) rows.push(`远端数据库：${data.remote_database_release_id}`);
    if (data.remote_game_version) rows.push(`远端游戏数据版本：${data.remote_game_version}`);
    const sizes=data.artifacts||{},formatSize=value=>{
      if(!Number.isFinite(value)||value<=0)return "未知";
      const units=["B","KiB","MiB","GiB"];let size=value,index=0;
      while(size>=1024&&index<units.length-1){size/=1024;index++;}
      return `${size.toFixed(index===0?0:1)} ${units[index]}`;
    };
    if(!product && sizes.app?.size_bytes)rows.push(`App 下载：${formatSize(sizes.app.size_bytes)}`);
    if(!product && sizes.database?.size_bytes)rows.push(`数据库下载：${formatSize(sizes.database.size_bytes)}`);
  }
  rows.push(data.coordinator_available ? "安装方式：保存当前工作后，按确认的选项更新并重启"
    : "安装方式：当前未找到可用更新器，仅显示版本信息");
  DS.setText(details, rows.join("\n"));
  if (banner) {
    const setup=data.local_database_setup_available===true;
    const showOnline=product&&data.update_action_available===true;
    if (setup||showOnline) {
      banner.classList.remove("hidden");
      banner.append(UI.el("span", setup ? "安装数据库后即可查询资料和计算。" : "发现新的应用或数据库版本。"));
      if(showOnline)banner.append(UI.button(setup?"下载数据库":"查看更新选项", () => DV.updateDatabase()));
      if(setup)banner.append(UI.button("选择本地发行包", () => DV.chooseLocalDatabase()));
      else banner.append(UI.button("稍后", () => banner.classList.add("hidden")));
    }
  }
  window.WS?.refresh?.();
};

DV.chooseLocalDatabase = async () => {
  if(DV.state.databaseUpdateRunning)return;
  const picked=await callApi("choose_local_database_release");
  if(picked.code){setStatus(picked.message);return;}
  const option=picked.data;
  if(option.cancelled)return;
  const dialog=window.WS?.dialog?.("安装本地数据库", `即将校验并安装 ${option.database_release_id}。请保存当前工作，安装后需重新打开应用。`);
  dialog?.setActions(UI.button("取消",()=>dialog.finish("cancelled")),
    UI.button("安装",()=>{if(dialog.finish("confirmed")!==false)DV.updateDatabase(option);},"primary"));
};

DV.updateDatabase = async (selection) => {
  if(DV.state.databaseUpdateRunning){
    const request = DV.state.updateRequest;
    const cancelled=await callApi("cancel_database_update");
    if (request !== DV.state.updateRequest || !DV.state.databaseUpdateRunning) return;
    const message = cancelled.code ? cancelled.message : cancelled.data?.cancel_requested === false
      ? "正在完成安装和校验，此时无法取消。" : "正在取消更新下载…";
    DS.setText($("#db-update-status"), message);
    return;
  }
  const local=selection?.local_package===true&&selection?.mode==="database_setup";
  if (!local&&DV.state.databaseUpdate?.update_action_available !== true) {
    DS.setText($("#db-update-status"), DV.state.databaseUpdate?.message
      || "当前没有可执行的应用与数据库联合更新");
    $("#data-update").disabled = true;
    window.WS?.refresh?.();
    return;
  }
  const candidate=DV.state.databaseUpdate||{}, options=candidate.update_options;
  const product=local||Array.isArray(options);
  const mib=value=>Number.isFinite(value)?`${(value/1024/1024).toFixed(1)} MiB`:"未知大小";
  if (product && !local && !options.includes(selection)) {
    const dialog=window.WS?.dialog?.("应用与数据库更新", "应用按所选项目下载并更新。请先保存当前编辑，更新完成后应用会重启。");
    if (!dialog) return;
    const labels={app_only:"只更新应用",database_only:"只更新数据库",both:"同时更新应用和数据库",
      database_setup:"安装数据库"};
    for (const option of options) {
      const row=UI.el("section",null,"workspace-release-option");
      const app=option.app_bytes ? `下载 ${mib(option.app_bytes)}` : "保留当前应用";
      const database=option.database_bytes ? `下载 ${mib(option.database_bytes)}`
        : option.mode==="database_setup"?"使用本地发行包":"保留当前数据库";
      row.append(UI.el("p", `应用 ${option.app_release_id}（${app}） · 数据库 ${option.database_release_id}（${database}）`));
      for (const [type, label, date, downloading] of [
        ["app", "应用", option.app_released_at, option.app_bytes],
        ["database", "数据库", option.database_released_at, option.database_bytes],
      ]) {
        const notes = option.release_notes?.[type];
        if (downloading && notes) row.append(UI.button(`${label}发行说明${date ? ` · ${date.slice(0, 10)}` : ""}`, async () => {
          const opened = await callApi("open_external_link", notes);
          if (opened.code) setStatus(opened.message);
        }));
      }
      row.append(UI.button(labels[option.mode], () => { if (dialog.finish("confirmed")!==false) DV.updateDatabase(option); },"primary"));
      dialog.appBody.append(row);
    }
    dialog.setActions(UI.button("稍后", () => dialog.finish("cancelled")));
    return;
  }
  if (product && (window.WB?.dirty?.() || window.WB?.unappliedEditor?.() || window.PL?.dirty?.()
      || window.RS?.state?.pending)) {
    setStatus("请先保存编辑并等待当前计算完成，再开始更新。");
    return;
  }
  const digest=product?selection.confirmation_digest:candidate.confirmation_digest;
  if(typeof digest!=="string"||!digest){
    DS.setText($("#db-update-status"),"版本确认信息不完整，请重新检查。");return;
  }
  const sizes=candidate.artifacts||{};
  const confirmed=product||window.confirm(
    `更新应用与数据库到同一版本？\n\n`+
    `App：${candidate.remote_app_release_id||"未知"}\n`+
    `数据库：${candidate.remote_database_release_id||"未知"}\n`+
    `游戏数据：${candidate.remote_game_version||"未知"}\n`+
    `下载：App ${mib(sizes.app?.size_bytes)}，数据库 ${mib(sizes.database?.size_bytes)}\n\n`+
    "下载和校验完成后，当前 App 会退出；外置更新器将同时换版并自动重启。"+
    "旧版本会话与结果不会直接继续计算。"
  );
  if(!confirmed)return;
  const request = ++DV.state.updateRequest;
  DV.state.databaseUpdateRunning=true;
  $("#data-update").disabled = false;
  $("#data-update").textContent = "取消下载";
  $("#db-update-status").textContent = "正在下载并检查选定的更新…";
  const progressTimer=setInterval(async()=>{
    const progress=await callApi("database_update_progress");
    if (request !== DV.state.updateRequest || !DV.state.databaseUpdateRunning || progress.code || !progress.data) return;
    const status=progress.data;
    $("#data-update").disabled=status.cancellable===false;
    if(status.cancellable===false)DS.setText($("#db-update-status"),"正在完成安装和校验…");
    else if(status.total_bytes)DS.setText($("#db-update-status"),
      `已下载 ${mib(status.bytes)} / ${mib(status.total_bytes)} · ${mib(status.bytes_per_second)}/秒`);
  },1000);
  let r;
  try {
    r=await callApi("update_database",digest,true);
  } finally {
    clearInterval(progressTimer);
    DV.state.databaseUpdateRunning=false;
    $("#data-update").textContent = product?"查看更新选项":"更新应用与数据库";
    $("#data-update").disabled = DV.state.databaseUpdate?.update_action_available !== true;
  }
  if (r.code !== 0) { DS.setText($("#db-update-status"), r.message); return; }
  if(r.data.status==="complete" && selection?.mode==="database_setup"){
    DS.setText($("#db-update-status"),"数据库安装完成。请保存当前编辑，重开应用后即可计算。");
    $("#data-update").disabled=true;
    const banner=$("#update-banner");
    if(banner)banner.replaceChildren(UI.el("span","数据库安装完成。重新打开应用后即可计算。"));
    return;
  }
  if(r.data.action==="already_current"){
    DS.setText($("#db-update-status"),"当前应用与数据库已经是该版本。");
    await DV.checkDatabaseUpdate();return;
  }
  if(r.data.action!=="handoff_launched"){
    DS.setText($("#db-update-status"),"更新尚未进入安装阶段。");return;
  }
  const status = $("#db-update-status");
  DS.setText(status, "更新器已接管。请确认关闭应用，关闭后开始安装。");
  if (window.AgentWorkbenchApp?.ready) {
    window.AgentWorkbenchApp.requestClose();
    if (await window.AgentWorkbenchApp.lifecycleController?.settle?.() !== false) return;
  } else if (!window.WB?.requestClose || window.WB.requestClose() === true) {
    const closed = await callApi("confirm_close");
    if (closed.code === 0 && closed.data?.closed === true) return;
  }
  if (request === DV.state.updateRequest) {
    DS.setText(status, "应用保持打开，更新尚未安装。请保存编辑后重试更新。");
  }
};

DV.init = async () => {
  const vlSel = $("#data-version-line");
  vlSel.addEventListener("change", () => {
    if (window.ED) { ED.request++; ED.detailRequest++; ED.detail = null; ED.dataVersion = null; }
    DV.state.versionLine = vlSel.value;
    DV.renderToolbarState();
    DS.ensure(vlSel.value).then(()=>{if(RT.tpl())RT.renderAll();});
    DV.state.detail = null;
    DV.renderDetail();
    DV.refreshList();
  });
  $("#data-search").addEventListener("click", () => DV.refreshList());
  $("#data-update").addEventListener("click", DV.updateDatabase);
  $("#data-annotations")?.addEventListener("click",DV.openAnnotationManager);
  $("#data-q").addEventListener("keydown", (e) => {
    if (e.key === "Enter") DV.refreshList();
  });
  DV.checkDatabaseUpdate();
  await DV.loadOverview();
};
