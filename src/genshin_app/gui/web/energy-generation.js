"use strict";
// Only source declarations are editable. Fresh Python compilation owns particles and dispatch.
window.EG={};
EG.caps=()=>PL.state.caps?.energy_generation_contract;
EG.sources=plan=>plan?.energy_generation_sources||[];
EG.records=report=>report?.energy_generation_compilations||[];
EG.commandMenu=()=>window.AppContextMenu?.forDocument?.(document,window)||null;
EG.menuError=error=>setStatus(`操作未完成：${String(error?.message||error||"未知错误")}`);
EG.openCommandMenu=(target,label,items)=>{
  const menu=EG.commandMenu();if(menu)return menu.open({target,trigger:target,returnFocus:target,label,items,onError:EG.menuError});
  const first=items.find(item=>item?.type!=="separator"&&item.danger!==true&&item.enabled!==false&&typeof item.run==="function");
  if(first)return first.run();setStatus("操作菜单暂不可用，请重新加载界面");return false;
};
EG.bindCommandMenu=(target,label,items)=>EG.commandMenu()?.bind(target,{label,items,onError:EG.menuError});
EG.withCurrentSource=(target,run)=>{
  const index=EG.sources(PL.state.plan).indexOf(target);
  if(index<0){setStatus("产球来源已变化，请重新打开操作菜单");return false;}
  return run(index);
};
EG.sourceMenuItems=row=>[{id:`edit-energy-source:${row.id||"unknown"}`,label:"编辑产球来源",run:()=>EG.withCurrentSource(row,index=>EG.edit(index))}];
EG.sourceRow=(content,row,index)=>{
  const label=`产球来源 ${index+1} 操作`,items=()=>EG.sourceMenuItems(row),container=UI.el("div",null,"eq-toolbar rc-definition-row");
  const more=UI.button("⋯",()=>EG.openCommandMenu(more,label,items()),"rc-row-menu");container.tabIndex=0;
  container.append(content,more);EG.bindCommandMenu(container,label,items);return container;
};
EG.confirmation=row=>row?.required_confirmation?.accepted_fingerprint;
EG.record=(report,id)=>EG.records(report).find(row=>row.id===id&&row.compiler===EG.caps()?.compiler);
EG.confirmable=row=>typeof EG.confirmation(row)==="string"&&/^[0-9a-f]{64}$/.test(EG.confirmation(row))&&
  (row.issues||[]).every(issue=>(EG.caps()?.confirmable_issue_codes||[]).includes(issue.code));
