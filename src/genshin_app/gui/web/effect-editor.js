"use strict";
window.EF={stats:["atk","hp","def","crit_rate","crit_dmg","em","energy_recharge",
  "dmg_bonus:all","dmg_bonus:physical","dmg_bonus:pyro","dmg_bonus:hydro","dmg_bonus:electro",
  "dmg_bonus:cryo","dmg_bonus:anemo","dmg_bonus:geo","dmg_bonus:dendro"]};
EF.registry=()=>PL.state.caps?.engine_v2 || PL.state.caps?.plan_contract?.engine_v2 || RT.state.enums?.engine_v2 || {};
EF.commandMenu=()=>window.AppContextMenu?.forDocument?.(document,window)||null;
EF.menuError=error=>setStatus(`操作未完成：${String(error?.message||error||"未知错误")}`);
EF.openCommandMenu=(target,label,items)=>{
  const menu=EF.commandMenu();if(menu)return menu.open({target,trigger:target,returnFocus:target,label,items,onError:EF.menuError});
  const first=items.find(item=>item?.type!=="separator"&&item.danger!==true&&item.enabled!==false&&typeof item.run==="function");
  if(first)return first.run();setStatus("操作菜单暂不可用，请重新加载界面");return false;
};
EF.bindCommandMenu=(target,label,items)=>EF.commandMenu()?.bind(target,{label,items,onError:EF.menuError});
EF.attachCommandMenu=(target,label,items)=>{const more=UI.button("⋯",()=>EF.openCommandMenu(more,label,items()),"rc-row-menu");target.tabIndex=0;
  target.append(more);EF.bindCommandMenu(target,label,items);return target;};
EF.withCurrent=(rows,target,label,run)=>{const index=(rows||[]).indexOf(target);
  if(index<0){setStatus(`${label}已变化，请重新打开操作菜单`);return false;}return run(index);};
EF.withBinding=(binding,label,run)=>{const plan=EQ.plan(),rows=plan?.effect_bindings||[];
  return EF.withCurrent(rows,binding,label,index=>run({plan,binding:rows[index],index}));};
EF.locatePart=part=>{for(const binding of EQ.plan()?.effect_bindings||[]){const index=(binding.parts||[]).indexOf(part);
  if(index>=0)return {binding,part:binding.parts[index],index};}return null;};
EF.withPart=(part,label,run)=>{const located=EF.locatePart(part);if(!located){setStatus(`${label}已变化，请重新打开操作菜单`);return false;}return run(located);};
EF.damageTypes=()=>[...(EF.registry().damage_types||[]),...(EQ.plan()?.manual_template.type_definitions?.damage_types||[])];
EF.types=(container,selected,onSet)=>{EF.damageTypes().forEach(t=>container.append(UI.check(DS.prose(t.label,"伤害类型中文名待补"),
  (selected||[]).includes(t.id),yes=>onSet(yes?[...new Set([...(selected||[]),t.id])]:(selected||[]).filter(id=>id!==t.id)))));};
EF.scope=(box,effect,change)=>{const s=effect.applies_to;
  box.append(UI.field("伤害作用范围",UI.select([{value:"",label:"尚未明确"},{value:"all",label:"全部（仍受时间/目标限制）"},
    {value:"types",label:"按伤害类型"},{value:"hits",label:"明确事件"},{value:"none",label:"明确不关联"}],s?.mode||"",mode=>{
      effect.applies_to=mode?{mode,...(mode==="types"?{damage_type_ids:[]}:mode==="hits"?{hit_ids:[]}:{})}:null;change();})));
  if(s?.mode==="types"){const checks=UI.el("div",null,"eq-checks");EF.types(checks,s.damage_type_ids,v=>{s.damage_type_ids=v;change();});box.append(checks);}
  if(s?.mode==="hits")EF.hitChecks(box,s.hit_ids,v=>{s.hit_ids=v;change();});
  if(s?.mode==="none")box.append(UI.field("明确不关联的场景理由",UI.area(effect.extensions?.exclusion_reason||"",v=>{
    effect.extensions ||= {};effect.extensions.exclusion_reason=v;change();})));
};
EF.hitChecks=(box,selected,onSet)=>{const list=UI.el("div",null,"eq-checks");
  EF.candidateHits().forEach(h=>list.append(UI.check(`${h.t}秒 · ${DS.character(h.character,EQ.plan()?.manual_template||RT.tpl(),EQ.plan())} · ${DS.hit(h,undefined,EQ.plan()?.manual_template||RT.tpl())}`,
    (selected||[]).includes(h.id),yes=>onSet(yes?[...new Set([...(selected||[]),h.id])]:(selected||[]).filter(id=>id!==h.id)))));box.append(list);};
EF.defaultDelivery=()=>EQ.isPanel()?{kind:"buff",start:0,end:5,scope:"team",target:EQ.characterId,
    effect:{id:UI.id("effect"),benefit_type_id:"benefit.dmg_bonus.all",zone:"dmg_bonus",stat:"all",applies_to:null}}
    :{kind:"panel",target_character_id:EQ.characterId,stat:"atk",operation:"flat"};
EF.newPart=()=>({part_id:UI.id("part"),label:"新效果分项",state:"pending",rule:{kind:"constant",value:0},
  delivery:EF.defaultDelivery(),conditions:[],
  dependencies:{mode:"source_only",context_fingerprint:null,note:"待核对依据"},reason:null});
