"use strict";
window.EQ={characterId:null, busy:false, error:null, cache:new Map(), loading:new Set(), section:"gear"};
EQ.commandMenu=()=>window.AppContextMenu?.forDocument?.(document,window)||null;
EQ.menuError=error=>setStatus(`操作未完成：${String(error?.message||error||"未知错误")}`);
EQ.openCommandMenu=(target,label,items)=>{
  const menu=EQ.commandMenu();if(menu)return menu.open({target,trigger:target,returnFocus:target,label,items,onError:EQ.menuError});
  const first=items.find(item=>item?.type!=="separator"&&item.danger!==true&&item.enabled!==false&&typeof item.run==="function");
  if(first)return first.run();setStatus("操作菜单暂不可用，请重新加载界面");return false;
};
EQ.bindCommandMenu=(target,label,items)=>EQ.commandMenu()?.bind(target,{label,items,onError:EQ.menuError});
EQ.attachCommandMenu=(target,label,items)=>{const more=UI.button("⋯",()=>EQ.openCommandMenu(more,label,items()),"rc-row-menu");more.setAttribute("aria-label",`${label}更多操作`);more.setAttribute("aria-haspopup","menu");target.tabIndex=0;
  target.append(more);EQ.bindCommandMenu(target,label,items);return target;};
EQ.withMenuTarget=(plan,actor,label,locate,run)=>{
  if(EQ.plan()!==plan||EQ.characterId!==actor){setStatus(`${label}所在的装备草稿已变化，请重新打开操作菜单`);return false;}
  const located=locate(EQ.input());if(!located){setStatus(`${label}已变化或移除，请重新打开操作菜单`);return false;}
  return run(located);
};
EQ.uniqueRow=(rows,reference,key)=>{if(!Array.isArray(rows))return null;
  const matches=rows.map((row,index)=>({row,index})).filter(item=>key!=null?item.row?.[key.name]===key.value:item.row===reference);
  return matches.length===1?{rows,...matches[0]}:null;
};
EQ.captureContext=()=>{EQ.returnContext={tab:document.querySelector(".tab.active")?.dataset?.tab||"rotation",
  scroll:Object.fromEntries(["#team-panel","#prop-panel","#timeline-wrap","#data-list","#data-detail"].map(s=>
    [s,{top:$(s)?.scrollTop||0,left:$(s)?.scrollLeft||0}]))};};
EQ.restoreContext=(context=EQ.returnContext)=>{if(!context)return;UI.tabs(context.tab);
  Object.entries(context.scroll).forEach(([s,pos])=>{if($(s)){$(s).scrollTop=pos.top;$(s).scrollLeft=pos.left;}});};
EQ.returnFromData=()=>{EQ.picking=false;EQ.openView(EQ.characterId);};
EQ.pickData=(category,slot)=>{EQ.picking=true;
  if(category!=="set")EQ.setPick=null;
  ED.showCategory(category,{filters:{type:slot||"",setId:null}});UI.tabs("data");};
EQ.plan=()=>PL.state.draft;
EQ.input=()=>EQ.plan()?.equipment_inputs?.[EQ.characterId];
EQ.isPanel=input=>(input||EQ.input())?.mode==="panel";
EQ.panelNote="初始面板已包含武器、圣遗物词条与两件套等常驻属性；换装后请填写对应面板，当前不自动重算词条。";
EQ.blankPanelInput=()=>({mode:"panel",panel_policy:"static_included",character_ref:null,weapon:null,
  artifact_sets:[],source_refs:[{kind:"manual",note:"用户初始面板，已含常驻属性"}],data_version:null});
EQ.blankInput=level=>({character_ref:null,level,basis:{mode:"manual",character_base_atk:null,character_base_hp:null,
  character_base_def:null,crit_rate:null,crit_dmg:null,energy_recharge:null,em:null,dmg_bonus:{},
  ascension_contributions:[],source_refs:[{kind:"manual",note:""}]},weapon:null,
  artifacts:Object.fromEntries(Object.keys(ED.slots).map(k=>[k,null])),manual_contributions:[],data_version:null});
EQ.openView=id=>{EQ.characterId=id;EQ.cache.clear();EQ.loading.clear();EQ.error=null;EQ.detailEpoch=(EQ.detailEpoch||0)+1;
  EQ.showing=true;EQ.picking=false;
  $("#equipment-editor").classList.remove("hidden");$("#rotation-main").classList.add("hidden");
  $("#rotation-checks")?.classList.add("hidden");
  UI.tabs("rotation");EQ.render();};
EQ.closeView=()=>{EQ.showing=false;EQ.picking=false;$("#equipment-editor")?.classList.add("hidden");$("#rotation-main")?.classList.remove("hidden");$("#rotation-checks")?.classList.remove("hidden");};
EQ.getDetail=(kind,id,line)=>EQ.cache.get(`${kind}:${id}:${line}`);
EQ.loadDetail=(kind,id,line)=>{const key=`${kind}:${id}:${line}`;if(EQ.cache.has(key)||EQ.loading.has(key)||!id)return;
  EQ.loading.add(key);const draft=EQ.plan(),epoch=EQ.detailEpoch;callApi(`${kind}_detail`,id,line).then(r=>{
    if(draft!==EQ.plan()||epoch!==EQ.detailEpoch)return;EQ.loading.delete(key);
    if(r.code){EQ.cache.set(key,{error:r.message});}else EQ.cache.set(key,r.data);EQ.render();});};
