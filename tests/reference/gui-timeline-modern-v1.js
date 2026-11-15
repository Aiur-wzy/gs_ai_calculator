// 时间轴渲染与拖拽（G2）：SVG 自绘，秒刻度 + hit 块（按角色泳道）+ Buff 条带（独立 lane）+ 切人标记
"use strict";

const SVGNS = "http://www.w3.org/2000/svg";
const LABEL_W = 210;
const RULER_H = 44;
const HIT_LANE_H = 36;
const BUFF_LANE_H = 24;
const HIT_W = 26;

function svgEl(tag, attrs = {}) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}

RT.duration = () => {
  const t = RT.tpl();
  if (t.loop_duration) return t.loop_duration;
  const ends = [
    ...t.hits.map((h) => h.t),
    ...(t.buffs || []).map((b) => b.end),
    ...(t.switches || []).map((s) => s.t),
  ];
  return Math.max(10, Math.ceil(Math.max(...ends, 0) + 2));
};

RT.renderLegacyTimeline = () => {
  RT.cancelDrag?.();
  const svg = $("#timeline");
  svg.innerHTML = "";
  const width = TV.prepare(RT.tpl(), RT.tpl() ? RT.duration() : 10);
  TV.labels = TV.ruler = TV.corner = null;
  svg.setAttribute("width", width);
  if (!RT.tpl()) {
    svg.setAttribute("height", 80);
    svg.appendChild(svgEl("text", { x: 20, y: 40, class: "tl-hint" }))
      .textContent = "加载或新建模板开始排轴";
    return;
  }
  const t = RT.tpl();
  const dur = RT.duration();
  RT.pxPerSec = TV.pixelsPerSecond;
  const X = (sec) => LABEL_W + sec * RT.pxPerSec;

  const hitLanes = t.characters.length;
  const buffLanes = (t.buffs || []).length;
  const height = RULER_H + hitLanes * HIT_LANE_H + buffLanes * BUFF_LANE_H + 16;
  svg.setAttribute("width", width);
  svg.setAttribute("height", height);
  const labels = svgEl("g", {class: "tl-label-overlay"});
  labels.appendChild(svgEl("rect", {x: 0, y: 0, width: LABEL_W - 14, height, class: "tl-overlay-bg"}));
  const ruler = svgEl("g", {class: "tl-ruler-overlay"});
  ruler.appendChild(svgEl("rect", {x: 0, y: 0, width, height: RULER_H, class: "tl-overlay-bg"}));

  // 秒刻度尺
  const step = TV.tickStep(RT.pxPerSec);
  for (let n = 0; n <= Math.floor(dur / step); n += 1) {
    const s = Number((n * step).toPrecision(12));
    svg.appendChild(svgEl("line", {
      x1: X(s), y1: RULER_H - 8, x2: X(s), y2: height, class: "tl-tick",
    }));
    ruler.appendChild(svgEl("text", { x: X(s) + 3, y: 14, class: "tl-tick-label" }))
      .textContent = `${s}秒`;
  }

  // 切人标记（尺子行菱形）
  (t.switches || []).forEach((sw, i) => {
    const g = svgEl("g", { class: "tl-switch" });
    const dia = svgEl("polygon", {
      points: `${X(sw.t)},${RULER_H - 22} ${X(sw.t) + 6},${RULER_H - 16} ${X(sw.t)},${RULER_H - 10} ${X(sw.t) - 6},${RULER_H - 16}`,
      fill: RT.charColor(sw.character),
      "data-select-kind": "switch", "data-select-index": i,
    });
    if (RT.sel()?.kind === "switch" && RT.sel().index === i) dia.classList.add("selected");
    dia.appendChild(Object.assign(document.createElementNS(SVGNS, "title"),
      { textContent: `切人 → ${DS.character(sw.character)} · ${sw.t}秒` }));
    g.appendChild(dia);
    if(window.PL?.state.plan?.rotation_model){g.addEventListener("click",()=>RT.select("switch",i));}
    else RT.enableDrag(g, sw.t, (d) => {
      sw.t = RT.round2(Math.max(0, Math.min(dur, sw.t + d)));
    }, dia, null, { kind: "switch", index: i });
    ruler.appendChild(g);
  });

  // hit 泳道（按角色）
  t.characters.forEach((c, li) => {
    const y0 = RULER_H + li * HIT_LANE_H;
    labels.appendChild(svgEl("text", { x: 6, y: y0 + HIT_LANE_H / 2 + 4, class: "tl-lane-label" }))
      .textContent = DS.character(c.id);
    svg.appendChild(svgEl("line", {
      x1: LABEL_W - 4, y1: y0, x2: width - 8, y2: y0, class: "tl-lane-line",
    }));
  });
  t.hits.forEach((h, i) => {
    const li = t.characters.findIndex((c) => c.id === h.character);
    if (li < 0) return;
    const y = RULER_H + li * HIT_LANE_H + 5;
    const g = svgEl("g", { class: "tl-hit" });
    const rect = svgEl("rect", {
      x: X(h.t) - HIT_W / 2, y, width: HIT_W, height: HIT_LANE_H - 10, rx: 4,
      fill: RT.charColor(h.character), class: "hit-block",
      "data-select-kind": "hit", "data-select-index": i,
    });
    if (RT.sel()?.kind === "hit" && RT.sel().index === i) rect.classList.add("selected");
    const label = svgEl("text", {
      x: X(h.t), y: y + (HIT_LANE_H - 10) / 2 + 4, class: "tl-hit-label",
    });
    label.textContent = DS.hit(h, i).slice(0, 2);
    const title = svgEl("title");
    title.textContent =
      `${DS.hit(h, i)} · ${h.t}秒 ×${h.count || 1}${h.snapshot ? "［快照］" : ""}`;
    g.append(rect, label, title);
    RT.enableDrag(g, h.t, (d) => {
      h.t = RT.round2(Math.max(0, Math.min(dur, h.t + d)));
    }, g, (ps) => `${DS.hit(h, i)} → ${ps}秒`, { kind: "hit", index: i });
    svg.appendChild(g);
  });

  // Buff 条带（每个 Buff 一条 lane）
  const buffY0 = RULER_H + hitLanes * HIT_LANE_H + 4;
  (t.buffs || []).forEach((b, i) => {
    const y = buffY0 + i * BUFF_LANE_H;
    const selected = RT.sel()?.kind === "buff" && RT.sel().index === i;
    const name = svgEl("text", { x: 6, y: y + 14, class: "tl-lane-label small" });
    name.textContent = DS.buff(b).length > 14 ? DS.buff(b).slice(0, 13) + "…" : DS.buff(b);
    name.appendChild(Object.assign(svgEl("title"), {textContent:DS.buff(b)})); labels.appendChild(name);
    const bar = svgEl("rect", {
      x: X(b.start), y: y + 3, width: Math.max(6, (b.end - b.start) * RT.pxPerSec),
      height: BUFF_LANE_H - 8, rx: 3,
      class: `buff-bar ${b.scope === "frontline" ? "frontline" : "team"}${b.target ? " targeted" : ""}`,
      "data-select-kind": "buff", "data-select-index": i,
    });
    if (selected) bar.classList.add("selected");
    bar.appendChild(Object.assign(document.createElementNS(SVGNS, "title"), {
      textContent: `${DS.buff(b)}（${DS.label("buff_scopes", b.scope)}${b.target ? "→" + DS.character(b.target) : ""}）${b.start}秒～${b.end}秒`,
    }));
    RT.enableDrag(bar, b.start, (d) => {
      const len = b.end - b.start;
      const ns = RT.round2(Math.max(0, Math.min(dur - len, b.start + d)));
      b.start = ns; b.end = RT.round2(ns + len);
    }, bar, (ps) => `${DS.buff(b)} ${ps}秒～${RT.round2(ps + (b.end - b.start))}秒`,
    { kind: "buff", index: i });
    svg.appendChild(bar);
    // 左右边缘拖拽手柄（改 start / end）
    [["l", -1], ["r", 1]].forEach(([side]) => {
      const hx = side === "l" ? X(b.start) - 3 : X(b.end) - 3;
      const handle = svgEl("rect", {
        x: hx, y: y + 3, width: 6, height: BUFF_LANE_H - 8, class: "buff-handle",
      });
      RT.enableDrag(handle, side === "l" ? b.start : b.end, (d) => {
        if (side === "l") {
          b.start = RT.round2(Math.max(0, Math.min(b.end - 0.1, b.start + d)));
        } else {
          b.end = RT.round2(Math.max(b.start + 0.1, Math.min(dur, b.end + d)));
        }
      }, handle, (ps) => `${DS.buff(b)} ${side === "l" ? "开始" : "结束"}=${ps}秒`,
      { kind: "buff", index: i });
      svg.appendChild(handle);
    });
  });
  const corner = svgEl("g", {class: "tl-corner-overlay"});
  corner.appendChild(svgEl("rect", {x: 0, y: 0, width: LABEL_W - 14, height: RULER_H, class: "tl-overlay-bg"}));
  corner.appendChild(svgEl("text", {x: 6, y: 20, class: "tl-lane-label"})).textContent = "角色 / 增益";
  svg.append(labels, ruler, corner);
  TV.labels = labels; TV.ruler = ruler; TV.corner = corner;
  TV.updateOverlays();
};

