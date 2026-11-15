"use strict";
// Database facts stay in receipts; native inputs retain source declarations.
window.AR={};
AR.bindings=()=>PL.state.plan?.action_resource_sources||[];
AR.commandMenu=()=>window.AppContextMenu?.forDocument?.(document,window)||null;
AR.menuError=error=>setStatus(`操作未完成：${String(error?.message||error||"未知错误")}`);
AR.openCommandMenu=(target,label,items)=>{
  const menu=AR.commandMenu();if(menu)return menu.open({target,trigger:target,returnFocus:target,label,items,onError:AR.menuError});
  const first=items.find(item=>item?.type!=="separator"&&item.danger!==true&&item.enabled!==false&&typeof item.run==="function");
  if(first)return first.run();setStatus("操作菜单暂不可用，请重新加载界面");return false;
};
AR.bindCommandMenu=(target,label,items)=>AR.commandMenu()?.bind(target,{label,items,onError:AR.menuError});
AR.withCurrent=(target,run)=>{const index=AR.bindings().indexOf(target);if(index<0){setStatus("动作资源来源已变化，请重新打开操作菜单");return false;}return run(index);};
AR.commandRow=(content,label,items)=>{const row=UI.el("div",null,"eq-toolbar rc-definition-row"),more=UI.button("⋯",()=>AR.openCommandMenu(more,label,items()),"rc-row-menu");row.tabIndex=0;
  row.append(content,more);AR.bindCommandMenu(row,label,items);return row;};
