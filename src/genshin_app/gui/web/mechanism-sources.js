"use strict";
// Native references only. Fresh Python compilation owns rules, pools and sampling.
window.MS={};
MS.caps=()=>PL.state.caps?.mechanism_source_contract;
MS.sources=plan=>plan?.mechanism_sources||[];
MS.records=report=>report?.mechanism_compilations||[];
MS.commandMenu=()=>window.AppContextMenu?.forDocument?.(document,window)||null;
MS.menuError=error=>setStatus(`操作未完成：${String(error?.message||error||"未知错误")}`);
MS.openCommandMenu=(target,label,items)=>{
  const menu=MS.commandMenu();if(menu)return menu.open({target,trigger:target,returnFocus:target,label,items,onError:MS.menuError});
  const first=items.find(item=>item?.type!=="separator"&&item.danger!==true&&item.enabled!==false&&typeof item.run==="function");
  if(first)return first.run();setStatus("操作菜单暂不可用，请重新加载界面");return false;
};
MS.bindCommandMenu=(target,label,items)=>MS.commandMenu()?.bind(target,{label,items,onError:MS.menuError});
MS.withCurrentSource=(target,run)=>{
  const index=MS.sources(PL.state.plan).indexOf(target);
  if(index<0){setStatus("通用机制来源已变化，请重新打开操作菜单");return false;}
  return run(index);
};
MS.sourceMenuItems=row=>[{id:`edit-mechanism-source:${row.id||"unknown"}`,label:"编辑通用机制来源",run:()=>MS.withCurrentSource(row,index=>MS.edit(index))}];
MS.sourceRow=(content,row,index)=>{
  const label=`通用机制来源 ${index+1} 操作`,items=()=>MS.sourceMenuItems(row),container=UI.el("div",null,"eq-toolbar rc-definition-row");
  const more=UI.button("⋯",()=>MS.openCommandMenu(more,label,items()),"rc-row-menu");container.tabIndex=0;
  container.append(content,more);MS.bindCommandMenu(container,label,items);return container;
};
MS.supportsCompiler=value=>(MS.caps()?.supported_compilers||[MS.caps()?.compiler]).includes(value);
MS.record=(report,id)=>MS.records(report).find(row=>row.id===id&&MS.supportsCompiler(row.compiler));
MS.fp=value=>typeof value==="string"&&/^[0-9a-f]{64}$/.test(value);
MS.sceneLabels={target_kind:"目标类型",party_mode:"队伍模式",all_party_alive:"所有队员存活",
  all_party_in_apply_predicate:"施加时满足来源范围条件",all_party_in_retention_predicate:"保有期间满足来源范围条件",
  initial_effect:"轴开始前的该来源效果",no_external_source_changes:"没有额外施加、移除或延长该来源",
  no_source_state_changes:"没有引起来源重施加或恢复的状态变化",
  no_provider_transformative_damage:"提供者不产生当前来源尚未覆盖的转化反应伤害（计算时复核）",
  hitlag_policy:"卡肉计时模型",origin_target_in_source_range:"原目标满足来源距离条件",
  recipient_bonus_policy:"接收者面板未重复包含本收益",stat_basis:"取样属性的面板口径",event_order_policy:"事件顺序政策"};
MS.valueLabels={ordinary_non_animal_monster:"普通非动物单目标敌人（不含怪物装备部件）",single_player_fixed:"单人固定队伍",
  none:"没有已有来源效果",zero_hitlag_reference:"采用零卡肉参考",not_included:"面板未计入本收益",
  fixed_panel_nonextra_equals_total:"固定面板：非额外属性等于给定总值",declared_atomic_hit_order:"采用已声明的原子命中顺序"};