EF.report=()=>PL.state.report||EQ.lastReport||{};
EF.targetCaps=()=>PL.state.caps?.upstream_target_state_contract||PL.state.caps?.plan_contract?.upstream_target_state_contract;
EF.targetRecord=(report,id)=>(report?.upstream_compilations||report?.plan?.resolution?.upstream_compilations||[]).find(r=>r.binding_id===id);
EF.targetConfirmation=r=>r?.required_confirmation?.accepted_assumptions_fingerprint||r?.assumptions_fingerprint||r?.semantic_assumptions_fingerprint;
EF.renderTargetRecord=(box,r)=>{
  if(!r){box.append(UI.el("p","尚无当前草稿的数据库来源预览。"));return;}
  box.append(UI.el("p",`经典命中投影：${r.projection_status==="ready"?"可执行（含参考假设）":"待完善"}；完整来源：${r.whole_source_status==="partial"?"仅部分支持":"待核实"}。`),
    UI.el("p","普通扩散不获得此暴率；未支持分支不会因来源确认而自动实现。"));
  for(const issue of r.issues||[])box.append(UI.el("p",issue.message||"来源待完善","eq-issue"));
  for(const [i,branch] of (r.branches||[]).entries()){
    const title=branch.label||branch.name_zh||(branch.damage_scope==="Moon/Star"?"月曜及星系反应分支":`来源分支 ${i+1}`);
    const status=["supported_in_scope","supported","projected"].includes(branch.status)?"声明范围内执行":branch.status==="excluded_by_scope"?"本投影范围外，未执行":"见分支覆盖依据";
    box.append(UI.el("p",`${DS.prose(title,"来源分支")} · ${status}${typeof branch.value==="number"?` · 条件暴击率 ${(branch.value*100).toFixed(4)}%`:""}${branch.reason?`；${DS.prose(branch.reason.replaceAll("classic","经典").replaceAll("Moon/Star","月曜及星系反应"),"未解释原因见原文")}`:""}`));
  }
  box.append(UI.details("全部分支与未支持原因",r.branches||[]),UI.details("逐项参考假设",r.semantic_assumptions||[]),
    UI.details("固定来源及只读分项",{sources:r.fixed_effect_sources||[],parts:r.parts||[]}),
    UI.details("完整来源与覆盖证据",{provenance:r.provenance,coverage:r.coverage_certificate}));
};
EF.renderTargetUpstream=(card,binding)=>{
  const s=binding.upstream_recipe.scenario;
  card.append(UI.el("h5","经典命中目标条件投影"),UI.el("p",`场景用途：${MC.label(s.status)}。两件常驻已含面板，四件条件暴率未含；按每击目标状态计算。`),
    UI.button("编辑并重新核实目标条件来源",()=>EF.editTargetSource(binding)));
  EF.renderTargetRecord(card,EF.targetRecord(PL.state.report,binding.binding_id));
};
EF.editTargetSource=binding=>{
  const caps=EF.targetCaps(),original=EQ.plan();
  if(!caps?.default_scenario||!original||original.plan_version!==3){setStatus("请载入当前版本的目标条件来源能力");return;}
  if(binding.owner.kind!=="artifact_set"||binding.owner.required_count!==4){setStatus("此入口仅用于明确的四件套效果来源");return;}
  const row=UI.clone(binding.upstream_recipe||{schema_version:1,scenario:caps.default_scenario,ability_refs:[]});
  if(!binding.upstream_recipe){row.scenario.status="pending";row.scenario.source="";row.scenario.accepted_assumptions_fingerprint=null;}
  const baseline=JSON.stringify(PL.input(original)),live=PL.currentInputJson(),epoch=PL.state.sourceEpoch,path=RT.state.path,sha=PL.state.fileSha;
  const previewBox=UI.el("div"),errors=UI.el("div"),actions=UI.el("div");
  let revision=0,request=0,busy=false,closed=false,preview=null;
  const current=()=>!closed&&EQ.plan()===original&&JSON.stringify(PL.input(original))===baseline&&PL.currentInputJson()===live
    &&PL.state.sourceEpoch===epoch&&RT.state.path===path&&PL.state.fileSha===sha;
  const valid=()=>{if(current())return true;setStatus("方案、装备草稿或数据库状态已变化，请重新打开来源编辑");return false;};
  const invalidate=()=>{if(closed)return true;closed=true;request++;return true;};
  const dialog=window.AppDialogKernel?.create?.({title:"经典命中目标条件来源",
    description:"范围固定为：普通单目标、已接受的冻结参考模型、本人经典命中。两件常驻已含面板；四件条件暴率明确未含。",
    className:"mc-dialog",onRequestClose:invalidate})||null;
  if(!dialog){setStatus("当前还有窗口正在处理，请先完成或关闭后再编辑目标条件来源");return;}
  const close=()=>dialog.finish?.("cancel")??false;
  const candidate=remove=>{const plan=UI.clone(PL.input(original)),b=plan.effect_bindings.find(b=>b.binding_id===binding.binding_id);
    b.parts=[];b.coverage={state:"unreviewed",reviewed_by:null,source_fingerprint:null,note:null};
    if(remove)delete b.upstream_recipe;else b.upstream_recipe=UI.clone(row);return plan;};
  const confirmable=r=>!!EF.targetConfirmation(r)&&r.status!=="invalid"&&(r.issues||[]).every(i=>
    ["semantic_profile_acceptance_required","scenario_evidence_pending"].includes(i.code));
  const refresh=()=>{previewBox.innerHTML="";errors.innerHTML="";actions.innerHTML="";dialog.setBusy?.(busy);
    previewBox.append(UI.el("p",busy?"正在读取当前来源…":row.scenario.accepted_assumptions_fingerprint?"已记录本地来源确认；应用时仍将重新读取。":"尚未确认来源；用途状态须单独核实。"));
    EF.renderTargetRecord(previewBox,preview?.revision===revision?preview.record:null);
    if(preview?.revision===revision)for(const issue of preview.issues)previewBox.append(UI.el("p",issue.message||"方案仍有待完善项","eq-issue"));
    for(const [label,mode,disabled] of [["读取并预览目标条件来源","preview",false],
      ["确认所示来源与参考假设","confirm",!confirmable(preview?.record)||preview?.revision!==revision],["应用目标条件来源","apply",false]]){
      const b=UI.button(label,()=>run(mode));b.disabled=busy||disabled;actions.append(b);}
    if(binding.upstream_recipe){const b=UI.button("解除自动来源并返回手工处理",()=>run("remove"));b.disabled=busy;actions.append(b);}
    actions.append(UI.button("放弃并关闭",close));};
  const update=(key,value)=>{row.scenario[key]=value;row.scenario.accepted_assumptions_fingerprint=null;
    revision++;request++;busy=false;preview=null;refresh();};
  const run=async mode=>{
    if(busy||!valid())return;const shown=preview?.revision===revision?preview.record:null;
    if(mode==="confirm"&&!confirmable(shown)){setStatus("请先读取完整来源，核对结构与所列参考假设");return;}
    const remove=mode==="remove",proposed=candidate(remove),fp=mode==="confirm"?EF.targetConfirmation(shown):row.scenario.accepted_assumptions_fingerprint;
    if(mode==="confirm")proposed.effect_bindings.find(b=>b.binding_id===binding.binding_id).upstream_recipe.scenario.accepted_assumptions_fingerprint=fp;
    const token=++request,version=revision;busy=true;refresh();let response;
    try{response=await callApi("compose_equipment_plan",proposed);}catch(e){response={code:1,message:String(e)};}
    if(token!==request||version!==revision||!current()){if(!closed&&!current())valid();return;}
    busy=false;const report=response.data,record=EF.targetRecord(report,binding.binding_id);
    if(response.code||!report||!["ready","pending"].includes(report.state)||(!remove&&!record)){
      refresh();errors.append(UI.error(response.code?response:{code:1,message:"缺少可核验的完整来源回执"}));return;}
    preview={revision,record,issues:report.missing_inputs||[]};
    if(!remove&&fp&&EF.targetConfirmation(record)!==fp){
      row.scenario.accepted_assumptions_fingerprint=null;revision++;preview.revision=revision;refresh();
      errors.append(UI.el("p","来源或假设已变化，尚未确认或应用；请核对新预览后重新确认。","eq-error"));return;}
    if(mode==="confirm"){
      if(!confirmable(record)){refresh();errors.append(UI.el("p","来源仍有未解释结构，未记录确认。","eq-error"));return;}
      row.scenario.accepted_assumptions_fingerprint=fp;revision++;preview.revision=revision;refresh();return;}
    if(mode==="preview"){refresh();return;}
    const b=proposed.effect_bindings.find(b=>b.binding_id===binding.binding_id);
    if(remove)delete binding.upstream_recipe;else binding.upstream_recipe=UI.clone(b.upstream_recipe);
    binding.parts=[];binding.coverage=UI.clone(b.coverage);close();PL.touch();EQ.render();
    setStatus(remove?"已解除自动来源，分项待手工处理":"已应用来源声明；请预览完整方案。确认来源不代表整能力或场景已全部支持。");
  };
  dialog.appBody.append(UI.el("p","完整来源仅部分支持；未知分支全部保留。来源确认不会自动更改下方用途状态，也不会创建动作或增益时间窗。"),
    UI.field("场景与面板政策核实状态",UI.select([{value:"pending",label:"待核实"},{value:"assumed",label:"明确参考假设"},{value:"confirmed",label:"已核实用途"}],row.scenario.status,v=>update("status",v))),
    UI.field("用途与条件暴率未含面板的依据",UI.area(row.scenario.source,v=>update("source",v))),previewBox,errors,actions);
  refresh();dialog.focusInitial?.();
};
EF.startUpstream=binding=>EF.withBinding(binding,"效果规则",({binding:current})=>{
  const caps=PL.state.caps?.upstream_contract||PL.state.caps?.plan_contract?.upstream_contract;
  if(!caps?.default_scenario){setStatus("请重新载入当前版本的方案能力说明");return false;}
  const scenario=UI.clone(caps.default_scenario);
  ["single_player_fixed","all_targets_monsters","single_unique_provider","no_external_state_changes"].forEach(k=>scenario[k]=false);
  scenario.max_party_distance=null;current.upstream_recipe={schema_version:1,scenario,ability_refs:[]};PL.touch();EQ.render();return true;
});
EF.returnManual=binding=>EF.withBinding(binding,"效果规则",({binding:current})=>{
  delete current.upstream_recipe;current.parts=[];
  current.coverage={state:"unreviewed",reviewed_by:null,source_fingerprint:null,note:null};PL.touch();EQ.render();return true;
});
EF.bindingMenuItems=binding=>{
  const items=[],plan=EQ.plan(),current=(plan?.effect_bindings||[]).includes(binding),empty=current&&!binding.parts?.length;
  if(binding.upstream_recipe)items.push({id:`manual:${binding.binding_id}`,label:"返回手工处理",run:()=>EF.returnManual(binding)});
  else if(current){
    if(plan.plan_version===3&&binding.owner.kind==="artifact_set"&&binding.owner.required_count===4&&empty&&EF.targetCaps())
      items.push({id:`target-source:${binding.binding_id}`,label:"选择经典命中目标条件投影",run:()=>EF.withBinding(binding,"效果规则",({binding:row})=>EF.editTargetSource(row))});
    if(plan.plan_version===3&&binding.owner.kind==="artifact_set"&&binding.owner.required_count===4&&empty&&window.RB?.caps())
      items.push({id:`reaction-source:${binding.binding_id}`,label:"选择普通扩散反应收益来源",run:()=>EF.withBinding(binding,"效果规则",({binding:row})=>RB.edit(row))});
    if(plan.plan_version===3&&["weapon","artifact_set"].includes(binding.owner.kind)&&empty)
      items.push({id:`upstream-source:${binding.binding_id}`,label:"尝试上游自动装配",run:()=>EF.startUpstream(binding)});
  }
  if(items.length)items.push({type:"separator"});
  items.push({id:`remove-binding:${binding.binding_id}`,label:"移除这份规则",danger:true,run:()=>EF.withBinding(binding,"效果规则",({plan:active,index})=>{
    active.effect_bindings.splice(index,1);PL.touch();EQ.render();return true;})});
  return items;
};
EF.render=box=>{const plan=EQ.plan(),report=EF.report();
  box.append(UI.el("p","先预览生成当前装备效果清单。常规已核实规则可直接复用；未明确的部分需逐项处理。"));
  const autoStatic=PL.state.caps?.static_inclusion_contract||PL.state.caps?.plan_contract?.static_inclusion_contract;
  if(EQ.isPanel())box.append(UI.el("p",autoStatic?"常驻属性已包含在初始面板中：通过资料核验的来源显示“已计入初始面板”，无需重复登记；其余分项仍需核对依据。条件效果需明确数值、持续区间和作用范围。":"常驻属性已包含在初始面板中：相应分项选择“已含面板或明确不计入”并填写原因；条件效果仍需明确数值、持续区间和作用范围。","hint"));
  (report.effect_requirements||[]).forEach(req=>{
    if(plan.effect_bindings.some(b=>b.binding_id===req.binding_id))return;
    const card=UI.el("section",null,"eq-card");if(req.effect)card.append(ED.record(req.effect));
    const included=window.SI?.forRequirement(report,req);
    if(included)SI.renderRecord(card,included,plan.manual_template);
    card.append(UI.details("效果归属",req.owner),UI.button(included?"改为人工处理此来源":"添加此效果的处理分项",()=>{
      plan.effect_bindings.push(UI.clone(req.proposed_binding||{binding_id:req.binding_id,provider_character_id:req.provider_character_id,
        owner:req.owner,source_refs:req.source_refs,source_fingerprint:req.source_fingerprint,
        coverage:{state:"unreviewed",reviewed_by:null,source_fingerprint:null,note:null},parts:[]}));PL.touch();EQ.render();}));box.append(card);
  });
  plan.effect_bindings.forEach((binding,i)=>{
    const card=UI.el("section",null,"eq-card"),req=(report.effect_requirements||[]).find(r=>r.binding_id===binding.binding_id);
    card.append(UI.el("h4",`${DS.character(binding.provider_character_id,plan.manual_template,plan)} · 效果规则 ${i+1}`));
    if(req?.effect)card.append(ED.record(req.effect));card.append(UI.details("来源与归属",{owner:binding.owner,source_refs:binding.source_refs}));
    if(binding.upstream_recipe){EF.renderUpstream(card,binding);EF.attachCommandMenu(card,`效果规则 ${i+1} 操作`,()=>EF.bindingMenuItems(binding));box.append(card);return;}
    card.append(UI.field("整段覆盖备注",UI.area(DS.prose(binding.coverage.note||""),v=>{binding.coverage.note=v;PL.touch();})),
      UI.button(binding.coverage.state==="complete"?"重新确认整段覆盖":"确认这些分项覆盖当前原文",()=>{
        if(!binding.parts.length||!binding.coverage.note?.trim()){setStatus("请先填写分项和覆盖备注");return;}
        binding.coverage={...binding.coverage,state:"complete",reviewed_by:"user",source_fingerprint:req?.source_fingerprint||binding.source_fingerprint};
        binding.source_fingerprint=req?.source_fingerprint||binding.source_fingerprint;PL.touch();EQ.render();}));
    binding.parts.forEach((part,j)=>EF.renderPart(card,binding,part,j));
    card.append(UI.button("添加效果分项",()=>{binding.parts.push(EF.newPart());PL.touch();EQ.render();}));
    EF.attachCommandMenu(card,`效果规则 ${i+1} 操作`,()=>EF.bindingMenuItems(binding));box.append(card);
  });
  box.append(UI.button("添加角色/人工派生规则",()=>{plan.effect_bindings.push({binding_id:UI.id("binding"),provider_character_id:EQ.characterId,
    owner:{kind:"manual",note:"用户明确的本方案派生效果"},source_refs:[{kind:"manual",note:"待填写机制依据"}],source_fingerprint:null,
    coverage:{state:"unreviewed",reviewed_by:null,source_fingerprint:null,note:null},parts:[]});PL.touch();EQ.render();}));
  EF.manualDependencies(box);
};
EF.renderUpstream=(card,binding)=>{
  const recipe=binding.upstream_recipe,s=recipe.scenario;
  if(window.REM&&s.profile_id===REM.profile){REM.render(card,binding);return;}
  if(window.RB&&s.profile_id===RB.profile){RB.render(card,binding);return;}
  if((EF.targetCaps()?.profile_id&&s.profile_id===EF.targetCaps().profile_id)||s.profile_id==="equipment-target-state-classic-v1"){
    EF.renderTargetUpstream(card,binding);return;
  }
  const fresh=PL.state.report?.upstream_compilations?.find(c=>c.binding_id===binding.binding_id);
  card.append(UI.el("h5","上游自动来源"),UI.el("p","填写当前场景后预览。支持的结构会生成只读规则；未知机制会列出缺项。"));
  const changed=()=>{s.accepted_assumptions_fingerprint=null;PL.touch();EQ.render();};
  [["single_player_fixed","单人固定队伍"],["all_targets_monsters","伤害目标均为怪物"],
   ["single_unique_provider","本场景只有一名提供者施加这个唯一效果"],
   ["no_external_state_changes","轴内没有死亡、换装、离队等外部状态变化"]].forEach(([k,label])=>{
    card.append(UI.check(label,s[k]===true,v=>{s[k]=v;changed();}));});
  card.append(UI.field("队友与提供者的最大距离（米，需明确）",UI.input(s.max_party_distance,v=>{s.max_party_distance=v;changed();},true)),
    UI.field("若来源包含分类常驻增伤",UI.select([{value:"included",label:"初始面板已含（缺分类承载时待完善）"},
      {value:"execute",label:"明确未含这一分类部件，由引擎计算"}],s.static_policy,v=>{s.static_policy=v;changed();})));
  const plan=EQ.plan(),gear=plan.equipment_inputs[binding.provider_character_id],ref=gear?.character_ref;
  const abilities=plan.rotation_model?.abilities?.filter(a=>a.character===binding.provider_character_id&&a.action_type_id==="action.elemental_burst")||[];
  if(ref){EQ.loadDetail("character",ref.db_character_id,ref.version_line);
    const detail=EQ.getDetail("character",ref.db_character_id,ref.version_line);
    const skills=detail?.skills?.filter(k=>k.slot==="burst")||[];
    for(const a of abilities){const selected=(recipe.ability_refs||[]).find(r=>r.ability_id===a.id);
      card.append(UI.field(`爆发能力：${a.variant||"元素爆发"} · 数据库技能`,UI.select([{value:"",label:"请选择真实爆发技能"},
        ...skills.map(k=>({value:String(k.id),label:k.name||"元素爆发"}))],selected?String(selected.skill_id):"",v=>{
          recipe.ability_refs=(recipe.ability_refs||[]).filter(r=>r.ability_id!==a.id);
          if(v)recipe.ability_refs.push({ability_id:a.id,skill_id:Number(v)});changed();})));
    }
  }else card.append(UI.el("p","需要爆发触发时，请先在装备方案绑定数据库角色。","hint"));
  if(fresh){card.append(UI.el("p",fresh.status==="ready"?"结构可执行；游戏语义仍包含适配假设。":fresh.status==="inactive"?"当前装备未启用此来源。":"此来源待完善，未应用部分规则。"));
    for(const issue of fresh.issues||[])card.append(UI.el("p",issue.message||"来源待完善","eq-issue"));
    card.append(UI.details("逐项适配假设",fresh.semantic_assumptions||[]),
      UI.details("只读生成分项与机制",{parts:fresh.parts||[],rules:fresh.rules||[]}),
      UI.details("来源、字段消费与证明",{provenance:fresh.provenance,coverage:fresh.coverage_certificate}));
    const fp=fresh.assumptions_fingerprint||fresh.semantic_assumptions_fingerprint;
    if(fp&&s.accepted_assumptions_fingerprint!==fp)card.append(UI.button("接受所列适配假设并重新预览",async()=>{
      if(!PL.state.report?.upstream_compilations?.includes(fresh)){setStatus("预览已失效，请重新预览后核对");return;}
      s.accepted_assumptions_fingerprint=fp;PL.touch();await PL.preview();EQ.render();}));
  }else card.append(UI.el("p","尚无当前输入的有效预览。"));
};
EF.partMenuItems=part=>[{id:`remove-part:${part.part_id||"unknown"}`,label:"删除分项",danger:true,run:()=>EF.withPart(part,"效果分项",({binding,index})=>{
  binding.parts.splice(index,1);PL.touch();EQ.render();return true;})}];