// The production page loads TD; the legacy renderer remains usable by older
// embedded pages which only supply numeric templates.
RT.renderTimeline = () => {
  if (!window.TD) return RT.renderLegacyTimeline();
  TV.refreshProjection();
  TV.paintProjection();
};

const VIEW_ROW_H = 44;
const ACTION_ROW_H = 84;
const ACTION_CAPTIONS = {"action.elemental_skill":"战技","action.elemental_burst":"爆发",
  "action.normal_attack":"普攻","action.charged_attack":"重击","action.plunging_attack":"下落",
  "action.dash":"冲刺","action.jump":"跳跃","action.wait":"等待"};
const VIEW_KINDS = {
  action: {label:"技能释放", shape:"bar",colorToken:"color-axis-action",fallback:"#61af72"},
  switch: {label:"切换角色", shape:"diamond",colorToken:"color-axis-reaction",fallback:"#c678dd"},
  hit: {label:"命中生效", shape:"circle",colorToken:"color-axis-action",fallback:"#5b8cff"},
  activation: {label:"效果触发", shape:"diamond",colorToken:"color-axis-warning",fallback:"#d19a66"},
  coverage: {label:"持续Buff区间", shape:"bar",colorToken:"color-axis-buff",fallback:"#56b6c2"},
  quota: {label:"次数变化", shape:"triangle",colorToken:"color-danger",fallback:"#e06c75"},
  resource: {label:"共享资源变化", shape:"triangle",colorToken:"color-axis-warning",fallback:"#f08c46"},
  reaction: {label:"反应记录", shape:"triangle",colorToken:"color-axis-reaction",fallback:"#da86ee"},
  damage: {label:"伤害事件", shape:"circle",colorToken:"color-axis-damage",fallback:"#ffb55a"},
  calculation: {label:"计算记录", shape:"square",colorToken:"color-axis-label-muted",fallback:"#9aa3b5"},
  external_damage: {label:"外部计算补充", shape:"diamond",colorToken:"color-axis-reaction",fallback:"#da86ee"},
  condition: {label:"条件与附着", shape:"square",colorToken:"color-axis-label-muted",fallback:"#9abbb6"},
};
const VIEW_NAMES = {actions:"动作轴", effects:"效果轴", damage:"伤害轴"};
const viewThemeColor = (token,fallback) => window.ThemeController?.token?.(token) || fallback;
const viewKind = kind => {
  const entry=VIEW_KINDS[kind] || {label:"其他记录",shape:"square",colorToken:"color-axis-label-muted",fallback:"#9aa3b5"};
  return {...entry,color:viewThemeColor(entry.colorToken,entry.fallback)};
};
const viewName = text => String(text || "来源未提供").replace(/^机制派生收益\s*[·：:]?\s*/, "");
const viewStatus = status => ({passed:"检查通过",calculated:"已计算",executed:"已执行",failed:"执行失败",invalid:"输入无效",
  not_verified:"未验证",not_calculated:"尚未计算",pending:"待完善",cancelled:"已取消",not_checked:"未检查"}[status] || "状态待解释");
const viewEl = (tag, text, cls) => { const n=document.createElement(tag); if(text!=null)n.textContent=text;
  if(cls)n.className=cls; return n; };
const viewSeconds = value => Number(value.toFixed(4));
const viewTime = event => `${viewSeconds(event.start)}秒${Number.isFinite(event.end)&&event.end>event.start?`～${viewSeconds(event.end)}秒`:""}`;
const viewPermission = mode => mode==="actions" ? "选择动作可编辑"
  : TV.projection?.views?.[mode]?.events?.some(event=>event.edit) ? "派生记录只读 · 手工输入可编辑" : "计算结果 · 只读";
const viewEmptyDetail = () => {
  const box=$("#timeline-detail");if(!box)return;
  box.innerHTML="";
  box.append(viewEl("div","事件详情","tl-detail-eyebrow"),viewEl("h4","选择轴上的记录"),
    viewEl("p","点击条块或时间点，在这里查看角色、来源与时间。密集标记可展开逐条查看。","tl-detail-hint"));
};
const viewRestoreFilterFocus = label => $("#timeline-filters")?.querySelector?.(`[aria-label="${label}"]`)?.focus?.();

TV.refreshProjection = () => {
  const template=RT.tpl(), plan=window.PL?.state.plan || null, result=window.RS?.state.result || null;
  // Only editor/result notifications enter here. Scroll, zoom and filtering use
  // the cached validity bit rather than serializing the full native plan again.
  const current=!!(result && window.RS?.isCurrent?.());
  const executionModel=window.RS?.state.snapshot?.executionModel || null;
  const executionTemplate=window.RS?.state.snapshot?.executionTemplate || null;
  const revision=RT.state.revision, old=TV.projectionKey;
  if(old && old.template===template && old.plan===plan && old.result===result && old.current===current
      && old.revision===revision && old.executionModel===executionModel)return;
  if(!TV.projection || TV.projectionTemplate!==template){
    TV.mode=plan?"actions":"damage"; TV.characterFilter=""; TV.sourceFilter="";
    TV.kindVisibility={}; TV.effectFocus="all";TV.damageFocus="damage"; TV.modeScroll={}; TV.detailSelection=null;TV.filterOpen=false;
    viewEmptyDetail();
  }
  TV.projectionTemplate=template;TV.dataInvalid=false;
  const detail=$("#timeline-detail");if(detail && TV.detailSelection){detail.textContent="数据已更新，请重新选择事件";TV.detailSelection=null;}
  TV.projectionKey={template,plan,result,current,revision,executionModel};
  TV.projection=TD.build({template,plan,result,executionModel,executionTemplate,current});
  TV.characterNames=new Map((TV.projection.characters||[]).map(c=>[c.id,c.label || c.id]));
  const rotation=(current&&(result?.result||result)?.rotation_check?.resolved_rotation_model)||plan?.rotation_model||{};
  TV.actionTypes=new Map((rotation.abilities||[]).map(ability=>[ability.id,ability.action_type_id]));
  TV.rebuildRows();
};

