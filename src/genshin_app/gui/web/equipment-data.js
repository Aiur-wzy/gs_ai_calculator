"use strict";
window.ED = {category:"character", page:1, request:0, detailRequest:0, caps:null, detail:null,
  dataVersion:null, picked:null, filters:{name:"", rarity:0, type:"", scope:"codex", setId:null},
  slots:{EQUIP_BRACER:"生之花",EQUIP_NECKLACE:"死之羽",EQUIP_SHOES:"时之沙",EQUIP_RING:"空之杯",EQUIP_DRESS:"理之冠"}};
ED.categoryState = new Map();
ED.defaultFilters = () => ({name:"",rarity:0,type:"",scope:"codex",setId:null});
ED.unit = (value, unit) => value == null ? "未知（源缺值）" : ["percent","ratio"].includes(unit)
  ? `${Number((value * 100).toFixed(4))}%` : String(Number(Number(value).toFixed(5)));
ED.name = item => DS.prose(item.name || "", `资料 ${item.id ?? item.affix_id}（中文名待补）`) || `资料 ${item.id ?? item.affix_id}（中文名待补）`;
ED.sameVersion = value => !ED.dataVersion || JSON.stringify(value) === JSON.stringify(ED.dataVersion);
ED.record = record => { const n = UI.el("article", null, "eq-card");
  n.append(UI.el("h4", DS.prose(record.name || "效果原文","效果中文名待补")));
  n.append(DV.richText(DS.prose(record.desc_template || DV.DESC_STATUS[record.desc_status] || "当前没有原文")));
  n.append(DV.renderSource(record, record.desc_template));
  if (record.param_list || record.add_props) n.append(UI.details("原始参数与属性（保留零槽）",
    {param_list:record.param_list,add_props:record.add_props}));
  (record.annotations || []).forEach(a => n.append(DS.textBlock(`${a.title || "追加机制"}：${a.content}`)));
  if(record.affix_id)n.append(UI.button(`管理此精确效果 #${record.affix_id} 的追加机制`,()=>{
    DV.state.annManager=false;DV.state.annPage=1;DV.state.annTarget={target_type:"equip_affix",target_id:record.affix_id,label:`精确效果 #${record.affix_id}`};
    $("#annotation-panel")?.remove();const holder=UI.el("div");holder.id="annotation-panel";n.append(holder);DV.refreshAnnotations();}));
  return n; };