EF.conditionMenuItems=(part,condition)=>[{id:`remove-condition:${condition.condition_id||"unknown"}`,label:"删除条件",danger:true,run:()=>EF.withPart(part,"效果分项",({part:current})=>
  EF.withCurrent(current.conditions,condition,"触发条件",index=>{current.conditions.splice(index,1);PL.touch();EQ.render();return true;}))}];
EF.intervalMenuItems=(part,condition,interval)=>[{id:`remove-interval:${condition.condition_id||"unknown"}`,label:"删除区间",danger:true,run:()=>EF.withPart(part,"效果分项",({part:current})=>
  EF.withCurrent(current.conditions,condition,"触发条件",()=>EF.withCurrent(condition.intervals,interval,"条件区间",index=>{
    condition.intervals.splice(index,1);PL.touch();EQ.render();return true;})))}];
EF.tuningMenuItems=match=>[{id:`remove-tuning:${match.id||"unknown"}`,label:"撤销此微调",danger:true,run:()=>{
  const matches=EQ.plan()?.rotation_tuning?.buff_hit_matches||[];return EF.withCurrent(matches,match,"轴例外微调",index=>{
    matches.splice(index,1);PL.touch();EQ.render();return true;});}}];
EF.renderPart=(box,binding,part,index)=>{const card=UI.el("article",null,"eq-part");box.append(card);
  const update=()=>{PL.touch();EQ.render();};
  card.append(DS.raw("效果分项说明", {label:part.label,reason:part.reason,dependencies:part.dependencies,conditions:part.conditions}));
  card.append(UI.field("分项名称",UI.input(DS.prose(part.label,"效果中文名待补"),v=>{part.label=v;PL.touch();})),
    UI.field("处理状态",UI.select([{value:"pending",label:"待处理"},{value:"resolved",label:"已明确"},{value:"excluded",label:EQ.isPanel()?"已含面板或明确不计入":"明确不计入"}],part.state,v=>{part.state=v;update();})),
    UI.field("依据 / 排除原因",UI.area(DS.prose(part.reason||""),v=>{part.reason=v||null;PL.touch();})));
  if(part.state!=="excluded") {
    const kind=part.rule?.kind||"constant";
    const kinds=DS.options("effect_rule_kinds",["constant","stat_scale","explicit_hit"]);
    if(EQ.plan().plan_version===3)kinds.push({value:"mechanics",label:"声明式机制"});
    if(EQ.plan().plan_version===3&&MC.caps?.schema?.properties?.fixed_effect_sources)kinds.push({value:"fixed_effect_source",label:"固定目标条件收益"});
    card.append(UI.field("有限规则",UI.select(kinds,kind,v=>{
      part.rule=v==="constant"?{kind:v,value:0}:v==="stat_scale"?{kind:v,from:{character_id:EQ.characterId,stage:"static",stat:"hp"},factor:0,offset:0}:
        v==="mechanics"?{kind:v,rule_id:""}:v==="fixed_effect_source"?{kind:v,fixed_source_id:""}:
        {kind:v,hit:{id:UI.id("hit"),damage_type_ids:null,t:0,character:EQ.characterId,skill:"additional",hit_name:"附加伤害",damage_system:"classic",element:"physical",multiplier:1}};
      if(["mechanics","fixed_effect_source"].includes(v))part.delivery={kind:v};
      else if(v==="explicit_hit")part.delivery={kind:"hit"};else if(["hit","mechanics","fixed_effect_source"].includes(part.delivery?.kind))part.delivery=EF.defaultDelivery();
      part.dependencies.mode=v==="stat_scale"?"stat_rule":"source_only";update();})));
    if(part.rule?.kind==="constant")card.append(UI.field("明确数值（比例用小数）",UI.input(part.rule.value,v=>{part.rule.value=v;PL.touch();},true)));
    else if(part.rule?.kind==="stat_scale"){
      const r=part.rule,grid=UI.el("div",null,"eq-grid");grid.append(
        UI.field("来源角色",UI.select(DS.characters(EQ.plan().manual_template,EQ.plan()),r.from.character_id,v=>{r.from.character_id=v;PL.touch();})),
        UI.field("读取阶段",UI.select([{value:"base",label:"裸基础面板"},{value:"static",label:"静态面板（不含转化产物）"}],r.from.stage,v=>{r.from.stage=v;PL.touch();})),
        UI.field("来源属性",UI.select(DS.options("stats",EF.stats.filter(s=>!s.includes(":"))),r.from.stat,v=>{r.from.stat=v;PL.touch();})));
      [["factor","系数"],["offset","固定偏移"],["min","下限（可空）"],["max","上限（可空）"]].forEach(([key,label])=>grid.append(UI.field(label,UI.input(r[key],v=>{
        if(v==null&&key!=="factor")delete r[key];else r[key]=v;PL.touch();},true))));card.append(grid);
    } else if(part.rule?.kind==="explicit_hit") EF.explicitHit(card,part.rule.hit,update);
    else if(part.rule?.kind==="mechanics"){
      const rules=EQ.plan().mechanics_model?.rules||[];
      card.append(UI.field("关联机制",UI.select([{value:"",label:"请选择已声明的机制"},...rules.map(r=>({value:r.id,label:DS.prose(r.name_zh,"机制中文名待补")}))],part.rule.rule_id,v=>{
        const rule=rules.find(r=>r.id===v);part.rule.rule_id=v;
        if(rule){rule.source_id=`${binding.binding_id}:${part.part_id}`;rule.owner=binding.provider_character_id;}
        update();})),UI.el("p","触发、计时、次数与取值由机制模型执行。此分项只负责确认来源；区间条件请写入机制声明。"));
    }
    else if(part.rule?.kind==="fixed_effect_source"){
      const sources=EQ.plan().mechanics_model?.fixed_effect_sources||[];
      card.append(UI.field("关联固定条件来源",UI.select([{value:"",label:"请选择已声明的固定来源"},...sources.map(s=>({value:s.id,label:DS.prose(s.name_zh,"固定来源中文名待补")}))],part.rule.fixed_source_id,v=>{
        const s=sources.find(s=>s.id===v);part.rule.fixed_source_id=v;
        if(s){s.source_id=`${binding.binding_id}:${part.part_id}`;s.owner=binding.provider_character_id;}
        update();})),UI.el("p","固定来源全程存在，条件按每次命中前的目标状态判断；此分项不设置增益时间窗。"));
    }
    if(!["explicit_hit","mechanics","fixed_effect_source"].includes(part.rule?.kind)) EF.delivery(card,part,update);
  }
  const dep=part.dependencies;card.append(UI.field("输入依赖",UI.select([
    {value:"source_only",label:"仅当前原文固定值"},{value:"stat_rule",label:"按属性自动重算"},{value:"manual_context",label:"当前场景人工折算"}],dep.mode,v=>{dep.mode=v;dep.context_fingerprint=null;update();})),
    UI.field("依赖说明",UI.input(DS.prose(dep.note||""),v=>{dep.note=v;PL.touch();})));
  if(dep.mode==="manual_context")card.append(UI.button("确认当前预览上下文",()=>{const fp=PL.state.report?.context_fingerprint;
    if(!fp){setStatus("请先生成当前预览，再确认上下文");return;}dep.context_fingerprint=fp;PL.touch();EQ.render();}));
  EF.conditions(card,part);
  EF.attachCommandMenu(card,`${DS.prose(part.label,"效果分项")}操作`,()=>EF.partMenuItems(part));
};
EF.delivery=(box,part,update)=>{let d=part.delivery;box.append(UI.field("交付方式",UI.select([{value:"panel",label:"静态面板"},{value:"buff",label:"时间轴增益"}],d?.kind||"panel",v=>{
    part.delivery=v==="panel"?{kind:v,target_character_id:EQ.characterId,stat:"atk",operation:"flat"}:{kind:v,start:0,end:5,scope:"team",target:EQ.characterId,
      effect:{id:UI.id("effect"),benefit_type_id:"benefit.dmg_bonus.all",zone:"dmg_bonus",stat:"all",applies_to:null}};update();})));
  if(!d)return;
  if(d.kind==="panel")box.append(UI.field("接受角色",UI.select(DS.characters(EQ.plan().manual_template,EQ.plan()),d.target_character_id,v=>{d.target_character_id=v;PL.touch();})),
    UI.field("属性",UI.select(DS.options("stats",EF.stats),d.stat,v=>{d.stat=v;PL.touch();})),
    UI.field("加算方式",UI.select([{value:"flat",label:"加算"},{value:"percent",label:"基础比例（仅攻生防）"}],d.operation,v=>{d.operation=v;PL.touch();})));
  if(d.kind==="buff"){
    box.append(UI.field("开始秒",UI.input(d.start,v=>{d.start=v;PL.touch();},true)),UI.field("结束秒",UI.input(d.end,v=>{d.end=v;PL.touch();},true)),
      UI.field("范围",UI.select(DS.options("buff_scopes",["team","frontline"]),d.scope,v=>{d.scope=v;PL.touch();})),
      UI.field("接受角色",UI.select([{value:"",label:"范围内全体"},...DS.characters(EQ.plan().manual_template,EQ.plan())],d.target,v=>{d.target=v||null;PL.touch();})),
      UI.field("收益类型",UI.select((EF.registry().benefit_types||[]).map(t=>({value:t.id,label:DS.prose(t.label,"收益类型中文名待补")})),d.effect.benefit_type_id,v=>{
        const t=EF.registry().benefit_types.find(t=>t.id===v);Object.assign(d.effect,{benefit_type_id:t.id,zone:t.zone,stat:t.stat});update();})));
    EF.scope(box,d.effect,update);
  }
};
EF.conditions=(box,part)=>{part.conditions.forEach(c=>{const row=UI.el("div",null,"eq-part");
  row.append(UI.field("条件说明",UI.input(DS.prose(c.description||""),v=>{c.description=v;PL.touch();})),
    UI.field("条件状态",UI.select([{value:"pending",label:"尚未确认"},{value:"confirmed",label:"已确认成立"}],c.state,v=>{c.state=v;PL.touch();})),
    UI.field("条件范围",UI.select([{value:"always",label:"全程"},{value:"intervals",label:"明确区间"}],c.scope,v=>{
      c.scope=v;c.intervals=v==="always"?[]:[{start:0,end:5}];PL.touch();EQ.render();})),
    UI.field("依据",UI.input(DS.prose(c.evidence?.[0]?.note||""),v=>{c.evidence=[{kind:"manual",note:v}];PL.touch();})));
  c.intervals.forEach((interval,j)=>{const intervalRow=UI.el("div",null,"eq-part");intervalRow.append(
    UI.field("起点秒",UI.input(interval.start,v=>{interval.start=v;PL.touch();},true)),
    UI.field("终点秒",UI.input(interval.end,v=>{interval.end=v;PL.touch();},true)));
    EF.attachCommandMenu(intervalRow,`条件区间 ${j+1} 操作`,()=>EF.intervalMenuItems(part,c,interval));row.append(intervalRow);});
  if(c.scope==="intervals")row.append(UI.button("添加区间",()=>{c.intervals.push({start:0,end:5});PL.touch();EQ.render();}));
  EF.attachCommandMenu(row,`${DS.prose(c.description,"触发条件")}操作`,()=>EF.conditionMenuItems(part,c));box.append(row);});
  box.append(UI.button("添加触发条件",()=>{part.conditions.push({condition_id:UI.id("condition"),description:"待说明条件",state:"pending",scope:"always",intervals:[],
    evidence:[{kind:"manual",note:"待补依据"}]});PL.touch();EQ.render();}));};