TV.rebuildRows = () => {
  RT.cancelDrag?.();TV.cancelPan?.();
  const view=TV.projection?.views?.[TV.mode] || {events:[],notes:[]};
  const records=(view.events||[]).filter(e=>Number.isFinite(e.start));
  const selected=records.filter(e=>!TV.characterFilter || e.character===TV.characterFilter);
  const visibleForCharacter=TV.mode==="effects" && TD.groupCoverage ? TD.groupCoverage(selected,TV.projection.characters) : selected;
  TV.sources=new Map(visibleForCharacter.map(e=>[e.sourceKey,viewName(e.sourceLabel)]));
  if(TV.sourceFilter && !TV.sources.has(TV.sourceFilter))TV.sourceFilter="";
  const visibility=TV.kindVisibility[TV.mode] ||= {};
  TV.kinds=[...new Set(records.map(e=>e.kind))];
  const groups=new Map();
  TV.filteredCount=0;
  for(const e of visibleForCharacter){
    if(TV.mode==="effects" && (TV.effectFocus==="coverage" && e.kind!=="coverage"
      || TV.effectFocus==="points" && e.kind==="coverage"))continue;
    if(TV.mode==="damage" && TV.damageFocus!=="all" && e.kind==="calculation")continue;
    if(TV.sourceFilter && e.sourceKey!==TV.sourceFilter || visibility[e.kind]===false)continue;
    TV.filteredCount++;
    // Actions share a character lane; their original source identities remain
    // available for filtering, details and selection. Other views keep sources.
    const key=JSON.stringify(TV.mode==="actions"?[e.character,"actions"]:[e.character,e.sourceKey,e.kind,e.detail?.state_status||null]);
    if(!groups.has(key))groups.set(key,{key,character:e.character,sourceKey:e.sourceKey,
      sourceLabel:viewName(e.sourceLabel),kind:e.kind,stateStatus:e.detail?.state_status,events:[]});
    groups.get(key).events.push(e);
  }
  const order=new Map((TV.projection.characters||[]).map((c,i)=>[c.id,i]));
  const shared=row=>row.kind==="coverage" && row.events.some(e=>e.detail?.owner!=null
    && e.characters?.some(id=>id!==e.detail.owner));
  TV.rows=[...groups.values()].sort((a,b)=>(TV.mode==="effects" ? Number(b.kind==="coverage")-Number(a.kind==="coverage") : 0)
    ||(TV.mode==="effects" ? Number(shared(b))-Number(shared(a)) : 0)
    ||(order.get(a.character)??999)-(order.get(b.character)??999)
    ||String(a.sourceKey).localeCompare(String(b.sourceKey))||a.kind.localeCompare(b.kind));
  // 同刻记录保留 TD 给出的执行顺序，ID 只是标识，不能当作时序。
  for(const row of TV.rows)row.events.sort((a,b)=>a.start-b.start);
  TV.packKey=null;
  TV.renderControls(view);
};

TV.changeMode = mode => {
  if(!VIEW_NAMES[mode] || TV.mode===mode)return;
  const wrap=$("#timeline-wrap"); TV.modeScroll[TV.mode]=wrap.scrollTop || 0;
  TV.mode=mode; TV.sourceFilter=""; TV.rebuildRows();
  // 不把上一视图的伤害数值或计算记录留在效果/动作详情中。
  TV.detailSelection=null;
  viewEmptyDetail();
  wrap.scrollTop=TV.modeScroll[mode] || 0;
  TV.paintProjection();
};