EQ.chooseCharacter=(characters)=>new Promise(resolve=>{
  const dialog=window.AppDialogKernel?.create?.({title:"将装备加入角色",description:"搜索并选择当前方案中的角色。",className:"workspace-dialog"});
  if(!dialog){setStatus("请先关闭当前对话框");resolve(null);return;}
  let selected=null;const body=UI.el("div"),list=UI.el("div");
  const render=query=>{list.replaceChildren();const matches=characters.filter(row=>`${row.label} ${row.id}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
    for(const row of matches){const button=UI.button(`${row.label} · ${row.id}`,()=>{selected=row.id;dialog.finish("selected");});list.append(button);}
    if(!matches.length)list.append(UI.el("p","没有匹配角色"));};
  const search=UI.input("",render);search.addEventListener("input",()=>render(search.value));
  body.append(UI.field("搜索角色",search),list,UI.button("取消",()=>dialog.finish("cancelled")));dialog.appBody.append(body);render("");
  dialog.onClosed(reason=>resolve(reason==="selected"?selected:null));dialog.focusInitial?.(search);
});
EQ.pickFromData=async(detail,picked)=>{if(!RT.tpl()){setStatus("请先新建或打开方案");return;}
  const template=RT.tpl();
  let id=EQ.characterId;
  if(!id||!RT.charIds().includes(id))id=RT.charIds().length>1?await EQ.chooseCharacter(DS.characters()):RT.charIds()[0];
  if(!id)return;
  if(RT.tpl()!==template||!RT.charIds().includes(id)){setStatus("方案已变化，请重新选择角色");return;}
  if(!await PL.begin(id))return;EQ.characterId=id;
  const existing=EQ.input();
  if(detail.artifact&&(!existing||EQ.isPanel(existing))){setStatus("当前使用初始面板，请选择圣遗物套装。");return;}
  if(detail.artifact_set&&picked.count===2&&picked.index===1&&!existing?.artifact_sets?.length){setStatus("请先选择第一套两件套");return;}
  EQ.plan().equipment_inputs[id] ||= EQ.blankPanelInput();
  const input=EQ.input();if(input.data_version && JSON.stringify(input.data_version)!==JSON.stringify(detail.data_version)){
    setStatus("方案与所选资料版本不同，请先预览应用新资料");return;}
  if(detail.artifact_set){if(!EQ.isPanel(input)){setStatus("此角色使用完整配装，请从套装资料选择单件；原配装已保留。");return;}
    EQ.setSelection(detail,picked.count,picked.index);return;}
  input.data_version=detail.data_version;
  if(detail.weapon)input.weapon={weapon_id:detail.weapon.id,level:picked.level,ascension:picked.ascension,
    refinements:picked.refinements,selected_by:"user"};
  else if(detail.artifact){const slot=detail.artifact.equip_type,old=input.artifacts[slot];
    input.artifacts[slot]={state:"equipped",artifact_id:detail.artifact.id,level:picked.level,
      main_prop_id:picked.main_prop_id,substats:old?.substats??null,selected_by:"user"};}
  PL.touch();EQ.render();};
EQ.render=()=>{if(!EQ.plan())return;const box=$("#equipment-editor"),scroll=box.scrollTop;box.innerHTML="";
  const plan=EQ.plan();
  const heading=UI.el("div",null,"eq-heading");heading.append(UI.button("← 返回",PL.back,"eq-back"),
    UI.el("h3",`装备与面板 · ${DS.character(EQ.characterId,plan.manual_template,plan)} · ${DS.prose(plan.manual_template.meta?.name||"未命名方案","方案中文名待补")}`));
  box.append(heading);
  const applied=EQ.summaryCharacter(EQ.characterId,PL.state.plan,PL.state.appliedSummary);
  box.append(EQ.summaryCard(applied,"当前已应用配置",true));
  if(PL.draftChanged())box.append(UI.el("p","本次草稿尚未应用；返回会保留草稿，磁盘文件尚未更新。","eq-draft-notice"));
  const nav=UI.el("div",null,"eq-toolbar");nav.append(UI.field("当前角色",UI.select(DS.characters(plan.manual_template,plan),EQ.characterId,id=>{
    EQ.characterId=id;EQ.render();})));
  [["gear",EQ.isPanel()?"武器与套装":"装备"],["basis",EQ.isPanel()?"初始面板":"基础与修正"],["effects","效果处理"],["passives","固有天赋"],["tuning","轴微调与增益关联"]].forEach(([key,label])=>
    nav.append(UI.button(label,()=>{EQ.section=key;EQ.render();},EQ.section===key?"active":"")));
  box.append(nav);const content=UI.el("div",null,"eq-content");box.append(content);
  if(!EQ.input() && ["gear","basis"].includes(EQ.section)){
    content.append(UI.el("p",EQ.panelNote),UI.button("启用初始面板与套装选择",()=>{
      plan.equipment_inputs[EQ.characterId]=EQ.blankPanelInput();PL.touch();EQ.render();}));
  }else if(EQ.section==="gear")EQ.renderGear(content);
  else if(EQ.section==="basis"){if(EQ.isPanel())EQ.renderPanel(content);else EQ.renderBasis(content);}
  else if(EQ.section==="effects")EF.render(content);
  else if(EQ.section==="passives"&&window.PB)PB.render(content);
  else EF.renderTuning(content);
  const foot=UI.el("div",null,"eq-toolbar eq-actions");foot.append(UI.button("预览变化",()=>PL.preview()),
    UI.button("查看并应用新资料",()=>PL.preview(true)),
    UI.button(PL.state.report?.state==="ready"?"应用并返回":"应用待完善方案并返回",PL.apply),
    UI.button("放弃本次装备修改",PL.discard,"danger"));
  box.append(foot);const preview=UI.el("div");preview.id="equipment-preview";box.append(preview);EQ.renderPreview();box.scrollTop=scroll;
};
EQ.renderPreview=()=>{const box=$("#equipment-preview");if(!box)return;box.innerHTML="";
  if(EQ.busy){box.append(UI.el("p","正在核验资料、面板与效果…"));return;}
  if(EQ.error){box.append(UI.error(EQ.error),UI.details("错误定位",EQ.error.details||[]));return;}
  const r=PL.state.report;if(!r){box.append(UI.el("p","预览尚未生成或已失效。"));return;}
  box.append(UI.el("h4",r.state==="ready"?"输入已就绪":"待完善输入（可保存草稿）"));
  const summary=r.equipment_summary?.characters?.find(c=>c.actor_id===EQ.characterId);
  if(summary)box.append(EQ.summaryCard(summary,"本次草稿预览",true));
  Object.entries(r.contribution_report?.characters||{}).forEach(([id,values])=>{
    const old=(PL.state.plan?.resolved_template||RT.tpl())?.characters?.find(c=>c.id===id)?.stats||{};
    const card=UI.el("section",null,"eq-card"),table=UI.el("table");card.append(UI.el("h4",`${DS.character(id,EQ.plan().manual_template,EQ.plan())} 面板预览`));
    const panel=EQ.plan().equipment_inputs[id]?.mode==="panel";
    const header=UI.el("tr");["属性","应用前","计算基础值",panel?"初始面板":"静态组合阶段","组合后面板"].forEach(v=>header.append(UI.el("th",v)));table.append(header);
    const panels=[old,values.base||{},values.static||{},values.final||{}].map(EQ.flatStats);
    const keys=[...new Set(panels.flatMap(p=>Object.keys(p)))];keys.forEach(key=>{
      const row=UI.el("tr");[DS.label("stats",key),...panels.map(p=>EQ.statValue(key,p[key]))].forEach(v=>row.append(UI.el("td",v)));table.append(row);});
    card.append(UI.el("p",panel?EQ.panelNote+" 条件增益按时间轴生效。":"计算基础攻击力已包含武器白值；组合后面板不代表轴内所有增益同时生效。","hint"),table);
    const sourceRows=r.contribution_report?.equipment_sources?.[id]?.base_sources||[];
    card.append(EQ.contributionsTable([...sourceRows.map(v=>({...v,operation:"base"})),...(values.contributions||[])]));box.append(card);
  });
  (r.missing_inputs||[]).forEach(m=>box.append(UI.button(DS.prose(m.message,"待完善输入，展开错误定位查看详情"),()=>{
    EQ.section=m.action==="review_passive_source"?"passives":m.action==="edit_basis"?"basis":m.action==="choose_equipment"?"gear":m.action==="select_hits"?"tuning":"effects";EQ.render();},"eq-issue")));
  box.append(UI.details("待完善输入定位",r.missing_inputs||[]),UI.details("面板贡献、基础值与来源",r.contribution_report||{}));
  if((r.match_previews||[]).length)box.append(UI.details("默认与微调匹配预览",r.match_previews));
  if((r.warnings||[]).length)box.append(UI.details("假设与提示",r.warnings));
};
EQ.renderPanel=(box,char=EQ.plan().manual_template.characters.find(c=>c.id===EQ.characterId),input=EQ.input(),change=PL.touch)=>{
  box.append(UI.el("p",EQ.panelNote,"hint"));
  if(window.EW){
    box.append(UI.el("p",`${char.level} 级 · ${input.panel_basis_mode==="manual"||!input.character_ref?"手动基础值":"数据库基础值"}。当前为已应用面板摘要；等级、角色绑定、基础白值与成品面板统一在“装备与初始面板”中修改。`,"hint"),
      DS.statRows(char.stats),UI.button("打开装备与初始面板",()=>{EQ.section="gear";return PL.begin(char.id);}));
    return;
  }
  const line=input.data_version?.version_line||DV.state.versionLine;
  box.append(UI.field("数据库角色（核对技能来源，保留给定面板）",UI.select([{value:"",label:"尚未绑定"},
    ...(DS.catalogs.get(line)?.characters||[]).map(c=>({value:String(c.id),label:c.display_name||c.name}))],
    input.character_ref?String(input.character_ref.db_character_id):"",v=>{
      input.character_ref=v?{db_character_id:Number(v),version_line:line}:null;change();})));
  const grid=UI.el("div",null,"eq-grid"),stats=char.stats;
  grid.append(UI.field("角色等级",UI.input(char.level,v=>{char.level=v;change();},true)));
  [["atk","攻击力"],["hp","生命值"],["def","防御力"],["base_atk","基础攻击力"],
    ["base_hp","基础生命值"],["base_def","基础防御力"],["em","元素精通"],
    ["crit_rate","暴击率 %",true],["crit_dmg","暴击伤害 %",true],["energy_recharge","元素充能效率 %",true]]
    .forEach(([key,label,pct])=>grid.append(UI.field(label,UI.input(stats[key]==null?null:stats[key]*(pct?100:1),v=>{
      if(v==null)delete stats[key];else stats[key]=v/(pct?100:1);change();},true))));
  box.append(grid,UI.el("h4","伤害加成"));
  const bonuses=UI.el("div",null,"eq-grid");
  ["all","physical","pyro","hydro","electro","cryo","anemo","geo","dendro"].forEach(element=>{
    bonuses.append(UI.field(`${DS.label("stats",`dmg_bonus:${element}`)} %`,UI.input(stats.dmg_bonus?.[element]==null?null:stats.dmg_bonus[element]*100,v=>{
      if(v==null){if(stats.dmg_bonus)delete stats.dmg_bonus[element];}else (stats.dmg_bonus ||= {})[element]=v/100;change();},true)));});
  box.append(bonuses,UI.field("初始面板来源",UI.area(DS.prose(input.source_refs?.find(s=>s.kind==="manual")?.note||""),v=>{
    input.source_refs=[...(input.source_refs||[]).filter(s=>s.kind!=="manual"),{kind:"manual",note:v}];change();})),
    UI.details("面板来源明细",input.source_refs));
};
EQ.renderBasis=box=>{const input=EQ.input(),basis=input.basis;
  let dbid=input.character_ref?.db_character_id??null;
  const bind=UI.el("div",null,"eq-grid");bind.append(UI.field("数据库角色",UI.select([{value:"",label:"请选择角色"},
    ...(DS.catalogs.get(DV.state.versionLine)?.characters||[]).map(c=>({value:c.id,label:c.display_name||c.name}))],dbid,v=>{dbid=v?Number(v):null;})),
    UI.field("角色等级",UI.input(input.level,v=>{input.level=v;PL.touch();},true)),
    UI.button("从角色资料填入基础",async()=>{const draft=EQ.plan();const req=PL.state.draftRevision;
      const r=await callApi("prepare_character_input",dbid,input.level,DV.state.versionLine);
      if(draft!==EQ.plan()||req!==PL.state.draftRevision)return;if(r.code){UI.error(r);return;}
      const prepared=r.data.input||r.data;
      EQ.plan().equipment_inputs[EQ.characterId]={...prepared,weapon:input.weapon,artifacts:input.artifacts,
        manual_contributions:input.manual_contributions};PL.touch();EQ.render();}));box.append(bind);
  box.append(UI.el("p",`基础来源：${basis.mode==="database"?"角色数据库":"人工输入"}。最终成品面板不能填作裸基础。`));
  const grid=UI.el("div",null,"eq-grid");
  [["character_base_atk","角色基础攻击",false],["character_base_hp","角色基础生命",false],
    ["character_base_def","角色基础防御",false],["crit_rate","基础暴击率 %",true],["crit_dmg","基础暴伤 %",true],
    ["energy_recharge","基础充能 %",true],["em","基础精通",false]].forEach(([key,label,pct])=>grid.append(UI.field(label,
      UI.input(basis[key]==null?null:basis[key]*(pct?100:1),v=>{basis[key]=v==null?null:v/(pct?100:1);
        basis.mode="manual";PL.touch();},true))));box.append(grid);
  box.append(UI.field("人工依据",UI.area(DS.prose(basis.source_refs?.find(s=>s.kind==="manual")?.note||""),v=>{
    basis.mode="manual";basis.source_refs=[{kind:"manual",note:v}];PL.touch();})));
  EQ.contributions(box,"角色突破贡献",basis.ascension_contributions ||= [],"ascension");
  EQ.contributions(box,"额外静态修正",input.manual_contributions ||= [],"manual");
  box.append(UI.details("基础来源明细",basis.source_refs));
  const plan=EQ.plan(),actor=EQ.characterId,management=UI.el("div",null,"eq-toolbar");management.append(UI.el("span","角色装备管理"));
  EQ.attachCommandMenu(management,"角色装备管理操作",()=>[{id:"detach",label:"解除装备管理并固化本次面板",danger:true,run:()=>EQ.withMenuTarget(plan,actor,"角色装备管理",current=>current===input?{input:current}:null,async()=>{
    if(!await PL.preview()||PL.state.report?.state!=="ready"){setStatus("请先完善输入并生成已就绪预览");return;}
    if(EQ.plan()!==plan||EQ.characterId!==actor){setStatus("角色装备草稿已变化，请重新打开操作菜单");return;}
    if(!window.confirm("将此角色的本次面板与相关派生值固化为手工输入？请核对返回的冻结说明，其他角色可继续换装。"))return;
    const request=++PL.state.request,revision=PL.state.draftRevision;
    const r=await callApi("detach_character",UI.clone(EQ.plan()),EQ.characterId);
    if(request!==PL.state.request||revision!==PL.state.draftRevision||EQ.plan()!==plan||EQ.characterId!==actor)return;
    if(r.code){UI.error(r);return;}PL.state.draft=r.data.plan;PL.state.draftRevision++;
    PL.state.report=r.data;EQ.lastReport=r.data;EQ.render();PL.renderStatus();
  })}]);box.append(management);
};
EQ.contributions=(box,title,items,collection=items===EQ.input()?.basis?.ascension_contributions?"ascension":"manual")=>{const plan=EQ.plan(),actor=EQ.characterId;
  box.append(UI.el("h4",title));items.forEach(c=>{const row=UI.el("div",null,"eq-grid");
  row.append(UI.field("属性",UI.select(DS.options("stats",EF.stats),c.stat,v=>{c.stat=v;PL.touch();})),
    UI.field("操作",UI.select([{value:"flat",label:"加算"},{value:"percent",label:"基础比例（攻击、生命、防御）"}],c.operation,v=>{c.operation=v;PL.touch();})),
    UI.field("值（比例用小数）",UI.input(c.value,v=>{c.value=v;PL.touch();},true)),
    UI.field("依据",UI.input(DS.prose(c.source_refs?.[0]?.note||""),v=>{c.source_refs=[{kind:"manual",note:v}];PL.touch();})));
  const key=c.contribution_id?{name:"contribution_id",value:c.contribution_id}:null;
  EQ.attachCommandMenu(row,`${title}操作`,()=>[{id:"remove",label:`删除${title}项目`,danger:true,run:()=>EQ.withMenuTarget(plan,actor,title,input=>{
    const rows=collection==="ascension"?input?.basis?.ascension_contributions:input?.manual_contributions;return EQ.uniqueRow(rows,c,key);},located=>{
      located.rows.splice(located.index,1);PL.touch();EQ.render();})}]);box.append(row);});
  box.append(UI.button(`添加${title}`,()=>{items.push({contribution_id:UI.id("contribution"),stat:"atk",operation:"flat",value:0,
    source_refs:[{kind:"manual",note:""}]});PL.touch();EQ.render();}));};
EQ.renderGear=box=>{const input=EQ.input(),line=input.character_ref?.version_line||DV.state.versionLine;
  box.append(UI.details("装备资料版本",input.data_version),UI.el("p",EQ.isPanel()?EQ.panelNote:"资料页中可搜索并选入当前方案；数值输入为本方案理论配装。"));
  const weapon=UI.el("section",null,"eq-card");weapon.append(UI.el("h4","武器"));box.append(weapon);
  const w=input.weapon;let wid=w?.weapon_id??null;
  weapon.append(UI.button("选择或更换武器",()=>EQ.pickData("weapon")));
  const rawWeapon=DS.raw("按武器编号指定",{weapon_id:wid});
  rawWeapon.append(UI.field("武器编号",UI.input(wid,v=>{wid=v;},true)),UI.button("按编号选择",()=>EQ.chooseWeapon(wid)));weapon.append(rawWeapon);
  if(w){EQ.loadDetail("weapon",w.weapon_id,line);const d=EQ.getDetail("weapon",w.weapon_id,line);
    if(d?.error)weapon.append(DS.textBlock(d.error));
    if(d?.weapon){weapon.append(UI.el("p",ED.name(d.weapon)));
      if(!EQ.isPanel())weapon.append(UI.field("等级",UI.input(w.level,v=>{w.level=v;PL.touch();},true)),
      UI.field("突破",UI.select([{value:"",label:"请选择"},...d.weapon.available_states.map(s=>({value:s.ascension,label:`${s.ascension} (${s.min_level}–${s.max_level}级)`}))],
        w.ascension,v=>{w.ascension=v===""?null:Number(v);PL.touch();})));
      if(EQ.isPanel())weapon.append(UI.el("p","武器用于展示与被动效果；白值、副属性已包含在初始面板中。","hint"));
      d.effects.forEach(g=>{let ref=w.refinements.find(r=>r.source_index===g.source_index);
        if(!ref)return;weapon.append(UI.field(`精炼 · 组 ${g.affix_group_id}`,UI.select([{value:"",label:"请选择"},...g.refinements.map(r=>({value:r.refinement,label:`精炼 ${r.refinement} 阶`}))],
          ref.refinement,v=>{ref.refinement=v?Number(v):null;PL.touch();EQ.render();})));
        const effect=g.refinements.find(e=>e.refinement===ref.refinement);if(effect)weapon.append(ED.record(effect));});}
  }
  if(EQ.isPanel()){EQ.renderSets(box,line);return;}
  Object.entries(ED.slots).forEach(([slot,label])=>{const a=input.artifacts[slot],card=UI.el("section",null,"eq-card"),plan=EQ.plan(),actor=EQ.characterId;
    card.append(UI.el("h4",label));box.append(card);let aid=a?.artifact_id??null;
    const rawArtifact=DS.raw("按单件编号指定",{artifact_id:aid});
    rawArtifact.append(UI.field("单件编号",UI.input(aid,v=>{aid=v;},true)),UI.button("按编号选择",()=>EQ.chooseArtifact(slot,aid)));card.append(rawArtifact);
    card.append(UI.button("选择或更换此部位",()=>EQ.pickData("artifact",slot)));
    EQ.attachCommandMenu(card,`${label}操作`,()=>[{id:"empty",label:"标记为本方案空槽",danger:true,run:()=>EQ.withMenuTarget(plan,actor,label,current=>
      current?.artifacts?.[slot]===a?{input:current}:null,located=>{located.input.artifacts[slot]={state:"empty",reason:"用户明确本方案未装备"};PL.touch();EQ.render();})}]);
    if(!a){card.append(UI.el("p","尚未指定"));return;}if(a.state==="empty"){card.append(UI.field("空槽原因",UI.input(DS.prose(a.reason||""),v=>{a.reason=v;PL.touch();})));return;}
    EQ.loadDetail("artifact",a.artifact_id,line);const d=EQ.getDetail("artifact",a.artifact_id,line);
    if(d?.error){card.append(DS.textBlock(d.error));return;}if(!d?.artifact)return;
    card.append(UI.el("p",`${ED.name(d.artifact)} · ${d.artifact.rarity}星 · 上限 +${d.artifact.max_level}`),
      UI.field("强化",UI.input(a.level,v=>{a.level=v;PL.touch();EQ.render();},true)));
    const key=`main:${d.main_prop_query.depot_id}:${line}`;
    if(!EQ.cache.has(key)&&!EQ.loading.has(key)){EQ.loading.add(key);const draft=EQ.plan(),epoch=EQ.detailEpoch;callApi("artifact_main_props",d.main_prop_query.depot_id,"",line,1,100).then(r=>{
      if(draft!==EQ.plan()||epoch!==EQ.detailEpoch)return;EQ.loading.delete(key);EQ.cache.set(key,r.code?{error:r.message}:r.data);EQ.render();});}
    const options=EQ.cache.get(key)?.items||[];
    card.append(UI.field("主词条",UI.select([{value:"",label:"请选择"},...options.map(p=>({value:p.id,label:`${p.prop_name} #${p.id}`}))],a.main_prop_id,v=>{
      a.main_prop_id=v?Number(v):null;PL.touch();EQ.render();})));
    const mainKey=`stat:${a.artifact_id}:${a.level}:${a.main_prop_id}:${line}`;
    if(a.level!=null&&a.main_prop_id!=null&&!EQ.cache.has(mainKey)&&!EQ.loading.has(mainKey)){
      EQ.loading.add(mainKey);const draft=EQ.plan(),epoch=EQ.detailEpoch;callApi("artifact_main_stats",a.artifact_id,a.level,a.main_prop_id,line).then(r=>{
        if(draft!==EQ.plan()||epoch!==EQ.detailEpoch)return;EQ.loading.delete(mainKey);EQ.cache.set(mainKey,r.code?{error:r.message}:r.data);EQ.render();});}
    const main=EQ.cache.get(mainKey);
    card.append(UI.el("p",main?.stat?`当前等级主词条：${main.stat.prop_name} ${ED.unit(main.stat.value,main.stat.unit)}`:
      main?.error?DS.prose(main.error,"主词条数值暂不可用"):"主词条数值：待选择或读取"));
    EQ.substats(card,a);card.append(UI.details("配置来源",d.artifact.source_refs||[]));
  });
};
EQ.pickSet=(index,count)=>{if(index===1&&!EQ.input()?.artifact_sets?.length){setStatus("请先选择第一套两件套");return;}
  EQ.setPick={index,count};EQ.pickData("set");};