MS.status=value=>({ready:"就绪",pending:"待完善",missing:"缺少资料",approved:"已审核",revoked:"审核已撤销",draft:"草稿",assumed:"明确参考假设",confirmed:"已核实用途"}[value]||"待核实");
MS.stat=value=>({atk:"攻击力",def:"防御力",hp:"生命值",em:"元素精通",energy_recharge:"元素充能效率"}[value]||"受审属性");
MS.rules=profile=>profile?.rule_templates||profile?.compiler_result?.rule_templates||profile?.descriptor?.rule_templates||[];
MS.elements=values=>(values||[]).map(value=>({physical:"物理",pyro:"火",hydro:"水",anemo:"风",electro:"雷",dendro:"草",cryo:"冰",geo:"岩"}[value]||"未识别元素")).join("、");
MS.ruleText=item=>{
  const rule=item.rule||item,trigger=({action_start:"动作施放时",elemental_reaction:"元素反应发生时",hit:rule.trigger?.phase==="post"?"命中处理后":"命中处理前"}[rule.trigger?.event]||"受审触发条件"),timing=rule.timing||{};
  const effects=(rule.effects||[]).map(effect=>{
    const part=effect.benefit_type_id?.split("."),label=part?.[1]==="stat_pct"?`基础${MS.stat(part[2])}百分比加成`:({base_add:"基础区加算",dmg_bonus:"伤害增伤",res_shred:"抗性削减",em:"元素精通变化",atk:"攻击力变化",def:"防御力变化"}[part?.[1]]||"受审收益");
    const value=effect.value;return `${label}：${typeof value==="number"?value:value?.kind==="stat"?`提供者${value.sample==="current"?"当前":"触发时"}${MS.stat(value.stat)} × ${value.factor??"待核实"}`:"数值等待来源核对"}`;
  }).join("；");
  const quota=rule.quota?`；${rule.quota.scope==="per_beneficiary"?"每位受益者独立":"共享"} ${rule.quota.capacity??"待核实"} 次，刷新${rule.quota.refresh==="reset"?"重置次数":"按受审规则"}${rule.quota.consume?.elements?.length?`，${MS.elements(rule.quota.consume.elements)}元素符合条件的命中消费`:""}`:"";
  const reaction=rule.trigger?.reaction_elements?.length?`（${MS.elements(rule.trigger.reaction_elements)}元素；${(rule.trigger.reaction_types||[]).map(t=>({swirl:"扩散",vaporize:"蒸发",melt:"融化",freeze:"冻结",crystallize:"结晶",stellar_superconduct:"星超导",stellar_swirl:"星扩散",lunar_electro:"月感电",lunar_bloom:"月绽放",lunar_crystallize:"月结晶"}[t]||"受审反应")).join("、")}）`:"";
  const recipients=rule.effects?.length&&rule.effects.every(e=>e.domain==="enemy")?"作用于目标敌人":({team:"队伍受益",self:"提供者自身受益",characters:"指定角色受益"}[rule.beneficiaries?.mode]||"受益者按来源核对");
  const stacking=rule.stacking?.policy==="add_and_refresh"?`每次增加 ${rule.stacking.add} 层，上限 ${rule.stacking.max} 层，所有层共同刷新`:({replace:"再次触发替换",refresh:"再次触发刷新",independent:"每层独立到期",stack:"按来源叠层"}[rule.stacking?.policy]||"叠层按受审规则");
  const cooldown=timing.cooldown?`；触发间隔至少 ${timing.cooldown} 秒`:"",presence=rule.trigger?.owner_presence==="frontline"?"；提供者须在前台":"";
  const qualification=rule.beneficiaries?.eligibility && window.EFX
    ? `；${EFX.eligibilityDescription(rule.beneficiaries,id=>window.DS?.character?.(id)||"指定角色（详见资料）").join("；")}` : "";
  return `${trigger}${reaction}${presence}；${recipients}${qualification}；持续 ${timing.duration??"待核实"} 秒${cooldown}；${effects}${quota}；${stacking}。`;
};
MS.scopes=(plan,actor,evidence={})=>{
  const found=new Map(),gear=plan?.equipment_inputs?.[actor],add=(target_type,target_id,version_line)=>{
    if(!Number.isSafeInteger(target_id)||target_id<1||!["release","beta"].includes(version_line))return;
    const key=`${version_line}:${target_type}:${target_id}`;found.set(key,{key,target_type,target_id,version_line,
      label:`${{character:"角色",weapon:"武器",artifact_set:"套装"}[target_type]}资料 · ${version_line==="release"?"正式线":"测试线"}`});};
  add("character",gear?.character_ref?.db_character_id,gear?.character_ref?.version_line);
  const line=gear?.data_version?.version_line||gear?.character_ref?.version_line;
  add("weapon",gear?.weapon?.weapon_id,line);
  for(const set of gear?.artifact_sets||[])add("artifact_set",set.set_id,line);
  for(const [slot,selected] of Object.entries(gear?.artifacts||{})){
    const detail=evidence[actor]?.artifacts?.[slot],item=detail?.artifact;
    if(selected?.state!=="empty"&&item?.id===selected?.artifact_id&&item?.equip_type===slot&&
       detail?.data_version?.version_line===line&&detail.data_version.upstream_commit===gear?.data_version?.upstream_commit)
      add("artifact_set",item.set_id,line);
  }
  const hits=plan?.manual_template?.hits||[];
  for(const source of plan?.hit_parameter_sources||[])if(hits.some(hit=>hit.id===source.hit_id&&hit.character===actor))add("character",source.selection?.character_id,source.version_line);
  for(const source of plan?.attack_metadata_sources||[]){const profile=plan.action_event_profiles?.find(p=>p.id===source.action_event_profile_id);
    if(hits.some(hit=>hit.id===profile?.prototype_hit_id&&hit.character===actor))add("character",source.selection?.character_id,source.version_line);}
  return [...found.values()];
};
MS.profilePreview=(box,profile)=>{
  box.append(UI.el("h4","数据库受审机制资料"),UI.el("p",`资料：${MS.status(profile.source_status)}；审核：${MS.status(profile.review_status)}；模板：${MS.status(profile.materialization_status)}。模板就绪不代表本轴已可执行。`));
  MS.rules(profile).forEach(item=>box.append(UI.el("p",MS.ruleText(item))));
  box.append(UI.el("p","仅覆盖受审的局部知识；审核或模板就绪不会自动取得本轴执行权，仍须由 Agent 材料化。"));
  if(profile.issues?.length)AE.issues(box,profile.issues,"来源待办");
  box.append(UI.details("资料标识、修订、完整原文与未覆盖范围",profile));
};
MS.preview=(box,row,plan=PL.state.plan)=>{
  if(!row){box.append(UI.el("p","先从角色绑定的数据库身份发现来源，再核对本轴父动作、场景与校准。未知条件可保存草稿。"));return;}
  box.append(UI.el("h4",`${DS.character(row.actor,plan?.manual_template,plan)} · 通用机制知识候选`),
    UI.el("p",`${row.candidate_status==="ready"?"候选资料已可供 Agent 审阅":"候选仍待完善"}；当前没有执行授权，也没有向计算模型交付规则。资料覆盖仍为局部。`));
  MS.rules(row.projection).forEach(item=>box.append(UI.el("p",MS.ruleText(item))));
  const parents=row.scope?.calibration?.parents||row.parent_graph?.parents||[];
  const contact=row.projection?.execution_qualification?.schema_version===3;
  if(parents.length){const table=UI.el("table"),head=UI.el("tr");["父动作","动作时点（秒）",contact?"候选命中时点（秒）":"生效时点（秒）"].forEach(x=>head.append(UI.el("th",x)));table.append(head);
    parents.slice(0,100).forEach(p=>{const tr=UI.el("tr"),tick=contact?p.absolute_tick:p.activation_tick;[AE.actionName(p.action_id,plan),Number.isFinite(p.action_tick)?p.action_tick/1e6:"见完整回执",Number.isFinite(tick)?tick/1e6:"随实际触发"]
      .forEach(x=>tr.append(UI.el("td",x)));table.append(tr);});box.append(table);
    if(parents.length>100)box.append(UI.el("p",`显示前100项，共${parents.length}项。`));
    if(contact)box.append(UI.el("p","候选命中不等于收益已触发。落空、前后台和内部冷却由计算判断；实际层数及续期见执行流水。"));}
  AE.issues(box,row.issues,"通用机制待办");box.append(UI.details("完整来源、资格、范围、取样与父动作证明",row));
};
MS.renderReceipt=(box,report,plan=PL.state.plan)=>{
  for(const row of MS.records(report)){const card=UI.el("section",null,"eq-card");MS.preview(card,row,plan);box.append(card);}
  if(report?.mechanism_summary){const card=UI.el("section",null,"eq-card");card.append(UI.el("h4","通用机制实际执行"));
    for(const [key,label] of Object.entries({activations:"实际激活",consumed_hits:"实际消费次数的命中",applied_benefits:"实际应用收益",sampled_events:"实际取样事件"}))
      if(Object.hasOwn(report.mechanism_summary,key))card.append(UI.el("p",`${label}：${report.mechanism_summary[key]}`));
    const owned=new Set(MS.records(report).flatMap(r=>(r.rules||[]).map(rule=>rule.id))),ledger=(report.mechanics_trace||[]).filter(r=>owned.has(r.rule_id)&&["activation","quota"].includes(r.type));
    if(ledger.length){const table=UI.el("table"),head=UI.el("tr");["时点（秒）","实际事件","次数或到期"].forEach(x=>head.append(UI.el("th",x)));table.append(head);
      ledger.slice(0,100).forEach(r=>{const tr=UI.el("tr");[r.t,r.type==="activation"?"激活／刷新":r.consumed?"消费次数":"本次未消费",r.type==="quota"?`${r.before} → ${r.after}`:`${r.layers??"待核实"} 层；到期 ${[...new Set(r.expires_at||[])].join("、")} 秒`].forEach(x=>tr.append(UI.el("td",x)));table.append(tr);});card.append(table);}
    const samples=(report.sampling_trace||[]).filter(r=>owned.has(r.rule_id));
    if(samples.length){const table=UI.el("table"),head=UI.el("tr");["事件时点（秒）","取样属性","取样时点（秒）","来源值","换算收益"].forEach(x=>head.append(UI.el("th",x)));table.append(head);
      samples.slice(0,100).forEach(r=>{const tr=UI.el("tr");[r.t,`${DS.character(r.character,plan?.manual_template,plan)} · ${MS.stat(r.stat)}`,r.sample_t,r.source_value,r.value].forEach(x=>tr.append(UI.el("td",x)));table.append(tr);});card.append(table);}
    if(ledger.length>100||samples.length>100)card.append(UI.el("p","流水每表显示前100项，完整事件见下方详情。"));
    card.append(UI.el("p","以已执行事件为准；未命中、隐藏伤害与次数消费分别判断。工作簿不重演状态机，改变动作或提供者属性后请重新计算与生成。"),
      UI.details("执行统计",report.mechanism_summary),UI.details("完整机制与取样流水",{events:ledger,samples}));box.append(card);}
};
MS.validCatalog=(data,scope,page)=>!!data&&data.component==="mechanic_semantics"&&["ready","missing"].includes(data.status)&&
  data.snapshot?.version_line===scope.version_line&&data.page===page&&data.page_size===20&&Number.isSafeInteger(data.total)&&data.total>=0&&
  Array.isArray(data.items)&&data.items.length<=20&&data.items.length<=data.total&&new Set(data.items.map(r=>r.id)).size===data.items.length&&
  data.items.every(r=>typeof r.id==="string"&&/^[a-z0-9][a-z0-9._-]{0,79}$/.test(r.id)&&Number.isSafeInteger(r.revision)&&r.revision>0&&
    r.subject?.target_type===scope.target_type&&r.subject?.target_id===scope.target_id&&["draft","pending","approved","revoked"].includes(r.review?.status));