AR.boundAbility=id=>AR.bindings().some(row=>row.ability_id===id);
AR.boundSpend=event=>event.type==="energy_spend"&&AR.boundAbility(RC.model()?.actions.find(a=>a.id===event.action_id)?.ability_id);
AR.deliveryCaps=()=>PL.state.caps?.action_resource_source_contract?.energy_delivery;
AR.mode=row=>row&&Object.hasOwn(row,"energy_delivery")?row.energy_delivery:"existing_events";
AR.autoAction=id=>AR.bindings().some(row=>AR.mode(row)==="action_start"&&row.ability_id===RC.model()?.actions.find(a=>a.id===id)?.ability_id);
AR.confirmKeys=["expected_snapshot_id","expected_resource_fingerprint","expected_scope_fingerprint"];
AR.clear=row=>AR.confirmKeys.forEach(key=>{row[key]=null;});
AR.targets=(model,id)=>{
  const ability=model?.abilities.find(a=>a.id===id),actions=model?.actions.filter(a=>a.ability_id===id)||[];
  const ids=new Set(actions.map(a=>a.id));
  return {ability,actions,spends:(model?.events||[]).filter(e=>e.type==="energy_spend"&&ids.has(e.action_id))};
};
AR.migration=(original,row,index)=>{
  const targets=AR.targets(original.rotation_model,row.ability_id),old=original.action_resource_sources?.[index];
  if(AR.mode(row)!=="action_start"||!targets.spends.length)return [];
  if(!old||old.ability_id!==row.ability_id||AR.mode(old)!=="existing_events")
    throw Error("自动扣能不能与手工扣能混用；未绑定的手工事件请先完成既有资源来源接管，再明确迁移。");
  if(["duration","charges"].some(k=>Object.hasOwn(targets.ability?.cooldown||{},k))||
    ["required","amount","delivery"].some(k=>Object.hasOwn(targets.ability?.energy||{},k)))
    throw Error("原来源绑定仍有手填或派生资源字段，不能通过迁移自动修复非法原生输入。");
  const allowed=new Set(["id","type","t","order","repeat","action_id","character","status","source"]),ids=new Set();
  for(const action of targets.actions){
    const rows=targets.spends.filter(e=>e.action_id===action.id),event=rows[0];
    if(rows.length!==1)throw Error("迁移要求每个实际动作恰好一条原有扣能事件；缺失或重复须先处理。");
    if(Object.keys(event).some(k=>!allowed.has(k))||typeof event.id!=="string"||!event.id||ids.has(event.id)||
      event.character!==targets.ability.character||!Number.isFinite(action.start)||event.t!==action.start||
      !Number.isInteger(action.order)||!Number.isInteger(event.order)||event.order<=action.order||
      JSON.stringify(event.repeat)!==JSON.stringify(action.repeat)||!["assumed","confirmed"].includes(event.status)||
      typeof event.source!=="string"||!event.source.trim())
      throw Error("仅能迁移同角色、动作开始同刻、合法后续顺序、相同重复方式且数值已省略的标准扣能事件；请先处理延迟、未知或冲突输入。");
    ids.add(event.id);
  }
  const references=[];
  const scan=(value,path)=>{
    if(Array.isArray(value)){value.forEach((child,i)=>scan(child,`${path}[${i}]`));return;}
    if(!value||typeof value!=="object")return;
    for(const [key,child] of Object.entries(value)){
      const values=Array.isArray(child)?child:[child];
      if(["event_id","event_ids","spawn_id","self_snapshot_event","snapshot_event_ids","replaces_event_ids"].includes(key)&&values.some(v=>ids.has(v)))references.push(`${path}.${key}`);
      scan(child,`${path}.${key}`);
    }
  };
  for(const key of ["rotation_model","mechanics_model","effect_bindings","matches","manual_template"])scan(original[key],key);
  if(references.length)throw Error(`扣能事件仍被其他输入引用或接管，不能移除：${references.join("、")}`);
  return targets.spends;
};
AR.propose=(original,row,index,manual)=>{
  const plan=UI.clone(original),targets=AR.targets(plan.rotation_model,row.ability_id),ability=targets.ability;
  if(!ability?.cooldown||!ability.variant)throw Error("请先在技能定义中明确动作变体、共享冷却组、起点及恢复方式。");
  const mode=AR.mode(row),old=original.action_resource_sources?.[index];
  if(!["existing_events","action_start"].includes(mode))throw Error("扣能交付模式无效，请明确采用既有事件或动作开始自动扣能。");
  if(mode==="action_start"&&!AR.deliveryCaps()?.modes?.includes(mode))throw Error("当前接口尚未提供动作开始自动扣能，请更新接口后重新读取。");
  if(mode==="action_start"&&old&&(["duration","charges"].some(k=>Object.hasOwn(ability.cooldown,k))||
    ["required","amount","delivery"].some(k=>Object.hasOwn(ability.energy||{},k))))
    throw Error("原来源绑定仍有手填或派生资源字段，不能通过迁移自动修复非法原生输入。");
  if(!targets.actions.length)throw Error("来源能力还没有实际动作，请先明确动作轴。");
  const migrating=!manual?AR.migration(original,row,index):[];
  if(mode==="existing_events"&&!old&&targets.actions.some(a=>targets.spends.filter(e=>e.action_id===a.id).length!==1))
    throw Error("每个关联动作需要恰好一条已有扣能事件，请先补齐映射和扣能时刻。");
  if(targets.actions.some(a=>targets.spends.filter(e=>e.action_id===a.id).length>1))throw Error("同一动作存在重复扣能事件，请先处理冲突。");
  plan.action_resource_sources||=[];
  if(manual){
    if(![manual.duration,manual.charges,manual.required,manual.amount].every(Number.isFinite)||manual.duration<0||
      !Number.isInteger(manual.charges)||manual.charges<1||manual.required<0||manual.amount<0)
      throw Error("解绑须明确冷却、正整数次数、能量门槛和扣除量；不会默认恢复0或1。");
    plan.action_resource_sources.splice(index,1);
    Object.assign(ability.cooldown,{duration:manual.duration,charges:manual.charges});
    ability.energy={required:manual.required,amount:manual.amount};
    targets.spends.forEach(e=>Object.assign(e,ability.energy));
  }else{
    if(index===undefined)plan.action_resource_sources.push(UI.clone(row));else plan.action_resource_sources[index]=UI.clone(row);
    delete ability.cooldown.duration;delete ability.cooldown.charges;
    ability.energy={};targets.spends.forEach(e=>{delete e.required;delete e.amount;});
    if(migrating.length){const ids=new Set(migrating.map(e=>e.id));plan.rotation_model.events=plan.rotation_model.events.filter(e=>!ids.has(e.id));}
  }
  return plan;
};
AR.preview=(box,record)=>{
  box.innerHTML="";if(!record){box.append(UI.el("p","读取当前来源后核对资料与适用依据。"));return;}
  box.append(UI.el("p",`用途：${RC.status(record.interpretation_status)}；数据库不会自动证明动作变体适用。`));
  const names={base_cooldown_seconds:"基础冷却（秒）",base_charge_count:"基础可用次数",base_energy_cost:"基础耗能",cost_element:"耗能元素"};
  Object.entries(names).forEach(([key,label])=>{const item=record.projection?.[key],value=item?.status==="ready"?
    (key==="cost_element"?DS.label("elements",item.value):item.value):"待补充";box.append(UI.el("p",`${label}：${value}`));});
  for(const key of ["name","description"]){const item=record.descriptions?.[key];
    if(item?.text)box.append(DS.textBlock(item.text,key==="name"?"技能名称":"技能原文"));}
  if(record.energy_delivery?.mode==="action_start")box.append(UI.el("p",`动作开始自动扣能：关联 ${record.energy_delivery.links?.length??0} 个实际动作；只生成提交瞬间的系统回执，不新增排队事件或改变顺序。`));
  else box.append(UI.el("p",`关联${record.action_spend_links?.length??0}个既有扣能事件；不会新增动作或改变时刻。`));
  if(record.target_paths?.length)box.append(UI.details("本次编译的数字字段",record.target_paths));
  if(record.issues?.length)box.append(UI.details("仍待完善的资源输入",record.issues));
  box.append(UI.details("来源与用途范围回执",record));
};
AR.edit=index=>{
  if(PL.state.draft){setStatus("请先应用或放弃装备草稿");return;}
  const original=PL.state.plan;if(original?.plan_version!==3){setStatus("动作资源来源需要原生版本3方案");return;}
  const bindings=AR.bindings(),others=new Set(bindings.filter((_,i)=>i!==index).map(s=>s.ability_id));
  const available=(original.rotation_model?.abilities||[]).filter(a=>a.action_type_id==="action.elemental_burst"&&!others.has(a.id));
  const initial=index===undefined?available[0]:original.rotation_model?.abilities.find(a=>a.id===bindings[index]?.ability_id);
  if(!initial){setStatus("请先定义元素爆发能力及实际动作；已失效的来源可使用“移除来源并保留待补输入”处理");return;}
  const fromAbility=ability=>{const ref=original.equipment_inputs?.[ability.character]?.character_ref;
    return {ability_id:ability.id,version_line:ref?.version_line||"release",
      selection:{character_id:ref?.db_character_id??null,slot:"burst",constellation:0},
      purpose:"ordinary_fixed_burst_base_resources",variant:ability.variant||"",status:"pending",source:"",
      expected_snapshot_id:null,expected_resource_fingerprint:null,expected_scope_fingerprint:null};};
  let row=index===undefined?fromAbility(initial):UI.clone(bindings[index]),revision=0,request=0,closed=false,shown=null,migrationReviewed=false,reviewControl=null;
  const baseline=PL.currentInputJson(),epoch=RC.sourceEpoch(),path=RT.state.path,sha=PL.state.fileSha;
  const current=()=>!closed&&PL.state.plan===original&&PL.currentInputJson()===baseline&&RC.sourceEpoch()===epoch&&
    RT.state.path===path&&PL.state.fileSha===sha&&!PL.state.draft;
  const form=UI.el("div"),preview=UI.el("div"),errors=UI.el("div"),actions=UI.el("div");
  const manual={duration:null,charges:null,required:null,amount:null};
  const invalidate=()=>{if(closed)return true;closed=true;request++;return true;};
  const dialog=window.AppDialogKernel?.create?.({title:"数据库动作资源来源",
    description:"基础资源由数据库填写执行副本；选择自动扣能时明确核对旧事件迁移，原动作时序保持。",
    onRequestClose:invalidate})||null;
  if(!dialog){setStatus("当前还有窗口正在处理，请先完成或关闭后再编辑动作资源");return;}
  const close=()=>{invalidate();return dialog.finish?.("cancel")??true;};
  const changed=()=>{revision++;request++;shown=null;migrationReviewed=false;if(reviewControl)reviewControl.checked=false;AR.clear(row);AR.preview(preview,null);};
  const update=(key,value)=>{row[key]=value;changed();};
  const selection=(key,value)=>{if(key==="skill_depot_id"&&value===null)delete row.selection[key];else row.selection[key]=value;changed();};
  const run=async mode=>{
    if(!current()){setStatus("方案已变化，请重新打开资源编辑");return;}
    errors.innerHTML="";let proposed,migrating;
    try{migrating=mode==="remove"?[]:AR.migration(original,row,index);proposed=AR.propose(original,row,index,mode==="remove"?manual:null);}catch(error){errors.append(UI.el("p",error.message,"eq-error"));return;}
    if(migrating.length&&!migrationReviewed){errors.append(UI.el("p","请先核对并勾选拟移除的扣能事件清单；原生方案尚未改变。","eq-error"));return;}
    const automatic=AR.mode(row)==="action_start"&&mode!=="remove",prior=shown;
    if(automatic&&mode==="confirm"&&!prior){errors.append(UI.el("p","请先读取并预览新扣能模式及来源范围，再确认所示内容。","eq-error"));return;}
    const attempt=++request,version=revision,valid=()=>attempt===request&&version===revision&&current();
    let result;
    try{
      if(migrating.length){
        const before=await callApi("compose_equipment_plan",UI.clone(original));
        if(!valid()){setStatus("旧资源响应已忽略，请重新读取");return;}
        if(before.code||before.data?.state==="invalid"||!(before.data?.action_resource_compilations||[]).some(r=>r.ability_id===row.ability_id)){
          errors.append(before.code?UI.error(before):UI.el("p","原始来源绑定未通过校验，不能通过删除事件修复输入；请先处理原方案。","eq-error"));return;
        }
      }
      result=await callApi("compose_equipment_plan",UI.clone(proposed));
    }catch(error){if(valid())errors.append(UI.el("p",`资源读取失败：${error.message}`,"eq-error"));return;}
    if(!valid()){setStatus("旧资源响应已忽略，请重新读取");return;}
    if(result.code){errors.append(UI.error(result));return;}
    const report=result.data,record=(report.action_resource_compilations||[]).find(r=>r.ability_id===row.ability_id);
    if(report.state==="invalid"){errors.append(UI.el("p","当前草稿结构或来源冲突未通过校验，未应用修改。","eq-error"));return;}
    const expectedActions=AR.targets(proposed.rotation_model,row.ability_id).actions,links=record?.energy_delivery?.links;
    if(automatic&&(!record||record.energy_delivery?.mode!=="action_start"||record.energy_delivery.contract!=="automatic-energy-delivery-v1"||
      record.energy_delivery.phase!=="action_admission"||!Array.isArray(links)||links.length!==expectedActions.length||
      links.some(link=>link.ability_id!==row.ability_id||!expectedActions.some(a=>a.id===link.action_id)||!/^@energy_spend:[0-9a-f]{64}$/.test(link.receipt_id))||
      new Set(links.map(link=>link.action_id)).size!==links.length||new Set(links.map(link=>link.receipt_id)).size!==links.length)){
      errors.append(UI.el("p","接口未返回完整的自动扣能模式与动作映射，未应用修改。","eq-error"));return;
    }
    AR.preview(preview,record);
    shown=record?.required_confirmation?UI.clone(record.required_confirmation):null;
    if(mode==="preview")return;
    if(mode==="confirm"){
      if(!record||!AR.confirmKeys.every(key=>typeof record.required_confirmation?.[key]==="string"&&record.required_confirmation[key])){
        errors.append(UI.el("p","当前资料尚无可确认的完整指纹，请先处理缺项。","eq-error"));return;}
      if(automatic&&AR.confirmKeys.some(key=>prior[key]!==record.required_confirmation[key])){
        AR.clear(row);errors.append(UI.el("p","来源或扣能范围已变化，请核对新的预览后再次确认。","eq-error"));return;
      }
      AR.confirmKeys.forEach(key=>{row[key]=record.required_confirmation[key];});revision++;
      setStatus("已记录当前来源与用途范围确认；未核实的适用项仍保持待处理");return;
    }
    if(mode!=="remove"&&!record){errors.append(UI.el("p","接口未返回该来源的编译回执，未应用修改。","eq-error"));return;}
    if(automatic&&AR.confirmKeys.some(key=>row[key])&&AR.confirmKeys.some(key=>row[key]!==record.required_confirmation?.[key])){
      AR.clear(row);revision++;errors.append(UI.el("p","已接受的扣能来源发生变化，未应用；请重新预览确认，或保留未确认草稿。","eq-error"));return;
    }
    original.action_resource_sources=UI.clone(proposed.action_resource_sources);
    original.rotation_model=UI.clone(proposed.rotation_model);close();RC.touch();
    AR.last={plan:original,input:PL.currentInputJson(),epoch:RC.sourceEpoch(),path:RT.state.path,sha:PL.state.fileSha,
      record:record?UI.clone(record):null,state:report.state};RC.render();
    setStatus(mode==="remove"?"已解绑并采用明确手填资源；请补齐需要的扣能事件后重新检查并保存":
      automatic?"自动扣能来源已应用；未新增输入事件，仍须计算验证实际提交与能量循环":
        "资源来源已应用，原生数值由来源声明取代；缺少扣能事件时请在事件编辑器补齐后检查");
  };
  const draw=()=>{form.innerHTML="";reviewControl=null;
    if(index===undefined)form.append(UI.field("绑定的元素爆发能力",UI.select(available.map(a=>({value:a.id,label:RC.abilityName(a)})),row.ability_id,v=>{
      row=fromAbility(available.find(a=>a.id===v));changed();draw();})));
    else form.append(UI.el("p",`关联能力：${RC.abilityName(initial)}`));
    if(index!==undefined&&row.variant!==initial.variant)form.append(
      UI.el("p",`技能定义的当前变体为“${initial.variant||"未填写"}”，与来源声明不同，请核实后更新。`,"eq-error"),
      UI.button("采用当前技能变体并重新核实",()=>{row.variant=initial.variant||"";changed();draw();}));
    form.append(UI.el("p",`采用动作变体：${row.variant||"尚未填写，请先编辑技能定义"}`),
      UI.field("数据库角色编号",UI.input(row.selection.character_id,v=>selection("character_id",v),true)),
      UI.field("数据库资料线",UI.select([{value:"release",label:"发布资料线"},{value:"beta",label:"测试资料线"}],row.version_line,v=>update("version_line",v))),
      UI.field("技能定位方式",UI.select([{value:"slot",label:"元素爆发技能槽"},{value:"skill_id",label:"明确技能编号"}],Object.hasOwn(row.selection,"skill_id")?"skill_id":"slot",v=>{
        delete row.selection.slot;delete row.selection.skill_id;row.selection[v]=v==="slot"?"burst":null;changed();draw();})));
    if(Object.hasOwn(row.selection,"skill_id"))form.append(UI.field("数据库技能编号",UI.input(row.selection.skill_id,v=>selection("skill_id",v),true)));
    form.append(UI.field("技能组编号（多技能组须填写）",UI.input(row.selection.skill_depot_id,v=>selection("skill_depot_id",v),true)),
      UI.field("已激活命座数",UI.input(row.selection.constellation,v=>selection("constellation",v),true)),
      UI.field("资源用途",UI.select([{value:"ordinary_fixed_burst_base_resources",label:"常规固定耗能爆发的基础资源"},{value:"unresolved_resource_mode",label:"特殊或尚未确定的资源模式"}],row.purpose,v=>update("purpose",v))),
      UI.field("用途核实状态",UI.select(["pending","assumed","confirmed"].map(v=>({value:v,label:RC.status(v)})),row.status,v=>update("status",v))),
      UI.field("本动作采用基础资源的依据",UI.area(row.source,v=>update("source",v))));
    if(AR.deliveryCaps()?.modes?.includes("action_start")){
      form.append(UI.field("扣能交付",UI.select([{value:"existing_events",label:"既有扣能事件（保持原方式）"},{value:"action_start",label:"动作开始自动扣能（系统回执）"}],AR.mode(row),v=>{
        if(v==="existing_events"&&!Object.hasOwn(bindings[index]||{},"energy_delivery"))delete row.energy_delivery;else row.energy_delivery=v;
        changed();draw();
      })));
      if(AR.mode(row)==="action_start"){
        form.append(UI.el("p","动作获准时扣除基础能量；系统回执记录提交瞬间，不是可拖动或手填的事件。它不推测退款或真实动画扣能帧。"));
        try{const migrating=AR.migration(original,row,index);if(migrating.length){
          form.append(UI.details("拟移除的旧扣能事件（逐项原文）",migrating));
          migrating.forEach(e=>form.append(UI.el("p",`移除 ${e.id} · 来源动作 ${e.action_id} · ${e.t} 秒 · 顺序 ${e.order}；两个数值由既有来源提供。`)));
          const review=UI.check("我已核对上述事件，仅将这些扣能事件改为动作开始自动交付",migrationReviewed,v=>{migrationReviewed=v;request++;});
          reviewControl=review.children[1];form.append(review);
        }}catch(error){form.append(UI.el("p",error.message,"eq-error"));}
      }else if(bindings[index]&&AR.mode(bindings[index])==="action_start")form.append(UI.el("p","切回后保留来源绑定；不会生成扣能时刻或顺序。请保存草稿后在能量事件中补齐。"));
    }
    if(index!==undefined)for(const [key,label] of Object.entries({duration:"解绑后冷却秒数",charges:"解绑后可用次数",required:"解绑后能量门槛",amount:"解绑后实际扣能"}))
      form.append(UI.field(label,UI.input(manual[key],v=>{manual[key]=v;revision++;request++;},true)));
  };
  actions.append(UI.button("读取并预览当前资源",()=>run("preview")),UI.button("确认当前来源及用途范围",()=>run("confirm")),
    UI.button("应用资源来源",()=>run("apply")));
  if(index!==undefined)actions.append(UI.button("解除绑定并采用手填资源",()=>run("remove")));
  actions.append(UI.button("放弃并关闭",close));
  dialog.appBody.append(UI.button("← 返回动作与能量",close),
    UI.el("p","当前支持零命、单次充能、常规固定耗能爆发。基础资料不证明变体适用，未知模式保持待处理。"),form,preview,errors,actions);
  draw();AR.preview(preview,null);dialog.focusInitial?.();
};
AR.drop=index=>{
  if(PL.state.draft){setStatus("请先处理装备草稿");return;}
  const original=PL.state.plan,baseline=PL.currentInputJson(),epoch=RC.sourceEpoch(),path=RT.state.path,sha=PL.state.fileSha;
  if(!original?.action_resource_sources?.[index])return;
  const dialog=window.AppDialogKernel?.create?.({title:"移除动作资源来源",
    description:"只移除这条来源声明。原生技能和扣能事件保持原样；缺少的资源数字仍待补充，需重新检查后才能用于计算。"})||null;
  if(!dialog){setStatus("当前还有窗口正在处理，请先完成或关闭后再移除动作资源来源");return;}
  const close=()=>dialog.finish?.("cancel")??true;
  dialog.appBody.append(
    UI.button("确认移除，保留待补输入",()=>{
      if(PL.state.plan!==original||PL.currentInputJson()!==baseline||RC.sourceEpoch()!==epoch||RT.state.path!==path||PL.state.fileSha!==sha||PL.state.draft){
        setStatus("方案已变化，请重新打开移除确认");return;}
      original.action_resource_sources.splice(index,1);close();RC.touch();setStatus("来源已移除，请补充缺失的技能与扣能资料后重新检查");}),
    UI.button("← 返回动作与能量",close));
  dialog.focusInitial?.();
};
AR.render=box=>{
  if(PL.state.plan?.plan_version!==3||!PL.state.caps?.action_resource_source_contract)return;
  const card=UI.el("section",null,"eq-card");card.append(UI.el("h4","数据库动作资源"),UI.button("添加动作资源来源",()=>AR.edit()));
  AR.bindings().forEach(row=>{const ability=RC.model()?.abilities.find(a=>a.id===row.ability_id),items=()=>[
      {id:`edit-action-resource-${row.ability_id}`,label:"编辑动作资源来源",run:()=>AR.withCurrent(row,index=>AR.edit(index))},
      {type:"separator"},{id:`remove-action-resource-${row.ability_id}`,label:"移除来源并保留待补输入",danger:true,run:()=>AR.withCurrent(row,index=>AR.drop(index))},
    ];card.append(AR.commandRow(UI.el("span",`${ability?RC.abilityName(ability):row.ability_id} · ${AR.mode(row)==="action_start"?"动作开始自动扣能":"既有扣能事件"} · ${RC.status(row.status)} · ${AR.confirmKeys.every(k=>row[k])?"已记录确认，计算时仍重新核对":"等待来源确认"}`),"动作资源来源操作",items));});
  const last=AR.last;
  if(last&&last.plan===PL.state.plan&&last.input===PL.currentInputJson()&&last.epoch===RC.sourceEpoch()&&last.path===RT.state.path&&last.sha===PL.state.fileSha){
    const detail=UI.el("div");AR.preview(detail,last.record);card.append(detail);}
  box.append(card);
};