ED.showCategory = (category, {id, setId, back, reset, filters}={}) => {
  const line=DV.state.versionLine, oldKey=`${ED.categoryLine||line}:${ED.category}`, key=`${line}:${category}`;
  const previous={category:ED.category, filters:{...ED.filters}, page:ED.page,
    id:ED.selectedId, detail:ED.detail, picked:ED.picked, version:ED.dataVersion, back:ED.back};
  if(category!==ED.category)ED.categoryState.set(oldKey,previous);
  const stored=reset?null:ED.categoryState.get(key);
  const chosen=back||stored;
  ED.category=category;ED.categoryLine=line;ED.page=setId!=null?1:chosen?.page||1;ED.filters=chosen?{...chosen.filters}:ED.defaultFilters();
  if(setId!=null)ED.filters={...ED.defaultFilters(),setId};
  if(filters)ED.filters={...ED.filters,...filters};
  ED.selectedId=null;ED.detail=null;ED.picked=null;
  ED.back=back?.back||(id!=null||setId!=null?previous:null);
  DV.state.listRequest=(DV.state.listRequest||0)+1;DV.state.detailRequest=(DV.state.detailRequest||0)+1;
  ED.dataVersion = null; ED.request++; ED.detailRequest++;
  $("#equipment-filters").classList.toggle("hidden", category === "character");
  ["#data-q","#data-element","#data-rarity"].forEach(id => $(id).classList.toggle("hidden", category !== "character"));
  const list=$("#data-list"); if(list)list.replaceChildren(UI.el("p","正在读取资料…"));
  $("#data-category").value=category;
  ED.renderFilters(); DV.renderToolbarState?.(); DV.refreshList(); DV.renderDetail();
  const selected=id??chosen?.id;
  if(category!=="character"&&selected!=null)void ED.open(selected,chosen);
};
ED.renderFilters = () => { const box = $("#equipment-filters"); if (!box) return; box.innerHTML = "";
  if(window.EQ?.picking)box.append(UI.button("← 返回装备",EQ.returnFromData,"eq-back"));
  if(ED.back)box.append(UI.button("← 返回上一项",()=>ED.showCategory(ED.back.category,{back:ED.back}),"eq-back"));
  box.append(UI.field("装备名称", UI.input(ED.filters.name, v => { ED.filters.name=v; ED.page=1; ED.refresh(); })),
    UI.field("目录", UI.select([{value:"codex",label:"图鉴目录"},{value:"all",label:"全部源配置"}],ED.filters.scope,v=>{
      ED.filters.scope=v; ED.page=1; ED.refresh(); })),
    UI.field("星级", UI.select([{value:0,label:"全部"},...[1,2,3,4,5].map(v=>({value:v,label:`${v} 星`}))],ED.filters.rarity,v=>{
      ED.filters.rarity=Number(v); ED.page=1; ED.refresh(); })));
  const types = ED.category === "weapon" ? ED.caps?.weapon_types || [] : ED.category === "artifact" ? ED.caps?.equip_types || [] : [];
  if (types.length) box.append(UI.field("类型/部位", UI.select([{value:"",label:"全部"},...types.map(value=>({value,
    label:ED.slots[value] || DV.WEAPON_NAME.get(value) || "类型中文名待补"}))],ED.filters.type,v=>{ED.filters.type=v;ED.page=1;ED.refresh();})));
  const id = UI.input("", ()=>{}, true);
  box.append(UI.field("精确编号",id), UI.button("定位编号",()=>{if(Number.isSafeInteger(Number(id.value)) && Number(id.value)>0) ED.open(Number(id.value));}));
};
ED.refresh = async () => { const req=++ED.request, category=ED.category, f={...ED.filters}, page=ED.page, line=DV.state.versionLine;
  const args = category === "weapon" ? [f.name,f.type,f.rarity,f.scope,line,page,50]
    : category === "set" ? [f.name,f.rarity,f.scope,line,page,50]
      : [f.name,f.setId,f.type,f.rarity,f.scope,line,page,50];
  const method = {weapon:"list_weapons",set:"list_artifact_sets",artifact:"list_artifacts"}[category];
  if (!method) return;
  const r=await callApi(method,...args); if(req!==ED.request || category!==ED.category || line!==DV.state.versionLine)return;
  const box=$("#data-list"); box.innerHTML="";
  if(r.code){box.append(UI.error(r),UI.button("重试",ED.refresh));return;}
  if(ED.dataVersion&&!ED.sameVersion(r.data.data_version)){ED.detail=null;ED.detailRequest++;PL.sourceChanged();ED.renderDetail();}
  ED.dataVersion=r.data.data_version;DV.renderToolbarState?.();
  box.append(UI.el("p",`共 ${r.data.total} 项 · 第 ${page} 页`));
  r.data.items.forEach(item=>{const button=UI.button(`${ED.name(item)} · #${item.id}${item.rarity?` · ${item.rarity}星`:""}`,
    ()=>{if(category===ED.category&&line===DV.state.versionLine)void ED.open(item.id);},"eq-list-item");
    button.setAttribute("data-equipment-id",String(item.id));button.setAttribute("aria-current",String(item.id===ED.selectedId));box.append(button);});
  const prev=UI.button("上一页",()=>{ED.page--;ED.refresh();}), next=UI.button("下一页",()=>{ED.page++;ED.refresh();});
  prev.disabled=page<=1; next.disabled=page*50>=r.data.total; box.append(prev,next);
};
ED.open = async (id, previous) => { const req=++ED.detailRequest, category=ED.category, line=DV.state.versionLine;
  if(id===ED.selectedId&&ED.detail&&ED.sameVersion(ED.detail.data_version))return;
  const r=await callApi({weapon:"weapon_detail",set:"artifact_set_detail",artifact:"artifact_detail"}[category],id,line);
  if(req!==ED.detailRequest || category!==ED.category || line!==DV.state.versionLine)return;
  if(r.code){$("#data-detail").innerHTML="";$("#data-detail").append(UI.error(r));return;}
  if(!ED.sameVersion(r.data.data_version)){ED.detail=null;ED.dataVersion=r.data.data_version;
    setStatus("资料版本已变化，请重新选择配置");PL.sourceChanged();ED.refresh();return;}
  ED.selectedId=id;ED.detail=r.data;ED.dataVersion=r.data.data_version;
  const restore=previous?.id===id&&previous.picked&&JSON.stringify(previous.version)===JSON.stringify(r.data.data_version);
  ED.picked=restore?previous.picked:{level:category==="weapon"?1:0,ascension:0,refinements:[],main_prop_id:null};
  if(category==="weapon"&&!restore) ED.picked.refinements=(r.data.effects||[]).map(g=>({source_index:g.source_index,
    affix_group_id:g.affix_group_id,refinement:g.refinements[0]?.refinement}));
  for(const button of $("#data-list")?.querySelectorAll?.("[data-equipment-id]")||[])button.setAttribute("aria-current",String(button.getAttribute("data-equipment-id")===String(id)));
  ED.renderDetail(); $("#data-detail").scrollTop=0;
};
ED.renderStats = (box, values) => { values.forEach(v=>box.append(UI.el("p",`${v.prop_name||DS.label("stats",v.prop_type)}：${ED.unit(v.value,v.unit)}`)));
  box.append(UI.details("成长值与来源",values)); };