EF.explicitHit=(box,h,update)=>{["t","multiplier","count","cast_t"].forEach(k=>box.append(UI.field(({t:"时刻（秒）",multiplier:"倍率",count:"段数",cast_t:"施放时刻（秒）"})[k],UI.input(h[k],v=>{
    if(v==null)delete h[k];else h[k]=v;PL.touch();},true))));
  box.append(UI.field("事件名称",UI.input(DS.prose(h.hit_name||""),v=>{h.hit_name=v;PL.touch();})),
    UI.field("角色",UI.select(DS.characters(EQ.plan().manual_template,EQ.plan()),h.character,v=>{h.character=v;PL.touch();})),
    UI.field("元素",UI.select(DS.options("elements",RT.state.enums.elements),h.element,v=>{h.element=v;PL.touch();})),
    UI.field("倍率属性",UI.select(DS.options("stats",RT.state.enums.scaling_stats),h.scaling_stat||"atk",v=>{h.scaling_stat=v;PL.touch();})),
    UI.check("快照",h.snapshot,v=>{h.snapshot=v;if(v)h.cast_t??=h.t;else delete h.cast_t;PL.touch();}));
  EF.types(box,h.damage_type_ids,v=>{h.damage_type_ids=v.length?v:null;update();});
  const advanced=UI.el("details");advanced.append(UI.el("summary","原始数据 · 完整数值事件（无脚本）"));
  const area=UI.area(JSON.stringify(h,null,2),()=>{});advanced.append(area,UI.button("采用完整事件",()=>{
    try{const parsed=JSON.parse(area.value);if(!parsed||typeof parsed!=="object"||Array.isArray(parsed))throw Error("必须是事件对象");
      Object.keys(h).forEach(k=>delete h[k]);Object.assign(h,parsed);update();}catch(e){setStatus(`事件结构化数据无效：${e.message}`);}}));box.append(advanced);};
