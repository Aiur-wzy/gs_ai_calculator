"use strict";
// Native declarations only; dispatch, fingerprints and execution remain in Python.
window.AE={fields:["action_event_profiles","action_event_bindings","action_event_overrides"]};
AE.caps=()=>PL.state.caps?.action_event_contract;
AE.isSingle=profile=>["calibrated_single_contact_v1","calibrated_single_application_v2"].includes(profile?.kind);
AE.profiles=plan=>plan?.action_event_profiles||[];
AE.bindings=plan=>plan?.action_event_bindings||[];
AE.overrides=plan=>plan?.action_event_overrides||[];
AE.commandMenu=()=>window.AppContextMenu?.forDocument?.(document,window)||null;
AE.menuError=error=>setStatus(`操作未完成：${String(error?.message||error||"未知错误")}`);
AE.openCommandMenu=(target,label,items)=>{
  const menu=AE.commandMenu();if(menu)return menu.open({target,trigger:target,returnFocus:target,label,items,onError:AE.menuError});
  const first=items.find(item=>item?.type!=="separator"&&item.danger!==true&&item.enabled!==false&&typeof item.run==="function");
  if(first)return first.run();setStatus("操作菜单暂不可用，请重新加载界面");return false;
};
AE.bindCommandMenu=(target,label,items)=>AE.commandMenu()?.bind(target,{label,items,onError:AE.menuError});
AE.withCurrent=(rows,target,label,run)=>{
  const index=rows.indexOf(target);if(index<0){setStatus(`${label}已变化，请重新打开操作菜单`);return false;}return run(index);
};
AE.commandRow=(content,label,items)=>{
  const row=UI.el("div",null,"eq-toolbar rc-definition-row"),more=UI.button("⋯",()=>AE.openCommandMenu(more,label,items()),"rc-row-menu");row.tabIndex=0;
  row.append(content,more);AE.bindCommandMenu(row,label,items);return row;
};
AE.variant=value=>({default:"默认形态",tap:"点按",hold:"长按"}[value]||"变体声明待修正");
AE.landed=value=>value===true?"明确命中":value===false?"明确未命中":"尚未核实";
AE.landedOptions=[{value:"",label:"尚未核实"},{value:"true",label:"明确命中"},{value:"false",label:"明确未命中"}];
AE.landedValue=value=>value===true?"true":value===false?"false":"";
AE.name=(profile,plan=PL.state.plan)=>{
  if(window.PA?.is(profile)){const first=plan?.manual_template?.hits?.find(h=>h.id===profile.channels?.[0]?.prototype_hit_id);return `${first?DS.hit(first,undefined,plan.manual_template):"周期动作"} · ${profile.channels?.length||0} 个攻击通道`;}
  const hit=plan?.manual_template?.hits?.find(h=>h.id===profile?.prototype_hit_id);
  return hit?`${DS.hit(hit,undefined,plan.manual_template)} · ${AE.variant(profile.variant)}`:"动作档案（原型待关联）";
};
AE.actionName=(id,plan=PL.state.plan)=>{
  const model=plan?.rotation_model,action=model?.actions.find(a=>a.id===id),ability=model?.abilities.find(a=>a.id===action?.ability_id);
  return action?`${ability?RC.abilityName(ability):"能力待关联"} · ${action.start??"待定"} 秒`:`动作引用待修复（${id}）`;
};
AE.options=(rows,value,label)=>[{value:"",label:"请选择"},...rows.map((row,i)=>({value:row.id,label:label(row,i)})),
  ...(value&&!rows.some(row=>row.id===value)?[{value,label:`引用已失效（${value}）`}]:[])];