EG.preview=(box,row,plan=PL.state.plan)=>{
  if(!row){box.append(UI.el("p","读取当前来源后，核对技能命中、场景与两段时间。"));return;}
  const p=row.resource_projection||{},certificate=row.trigger_certificate||{},calibration=row.calibration||{};
  box.append(UI.el("h4","技能产球来源"),UI.el("p",`${row.status==="ready"?"本范围已就绪":"仍有待完善内容"}；采用有限参考模型，技能仅部分覆盖。`),
    UI.el("p",`每批 ${p.count??"待补"} 个${p.particle_type==="orb"?"元素晶球":p.particle_type==="particle"?"元素微粒":"能量产物"}；元素：${DS.label("elements",p.element,"待核实")}。`),
    UI.el("p",`命中后 ${calibration.spawn_delay_seconds??"待补"} 秒产球，再经过 ${calibration.travel_seconds??"待补"} 秒收取；到账按当时的前后台和充能效率计算。`),
    UI.el("p","当前参考模型不计受击停顿延长；未知场景、随机分支或控制关系不能靠确认按钮放行。"));
  if(certificate.isolation)box.append(UI.el("p",`系统按来源检查命中间隔是否大于 ${certificate.isolation.isolation_ticks} 微秒；这是参考模型的准入边界，并非技能冷却。`));
  const parents=row.parent_links||[];
  if(parents.length){const table=UI.el("table"),head=UI.el("tr");["来源动作","命中时点（秒）","命中声明"].forEach(v=>head.append(UI.el("th",v)));table.append(head);
    parents.slice(0,100).forEach(parent=>{const tr=UI.el("tr");[AE.actionName(parent.action_id,plan),parent.t??"待补",AE.landed(parent.landed)].forEach(v=>tr.append(UI.el("td",v)));table.append(tr);});box.append(table);
    if(parents.length>100)box.append(UI.el("p",`显示前100项，共${parents.length}项；完整映射见详情。`));}
  if(row.migration)box.append(UI.details("接管的旧产球与收取事件",row.migration));
  AE.issues(box,row.issues,"产球来源待办");box.append(UI.details("完整原文、参考依据与实际命中关系",row));
};
EG.renderReceipt=(box,report,plan=PL.state.plan)=>{
  for(const row of EG.records(report)){const card=UI.el("section",null,"eq-card");EG.preview(card,row,plan);box.append(card);}
  if(report?.energy_generation_summary){const card=UI.el("section",null,"eq-card");card.append(UI.el("h4","本次产球与收取"),
    UI.el("p","区分计划命中、实际产球、已到账和仍在途的能量。未执行的后续动作不会自动补齐。"));
    const names={planned_parent_hits:"计划来源命中",reported_parent_hits:"已报告来源命中",eligible_parent_hits:"具备产球资格的命中",missed_parent_hits:"未命中",offfield_parent_hits:"提供者不在前台",unverified_parent_hits:"未能验证的命中",unreported_parent_hits:"尚未报告的命中",spawned_batches:"已产球批次",spawned_particles:"已产生数量",credited_batches:"已收取批次",credited_particles:"已收取数量",in_flight_particles:"仍在途数量"};
    Object.entries(names).forEach(([key,name])=>{if(Object.hasOwn(report.energy_generation_summary,key))card.append(UI.el("p",`${name}：${report.energy_generation_summary[key]}`));});
    card.append(UI.details("本次执行统计",report.energy_generation_summary));box.append(card);}
};
EG.edit=index=>{
  if(PL.state.draft){setStatus("请先应用或放弃装备草稿");return;}
  const original=PL.state.plan,caps=EG.caps();
  if(original?.plan_version!==3||!caps?.default_source){setStatus("技能产球来源需要当前版本接口及原生版本3方案");return;}
  const profiles=AE.profiles(original).filter(p=>p.kind==="calibrated_single_contact_v1");
  if(index===undefined&&!profiles.length){setStatus("请先建立并核对单次战技命中档案");return;}
  const previous=EG.sources(original)[index];
  if(index!==undefined&&!previous)return;
  const row=UI.clone(previous||caps.default_source);if(!previous)row.id=UI.id("energy-source");
  const baseline=PL.currentInputJson(),epoch=PL.state.sourceEpoch,path=RT.state.path,sha=PL.state.fileSha;
  let closed=false,revision=0,request=0,shown=null;
  const current=()=>!closed&&PL.state.plan===original&&!PL.state.draft&&PL.currentInputJson()===baseline&&
    PL.state.sourceEpoch===epoch&&RT.state.path===path&&PL.state.fileSha===sha;
  const form=UI.el("div"),preview=UI.el("div"),errors=UI.el("div"),migrationBox=UI.el("div");
  const invalidate=()=>{if(closed)return true;closed=true;request++;return true;};
  const dialog=window.AppDialogKernel?.create?.({title:"技能命中自动产球",
    description:"声明技能命中后的产球、飞行与收取时点；系统读取球数与元素，并在计算时按实际前后台和充能效率结算。",
    className:"mc-dialog",onRequestClose:invalidate})||null;
  if(!dialog){setStatus("当前还有窗口正在处理，请先完成或关闭后再编辑产球来源");return;}
  const close=()=>{invalidate();return dialog.finish?.("cancel")??true;};
  const changed=()=>{revision++;request++;shown=null;row.accepted_fingerprint=null;preview.innerHTML="";EG.preview(preview,null);};
  const update=(object,key,value)=>{object[key]=value;changed();};
  const candidate=remove=>{const plan=UI.clone(original);plan.energy_generation_sources=EG.sources(plan).filter(s=>s.id!==row.id);
    if(!remove)plan.energy_generation_sources.push(UI.clone(row));return plan;};
  const run=async mode=>{
    if(!current()){setStatus("方案或数据库已变化，请重新打开产球编辑");return;}
    errors.innerHTML="";const remove=mode==="remove",prior=shown?.revision===revision?shown.record:null;
    if(mode==="confirm"&&!EG.confirmable(prior)){errors.append(UI.el("p","请先完善来源、命中与场景，再接受当前预览。","eq-error"));return;}
    const plan=candidate(remove),commitRow=UI.clone(row),accepted=mode==="confirm"?EG.confirmation(prior):row.accepted_fingerprint;
    if(mode==="confirm"){plan.energy_generation_sources.find(s=>s.id===row.id).accepted_fingerprint=accepted;commitRow.accepted_fingerprint=accepted;}
    const attempt=++request,version=revision;let response;
    try{response=await callApi("compose_equipment_plan",UI.clone(plan));}catch(error){response={code:1,message:String(error)};}
    if(attempt!==request||version!==revision||!current()){if(!closed)setStatus("旧产球来源响应已忽略，请重新读取");return;}
    const report=response.data,record=EG.record(report,row.id);
    if(response.code||!report||!["ready","pending"].includes(report.state)||(!remove&&!record)){
      errors.append(UI.error(response.code?response:{code:1,message:"接口未返回可核验的产球来源，未应用修改"}));return;}
    preview.innerHTML="";if(!remove)EG.preview(preview,record,plan);shown={revision,record};
    if(!remove&&accepted&&EG.confirmation(record)!==accepted){row.accepted_fingerprint=null;revision++;shown.revision=revision;
      errors.append(UI.el("p","来源或校准已变化，请核对新的预览后重新接受。","eq-error"));return;}
    if(mode==="preview")return;
    if(mode==="confirm"){
      if(!EG.confirmable(record)||record.status!=="ready"){errors.append(UI.el("p","仍有未支持或未知条件，未记录接受。","eq-error"));return;}
      row.accepted_fingerprint=accepted;revision++;shown.revision=revision;setStatus("已接受本次来源与场景；仍需应用并保存");return;
    }
    if(!remove&&accepted&&record.status!=="ready"){errors.append(UI.el("p","已接受的来源当前无法执行，未应用；请处理新出现的冲突。","eq-error"));return;}
    // Commit only the local declaration. Never copy service-generated rules/events into input.
    original.energy_generation_sources=EG.sources(original).filter(s=>s.id!==row.id);
    if(!remove)original.energy_generation_sources.push(commitRow);
    close();RC.touch();RC.render();setStatus(remove?"产球来源已移除；原生旧事件恢复按原规则校验，请重新计算":
      accepted?"产球来源已应用，请计算验证实际产球及充能循环":"未确认的产球草稿已应用，请完善后再计算");
  };
  const choices=(label,object,key,values)=>UI.field(label,UI.select(values.map(([value,label])=>({value,label})),object[key]??"",v=>update(object,key,v||null)));
  const boolean=(label,key)=>UI.field(label,UI.select([{value:"",label:"尚未明确"},{value:"true",label:"明确成立"},{value:"false",label:"明确不成立"}],AE.landedValue(row.scenario[key]),v=>update(row.scenario,key,v===""?null:v==="true")));
  const drawMigration=()=>{
    migrationBox.innerHTML="";
    const abilities=new Set(AE.bindings(original).filter(b=>b.profile_id===row.action_event_profile_id).map(b=>b.ability_id));
    const actions=new Set((original.rotation_model?.actions||[]).filter(a=>abilities.has(a.ability_id)).map(a=>a.id));
    const spawns=(original.rotation_model?.events||[]).filter(e=>e.type==="energy_spawn"&&(actions.has(e.action_id)||row.replaces_spawn_ids.includes(e.id)));
    if(spawns.length||row.replaces_spawn_ids.length)migrationBox.append(UI.el("h4","接管已有产球"),UI.el("p","仅勾选需要替换的旧产球；关联收取事件一并由系统核对。原生旧事件保留，解除来源后恢复原校验。"));
    spawns.forEach(event=>migrationBox.append(UI.check(`${event.t??"待定"} 秒 · ${AE.actionName(event.action_id,original)} · ${event.count??"待核实"} 个旧产球`,row.replaces_spawn_ids.includes(event.id),value=>{
      row.replaces_spawn_ids=row.replaces_spawn_ids.filter(id=>id!==event.id);if(value)row.replaces_spawn_ids.push(event.id);changed();drawMigration();})));
    row.replaces_spawn_ids.filter(id=>!spawns.some(e=>e.id===id)).forEach(id=>migrationBox.append(UI.el("p",`旧产球引用已失效：${id}`,"eq-error"),
      UI.button("移除这条失效接管声明",()=>{row.replaces_spawn_ids=row.replaces_spawn_ids.filter(v=>v!==id);changed();drawMigration();})));
    if(spawns.length)migrationBox.append(UI.details("旧事件完整原文",spawns));
  };
  form.append(UI.field("关联的战技命中档案",UI.select(AE.options(profiles,row.action_event_profile_id,p=>AE.name(p,original)),row.action_event_profile_id,v=>{
    row.action_event_profile_id=v||null;changed();drawMigration();})),
    UI.field("数据库产球来源标识",UI.input(row.origin_reference_id,v=>update(row,"origin_reference_id",v.trim()||null))),
    UI.el("p","来源标识由数据库资料或Agent方案提供。球数、元素和逐动作关联由系统读取，不在这里手填。"),
    choices("目标类型",row.scenario,"target_kind",[["unknown","尚未明确"],["ordinary_non_animal_monster","普通非动物单目标敌人"]]),
    boolean("场景允许技能掉落","drop_allowed"),
    choices("轴开始前的产球状态",row.scenario,"initial_controller",[["unknown","尚未明确"],["inactive","没有已有活跃状态"]]),
    choices("拾取方式",row.scenario,"pickup",[["unknown","尚未明确"],["all_at_once","同一批全数同时收取"]]),
    boolean("没有额外控制器重置或延寿","no_external_controller_changes"),boolean("所有队员存活","all_party_alive"),
    choices("受击停顿的计时模型",row.scenario,"hitlag_policy",[["unknown","尚未选择"],["zero_hitlag_reference","采用不计受击停顿的参考模型"]]),
    UI.el("p","选择参考模型不代表真实战斗没有停顿；系统仍检查轴中已知的冲突和共享状态。"),
    choices("场景声明状态",row.scenario,"status",[["pending","待核实"],["assumed","明确参考假设"],["confirmed","已核实用途"]]),
    UI.field("场景与初态依据",UI.area(row.scenario.source,v=>update(row.scenario,"source",v))),
    UI.field("命中到产球（秒）",UI.input(row.calibration.spawn_delay_seconds,v=>update(row.calibration,"spawn_delay_seconds",v),true)),
    UI.field("产球到收取（秒）",UI.input(row.calibration.travel_seconds,v=>update(row.calibration,"travel_seconds",v),true)),
    UI.field("时间记录精度（秒）",UI.input(row.calibration.precision_seconds,v=>update(row.calibration,"precision_seconds",v),true)),
    choices("时间校准状态",row.calibration,"status",[["pending","待核实"],["assumed","明确参考假设"],["confirmed","已核实用途"]]),
    UI.field("时间校准依据",UI.area(row.calibration.source,v=>update(row.calibration,"source",v))),migrationBox);
  const actions=UI.el("div");actions.append(UI.button("预览产球来源与命中",()=>run("preview")),UI.button("接受本次产球来源与场景",()=>run("confirm")),UI.button("应用产球来源",()=>run("apply")));
  if(previous)actions.append(UI.button("移除产球来源",()=>run("remove")));
  actions.append(UI.button("放弃并关闭",close));
  dialog.appBody.append(UI.button("← 返回动作与能量",close),form,preview,errors,actions);
  drawMigration();EG.preview(preview,null);dialog.focusInitial?.();
};
EG.render=box=>{
  if(PL.state.plan?.plan_version!==3||!EG.caps())return;
  const card=UI.el("section",null,"eq-card");card.append(UI.el("h4","技能命中自动产球"),UI.button("添加技能产球来源",()=>EG.edit()));
  EG.sources(PL.state.plan).forEach((row,index)=>{const profile=AE.profiles(PL.state.plan).find(p=>p.id===row.action_event_profile_id);
    card.append(EG.sourceRow(UI.el("p",`${profile?AE.name(profile):"动作档案待修复"} · ${row.accepted_fingerprint?"已接受，计算时重新核对":"草稿待确认"}`),row,index));});
  box.append(card);
};