EF.manualDependencies=box=>{const plan=EQ.plan(),previews=EF.report().manual_dependency_previews||[];
  if(!(plan.manual_effect_dependencies||[]).length)return;
  box.append(UI.el("h4","旧手工增益依赖核对"));plan.manual_effect_dependencies.forEach(dep=>{
    const row=UI.el("div",null,"eq-card");row.append(UI.el("p",DS.buff(dep.effect_ref.buff_id,plan.manual_template)),
      UI.field("是否依赖装备",UI.select([{value:"unreviewed",label:"尚未核对"},{value:"source_only",label:"明确与装备无关"},
        {value:"manual_context",label:"依赖当前装备/排轴的人工值"}],dep.mode,v=>{dep.mode=v;dep.context_fingerprint=null;PL.touch();})),
      UI.field("核对依据",UI.input(DS.prose(dep.note||""),v=>{dep.note=v;PL.touch();})),UI.button("确认当前上下文",()=>{
        const p=previews.find(p=>JSON.stringify(p.effect_ref)===JSON.stringify(dep.effect_ref));
        dep.context_fingerprint=p?.context_fingerprint||PL.state.report?.context_fingerprint||null;PL.touch();EQ.render();}));box.append(row);});};
EF.renderTuning=box=>{const plan=EQ.plan();plan.rotation_tuning ||= {buff_hit_matches:[]};const matches=plan.rotation_tuning.buff_hit_matches;
  box.append(UI.el("p","默认没有额外微调，按已明确类型/规则处理。只有特定排轴的例外才添加关联。"));
  const report=EF.report();box.append(UI.details("默认候选与实际微调预览",report.match_previews||[]));
  matches.forEach((m,i)=>{const card=UI.el("section",null,"eq-card");
    const selectedId=m.effect_ref.fixed_source_id||m.effect_ref.buff_id;
    card.append(UI.field("增益",UI.select([{value:"",label:"请选择增益"},...EF.currentBuffs().map(b=>({value:b.id,label:`${b.fixed_source_id?"固定条件 · ":""}${DS.buff(b,EF.currentTemplate())}`}))],selectedId,v=>{
      const selected=EF.currentBuffs().find(b=>b.id===v);m.effect_ref=selected?.fixed_source_id?{fixed_source_id:v,effect_id:""}:{buff_id:v,effect_id:""};
      m.state="pending";m.basis_fingerprint=null;PL.touch();EQ.render();})),
      UI.field("增益内收益",UI.select([{value:"",label:"请选择收益"},...(EF.currentBuffs().find(b=>b.id===selectedId)?.effects||[]).map((e,i)=>({value:e.id,label:DS.effect(e,i)}))],m.effect_ref.effect_id,v=>{m.effect_ref.effect_id=v;m.state="pending";m.basis_fingerprint=null;PL.touch();})),
      DS.raw("关联标识与说明",m),
      UI.field("微调模式",UI.select([{value:"adjust",label:"增补 / 排除"},{value:"replace",label:"替换本轴完整关系"}],m.mode,v=>{
        m.mode=v;delete m.hit_ids;delete m.include_hit_ids;delete m.exclude_hit_ids;
        if(v==="adjust"){m.include_hit_ids=[];m.exclude_hit_ids=[];}else m.hit_ids=[];PL.touch();EQ.render();})),
      UI.field("理由与依据",UI.area(DS.prose(m.reason||""),v=>{m.reason=v;m.source_refs=[{kind:"manual",note:v}];PL.touch();})));
    if(m.effect_ref.fixed_source_id)card.append(UI.el("p","这是本轴手工例外：会保留自动条件结果和覆盖操作。不能绕过未知目标状态、角色归属或伤害体系限制。"));
    const keys=m.mode==="replace"?["hit_ids"]:["include_hit_ids","exclude_hit_ids"];
    keys.forEach(key=>{card.append(UI.el("h5",{hit_ids:"完整关联（空表示明确无关联）",include_hit_ids:"增补事件",exclude_hit_ids:"排除事件"}[key]));
      EF.hitChecks(card,m[key],v=>{m[key]=v;m.state="pending";PL.touch();EQ.render();});});
    card.append(UI.el("p",`状态：${DS.label("states",m.state)}`),UI.button("确认本次预览匹配",()=>{
      const preview=(PL.state.report?.match_previews||[]).find(p=>p.id===m.id||JSON.stringify(p.effect_ref)===JSON.stringify(m.effect_ref));
      if(!preview?.basis_fingerprint){setStatus("请先预览，再核对并确认匹配");return;}
      m.basis_fingerprint=preview.basis_fingerprint;m.state="confirmed";PL.touch();EQ.render();}));
    EF.attachCommandMenu(card,`轴例外微调 ${i+1} 操作`,()=>EF.tuningMenuItems(m));box.append(card);});
  box.append(UI.button("添加本轴例外",()=>{matches.push({id:UI.id("match"),effect_ref:{buff_id:"",effect_id:""},mode:"adjust",include_hit_ids:[],exclude_hit_ids:[],
    reason:"",source_refs:[{kind:"manual",note:"待填写依据"}],state:"pending",basis_fingerprint:null});PL.touch();EQ.render();}));
  box.append(UI.details("可定位的生成输出",report.contribution_report?.generated_outputs||[]));
  box.append(UI.details("可定位的当前输出与手工增益",(plan.resolved_template||report.plan?.resolved_template||plan.manual_template).buffs||[]));
};