TV.renderControls = view => {
  const modes=$("#timeline-modes"), filters=$("#timeline-filters"), legend=$("#timeline-legend"), note=$("#timeline-note");
  if(modes){modes.innerHTML="";for(const [id,name] of Object.entries(VIEW_NAMES)){
    const button=viewEl("button",name);button.type="button";button.setAttribute("aria-pressed",String(TV.mode===id));
    button.addEventListener("click",()=>TV.changeMode(id));modes.appendChild(button);
  }modes.appendChild(viewEl("span",viewPermission(TV.mode),"tl-mode-permission"));}
  if(filters){
    // Rebuilding the controls must retire the old overlay instance first.  The
    // filter choices themselves remain in TV, so an open menu can be restored
    // without leaving document-level dismissal handlers behind.
    if(TV.filterSurface && !TV.filterSurface.appFinished){
      TV.filterSurface.destroy?.("rerender",{restoreFocus:false});
    }
    TV.filterSurface=null;
    filters.innerHTML="";
    if(TV.mode==="effects")for(const [value,label] of [["all","全部效果"],["coverage","持续Buff"],["points","命中与触发"]]){
      const button=viewEl("button",label);button.type="button";button.setAttribute("aria-pressed",String((TV.effectFocus||"all")===value));
      button.addEventListener("click",()=>{TV.effectFocus=value;TV.rebuildRows();TV.paintProjection();});filters.appendChild(button);
    }
    if(TV.mode==="damage")for(const [value,label] of [["damage","伤害事件"],["all","含计算记录"]]){
      const button=viewEl("button",label);button.type="button";button.setAttribute("aria-pressed",String((TV.damageFocus||"damage")===value));
      button.addEventListener("click",()=>{TV.damageFocus=value;TV.rebuildRows();TV.paintProjection();});filters.appendChild(button);
    }
    const select=(parent,label, entries, selected, change)=>{
      const box=viewEl("label",label), control=viewEl("select");control.setAttribute("aria-label",label);
      for(const [value,text] of entries){const option=viewEl("option",text);option.value=value;control.appendChild(option);}
      control.value=selected;control.addEventListener("change",()=>{change(control.value);TV.rebuildRows();TV.paintProjection();viewRestoreFilterFocus(label);});
      box.appendChild(control);parent.appendChild(box);
    };
    select(filters,"角色",[["","全部角色"],...[...TV.characterNames]],TV.characterFilter,
      value=>{TV.characterFilter=value;TV.sourceFilter="";});
    const hidden=TV.kinds.filter(kind=>TV.kindVisibility[TV.mode][kind]===false).length;
    const active=Number(!!TV.characterFilter)+Number(!!TV.sourceFilter)+hidden;
    const filter=viewEl("details",null,"tl-filter-menu"),summary=viewEl("summary",`筛选${active?` · ${active}`:""}`);
    const panel=viewEl("div",null,"tl-filter-panel");panel.id="timeline-filter-options";
    filter.open=!!TV.filterOpen;summary.setAttribute("aria-expanded",String(filter.open));summary.setAttribute("aria-controls",panel.id);
    filter.append(summary,panel);filters.appendChild(filter);TV.filterDetails=filter;
    const openSurface=()=>{
      if(TV.filterDetails!==filter)return;
      TV.filterOpen=true;
      if(!window.AppSurfaceKernel){summary.setAttribute("aria-expanded","true");return;}
      if(TV.filterSurface===panel&&!panel.appFinished)return;
      // Let the shared surface own expanded/controls while it is active.  A
      // false previous value guarantees that dismissal restores the summary.
      summary.setAttribute("aria-expanded","false");
      const opened=AppSurfaceKernel.create({surface:panel,trigger:summary,returnFocus:summary,kind:"popover",
        role:"dialog",ariaLabel:"时间轴显示筛选",removeOnClose:false,dismissOnOutside:true,dismissOnScroll:true,
        onRequestClose:()=>{filter.open=false;TV.filterOpen=false;if(TV.filterSurface===panel)TV.filterSurface=null;return true;}});
      if(!opened){filter.open=false;TV.filterOpen=false;summary.setAttribute("aria-expanded","false");return;}
      TV.filterSurface=opened;TV.filterOpen=true;
    };
    const close=(reason="button")=>{
      filter.open=false;TV.filterOpen=false;
      const opened=TV.filterSurface===panel?TV.filterSurface:null;
      if(opened&&!opened.appFinished)opened.finish?.(reason);
      if(TV.filterSurface===panel)TV.filterSurface=null;
      summary.setAttribute("aria-expanded","false");summary.focus?.();
    };
    filter.addEventListener("toggle",()=>{
      if(TV.filterDetails!==filter)return;
      if(filter.open)openSurface();else close("native-close");
    });
    filter.addEventListener("keydown",e=>{if(e.key==="Escape"&&filter.open){e.preventDefault();e.stopPropagation();close();}});
    if(!window.AppSurfaceKernel&&!TV.filterOutsideBound){TV.filterOutsideBound=true;window.addEventListener("pointerdown",e=>{
      const open=TV.filterDetails;if(open?.open && !open.contains?.(e.target)){open.open=false;TV.filterOpen=false;
        open.querySelector?.("summary")?.setAttribute("aria-expanded","false");}
    });}
    panel.appendChild(viewEl("div","筛选记录","tl-filter-heading"));
    select(panel,"来源",[["","全部来源"],...[...TV.sources]],TV.sourceFilter,value=>{TV.sourceFilter=value;});
    const kinds=viewEl("div",null,"tl-filter-kinds");
    for(const kind of TV.kinds){const label=viewEl("label"), check=viewEl("input");check.type="checkbox";
      check.checked=TV.kindVisibility[TV.mode][kind]!==false;check.setAttribute("aria-label",viewKind(kind).label);
      check.addEventListener("change",()=>{TV.kindVisibility[TV.mode][kind]=check.checked;TV.rebuildRows();TV.paintProjection();viewRestoreFilterFocus(viewKind(kind).label);});
      label.append(check,viewEl("span",viewKind(kind).label));kinds.appendChild(label);
    }
    panel.appendChild(kinds);
    if(legend)panel.appendChild(legend);
    const reset=viewEl("button","重置筛选"),done=viewEl("button","收起");reset.type=done.type="button";
    reset.addEventListener("click",()=>{TV.characterFilter="";TV.sourceFilter="";TV.kindVisibility[TV.mode]={};TV.effectFocus="all";TV.damageFocus="damage";TV.rebuildRows();TV.paintProjection();TV.filterDetails?.querySelector?.("summary")?.focus?.();});
    done.addEventListener("click",close);const footer=viewEl("div",null,"tl-filter-footer");footer.append(reset,done);panel.appendChild(footer);
    if(filter.open)openSurface();
  }
  if(legend){legend.innerHTML="";const symbols={bar:"━",circle:"●",diamond:"◆",triangle:"▲",square:"■"};
    const shown=kind=>TV.kindVisibility[TV.mode][kind]!==false
      && !(TV.mode==="effects"&&(TV.effectFocus==="coverage"&&kind!=="coverage"||TV.effectFocus==="points"&&kind==="coverage"))
      && !(TV.mode==="damage"&&TV.damageFocus!=="all"&&kind==="calculation");
    for(const kind of TV.kinds)if(shown(kind)){const k=viewKind(kind);
      const item=viewEl("span"),symbol=viewEl("span",symbols[k.shape],"tl-legend-symbol");symbol.style.color=k.color;
      item.append(symbol,viewEl("span",` ${k.label}`));legend.appendChild(item);}
    if(TV.mode==="effects" && TV.effectFocus!=="points"){
      const exhausted=viewEl("span","━ 次数或共享库存耗尽（仍在计时）");exhausted.style.color="var(--color-axis-exhausted)";legend.appendChild(exhausted);}
    legend.appendChild(viewEl("span","×数量：密集记录，点击逐条查看"));
    if(TV.mode==="damage")legend.appendChild(viewEl("span","Σ：密集组期望伤害合计。计算记录解释同次伤害，不是额外游戏事件"));
  }
  if(note){note.textContent=[...(TV.mode==="effects"?[view.coverage?.message,"持续Buff按来源显示起止区间；点击查看接收角色与刷新记录。无独立时长的自身条件增益只保留在逐击明细。"]:[]),
    ...(view.notes||[]).map(n=>typeof n==="string"?n:n.message || n.note || "")].filter(Boolean).join("；");
    if(TV.mode==="effects" && view.coverage?.excluded?.length){const details=viewEl("details"),list=viewEl("ul");
      details.appendChild(viewEl("summary",`未显示为持续条的来源（${view.coverage.excluded.length}）`));
      for(const item of view.coverage.excluded)list.appendChild(viewEl("li",`${/[\u3400-\u9fff]/u.test(item.name_zh||"")?item.name_zh:"增益来源"}：${item.message||"没有可靠的有限持续时间"}`));
      details.appendChild(list);note.appendChild(details);}}
  const empty=$("#timeline-empty-state");if(empty){empty.innerHTML="";
    const needsCalc=["not_calculated","stale"].includes(view.coverage?.origin);
    const absent=!(view.events||[]).some(event=>event.kind==="coverage");
    empty.hidden=!(TV.mode==="effects"&&TV.effectFocus!=="points"&&(needsCalc||absent)&&view.coverage);
    if(!empty.hidden){empty.appendChild(viewEl("span",view.coverage.message));
      if(needsCalc){const button=viewEl("button","计算当前方案，展开持续状态");button.type="button";button.disabled=!!window.RS?.state.pending;
        button.addEventListener("click",()=>window.RS?.runCalc?.());empty.appendChild(button);}
      else if(view.coverage.excluded?.length)empty.appendChild(viewEl("span",`有 ${view.coverage.excluded.length} 个来源未绘制；原因见「操作与说明」。`));}}
};

// Pack dense points once per scale/filter change. Absolute-time bins and the
// complete lane keep groups stable while panning across the viewport boundary.
TV.packRows = pixelsPerSecond => {
  if(TV.packKey===pixelsPerSecond)return;
  TV.packKey=pixelsPerSecond;TV.markerCount=0;
  for(const row of TV.rows){
    const items=[];let point=null, interval=null;
    for(const event of row.events){
      const isInterval=Number.isFinite(event.end) && event.end>event.start;
      if(isInterval){
        point=null;
        // Merge only genuinely overlapping spans, never bridge temporal gaps.
        if(interval && event.start<interval.end){interval.events.push(event);interval.end=Math.max(interval.end,event.end);}
        else {interval={events:[event],start:event.start,end:event.end,interval:true};items.push(interval);}
      }else{
        interval=null;
        if(point && (event.start-point.start)*pixelsPerSecond<28){point.events.push(event);point.last=event.start;}
        else {point={events:[event],start:event.start,end:event.start,last:event.start,interval:false};items.push(point);}
      }
    }
    row.items=items;
    // Prefix maxima allow a binary seek even when an earlier long interval
    // spans many later point records. No event is dropped from a group.
    let end=-Infinity;row.ends=items.map(item=>end=Math.max(end,item.end,item.last??item.start));
    TV.markerCount+=items.length;
    row.actionLabels=TV.mode==="actions"?TV.packActionLabels(row.events,pixelsPerSecond):[];
  }
};

TV.actionCaption = event => {
  if(event.kind==="switch")return viewName(event.label).replace(/^切换至/,"切→");
  const type=TV.actionTypes?.get(event.detail?.ability_id);
  if(ACTION_CAPTIONS[type])return ACTION_CAPTIONS[type];
  const label=viewName(event.label),words={元素战技:"战技",元素爆发:"爆发",普通攻击:"普攻",重击:"重击",下落攻击:"下落",冲刺:"冲刺",跳跃:"跳跃",等待:"等待"};
  for(const [name,short] of Object.entries(words))if(label.includes(name))return short;
  return label.length>4?`${label.slice(0,3)}…`:label;
};