ED.renderDetail = () => { const box=$("#data-detail");box.innerHTML=""; const d=ED.detail;
  if(!d){box.append(UI.el("p","从左侧选择装备，或按精确编号定位。"));return;}
  const item=d.weapon||d.artifact_set||d.artifact;
  box.append(UI.el("h3",`${ED.name(item)} · #${item.id}`),UI.el("p",`${item.in_codex?"图鉴收录":"未在本快照图鉴目录收录"} · ${DS.label("version_lines",DV.state.versionLine)}`),
    UI.details("资料版本",d.data_version));
  if(item.desc_template || item.desc_status)box.append(ED.record(item));
  if(d.weapon) {
    const controls=UI.el("div",null,"eq-grid"), stats=UI.el("div");
    const load=async()=>{const identity=ED.detail, token=++ED.statsRequest;
      const r=await callApi("weapon_stats",item.id,ED.picked.level,ED.picked.ascension,DV.state.versionLine);
      if(identity!==ED.detail||token!==ED.statsRequest)return;stats.innerHTML="";
      if(r.code)stats.append(UI.error(r));else if(!ED.sameVersion(r.data.data_version))stats.append(UI.el("p","资料已变化，请重选"));
      else ED.renderStats(stats,r.data.properties);};
    ED.statsRequest=ED.statsRequest||0;
    controls.append(UI.field("武器等级",UI.input(ED.picked.level,v=>{ED.picked.level=v;load();},true)),
      UI.field("突破阶段",UI.select((item.available_states||[]).map(s=>({value:s.ascension,label:`突破 ${s.ascension} (${s.min_level}–${s.max_level}级)`})),
        ED.picked.ascension,v=>{ED.picked.ascension=Number(v);load();})));
    box.append(controls,stats);load();
    if(!(d.effects||[]).length)box.append(UI.el("p","本配置无被动效果"));
    (d.effects||[]).forEach((g,i)=>{const body=UI.el("div"),render=()=>{body.innerHTML="";
      const e=g.refinements.find(x=>x.refinement===ED.picked.refinements[i].refinement);if(e)body.append(ED.record(e));};
      box.append(UI.field(`效果组 ${g.affix_group_id} 精炼`,UI.select(g.refinements.map(e=>({value:e.refinement,label:`精炼 ${e.refinement} 阶`})),
        ED.picked.refinements[i].refinement,v=>{ED.picked.refinements[i].refinement=Number(v);render();})),body);render();});
  } else if(d.artifact_set) {
    (d.effects||[]).forEach(e=>{box.append(UI.el("h4",`${e.required_count} 件效果`),e.effect?ED.record(e.effect):UI.el("p","源配置未提供关联效果"));});
    if(!EQ.input()||EQ.isPanel()){
      const selected=EQ.picking?EQ.setPick:null;
      if(selected)box.append(UI.button(selected.count===4?"选为当前4件套":`选为第${selected.index+1}套（2件）`,()=>EQ.pickFromData(d,{...selected})));
      else box.append(UI.button("选为4件套",()=>EQ.pickFromData(d,{count:4,index:0})),
        UI.button("选为第一套（2件）",()=>EQ.pickFromData(d,{count:2,index:0})),
        UI.button("选为第二套（2件）",()=>EQ.pickFromData(d,{count:2,index:1})));
    }
    box.append(UI.button("查看本套装单件",()=>ED.showCategory("artifact",{setId:item.id})));
    (d.piece_refs||[]).forEach(p=>box.append(UI.button(`${ED.slots[p.equip_type]||"部位中文名待补"} · ${p.rarity}星 · #${p.artifact_id}`,
      ()=>ED.showCategory("artifact",{id:p.artifact_id,setId:item.id}))));
  } else {
    box.append(UI.el("p",`${ED.slots[item.equip_type]||"部位中文名待补"} · ${item.rarity}星 · 上限 +${item.max_level}`));
    if(d.set_ref)box.append(UI.button(`套装：${d.set_ref.name||d.set_ref.id}`,()=>ED.showCategory("set",{id:d.set_ref.id})));
    const controls=UI.el("div",null,"eq-grid"),stats=UI.el("div");box.append(controls,stats);
    const identity=d; const load=async()=>{if(!ED.picked.main_prop_id)return;
      const token=++ED.statsRequest,r=await callApi("artifact_main_stats",item.id,ED.picked.level,ED.picked.main_prop_id,DV.state.versionLine);
      if(identity!==ED.detail||token!==ED.statsRequest)return;stats.innerHTML="";
      if(r.code)stats.append(UI.error(r));else if(ED.sameVersion(r.data.data_version))ED.renderStats(stats,[r.data.stat]);};
    ED.statsRequest=ED.statsRequest||0;
    controls.append(UI.field("强化等级",UI.input(ED.picked.level,v=>{ED.picked.level=v;load();},true)));
    callApi("artifact_main_props",d.main_prop_query.depot_id,"",DV.state.versionLine,1,100).then(r=>{
      if(identity!==ED.detail)return;if(r.code){controls.append(UI.error(r));return;}
      if(!ED.sameVersion(r.data.data_version)){controls.append(UI.el("p","资料已变化，请重选"));return;}
      if(r.data.items.length===1)ED.picked.main_prop_id=r.data.items[0].id;
      controls.append(UI.field("主词条",UI.select([{value:"",label:"请选择"},...r.data.items.map(p=>({value:p.id,label:`${p.prop_name} #${p.id}`}))],
        ED.picked.main_prop_id,v=>{ED.picked.main_prop_id=v?Number(v):null;load();})));load();});
    callApi("artifact_substats",d.substat_query.depot_id,"",DV.state.versionLine,1,100).then(r=>{
      if(identity===ED.detail&&!r.code&&ED.sameVersion(r.data.data_version))box.append(UI.details("副词条源档位（非累计总值、无概率）",r.data.items));});
  }
  if(!d.artifact_set&&!(d.artifact&&EQ.isPanel()))box.append(UI.button("选入当前方案…",()=>EQ.pickFromData(d,UI.clone(ED.picked))));
  if((d.warnings||[]).length)box.append(UI.details("资料缺项",d.warnings));
  box.append(UI.details("完整来源",item.source_refs||[]));
  const target=d.weapon?"weapon":d.artifact_set?"artifact_set":"artifact";
  box.append(UI.button("管理追加机制",()=>{DV.state.annTarget={target_type:target,target_id:item.id,label:ED.name(item)};
    DV.state.annPage=1;DV.state.annManager=false;$("#annotation-panel")?.remove();
    const holder=UI.el("div");holder.id="annotation-panel";box.append(holder);DV.refreshAnnotations();}));
};
const characterRefresh=DV.refreshList,characterDetail=DV.renderDetail;
DV.refreshList=()=>ED.category==="character"?characterRefresh():ED.refresh();
DV.renderDetail=()=>ED.category==="character"?characterDetail():ED.renderDetail();
ED.init=async()=>{const r=await callApi("db_capabilities");if(!r.code)ED.caps=r.data;
  $("#data-category").addEventListener("change",e=>ED.showCategory(e.target.value));
  ED.renderFilters();};