EF.currentTemplate=()=>EQ.plan()?.resolved_template||EF.report().plan?.resolved_template||EQ.plan()?.manual_template||RT.tpl();
EF.currentBuffs=()=>{
  const plan=EQ.plan(),buffs=EF.currentTemplate()?.buffs||[];
  if(plan?.plan_version!==3)return buffs;
  const rules=EF.report().mechanics_model?.rules||plan.mechanics_model?.rules||[];
  const ordinary=EF.report().plan?.resolved_template?.buffs||plan.manual_template.buffs||[];
  const fixed=EF.report().mechanics_model?.fixed_effect_sources||plan.mechanics_model?.fixed_effect_sources||[];
  return [...ordinary,...[...rules,...fixed].map(r=>({id:r.id,name:r.name_zh,source:r.source,
    ...(fixed.includes(r)?{fixed_source_id:r.id}:{}),
    effects:r.effects.map(e=>({...e,name:(EF.registry().benefit_types||[]).find(t=>t.id===e.benefit_type_id)?.label||"机制收益"}))}))];
};
EF.candidateHits=()=>{
  const plan=EQ.plan(),hits=plan?.manual_template.hits||RT.tpl()?.hits||[];
  if(plan?.plan_version!==3||!plan.mechanics_model)return hits;
  const model=plan.mechanics_model,result=[],byId=new Map(hits.map(h=>[h.id,h]));
  for(const ref of model.hit_events){const h=byId.get(ref.hit_id);if(h)result.push(h);}
  for(const pattern of model.hit_patterns){const h=byId.get(pattern.prototype_hit_id),action=plan.rotation_model?.actions?.find(a=>a.id===pattern.action_id);
    if(!h||!action)continue;
    pattern.offsets.forEach((offset,i)=>result.push({...h,id:`${pattern.id}:${i+1}`,t:action.start+offset,hit_name:`${h.hit_name||"连续命中"} · 第 ${i+1} 击`}));
  }return result;
};