TV.packActionLabels = (events,pixelsPerSecond) => {
  const labels=[];
  for(const event of events){
    const caption=TV.actionCaption(event),previous=labels.at(-1);
    // Only annotation text is grouped. Every original action keeps its exact
    // time block and identity; clicking a grouped label lists those actions.
    if(previous && event.kind!=="switch" && previous.caption===caption
        && (event.start-previous.last)*pixelsPerSecond<32){
      previous.events.push(event);previous.last=event.start;
    }else labels.push({events:[event],caption,start:event.start,last:event.start});
  }
  const occupied=[[],[]],plotWidth=Math.max(1,TV.projection.duration*pixelsPerSecond);
  const priority=label=>["战技","爆发"].includes(label.caption)?0:label.events[0].kind==="switch"?2:1;
  for(const label of [...labels].sort((a,b)=>priority(a)-priority(b)||a.start-b.start)){
    label.text=label.caption+(label.events.length>1?`×${label.events.length}`:"");
    const width=label.text.length*11+8,center=(label.start+label.last)/2*pixelsPerSecond;
    label.x=Math.max(3,Math.min(center-width/2,plotWidth-width-3));label.width=width;
    label.level=occupied.findIndex(spans=>spans.every(span=>label.x+width+4<=span[0]||label.x>=span[1]+4));
    if(label.level>=0)occupied[label.level].push([label.x,label.x+width]);
  }
  return labels;
};

TV.visibleItems = (row, left, right) => {
  let lo=0,hi=row.ends.length;
  while(lo<hi){const mid=(lo+hi)>>1;if(row.ends[mid]<left)lo=mid+1;else hi=mid;}
  const items=[];
  for(let i=lo;i<row.items.length && row.items[i].start<=right;i++){
    if(Math.max(row.items[i].end,row.items[i].last??row.items[i].start)>=left)items.push(row.items[i]);
  }
  return items;
};

TV.color = character => {
  const index=(TV.projection.characters||[]).findIndex(c=>c.id===character);
  const slot=index>=0?index%6:0;
  return viewThemeColor(`color-axis-character-${slot+1}`,
    ["#5b8cff","#e06c75","#61af72","#d19a66","#c678dd","#56b6c2"][slot] || "#9aa3b5");
};

TV.updateDetailSelection = () => {
  const selected=TV.detailSelection;
  const ids=new Set((selected?.events || (selected?[selected]:[])).map(event=>event.id));
  for(const {node,events} of TV.markers || []){
    const active=events.some(event=>ids.has(event.id));
    node.classList.toggle("tl-event-selected",active);node.setAttribute("aria-pressed",String(active));
  }
};