EQ.setSelection=(detail,count=4,index=0)=>{const input=EQ.input(),setId=detail.artifact_set.id;
  if(!EQ.isPanel(input)){setStatus("请先启用初始面板与套装选择");return;}
  if(![2,4].includes(count)||![0,1].includes(index)){setStatus("请选择4件套或两组2件套");return;}
  if(input.data_version&&JSON.stringify(input.data_version)!==JSON.stringify(detail.data_version)){
    setStatus("方案与所选资料版本不同，请先预览应用新资料");return;}
  const current=input.artifact_sets||[];
  if(count===2&&index===1&&!current.length){setStatus("请先选择第一套两件套");return;}
  const next=count===4?[{set_id:setId,count:4}]:current.slice(0,2).map(s=>({set_id:s.set_id,count:2}));
  if(count===2)next[index]={set_id:setId,count:2};
  if(new Set(next.map(s=>s.set_id)).size!==next.length){setStatus("2+2需要选择两个不同套装；同套请选择4件套");return;}
  input.artifact_sets=next;input.data_version=detail.data_version;
  const line=input.character_ref?.version_line||DV.state.versionLine;
  EQ.cache.set(`artifact_set:${setId}:${line}`,detail);EQ.setPick=null;PL.touch();EQ.section="gear";EQ.render();
  setStatus("套装已加入草稿；初始面板保持给定值，请核对它已包含所选套装的常驻属性。");
};
EQ.renderSets=(box,line)=>{const input=EQ.input(),card=UI.el("section",null,"eq-card");
  card.append(UI.el("h4","圣遗物套装"),UI.el("p","直接选择4件套或2+2；两件套常驻属性已计入初始面板，条件效果在效果处理中确认。","hint"));
  const toolbar=UI.el("div",null,"eq-toolbar");toolbar.append(UI.button("选择4件套",()=>EQ.pickSet(0,4)),
    UI.button("选择第一套（2件）",()=>EQ.pickSet(0,2)),UI.button("选择第二套（2件）",()=>EQ.pickSet(1,2)));card.append(toolbar);
  if(!input.artifact_sets?.length)card.append(UI.el("p","尚未选择套装。可保留不计套装效果的配置。"));
  (input.artifact_sets||[]).forEach(selected=>{EQ.loadDetail("artifact_set",selected.set_id,line);
    const detail=EQ.getDetail("artifact_set",selected.set_id,line),entry=UI.el("article",null,"eq-card");
    entry.append(UI.el("h4",`${detail?.artifact_set?ED.name(detail.artifact_set):"套装资料待核验"} · ${selected.count}件`));
    const plan=EQ.plan(),actor=EQ.characterId,locate=current=>EQ.uniqueRow(current?.artifact_sets,selected,null);
    EQ.attachCommandMenu(entry,"套装操作",()=>[
      {id:"replace",label:"更换此套装",run:()=>EQ.withMenuTarget(plan,actor,"套装",locate,located=>EQ.pickSet(located.index,located.row.count===4?4:2))},
      {id:"remove",label:"移除此套装",danger:true,run:()=>EQ.withMenuTarget(plan,actor,"套装",locate,located=>{located.rows.splice(located.index,1);PL.touch();EQ.render();})},
    ]);
    if(detail?.error)entry.append(DS.textBlock(detail.error));
    (detail?.effects||[]).filter(e=>e.required_count<=selected.count).forEach(e=>{
      entry.append(UI.el("h5",`${e.required_count}件效果`),e.effect?ED.record(e.effect):UI.el("p","效果原文待补"));});
    entry.append(UI.details("套装选择与来源",{...selected,source_refs:detail?.artifact_set?.source_refs||[]}));card.append(entry);});
  box.append(card);
};
EQ.chooseWeapon=async id=>{const input=EQ.input(),draft=EQ.plan(),line=input.character_ref?.version_line||DV.state.versionLine;
  const req=PL.state.draftRevision,r=await callApi("weapon_detail",id,line);if(draft!==EQ.plan()||req!==PL.state.draftRevision)return;
  if(r.code){UI.error(r);return;}const d=r.data,old=input.weapon;
  const level=old?.level??1,ascension=old?.ascension??0;
  const valid=(d.weapon.available_states||[]).some(s=>s.ascension===ascension&&level>=s.min_level&&level<=s.max_level);
  input.weapon={weapon_id:id,...(EQ.isPanel()?{}:{level:valid?level:null,ascension:valid?ascension:null}),selected_by:"user",
    refinements:d.effects.map(g=>{const prior=old?.refinements?.length===1&&d.effects.length===1?old.refinements[0].refinement:null;
      return {source_index:g.source_index,affix_group_id:g.affix_group_id,refinement:prior!=null?
        (g.refinements.some(r=>r.refinement===prior)?prior:null):g.refinements[0]?.refinement};})};
  input.data_version ||= d.data_version;EQ.cache.set(`weapon:${id}:${line}`,d);PL.touch();EQ.render();};
