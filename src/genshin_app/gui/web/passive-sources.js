"use strict";
window.PB={profile:"inherent-ordinary-swirl-bonus-v1",attributeProfile:"inherent-ordinary-swirl-attribute-v2"};
PB.commandMenu=()=>window.AppContextMenu?.forDocument?.(document,window)||null;
PB.menuError=error=>setStatus(`操作未完成：${String(error?.message||error||"未知错误")}`);
PB.openCommandMenu=(target,label,items)=>{const menu=PB.commandMenu();
  if(menu)return menu.open({target,trigger:target,returnFocus:target,label,items,onError:PB.menuError});
  const first=items.find(item=>item?.type!=="separator"&&item.danger!==true&&item.enabled!==false&&typeof item.run==="function");
  if(first)return first.run();setStatus("操作菜单暂不可用，请重新加载界面");return false;};
PB.bindCommandMenu=(target,label,items)=>PB.commandMenu()?.bind(target,{label,items,onError:PB.menuError});
PB.attachCommandMenu=(target,label,items)=>{const more=UI.button("⋯",()=>PB.openCommandMenu(more,label,items()),"rc-row-menu");more.setAttribute("aria-label",`${label}更多操作`);more.setAttribute("aria-haspopup","menu");target.tabIndex=0;
  target.append(more);PB.bindCommandMenu(target,label,items);return target;};
PB.withSource=(plan,actor,reference,run)=>{if(EQ.plan()!==plan){setStatus("固有来源所在的装备草稿已变化，请重新打开操作菜单");return false;}
  const matches=(plan.passive_sources||[]).map((row,index)=>({row,index})).filter(item=>item.row?.actor===actor&&(reference.id?item.row?.id===reference.id:item.row===reference.row));
  if(matches.length!==1){setStatus("固有来源已变化或移除，请重新打开操作菜单");return false;}return run(matches[0]);};