TV.paintProjection = () => {
  if(!TV.projection)return;
  TV.paintQueued=false;
  const template=RT.tpl(), svg=$("#timeline"), wrap=$("#timeline-wrap");
  TV.markers=[];
  // Read the container before replacing content; CSS reserves scrollbar space.
  const width=TV.prepare(template, Math.max(0.001,TV.projection.duration || 10));
  RT.pxPerSec=TV.pixelsPerSecond;
  TV.packRows(TV.pixelsPerSecond);
  const rowHeight=TV.mode==="actions"?ACTION_ROW_H:VIEW_ROW_H;
  const height=Math.max(100,RULER_H+TV.rows.length*rowHeight+16);
  svg.setAttribute("width",width);svg.setAttribute("height",height);
  const nodes=[], labels=svgEl("g",{class:"tl-label-overlay"}), ruler=svgEl("g",{class:"tl-ruler-overlay"});
  const viewportWidth=wrap.clientWidth || 800, viewportHeight=wrap.clientHeight || 500;
  const top=wrap.scrollTop || 0, left=wrap.scrollLeft || 0;
  const startTime=Math.max(0,(left-24)/TV.pixelsPerSecond);
  const endTime=(left+Math.max(1,viewportWidth-LABEL_W)+24)/TV.pixelsPerSecond;
  const X=sec=>LABEL_W+sec*TV.pixelsPerSecond;
  const first=Math.max(0,Math.floor((top-RULER_H)/rowHeight)-2);
  const last=Math.min(TV.rows.length,Math.ceil((top+viewportHeight-RULER_H)/rowHeight)+2);
  labels.appendChild(svgEl("rect",{x:0,y:top,width:LABEL_W-8,height:viewportHeight,class:"tl-overlay-bg"}));
  ruler.appendChild(svgEl("rect",{x:left,y:0,width:viewportWidth,height:RULER_H,class:"tl-overlay-bg"}));
  const step=TV.tickStep(TV.pixelsPerSecond);
  for(let s=Math.max(0,Math.ceil(startTime/step)*step);s<=Math.min(TV.duration,endTime);s+=step){
    const time=Number(s.toPrecision(12));
    nodes.push(svgEl("line",{x1:X(time),y1:top+RULER_H-8,x2:X(time),y2:top+viewportHeight,class:"tl-tick"}));
    const text=svgEl("text",{x:X(time)+3,y:20,class:"tl-tick-label"});text.textContent=`${time}秒`;ruler.appendChild(text);
  }
  let drawn=0;
  for(let index=first;index<last;index++){
    const row=TV.rows[index], y=RULER_H+index*rowHeight;
    const owner=TV.characterNames.get(row.character), name=(owner && !row.sourceLabel.startsWith(owner)?`${owner} · `:"")
      +row.sourceLabel+` · ${viewKind(row.kind).label}`;
    const actionLane=TV.mode==="actions";
    const sourceName=owner && row.sourceLabel.startsWith(`${owner} · `)?row.sourceLabel.slice(owner.length+3):row.sourceLabel;
    const primary=actionLane?(owner||"全队"):sourceName;
    const secondary=actionLane?"技能释放 · 切换角色":`${owner||"全队"} · ${row.stateStatus?TD.stateName(row.stateStatus):viewKind(row.kind).label}`;
    labels.appendChild(svgEl("circle",{cx:10,cy:y+20,r:4,fill:TV.color(row.character),class:"tl-character-color"}));
    const text=svgEl("text",{x:20,y:y+18,class:"tl-lane-label"});text.textContent=primary.length>14?`${primary.slice(0,13)}…`:primary;
    const title=svgEl("title");title.textContent=actionLane?secondary+` · ${primary}`:name;text.appendChild(title);labels.appendChild(text);
    const sub=svgEl("text",{x:20,y:y+34,class:"tl-lane-subtitle"});sub.textContent=secondary;labels.appendChild(sub);
    nodes.push(svgEl("line",{x1:left+LABEL_W-4,y1:y,x2:left+viewportWidth,y2:y,class:"tl-lane-line"}));
    const items=TV.visibleItems(row,startTime,endTime);
    for(let i=0;i<items.length;i++){
      const item=items[i], event=item.events[0], count=item.events.length;
      const kind=viewKind(event.kind), color=event.detail?.state_status==="exhausted"?viewThemeColor("color-axis-exhausted","#89919c"):TV.mode==="actions" && event.kind==="action"?TV.color(event.character):kind.color, group=svgEl("g",{class:`tl-view-event tl-${event.kind}`,
        "data-event-id":event.id,"data-event-count":count,role:"button",tabindex:0});
      const sec=item.interval?item.start:(item.start+(item.last??item.start))/2;
      const x=X(sec), cy=y+22;
      let shape;
      if(item.interval){shape=svgEl("rect",{x:X(item.start),y:y+10,width:Math.max(2,(item.end-item.start)*TV.pixelsPerSecond),
        height:24,rx:4,fill:color});}
      else if(kind.shape==="circle")shape=svgEl("circle",{cx:x,cy,r:count>1?8:5,fill:color});
      else if(kind.shape==="diamond")shape=svgEl("polygon",{points:`${x},${cy-7} ${x+7},${cy} ${x},${cy+7} ${x-7},${cy}`,fill:color});
      else if(kind.shape==="triangle")shape=svgEl("polygon",{points:`${x},${cy-7} ${x+7},${cy+6} ${x-7},${cy+6}`,fill:color});
      else shape=svgEl("rect",{x:x-5,y:cy-5,width:10,height:10,fill:color});
      const statuses=[...new Set(item.events.map(e=>e.detail?.execution_status).filter(Boolean))];
      const failed=statuses.some(s=>["failed","invalid","cancelled"].includes(s));
      if(failed){shape.setAttribute("stroke",viewThemeColor("color-danger","#ff7070"));shape.setAttribute("stroke-width",2);}
      if(statuses.some(s=>!["passed","calculated","executed"].includes(s))){shape.setAttribute("stroke-dasharray","3 2");shape.setAttribute("opacity",0.7);}
      group.appendChild(shape);
      const nextX=i+1<items.length?X(items[i+1].start):left+viewportWidth;
      const available=item.interval?(item.end-item.start)*TV.pixelsPerSecond:nextX-x-14;
      let label=count>1?`×${count}`:viewName(event.label);
      if(count===1 && event.detail?.execution_status && !["passed","calculated","executed"].includes(event.detail.execution_status))label+=`（${viewStatus(event.detail.execution_status)}）`;
      const damageValue=TV.mode==="damage" && item.events.every(e=>["damage","external_damage"].includes(e.kind)&&Number.isFinite(e.value))
        ?Math.round(item.events.reduce((sum,e)=>sum+e.value,0)).toLocaleString("zh-CN"):null;
      if(count>1 && !item.interval){const text=svgEl("text",{x,y:cy+3,class:"tl-view-count","text-anchor":"middle"});
        text.textContent=count>99?"99+":String(count);group.appendChild(text);
        if(damageValue!==null && available>=(damageValue.length+2)*7){
          const total=svgEl("text",{x:x+12,y:cy+4,class:"tl-view-label"});total.textContent=`Σ ${damageValue}`;group.appendChild(total);
        }
      }
      else {
        const capacity=Math.max(0,Math.floor(available/11));let visibleLabel="";
        if(damageValue!==null){
          // 数值完整优先；不足以显示完整数值时，只留标记与完整提示。
          if(capacity>=damageValue.length){
            const room=capacity-damageValue.length-1;
            const name=room>=2?(label.length<=room?label:`${label.slice(0,room-1)}…`):"";
            visibleLabel=damageValue+(name?` ${name}`:"");
          }
        }else if(item.interval && event.kind==="coverage" && available>=64){
          // The lane already names this effect; the bar communicates duration.
          const range=`${event.detail?.state_status==="exhausted"?"耗尽 ":""}${viewSeconds(item.start)}–${viewSeconds(item.end)}秒${count>1?` ×${count}`:""}`;
          if(range.length<=capacity)visibleLabel=range;
          else if(count>1)visibleLabel=`×${count}`;
        }
        if(visibleLabel){const text=svgEl("text",{x:item.interval?X(item.start)+5:x+10,y:cy+4,class:"tl-view-label"});
          text.textContent=visibleLabel;group.appendChild(text);}
      }
      const title=svgEl("title");title.textContent=count>1?`${TV.mode==="actions"?owner||"动作":row.sourceLabel} · ${count}条记录，点击逐条查看`
        :`${viewName(event.label)} · ${event.start}秒${item.interval?`～${event.end}秒`:""}`;group.appendChild(title);
      if(count===1 && TV.mode==="effects" && window.EFX){
        const description=EFX.describe(event,{characterName:id=>TV.characterNames?.get(id)||"未指定角色"});
        title.textContent=[description.title,description.source,description.duration,...description.values].filter(Boolean).join("\n");
      }
      if(damageValue!==null)title.textContent+=` · ${count>1?"本组期望伤害合计":"期望伤害"}：${damageValue}`;
      if(statuses.length)title.textContent+=` · ${statuses.map(viewStatus).join("、")}`;
      group.setAttribute("aria-label",title.textContent);
      TV.markers.push({node:group,events:item.events});
      const selected=count>1?item:event;
      group.addEventListener("click",()=>TV.showDetail(selected));
      group.addEventListener("keydown",e=>{if(e.key==="Enter" || e.key===" "){e.preventDefault();TV.showDetail(selected);}});
      if(count===1 && event.edit && ["hit","buff"].includes(event.edit.kind))TV.bindEditable(group,event);
      nodes.push(group);drawn++;
    }
    if(actionLane)for(const caption of row.actionLabels){
      if(caption.level<0 || caption.x+caption.width<left || caption.x>left+viewportWidth-LABEL_W)continue;
      const x=LABEL_W+caption.x,cy=y+51+caption.level*21,center=X((caption.start+caption.last)/2);
      const group=svgEl("g",{class:"tl-action-caption",role:"button",tabindex:0,"aria-label":`${caption.text} · ${viewTime(caption.events[0])}${caption.events.length>1?`，${caption.events.length}个动作，点击逐条查看`:""}`});
      group.appendChild(svgEl("line",{x1:center,y1:y+35,x2:x+caption.width/2,y2:cy-12,class:"tl-action-leader"}));
      group.appendChild(svgEl("rect",{x,y:cy-13,width:caption.width,height:19,rx:4,class:"tl-action-badge"}));
      const text=svgEl("text",{x:x+caption.width/2,y:cy,"text-anchor":"middle",class:"tl-action-text"});text.textContent=caption.text;group.appendChild(text);
      const selected=caption.events.length===1?caption.events[0]:{events:caption.events};
      group.addEventListener("click",()=>TV.showDetail(selected));
      group.addEventListener("keydown",e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();TV.showDetail(selected);}});
      TV.markers.push({node:group,events:caption.events});nodes.push(group);
    }
  }
  if(!TV.rows.length){const hint=svgEl("text",{x:LABEL_W+12,y:70,class:"tl-hint"});
    hint.textContent=template?"当前视图与筛选没有记录":"加载或新建模板开始排轴";nodes.push(hint);}
  const corner=svgEl("g",{class:"tl-corner-overlay"});corner.appendChild(svgEl("rect",{x:0,y:0,width:LABEL_W-8,height:RULER_H,class:"tl-overlay-bg"}));
  const title=svgEl("text",{x:6,y:23,class:"tl-lane-label"});title.textContent=TV.mode==="actions"?"出场角色":"效果来源 / 角色";corner.appendChild(title);
  // Build off-DOM and swap once, instead of exposing an empty auto-sized SVG.
  if(svg.replaceChildren)svg.replaceChildren(...nodes,labels,ruler,corner);
  else {svg.innerHTML="";svg.append(...nodes,labels,ruler,corner);}
  TV.labels=labels;TV.ruler=ruler;TV.corner=corner;TV.updateOverlays();
  TV.updateDetailSelection();
  const stats=$("#timeline-stats");if(stats){stats.textContent=`${TV.filteredCount} 条记录 · ${TV.rows.length} 条泳道 · ${viewSeconds(TV.projection.duration||10)}秒`;
    stats.title=`聚合后 ${TV.markerCount} 个标记；当前视口绘制 ${drawn} 个。缩放或筛选不改变计算结果。`;}
};