EQ.chooseArtifact=async(slot,id)=>{const input=EQ.input(),draft=EQ.plan(),line=input.character_ref?.version_line||DV.state.versionLine;
  const req=PL.state.draftRevision,r=await callApi("artifact_detail",id,line);if(draft!==EQ.plan()||req!==PL.state.draftRevision)return;
  if(r.code){UI.error(r);return;}const d=r.data;if(d.artifact.equip_type!==slot){setStatus("此单件不属于当前槽位");return;}
  const p=await callApi("artifact_main_props",d.main_prop_query.depot_id,"",line,1,100);
  if(draft!==EQ.plan()||req!==PL.state.draftRevision)return;if(p.code){UI.error(p);return;}
  const old=input.artifacts[slot],oldDetail=old?.artifact_id?EQ.getDetail("artifact",old.artifact_id,line):null;
  const oldOptions=oldDetail?EQ.cache.get(`main:${oldDetail.main_prop_query.depot_id}:${line}`)?.items:[];
  const prop=oldOptions?.find(x=>x.id===old.main_prop_id)?.prop_type;
  const candidates=prop?p.data.items.filter(x=>x.prop_type===prop):p.data.items;
  input.artifacts[slot]={state:"equipped",artifact_id:id,level:(old?.level??0)<=d.artifact.max_level?(old?.level??0):null,
    main_prop_id:candidates.length===1?candidates[0].id:null,substats:old?.substats??null,selected_by:"user"};
  input.data_version ||= d.data_version;EQ.cache.set(`artifact:${id}:${line}`,d);EQ.cache.set(`main:${d.main_prop_query.depot_id}:${line}`,p.data);PL.touch();EQ.render();};
