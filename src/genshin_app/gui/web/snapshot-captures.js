"use strict";
// Edits only explicit capture declarations and user-selected consumers.
window.SC={};
SC.caps=()=>PL.state.caps?.mechanics_contract?.snapshot_captures||window.MC?.caps?.snapshot_captures;
SC.rows=plan=>plan?.mechanics_model?.snapshot_captures||[];
SC.commandMenu=()=>window.AppContextMenu?.forDocument?.(document,window)||null;
SC.menuError=error=>setStatus(`操作未完成：${String(error?.message||error||"未知错误")}`);
SC.openCommandMenu=(target,label,items)=>{
  const menu=SC.commandMenu();if(menu)return menu.open({target,trigger:target,returnFocus:target,label,items,onError:SC.menuError});
  const first=items.find(item=>item?.type!=="separator"&&item.danger!==true&&item.enabled!==false&&typeof item.run==="function");
  if(first)return first.run();setStatus("操作菜单暂不可用，请重新加载界面");return false;
};
SC.bindCommandMenu=(target,label,items)=>SC.commandMenu()?.bind(target,{label,items,onError:SC.menuError});
SC.withCurrent=(target,run)=>{const index=SC.rows(PL.state.plan).indexOf(target);if(index<0){setStatus("面板取样已变化，请重新打开操作菜单");return false;}return run(index);};
SC.commandRow=(content,label,items)=>{const row=UI.el("div",null,"eq-toolbar rc-definition-row"),more=UI.button("⋯",()=>SC.openCommandMenu(more,label,items()),"rc-row-menu");row.tabIndex=0;
  row.append(content,more);SC.bindCommandMenu(row,label,items);return row;};