MS.validProfile=(data,row,scope)=>!!data&&["ready","pending"].includes(data.status)&&data.selection?.mechanism_id===row.mechanism_id&&
  (row.revision===null||data.selection.revision===row.revision)&&data.snapshot?.version_line===row.version_line&&
  (data.descriptor===null||Number.isSafeInteger(data.selection.revision)&&data.selection.revision>0&&data.descriptor?.mechanism_id===row.mechanism_id&&data.descriptor?.revision===data.selection.revision)&&
  (!scope||!data.source_binding||data.source_binding.subject?.target_type===scope.target_type&&data.source_binding.subject?.target_id===scope.target_id);
MS.materialMatches=(material,row)=>material===null||!!material&&material.selection?.mechanism_id===row.mechanism_id&&
  material.snapshot?.version_line===row.version_line&&(row.revision===null||material.selection?.revision===row.revision);
MS.validReport=(report,plan)=>!!report&&["ready","pending"].includes(report.state)&&(!MS.sources(plan).length&&!Object.hasOwn(report,"mechanism_compilations")||Array.isArray(report.mechanism_compilations)&&
  report.mechanism_compilations.length===MS.sources(plan).length&&new Set(report.mechanism_compilations.map(r=>r.id)).size===report.mechanism_compilations.length&&
  report.mechanism_compilations.every(r=>MS.sources(plan).some(s=>s.id===r.id&&s.actor===r.actor&&MS.materialMatches(r.source_material,s))&&MS.supportsCompiler(r.compiler)&&
    ["ready","pending"].includes(r.status)&&Array.isArray(r.issues)&&Array.isArray(r.rules)&&Array.isArray(r.parts)&&Array.isArray(r.initial_rules)&&
    r.execution_authority==="none"&&r.required_confirmation===null));