TV.bindEditable = (group,event) => {
  const edit=event.edit, target=edit.kind==="hit"?RT.tpl()?.hits?.[edit.index]:RT.tpl()?.buffs?.[edit.index];
  if(!target)return;
  group.addEventListener("pointerdown",e=>{if(e.button===0)TV.showDetail(event);});
  const start=edit.kind==="hit"?target.t:target.start;
  RT.enableDrag(group,start,delta=>{
    if(edit.kind==="hit")target.t=RT.round2(Math.max(0,Math.min(TV.duration,target.t+delta)));
    else {const duration=target.end-target.start, next=RT.round2(Math.max(0,Math.min(TV.duration-duration,target.start+delta)));
      target.start=next;target.end=RT.round2(next+duration);}
  },group,null,edit);
};

TV.showDetail = (selection,back=null,page=0) => {
  const box=$("#timeline-detail");if(!box)return;
  // 只读详情不应保留上一次手工事件的编辑卡；选择状态不是方案输入。
  if(!["hit","buff","switch"].includes(selection.edit?.kind) && RT.sel()){
    RT.state.selection=null;
    RT.updateTimelineSelection();
    RT.renderPropPanel();
  }
  box.innerHTML="";TV.detailSelection=selection;TV.updateDetailSelection();
  window.WS?.reveal?.("rotation","right");
  const button=(label,fn)=>{const n=viewEl("button",label);n.type="button";n.addEventListener("click",fn);return n;};
  if(selection.events){
    const events=selection.events, size=40, pages=Math.ceil(events.length/size);
    box.appendChild(viewEl("div","密集时间点","tl-detail-eyebrow"));
    box.appendChild(viewEl("h4",`${events.length} 条聚合记录 · 第 ${page+1}/${pages} 页`));
    if(TV.mode==="damage"&&events.every(event=>["damage","external_damage"].includes(event.kind)&&Number.isFinite(event.value)))
      box.appendChild(viewEl("p",`本组伤害合计：${Math.round(events.reduce((sum,event)=>sum+event.value,0)).toLocaleString("zh-CN")}`,"tl-detail-damage"));
    box.appendChild(viewEl("p","选择一条记录查看详情；放大时间轴可分开相邻时间点。","tl-detail-hint"));
    for(const event of events.slice(page*size,(page+1)*size)){
      const entry=button(`${viewTime(event)} · ${viewName(event.label)}${event.beneficiary_names?.length?` · ${event.beneficiary_names.join("、")}`:""}${TV.mode==="damage"&&Number.isFinite(event.value)?` · ${Math.round(event.value).toLocaleString("zh-CN")}`:""}`,
        ()=>TV.showDetail(event,{selection,page}));entry.className="tl-detail-record";box.appendChild(entry);
    }
    if(page>0)box.appendChild(button("上一页",()=>TV.showDetail(selection,null,page-1)));
    if(page+1<pages)box.appendChild(button("下一页",()=>TV.showDetail(selection,null,page+1)));
    return;
  }
  const event=selection;
  if(back?.selection)box.appendChild(button("← 返回聚合记录",()=>TV.showDetail(back.selection,null,back.page)));
  if(back?.event)box.appendChild(button("← 返回伤害事件",()=>TV.showDetail(back.event,back.back)));
  box.appendChild(viewEl("div",`${viewKind(event.kind).label} · ${event.edit?"输入可编辑":"只读记录"}`,"tl-detail-eyebrow"));
  box.appendChild(viewEl("h4",viewName(event.label)));
  const explanation=TV.mode==="effects" && window.EFX && window.DS?.renderEffectDescription
    ? EFX.describe(event,{characterName:id=>TV.characterNames?.get(id)||"未指定角色"}) : null;
  if(explanation)box.appendChild(DS.renderEffectDescription(explanation));
  const card=viewEl("div",null,"tl-detail-card"),fields=viewEl("dl",null,"tl-detail-fields");
  const field=(label,value)=>{if(value==null||value==="")return;fields.append(viewEl("dt",`${label}：`),viewEl("dd",String(value)));};
  if(!explanation && window.EFX && event.detail?.beneficiaries_scope?.eligibility){
    for(const line of EFX.eligibilityDescription(event.detail.beneficiaries_scope,id=>TV.characterNames?.get(id)||"未指定角色"))field("受益资格",line);
    for(const line of EFX.selectionDescription(event.detail.beneficiary_selection,id=>TV.characterNames?.get(id)||"未指定角色"))field("实际选人",line);
  }
  field("角色",TV.characterNames?.get(event.character)||"全队 / 未指定角色");
  field("时间",viewTime(event));
  if(Number.isFinite(event.end)&&event.end>event.start)field("区间长度",`${viewSeconds(event.end-event.start)}秒（结束时刻不含）`);
  if(!explanation)field("来源",viewName(event.sourceLabel));
  if(event.element)field("元素",window.DS?.label?.("elements",event.element)||event.element);
  if(event.detail?.execution_status)field("执行状态",viewStatus(event.detail.execution_status));
  if(event.kind==="coverage"&&event.beneficiary_names?.length)field(event.detail?.receipt_id?"实例角色范围":"接收角色",event.beneficiary_names.join("、"));
  if(event.kind==="coverage"){
    if(event.detail?.state_status)field("区间状态",TD.stateName(event.detail.origin==="declared_interval"?"declared":event.detail.state_status));
    if(event.detail?.remaining_quota!=null)field("本段剩余次数",event.detail.remaining_quota);
    if(Number.isFinite(event.detail?.resource_balance?.remaining)){const balance=event.detail.resource_balance,scale=Number.isFinite(balance.unit_scale)&&balance.unit_scale>0?balance.unit_scale:1;
      field("本段共享库存",`${viewSeconds(balance.remaining/scale)}${Number.isFinite(balance.required)?`（下一次需要 ${viewSeconds(balance.required/scale)}）`:""}`);}
    if(event.detail?.layers!=null)field("本段层数",event.detail.layers);
    if(event.detail?.end_reason)field("区间结束原因",TD.endReason(event.detail.end_reason));
  }
  const damage=TV.mode==="damage"?(Number.isFinite(event.value)?event.value:event.detail?.hit?.expected):null;
  if(Number.isFinite(damage))card.appendChild(viewEl("p",`期望伤害：${Math.round(damage).toLocaleString("zh-CN")}`,"tl-detail-damage"));
  card.appendChild(fields);box.appendChild(card);
  if(event.kind==="coverage"){
    if(event.detail?.beneficiaries_scope?.scope==="current_frontline")box.appendChild(viewEl("p","该区间表示效果已激活，只有符合前台限制的角色命中时才受益。"));
    for(const scope of event.detail?.effect_applicability||[])if(scope.message)box.appendChild(viewEl("p",`收益作用范围：${scope.message}`));
  }
  if(TV.mode==="damage"&&event.kind==="damage"&&event.detail?.hit_id){
    const related=TV.projection.views.damage.events.find(row=>row.kind==="calculation"&&row.detail?.hit_id===event.detail.hit_id&&row.start===event.start);
    if(related)box.appendChild(button("查看这次伤害的计算明细",()=>TV.showDetail(related,{event,back})));
  }
  if(event.kind==="calculation"&&event.detail?.zones){
    const zones=viewEl("dl",null,"tl-detail-fields tl-detail-zones");
    for(const [name,value] of Object.entries(event.detail.zones))if(Number.isFinite(value))zones.append(
      viewEl("dt",window.DS?.label?.("result_zones",name)||name),viewEl("dd",Number(value.toFixed(5)).toLocaleString("zh-CN")));
    if(zones.children.length){box.append(viewEl("h5","计算分区"),zones);}
  }
  if(event.kind==="calculation"&&event.detail?.applied_effects?.length){
    const details=viewEl("details"),list=viewEl("ul");details.open=true;
    details.appendChild(viewEl("summary",`本次实际生效的机制收益（${event.detail.applied_effects.length} 项）`));
    for(const row of event.detail.applied_effects.slice(0,40)){
      const described=window.EFX?.describeAppliedBenefit?.(row)||{label:"机制收益",value:Number.isFinite(row.value)?String(row.value):"数值未提供"};
      const readableSource=/[\u3400-\u9fff]/u.test(String(row.source_name||""))?row.source_name:"机制收益";
      const owner=row.owner?TV.characterNames?.get(row.owner):null;
      list.appendChild(viewEl("li",`${owner?`${owner} · `:""}${readableSource}：${described.label} ${described.value}`));
    }
    details.appendChild(list);
    if(event.detail.applied_effects.length>40)details.appendChild(viewEl("p","这里只显示前 40 项，完整回执见原始记录。"));
    details.appendChild(viewEl("p","以上来自该次命中的引擎实际应用回执；共享库存的获得、消费和补充见效果轴。","tl-detail-hint"));
    box.appendChild(details);
  }
  for(const key of ["message","note","reason","meaning","interpretation"]){const value=event.detail?.[key];
    if(typeof value==="string")box.appendChild(viewEl("p",value));}
  if(event.kind==="coverage" && Array.isArray(event.detail?.states)){
    const activations=new Map(),states=event.detail.states;
    for(const state of states){
      if(!Number.isInteger(state.activation_trace_index) || !Number.isFinite(state.start) || !Number.isFinite(state.rule_expires_at))continue;
      // 次数消费和层数到期可切分同一次激活，不能把每个 state 都当作刷新。
      const key=JSON.stringify([state.activation_trace_index,state.rule_expires_at]);
      if(!activations.has(key))activations.set(key,state);
    }
    const entries=[...activations.values()],seconds=n=>Number(n.toFixed(4));
    if(entries.length>1){
      box.appendChild(viewEl("p",`此连续可用段由 ${entries.length} 次激活/刷新合并形成；整段长度 ${seconds(event.end-event.start)} 秒包含后续刷新，单次到期分别见下列记录。`));
      const history=viewEl("details"),list=viewEl("ol");history.open=true;
      history.appendChild(viewEl("summary",`激活/刷新明细（${entries.length} 次）`));
      for(const [i,state] of entries.slice(0,8).entries())list.appendChild(viewEl("li",
        `${seconds(state.activation_time??state.start)}秒：${state.activation_trace_index<0?"初态登记":i===0?"激活":"刷新"}，规则到期 ${seconds(state.rule_expires_at)}秒。`));
      history.appendChild(list);
      if(entries.length>8)history.appendChild(viewEl("p","这里只显示前 8 次，完整状态记录见下方原始记录。"));
      box.appendChild(history);
    }else if(entries.length===1){const state=entries[0];
      box.appendChild(viewEl("p",`${seconds(state.activation_time??state.start)}秒${state.activation_trace_index<0?"初态登记":"激活"}，规则到期 ${seconds(state.rule_expires_at)}秒；本段未合并后续刷新。`));
    }
    if(entries.length && states.length>entries.length)box.appendChild(viewEl("p","本段还包含次数或层数等状态变化；这些变化不计为刷新。"));
    if(event.detail.receipt_id){const history=viewEl("details");history.appendChild(viewEl("summary",`状态变化（${states.length} 段）`));
      for(const state of states.slice(0,40)){const balance=state.resource_balance,scale=Number.isFinite(balance?.unit_scale)&&balance.unit_scale>0?balance.unit_scale:1;
        history.appendChild(viewEl("p",`${seconds(state.start)}～${seconds(state.end)}秒：${TD.stateName(state.status)}；${state.layers} 层${state.remaining_quota!=null?`；剩余 ${state.remaining_quota} 次`:""}${Number.isFinite(balance?.remaining)?`；共享库存 ${seconds(balance.remaining/scale)}`:""}；结束原因：${TD.endReason(state.end_reason)}`));}
      if(states.length>40)history.appendChild(viewEl("p","其余切片见原始记录。"));box.appendChild(history);}
  }
  if(event.edit){
    if(event.edit.kind==="action" && window.RC?.editAction)box.appendChild(button("编辑此动作",()=>RC.editAction(event.edit.index)));
    else if(["hit","buff","switch"].includes(event.edit.kind))box.appendChild(button("编辑此事件",()=>RT.select(event.edit.kind,event.edit.index)));
  }
  const raw=viewEl("details"), summary=viewEl("summary","原始记录（按需展开）");raw.appendChild(summary);
  let populated=false;raw.addEventListener("toggle",()=>{if(raw.open&&!populated){populated=true;
    raw.appendChild(viewEl("pre",JSON.stringify(event.detail??{},null,2)));}});box.appendChild(raw);
};

