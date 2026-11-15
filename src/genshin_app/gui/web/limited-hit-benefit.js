"use strict";
// Native declarations only. Python verifies sources, actual ordering, pools and sampling.
window.LHB={};
LHB.caps=()=>PL.state.caps?.limited_hit_benefit_contract;
LHB.variants=()=>{
  const caps=LHB.caps();
  if(!caps||!Object.prototype.hasOwnProperty.call(caps,"supported_variants"))return ["hold"];
  return Array.isArray(caps.supported_variants)?caps.supported_variants.filter(value=>["tap","hold"].includes(value)):[];
};
LHB.sources=plan=>plan?.limited_hit_benefit_sources||[];
LHB.records=report=>report?.limited_hit_benefit_compilations||[];
LHB.commandMenu=()=>window.AppContextMenu?.forDocument?.(document,window)||null;
LHB.menuError=error=>setStatus(`操作未完成：${String(error?.message||error||"未知错误")}`);
LHB.openCommandMenu=(target,label,items)=>{
  const menu=LHB.commandMenu();if(menu)return menu.open({target,trigger:target,returnFocus:target,label,items,onError:LHB.menuError});
  const first=items.find(item=>item?.type!=="separator"&&item.danger!==true&&item.enabled!==false&&typeof item.run==="function");
  if(first)return first.run();setStatus("操作菜单暂不可用，请重新加载界面");return false;
};
LHB.bindCommandMenu=(target,label,items)=>LHB.commandMenu()?.bind(target,{label,items,onError:LHB.menuError});
LHB.withCurrentSource=(target,run)=>{
  const index=LHB.sources(PL.state.plan).indexOf(target);
  if(index<0){setStatus("有限次数收益来源已变化，请重新打开操作菜单");return false;}
  return run(index);
};
LHB.sourceMenuItems=row=>[{id:`edit-limited-hit-source:${row.id||"unknown"}`,label:"编辑有限次数收益来源",run:()=>LHB.withCurrentSource(row,index=>LHB.edit(index))}];
LHB.sourceRow=(content,row,index)=>{
  const label=`有限次数收益来源 ${index+1} 操作`,items=()=>LHB.sourceMenuItems(row),container=UI.el("div",null,"eq-toolbar rc-definition-row");
  const more=UI.button("⋯",()=>LHB.openCommandMenu(more,label,items()),"rc-row-menu");container.tabIndex=0;
  container.append(content,more);LHB.bindCommandMenu(container,label,items);return container;
};
LHB.confirmation=row=>row?.required_confirmation?.accepted_fingerprint;
LHB.record=(report,id)=>LHB.records(report).find(row=>row.id===id&&row.compiler===LHB.caps()?.compiler);
LHB.confirmable=row=>!!row&&["ready","pending"].includes(row.status)&&Array.isArray(row.issues)&&
  typeof LHB.confirmation(row)==="string"&&/^[0-9a-f]{64}$/.test(LHB.confirmation(row))&&
  row.issues.every(issue=>(LHB.caps()?.confirmable_issue_codes||[]).includes(issue.code));