PB.sourceMenuItems=(plan,actor,row)=>{const reference={id:row?.id||null,row};return [{id:"edit",label:"查看或编辑固有天赋来源",run:()=>PB.withSource(plan,actor,reference,located=>PB.edit(actor,located.row.id))}];};
PB.caps=(profile=PB.profile)=>{const key=profile===PB.attributeProfile?"passive_attribute_source_contract":"passive_source_contract";return PL.state.caps?.[key]||PL.state.caps?.plan_contract?.[key];};
PB.record=(report,id)=>(report?.passive_compilations||[]).find(r=>r.id===id&&[PB.profile,PB.attributeProfile].includes(r.profile_id));
PB.confirmation=r=>r?.required_confirmation?.accepted_fingerprint;
PB.recordView=(box,r)=>{
  if(!r){box.append(UI.el("p","尚无当前固有天赋草稿的来源预览。"));return;}
  const desc=r.source?.descriptions||{},unlock=r.unlock?.status;
  if(desc.name?.text)box.append(UI.el("h4",DS.prose(desc.name.text,"固有天赋")));
  if(desc.description?.text)box.append(DS.textBlock(desc.description.text,"固有天赋原文"));
  box.append(UI.el("p",`自动增伤：${r.status==="ready"?"声明范围内就绪":"待完善"}；完整来源仅部分支持。`),
    UI.el("p",`突破检查：${({eligible_under_declared_progression:"声明达到来源门槛",locked:"未达到门槛",invalid:"等级与突破不一致",pending:"资料或声明待补"})[unlock]||"尚未完成"}。资料存在不等于已解锁。`),
    UI.el("p","提供者在扩散触发时取值，增伤计入受益角色自身快照；允许合格的后台反应触发，扩散伤害本身不吃该元素增伤。"));
  if(desc.unlock_description?.status==="pending")box.append(UI.el("p","此来源缺少解锁说明原文；数值门槛仍单独核对。"));
  for(const issue of r.issues||[])box.append(UI.el("p",issue.message||"固有来源待完善","eq-issue"));
  for(const rule of r.rules?.length?r.rules:r.preview_rules||[]){
    const factor=rule.effects?.[0]?.value?.factor,em=r.stat_basis?.panel_value;
    const value=r.profile_id===PB.attributeProfile?`每次反应读取非额外精通 × ${factor??"待核实系数"}（比例）；实际值见计算取样账`:
      typeof factor==="number"&&typeof em==="number"?(factor*em*100).toFixed(2)+"%":"按已声明固定精通取值";
    box.append(UI.el("p",`${DS.prose(rule.name_zh,"元素增伤")}：${value}，持续 ${rule.timing.duration} 秒${r.status==="ready"?"":"（未交付的预览）"}。`));
  }
  box.append(UI.details("培养条件、原文与参数",r.source),UI.details("精通口径与动作来源资格",{stat_basis:r.stat_basis,scope:r.scope}),
    UI.details("覆盖分支与待支持部分",{branches:r.branches,coverage:r.coverage_certificate}));
  if(r.attribute_evidence)box.append(UI.details("属性读取事实、参数与取样依据",r.attribute_evidence),UI.details("最终精通写者资格",r.final_attribute_sampling_qualification));
};
PB.renderReceipt=(box,report)=>{
  for(const record of report?.passive_compilations||[]){const card=UI.el("details",null,"eq-card");
    card.append(UI.el("summary","固有天赋来源与实际生效（只读）"));PB.recordView(card,record);box.append(card);}
};
PB.render=box=>{
  const plan=EQ.plan(),actor=EQ.characterId,gear=plan?.equipment_inputs?.[actor];
  box.append(UI.el("h4","固有天赋与突破"),UI.el("p","先读取该角色的原文与解锁条件，再选择来源。系统按实际反应生成受支持的增伤，不需要逐次填写生效区间。"));
  if(plan.plan_version!==3||!PB.caps()){box.append(UI.el("p","自动固有来源需要原生版本3与当前接口。"));return;}
  const progression=gear?.progression;
  box.append(UI.el("p",`当前突破声明：${Number.isInteger(progression?.ascension)?"第 "+progression.ascension+" 阶":"尚未提供"}。等级高低不会自动替代突破声明。`));
  if(gear?.character_ref)box.append(UI.button("读取并选择固有天赋",()=>PB.edit(actor)));
  else box.append(UI.el("p","请先在初始面板中绑定数据库角色，保留当前给定面板。已有来源仍可查看或移除。"));
  for(const row of plan.passive_sources||[])if(row.actor===actor){const card=UI.el("section",null,"eq-card");
    PB.recordView(card,PB.record(PL.state.report,row.id));PB.attachCommandMenu(card,"固有天赋来源操作",()=>PB.sourceMenuItems(plan,actor,row));box.append(card);}
};
PB.edit=(actor,id)=>{
  const original=EQ.plan(),gear=original?.equipment_inputs?.[actor];
  const existing=(original?.passive_sources||[]).find(r=>r.id===id&&r.actor===actor);
  let caps=PB.caps(existing?.scenario?.profile_id);
  if(original?.plan_version!==3||(!gear?.character_ref&&!existing)||!caps?.default_scenario){setStatus("请先绑定当前原生方案的数据库角色");return;}
  const row=UI.clone(existing||{id:UI.id("passive"),actor,
    proud_skill_id:null,constellation:null,scenario:caps.default_scenario,accepted_fingerprint:null});
  const progression=UI.clone(gear?.progression||{ascension:null,status:"pending",source:""});
  const baseline=JSON.stringify(PL.input(original)),live=PL.currentInputJson(),epoch=PL.state.sourceEpoch,path=RT.state.path,sha=PL.state.fileSha;
  const queryBox=UI.el("div"),candidatesBox=UI.el("div"),previewBox=UI.el("div"),errors=UI.el("div"),actions=UI.el("div");
  let closed=false,busy=false,request=0,revision=0,preview=null,candidates=[],query=null;
  const invalidate=()=>{if(closed)return true;closed=true;request++;return true;};
  const dialog=window.AppDialogKernel?.create?.({title:"固有天赋来源与角色培养",
    description:"当前自动范围为零命、90级且明确突破6阶，使用已声明的面板与属性来源；普通冰水扩散来自已核对的战技动作。不能为通过检查修改实际命座或面板。",
    className:"mc-dialog",onRequestClose:invalidate})||null;
  if(!dialog){const reason=window.AppDialogKernel?.getLastCreateResult?.()?.reason;
    setStatus(reason==="active_busy"?"当前窗口正在处理请求，请等待完成或主动关闭后再编辑固有天赋来源":"当前还有窗口正在处理，请先完成或关闭后再编辑固有天赋来源");return;}
  const current=()=>!closed&&EQ.plan()===original&&JSON.stringify(PL.input(original))===baseline&&PL.currentInputJson()===live
    &&PL.state.sourceEpoch===epoch&&RT.state.path===path&&PL.state.fileSha===sha;
  const valid=()=>{if(current())return true;setStatus("方案、装备草稿或数据库状态已变化，请重新打开固有来源编辑");return false;};
  const close=()=>{invalidate();return dialog.finish?.("cancel")??true;};
  const confirmable=r=>!!PB.confirmation(r)&&r.status!=="invalid"&&(r.issues||[]).every(i=>(caps.confirmable_issue_codes||[]).includes(i.code));
  const candidate=remove=>{const plan=UI.clone(PL.input(original));plan.passive_sources=(plan.passive_sources||[]).filter(r=>r.id!==row.id);
    if(!remove){plan.passive_sources.push(UI.clone(row));plan.equipment_inputs[actor].progression=UI.clone(progression);}return plan;};
  const refresh=()=>{dialog.setBusy?.(busy);previewBox.innerHTML="";errors.innerHTML="";actions.innerHTML="";
    previewBox.append(UI.el("p",busy?"正在核对固有来源与实际动作资格…":row.accepted_fingerprint?"已记录本次采用，应用时仍会重新读取。":"尚未采用；资料、培养、面板口径和场景须分别明确。"));
    PB.recordView(previewBox,preview?.revision===revision?preview.record:null);
    for(const [label,mode,disabled] of [["预览固有来源与实际资格","preview",false],["接受本次来源与声明","confirm",!confirmable(preview?.record)||preview?.revision!==revision],
      ["应用固有天赋草稿","apply",false]]){const b=UI.button(label,()=>run(mode));b.disabled=busy||disabled;actions.append(b);}
    if(existing){const b=UI.button("移除此固有来源",()=>run("remove"));b.disabled=busy;actions.append(b);}
    actions.append(UI.button("放弃并关闭",close));};
  const changed=()=>{row.accepted_fingerprint=null;revision++;request++;busy=false;preview=null;refresh();};
  const drawCandidates=()=>{candidatesBox.innerHTML="";
    if(!query){candidatesBox.append(UI.el("p","先读取角色固有天赋候选；已有选择也会在预览时重新核对。"));return;}
    const options=[{value:"",label:"请选择，系统不会自动采用第一条"},...candidates.map(c=>({value:String(c.proud_skill_id),
      label:`${DS.prose(c.name?.text||"固有天赋","固有天赋")} · ${c.threshold?.status==="ready"?"突破 "+c.threshold.required_ascension+" 阶":"门槛待核实"}`}))];
    candidatesBox.append(UI.field("采用的固有天赋",UI.select(options,row.proud_skill_id===null?"":String(row.proud_skill_id),v=>{row.proud_skill_id=v?Number(v):null;changed();})),
      UI.el("p","候选可查不代表都能自动执行；普通技能倍率10/13级不用于固有天赋记录等级。"),UI.details("角色固有天赋候选与缺项",query));};
  const read=async()=>{if(!valid())return;if(!gear?.character_ref){setStatus("当前未绑定数据库角色；可移除此来源，或返回面板先补齐引用。");return;}
    const payload={action:"passive_source_profile",character_id:gear.character_ref.db_character_id,version_line:gear.character_ref.version_line};
    if(row.skill_depot_id!==undefined)payload.skill_depot_id=row.skill_depot_id;
    const token=++request,version=revision;busy=true;refresh();let response;
    try{response=await callApi("query_runtime",UI.clone(payload));}catch(error){response={code:1,message:String(error)};}
    if(token!==request||version!==revision||!current()){if(!closed&&token===request){busy=false;dialog.setBusy?.(false);if(!current())valid();}return;}busy=false;refresh();queryBox.innerHTML="";
    const list=response.data?.candidates;
    if(response.code||!Array.isArray(list)||list.length>64||list.some(c=>!Number.isInteger(c.proud_skill_id))||new Set(list.map(c=>c.proud_skill_id)).size!==list.length){
      queryBox.append(UI.error(response.code?response:{code:1,message:"候选列表不完整或身份重复，未采用"}));return;}
    query=UI.clone(response.data);candidates=query.candidates;drawCandidates();};
  const run=async mode=>{
    if(busy||!valid())return;const remove=mode==="remove",shown=preview?.revision===revision?preview.record:null;
    if(!remove&&!gear){setStatus("角色面板已移除，请移除此来源或先恢复面板。");return;}
    if(!remove&&(!Number.isInteger(row.proud_skill_id)||row.proud_skill_id<1)){setStatus("先读取并明确选择固有天赋");return;}
    if(mode==="confirm"&&!confirmable(shown)){setStatus("请先完善场景、培养及来源资格，再接受当前预览");return;}
    const proposed=candidate(remove),fp=mode==="confirm"?PB.confirmation(shown):row.accepted_fingerprint,commitRow=UI.clone(row),commitProgression=UI.clone(progression);
    if(mode==="confirm"){proposed.passive_sources.find(r=>r.id===row.id).accepted_fingerprint=fp;commitRow.accepted_fingerprint=fp;}
    const token=++request,version=revision;busy=true;refresh();let response;
    try{response=await callApi("compose_equipment_plan",UI.clone(proposed));}catch(error){response={code:1,message:String(error)};}
    if(token!==request||version!==revision||!current()){if(!closed&&token===request){busy=false;dialog.setBusy?.(false);if(!current())valid();}return;}
    busy=false;const report=response.data,found=PB.record(report,row.id),record=found?.profile_id===row.scenario.profile_id?found:null;
    if(response.code||!report||!["ready","pending"].includes(report.state)||(!remove&&!record)){
      refresh();errors.append(UI.error(response.code?response:{code:1,message:"缺少可核验的固有来源回执"}));return;}
    preview={revision,record};
    if(!remove&&fp&&PB.confirmation(record)!==fp){row.accepted_fingerprint=null;revision++;preview.revision=revision;refresh();
      errors.append(UI.el("p","来源、面板或动作资格已变化，尚未应用；请核对新预览。","eq-error"));return;}
    if(mode==="confirm"){if(!confirmable(record)){refresh();errors.append(UI.el("p","仍有未知条件，未记录采用。","eq-error"));return;}
      row.accepted_fingerprint=fp;revision++;preview.revision=revision;refresh();return;}
    if(mode==="preview"){refresh();return;}
    original.passive_sources=(original.passive_sources||[]).filter(r=>r.id!==row.id);
    if(!remove){original.passive_sources.push(commitRow);original.equipment_inputs[actor].progression=commitProgression;}
    close();PL.touch();EQ.render();setStatus(remove?"已移除固有来源，未创建替代手工规则":"固有来源草稿已应用，请预览完整方案并保存。");
  };
  const choices=(label,key,values)=>UI.field(label,UI.select(values.map(([value,label])=>({value,label})),row.scenario[key]??"",v=>{row.scenario[key]=v||null;changed();}));
  const target=original.mechanics_model?.elemental_model?.target;
  const scopeBox=UI.el("div"),drawScope=()=>{
    scopeBox.innerHTML="";const dynamic=row.scenario.profile_id===PB.attributeProfile;
    scopeBox.append(choices(dynamic?"提供者非额外精通口径":"提供者固定精通口径","stat_basis",[["unknown","尚未明确"],dynamic?["attribute_ledger_nonextra","由已审核的属性账读取"]:["fixed_panel_nonextra_em_equals_total_em","明确与非额外精通相等"]]));
    if(dynamic)scopeBox.append(choices("同次反应取样资格","same_proc_em_policy",[["unknown","尚未明确"],["no_same_proc_em_changes","无尚未核验的同次精通变化"]]),
      UI.el("p","需要显式精通属性账v2及普通扩散v3；不会自动改写旧模型。实际读取的非额外精通限已核实的常量增益，同次回调与额外属性共享的取样资格另行检查。"));
  };
  dialog.appBody.append(UI.button("← 返回固有天赋",close),
    UI.field("技能组身份（多技能组时填写）",UI.input(row.skill_depot_id,v=>{if(v===null)delete row.skill_depot_id;else row.skill_depot_id=v;query=null;candidates=[];changed();drawCandidates();},true)),
    UI.button("读取角色固有天赋候选",read),queryBox,candidatesBox,
    UI.field("实际命座",UI.select([{value:"",label:"尚未明确"},...[0,1,2,3,4,5,6].map(n=>({value:String(n),label:`${n} 命`}))],row.constellation===null?"":String(row.constellation),v=>{row.constellation=v===""?null:Number(v);changed();})),
    UI.field("角色突破阶段",UI.select([{value:"",label:"尚未明确"},...[0,1,2,3,4,5,6].map(n=>({value:String(n),label:`第 ${n} 阶`}))],progression.ascension===null?"":String(progression.ascension),v=>{progression.ascension=v===""?null:Number(v);changed();})),
    UI.field("突破声明状态",UI.select([{value:"pending",label:"待核实"},{value:"assumed",label:"明确参考假设"},{value:"confirmed",label:"已核实声明"}],progression.status,v=>{progression.status=v;changed();})),
    UI.field("突破依据",UI.area(progression.source,v=>{progression.source=v;changed();})),
    choices("对应元素目标","target_id",[["","尚未明确"],...(target?.id?[[target.id,"当前唯一元素目标"]]:[])]),
    choices("原目标范围","origin_target_range",[["unknown","尚未明确"],["within_source_range","满足原始监听范围"]]),
    choices("全队受益范围","recipient_range",[["unknown","尚未明确"],["all_party_within_source_range","全队满足原始受益范围"]]),
    choices("窗口前的该源增伤","initial_source_effects",[["unknown","尚未明确"],["none","开始前没有活跃实例"]]),
    choices("各角色初始面板","recipient_bonus_policy",[["unknown","尚未明确"],["not_included","所有角色均未计入该条件增伤"]]),
    UI.field("属性取样模式",UI.select([{value:PB.profile,label:"旧版固定精通"},...(PB.caps(PB.attributeProfile)?[{value:PB.attributeProfile,label:"动态非额外精通属性账"}]:[])],row.scenario.profile_id,v=>{
      const next=PB.caps(v);if(!next?.default_scenario)return;caps=next;const previous=row.scenario;row.scenario=UI.clone(next.default_scenario);
      for(const key of Object.keys(row.scenario))if(!["profile_id","profile_version","target_state_profile","stat_basis","same_proc_em_policy"].includes(key)&&Object.hasOwn(previous,key))row.scenario[key]=UI.clone(previous[key]);
      changed();drawScope();
    })),scopeBox,
    choices("原子事件顺序","event_order_policy",[["unknown","尚未明确"],["declared_atomic_hit_order","已校准原子命中顺序"]]),
    choices("场景声明状态","status",[["pending","待核实"],["assumed","明确参考假设"],["confirmed","已核实用途"]]),
    UI.field("精通组成、初态及范围依据",UI.area(row.scenario.source,v=>{row.scenario.source=v;changed();})),previewBox,errors,actions);
  drawScope();drawCandidates();refresh();dialog.focusInitial?.();
};