AE.issues=(box,items,title="待完善事项")=>{
  if(!items?.length)return;const detail=UI.el("details");detail.append(UI.el("summary",title));
  items.slice(0,30).forEach(item=>detail.append(DS.textBlock(item.message||item.reason||"请查看原始定位","待核实内容，详见原始定位")));
  detail.append(UI.details("完整问题定位",items));box.append(detail);
};
AE.record=(box,row,plan=PL.state.plan,result=null)=>{
  const profile=AE.profiles(plan).find(p=>p.id===row.profile_id),card=UI.el("section",null,"eq-card");
  card.append(UI.el("h4",AE.name(profile,plan)),UI.el("p",`档案状态：${row.status==="ready"?"范围内资料就绪":RC.status(row.status)}；校准派发，技能仅部分覆盖，产球不由此生成。`),
    UI.el("p","接受指纹只确认这份档案及来源依赖；不会把校准假设或变体声明升级为游戏原生规则。"));
  if(profile?.calibration)card.append(UI.el("p",`校准偏移 ${profile.calibration.offset_seconds??"待补"} 秒；记录精度 ${profile.calibration.precision_seconds??"待补"} 秒；${RC.status(profile.calibration.status)}`),
    DS.textBlock(profile.calibration.source,"校准来源及固定范围请展开查看原文"));
  if(window.PA?.is(profile)){
    const lifecycle=profile.lifecycle||{};
    card.append(UI.el("p",`相对动作开始建立 ${lifecycle.establishment_offset_seconds??"待补"} 秒、结束 ${lifecycle.instance_end_offset_seconds??"待补"} 秒；${RC.status(lifecycle.status)}。周期来自所选资料，实际启动与结束按校准执行。`));
    profile.channels.forEach((c,i)=>card.append(UI.el("p",`通道 ${i+1}：${c.schedule.kind==="periodic"?`首击 ${c.schedule.first_offset_seconds??"待补"} 秒，共 ${c.schedule.count??"待补"} 次`:`独立攻击 ${c.schedule.offset_seconds??"待补"} 秒`}；${AE.landed(c.default_landed)}。`)));
  }
  const generated=row.generated||[],stats=row.stats||{},reported=new Set((result?.hits||[]).map(h=>h.id));
  const execution=new Map((result?.rotation_check?.event_log||[]).filter(e=>e.source_cycle===0||e.cycle===0).map(e=>[e.id,e]));
  card.append(UI.el("p",`匹配动作 ${stats.matched_actions??0}；计划命中 ${stats.planned_hits??0}；已生成 ${stats.generated_hits??generated.length}；声明未命中 ${stats.missed_hits??generated.filter(g=>g.landed===false).length}${result?`；本次实际计伤 ${generated.filter(g=>reported.has(g.hit_id)).length}`:"；实际执行尚待计算"}。`));
  if(generated.length){const table=UI.el("table"),head=UI.el("tr");["来源动作","接触秒数／同刻顺序","命中声明","派生输出／执行状态"].forEach(v=>head.append(UI.el("th",v)));table.append(head);
    generated.slice(0,100).forEach(g=>{const tr=UI.el("tr"),event=execution.get(g.hit_id),state=!result?"待运行":reported.has(g.hit_id)?"已计入伤害":event?.status==="missed"?"未命中，保留施放记录":event?RC.status(event.status):"未计入伤害，详见执行检查";
      const channel=profile?.channels?.findIndex(c=>c.channel_id===g.channel_id);
      [AE.actionName(g.action_id,plan),`${g.t??"—"}／${g.order??"—"}`,AE.landed(g.landed),`${g.channel_id?`通道 ${channel>=0?channel+1:"待关联"} · 第 ${g.ordinal??"?"} 次` : "单次命中"} · ${state}`].forEach(v=>tr.append(UI.el("td",v)));table.append(tr);});card.append(table);
    if(generated.length>100)card.append(UI.el("p",`显示前100项，共${generated.length}项；完整映射可在原始回执查看。`));}
  AE.issues(card,row.issues);card.append(UI.details("动作、原型和派生命中完整映射与来源",row));box.append(card);
};
AE.renderReceipt=(box,report,plan=PL.state.plan,result=null)=>{
  const records=report?.action_event_compilations||[];records.forEach(row=>{
    AE.record(box,row,plan,result);
    const parameter=(report.hit_parameter_compilations||[]).find(p=>p.hit_id===row.prototype_hit_id)
      ||(report.hit_parameter_sources||[]).find(p=>p.hit_id===row.prototype_hit_id);
    if(parameter&&MC.renderHitParameterCompilation){const source=UI.el("details");source.append(UI.el("summary","共享原型的数值来源与完整参数说明"));MC.renderHitParameterCompilation(source,parameter);box.append(source);}
  });
  AE.issues(box,report?.missing_inputs,"方案其他待办（不等于档案已可执行）");
  if(window.AM)AM.renderReceipt(box,report);
  if(window.PA)PA.renderReceipt(box,report);
  if(window.PB)PB.renderReceipt(box,report);
  if(window.EG)EG.renderReceipt(box,report,plan);
  if(window.LHB)LHB.renderReceipt(box,report,plan);
  if(window.MS)MS.renderReceipt(box,report,plan);
};
AE.session=title=>{
  if(PL.state.draft){setStatus("请先应用或放弃装备草稿，再编辑动作档案");return null;}
  const original=PL.state.plan;if(original?.plan_version!==3||!AE.caps()){setStatus("动作档案需要原生版本3及当前接口支持");return null;}
  const baseline=PL.currentInputJson(),epoch=PL.state.sourceEpoch,path=RT.state.path,sha=PL.state.fileSha,caps=JSON.stringify(PL.state.caps);
  const local=Object.fromEntries(AE.fields.map(key=>[key,UI.clone(original[key]||[])]));
  let closed=false,revision=0,request=0,shown=null;
  const form=UI.el("div"),preview=UI.el("div"),errors=UI.el("div"),actions=UI.el("div",null,"eq-actions");
  const invalidate=()=>{if(closed)return true;closed=true;request++;return true;};
  const dialog=window.AppDialogKernel?.create?.({title,
    description:"动作档案按明确的来源和校准执行；完整动作须逐次输入。支持范围见各类档案，不会自动补轮次或产球。",
    className:"mc-dialog",onRequestClose:invalidate})||null;
  if(!dialog){setStatus("当前还有窗口正在处理，请先完成或关闭后再编辑动作档案");return null;}
  const current=()=>!closed&&PL.state.plan===original&&!PL.state.draft&&PL.currentInputJson()===baseline&&PL.state.sourceEpoch===epoch&&RT.state.path===path&&PL.state.fileSha===sha&&JSON.stringify(PL.state.caps)===caps;
  const close=()=>{invalidate();return dialog.finish?.("cancel")??true;};
  const changed=profile=>{revision++;request++;shown=null;if(profile)profile.accepted_profile_fingerprint=null;preview.innerHTML="";errors.innerHTML="";};
  const proposal=()=>{const value=UI.clone(original);AE.fields.forEach(key=>{value[key]=UI.clone(local[key]);});if(session.transform)session.transform(value);return value;};
  const run=async(mode,profileId=null)=>{
    if(!current()){setStatus("方案、文件或数据库状态已变化，请重新打开动作档案");return;}
    errors.innerHTML="";
    if(mode==="confirm"&&!shown){errors.append(UI.el("p","请先读取并预览当前档案，再独立确认。","eq-error"));return;}
    let proposed;try{proposed=proposal();if(session.validate)session.validate();}catch(error){errors.append(UI.el("p",error.message,"eq-error"));return;}
    const prior=shown,version=revision,attempt=++request;let response;
    try{response=await callApi("compose_equipment_plan",UI.clone(proposed));}catch(error){if(current()&&attempt===request)errors.append(UI.el("p",`读取失败：${error.message}`,"eq-error"));return;}
    if(!current()||version!==revision||attempt!==request){setStatus("旧动作档案响应已忽略，请重新读取");return;}
    if(response.code){errors.append(UI.error(response));return;}
    const report=response.data,records=report?.action_event_compilations||[],row=records.find(r=>r.profile_id===profileId);
    if(report?.state==="invalid"||local.action_event_profiles.some(p=>!records.some(r=>r.profile_id===p.id))){errors.append(UI.el("p","接口未返回完整档案回执，未应用修改。","eq-error"));return;}
    if(session.validateReport){try{session.validateReport(report,proposed);}catch(error){errors.append(UI.el("p",error.message,"eq-error"));return;}}
    preview.innerHTML="";AE.renderReceipt(preview,report,proposed);shown={report:UI.clone(report),revision};
    if(mode==="preview")return;
    if(mode==="confirm"){
      const required=row?.required_confirmation?.accepted_profile_fingerprint,old=prior.report.action_event_compilations?.find(r=>r.profile_id===profileId)?.required_confirmation?.accepted_profile_fingerprint;
      if(typeof required!=="string"||!/^[0-9a-f]{64}$/.test(required)){errors.append(UI.el("p","当前依赖尚无完整可确认指纹，请先处理来源或范围缺项。","eq-error"));return;}
      const profile=local.action_event_profiles.find(p=>p.id===profileId);
      if(required!==old){profile.accepted_profile_fingerprint=null;errors.append(UI.el("p","当前来源或档案依赖已变化，请阅读新预览后再次确认。","eq-error"));return;}
      profile.accepted_profile_fingerprint=required;revision++;shown.revision=revision;
      errors.append(UI.el("p","已记录所示档案指纹；校准状态和其他待办保持原样，尚未应用到原生方案。"));return;
    }
    const changedSources=local.action_event_profiles.filter(p=>p.accepted_profile_fingerprint&&p.accepted_profile_fingerprint!==records.find(r=>r.profile_id===p.id)?.required_confirmation?.accepted_profile_fingerprint);
    if(changedSources.length){changedSources.forEach(p=>{p.accepted_profile_fingerprint=null;});revision++;shown.revision=revision;errors.append(UI.el("p","来源或档案依赖已变化，旧确认已清除。请重新预览确认，或再次应用未确认草稿。","eq-error"));return;}
    // Never trust server-returned plan/patterns or allow the API to mutate our copy.
    AE.fields.forEach(key=>{original[key]=UI.clone(local[key]);});if(session.commit)session.commit(original,proposed);close();MC.touch();
    AE.last={plan:original,input:PL.currentInputJson(),epoch:PL.state.sourceEpoch,path:RT.state.path,sha:PL.state.fileSha,report:UI.clone(report)};MC.render();
    setStatus(report.state==="ready"?"动作档案已应用；请重新计算并保存":"动作档案草稿已应用；待办仍需补齐，尚未给出计算结论");
  };
  const session={original,local,dialog,form,preview,errors,actions,changed,run,close,proposal,current};
  dialog.appBody.append(UI.button("← 返回机制列表",close),form,preview,errors,actions);
  dialog.focusInitial?.();return session;
};
AE.controls=(session,profileId=null)=>{
  session.actions.append(UI.button("读取并预览动作档案",()=>session.run("preview",profileId)));
  if(profileId)session.actions.append(UI.button("确认所示档案指纹",()=>session.run("confirm",profileId)));
  session.actions.append(UI.button("应用档案草稿",()=>session.run("apply",profileId)),UI.button("放弃并关闭",session.close));
};
AE.editProfile=index=>{
  if(index!==undefined&&window.PA?.is(AE.profiles(PL.state.plan)[index]))return PA.editProfile(index);
  const s=AE.session(index===undefined?"新增单次动作档案":"编辑单次动作档案");if(!s)return;
  const newApplication=AE.caps()?.profile_kinds?.includes("calibrated_single_application_v2")&&s.original.mechanics_model?.elemental_model?.ruleset==="gauge-swirl-dynamic-v3";
  const row=index===undefined?{id:UI.id("action-profile"),kind:newApplication?"calibrated_single_application_v2":"calibrated_single_contact_v1",prototype_hit_id:"",variant:"default",
    calibration:{offset_seconds:null,precision_seconds:null,status:"pending",source:""},application_profile_id:"",contact_profile_id:"",default_landed:null,self_sampling:"current",accepted_profile_fingerprint:null}:s.local.action_event_profiles[index];
  if(!row){s.close();return;}if(index===undefined)s.local.action_event_profiles.push(row);
  const applicationOnly=row.kind==="calibrated_single_application_v2";
  if(index===undefined&&applicationOnly)delete row.contact_profile_id;
  const change=(key,value)=>{row[key]=value;s.changed(row);},calibrate=(key,value)=>{row.calibration[key]=value;s.changed(row);};
  const hits=s.original.manual_template.hits.filter(h=>h.damage_system==="classic"),elemental=s.original.mechanics_model?.elemental_model;
  const metadata=s.original.attack_metadata_sources?.some(source=>source.action_event_profile_id===row.id);
  s.form.append(UI.el("p",`档案标识：${row.id}`),UI.el("p",applicationOnly?"单次伤害与附着档案：使用普通扩散模型，不处理冻结接触。旧档案不会在此升级。":"单次冻结接触档案：使用冻结参考模型。"),UI.el("p","命中原型、倍率来源和附着档案在原有编辑器中维护；这里仅引用，不复制来源或填写倍率。"),
    UI.field("共享命中原型",metadata?UI.el("span","由攻击来源独占；更换所属原型前先移除该来源"):UI.select(AE.options(hits,row.prototype_hit_id,h=>DS.hit(h,undefined,s.original.manual_template)),row.prototype_hit_id,v=>change("prototype_hit_id",v))),
    UI.field("动作变体声明",UI.select(["default","tap","hold"].map(v=>({value:v,label:AE.variant(v)})),row.variant,v=>change("variant",v))),
    UI.field("从动作开始到接触的秒数",UI.input(row.calibration.offset_seconds,v=>calibrate("offset_seconds",v),true)),
    UI.field("记录精度（秒）",UI.input(row.calibration.precision_seconds,v=>calibrate("precision_seconds",v),true)),
    UI.field("校准依据状态",UI.select(["pending","assumed","confirmed"].map(v=>({value:v,label:RC.status(v)})),row.calibration.status,v=>calibrate("status",v))),
    UI.field("校准来源、固定状态和适用范围",UI.area(row.calibration.source,v=>calibrate("source",v))),
    UI.field("附着档案",metadata?UI.el("span","由数据库攻击来源在本次执行中生成"):UI.select(AE.options(elemental?.profiles||[],row.application_profile_id,(p,i)=>`附着档案 ${i+1} · ${MC.label(p.element)} · ${MC.label(p.application)}`),row.application_profile_id,v=>change("application_profile_id",v))),
    ...(applicationOnly?[]:[UI.field("接触档案",UI.select(AE.options(elemental?.contact_profiles||[],row.contact_profile_id,(p,i)=>`接触档案 ${i+1} · ${p.freeze_interaction==="none"?"不削减冻结量":"接触范围待核实"}`),row.contact_profile_id,v=>change("contact_profile_id",v)))]),
    UI.field("默认命中判断",UI.select(AE.landedOptions,AE.landedValue(row.default_landed),v=>change("default_landed",v===""?null:v==="true"))),
    UI.field("自身收益取值时点",UI.select([{value:"current",label:"每次命中时"},{value:"action_start",label:"动作开始的前置收益后、后置收益前"},{value:"action_capture",label:"采用该动作明确声明的面板取样"}],row.self_sampling,v=>change("self_sampling",v))));
  if(elemental?.ruleset!==(applicationOnly?"gauge-swirl-dynamic-v3":"gauge-freeze-reference-v1"))s.form.append(UI.el("p",applicationOnly?"当前尚非普通扩散模型，请核对元素模型；此处不会自动切换。":"当前尚非冻结参考模型，请在原有元素编辑器明确选择并接受参考契约；此处不会自动切换。","eq-error"));
  s.validate=()=>{if(!row.prototype_hit_id||!row.application_profile_id||!applicationOnly&&!row.contact_profile_id)throw Error(applicationOnly?"请明确选择原型和附着档案。":"请明确选择原型、附着档案和接触档案。");
    if(!Number.isFinite(row.calibration.offset_seconds)||row.calibration.offset_seconds<=0||row.calibration.offset_seconds>60||!Number.isFinite(row.calibration.precision_seconds)||row.calibration.precision_seconds<0||row.calibration.precision_seconds>1)throw Error("请填写大于0且不超过60秒的偏移，以及0至1秒的记录精度；时间须精确微秒，记录精度不代表实际误差保证。");
    if(!row.calibration.source.trim())throw Error("请填写校准来源和范围；未知可保持待核实，不会自动认定。");};
  AE.controls(s,row.id);
};
AE.editBinding=index=>{
  const s=AE.session(index===undefined?"绑定战技能力与档案":"编辑能力档案绑定");if(!s)return;
  const row=index===undefined?{ability_id:"",profile_id:""}:s.local.action_event_bindings[index];if(!row){s.close();return;}
  if(index===undefined)s.local.action_event_bindings.push(row);
  const hasPeriodic=s.local.action_event_profiles.some(p=>p.kind==="calibrated_multi_contact_v1"),abilities=(s.original.rotation_model?.abilities||[]).filter(a=>a.action_type_id==="action.elemental_skill"||hasPeriodic&&a.action_type_id==="action.elemental_burst");
  const change=(key,value)=>{row[key]=value;s.changed();draw();};
  const draw=()=>{s.form.innerHTML="";s.form.append(UI.field(hasPeriodic?"绑定的战技或爆发能力":"绑定的战技能力",UI.select(AE.options(abilities,row.ability_id,a=>`${RC.abilityName(a)} · ${AE.variant(a.variant)}`),row.ability_id,v=>change("ability_id",v))),
    UI.field("采用的动作档案",UI.select(AE.options(s.local.action_event_profiles,row.profile_id,p=>AE.name(p,s.original)),row.profile_id,v=>change("profile_id",v))));
    const ability=abilities.find(a=>a.id===row.ability_id),profile=s.local.action_event_profiles.find(p=>p.id===row.profile_id),matches=(s.original.rotation_model?.actions||[]).filter(a=>a.ability_id===row.ability_id);
    s.form.append(UI.el("p",`实际输入动作 ${matches.length} 个；所有关联动作按各自开始时点实例化，不复制首轮。`));
    if(ability&&profile&&ability.variant!==profile.variant)s.form.append(UI.el("p","能力与档案的变体声明不一致，请在对应编辑器核实；不会自动改名通过校验。","eq-error"));
    if(ability&&profile&&((profile.kind==="calibrated_multi_contact_v1")!==(ability.action_type_id==="action.elemental_burst")))s.form.append(UI.el("p","当前周期档案只支持爆发，单次接触档案只支持战技；请核实此绑定。","eq-error"));
    if(!matches.length)s.form.append(UI.el("p","尚无对应的实际动作，绑定只能保存为待完善草稿。"));};
  s.validate=()=>{if(!row.ability_id||!row.profile_id)throw Error("请选择已有能力和动作档案。");};draw();AE.controls(s);
};
AE.editOverride=index=>{
  if(index!==undefined&&AE.overrides(PL.state.plan)[index]?.kind==="calibrated_multi_contact_v1")return window.PA?.editOverride(index);
  const s=AE.session(index===undefined?"添加本轴动作例外":"编辑本轴动作例外");if(!s)return;
  const row=index===undefined?{action_id:"",landed:null,status:"pending",source:""}:s.local.action_event_overrides[index];if(!row){s.close();return;}
  if(index===undefined)s.local.action_event_overrides.push(row);
  const single=new Set(s.local.action_event_profiles.filter(AE.isSingle).map(p=>p.id)),bound=new Set(s.local.action_event_bindings.filter(b=>single.has(b.profile_id)).map(b=>b.ability_id)),items=(s.original.rotation_model?.actions||[]).filter(a=>bound.has(a.ability_id));
  const change=(key,value)=>{row[key]=value;s.changed();};
  s.form.append(UI.field("本轴实际动作",UI.select(AE.options(items,row.action_id,a=>AE.actionName(a.id,s.original)),row.action_id,v=>change("action_id",v))),
    UI.field("本次命中例外",UI.select([{value:"inherit",label:"沿用档案，不覆盖"},...AE.landedOptions],Object.hasOwn(row,"landed")?AE.landedValue(row.landed):"inherit",v=>{if(v==="inherit")delete row.landed;else row.landed=v===""?null:v==="true";s.changed();})),
    UI.field("本次同刻顺序（空白不覆盖）",UI.input(row.order,v=>{if(v===null)delete row.order;else row.order=v;s.changed();},true)),
    UI.field("例外核实状态",UI.select(["pending","assumed","confirmed"].map(v=>({value:v,label:RC.status(v)})),row.status,v=>change("status",v))),
    UI.field("本次例外的依据",UI.area(row.source,v=>change("source",v))));
  s.validate=()=>{if(!items.some(a=>a.id===row.action_id))throw Error("例外只能关联本轴已绑定能力的实际动作。");if(!Object.hasOwn(row,"landed")&&!Object.hasOwn(row,"order"))throw Error("例外至少应改变命中判断或同刻顺序。");
    if(Object.hasOwn(row,"order")&&(!Number.isInteger(row.order)||Math.abs(row.order)>1e9))throw Error("同刻顺序必须为范围内整数。");if(!row.source.trim())throw Error("请填写本次例外的依据。");};AE.controls(s);
};
AE.drop=(kind,index)=>{
  if(kind===0&&window.PA?.is(AE.profiles(PL.state.plan)[index]))return PA.dropProfile(index);
  if(kind===0&&PL.state.plan?.attack_metadata_sources?.some(source=>source.action_event_profile_id===AE.profiles(PL.state.plan)[index]?.id)){setStatus("请先在攻击来源编辑器移除该来源并补回原型元素，再移除动作档案。");return;}
  const s=AE.session("移除动作档案声明");if(!s)return;const field=AE.fields[kind],row=s.local[field]?.[index];if(!row){s.close();return;}
  const abilityIds=new Set(kind===0?s.local.action_event_bindings.filter(b=>b.profile_id===row.id).map(b=>b.ability_id):kind===1?[row.ability_id]:[]);
  const actionIds=new Set((s.original.rotation_model?.actions||[]).filter(a=>abilityIds.has(a.ability_id)).map(a=>a.id));s.local[field].splice(index,1);
  if(kind===0)s.local.action_event_bindings=s.local.action_event_bindings.filter(b=>b.profile_id!==row.id);
  if(kind!==2)s.local.action_event_overrides=s.local.action_event_overrides.filter(o=>!actionIds.has(o.action_id));
  s.form.append(UI.el("p",kind===0?"将移除该档案及其能力绑定、相应动作例外。共享原型、倍率来源和实际动作仍保留，孤立原型须另行处理。":kind===1?"将移除该绑定及对应动作例外；档案、原型和实际动作仍保留，未使用的档案仍待处理。":"仅移除此动作的例外，之后沿用档案声明。"),
    UI.el("p","不会删除用户命中或写入生成事件；请核对预览中的剩余待办。"));AE.controls(s);
};
AE.render=box=>{
  const plan=PL.state.plan;if(plan?.plan_version!==3||!AE.caps())return;const card=UI.el("section",null,"eq-card");
  card.append(UI.el("h4","动作档案自动派发"),UI.el("p","一份单次接触档案关联已输入的战技动作。使用显式校准，仅覆盖部分技能逻辑，不自动补动作或随机产球。"),
    UI.button("新增动作档案",()=>AE.editProfile()),UI.button(window.PA?.caps()?"绑定能力与档案":"绑定战技能力与档案",()=>AE.editBinding()),UI.button("添加本轴动作例外",()=>AE.editOverride()));
  if(window.ASA?.caps())card.append(UI.button("从数据库建立动作档案",()=>ASA.edit()));
  if(window.PAS?.caps())card.append(UI.button("从数据库建立多通道档案",()=>PAS.edit()));
  if(window.PA?.caps())card.append(UI.button("新增周期动作档案",()=>PA.editProfile()),UI.button("添加周期接触例外",()=>PA.editOverride()),UI.el("p","周期档案从数据库读取可证的倍率、附着与间隔，按各次实际爆发动作展开；首末时点、次数与生命周期仍需校准。"));
  AE.profiles(plan).forEach(p=>{const items=()=>[
      {id:`edit-profile-${p.id}`,label:"编辑动作档案",run:()=>AE.withCurrent(AE.profiles(PL.state.plan),p,"动作档案",i=>AE.editProfile(i))},
      ...(window.AM&&AM.caps()&&AE.isSingle(p)?[{id:`attack-source-${p.id}`,label:"选择数据库攻击来源",run:()=>AE.withCurrent(AE.profiles(PL.state.plan),p,"动作档案",()=>AM.edit(p.id))}]:[]),
      {type:"separator"},{id:`remove-profile-${p.id}`,label:"删除动作档案",danger:true,run:()=>AE.withCurrent(AE.profiles(PL.state.plan),p,"动作档案",i=>AE.drop(0,i))},
    ];card.append(AE.commandRow(UI.el("span",`${AE.name(p,plan)} · ${RC.status(p.calibration?.status||p.lifecycle?.status)} · ${p.channels?"命中声明见各通道":`默认${AE.landed(p.default_landed)}`} · ${p.accepted_profile_fingerprint?"已记录指纹，计算时重新核对":"待预览确认"}`),`${AE.name(p,plan)}操作`,items));});
  AE.bindings(plan).forEach(b=>{const ability=plan.rotation_model?.abilities.find(a=>a.id===b.ability_id),profile=AE.profiles(plan).find(p=>p.id===b.profile_id),items=()=>[
      {id:"edit-action-event-binding",label:"编辑能力档案绑定",run:()=>AE.withCurrent(AE.bindings(PL.state.plan),b,"能力档案绑定",i=>AE.editBinding(i))},
      {type:"separator"},{id:"remove-action-event-binding",label:"移除能力档案绑定",danger:true,run:()=>AE.withCurrent(AE.bindings(PL.state.plan),b,"能力档案绑定",i=>AE.drop(1,i))},
    ];card.append(AE.commandRow(UI.el("span",`${ability?RC.abilityName(ability):"能力引用已失效"} → ${AE.name(profile,plan)}`),"能力档案绑定操作",items));});
  AE.overrides(plan).forEach(o=>{const items=()=>[
      {id:"edit-action-event-override",label:"编辑本轴动作例外",run:()=>AE.withCurrent(AE.overrides(PL.state.plan),o,"动作例外",i=>AE.editOverride(i))},
      {type:"separator"},{id:"remove-action-event-override",label:"移除本轴动作例外",danger:true,run:()=>AE.withCurrent(AE.overrides(PL.state.plan),o,"动作例外",i=>AE.drop(2,i))},
    ];card.append(AE.commandRow(UI.el("span",`${AE.actionName(o.action_id,plan)} · ${o.kind==="calibrated_multi_contact_v1"?`${o.contacts.length} 项逐接触例外`:`${Object.hasOwn(o,"landed")?AE.landed(o.landed):"沿用命中判断"} · ${o.order===undefined?"顺序不覆盖":`顺序 ${o.order}`}`}`),"动作例外操作",items));});
  const last=AE.last;if(last&&last.plan===plan&&last.input===PL.currentInputJson()&&last.epoch===PL.state.sourceEpoch&&last.path===RT.state.path&&last.sha===PL.state.fileSha)AE.renderReceipt(card,last.report,plan);
  box.append(card);
};
AE.renderResult=(box,result)=>{
  if(!result?.action_event_compilations?.length)return;const detail=UI.el("details");detail.append(UI.el("summary","动作档案与实际执行（只读）"));
  AE.renderReceipt(detail,result,RS.state.snapshot?.template||PL.state.plan,result);box.append(detail);
};