LHB.preview=(box,row,plan=PL.state.plan)=>{
  if(!row){box.append(UI.el("p","选择接口支持且已核对的战技命中档案，读取来源并预览生效时点；未确认的内容可保存为草稿。"));return;}
  const certificate=row.benefit_certificate||row.projection?.benefit_certificate||{},calibration=row.calibration||{};
  box.append(UI.el("h4","冰翎：有限次数收益"),
    UI.el("p",`${row.status==="ready"?"本范围已就绪":"仍有待完善内容"}；参考适配仅覆盖技能的一部分，未完整解释所有原始分支。`),
    UI.el("p",`来源认证变体：${AE.variant(certificate.variant)}。同一提供者在本轴固定使用一种变体；混合点按与长按仍不支持。`),
    UI.el("p",`每次符合条件的命中，增加提供者当前攻击力 × ${certificate.coefficient??"待核实"} 的伤害基础值。`),
    UI.el("p",`每位角色独立 ${certificate.capacity??"待核实"} 次，持续 ${certificate.duration_seconds??"待核实"} 秒；再次施放重置次数，不叠加独立次数池。`),
    UI.el("p",`施放后 ${calibration.activation_offset_seconds??"待补"} 秒施加；记录精度 ${calibration.precision_seconds??"待补"} 秒。系统按实际事件顺序核对初始命中前是否生效。`),
    UI.el("p","施放已成功但初始攻击落空时仍施加；未命中不消费次数。隐藏伤害或排除该收益仍可能消费次数。"),
    UI.el("p","采用零卡肉参考；实际距离、死亡、其它变体与复杂攻击力转换未由本来源自动模拟。"));
  if(certificate.variant==="tap")box.append(UI.el("p","点按范围限定普通非动物单目标敌人，明确排除怪物装备部件（MonsterEquip）；其原文分支仍不支持。提前切人仍待支持，不会自动取消延迟激活。"));
  const links=row.parent_links||[];
  if(links.length){const table=UI.el("table"),head=UI.el("tr");
    ["来源动作","生效时点（秒）","初始命中（秒）","命中声明"].forEach(value=>head.append(UI.el("th",value)));table.append(head);
    links.slice(0,100).forEach(link=>{const tr=UI.el("tr");
      [AE.actionName(link.action_id,plan),Number.isFinite(link.activation_tick)?link.activation_tick/1000000:"待核实",link.t??"待核实",AE.landed(link.landed)]
        .forEach(value=>tr.append(UI.el("td",value)));table.append(tr);});box.append(table);
    if(links.length>100)box.append(UI.el("p",`显示前100项，共${links.length}项；完整映射见详情。`));}
  AE.issues(box,row.issues,"有限次数收益待办");
  box.append(UI.details("完整原文、未覆盖分支、参考依据与实际生效关系",row));
};
LHB.renderReceipt=(box,report,plan=PL.state.plan)=>{
  for(const row of LHB.records(report)){const card=UI.el("section",null,"eq-card");LHB.preview(card,row,plan);box.append(card);}
  if(report?.limited_hit_benefit_summary){const card=UI.el("section",null,"eq-card");
    card.append(UI.el("h4","有限次数收益的实际执行"),UI.el("p","次数与当前攻击力按真实已执行事件记录；未执行的后续动作不会自动补齐。"),
      UI.details("实际激活、次数与取样统计",report.limited_hit_benefit_summary));box.append(card);}
};
LHB.edit=index=>{
  if(PL.state.draft){setStatus("请先应用或放弃装备草稿");return;}
  const original=PL.state.plan,caps=LHB.caps();
  if(original?.plan_version!==3||!caps?.default_source){setStatus("有限次数收益需要当前版本接口及原生版本3方案");return;}
  if(index===undefined&&LHB.sources(original).length>=(caps.limits?.max_sources??1)){setStatus("当前仅支持一个有限次数收益提供者，请编辑已有来源");return;}
  const variants=LHB.variants(),profiles=AE.profiles(original).filter(profile=>profile.kind==="calibrated_single_contact_v1"&&variants.includes(profile.variant));
  if(index===undefined&&!profiles.length){setStatus("请先建立并核对接口支持的战技单次命中档案");return;}
  const previous=LHB.sources(original)[index];if(index!==undefined&&!previous)return;
  const row=UI.clone(previous||caps.default_source);if(!previous)row.id=UI.id("limited-hit-source");
  const baseline=PL.currentInputJson(),epoch=PL.state.sourceEpoch,path=RT.state.path,sha=PL.state.fileSha,capsJson=JSON.stringify(caps);
  let closed=false,revision=0,request=0,shown=null;
  const current=()=>!closed&&PL.state.plan===original&&!PL.state.draft&&PL.currentInputJson()===baseline&&
    PL.state.sourceEpoch===epoch&&RT.state.path===path&&PL.state.fileSha===sha&&JSON.stringify(LHB.caps())===capsJson;
  const form=UI.el("div"),preview=UI.el("div"),errors=UI.el("div");
  const invalidate=()=>{if(closed)return true;closed=true;request++;return true;};
  const dialog=window.AppDialogKernel?.create?.({title:"有限次数收益来源",
    description:"声明有限次数收益的来源动作、适用场景与生效校准；次数池、系数和持续时间由受审来源读取并在实际命中时核对。",
    className:"mc-dialog",onRequestClose:invalidate})||null;
  if(!dialog){setStatus("当前还有窗口正在处理，请先完成或关闭后再编辑有限次数收益来源");return;}
  const close=()=>{invalidate();return dialog.finish?.("cancel")??true;};
  const changed=()=>{revision++;request++;shown=null;row.accepted_fingerprint=null;preview.innerHTML="";errors.innerHTML="";LHB.preview(preview,null);};
  const update=(object,key,value)=>{if(object[key]===value)return;object[key]=value;changed();};
  const candidate=remove=>{const plan=UI.clone(original);plan.limited_hit_benefit_sources=LHB.sources(plan).filter(source=>source.id!==row.id);
    if(!remove)plan.limited_hit_benefit_sources.push(UI.clone(row));return plan;};
  const run=async mode=>{
    if(!current()){setStatus("方案、接口或数据库已变化，请重新打开有限次数收益编辑");return;}
    errors.innerHTML="";const remove=mode==="remove",prior=shown?.revision===revision?shown.record:null;
    if(mode==="confirm"&&!LHB.confirmable(prior)){errors.append(UI.el("p","请先完善来源与场景，再接受当前预览。","eq-error"));return;}
    const plan=candidate(remove),commitRow=UI.clone(row),accepted=mode==="confirm"?LHB.confirmation(prior):row.accepted_fingerprint;
    if(mode==="confirm"){plan.limited_hit_benefit_sources.find(source=>source.id===row.id).accepted_fingerprint=accepted;commitRow.accepted_fingerprint=accepted;}
    const attempt=++request,version=revision;let response;
    try{response=await callApi("compose_equipment_plan",UI.clone(plan));}catch(error){response={code:1,message:String(error)};}
    if(attempt!==request||version!==revision||!current()){if(!closed)setStatus("旧有限次数收益响应已忽略，请重新读取");return;}
    const report=response.data,record=LHB.record(report,row.id);
    if(response.code||!report||!["ready","pending"].includes(report.state)||(!remove&&(!record||record.action_event_profile_id!==row.action_event_profile_id))){
      errors.append(UI.error(response.code?response:{code:1,message:"接口未返回对应来源的可核验回执，未应用修改"}));return;}
    preview.innerHTML="";if(!remove)LHB.preview(preview,record,plan);shown={revision,record};
    if(!remove&&accepted&&LHB.confirmation(record)!==accepted){row.accepted_fingerprint=null;revision++;shown.revision=revision;
      errors.append(UI.el("p","来源或校准已变化，请核对新的预览后重新接受。","eq-error"));return;}
    if(mode==="preview")return;
    if(mode==="confirm"){
      if(!LHB.confirmable(record)||record.status!=="ready"){errors.append(UI.el("p","仍有未知条件或作用域冲突，未记录接受。","eq-error"));return;}
      row.accepted_fingerprint=accepted;revision++;shown.revision=revision;setStatus("已接受本次收益来源与场景；仍需应用并保存");return;
    }
    if(!remove&&accepted&&record.status!=="ready"){errors.append(UI.el("p","已接受的来源当前无法执行，未应用；请处理新冲突或重新声明。","eq-error"));return;}
    // Never copy report.plan, generated rules, pools or projected buffs into the native input.
    original.limited_hit_benefit_sources=LHB.sources(original).filter(source=>source.id!==row.id);
    if(!remove)original.limited_hit_benefit_sources.push(commitRow);
    close();RC.touch();setStatus(remove?"有限次数收益来源已移除，请重新计算":accepted?"有限次数收益已应用，请计算核对实际次数与取样":"未确认的收益草稿已应用，请完善后再计算");
  };
  const choices=(label,object,key,values)=>UI.field(label,UI.select(values.map(([value,label])=>({value,label})),object[key]??"",value=>update(object,key,value||null)));
  const boolean=(label,key)=>UI.field(label,UI.select([{value:"",label:"尚未明确"},{value:"true",label:"明确成立"},{value:"false",label:"明确不成立"}],AE.landedValue(row.scenario[key]),value=>update(row.scenario,key,value===""?null:value==="true")));
  form.append(UI.field("关联的战技命中档案",UI.select(AE.options(profiles,row.action_event_profile_id,profile=>AE.name(profile,original)),row.action_event_profile_id,value=>update(row,"action_event_profile_id",value||null))),
    UI.el("p",`当前接口支持：${variants.length?variants.map(AE.variant).join("、"):"暂无可选变体"}。同一提供者在本轴固定一种，不可用两个来源建立点按与长按双次数池。`),
    UI.el("p","系统从已接受父档案读取精确来源；系数、每人次数与持续时间不需手填。点按或长按声明仍须通过数据库父关系核验，不能只改名称通过。"),
    choices("目标类型",row.scenario,"target_kind",[["unknown","尚未明确"],["ordinary_non_animal_monster","普通非动物单目标敌人"]]),
    choices("队伍模式",row.scenario,"party_mode",[["unknown","尚未明确"],["single_player_fixed","单人固定队伍"]]),
    boolean("所有队员存活","all_party_alive"),boolean("施加时满足来源范围条件","all_party_in_apply_predicate"),
    boolean("保有期间满足来源范围条件","all_party_in_retention_predicate"),
    choices("轴开始前的冰翎",row.scenario,"initial_effect",[["unknown","尚未明确"],["none","没有已有冰翎"]]),
    boolean("没有额外冰翎施加、移除或延寿","no_external_quill_changes"),
    choices("卡肉计时模型",row.scenario,"hitlag_policy",[["unknown","尚未选择"],["zero_hitlag_reference","采用零卡肉参考"]]),
    UI.el("p","范围条件和零卡肉是明确参考声明，不代表系统已模拟实际距离或真实停顿。"),
    choices("场景声明状态",row.scenario,"status",[["pending","待核实"],["assumed","明确参考假设"],["confirmed","已核实用途"]]),
    UI.field("场景与初态依据",UI.area(row.scenario.source,value=>update(row.scenario,"source",value))),
    UI.field("施放到生效（秒）",UI.input(row.calibration.activation_offset_seconds,value=>update(row.calibration,"activation_offset_seconds",value),true)),
    UI.field("记录精度（秒）",UI.input(row.calibration.precision_seconds,value=>update(row.calibration,"precision_seconds",value),true)),
    choices("生效校准状态",row.calibration,"status",[["pending","待核实"],["assumed","明确参考假设"],["confirmed","已核实用途"]]),
    UI.field("生效校准依据",UI.area(row.calibration.source,value=>update(row.calibration,"source",value))));
  const actions=UI.el("div");actions.append(UI.button("预览来源与生效顺序",()=>run("preview")),UI.button("接受本次来源与场景",()=>run("confirm")),UI.button("应用有限次数收益",()=>run("apply")));
  if(previous)actions.append(UI.button("移除有限次数收益来源",()=>run("remove")));actions.append(UI.button("放弃并关闭",close));
  dialog.appBody.append(UI.button("← 返回动作与能量",close),form,preview,errors,actions);
  LHB.preview(preview,null);dialog.focusInitial?.();
};
LHB.render=box=>{
  if(PL.state.plan?.plan_version!==3||!LHB.caps())return;
  const card=UI.el("section",null,"eq-card");card.append(UI.el("h4","有限次数收益来源"));
  if(LHB.sources(PL.state.plan).length<(LHB.caps().limits?.max_sources??1))card.append(UI.button("添加有限次数收益来源",()=>LHB.edit()));
  LHB.sources(PL.state.plan).forEach((row,index)=>{const profile=AE.profiles(PL.state.plan).find(value=>value.id===row.action_event_profile_id);
    card.append(LHB.sourceRow(UI.el("p",`${profile?AE.name(profile):"动作档案待修复"} · ${row.accepted_fingerprint?"已接受，计算时重新核对":"草稿待确认"}`),row,index));});
  box.append(card);
};