MS.edit=index=>{
  if(PL.state.draft){setStatus("请先应用或放弃装备草稿");return;}
  const original=PL.state.plan,caps=MS.caps(),previous=MS.sources(original)[index];
  if(original?.plan_version!==3||!caps?.default_source){setStatus("通用机制需要当前接口及原生版本3方案");return;}
  if(index!==undefined&&!previous)return;
  if(!previous&&MS.sources(original).length>=caps.limits.max_sources){setStatus("通用机制来源已达到接口数量上限");return;}
  const row=UI.clone(previous||caps.default_source),legacyAcceptance=previous?.accepted_fingerprint||null;
  row.accepted_fingerprint=null;if(!previous)row.id=UI.id("mechanism");
  const baseline=PL.currentInputJson(),epoch=PL.state.sourceEpoch,path=RT.state.path,sha=PL.state.fileSha,capsJson=JSON.stringify(PL.state.caps);
  let closed=false,revision=0,request=0,shown=null,profile=null,scopeKey="",catalog=null,page=1,equipmentEvidence={};
  const current=()=>!closed&&PL.state.plan===original&&!PL.state.draft&&PL.currentInputJson()===baseline&&PL.state.sourceEpoch===epoch&&
    RT.state.path===path&&PL.state.fileSha===sha&&JSON.stringify(PL.state.caps)===capsJson;
  const discovery=UI.el("div"),sourceBox=UI.el("div"),form=UI.el("div"),preview=UI.el("div"),errors=UI.el("div");
  const invalidate=()=>{if(closed)return true;closed=true;request++;return true;};
  const dialog=window.AppDialogKernel?.create?.({title:"通用机制知识",
    description:"保存受审知识引用与当前场景，供 Agent 材料化为引擎输入；数据库审核本身不授予执行权。",
    className:"mc-dialog",onRequestClose:invalidate})||null;
  if(!dialog){setStatus("当前还有窗口正在处理，请先完成或关闭后再编辑通用机制知识");return;}
  const close=()=>{invalidate();return dialog.finish?.("cancel")??true;};
  const changed=()=>{revision++;request++;shown=null;row.accepted_fingerprint=null;preview.innerHTML="";errors.innerHTML="";MS.preview(preview,null);};
  const update=(object,key,value)=>{if(JSON.stringify(object[key])===JSON.stringify(value))return;object[key]=value;changed();};
  const scopes=()=>MS.scopes(original,row.actor,equipmentEvidence),scope=()=>scopes().find(s=>s.key===scopeKey);
  const check=()=>{if(current())return true;setStatus("方案、草稿、文件或接口已变化，请重新打开通用机制编辑");return false;};
  const query=async(query,consume)=>{
    if(!check())return;const attempt=++request,version=revision;errors.innerHTML="";let response;
    try{response=await callApi("query_runtime",UI.clone(query));}catch(error){response={code:1,message:String(error)};}
    if(attempt!==request||revision!==version||!current()){if(!closed)setStatus("旧来源响应已忽略，请重新读取");return;}
    if(response.code){errors.append(UI.error(response));return;}
    try{consume(response.data);}catch(error){errors.append(UI.error({message:String(error)}));}
  };
  const readProfile=()=>{
    if(!row.mechanism_id){errors.append(UI.el("p","请先明确选择一项资料。","eq-error"));return;}
    const expected=UI.clone(row),wanted=scope();return query({action:"mechanism_profile",mechanism_id:row.mechanism_id,version_line:row.version_line,
      ...(row.revision===null?{}:{revision:row.revision})},data=>{
      if(!MS.validProfile(data,expected,wanted))throw Error("资料回复与所选来源、修订或资料线不一致，未采用。");
      profile=UI.clone(data);shown=null;sourceBox.innerHTML="";MS.profilePreview(sourceBox,profile);drawDiscovery();drawForm();});
  };
  const readCatalog=nextPage=>{
    const wanted=scope();if(!wanted){errors.append(UI.el("p","请选择已有的数据库身份；本界面不会按角色名称猜测来源。","eq-error"));return;}
    return query({action:"mechanism_catalog",target_type:wanted.target_type,target_id:wanted.target_id,version_line:wanted.version_line,page:nextPage,page_size:20},data=>{
      if(!MS.validCatalog(data,wanted,nextPage))throw Error("目录回复的范围或分页不一致，未采用。");catalog=UI.clone(data);page=nextPage;drawDiscovery();});
  };
  const readEquipment=async()=>{
    if(!check())return;const attempt=++request,version=revision;errors.innerHTML="";let response;
    try{response=await callApi("compose_equipment_plan",UI.clone(original));}catch(error){response={code:1,message:String(error)};}
    if(attempt!==request||revision!==version||!current()){if(!closed)setStatus("旧装备响应已忽略，请重新读取");return;}
    const report=response?.data;
    if(response?.code||!MS.validReport(report,original)||!report.contribution_report?.equipment_sources){
      errors.append(UI.error(response?.code?response:{message:"未返回对应的完整装备资料，未采用套装身份。"}));return;
    }
    equipmentEvidence=UI.clone(report.contribution_report.equipment_sources);drawDiscovery();
  };
  const drawDiscovery=()=>{
    discovery.innerHTML="";const actors=original.manual_template?.characters||[],actorOptions=[{value:"",label:"请选择提供者"},...actors.map(a=>({value:a.id,label:DS.character(a.id,original.manual_template,original)}))];
    if(row.actor&&!actors.some(a=>a.id===row.actor))actorOptions.push({value:row.actor,label:"提供者已移除（可移除此来源）"});
    discovery.append(UI.field("机制提供者",UI.select(actorOptions,row.actor,value=>{update(row,"actor",value);scopeKey="";catalog=null;profile=null;sourceBox.innerHTML="";drawDiscovery();drawForm();})));
    const choices=scopes();if(!scopeKey&&choices.length===1)scopeKey=choices[0].key;
    discovery.append(UI.field("来源数据库身份",UI.select([{value:"",label:"请选择精确身份"},...choices.map((s,i)=>({value:s.key,label:`${s.label} · 身份 ${i+1}`}))],scopeKey,value=>{
      scopeKey=value;changed();catalog=null;profile=null;sourceBox.innerHTML="";drawDiscovery();drawForm();})),UI.button("发现受审机制来源",()=>readCatalog(1)));
    if(Object.values(original.equipment_inputs?.[row.actor]?.artifacts||{}).some(item=>item&&item.state!=="empty"))
      discovery.append(UI.button("读取已装备圣遗物的套装身份",readEquipment));
    if(choices.length)discovery.append(UI.details("角色与装备的精确数据库身份",choices));
    if(!choices.length)discovery.append(UI.el("p","缺少可用的数据库身份。请先绑定角色、装备或真实攻击参数来源；已有引用仍可预览、保存草稿或移除。"));
    if(catalog){discovery.append(UI.el("p",`本页 ${catalog.items.length} 项，共 ${catalog.total} 项；单项也需要明确选择。`));
      discovery.append(UI.field("目录中的来源",UI.select([{value:"",label:"请选择受审资料"},...catalog.items.map((item,i)=>({value:item.id,label:`受审来源 ${(page-1)*20+i+1} · 修订 ${item.revision} · ${MS.status(item.review.status)}`}))],catalog.items.some(i=>i.id===row.mechanism_id)?row.mechanism_id:"",value=>{
        const item=catalog.items.find(i=>i.id===value);if(!item)return;row.mechanism_id=item.id;row.version_line=scope().version_line;row.revision=null;
        row.scenario=UI.clone(caps.default_source.scenario);row.calibration=null;delete row.cast_binding;profile=null;sourceBox.innerHTML="";changed();drawDiscovery();drawForm();return readProfile();})));
      if(page>1)discovery.append(UI.button("上一页来源",()=>readCatalog(page-1)));
      if(page*20<catalog.total)discovery.append(UI.button("下一页来源",()=>readCatalog(page+1)));}
    if(row.mechanism_id){discovery.append(UI.el("p",`当前引用：${row.revision===null?"跟随当前受审修订":"固定修订 "+row.revision} · ${row.version_line==="release"?"正式线":"测试线"}`),
      UI.button("读取当前引用资料",readProfile),UI.details("当前资料标识",{mechanism_id:row.mechanism_id,revision:row.revision,version_line:row.version_line}));
      if(profile?.selection?.revision)discovery.append(UI.field("修订选择",UI.select([{value:"head",label:"跟随当前受审修订（变化须重新材料化）"},{value:String(profile.selection.revision),label:`固定当前资料修订 ${profile.selection.revision}`}],row.revision===null?"head":String(row.revision),value=>{update(row,"revision",value==="head"?null:Number(value));})));}
  };
  const drawForm=()=>{
    form.innerHTML="";const qualification=profile?.compiler_result?.execution_qualification,requirements=qualification?.scenario_requirements||{},values=row.scenario.values;
    const target=original.mechanics_model?.elemental_model?.target,targets=target?[target]:[];
    const options=[{value:"",label:"尚未明确目标"},...targets.map((t,i)=>({value:t.id,label:`目标 ${i+1}`}))];
    if(row.scenario.target_id&&!targets.some(t=>t.id===row.scenario.target_id))options.push({value:row.scenario.target_id,label:"已有目标引用（需核对）"});
    if(qualification?.target_dependency?.mode==="none"){
      form.append(UI.el("p","该来源作用于角色属性，无需敌方目标。"));
      if(row.scenario.target_id!==null)form.append(UI.button("清除不适用的目标",()=>{update(row.scenario,"target_id",null);drawForm();}));
    }else form.append(UI.field("收益目标",UI.select(options,row.scenario.target_id,value=>update(row.scenario,"target_id",value||null))));
    if(qualification?.schema_version===2||row.cast_binding){
      form.append(UI.el("h4","关联成功施放"),UI.el("p","选择已有能力后，系统覆盖本窗口中它的每次实际施放。施放资格独立于伤害命中；能量、冷却与前台条件仍由引擎检查。"));
      const abilities=(original.rotation_model?.abilities||[]).filter(a=>a.character===row.actor),ids=row.cast_binding?.ability_ids||[];
      ids.forEach((id,i)=>{
        const choices=[{value:"",label:"请选择能力"},...abilities.filter(a=>a.id===id||!ids.includes(a.id)).map(a=>({value:a.id,label:RC.abilityName(a)}))];
        if(!abilities.some(a=>a.id===id))choices.push({value:id,label:"能力引用已失效"});
        form.append(UI.field(`施放能力 ${i+1}`,UI.select(choices,id,value=>{if(!value)return;const next=[...ids];next[i]=value;update(row.cast_binding,"ability_ids",next);drawForm();})),
          UI.button(`移除施放能力 ${i+1}`,()=>{const next=ids.filter((_,n)=>n!==i);if(next.length)update(row.cast_binding,"ability_ids",next);else{delete row.cast_binding;changed();}drawForm();}));
      });
      const available=abilities.filter(a=>!ids.includes(a.id));
      if(available.length&&ids.length<64)form.append(UI.field("添加施放能力",UI.select([{value:"",label:"请选择要关联的能力"},...available.map(a=>({value:a.id,label:RC.abilityName(a)}))],"",value=>{
        if(!value)return;if(!row.cast_binding)row.cast_binding={ability_ids:[],selection:null};update(row.cast_binding,"ability_ids",[...ids,value]);drawForm();})));
      if(row.cast_binding){
        form.append(UI.el("p","系统复用已有明确身份。仅在提示缺少或有歧义时补充以下选择；补充项不会覆盖互相矛盾的既有资料。"));
        for(const [key,label] of Object.entries({constellation:"补充命座",talent_level:"补充有效天赋等级",skill_depot_id:"补充技能组标识",skill_id:"补充技能标识"}))
          form.append(UI.field(label,UI.input(row.cast_binding.selection?.[key]??null,value=>{const next={...row.cast_binding.selection};if(value===null)delete next[key];else next[key]=value;update(row.cast_binding,"selection",Object.keys(next).length?next:null);},true)));
      }
    }
    if(qualification?.schema_version===3)form.append(UI.el("p","系统核对本窗口中提供者的所有相关真实命中，复用已接受的单次或周期动作档案。无需逐击填写收益区间；有关命中缺少资料时整组保持待完善。"));
    const properties=caps.scenario_schema?.properties?.values?.properties||{};
    for(const key of new Set([...Object.keys(requirements),...Object.keys(values)])){
      if(!properties[key]){form.append(UI.el("p","来源要求接口未知的场景字段，不能形成可材料化候选，请更新接口。","eq-error"));continue;}
      const expected=properties[key].enum.find(value=>value!==null&&value!=="unknown");
      form.append(UI.field(MS.sceneLabels[key]||"受审场景条件",UI.select([{value:"unknown",label:"尚未明确"},{value:"supported",label:expected===true?"明确成立":MS.valueLabels[expected]||"采用接口支持的声明"}],
        values[key]===expected?"supported":"unknown",value=>update(values,key,value==="supported"?expected:null))));
    }
    if(!profile)form.append(UI.el("p","读取资料后将列出该来源要求的场景；不会自动勾选未知条件。"));
    const evidence=(object,prefix)=>{form.append(UI.field(prefix+"状态",UI.select(["pending","assumed","confirmed"].map(value=>({value,label:MS.status(value)})),object.status,value=>update(object,"status",value))),
      UI.field(prefix+"依据",UI.area(object.source,value=>update(object,"source",value))));};
    evidence(row.scenario,"场景声明");
    const needsCalibration=qualification?.parent?.activation?.offset_policy!=="fixed_zero"&&MS.rules(profile).some(item=>(item.rule||item).trigger?.event==="action_start");
    if(needsCalibration||row.calibration){
      if(!row.calibration)form.append(UI.button("添加生效时间校准",()=>{row.calibration={activation_offset_seconds:null,precision_seconds:null,status:"pending",source:""};changed();drawForm();}));
      else {form.append(UI.field("施放到生效（秒）",UI.input(row.calibration.activation_offset_seconds,value=>update(row.calibration,"activation_offset_seconds",value),true)),
        UI.field("生效记录精度（秒）",UI.input(row.calibration.precision_seconds,value=>update(row.calibration,"precision_seconds",value),true)));evidence(row.calibration,"生效校准");
        form.append(UI.button("清除生效时间校准",()=>{row.calibration=null;changed();drawForm();}));}}
    form.append(UI.el("p","数据库在这里提供知识候选。这里只声明场景与必要时点；改轴、来源或场景变化后须重新预览，并由 Agent 为当前方案材料化。"));
    if(legacyAcceptance)form.append(UI.el("p","文件含旧版接受指纹；它不再代表当前方案执行授权，保存候选时会清除。","eq-error"));
  };
  const candidate=remove=>{const plan=UI.clone(original);plan.mechanism_sources=MS.sources(plan).filter((_,i)=>i!==index);if(!remove){if(previous)plan.mechanism_sources.splice(index,0,UI.clone(row));else plan.mechanism_sources.push(UI.clone(row));}return plan;};
  const run=async mode=>{
    if(!check())return;errors.innerHTML="";const remove=mode==="remove",prior=shown?.revision===revision?shown.record:null;
    if(!remove&&(!row.actor||!row.mechanism_id)){errors.append(UI.el("p","请选择提供者与来源；不可保存没有身份的引用。","eq-error"));return;}
    const proposed=candidate(remove);
    const attempt=++request,version=revision;let response;try{response=await callApi("compose_equipment_plan",UI.clone(proposed));}catch(error){response={code:1,message:String(error)};}
    if(attempt!==request||version!==revision||!current()){if(!closed)setStatus("旧组合预览已忽略，请重新读取");return;}
    const report=response.data,record=MS.record(report,row.id);
    if(response.code||!MS.validReport(report,proposed)){errors.append(UI.error(response.code?response:{message:"未返回完整对应的机制回执，未应用修改"}));return;}
    preview.innerHTML="";if(!remove)MS.preview(preview,record,proposed);shown={revision,record};
    if(mode==="preview")return;
    original.mechanism_sources=UI.clone(proposed.mechanism_sources);close();RC.touch();RC.render();
    setStatus(remove?"通用机制知识引用已移除，请重新计算":"机制知识候选已保存；待 Agent 材料化，当前没有执行授权");
  };
  const actions=UI.el("div");actions.append(UI.button("预览本轴机制",()=>run("preview")),
    UI.button("应用机制引用",()=>run("apply")));
  if(previous)actions.append(UI.button("移除机制引用",()=>run("remove")));actions.append(UI.button("放弃并关闭",close));
  dialog.appBody.append(UI.button("← 返回动作与能量",close),
    UI.el("p","这里保存数据库知识引用和本轴场景，不会直接生成可执行规则。Agent 材料化功能尚未接通。"),discovery,sourceBox,form,preview,errors,actions);
  drawDiscovery();drawForm();MS.preview(preview,null);dialog.focusInitial?.();
};
MS.render=box=>{
  if(PL.state.plan?.plan_version!==3||!MS.caps())return;const card=UI.el("section",null,"eq-card");card.append(UI.el("h4","通用机制知识"),UI.el("p","保存受审知识引用与当前场景，供 Agent 后续材料化；数据库审核本身不授权执行。"));
  if(MS.sources(PL.state.plan).length<MS.caps().limits.max_sources)card.append(UI.button("添加通用机制来源",()=>MS.edit()));
  MS.sources(PL.state.plan).forEach((row,index)=>card.append(MS.sourceRow(UI.el("p",`${DS.character(row.actor,PL.state.plan.manual_template,PL.state.plan)} · 知识候选，待 Agent 材料化${row.accepted_fingerprint?"（旧接受字段无当前执行权）":""}`),row,index)));box.append(card);
};