SC.native=plan=>Object.fromEntries(Object.entries(plan).filter(([key])=>!["resolution","resolved_template"].includes(key)));
SC.canonical=value=>JSON.stringify(value,(_,v)=>v&&typeof v==="object"&&!Array.isArray(v)?Object.fromEntries(Object.keys(v).sort().map(k=>[k,v[k]])):v);
SC.id=action=>`capture.action.${encodeURIComponent(action).replace(/~/g,"%7E")}`;
SC.ticks=value=>{
  if(typeof value!=="number"||!Number.isFinite(value))throw Error("时点和记录精度必须明确填写有限数字");
  const m=String(value).match(/^(-?)(\d+)(?:\.(\d+))?(?:e([+-]?\d+))?$/i),fraction=m?.[3]||"";
  if(!m)throw Error("时间格式无效");let digits=BigInt(m[2]+fraction),power=6+Number(m[4]||0)-fraction.length;
  if(Math.abs(power)>400)throw Error("时间超出支持范围");
  if(power<0){const divisor=10n**BigInt(-power);if(digits%divisor)throw Error("只接受精确整数微秒，不能自动四舍五入");digits/=divisor;}else digits*=10n**BigInt(power);
  const result=Number(m[1]? -digits:digits);if(!Number.isSafeInteger(result))throw Error("时间超出安全整数范围");return result;
};
SC.actionName=(action,plan)=>{const ability=plan.rotation_model.abilities.find(a=>a.id===action.ability_id);return `${ability?RC.abilityName(ability):"能力待关联"} · ${action.start??"待定"} 秒`;};
SC.actions=(plan,draft)=>plan.rotation_model.actions.filter(a=>draft.index!==undefined?a.id===SC.rows(plan)[draft.index]?.action_id:a.ability_id===draft.ability_id);
SC.consumers=(plan,actions)=>["hit_events","hit_patterns"].flatMap(key=>(plan.mechanics_model[key]||[]).flatMap((row,index)=>{
  const action=actions.find(a=>a.id===row.action_id);return action?[{key,index,row,action,id:`${key}:${index}`}]:[];
}));
SC.profileUnits=(plan,actions)=>(plan.action_event_profiles||[]).flatMap(profile=>{
  const abilities=(plan.action_event_bindings||[]).filter(b=>b.profile_id===profile.id).map(b=>b.ability_id);
  if(!actions.some(a=>abilities.includes(a.ability_id)))return [];
  return profile.channels?profile.channels.map((channel,index)=>({key:`${profile.id}:${channel.channel_id}`,profile,channel,index})):[{key:profile.id,profile}];
});
SC.propose=(original,draft)=>{
  const plan=UI.clone(original),actions=SC.actions(plan,draft),model=plan.mechanics_model;
  if(!actions.length)throw Error("请选择已有能力及其实际动作；不会自动新建动作");
  const captures=UI.clone(SC.rows(plan)),byAction=new Map(),changed=new Set();
  for(const row of captures){if(byAction.has(row.action_id))throw Error("同一动作已有多个面板取样，请先修复");byAction.set(row.action_id,row);}
  for(const action of actions){
    let row=byAction.get(action.id);
    if(draft.index!==undefined||!row){
      const next={id:row?.id||SC.id(action.id),action_id:action.id,offset:draft.offset,precision:draft.precision,status:draft.status,source:draft.source};
      if(!row&&captures.some(c=>c.id===next.id))throw Error("稳定取样标识与已有声明冲突，未覆盖");
      if(SC.ticks(next.offset)<0||SC.ticks(next.precision)<=0)throw Error("偏移必须非负，记录精度必须严格大于零");
      const tick=SC.ticks(action.start)+SC.ticks(next.offset);
      if(tick>SC.ticks(action.occupation_end)||tick>=SC.ticks(plan.rotation_model.period))throw Error("面板取样必须在动作占用内，且严格早于输入窗口右端");
      if(!row||SC.canonical(row)!==SC.canonical(next))changed.add(action.id);
      if(row)captures[captures.indexOf(row)]=next;else captures.push(next);row=next;byAction.set(action.id,row);
    }
  }
  for(const consumer of SC.consumers(plan,actions)){
    if(!Object.hasOwn(draft.links,consumer.id))continue;
    const capture=byAction.get(consumer.action.id),row=model[consumer.key][consumer.index];
    if(draft.links[consumer.id]){
      if(row.self_snapshot_event&&row.self_snapshot_event!==capture.id)throw Error("已有其他自身取样引用，不能覆盖；请先在原编辑器明确调整");
      row.self_snapshot_event=capture.id;
    }else if(row.self_snapshot_event===capture.id)delete row.self_snapshot_event;
  }
  if(draft.remove&&SC.rows(original)[draft.index])changed.add(SC.rows(original)[draft.index].action_id);
  for(const unit of SC.profileUnits(plan,actions)){
    const row=unit.channel||unit.profile;
    if(draft.profiles[unit.key]){
      const abilities=(plan.action_event_bindings||[]).filter(b=>b.profile_id===unit.profile.id).map(b=>b.ability_id);
      if(plan.rotation_model.actions.some(a=>abilities.includes(a.ability_id)&&!byAction.has(a.id)))throw Error("此档案还关联其他能力，请先为那些实际动作声明面板取样");
      if(row.self_sampling!=="action_capture"){row.self_sampling="action_capture";unit.profile.accepted_profile_fingerprint=null;}
    }
    if(row.self_sampling==="action_capture"&&actions.some(a=>changed.has(a.id)))unit.profile.accepted_profile_fingerprint=null;
  }
  if(draft.remove){
    const row=SC.rows(original)[draft.index];
    if(!row)throw Error("待移除的取样声明已不存在");
    if([...(model.hit_events||[]),...(model.hit_patterns||[])].some(h=>h.self_snapshot_event===row.id))throw Error("请先明确取消手工命中的引用，再移除此取样");
    model.snapshot_captures=captures.filter(c=>c.id!==row.id);
  }else model.snapshot_captures=captures;
  return plan;
};
SC.edit=index=>{
  const original=PL.state.plan;
  if(PL.state.draft||original?.plan_version!==3||!SC.caps()){setStatus("请先完成装备草稿，并在原生版本3使用面板取样");return;}
  const existing=SC.rows(original)[index],ability=original.rotation_model?.abilities.find(a=>original.rotation_model.actions.some(x=>x.id===existing?.action_id&&x.ability_id===a.id));
  const draft={index,ability_id:ability?.id||"",offset:existing?.offset??null,precision:existing?.precision??null,status:existing?.status||"pending",source:existing?.source||"",links:{},profiles:{},remove:false};
  const baseline=PL.currentInputJson(),epoch=PL.state.sourceEpoch,path=RT.state.path,sha=PL.state.fileSha,
    caps=SC.canonical(SC.caps());
  let closed=false,revision=0,request=0;
  const form=UI.el("div"),consumers=UI.el("div"),preview=UI.el("div"),errors=UI.el("div");
  const invalidate=()=>{if(closed)return true;closed=true;request++;return true;};
  const dialog=window.AppDialogKernel?.create?.({title:existing?"编辑面板取样":"按能力声明面板取样",
    description:"取样是动作成功后的一次状态记录，不产生伤害。零偏移在父动作前置和后置收益之后；取消、命中先后和范围由实际执行再检查。",
    className:"mc-dialog",onRequestClose:invalidate})||null;
  if(!dialog){setStatus("当前还有窗口正在处理，请先完成或关闭后再编辑面板取样");return;}
  const current=()=>!closed&&PL.state.plan===original&&!PL.state.draft&&PL.currentInputJson()===baseline&&epoch===PL.state.sourceEpoch&&path===RT.state.path&&sha===PL.state.fileSha&&caps===SC.canonical(SC.caps());
  const close=()=>{invalidate();return dialog.finish?.("cancel")??true;};
  const changed=()=>{revision++;request++;preview.innerHTML="";errors.innerHTML="";};
  const draw=()=>{
    consumers.innerHTML="";const actions=SC.actions(original,draft);
    consumers.append(UI.el("p",`本次覆盖 ${actions.length} 个现有动作。已有声明保留；只为缺少声明的动作新增。`));
    for(const action of actions){const row=SC.rows(original).find(c=>c.action_id===action.id);consumers.append(UI.el("p",`${SC.actionName(action,original)}${row?` · 已有取样 ${row.offset} 秒（逐项编辑时才修改）`:" · 将生成明确取样声明"}`));}
    for(const item of SC.consumers(original,actions)){
      const id=SC.rows(original).find(c=>c.action_id===item.action.id)?.id||SC.id(item.action.id),old=item.row.self_snapshot_event;
      if(old&&old!==id){consumers.append(UI.el("p",`手工${item.key==="hit_patterns"?"多段":"命中"} ${item.index+1} 已关联其他取样，保持不变。`));continue;}
      const checked=Object.hasOwn(draft.links,item.id)?draft.links[item.id]:old===id;
      consumers.append(UI.check(`关联手工${item.key==="hit_patterns"?"多段":"命中"} ${item.index+1} · ${SC.actionName(item.action,original)}`,checked,v=>{draft.links[item.id]=v;changed();}));
    }
    for(const unit of SC.profileUnits(original,actions)){
      const selected=(unit.channel||unit.profile).self_sampling==="action_capture";
      if(selected)consumers.append(UI.el("p",`动作档案${unit.channel?`通道 ${unit.index+1}`:""} 已采用面板取样；校准改变后需重新确认。`));
      else consumers.append(UI.check(`关联动作档案${unit.channel?`通道 ${unit.index+1}`:""} · ${window.AE?AE.name(unit.profile,original):unit.profile.id}`,!!draft.profiles[unit.key],v=>{draft.profiles[unit.key]=v;changed();}));
    }
    if(!SC.consumers(original,actions).length&&!SC.profileUnits(original,actions).length)consumers.append(UI.el("p","当前没有可关联的伤害声明；可只保存取样供后续显式引用。"));
  };
  const field=(key,value)=>{draft[key]=value;changed();};
  form.append(UI.field("采用取样的能力",index===undefined?UI.select([{value:"",label:"请选择已有能力"},...(original.rotation_model?.abilities||[]).map(a=>({value:a.id,label:RC.abilityName(a)}))],draft.ability_id,v=>{draft.ability_id=v;draft.links={};draft.profiles={};changed();draw();}):UI.el("span",ability?RC.abilityName(ability):"关联动作已失效")),
    UI.field("从动作开始到面板取样（秒）",UI.input(draft.offset,v=>field("offset",v),true)),
    UI.field("记录精度（秒）",UI.input(draft.precision,v=>field("precision",v),true)),
    UI.field("取样依据状态",UI.select(["pending","assumed","confirmed"].map(v=>({value:v,label:RC.status(v)})),draft.status,v=>field("status",v))),
    UI.field("取样校准来源与适用范围",UI.area(draft.source,v=>field("source",v))));
  if(existing)form.append(UI.check("移除此取样（先取消手工引用；动作档案将待补充）",false,v=>field("remove",v)));
  const run=async apply=>{
    if(!current()){setStatus("方案、文件或资料状态已变化，请重新打开面板取样");return;}
    errors.innerHTML="";let proposed;try{proposed=SC.propose(original,draft);}catch(error){errors.append(UI.el("p",error.message,"eq-error"));return;}
    const version=revision,attempt=++request;let response;
    try{response=await callApi("compose_equipment_plan",UI.clone(proposed));}catch(error){if(current()&&attempt===request)errors.append(UI.el("p",error.message,"eq-error"));return;}
    if(!current()||revision!==version||request!==attempt){setStatus("旧面板取样响应已忽略，请重新预览");return;}
    if(response.code){errors.append(UI.error(response));return;}
    const report=response.data;
    if(!report?.plan||!["ready","pending"].includes(report.state)||SC.canonical(SC.native(report.plan))!==SC.canonical(SC.native(proposed))){errors.append(UI.el("p","接口未返回对应原生草稿；未应用修改。","eq-error"));return;}
    preview.innerHTML="";SC.renderRows(preview,proposed);if(report.missing_inputs?.length)preview.append(UI.details("仍需补充的来源与执行条件",report.missing_inputs));
    if(!apply)return;
    // Only the locally reviewed delta is committed, never server materialization.
    for(const key of ["snapshot_captures","hit_events","hit_patterns"])if(Object.hasOwn(proposed.mechanics_model,key))original.mechanics_model[key]=UI.clone(proposed.mechanics_model[key]);
    if(Object.hasOwn(proposed,"action_event_profiles"))original.action_event_profiles=UI.clone(proposed.action_event_profiles);
    close();MC.touch();setStatus("面板取样声明已应用；来源确认与实际计算仍按原流程进行");
  };
  dialog.appBody.append(UI.button("← 返回机制列表",close),form,consumers,preview,errors,
    UI.button("预览面板取样",()=>run(false)),UI.button("应用取样草稿",()=>run(true)),UI.button("放弃并关闭",close));
  draw();dialog.focusInitial?.();
};
SC.renderRows=(box,plan,interactive=false)=>{
  for(const row of SC.rows(plan)){const action=plan.rotation_model?.actions.find(a=>a.id===row.action_id),content=UI.el("div");
    content.append(UI.el("p",`${action?SC.actionName(action,plan):"关联动作待修复"} · 偏移 ${row.offset} 秒 · 记录精度 ${row.precision} 秒 · ${RC.status(row.status)}`),DS.textBlock(row.source,"取样来源请展开查看"));
    if(!interactive){box.append(content);continue;}
    const items=()=>[{id:`edit-snapshot-${row.id}`,label:"编辑或移除面板取样",run:()=>SC.withCurrent(row,index=>SC.edit(index))}];
    box.append(SC.commandRow(content,"面板取样操作",items));}
};
SC.render=box=>{
  if(!SC.caps())return;const card=UI.el("section",null,"eq-card");card.append(UI.el("h4","面板取样"),UI.el("p","一次填写校准可覆盖所选能力的现有动作；已有声明保留，新增动作需再次明确添加。"),UI.button("按能力声明面板取样",()=>SC.edit()));
  SC.renderRows(card,PL.state.plan,true);box.append(card);
};
SC.renderTrace=(box,result)=>{
  if(!result.snapshot_captures)return;const section=UI.el("details");section.append(UI.el("summary","面板取样与实际引用（只读）"),UI.el("p","以下仅记录状态取样与引用，不添加伤害。当前声明的执行一致性不等于游戏时点已实测。"));
  for(const row of result.snapshot_captures.trace.slice(0,100))section.append(UI.el("p",`${row.t} 秒 · ${row.type==="snapshot_capture"?(row.status==="captured"?"已完成面板取样":"取样已取消"):`引用面板取样（${row.usage==="hit_self"?"伤害自身属性":"属性表达式"}）`} · ${row.capture_id}`));
  section.append(UI.details("完整取样声明与执行输入",result.snapshot_capture_execution||{}),UI.details("完整取样回执",result.snapshot_captures));box.append(section);
};
if(window.MC)Object.assign(MC.words,{snapshot_captures:"面板取样",snapshot_capture:"面板取样",snapshot_capture_reference:"引用面板取样",mechanics_snapshot_capture:"面板取样",action_capture:"已声明的动作面板取样"});
