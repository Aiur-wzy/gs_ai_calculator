"use strict";
window.RB={profile:"equipment-ordinary-swirl-benefits-v1"};
RB.caps=()=>PL.state.caps?.upstream_reaction_benefit_contract||PL.state.caps?.plan_contract?.upstream_reaction_benefit_contract;
RB.record=(report,id)=>(report?.upstream_compilations||[]).find(r=>r.binding_id===id&&r.profile_id===RB.profile);
RB.confirmation=r=>r?.required_confirmation?.accepted_assumptions_fingerprint;
RB.renderRecord=(box,r)=>{
  if(!r){box.append(UI.el("p","尚无当前草稿的反应收益来源预览。"));return;}
  box.append(UI.el("p",`普通冰水原目标收益：${r.status==="ready"?"声明范围内就绪":"待完善"}；完整来源仅部分支持。`),
    UI.el("p","新减抗要求持有者在场；既有减抗继续计时，持有者的常驻扩散加成也可在后台生效。"));
  for(const issue of r.issues||[])box.append(UI.el("p",issue.message||"来源待完善","eq-issue"));
  for(const part of r.static_parts||[])if(typeof part.rule?.value==="number")box.append(UI.el("p",`持有者常驻扩散伤害加成：${(part.rule.value*100).toFixed(2)}%。`));
  for(const rule of r.rules||[])box.append(UI.el("p",`${DS.prose(rule.name_zh,"反应减抗")}：${((rule.effects?.[0]?.value||0)*100).toFixed(2)}%，每次对应扩散刷新，持续 ${rule.timing.duration} 秒。`));
  box.append(UI.details("来源分支与未支持范围",r.branches||[]),UI.details("本次场景与参考假设",r.semantic_assumptions||[]),
    UI.details("只读自动收益与规则",{parts:r.parts||[],rules:r.rules||[]}),
    UI.details("来源与覆盖证据",{provenance:r.provenance,coverage:r.coverage_certificate}));
};
RB.render=(card,binding)=>{
  card.append(UI.el("h5","普通扩散的装备收益"),UI.el("p","按实际冰水扩散触发减抗，自动分别刷新和到期；常驻反应加成按装备来源提供。"),
    UI.button("编辑并核实反应收益来源",()=>RB.edit(binding)));
  RB.renderRecord(card,RB.record(PL.state.report,binding.binding_id));
};
RB.edit=binding=>{
  const caps=RB.caps(),original=EQ.plan();
  if(!caps?.default_scenario||!original||original.plan_version!==3){setStatus("请载入当前版本的反应收益来源能力");return;}
  if(binding.owner.kind!=="artifact_set"||binding.owner.required_count!==4){setStatus("此入口用于明确的四件套装备效果来源");return;}
  const row=UI.clone(binding.upstream_recipe||{schema_version:1,scenario:caps.default_scenario});
  if(row.scenario.profile_id!==RB.profile){setStatus("请先解除已有的其他自动来源，再选择反应收益方案");return;}
  const baseline=JSON.stringify(PL.input(original)),live=PL.currentInputJson(),epoch=PL.state.sourceEpoch,path=RT.state.path,sha=PL.state.fileSha;
  const previewBox=UI.el("div"),errors=UI.el("div"),actions=UI.el("div");
  let revision=0,request=0,busy=false,closed=false,preview=null;
  const invalidate=()=>{if(closed)return true;closed=true;request++;return true;};
  const dialog=window.AppDialogKernel?.create?.({title:"普通冰水扩散的装备收益来源",
    description:"固定单人队伍、普通单目标、无命中停滞；两件套常驻已含面板，四件套反应加成未另行计入。火雷、星烁、传播和多提供者暂未支持。",
    className:"mc-dialog",onRequestClose:invalidate})||null;
  if(!dialog){setStatus("当前还有窗口正在处理，请先完成或关闭后再编辑反应收益来源");return;}
  const current=()=>!closed&&EQ.plan()===original&&JSON.stringify(PL.input(original))===baseline&&PL.currentInputJson()===live
    &&PL.state.sourceEpoch===epoch&&RT.state.path===path&&PL.state.fileSha===sha;
  const valid=()=>{if(current())return true;setStatus("方案、装备草稿或数据库状态已变化，请重新打开来源编辑");return false;};
  const close=()=>{invalidate();return dialog.finish?.("cancel")??true;};
  const candidate=remove=>{const plan=UI.clone(PL.input(original)),b=plan.effect_bindings.find(b=>b.binding_id===binding.binding_id);
    b.parts=[];b.coverage={state:"unreviewed",reviewed_by:null,source_fingerprint:null,note:null};
    if(remove)delete b.upstream_recipe;else b.upstream_recipe=UI.clone(row);return plan;};
  const confirmable=r=>!!RB.confirmation(r)&&r.status!=="invalid"&&(r.issues||[]).every(i=>(caps.confirmable_issue_codes||[]).includes(i.code));
  const refresh=()=>{previewBox.innerHTML="";errors.innerHTML="";actions.innerHTML="";
    previewBox.append(UI.el("p",busy?"正在核对当前装备来源…":row.scenario.accepted_assumptions_fingerprint?"已记录来源确认，应用时仍会重新读取。":"尚未确认来源；未知场景条件仍须单独补全。"));
    RB.renderRecord(previewBox,preview?.revision===revision?preview.record:null);
    if(preview?.revision===revision)for(const issue of preview.issues)previewBox.append(UI.el("p",issue.message||"方案仍有待完善项","eq-issue"));
    for(const [label,mode,disabled] of [["读取并预览反应收益来源","preview",false],
      ["确认所示来源与参考假设","confirm",!confirmable(preview?.record)||preview?.revision!==revision],["应用反应收益草稿","apply",false]]){
      const button=UI.button(label,()=>run(mode));button.disabled=busy||disabled;actions.append(button);}
    if(binding.upstream_recipe){const button=UI.button("解除自动来源并返回手工处理",()=>run("remove"));button.disabled=busy;actions.append(button);}
    actions.append(UI.button("放弃并关闭",close));};
  const update=(key,value)=>{row.scenario[key]=value;row.scenario.accepted_assumptions_fingerprint=null;
    revision++;request++;busy=false;preview=null;refresh();};
  const run=async mode=>{
    if(busy||!valid())return;const shown=preview?.revision===revision?preview.record:null;
    if(mode==="confirm"&&!confirmable(shown)){setStatus("先读取来源并核对所有未解释结构");return;}
    const remove=mode==="remove",proposed=candidate(remove),fp=mode==="confirm"?RB.confirmation(shown):row.scenario.accepted_assumptions_fingerprint;
    const commitRecipe=UI.clone(row);
    if(mode==="confirm"){
      proposed.effect_bindings.find(b=>b.binding_id===binding.binding_id).upstream_recipe.scenario.accepted_assumptions_fingerprint=fp;
      commitRecipe.scenario.accepted_assumptions_fingerprint=fp;}
    const token=++request,version=revision;busy=true;refresh();let response;
    try{response=await callApi("compose_equipment_plan",UI.clone(proposed));}catch(e){response={code:1,message:String(e)};}
    if(token!==request||version!==revision||!current()){if(!closed&&!current())valid();return;}
    busy=false;const report=response.data,record=RB.record(report,binding.binding_id);
    if(response.code||!report||!["ready","pending"].includes(report.state)||(!remove&&!record)){
      refresh();errors.append(UI.error(response.code?response:{code:1,message:"缺少可核验的反应收益来源回执"}));return;}
    preview={revision,record,issues:report.missing_inputs||[]};
    if(!remove&&fp&&RB.confirmation(record)!==fp){
      row.scenario.accepted_assumptions_fingerprint=null;revision++;preview.revision=revision;refresh();
      errors.append(UI.el("p","来源或场景已变化，尚未应用；请复核当前预览后重新确认。","eq-error"));return;}
    if(mode==="confirm"){
      if(!confirmable(record)){refresh();errors.append(UI.el("p","来源仍有未解释结构，未记录确认。","eq-error"));return;}
      row.scenario.accepted_assumptions_fingerprint=fp;revision++;preview.revision=revision;refresh();return;}
    if(mode==="preview"){refresh();return;}
    if(remove)delete binding.upstream_recipe;else binding.upstream_recipe=commitRecipe;
    binding.parts=[];binding.coverage={state:"unreviewed",reviewed_by:null,source_fingerprint:null,note:null};
    close();PL.touch();EQ.render();setStatus(remove?"已解除自动来源，效果待手工处理":"已应用来源草稿；请预览完整方案。来源确认不会补齐未知场景或手写收益时窗。");
  };
  const field=(label,key,choices)=>UI.field(label,UI.select(choices.map(([value,label])=>({value,label})),row.scenario[key]??"",value=>update(key,value||null)));
  const target=original.mechanics_model?.elemental_model?.target;
  dialog.appBody.append(UI.button("← 返回装备效果",close),
    UI.el("p","系统读取收益值和持续时间，并按实际反应刷新。空间范围、初始减抗和原子命中顺序必须明确，不会从持续时间反推动作。"),
    field("对应的元素目标","target_id",[["","尚未明确"],...(target?.id?[[target.id,"当前唯一元素目标"]]:[])]),
    field("原目标是否满足来源范围","origin_target_range",[["unknown","尚未明确"],["within_source_range","整个轴窗口满足原始来源范围"]]),
    field("窗口开始前的该源减抗","initial_source_effects",[["unknown","尚未明确"],["none","开始前没有该源减抗"]]),
    field("输入命中的原子顺序","event_order_policy",[["unknown","尚未明确"],["declared_atomic_hit_order","已校准每个原子命中的先后"]]),
    field("场景核实状态","status",[["pending","待核实"],["assumed","明确参考假设"],["confirmed","已核实用途"]]),
    UI.field("范围、初态与事件顺序依据",UI.area(row.scenario.source,value=>update("source",value))),previewBox,errors,actions);
  refresh();dialog.focusInitial?.();
};