EQ.substats=(box,a)=>{const plan=EQ.plan(),actor=EQ.characterId;box.append(UI.el("h5","确定副词条累计值"));
  if(a.substats===null)box.append(UI.el("p","尚未指定副词条"));
  const properties=ED.caps?.properties||{};
  (a.substats||[]).forEach(s=>{const info=properties[s.prop_type]||{},pct=["percent","ratio"].includes(info.unit),row=UI.el("div",null,"eq-grid");
    row.append(UI.field("属性",UI.select([{value:"",label:"请选择"},...Object.entries(properties).map(([value,v])=>({value,label:v.name||DS.label("stats",value)}))],s.prop_type,v=>{
      s.prop_type=v;PL.touch();EQ.render();})),UI.field(`累计值${pct?" %":""}`,UI.input(s.value==null?null:s.value*(pct?100:1),v=>{s.value=v==null?null:v/(pct?100:1);PL.touch();},true)),
      UI.field("依据",UI.input(DS.prose(s.source?.note||""),v=>{s.source={kind:"manual",note:v};PL.touch();})));
    EQ.attachCommandMenu(row,"副词条操作",()=>[{id:"remove",label:"删除此副词条",danger:true,run:()=>EQ.withMenuTarget(plan,actor,"副词条",input=>{
      for(const artifact of Object.values(input?.artifacts||{})){if(artifact===a){const located=EQ.uniqueRow(artifact.substats,s,null);if(located)return located;}}return null;},located=>{
        located.rows.splice(located.index,1);PL.touch();EQ.render();})}]);box.append(row);});
  const actions=UI.el("div",null,"eq-toolbar");actions.append(UI.button("添加副词条",()=>{(a.substats ||= []).push({prop_type:"",value:null,source:{kind:"manual",note:"本方案理论总值"}});PL.touch();EQ.render();}));
  const locateArtifact=input=>Object.values(input?.artifacts||{}).includes(a)?{artifact:a}:null;
  EQ.attachCommandMenu(actions,"副词条列表操作",()=>[
    {id:"none",label:"明确不计副词条",danger:true,run:()=>EQ.withMenuTarget(plan,actor,"副词条列表",locateArtifact,located=>{located.artifact.substats=[];PL.touch();EQ.render();})},
    {id:"clear",label:"清空，稍后重填",danger:true,run:()=>EQ.withMenuTarget(plan,actor,"副词条列表",locateArtifact,located=>{located.artifact.substats=null;PL.touch();EQ.render();})},
  ]);box.append(actions);};