// 拖拽：移动中只做视觉预览（transform），松手才提交数值并重渲染
RT.updateTimelineSelection = () => {
  document.querySelectorAll("#timeline [data-select-kind]").forEach((el) => {
    el.classList.toggle("selected", el.dataset.selectKind === RT.sel()?.kind
      && Number(el.dataset.selectIndex) === RT.sel()?.index);
  });
};

RT.enableDrag = (el, baseSec, commit, visualEl, label, selection) => {
  el.addEventListener("pointerdown", (e) => {
    if (!RT.tpl() || e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    RT.cancelDrag?.();
    RT.select(selection.kind, selection.index, false);
    const svg = $("#timeline");
    // 捕获到不会在选中时卸载的 SVG；window 监听同时覆盖捕获不可用的环境。
    try { svg.setPointerCapture(e.pointerId); } catch { /* 合成指针无活动捕获 */ }
    const x0 = e.clientX;
    const wrap = $("#timeline-wrap");
    const scroll0 = wrap.scrollLeft || 0;
    const dur = RT.duration();
    const pixelsPerSec = RT.pxPerSec;
    const template = RT.tpl();
    const move = (ev) => {
      if (ev.pointerId !== e.pointerId) return;
      const d = (ev.clientX - x0 + (wrap.scrollLeft || 0) - scroll0) / pixelsPerSec;
      visualEl.setAttribute("transform", `translate(${d * pixelsPerSec},0)`);
      if (label) setStatus(`${label(RT.round2(Math.max(0, Math.min(dur, baseSec + d))))}（预览）`);
    };
    const cleanup = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", cancel);
      svg.removeEventListener("lostpointercapture", cancel);
      visualEl.removeAttribute("transform");
      RT.cancelDrag = null;
      if (svg.hasPointerCapture(e.pointerId)) svg.releasePointerCapture(e.pointerId);
    };
    const cancel = (ev) => {
      if (ev && ev.pointerId !== e.pointerId) return;
      cleanup();
      setStatus("已取消拖拽，数值未改变");
    };
    const up = (ev) => {
      if (ev.pointerId !== e.pointerId) return;
      const d = (ev.clientX - x0 + (wrap.scrollLeft || 0) - scroll0) / pixelsPerSec;
      cleanup();
      if (RT.tpl() !== template) return;
      if (Math.abs(d) > 0.001) {
        commit(d);
        setStatus("已调整时间；请重新计算结果");
      }
      RT.renderAll();
    };
    RT.cancelDrag = cancel;
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", cancel);
    svg.addEventListener("lostpointercapture", cancel);
  });
};