EQ.flatStats=stats=>Object.fromEntries(Object.entries(stats||{}).flatMap(([key,value])=>
  key==="dmg_bonus"?Object.entries(value).map(([element,v])=>[`dmg_bonus:${element}`,v]):[[key,value]]));
EQ.statValue=(key,value,operation)=>value==null?"未提供":ED.unit(value,
  operation==="percent"||["crit_rate","crit_dmg","energy_recharge","charge_efficiency"].includes(key)||key.startsWith("dmg_bonus:")?"ratio":"flat");
EQ.panelStats=stats=>{const box=UI.el("div");Object.entries(EQ.flatStats(stats)).forEach(([key,value])=>
  box.append(UI.el("p",`${DS.label("stats",key)}：${EQ.statValue(key,value)}`)));return box;};
EQ.summaryCharacter=(id,plan=PL.state.plan,summary=PL.state.appliedSummary)=>{
  const found=plan?.equipment_inputs?summary?.characters?.find(c=>c.actor_id===id):null;if(found)return found;
  const template=plan?.manual_template||(plan?.characters?plan:RT.tpl()),gear=plan?.equipment_inputs?.[id],char=template?.characters?.find(c=>c.id===id);
  const panel=gear?.mode==="panel";
  return {actor_id:id,display_name:DS.character(id,template,plan),level:panel?char?.level:gear?.level??char?.level,
    input_mode:panel?"panel":gear?"equipment":"manual_resolved",source_status:"unverified",
    source_refs:UI.clone(gear?.source_refs||[]),panel_policy:gear?.panel_policy,
    weapon:gear?.weapon?{...UI.clone(gear.weapon),state:"selected",display_name:"武器资料待核验"}:{state:"not_provided"},
    artifact_slots:panel?[]:Object.entries(ED.slots).map(([slot,slot_label])=>({slot,slot_label,
      ...UI.clone(gear?.artifacts?.[slot]||{state:gear?"not_specified":"not_provided"})})),
    artifact_sets:panel?(gear.artifact_sets||[]).map(s=>({...UI.clone(s),display_name:"套装资料待核验",effects:[]})):[],
    effect_statuses:[],panel_stages:panel?{static:UI.clone(char?.stats||{})}:gear?{}:{final:char?.stats||{}},missing_inputs:[]};
};
EQ.summaryCard=(char,title,detail=false)=>{const box=UI.el("section",null,"eq-card equipment-summary");
  if(!char)return box;
  box.append(UI.el("h4",title||DS.prose(char.display_name,"角色中文名待补")));
  if(char.input_mode==="manual_resolved"){
    box.append(UI.el("p","数值快照：装备来源未提供。武器、圣遗物及其贡献无法从面板还原。","hint"));
    if(detail)box.append(DS.statRows(char.panel_stages?.final));return box;}
  const panel=char.input_mode==="panel";
  if(panel)box.append(UI.el("p","初始面板模式：常驻属性已包含，条件增益按时间轴生效。","hint"));
  box.append(UI.el("p",`角色等级：${char.level??"未指定"} · 资料状态：${char.source_status==="unverified"?"尚未核验":DS.label("states",char.source_status)}`));
  const w=char.weapon||{},refs=(w.refinements||[]).map(r=>
    `${w.refinements.length>1?`效果槽 ${r.source_index}：`:""}精炼 ${r.refinement??"未指定"} 阶`).join("；");
  box.append(UI.el("p",w.state==="selected"?`武器：${DS.prose(w.display_name,"武器中文名待补")}${panel?"":` · ${w.level??"未指定"}级 · 突破 ${w.ascension??"未指定"}`} · ${refs||"无已指定效果槽"}`:panel?"武器：未选择（不计武器被动）":"武器：尚未指定"));
  box.append(UI.el("p",`圣遗物：${char.artifact_sets?.map(s=>`${DS.prose(s.display_name,"套装中文名待补")} ${s.count}件`).join(" + ")||(panel?"未选择套装效果":"套装计件尚未核验")}`));
  const slots=char.artifact_slots||[],specified=slots.filter(s=>["equipped","empty"].includes(s.state)).length;
  box.append(UI.el("p",`${panel?"":`五槽：已指定 ${specified}/5 · 已装备 ${slots.filter(s=>s.state==="equipped").length}/5 · `}待处理效果 ${char.pending_effects??"待核验"}项`));
  if(!detail)return box;
  const table=UI.el("table");const head=UI.el("tr");["部位","配置","主词条","累计副词条"].forEach(t=>head.append(UI.el("th",t)));table.append(head);
  slots.forEach(a=>{const tr=UI.el("tr"),stat=a.main_stat;
    const item=a.state==="empty"?`本方案空槽：${DS.prose(a.reason||"")}`:a.state==="equipped"?
      `${DS.prose(a.display_name,"单件资料待核验")} · ${a.rarity??"待核验"}星 · +${a.level??"未指定"}`:"尚未指定";
    const main=stat?`${stat.prop_name||"主属性"} ${ED.unit(stat.value,stat.unit)}`:a.state==="equipped"?"未核验":"—";
    const subs=a.state!=="equipped"?"—":a.substats==null?"未指定":!a.substats.length?"明确不计":a.substats.map(s=>{
      const prop=ED.caps?.properties?.[s.prop_type]||{};return `${prop.name||"属性中文名待补"} ${ED.unit(s.value,prop.unit)}`;}).join("；");
    [a.slot_label,item,main,subs].forEach(v=>tr.append(UI.el("td",v)));table.append(tr);});if(!panel)box.append(table);
  if(panel)box.append(UI.el("h5","给定初始面板"),EQ.panelStats(char.panel_stages?.static),UI.details("初始面板来源",char.source_refs||[]));
  (char.artifact_sets||[]).forEach(s=>{box.append(UI.el("h5",`${DS.prose(s.display_name,"套装中文名待补")}：${s.count}件`));
    (s.effects||[]).forEach(e=>box.append(UI.el("p",`${e.required_count}件效果 · ${DS.label("states",e.state)}：${DS.prose(e.effect?.desc_template||"原文待补")}`)));});
  (char.effect_statuses||[]).forEach(e=>{const node=UI.el("div",null,"equipment-effect-status");
    node.append(UI.el("p",`${DS.prose(e.effect?.name||"装备效果")} · ${e.state==="resolved"?"已计入":DS.label("states",e.state)}`));
    (e.parts||[]).forEach(p=>{const d=p.delivery||{};
      node.append(UI.el("p",`${DS.prose(p.label,"效果部件")} · ${DS.label("states",p.state)}${p.reason?` · ${DS.prose(p.reason)}`:""}`));
      if(d.kind==="buff")node.append(UI.el("p",`区间 ${d.start}–${d.end}秒 · ${DS.label("buff_zones",d.effect?.zone)} · 收益以逐事件回执为准`));});
    box.append(node);});
  box.append(UI.details(panel?"武器、套装与来源":"装备标识、词条与来源",char));return box;
};
EQ.contributionsTable=contributions=>{const table=UI.el("table",null,"equipment-contributions"),head=UI.el("tr");
  ["属性","计入方式","贡献值","依据"].forEach(v=>head.append(UI.el("th",v)));table.append(head);
  contributions.forEach(c=>{const row=UI.el("tr");[DS.label("stats",c.stat),c.operation==="base"?"已在计算基础中":DS.label("operations",c.operation),
    EQ.statValue(c.stat,c.value,c.operation),DS.prose(c.note||(c.source_refs||[]).map(s=>s.note||"数据库资料").join("；"),"来源原文见详情")].forEach(v=>row.append(UI.el("td",v)));table.append(row);});
  return table;
};
