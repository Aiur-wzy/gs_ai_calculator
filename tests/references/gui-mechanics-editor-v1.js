"use strict";
// Forms edit declared inputs only. Python owns execution, validation and all derived values.
window.MC={caps:null,shown:false};
MC.words={rules:"机制规则",hit_events:"独立命中关联",hit_patterns:"连续命中模式",condition_events:"条件变化",initial_state:"初始机制状态",
  resource_ownership:"资源交付归属",assumptions:"机制假设",id:"标识",name_zh:"中文名称",owner:"提供角色",source_id:"来源分项标识",
  trigger:"触发条件",event:"事件类型",phase:"执行相位",action_ids:"动作标识筛选",event_ids:"事件标识筛选",characters:"角色筛选",
  action_type_ids:"动作类型筛选",damage_type_ids:"伤害类型筛选",elements:"元素筛选",condition:"条件标识",beneficiaries:"受益对象",
  mode:"方式",scope:"范围",timing:"计时",delay:"延迟秒数",duration:"持续秒数",cooldown:"内部冷却秒数",cooldown_anchor:"冷却起点",
  eligibility:"受益者资格",exclude_owner:"排除提供者",beneficiary_selection:"本次受益者选择",selection_t:"选人时点（秒）",
  base_candidates:"基础候选角色",recipients:"实际接收角色",excluded:"被排除角色",excluded_owner:"提供者被排除",not_in_whitelist:"不在资格名单内",
  no_eligible_beneficiary:"无人符合受益资格，本次未激活","beneficiary-eligibility-v1":"受益者资格第一版",
  stacking:"层数规则",max:"上限",add:"新增量",policy:"叠层方式",effects:"收益项目",resources:"资源产出",status:"证据状态",source:"来源、版本及依据",
  quota:"次数池",capacity:"次数上限",refresh:"刷新方式",consume:"消费条件",benefit_type_id:"收益类型",value:"数值或属性比例",
  domain:"采样域",filter:"收益命中筛选",kind:"类别",subject:"取值对象",stat:"来源属性",factor:"比例系数",min:"下限",sample:"取值时点",event_id:"采样事件标识",
  amounts:"各角色直接回复量",count:"数量",particle_type:"粒子类型",element:"元素",travel_time:"抵达间隔秒数",source_part:"资源来源分项",
  hit_id:"命中标识",action_id:"所属动作标识",order:"同刻顺序",report:"计入伤害报告",self_snapshot_event:"自身快照事件标识",
  prototype_hit_id:"命中原型标识",offsets:"相对动作开始的命中秒数",orders:"逐击同刻顺序",t:"发生秒数",repeat:"重复规则",cycles:"轮次（从零开始）",
  rule_id:"机制规则标识",cooldown_remaining:"初始剩余冷却秒数",instances:"初始活跃实例",remaining:"各层剩余秒数",replaces_event_ids:"取代的手工资源事件标识",
  action_start:"动作开始",action_end:"动作结束",switch:"切人",hit:"伤害命中",pre:"事件前",post:"事件后",self:"自身",team:"全队",
  frontline:"当前前台",next_switch:"实际接棒角色",locked:"锁定受益角色",current_frontline:"跟随当前前台",success:"成功触发时",attempt:"尝试触发时",
  independent:"每层独立到期",add_and_refresh:"增加层数并共同刷新",replace:"替换现有实例",keep:"保留现有状态",extend:"延长持续时间",reset:"重置",per_beneficiary:"每个受益角色独立",
  recipient:"受益角色",provider:"提供者",enemy:"敌方",current:"本次求值时",energy_direct:"直接恢复能量",particles:"元素微粒／晶球",
  particle:"微粒",orb:"晶球",clear:"无色",confirmed:"已核实",assumed:"情景假设",pending:"待核实",each_cycle:"每轮重复",once:"只执行一次",
  atk:"攻击力",hp:"生命值",def:"防御力",em:"元素精通",base_atk:"基础攻击力",base_hp:"基础生命值",base_def:"基础防御力",energy_recharge:"元素充能效率",
  pyro:"火",hydro:"水",electro:"雷",cryo:"冰",anemo:"风",geo:"岩",dendro:"草",physical:"物理",
  activation:"机制激活",expiry:"效果到期",trigger_rejected:"触发未生效",effect_rejected:"收益未生效",effect_applied:"收益生效",
  internal_cooldown:"内部冷却尚未结束",no_quota:"次数不足",match_excluded:"微调排除",quota_not_consumed:"本次未消费额度",
  quota_exhausted:"次数已用尽",unknown_condition:"条件尚未核实",manual_effect_applied:"区间收益生效",action_end_superseded:"动作结束时刻已由取消动作替换",
  not_active:"机制当前未激活",recipient_mismatch:"不属于受益角色",not_frontline:"当前不在前台",
  no_consumption_receipt:"本次未取得次数额度（次数不足或消费条件未满足）",agent_excluded:"本轴微调明确排除",filter_mismatch:"不满足收益筛选",
  passed:"通过",failed:"失败",invalid:"输入无效",partial:"部分支持",not_verified:"后续未验证",unproven:"尚未证明稳定"};
Object.assign(MC.words,{elemental_model:"附着与元素底",application_events:"无伤害附着事件",application:"附着声明",
  profiles:"附着档案",profile_id:"附着档案标识",target:"目标",target_id:"目标标识",kind:"种类",ordinary:"普通目标",
  knowledge:"初态可信度",known:"已知",unknown:"未知",initial_aura:"初始元素底",remaining_gu:"剩余元素量（GU）",
  decay_gu_per_second:"每秒衰减元素量",ruleset:"规则版本",schema_version:"模型版本",landed:"实际命中或接触",
  apply:"施加元素",none:"无附着",gauge_units:"施加元素量（GU）",icd:"附着冷却",group:"旧版 ICD 组（兼容字段）",
  standard:"标准附着冷却",element:"元素",elemental_reaction:"实际元素反应",reaction_types:"反应种类",
  vaporize:"蒸发",melt:"融化",elemental_application:"附着判定",no_icd:"不受附着冷却限制",first_hit:"该计数器首次尝试允许附着",
  icd_timer_reset:"附着计时重置",icd_hit_sequence:"命中序列允许附着",icd_blocked:"附着冷却阻止",missed:"未命中",
  no_application:"本次不附着",aura_applied:"建立元素底",aura_refreshed:"刷新元素底","gauge-basic-v1":"普通增幅反应规则第1版"});
Object.assign(MC.words,{sequence:"有限附着序列",reset_seconds:"附着重置秒数",elemental_sources:"数据库附着规则来源",
  attenuation_group:"附着规则组",shared_group:"附着计数标签（ICD Tag）",ready:"资料就绪"});
Object.assign(MC.words,{hit_parameter_sources:"数据库技能倍率来源",single_stat_damage_multiplier:"单属性直接伤害倍率",
  declared_not_verified:"用途由方案声明，引擎尚未验证游戏语义"});
Object.assign(MC.words,{"gauge-swirl-v2":"普通单目标扩散规则第2版",swirl:"扩散",swirl_damage:"普通扩散伤害规则",
  reaction_damage:"扩散派生伤害",reaction_damage_attempt:"扩散伤害冷却判定",reaction_damage_sources:"数据库扩散伤害来源",
  reaction_elements:"被扩散元素筛选",owner_presence:"提供者在场条件",any:"不限制前后台"});
Object.assign(MC.words,{"gauge-swirl-dynamic-v3":"普通冰水火扩散与动态精通规则第3版",
  provider_stat_sample:"反应产生时的提供者属性",base_value:"基础输入值",dependencies:"属性贡献来源",reaction_created:"反应产生时"});
Object.assign(MC.words,{shared_pools:"共享资源池",pools:"资源池",operations:"资源操作",consumers:"资源消费者",
  pool_id:"资源池标识",consumer_id:"消费者标识",resource_gate:"资源消费门槛",eligibility_rule_id:"收益资格规则",
  per_character:"各角色独立",initial:"初始余额",operation:"操作",set:"设置余额",grant:"增加资源",targets:"目标对象",
  unit_scale:"每单位原子数",spend:"扣除资源",spend_policy:"扣量策略",all_available:"全部可用资源",all_or_nothing:"足额才扣除",up_to_available:"最多扣尽余额",
  all_pool_keys:"池内全部对象",actor_relation:"命中者关系",party_member:"队伍成员",other_frontline:"其他前台角色",
  damage_system:"伤害计算类别",classic:"普通伤害公式",amount:"资源数量",seconds:"秒数",anchor:"冷却起算条件"});
Object.assign(MC.words,{fixed_effect_sources:"固定目标条件来源",fixed_effect_source:"固定目标条件来源",fixed_source_id:"固定来源标识",
  availability:"来源可用性",fixed:"全程固定",damage_scope:"伤害适用范围",classic_hits_only:"仅本人经典命中",
  target_state_profile:"目标状态解释档案","freeze-reference-target-v1":"普通冻结参考状态第1版",
  target_predicate:"命中前目标条件",target_aura_in:"目标具有指定元素底",target_status:"目标具有指定状态",
  children:"子条件",status_id:"目标状态",element_freeze:"实际冻结状态",frozen:"冻结元素底",
  target_condition:"目标条件收益",condition_true:"自动条件成立",condition_false:"自动条件不成立",
  unknown_target_state:"目标状态未知或不在支持范围",agent_included:"本轴微调明确计入",damage_scope_mismatch:"不属于经典命中适用范围"});
MC.label=v=>MC.words[v]||(EF.registry().benefit_types||[]).find(x=>x.id===v)?.label
  ||RC.caps?.action_types?.find(x=>x.id===v)?.name_zh||(EF.registry().damage_types||[]).find(x=>x.id===v)?.label||v;
Object.assign(MC.words,{state_runtime:"生命周期与定时",lifecycles:"生命周期",timers:"定时器",lifecycle_id:"所属生命周期",
  start:"启动条件",end_trigger:"提前结束条件",start_order:"启动顺序",establishment_order:"建立顺序",end_order:"结束顺序",
  establishment_delay_us:"建立延迟（微秒）",duration_us:"持续时间（微秒）",first_offset_us:"首次偏移（微秒）",interval_us:"间隔（微秒）",
  restart:"重新启动方式",pending_cancel:"建立前取消方式",never_after_success:"成功后不取消",source_action_cancel_before_establishment:"源动作在建立前取消则结束",
  hooks:"阶段指令",create:"创建时",established:"建立时",end:"结束时",max_ticks:"最多触发次数",end_tick:"到期末跳",
  exclude:"不含到期末跳",before_expiry:"先末跳后到期",terminal_order:"末跳事务顺序",payload:"执行指令",dispatch_rule:"激活收益",resource_operation:"资源操作",ref_id:"目标标识"});
Object.assign(MC.words,{start_lifecycle:"开始领域",end_lifecycle:"结束领域",resource_threshold:"资源阈值触发",binding_id:"触发绑定标识",
  lifecycle_start:"开始领域",lifecycle_established:"建立领域",lifecycle_closing:"领域结束处理中",lifecycle_end:"领域已结束",timer_tick:"定时触发",dispatch:"派发定时指令"});
MC.model=()=>PL.state.plan?.mechanics_model;
MC.default=s=>{
  if(s.const!==undefined)return s.const;
  if(s.oneOf)return MC.default(s.oneOf[0]);
  if(s.enum)return s.enum[0];
  const type=Array.isArray(s.type)?s.type.find(t=>t!=="null"):s.type;
  if(type==="object"){const value=Object.fromEntries((s.required||[]).map(k=>[k,MC.default(s.properties[k])]));
    if(s.properties?.availability?.const==="fixed")value.status="pending";return value;}
  if(type==="array")return [];
  if(type==="boolean")return false;
  if(type==="null")return null;
  if(type==="number"||type==="integer")return s.minimum??0;
  return "";
};
MC.branch=(s,v)=>s.oneOf?.findIndex(x=>x.type==="object"?v!==null&&typeof v==="object"&&
  Object.entries(x.properties||{}).every(([key,child])=>(child.const===undefined||v[key]===child.const)&&(!child.enum||v[key]===undefined||child.enum.includes(v[key]))):
  x.type==="null"?v===null:typeof v===x.type||x.type==="integer"&&Number.isInteger(v))??-1;
MC.form=(box,s,value,set,label,redraw)=>{
  if(s.oneOf){const i=Math.max(0,MC.branch(s,value));
    box.append(UI.field(`${label} · 表达方式`,UI.select(s.oneOf.map((branch,j)=>({value:String(j),label:branch["x-label"]||
      (branch.properties?.kind?.const?MC.label(branch.properties.kind.const):branch.properties?.kind?.enum?.includes("all")?"组合条件":j===0?"固定数值":"属性比例")})),String(i),v=>{set(MC.default(s.oneOf[Number(v)]));redraw();})));
    MC.form(box,s.oneOf[i],value,set,label,redraw);return;}
  const type=Array.isArray(s.type)?s.type.find(t=>t!=="null"):s.type;
  if(Array.isArray(s.type)&&s.type.includes("null")){
    box.append(UI.check(`${label} · ${s["x-null-label"]||"未知"}`,value===null,yes=>{set(yes?null:MC.default({...s,type}));redraw();}));if(value===null)return;}
  if(type==="null"){box.append(UI.el("p",`${label}：${s["x-null-label"]||"空值"}`));return;}
  if(s.const!==undefined){box.append(UI.el("p",`${label}：${s["x-const-label"]||MC.label(s.const)}`));return;}
  if(s.enum){box.append(UI.field(label,UI.select(s.enum.map(v=>({value:String(v),label:s["x-enum-labels"]?.[v]||(s.enum.includes("all")&&s.enum.includes("any")?v==="all"?"全部子条件成立":"至少一项子条件成立":MC.label(v))})),String(value),v=>set(s.enum.find(x=>String(x)===v)))));return;}
  if(type==="array"){
    const group=UI.el("fieldset",null,"eq-part");group.append(UI.el("legend",label));box.append(group);
    (value||[]).forEach((v,i)=>{const row=UI.el("section",null,"eq-part");group.append(row);
      MC.form(row,s.items,v,n=>{value[i]=n;},`${label} ${i+1}`,redraw);
      row.append(UI.button("移除此项",()=>{value.splice(i,1);redraw();}));});
    group.append(UI.button(`添加${label}`,()=>{value.push(MC.default(s.items));redraw();}));return;}
  if(type==="object"){
    const group=UI.el("fieldset",null,"eq-part");group.append(UI.el("legend",label));box.append(group);
    if(s.properties){Object.entries(s.properties).forEach(([key,child])=>{
      const required=(s.required||[]).includes(key),shown=Object.hasOwn(value,key),name=child["x-label"]||MC.label(key);
      if(!required)group.append(UI.check(`设置${name}`,shown,yes=>{if(yes)value[key]=MC.default(child);else delete value[key];redraw();}));
      if(required||shown){
        if(key==="owner")group.append(UI.field(name,UI.select(DS.characters(RT.tpl(),PL.state.plan),value[key],v=>{value[key]=v;})));
        else MC.form(group,child,value[key],v=>{value[key]=v;},name,redraw);
      }
    });}else{
      Object.entries(value).forEach(([k,v])=>{const row=UI.el("div");group.append(row);
        MC.form(row,s.additionalProperties,v,n=>{value[k]=n;},DS.character(k,RT.tpl(),PL.state.plan),redraw);
        row.append(UI.button("移除角色项",()=>{delete value[k];redraw();}));});
      group.append(UI.field("添加角色项",UI.select([{value:"",label:"选择角色"},...DS.characters(RT.tpl(),PL.state.plan)],"",v=>{if(v&&!Object.hasOwn(value,v)){value[v]=MC.default(s.additionalProperties);redraw();}})));
    }return;}
  if(type==="boolean"){box.append(UI.check(label,!!value,set));return;}
  box.append(UI.field(label,(["source","assumptions"].some(k=>label.startsWith(MC.label(k))))?UI.area(value,set):UI.input(value,set,type==="number"||type==="integer")));
};
MC.touch=()=>{RC.invalidate();RT.syncRevision();PL.renderStatus();MC.render();RT.renderTimeline?.();};
Object.assign(MC.words,{attribute_model:"属性账与取样基底",contract:"契约版本",initial_basis:"初始基底声明",
  nonextra:"非额外精通",base:"基础量",panel_total:"明确全部属于普通精通",explicit:"明确数值",included_components:"初始面板已含来源",
  effect_classifications:"动态属性来源分类",component:"来源组件",nonextra_inclusion:"是否计入非额外精通",included:"计入",excluded:"不计入",
  effect_ref:"收益引用",manual_buff:"手工区间增益",part_id:"来源部件",source_fingerprint:"来源指纹",basis:"取值基底",total:"总量"});
MC.adoptAttributes=()=>{
  if(PL.state.draft||!MC.model()?.attribute_model){setStatus("请先应用装备草稿，并填写属性账声明");return;}
  const original=PL.state.plan,baseline=PL.currentInputJson(),epoch=PL.state.sourceEpoch,path=RT.state.path,sha=PL.state.fileSha;
  const errors=UI.el("div");let note="",closed=false,busy=false;
  const invalidate=()=>{if(closed)return;closed=true;};
  const dialog=window.AppDialogKernel?.create?.({title:"核对属性来源",
    description:"先核对初始面板、基底和每项分类。软件仅对齐实际来源身份；未知分类仍保持未知，已含属性不重复加算。",
    onRequestClose:()=>{invalidate();return true;}})||null;
  if(!dialog){setStatus("当前还有窗口正在处理，请先完成或关闭后再核对属性来源");return;}
  const close=()=>{invalidate();dialog.finish("attribute-review-close");};
  const current=()=>!closed&&PL.state.plan===original&&PL.currentInputJson()===baseline&&!PL.state.draft&&PL.state.sourceEpoch===epoch&&RT.state.path===path&&PL.state.fileSha===sha;
  const apply=UI.button("核对完毕，采用当前声明",async()=>{
    if(busy)return;if(!current()){errors.append(UI.el("p","方案已变化，请重新打开核对。","eq-error"));return;}
    if(!note.trim()){errors.append(UI.el("p","请填写基底与分类的核对依据。","eq-error"));return;}
    const submittedNote=note;busy=true;apply.disabled=true;dialog.setBusy(true);let response;
    try{response=await callApi("prepare_attribute_review",UI.clone(original),submittedNote.trim());}catch(error){response={code:1,message:String(error)};}
    busy=false;if(!current())return;dialog.setBusy(false);apply.disabled=false;
    if(submittedNote!==note){errors.append(UI.el("p","核对依据已修改，请再次确认。","eq-error"));return;}
    if(response.code||!response.data?.plan||response.data.attribute_source_adoption?.contract!=="attribute-source-adoption-v1"){
      errors.append(UI.error(response.code?response:{code:1,message:"未获得可核对的属性来源回执"}));return;}
    const saved=PL.state.savedJson;close();PL.replace(response.data.plan,path,sha);PL.state.savedJson=saved;
    PL.state.report=response.data;PL.renderStatus();MC.render();
    setStatus(response.data.state==="ready"?"属性声明已采用为未保存候选，请重新计算":"属性声明已采用；其他资料仍待补全，请查看缺项后重新计算");
  });
  dialog.appBody.append(UI.details("本次待核对的声明",MC.model().attribute_model),UI.field("核对依据",UI.area(note,v=>{note=v;})),errors);
  dialog.appFooter.append(UI.button("← 返回机制列表",close),apply);dialog.appFooter.hidden=false;
};
MC.edit=key=>{
  if(PL.state.draft){setStatus("请先应用或放弃装备草稿");return;}
  const baseline=PL.currentInputJson(),model=UI.clone(MC.model()),form=UI.el("div"),errors=UI.el("div");
  let closed=false,busy=false,request=0;
  const invalidate=()=>{if(closed)return;closed=true;request++;};
  const dialog=window.AppDialogKernel?.create?.({title:`编辑${MC.label(key)}`,className:"mc-dialog",
    onRequestClose:()=>{invalidate();return true;}})||null;
  if(!dialog){setStatus("当前还有窗口正在处理，请先完成或关闭后再编辑机制");return;}
  const draw=()=>{form.innerHTML="";MC.form(form,MC.caps.schema.properties[key],model[key]??MC.default(MC.caps.schema.properties[key]),v=>{model[key]=v;},MC.label(key),draw);};
  model[key]??=MC.default(MC.caps.schema.properties[key]);draw();
  const close=()=>{invalidate();dialog.finish("mechanics-edit-close");},apply=UI.button("应用修改",async()=>{
    if(busy)return;if(closed||baseline!==PL.currentInputJson()||PL.state.draft){setStatus("方案已变化，请重新打开编辑");return;}
    const token=++request;busy=true;dialog.setBusy(true);apply.disabled=true;
    const r=await callApi("validate_mechanics_model",model);
    if(token!==request||closed)return;
    busy=false;dialog.setBusy(false);apply.disabled=false;
    if(baseline!==PL.currentInputJson()||PL.state.draft){setStatus("校验期间方案已变化，请重新打开");return;}
    if(r.code||r.data.status==="invalid"){errors.innerHTML="";errors.append(r.code?UI.error(r):UI.details("待修正的字段",r.data.issues));return;}
    PL.state.plan.mechanics_model=model;close();MC.touch();
  });
  dialog.appBody.append(form,errors);dialog.appFooter.append(UI.button("← 返回机制列表",close),apply,UI.button("放弃并关闭",close));dialog.appFooter.hidden=false;
};
MC.enable=async()=>{
  if(!await RC.ensurePlan())return;
  const baseline=PL.currentInputJson(),r=await callApi("enable_mechanics",UI.clone(PL.state.plan));
  if(baseline!==PL.currentInputJson()||PL.state.draft){setStatus("方案已变化，请重试");return;}
  if(r.code){UI.error(r);return;}
  const saved=PL.state.savedJson;PL.replace(r.data.plan||r.data,RT.state.path,PL.state.fileSha);PL.state.savedJson=saved;
  MC.touch();setStatus("已启用机制方案，请明确动作、规则与初始状态，再预览和计算");
};
MC.newRule=()=>{
  if(PL.state.draft){setStatus("请先应用或放弃装备草稿");return;}
  const model=MC.model(),owner=RT.tpl()?.characters[0]?.id;if(!owner)return;
  const bid=UI.id("binding"),pid=UI.id("part"),rid=UI.id("rule"),rule=MC.default(MC.caps.schema.properties.rules.items);
  Object.assign(rule,{id:rid,name_zh:"待编辑机制",owner,source_id:`${bid}:${pid}`,status:"pending",source:"待核实机制依据"});
  Object.assign(rule.timing,{duration:10,cooldown:0});Object.assign(rule.stacking,{max:1,add:1});
  model.rules.push(rule);model.initial_state.rules.push({rule_id:rid,cooldown_remaining:0,instances:[]});
  PL.state.plan.effect_bindings.push({binding_id:bid,provider_character_id:owner,owner:{kind:"manual",note:"本方案明确声明的机制"},
    source_refs:[{kind:"manual",note:"待核实机制依据"}],source_fingerprint:null,
    coverage:{state:"unreviewed",reviewed_by:null,source_fingerprint:null,note:null},
    parts:[{part_id:pid,label:"待编辑机制来源",state:"pending",rule:{kind:"mechanics",rule_id:rid},delivery:{kind:"mechanics"},
      conditions:[],dependencies:{mode:"source_only",context_fingerprint:null,note:"以机制模型声明为准"},reason:null}]});
  MC.touch();MC.edit("rules");
};
MC.elementalCompilations=report=>report?.elemental_compilations||report?.plan?.resolution?.elemental_compilations||[];
MC.elementalCompilation=(report,id)=>MC.elementalCompilations(report).find(r=>r.profile_id===id);
MC.renderElementalIssues=(box,issues,title)=>{
  if(!issues?.length)return;box.append(UI.el("p",`${title}（${issues.length} 项）`,"eq-error"));
  issues.slice(0,8).forEach(issue=>box.append(UI.el("p",issue.message||"仍有未解释的规则参数，请查看完整定位。")));
  box.append(UI.details("查看完整问题定位",issues));
};
MC.renderElementalCompilation=(box,record)=>{
  if(!record){box.append(UI.el("p","尚无当前草稿的数据库来源预览。"));return;}
  const p=record.projection,s=record.source,mode=window.AM?.mode?window.AM.mode(record):record.application_mode||"sequence";
  box.append(UI.el("p",record.assembly_issues?.length?"规则组合后仍有冲突，尚不能用于计算。":mode==="sequence"?(record.execution?.status==="ready"?"规则参数处于已支持范围；确认来源不代表整份方案已经可计算。":"当前规则参数待核实，不能作为已支持的附着规则。"):
    mode==="no_icd"?"已声明每次命中均附着（无 ICD）；不使用计数标签或规则组。":mode==="no_application"?"已声明该攻击组件不施加元素；这与资料缺失不同。":"附着模式待核实，确定性计算已阻止。"),
    UI.el("p",`元素附着模式：${window.AM?.modeText?window.AM.modeText(mode):mode}${record.deprecated_application_mode?"；旧 unknown 已规范为 pending":""}。`));
  const canonicalRule=record.rule_group?.group||record.rule_group||record.attenuation_group;
  if(typeof canonicalRule==="string")box.append(UI.el("p",`规则条目：${canonicalRule}；${record.version_line==="beta"?"测试资料线":"发布资料线"}`));
  const identity=record.application_identity;
  if(identity)box.append(UI.el("p",`附着计数标签（ICD Tag）：${identity.tag||record.counter_tag||record.shared_group||(identity.status==="not_applicable"?"不适用":"待核实")}；身份依据：${identity.status==="database_confirmed"?"数据库确认":identity.status==="declared"?"方案有证据声明":identity.status==="not_applicable"?"此模式不适用":"待核实"}。`));
  if(identity?.source)box.append(UI.el("p",`计数标签来源：${window.AM?.sourceText?window.AM.sourceText(identity.source):typeof identity.source==="string"?identity.source:"查看完整回执"}`));
  if(record.rule_group)box.append(UI.el("p",`附着规则组：${record.rule_group.group||record.attenuation_group||(record.rule_group.status==="not_applicable"?"不适用":"待核实")}；状态：${record.rule_group.status==="database_confirmed"?"数据库确认":record.rule_group.status==="not_applicable"?"此模式不适用":"待核实"}。规则组只提供重置时间和有限序列。`));
  if(p){box.append(UI.el("p",`附着重置：${p.reset_cycle_seconds_rounded??"待核实"} 秒；原始值 ${p.reset_cycle_seconds_raw??"缺失"} 秒`),
    UI.el("p",`有限附着序列：${Array.isArray(p.durability_sequence)?p.durability_sequence.join("、"):"尚不能解释"}（1 允许，0 阻止；耗尽后等待计时重置）`));}
  if(s)box.append(UI.el("p",`来源：${s.source_path}${s.json_pointer||""}`));
  if(record.issue)box.append(UI.el("p",record.issue.message||"数据库来源不可用","eq-error"));
  MC.renderElementalIssues(box,record.assembly_issues,"规则组合待修正");
  MC.renderElementalIssues(box,record.execution?.issues,"来源参数待核实");
  if(window.AM?.renderVerificationTask){const tasks=record.verification_tasks||[record.verification_task].filter(Boolean);tasks.forEach(task=>AM.renderVerificationTask(box,task,identity?.next_action));}
  box.append(UI.details("查看来源、快照与参数回执",record));
};
MC.renderElementalVerificationTasks=(box,report)=>{
  const rows=[...(report?.elemental_counter_verification_tasks||report?.plan?.resolution?.elemental_counter_verification_tasks||[])];
  for(const record of MC.elementalCompilations(report))rows.push(...(record.verification_tasks||[record.verification_task].filter(Boolean)));
  if(!Array.isArray(rows)||!rows.length||!window.AM?.renderVerificationTask)return;
  const tasks=[],seen=new Set();
  for(const task of rows){const key=JSON.stringify(task);if(!seen.has(key)){seen.add(key);tasks.push(task);}}
  const card=UI.el("section",null,"eq-card");
  card.append(UI.el("h4",`元素附着身份与共享计数核实（${tasks.length}）`),
    UI.el("p","这些工作单来自当前方案的真实组合检查。必须按角色、技能和具体命中分量核实；不能用普攻/战技/爆发类别、同角色其他技能或同名规则类推。","eq-error"));
  tasks.forEach(task=>AM.renderVerificationTask(card,task));box.append(card);
};
MC.editElementalSource=index=>{
  if(PL.state.draft){setStatus("请先应用或放弃装备草稿");return;}
  const original=PL.state.plan,profiles=original?.mechanics_model?.elemental_model?.profiles||[];
  if(!original||original.plan_version!==3||!profiles.some(p=>["apply","unknown"].includes(p.application))){setStatus("请先在附着与元素底中建立附着档案");return;}
  const bindings=original.elemental_sources||[],otherIds=new Set(bindings.filter((_,i)=>i!==index).map(s=>s.profile_id));
  const available=profiles.filter(p=>["apply","unknown"].includes(p.application)&&!otherIds.has(p.id));
  if(index===undefined&&!available.length){setStatus("现有附着档案均已绑定数据库来源，请编辑已有绑定");return;}
  const row=index===undefined?{profile_id:available[0].id,version_line:"release",application_mode:"pending",source:"",expected_snapshot_id:null,expected_node_sha256:null}:UI.clone(bindings[index]);
  row.application_mode=window.AM?.mode?window.AM.mode(row):(row.application_mode==="unknown"?"pending":row.application_mode||"sequence");
  let rememberedRule=row.rule_group||row.attenuation_group||"",rememberedTag=row.counter_tag||row.shared_group||"";
  const verification=UI.clone(row.verification||{locator:"",version:"",reviewed_at:"",component_scope:""});let verificationStatus=row.verification?.status==="confirmed"?"confirmed":"pending";
  delete row.attenuation_group;delete row.shared_group;delete row.verification;
  if(row.application_mode==="sequence"){row.rule_group=rememberedRule;row.counter_tag=rememberedTag;}else{delete row.rule_group;delete row.counter_tag;}
  const baseline=PL.currentInputJson(),epoch=PL.state.sourceEpoch,path=RT.state.path,sha=PL.state.fileSha;
  const form=UI.el("div"),previewBox=UI.el("div"),errors=UI.el("div"),actions=UI.el("div",null,"eq-actions");
  let revision=0,request=0,busy=false,closed=false,preview=null;
  const invalidate=()=>{if(closed)return;closed=true;request++;};
  const dialog=window.AppDialogKernel?.create?.({title:"数据库附着规则来源",className:"mc-dialog",
    description:"数据库规则组提供计时和有限附着序列。计数标签决定哪些攻击共享计数；缺少可靠标签时必须逐攻击段查证。",
    onRequestClose:()=>{invalidate();return true;}})||null;
  if(!dialog){setStatus("当前还有窗口正在处理，请先完成或关闭后再编辑附着规则来源");return;}
  const current=()=>!closed&&PL.state.plan===original&&PL.currentInputJson()===baseline&&!PL.state.draft
    &&PL.state.sourceEpoch===epoch&&RT.state.path===path&&PL.state.fileSha===sha;
  const valid=()=>{if(current())return true;setStatus("方案或数据库状态已变化，请重新打开来源编辑");return false;};
  const close=()=>{invalidate();dialog.finish("elemental-source-close");};
  const confirmed=()=>row.application_mode==="sequence"&&!!row.expected_snapshot_id&&!!row.expected_node_sha256;
  const same=(record,receipt)=>!!record?.required_confirmation&&record.required_confirmation.expected_snapshot_id===receipt.expected_snapshot_id
    &&record.required_confirmation.expected_node_sha256===receipt.expected_node_sha256;
  const candidate=remove=>{const plan=UI.clone(original),rows=plan.elemental_sources||[];
    if(remove)rows.splice(index,1);else if(index===undefined)rows.push(UI.clone(row));else rows[index]=UI.clone(row);
    plan.elemental_sources=rows;
    if(!remove){const profile=plan.mechanics_model.elemental_model.profiles.find(p=>p.id===row.profile_id);
      if(profile&&row.application_mode==="no_application"){profile.application="unknown";delete profile.gauge_units;delete profile.icd;}
      else if(profile?.application==="apply")profile.icd={kind:"unknown"};}
    return plan;};
  const refresh=()=>{dialog.setBusy(busy);previewBox.innerHTML="";errors.innerHTML="";
    previewBox.append(UI.el("p",busy?"正在核对当前声明…":row.application_mode==="sequence"?(confirmed()?"草稿已记录来源确认；应用时会再次检查当前数据库。":"尚未确认数据库来源；可以先应用为待核实草稿。"):
      row.application_mode==="pending"?"附着模式仍待核实；应用后会阻止确定性计算。":"当前模式由逐攻击组件证据声明，不需要附着规则数据库指纹。"));
    if(preview?.revision===revision){MC.renderElementalCompilation(previewBox,preview.record);
      MC.renderElementalIssues(previewBox,preview.issues,"方案仍需完善");}
    else previewBox.append(UI.el("p",row.application_mode==="sequence"?"请读取当前数据库；不会沿用此前装备预览中的确认信息。":"请预览当前附着模式声明；不会沿用旧计算结果。"));
    actions.innerHTML="";
    const add=(label,handler,disabled=false)=>{const b=UI.button(label,handler);b.disabled=busy||disabled;actions.append(b);};
    add(row.application_mode==="sequence"?"读取并预览当前数据库规则":"预览当前附着模式",()=>run("preview"));
    if(row.application_mode==="sequence")add("确认所示来源用于此档案",()=>run("confirm"),!preview?.record?.required_confirmation||preview.revision!==revision||preview.record.execution?.status!=="ready");
    add("应用来源绑定",()=>run("apply"));
    if(index!==undefined)add("移除此来源绑定",()=>run("remove"));
    actions.append(UI.button("放弃并关闭",close));
  };
  const update=(key,value)=>{if(row[key]===value)return;row[key]=value;row.expected_snapshot_id=null;row.expected_node_sha256=null;
    revision++;request++;busy=false;preview=null;refresh();};
  const updateMode=value=>{if(row.application_mode===value)return;row.application_mode=value;if(value==="sequence"){
      row.rule_group=rememberedRule;row.counter_tag=rememberedTag;}else{delete row.rule_group;delete row.counter_tag;}
    row.expected_snapshot_id=null;row.expected_node_sha256=null;revision++;request++;busy=false;preview=null;drawForm();refresh();};
  const run=async mode=>{
    if(busy||!valid())return;
    const selectedProfile=profiles.find(p=>p.id===row.profile_id);
    if(!row.source.trim()){errors.append(UI.el("p","请填写这一具体攻击组件的附着模式依据。","eq-error"));return;}
    if(["sequence","no_icd"].includes(row.application_mode)&&selectedProfile?.application!=="apply"){
      errors.append(UI.el("p","有限序列或无 ICD 需要已有明确元素与原始 GU 的 apply 档案。","eq-error"));return;}
    if(row.application_mode==="sequence"&&(!(row.rule_group||"").trim()||!(row.counter_tag||"").trim())){
      errors.append(UI.el("p","有限序列模式必须填写附着规则组与附着计数标签（ICD Tag）。","eq-error"));return;}
    delete row.verification;const needsEvidence=row.application_mode!=="pending",complete=["locator","version","reviewed_at","component_scope"].every(key=>(verification[key]||"").trim());
    if(needsEvidence&&verificationStatus!=="confirmed"){
      errors.append(UI.el("p","有限序列、无 ICD 或无附着结论都需要逐攻击段结构化证据；尚未核实时请选择待核实模式。","eq-error"));return;}
    if(needsEvidence&&!complete){errors.append(UI.el("p","请填写证据类型/定位、适用版本或commit、复核日期与具体攻击组件范围。","eq-error"));return;}
    if(needsEvidence)row.verification={status:"confirmed",...UI.clone(verification),confirmed_fields:row.application_mode==="sequence"?["application_mode","counter_tag","shared_scope"]:["application_mode","shared_scope"]};
    const removal=mode==="remove",shown=preview?.revision===revision?preview.record:null;
    if(mode==="confirm"&&(!shown?.required_confirmation||shown.execution?.status!=="ready")){setStatus("请先读取并检查当前草稿的规则来源");return;}
    const proposed=candidate(removal),receipt=mode==="confirm"?UI.clone(shown.required_confirmation):confirmed()?{
      expected_snapshot_id:row.expected_snapshot_id,expected_node_sha256:row.expected_node_sha256}:null;
    if(mode==="confirm")Object.assign(proposed.elemental_sources.find(s=>s.profile_id===row.profile_id),receipt);
    const token=++request,version=revision;busy=true;refresh();
    let response;
    try{response=await callApi("compose_equipment_plan",proposed);}catch(error){response={code:1,message:String(error)};}
    if(token!==request||version!==revision||!current()){if(!closed&&!current())valid();return;}
    busy=false;const report=response.data,record=MC.elementalCompilation(report,row.profile_id);
    if(response.code||!report||!["ready","pending"].includes(report.state)||(!removal&&!record)){
      refresh();errors.append(UI.error(response.code?response:{code:1,message:"未获得可校验的来源回执，请重新读取"}));return;}
    if(!removal&&row.application_mode==="sequence"&&record.rule_group?.group!==row.rule_group){
      refresh();errors.append(UI.el("p",`数据库回执规则组（${record.rule_group?.group||"缺失"}）与草稿（${row.rule_group}）不一致，未应用。`,"eq-error"));return;}
    preview={revision,record,issues:report.missing_inputs||[]};
    if(!removal&&receipt&&!same(record,receipt)){
      row.expected_snapshot_id=null;row.expected_node_sha256=null;revision++;preview.revision=revision;refresh();
      errors.append(UI.el("p","数据库来源已变化，尚未确认或应用。请检查新显示的来源，再明确确认。","eq-error"));return;}
    if(mode==="confirm"){
      if(record.status!=="ready"){refresh();errors.append(UI.el("p","当前规则仍待解释，来源确认未写入草稿。","eq-error"));return;}
      Object.assign(row,receipt);revision++;preview.revision=revision;refresh();setStatus("当前来源已核对，确认记录仅保存在本次编辑草稿中");return;}
    if(mode==="preview"){refresh();return;}
    original.elemental_sources=UI.clone(proposed.elemental_sources);
    original.mechanics_model.elemental_model.profiles=UI.clone(proposed.mechanics_model.elemental_model.profiles);
    close();MC.touch();MC.elementalDiagnostics={plan:original,input:PL.currentInputJson(),epoch:PL.state.sourceEpoch,
      path:RT.state.path,sha:PL.state.fileSha,...UI.clone({record,issues:report.missing_inputs||[],state:report.state})};MC.render();
    setStatus(removal?"已移除来源绑定；附着冷却保留待核实，请明确填写规则":report.state==="pending"?"已应用待完善的来源绑定；请查看机制页中的校验问题，尚未写入文件":"来源绑定已应用到当前方案；尚未写入文件，请重新计算或保存");
  };
  const drawForm=()=>{form.innerHTML="";form.append(UI.field("附着档案",UI.select(available.map(p=>({value:p.id,label:`${MC.label(p.element)} · ${p.id}`})),row.profile_id,v=>update("profile_id",v))),
    UI.field("元素附着模式",UI.select(window.AM?.applicationModes||[{value:"pending",label:"待核实（阻止确定性计算）"},{value:"sequence",label:"有限序列（计数标签 + 规则组）"},{value:"no_icd",label:"每次均附着（无 ICD）"},{value:"no_application",label:"明确不施加元素"}],row.application_mode,updateMode)),
    UI.field("数据库资料线",UI.select([{value:"release",label:"发布资料线"},{value:"beta",label:"测试资料线"}],row.version_line,v=>update("version_line",v))));
    if(row.application_mode==="sequence")form.append(
      UI.field("附着规则组（区分大小写）",UI.input(row.rule_group||"",v=>{rememberedRule=v;update("rule_group",v);})),
      UI.field("附着计数标签（ICD Tag）",UI.input(row.counter_tag||"",v=>{rememberedTag=v;update("counter_tag",v);})),
      UI.el("p","附着规则组提供重置时间和有限序列；计数标签决定具体攻击间的共享计数。两者不能互换。"));
    else form.append(UI.el("p",row.application_mode==="no_icd"?"无 ICD：每次命中均允许附着，不填写计数标签或规则组。":row.application_mode==="no_application"?"无附着：该攻击组件不尝试施加元素，与缺资料不同。":"待核实：Agent须查询具体攻击段，确定性计算会被阻止。",row.application_mode==="pending"?"eq-error":null));
    form.append(UI.field("附着模式与计数关系依据（来源、版本）",UI.area(row.source,v=>update("source",v))),
      UI.field("逐段结构化证据状态",UI.select([{value:"pending",label:"待核实（不生成伪证据）"},{value:"confirmed",label:"已逐段核实"}],verificationStatus,v=>{verificationStatus=v;revision++;request++;busy=false;preview=null;drawForm();refresh();})),
      UI.field("证据类型与定位（URL、本地路径或原始 pointer）",UI.input(verification.locator,v=>{verification.locator=v;revision++;request++;busy=false;preview=null;refresh();})),
      UI.field("适用游戏版本或固定 commit",UI.input(verification.version,v=>{verification.version=v;revision++;request++;busy=false;preview=null;refresh();})),
      UI.field("检索/复核日期（YYYY-MM-DD）",UI.input(verification.reviewed_at,v=>{verification.reviewed_at=v;revision++;request++;busy=false;preview=null;refresh();})),
      UI.field("具体攻击 component_scope 与共享范围",UI.area(verification.component_scope,v=>{verification.component_scope=v;revision++;request++;busy=false;preview=null;refresh();})),
      UI.el("p",`confirmed_fields 由模式生成：${row.application_mode==="sequence"?"application_mode、counter_tag、shared_scope":row.application_mode==="pending"?"待核实模式不生成证据":"application_mode、shared_scope"}。`));};
  dialog.appBody.append(UI.el("p","数据库规则组提供计时和有限附着序列。计数标签决定哪些攻击共享计数；缺少可靠 attenuationTag 时，Agent 必须逐攻击段查证，不能按普攻/战技/爆发类别猜测。"),
    UI.el("p","同技能不同段、召唤物和命座追加攻击可能使用不同计数标签；同名规则组也不表示它们共享计数。"),
    UI.el("p","应用绑定后，来源拥有该档案的附着模式。移除绑定后保留待核实占位，不会从旧计算结果恢复规则。"),form,previewBox,errors,actions);
  dialog.appFooter.append(UI.button("← 返回机制列表",close));dialog.appFooter.hidden=false;drawForm();refresh();
};
MC.renderElementalSources=box=>{
  const sources=PL.state.plan?.elemental_sources||[],card=UI.el("section",null,"eq-card");
  card.append(UI.el("h4",`数据库附着规则来源（${sources.length}）`),UI.el("p","读取规则组参数并核对来源；计数标签（ICD Tag）须来自具体攻击组件的 attenuationTag 或有证据的方案声明。"),
    UI.button("添加数据库附着规则来源",()=>MC.editElementalSource()));
  sources.forEach((source,index)=>card.append(UI.el("p",`${source.profile_id} · ${window.AM?.modeText?window.AM.modeText(source):source.application_mode||"有限序列"} · ${source.rule_group||source.attenuation_group||"无规则组"} · ${source.expected_snapshot_id&&source.expected_node_sha256?"已有来源确认，需以本次计算复核为准":source.verification?.status==="confirmed"?"结构化逐段证据":"待核实来源"}`),
    UI.button(`编辑来源 ${index+1}`,()=>MC.editElementalSource(index))));
  const check=MC.elementalDiagnostics;
  if(check&&check.plan===PL.state.plan&&check.input===PL.currentInputJson()&&check.epoch===PL.state.sourceEpoch&&check.path===RT.state.path&&check.sha===PL.state.fileSha){
    card.append(UI.el("p",`最近一次来源编辑校验：${check.state==="ready"?"已通过所声明输入的组合检查":"方案待完善"}；后续计算仍会重新核对来源。`));
    MC.renderElementalIssues(card,check.issues,"方案待完善事项");
    if(check.record)MC.renderElementalCompilation(card,check.record);
  }box.append(card);
};
MC.hitParameterCompilation=(report,id)=>(report?.hit_parameter_compilations||report?.plan?.resolution?.hit_parameter_compilations||[]).find(r=>r.hit_id===id);
MC.renderHitParameterCompilation=(box,record)=>{
  if(!record){box.append(UI.el("p","尚无当前草稿的技能参数预览。"));return;}
  const scalar=record.scalar_projection,descriptions=record.parameter_descriptions;
  box.append(UI.el("p",`数据库原始数值：${scalar?.value??"缺失"}；有效天赋等级：${record.level?.effective_level??"待核实"}`),
    UI.el("p",`倍率属性：${MC.label(record.scaling_stat)}；用途状态：${MC.label(record.interpretation_status||"pending")}`),
    UI.el("p","来源确认只核对当前数据库。用途由方案声明，引擎尚未验证游戏语义；治疗、冷却和加算贡献不能当作直接伤害倍率。"));
  if(record.parameter_contract==="level-bound-parameter-v1"){
    box.append(UI.el("p",`基础等级：${record.level?.base_level??"待核实"}；已核实的命座加级：${record.level?.extra_level??"待核实"}。其它命座修改尚未执行。`));
    MC.renderElementalIssues(box,record.level_profile?.issues,"有效等级依据待核实");
  }
  if(descriptions?.matched?.length){box.append(UI.el("h4","对应参数的完整中文原文"));
    descriptions.matched.forEach(row=>box.append(UI.el("p",row.text)));}
  else box.append(UI.el("p","没有找到引用此参数的中文原文；请补充用途依据，保持待核实直到完成检查。","eq-error"));
  if(descriptions?.unresolved?.length)box.append(UI.el("p",`有 ${descriptions.unresolved.length} 条描述缺文或无法解析；现有原文不能代表技能资料完整。`,"eq-error"));
  if(record.source)box.append(UI.el("p",`数值来源：${record.source.source_path}${record.source.parameter_pointer||record.source.json_pointer||""}`));
  if(record.actor_identity==="explicitly_declared")box.append(UI.el("p","角色身份仅由本方案声明，尚无装备角色关联用于交叉核对。"));
  if(record.issue)box.append(UI.el("p",record.issue.message||"技能参数来源不可用","eq-error"));
  MC.renderElementalIssues(box,scalar?.issues,"参数尚不能自动采用");
  box.append(UI.details("查看完整参数、描述与来源证据",record));
};
MC.editHitParameterSource=index=>{
  if(PL.state.draft){setStatus("请先应用或放弃装备草稿");return;}
  const original=PL.state.plan,hits=original?.manual_template?.hits||[],bindings=original?.hit_parameter_sources||[];
  if(!original||original.plan_version!==3){setStatus("数据库技能倍率来源需要原生第三版方案");return;}
  const others=new Set(bindings.filter((_,i)=>i!==index).map(s=>s.hit_id)),available=hits.filter(h=>h.damage_system==="classic"&&!others.has(h.id));
  if(!available.length){setStatus("请先建立未绑定来源的常规伤害命中或原型");return;}
  const initial=hits.find(h=>h.id===bindings[index]?.hit_id)||available[0],ref=original.equipment_inputs?.[initial.character]?.character_ref;
  const supportsLevels=PL.state.caps?.hit_parameter_source_contract?.level_contract==="level-bound-parameter-v1";
  const row=index===undefined?{hit_id:initial.id,version_line:ref?.version_line||"release",selection:{character_id:ref?.db_character_id??null,slot:"skill",constellation:0,...(supportsLevels?{base_level:10}:{talent_level:10})},
    ...(supportsLevels?{parameter_contract:"level-bound-parameter-v1",expected_level_fingerprint:null}:{}),
    param_index:0,scaling_stat:initial.scaling_stat||"",purpose:"single_stat_damage_multiplier",status:"pending",source:"",expected_snapshot_id:null,expected_node_sha256:null}:UI.clone(bindings[index]);
  const baseline=PL.currentInputJson(),epoch=PL.state.sourceEpoch,path=RT.state.path,sha=PL.state.fileSha;
  const form=UI.el("div"),previewBox=UI.el("div"),errors=UI.el("div"),actions=UI.el("div",null,"eq-actions");
  let revision=0,request=0,busy=false,closed=false,preview=null,manualMultiplier=null;
  const invalidate=()=>{if(closed)return;closed=true;request++;};
  const dialog=window.AppDialogKernel?.create?.({title:"数据库技能倍率来源",className:"mc-dialog",
    description:"只给已有命中或原型取数，不生成攻击；用途、倍率属性与等级关系仍需单独核实。",
    onRequestClose:()=>{invalidate();return true;}})||null;
  if(!dialog){setStatus("当前还有窗口正在处理，请先完成或关闭后再编辑技能倍率来源");return;}
  const current=()=>!closed&&PL.state.plan===original&&PL.currentInputJson()===baseline&&!PL.state.draft
    &&PL.state.sourceEpoch===epoch&&RT.state.path===path&&PL.state.fileSha===sha;
  const valid=()=>{if(current())return true;setStatus("方案或数据库状态已变化，请重新打开倍率来源编辑");return false;};
  const close=()=>{invalidate();dialog.finish("hit-parameter-source-close");};
  const levelBound=()=>row.parameter_contract==="level-bound-parameter-v1";
  const confirmed=()=>!!row.expected_snapshot_id&&!!row.expected_node_sha256&&(!levelBound()||!!row.expected_level_fingerprint);
  const same=(record,receipt)=>!!record?.required_confirmation&&record.required_confirmation.expected_snapshot_id===receipt.expected_snapshot_id
    &&record.required_confirmation.expected_node_sha256===receipt.expected_node_sha256
    &&(!levelBound()||!!receipt.expected_level_fingerprint&&record.required_confirmation.expected_level_fingerprint===receipt.expected_level_fingerprint);
  const supported=record=>record?.scalar_projection?.status==="ready"&&Number.isFinite(record.scalar_projection.value)&&record.scalar_projection.value>=0
    &&(!levelBound()||record.parameter_contract==="level-bound-parameter-v1"&&record.level_profile?.status==="ready"&&!!record.required_confirmation?.expected_level_fingerprint);
  const candidate=remove=>{const plan=UI.clone(original),sources=plan.hit_parameter_sources||[],hit=plan.manual_template.hits.find(h=>h.id===row.hit_id);
    if(remove){sources.splice(index,1);hit.multiplier=manualMultiplier;}
    else {if(index===undefined)sources.push(UI.clone(row));else sources[index]=UI.clone(row);
      delete hit.multiplier;if(!Object.hasOwn(hit,"scaling_stat"))hit.scaling_stat=row.scaling_stat;}
    plan.hit_parameter_sources=sources;return plan;};
  const refresh=()=>{dialog.setBusy(busy);previewBox.innerHTML="";errors.innerHTML="";
    previewBox.append(UI.el("p",busy?"正在读取当前技能参数…":confirmed()?"草稿已记录数值来源确认；用途状态仍需单独核实。":"数值来源尚未确认；可以先应用为待核实草稿。"));
    if(preview?.revision===revision){MC.renderHitParameterCompilation(previewBox,preview.record);MC.renderElementalIssues(previewBox,preview.issues,"方案待完善事项");}
    else previewBox.append(UI.el("p","请读取当前数据库，检查完整中文原文及用途；不会采用旧报告中的数值或确认信息。"));
    actions.innerHTML="";const add=(label,fn,disabled=false)=>{const b=UI.button(label,fn);b.disabled=busy||disabled;actions.append(b);};
    add("读取并预览当前技能参数",()=>run("preview"));
    add("确认所示数值来源",()=>run("confirm"),!preview?.record?.required_confirmation||preview.revision!==revision||!supported(preview.record));
    add("应用倍率来源绑定",()=>run("apply"));
    if(index!==undefined)add("解除来源并采用手填倍率",()=>run("remove"),manualMultiplier===null||manualMultiplier<0);
    actions.append(UI.button("放弃并关闭",close));
  };
  const changed=()=>{row.expected_snapshot_id=null;row.expected_node_sha256=null;if(levelBound())row.expected_level_fingerprint=null;revision++;request++;busy=false;preview=null;refresh();};
  const update=(key,value)=>{if(row[key]===value)return;row[key]=value;changed();};
  const updateSelection=(key,value)=>{if(value===null)delete row.selection[key];else row.selection[key]=value;changed();};
  const run=async mode=>{
    if(busy||!valid())return;const removal=mode==="remove",shown=preview?.revision===revision?preview.record:null;
    if(removal&&(manualMultiplier===null||!Number.isFinite(manualMultiplier)||manualMultiplier<0)){setStatus("解除来源前请明确填写非负手动倍率，不会从旧报告补值");return;}
    if(mode==="confirm"&&(!shown?.required_confirmation||!supported(shown))){setStatus("请先读取并检查当前技能参数来源");return;}
    const proposed=candidate(removal),receipt=mode==="confirm"?UI.clone(shown.required_confirmation):confirmed()?{
      expected_snapshot_id:row.expected_snapshot_id,expected_node_sha256:row.expected_node_sha256,
      ...(levelBound()?{expected_level_fingerprint:row.expected_level_fingerprint}:{})}:null;
    if(mode==="confirm")Object.assign(proposed.hit_parameter_sources.find(s=>s.hit_id===row.hit_id),receipt);
    const token=++request,version=revision;busy=true;refresh();let response;
    try{response=await callApi("compose_equipment_plan",proposed);}catch(error){response={code:1,message:String(error)};}
    if(token!==request||version!==revision||!current()){if(!closed&&!current())valid();return;}
    busy=false;const report=response.data,record=MC.hitParameterCompilation(report,row.hit_id);
    if(response.code||!report||!["ready","pending"].includes(report.state)||(!removal&&!record)){
      refresh();errors.append(UI.error(response.code?response:{code:1,message:"未取得当前参数校验回执，请重新读取"}));return;}
    preview={revision,record,issues:report.missing_inputs||[]};
    if(!removal&&receipt&&!same(record,receipt)){
      row.expected_snapshot_id=null;row.expected_node_sha256=null;if(levelBound())row.expected_level_fingerprint=null;revision++;preview.revision=revision;refresh();
      errors.append(UI.el("p","数据库参数来源已变化，尚未确认或应用；请检查新显示的原文和数值后重新确认。","eq-error"));return;}
    if(mode==="confirm"){
      if(!supported(record)){refresh();errors.append(UI.el("p","本参数当前仍不能自动采用，确认未写入草稿。","eq-error"));return;}
      Object.assign(row,receipt);revision++;preview.revision=revision;refresh();setStatus("数值来源确认已记录；用途仍以显式核实状态为准");return;}
    if(mode==="preview"){refresh();return;}
    original.hit_parameter_sources=UI.clone(proposed.hit_parameter_sources);
    const hit=original.manual_template.hits.find(h=>h.id===row.hit_id),submitted=proposed.manual_template.hits.find(h=>h.id===row.hit_id);
    if(removal)hit.multiplier=submitted.multiplier;else {delete hit.multiplier;if(!Object.hasOwn(hit,"scaling_stat"))hit.scaling_stat=submitted.scaling_stat;}
    close();MC.touch();MC.hitParameterDiagnostics={plan:original,input:PL.currentInputJson(),epoch:PL.state.sourceEpoch,
      path:RT.state.path,sha:PL.state.fileSha,...UI.clone({record,issues:report.missing_inputs||[],state:report.state})};MC.render();
    setStatus(removal?"已解除来源并采用明确填写的倍率；尚未写入文件":report.state==="pending"?"已应用待完善的倍率来源，请查看校验问题；尚未写入文件":"倍率来源已应用；请重新计算或保存");
  };
  const draw=()=>{form.innerHTML="";const hit=hits.find(h=>h.id===row.hit_id);
    if(index===undefined)form.append(UI.field("命中或连续命中原型",UI.select(available.map(h=>({value:h.id,label:`${DS.character(h.character,RT.tpl(),original)} · ${h.hit_name||h.skill||h.id} · ${h.id}`})),row.hit_id,v=>{
      const previous=hits.find(h=>h.id===row.hit_id),next=hits.find(h=>h.id===v);
      row.hit_id=v;row.scaling_stat=next.scaling_stat||"";
      if(previous?.character!==next.character){const targetRef=original.equipment_inputs?.[next.character]?.character_ref;
        row.selection={character_id:targetRef?.db_character_id??null,slot:"skill",constellation:0,...(levelBound()?{base_level:10}:{talent_level:10})};
        row.version_line=targetRef?.version_line||"release";row.param_index=0;row.source="";row.status="pending";}
      changed();draw();})));
    else form.append(UI.el("p",`命中或原型：${row.hit_id}；角色：${DS.character(hit.character,RT.tpl(),original)}`));
    if(hit.scaling_stat)form.append(UI.el("p",`本命中已声明倍率属性：${MC.label(hit.scaling_stat)}（本页不会改动）`));
    else form.append(UI.field("明确选择本命中的倍率属性",UI.select([{value:"",label:"请选择，不默认攻击力"},...['atk','hp','def','em'].map(v=>({value:v,label:MC.label(v)}))],row.scaling_stat,v=>update("scaling_stat",v))));
    if(supportsLevels||levelBound())form.append(UI.field("参数等级模式",UI.select([{value:"level",label:"基础等级与静态命座加级"},{value:"legacy",label:"旧版零命10级兼容模式"}],levelBound()?"level":"legacy",v=>{
      delete row.selection.base_level;delete row.selection.talent_level;
      if(v==="level"){row.parameter_contract="level-bound-parameter-v1";row.expected_level_fingerprint=null;row.selection.base_level=10;}
      else{delete row.parameter_contract;delete row.expected_level_fingerprint;row.selection.talent_level=10;}
      changed();draw();})));
    form.append(UI.field("数据库角色编号",UI.input(row.selection.character_id,v=>updateSelection("character_id",v),true)),
      UI.field("数据库资料线",UI.select([{value:"release",label:"发布资料线"},{value:"beta",label:"测试资料线"}],row.version_line,v=>update("version_line",v))),
      UI.field("技能定位方式",UI.select([{value:"slot",label:"技能槽"},{value:"skill_id",label:"明确技能编号"}],Object.hasOwn(row.selection,"skill_id")?"skill_id":"slot",v=>{
        delete row.selection.slot;delete row.selection.skill_id;row.selection[v]=v==="slot"?"skill":null;changed();draw();})));
    if(Object.hasOwn(row.selection,"skill_id"))form.append(UI.field("数据库技能编号",UI.input(row.selection.skill_id,v=>updateSelection("skill_id",v),true)));
    else form.append(UI.field("技能槽",UI.select([{value:"normal",label:"普通攻击"},{value:"skill",label:"元素战技"},{value:"burst",label:"元素爆发"}],row.selection.slot,v=>updateSelection("slot",v))));
    form.append(UI.field("技能组编号（多技能组角色须填写）",UI.input(row.selection.skill_depot_id,v=>updateSelection("skill_depot_id",v),true)),
      UI.field("已激活命座数",UI.input(row.selection.constellation,v=>updateSelection("constellation",v),true)),
      levelBound()?UI.field("基础天赋等级（1至10，留空采用10级）",UI.input(row.selection.base_level,v=>updateSelection("base_level",v),true)):
        UI.field("有效天赋等级（C0 留空采用10级）",UI.input(row.selection.talent_level,v=>updateSelection("talent_level",v),true)),
      UI.field("参数序号（从0开始）",UI.input(row.param_index,v=>update("param_index",v),true)),
      UI.field("倍率用途核实状态",UI.select([{value:"pending",label:"待核实"},{value:"assumed",label:"情景假设"},{value:"confirmed",label:"已人工或Agent核实"}],row.status,v=>update("status",v))),
      UI.field("作为单属性直接伤害倍率的依据",UI.area(row.source,v=>update("source",v))));
    if(index!==undefined)form.append(UI.field("解除绑定后采用的手填倍率（不自动恢复）",UI.input(manualMultiplier,v=>{manualMultiplier=v;revision++;request++;busy=false;preview=null;refresh();},true)));
  };
  dialog.appBody.append(UI.el("p","只给已有命中或原型取数，不生成攻击。应用绑定会移除手填倍率并由数据库编译；不会改变原有倍率属性、动作、时点或次数。"),
    UI.el("p","新等级模式从基础等级和已激活命座读取原始参数；其它命座效果尚未执行。旧兼容模式限零命10级。有效13级须来自已核实的加级关系，不能直接填写代替依据。"),form,previewBox,errors,actions);
  dialog.appFooter.append(UI.button("← 返回机制列表",close));dialog.appFooter.hidden=false;draw();refresh();
};
MC.renderHitParameterSources=box=>{
  const sources=PL.state.plan?.hit_parameter_sources||[],card=UI.el("section",null,"eq-card");
  card.append(UI.el("h4",`数据库技能倍率来源（${sources.length}）`),UI.el("p","从精确技能参数读取已有命中的倍率；用途与倍率属性需要单独核实。"),
    UI.button("添加数据库技能倍率来源",()=>MC.editHitParameterSource()));
  sources.forEach((row,index)=>card.append(UI.el("p",`${row.hit_id} · 参数 ${row.param_index} · ${MC.label(row.scaling_stat)} · 用途${MC.label(row.status)} · ${row.expected_snapshot_id&&row.expected_node_sha256?"已记录来源确认，本次计算仍会复核":"来源待核实"}`),
    UI.button(`编辑倍率来源 ${index+1}`,()=>MC.editHitParameterSource(index))));
  const check=MC.hitParameterDiagnostics;
  if(check&&check.plan===PL.state.plan&&check.input===PL.currentInputJson()&&check.epoch===PL.state.sourceEpoch&&check.path===RT.state.path&&check.sha===PL.state.fileSha){
    card.append(UI.el("p",`最近一次倍率来源校验：${check.state==="ready"?"通过声明输入检查":"方案待完善"}；后续计算仍会重新读取。`));
    MC.renderElementalIssues(card,check.issues,"方案待完善事项");if(check.record)MC.renderHitParameterCompilation(card,check.record);
  }box.append(card);
};
MC.reactionCompilation=report=>(report?.reaction_damage_compilations||report?.plan?.resolution?.reaction_damage_compilations||[])[0];
MC.renderReactionCompilation=(box,record)=>{
  if(!record){box.append(UI.el("p","请读取当前数据库，核对冰水原目标来源与伤害序列。"));return;}
  box.append(UI.el("p","数据库只提供伤害冷却的来源与参数。普通原目标归属和次序由当前规则集承担，尚未执行一般能力、特殊扩散或传播。"));
  (record.profiles||[]).forEach((profile,i)=>{const p=profile.projection||{};
    box.append(UI.el("h4",`${MC.label(profile.selection?.element||`来源 ${i+1}`)}扩散`),
      UI.el("p",`伤害序列：${p.damage_sequence?.join("、")??"尚不能执行"}；重置：${p.reset_cycle_seconds_rounded??"未知"} 秒`),
      UI.el("p",`序列状态：${MC.label(profile.execution?.sequence_status||"pending")}；完整能力仍未执行。`));
    MC.renderElementalIssues(box,profile.issues,"来源未解释项");});
  if(record.issue)box.append(UI.el("p",record.issue.message||"扩散伤害来源不可用","eq-error"));
  MC.renderElementalIssues(box,record.assembly_issues,"组合校验问题");box.append(UI.details("查看扩散来源原文与完整证据",record));
};
MC.swirlRulesets=["gauge-swirl-v2","gauge-freeze-reference-v1","gauge-swirl-dynamic-v3"];
Object.assign(MC.words,{"gauge-freeze-reference-v1":"连续冻结实验参考模型",reference_acceptance:"参考模型假设确认",
  contract_id:"约定编号",contract_fingerprint:"约定内容指纹",acknowledged:"已接受所列假设",experimental_reference:"实验参考",
  contact_profiles:"接触档案",contact:"接触方式",freeze_interaction:"冻结交互",may_reduce:"可能削冻或碎冰",
  ordinary_aura:"普通元素底",frozen:"冻结量",freeze:"冻结",freezable:"目标可被冻结",resistance:"冻结抗性",
  freeze_history_seconds:"冻结历史量（秒）",freeze_decay_gu_per_second:"当前冻结衰减速率",elemental_state_transition:"元素状态变化",
  layer_consumptions:"逐层消耗",reference_contract:"参考模型约定",profile:"已声明接触档案",
  frozen_expired:"冻结自然到期",ordinary_aura_expired:"普通元素底自然到期",freeze_history_recovered:"冻结历史已恢复",
  freeze_contact_unknown:"接触的削冻或碎冰性质待核实",freeze_package_effect_ambiguous:"同次多反应的收益顺序尚未明确"});
MC.elementalStateText=state=>{
  if(!state||state.knowledge==="unknown")return "未知";
  const number=value=>Number.isFinite(value)?String(Number(value.toFixed(6))):"未知";
  const aura=state.ordinary_aura??state.aura,parts=[];
  parts.push(aura?`${MC.label(aura.element)}底 ${number(aura.remaining_gu)} GU`:"无普通元素底");
  if(Object.hasOwn(state,"frozen"))parts.push(state.frozen?`冻结量 ${number(state.frozen.remaining_gu)} GU`:"未冻结",
    `冻结历史 ${number(state.freeze_history_seconds)} 秒`);
  return parts.join("；");
};
MC.renderReferenceModel=box=>{
  const original=PL.state.plan,model=original?.mechanics_model?.elemental_model;
  if(model?.ruleset!=="gauge-freeze-reference-v1")return;
  const contract=MC.caps?.elemental?.reference_contract,accepted=model.reference_acceptance;
  const card=UI.el("section",null,"eq-card");card.append(UI.el("h4","连续冻结：实验参考范围"),
    UI.el("p","本模型含有来源的规则假设和反应聚合约定。接受表示按这些约定计算，不表示所有机制已经游戏实测。"));
  if(!contract){card.append(UI.el("p","未取得当前参考约定，不能确认。"));box.append(card);return;}
  (contract.assumptions||[]).forEach(text=>card.append(UI.el("p",text)));
  const current=accepted?.acknowledged===true&&accepted.contract_id===contract.contract_id&&accepted.contract_fingerprint===contract.contract_fingerprint;
  card.append(UI.el("p",current?"已接受当前约定；重算仍会核对指纹。":"参考约定尚未确认或已经变化。"));
  let reason=accepted?.source||"";
  card.append(UI.field("本方案采用参考模型的依据",UI.area(reason,value=>{reason=value;})),
    UI.button("接受所示参考约定",()=>{
      if(PL.state.plan!==original||PL.state.draft||original.mechanics_model?.elemental_model!==model){setStatus("方案已变化，请重新打开机制列表");return;}
      if(!reason.trim()){setStatus("请说明本方案采用参考模型的依据");return;}
      model.reference_acceptance={contract_id:contract.contract_id,contract_fingerprint:contract.contract_fingerprint,acknowledged:true,source:reason};
      MC.touch();setStatus("已记录参考模型假设；尚未执行计算或验证真实游戏机制");
    }),UI.details("查看完整来源与约定",contract));box.append(card);
};
MC.editReactionDamageSource=()=>{
  if(PL.state.draft){setStatus("请先应用或放弃装备草稿");return;}
  const original=PL.state.plan,model=original?.mechanics_model?.elemental_model;
  if(original?.plan_version!==3||!MC.swirlRulesets.includes(model?.ruleset)||!model.swirl_damage){setStatus("请先明确受支持的普通扩散或冻结参考模型及扩散伤害档案");return;}
  const sourceElements=model.ruleset==="gauge-swirl-dynamic-v3"?["cryo","hydro","pyro"]:["cryo","hydro"];
  const sourceLabel=sourceElements.length===3?"冰水火":"冰水";
  const old=original.reaction_damage_sources?.[0],row=UI.clone(old||{reaction_type:"swirl",version_line:"release",source:"",expected_snapshot_id:null,expected_profile_fingerprint:null});
  const baseline=PL.currentInputJson(),epoch=PL.state.sourceEpoch,path=RT.state.path,sha=PL.state.fileSha;
  const previewBox=UI.el("div"),errors=UI.el("div"),actions=UI.el("div",null,"eq-actions");
  let revision=0,request=0,busy=false,closed=false,preview=null;
  const invalidate=()=>{if(closed)return;closed=true;request++;};
  const dialog=window.AppDialogKernel?.create?.({title:"数据库扩散伤害来源",className:"mc-dialog",
    description:`仅用于普通单目标${sourceLabel}扩散；来源应用不会修改动作或升级机制证据状态。`,
    onRequestClose:()=>{invalidate();return true;}})||null;
  if(!dialog){setStatus("当前还有窗口正在处理，请先完成或关闭后再编辑扩散伤害来源");return;}
  const current=()=>!closed&&PL.state.plan===original&&PL.currentInputJson()===baseline&&!PL.state.draft&&PL.state.sourceEpoch===epoch&&RT.state.path===path&&PL.state.fileSha===sha;
  const valid=()=>{if(current())return true;setStatus("方案或数据库状态已变化，请重新打开扩散伤害来源");return false;};
  const close=()=>{invalidate();dialog.finish("reaction-damage-source-close");};
  const receipt=()=>row.expected_snapshot_id&&row.expected_profile_fingerprint?{expected_snapshot_id:row.expected_snapshot_id,expected_profile_fingerprint:row.expected_profile_fingerprint}:null;
  const supported=record=>{
    const profiles=record?.profiles;
    if(record?.issue||record?.assembly_issues?.length||profiles?.length!==sourceElements.length)return false;
    if(JSON.stringify(profiles.map(p=>p.selection?.element).sort())!==JSON.stringify(sourceElements))return false;
    if(!profiles.every(p=>p.execution?.sequence_status==="ready"&&p.snapshot?.snapshot_id===record.required_confirmation?.expected_snapshot_id))return false;
    const valid=p=>{const q=p.projection||{},seq=q.damage_sequence,s=q.reset_cycle_seconds_rounded;
      return Number.isFinite(s)&&s>0&&s<=1e9&&Array.isArray(seq)&&seq.length>0&&seq.length<=64&&seq.every(v=>v===0||v===1);};
    return profiles.every(valid)&&profiles.every(p=>p.projection.reset_cycle_seconds_rounded===profiles[0].projection.reset_cycle_seconds_rounded
      &&JSON.stringify(p.projection.damage_sequence)===JSON.stringify(profiles[0].projection.damage_sequence));
  };
  const same=(record,confirmation)=>record?.required_confirmation?.expected_snapshot_id===confirmation.expected_snapshot_id&&record?.required_confirmation?.expected_profile_fingerprint===confirmation.expected_profile_fingerprint;
  const candidate=remove=>{const plan=UI.clone(original);plan.reaction_damage_sources=remove?[]:[UI.clone(row)];plan.mechanics_model.elemental_model.swirl_damage.icd={kind:"unknown"};return plan;};
  const refresh=()=>{dialog.setBusy(busy);previewBox.innerHTML="";errors.innerHTML="";
    previewBox.append(UI.el("p",busy?`正在核对当前${sourceLabel}来源…`:receipt()?"已记录来源确认，计算仍会重新读取。":"来源尚未确认，可保存待完善草稿。"));
    MC.renderReactionCompilation(previewBox,preview?.revision===revision?preview.record:null);
    if(preview?.revision===revision)MC.renderElementalIssues(previewBox,preview.issues,"方案待完善事项");
    actions.innerHTML="";const add=(label,fn,disabled=false)=>{const button=UI.button(label,fn);button.disabled=busy||disabled;actions.append(button);};
    add("读取并预览扩散来源",()=>run("preview"));add("确认所示扩散来源",()=>run("confirm"),!preview?.record?.required_confirmation||preview.revision!==revision||!supported(preview.record));
    add("应用扩散来源绑定",()=>run("apply"));if(old)add("移除扩散来源绑定",()=>run("remove"));actions.append(UI.button("放弃并关闭",close));};
  const changed=()=>{row.expected_snapshot_id=null;row.expected_profile_fingerprint=null;revision++;request++;busy=false;preview=null;refresh();};
  const run=async mode=>{
    if(busy||!valid())return;const remove=mode==="remove",shown=preview?.revision===revision?preview.record:null;
    if(mode==="confirm"&&(!shown?.required_confirmation||!supported(shown))){setStatus(`请先读取并核对当前${sourceLabel}来源`);return;}
    const proposed=candidate(remove),confirmation=mode==="confirm"?UI.clone(shown.required_confirmation):receipt();
    if(mode==="confirm")Object.assign(proposed.reaction_damage_sources[0],confirmation);
    const token=++request,version=revision;busy=true;refresh();let response;
    try{response=await callApi("compose_equipment_plan",proposed);}catch(error){response={code:1,message:String(error)};}
    if(token!==request||version!==revision||!current()){if(!closed&&!current())valid();return;}busy=false;
    const report=response.data,record=MC.reactionCompilation(report);
    if(response.code||!report||!["ready","pending"].includes(report.state)||(!remove&&!record)){
      refresh();errors.append(UI.error(response.code?response:{code:1,message:"未取得当前扩散来源回执，请重新读取"}));return;}
    preview={revision,record,issues:report.missing_inputs||[]};
    if(!remove&&confirmation&&!same(record,confirmation)){row.expected_snapshot_id=null;row.expected_profile_fingerprint=null;revision++;preview.revision=revision;refresh();
      errors.append(UI.el("p","扩散来源已变化，未确认或应用；请复核新显示的内容。","eq-error"));return;}
    if(mode==="confirm"){if(!supported(record)||record.status!=="ready"){refresh();errors.append(UI.el("p","当前伤害序列仍不能执行，确认未写入。","eq-error"));return;}
      Object.assign(row,confirmation);revision++;preview.revision=revision;refresh();return;}
    if(mode==="preview"){refresh();return;}
    original.reaction_damage_sources=UI.clone(proposed.reaction_damage_sources);model.swirl_damage.icd={kind:"unknown"};
    close();MC.touch();setStatus(remove?"已移除来源；伤害冷却保持未知，实际扩散前需补充规则":"扩散来源已应用；普通范围证据及方案其他缺项仍需核实，尚未写入文件");
  };
  dialog.appBody.append(UI.el("p",`仅用于普通单目标${sourceLabel}扩散。应用时移除手填伤害序列，原生保存未知占位，计算从数据库重新编译；不会修改动作或升级机制证据状态。`),
    UI.field("扩散来源资料线",UI.select([{value:"release",label:"发布资料线"},{value:"beta",label:"测试资料线"}],row.version_line,v=>{row.version_line=v;changed();})),
    UI.field("普通扩散适用范围与来源复核依据",UI.area(row.source,v=>{row.source=v;changed();})),previewBox,errors,actions);
  dialog.appFooter.append(UI.button("← 返回机制列表",close));dialog.appFooter.hidden=false;refresh();
};
MC.renderReactionDamageSources=box=>{
  const rows=PL.state.plan?.reaction_damage_sources||[],card=UI.el("section",null,"eq-card");
  card.append(UI.el("h4",`数据库扩散伤害来源（${rows.length}）`),UI.el("p","扩散发生与扩散出伤分别记录，伤害冷却不使用附着序列。"),
    UI.button(rows.length?"编辑扩散伤害来源":"添加扩散伤害来源",MC.editReactionDamageSource));box.append(card);
};
MC.render=()=>{
  const box=$("#mechanics-editor");if(!box)return;box.classList.toggle("hidden",!MC.shown);if(!MC.shown)return;
  box.innerHTML="";box.append(UI.button("← 返回排轴",()=>{MC.shown=false;MC.render();$("#btn-mechanics")?.scrollIntoView?.({block:"center"});}),UI.el("h3","机制规则与过程"));
  if(!MC.caps){box.append(UI.el("p","正在加载机制接口…"));return;}
  if(!MC.model()){box.append(UI.el("p","启用后可声明触发、次数、叠层和采样，引擎与动作、能量共同执行。"),UI.button("启用机制模型",MC.enable));return;}
  box.append(UI.el("p","每条机制应关联装备效果或人工来源分项。先声明规则，再到角色装备的“效果处理”核实来源和整段覆盖。未知依据保留待核实。"),
    UI.button("添加机制及待核实来源",MC.newRule));
  ["rules","fixed_effect_sources","hit_events","hit_patterns","condition_events","initial_state","resource_ownership","shared_pools","attribute_model","state_runtime","assumptions","elemental_model","application_events"].filter(key=>MC.caps.schema.properties[key]).forEach(key=>{
    const card=UI.el("details",null,"eq-card mc-section-card"),value=MC.model()[key];card.append(UI.el("summary",`${MC.label(key)}${Array.isArray(value)?`（${value.length}）`:""}`),UI.button(`编辑${MC.label(key)}`,()=>MC.edit(key)));
    if(["rules","fixed_effect_sources"].includes(key))for(const rule of value||[])card.append(UI.el("p",`${DS.prose(rule.name_zh,"机制中文名待补")} · ${DS.character(rule.owner,RT.tpl(),PL.state.plan)} · ${MC.label(rule.status)}`));
    if(key==="shared_pools")card.append(UI.el("p","资源以整数记账；带单位换算的池以整数原子表示小数资源。支持按实际命中消费或明确的资源扣量操作。相关来源须核对当前整份方案；修改资源或动作后须重新核对并计算。"));
    if(key==="state_runtime")card.append(UI.el("p","声明领域建立、结束与定时刷新；时间字段使用微秒，1 秒为 1000000 微秒。领域结束仅停止后续发放，已发放收益按自身持续时间到期。来源需核对当前整份方案。"));
    if(key==="attribute_model"&&value)card.append(UI.el("p",value.contract==="attribute-ledger-v3"?
      "精通与攻击总量来自当前面板；初始已含组成不会再加一次。百分比攻击按受益者基础攻击力折算，缺失白值须补全。非额外分类仅支持精通；未知仍须核实。":
      "总精通来自当前面板；非额外精通和已含组成按依据声明。未知分类不会被自动认定。"),UI.button("核对并采用属性来源",MC.adoptAttributes));
    card.append(UI.details("查看原始声明",value??[]));box.append(card);
  });
  MC.renderElementalVerificationTasks(box,PL.state.report);
  MC.renderElementalSources(box);
  MC.renderHitParameterSources(box);
  MC.renderReactionDamageSources(box);
  MC.renderReferenceModel(box);
  window.AE?.render(box);
  window.SC?.render(box);
  box.append(UI.details("当前能力与限制",{supported:MC.caps.supported,unsupported:MC.caps.unsupported}));
};
MC.renderSharedPools=(section,result,template)=>{
  const pools=result.shared_pools;if(!pools)return;
  const box=UI.el("section",null,"eq-card"),table=UI.el("table",null,"grid"),head=UI.el("tr"),rows=pools.trace||[];
  const scaled=pools.schema_version===2;
  const reasons={consumed:"已消费",spent:"已扣除",empty:"余额为空",granted:"已补充",set:"设置余额",insufficient:"余额不足",ineligible:"不满足资格",cooldown:"冷却未结束"};
  const poolDefinitions=RS.state.snapshot?.executionModel?.shared_pools?.pools
    ||RS.state.snapshot?.template?.mechanics_model?.shared_pools?.pools
    ||result.mechanism_execution?.model?.shared_pools?.pools||[];
  const names=new Map(poolDefinitions.map(p=>[p.id,p.name_zh]));
  box.append(UI.el("h4","共享资源流水"),UI.el("p","本次计算的只读消费回执。附加检查轮不增加伤害汇总；修改资源后需回到方案重新计算。"));
  if(scaled)box.append(UI.el("p","数量均为整数原子；实际单位量 = 原子数 ÷ 每单位原子数。未扣足与补充溢出分别记录。"));
  ["秒数 / 检查轮","资源池","操作","对象","请求 / 实际","余额变化","结果",...(scaled?["每单位原子数","未扣足 / 溢出"]:[])].forEach(v=>head.append(UI.el("th",v)));table.append(head);
  for(const row of rows.slice(0,250)){
    const tr=UI.el("tr"),kind=row.kind??row.type,values=[`${row.t??"—"} / ${row.cycle??"—"}`,
      names.get(row.pool_id)||row.pool_id,kind==="consume"?"消费":row.operation==="spend"?"扣除资源":MC.label(row.operation||kind),
      row.key==="team"?"全队":DS.character(row.key,template),`${row.requested??"—"} / ${row.accepted??"—"}`,
      `${row.before??"—"} → ${row.after??"—"}`,reasons[row.reason]||MC.label(row.reason??"applied")];
    if(scaled)values.push(row.unit_scale??"—",`${row.unfulfilled??"—"} / ${row.wasted??"—"}`);
    values.forEach(v=>tr.append(UI.el("td",v??"—")));table.append(tr);
  }
  const scroll=UI.el("div",null,"table-scroll");scroll.append(table);box.append(scroll);if(rows.length>250)box.append(UI.el("p",`已显示前 250 条，共 ${rows.length} 条。完整来源与回执见下方。`));
  box.append(UI.details("完整共享资源状态、来源与消费回执",pools));section.append(box);
};
MC.renderResourceCounters=(section,result)=>{
  const ledger=result.resource_counters;if(!ledger)return;
  const box=UI.el("details"),table=UI.el("table",null,"grid"),head=UI.el("tr"),rows=ledger.trace||[];
  const kinds={counter_accumulation:"实际消费累计",counter_reset:"重置累计",counter_window:"累计窗口变化"};
  const states={accumulated:"已累计",outside_window:"累计窗口外",no_spend:"无实际消费",created:"已创建",established:"已建立",closing:"正在结束",ended:"已结束"};
  const amount=v=>v&&typeof v==="object"?Object.entries(v).map(([k,n])=>`${k==="total"?"合计":k}：${n}`).join("；"):v??"—";
  box.append(UI.el("summary",`资源累计流水（${rows.length} 条，只读）`),UI.el("p",result.resource_counter_views?"按实际消费累计，数量为整数原子；单位量 = 原子数 ÷ 每单位原子数。含附加检查流水，不增加伤害汇总；收益取值另见资源读数流水。":"按实际消费累计，数量为整数原子；单位量 = 原子数 ÷ 每单位原子数。含附加检查流水，不增加伤害汇总；尚未配置累计转伤害。"));
  ["秒数","累计项 / 过程","请求 / 实际扣量 / 计入累计","累计变化","每单位原子数","状态"].forEach(v=>head.append(UI.el("th",v)));table.append(head);
  for(const row of rows.slice(0,250)){
    const tr=UI.el("tr"),values=[Number.isFinite(row.time_us)?row.time_us/1000000:"—",`${row.counter_id||"—"} / ${kinds[row.type]||row.type}`,
      `${row.requested??"—"} / ${row.resource_accepted??"—"} / ${row.accepted??"—"}`,`${amount(row.before)} → ${amount(row.after)}`,
      ledger.counter_units?.[row.counter_id]??"—",states[row.reason||row.phase]||row.reason||row.phase||"—"];
    values.forEach(v=>tr.append(UI.el("td",v)));table.append(tr);
  }
  const scroll=UI.el("div",null,"table-scroll");scroll.append(table);box.append(scroll);
  if(rows.length>250)box.append(UI.el("p",`已显示前 250 条，共 ${rows.length} 条。`));
  box.append(UI.details("完整资源累计账（含实际提交状态与来源审核）",ledger));section.append(box);
};
MC.renderResourceCounterViews=(section,result,template)=>{
  if(window.CF?.render(section,result,template))return;
  const ledger=result.resource_counter_views;if(!ledger)return;
  const box=UI.el("details"),table=UI.el("table",null,"grid"),head=UI.el("tr"),rows=ledger.trace||[];
  box.append(UI.el("summary",`资源读数流水（${rows.length} 条，只读）`),UI.el("p","从实际已提交的累计账读取。原始原子数、单位换算、封顶/阶梯与最终层数合成分开；此处不额外增加伤害。"));
  ["秒数","来源规则 / 收益","接收角色","累计 / 读数","原始原子 / 每单位原子","当前读数","单次值 / 层数 / 合成收益"].forEach(v=>head.append(UI.el("th",v)));table.append(head);
  for(const row of rows.slice(0,250)){
    const tr=UI.el("tr"),read=row.reading||{},values=[row.t??"—",`${row.rule_id||"—"} / ${row.effect_id||"—"}`,DS.character(row.character,template),
      `${read.counter_id||"—"} / ${read.view_id||"—"}`,`${read.raw_atoms??"—"} / ${read.unit_scale??"—"}`,row.source_value??"—",
      `${row.single_value??row.value??"—"} / ${row.layer_count??"—"} / ${row.combined_value??"—"}`];
    values.forEach(v=>tr.append(UI.el("td",v)));table.append(tr);
  }
  const scroll=UI.el("div",null,"table-scroll");scroll.append(table);box.append(scroll);
  if(rows.length>250)box.append(UI.el("p",`已显示前 250 条，共 ${rows.length} 条。`));
  box.append(UI.details("完整资源读数、来源、生命周期与实际账游标",ledger));section.append(box);
};
MC.renderResourceThresholds=(section,result,template)=>{
  const compact=v=>typeof v==="string"&&v.length>36?`${v.slice(0,18)}…${v.slice(-8)}`:v??"—";
  const specs=[["resource_threshold_observers","资源阈值观察"],["resource_threshold_triggers","阈值收益执行"]];
  for(const [field,title] of specs){
    const ledger=result[field];if(!ledger)continue;
    const observer=field===specs[0][0],rows=ledger.trace||[],box=UI.el("details"),table=UI.el("table",null,"grid"),head=UI.el("tr");
    table.style.tableLayout="fixed";table.style.width="100%";
    const lifecycle=rows.some(row=>row.lifecycle_id!==undefined);
    box.append(UI.el("summary",`${title}（${rows.length} 条，只读）`),UI.el("p","观察到阈值变化、签发调度与实际执行分别记录。领域开始仅表示已开启后续定时逻辑；只有“收益已激活”表示已发放Buff，持续区间见效果轴。包含附加检查流水，此处不额外增加伤害。数量为整数原子，单位见完整资源账。"));
    [lifecycle?"秒数 / 来源操作阶段":"秒数 / 阶段","父事件 / 执行标识","观察项或绑定 / 来源","结果","阈值 / 之前 → 之后","提供者",...(lifecycle?["目标领域 / 实例"]:[])].forEach(v=>head.append(UI.el("th",v)));table.append(head);
    if(lifecycle)box.append(UI.el("p","阶段列沿用触发它的原资源操作，不代表领域在该阶段已经建立；实际控制提交顺序见生命周期执行流水。"));
    for(const row of rows.slice(0,250)){
      const id=row[observer?"observer_id":"binding_id"],matches=(ledger.source_adoptions||[]).filter(r=>r.id===id),source=matches.length===1&&matches[0].status==="ready"?matches[0]:{};
      const name=source.name_zh?`${DS.prose(source.name_zh,"来源规则")}（${id}）`:id??"—",t=row[observer?"t_us":"time_us"],tr=UI.el("tr");
      const outcomes={activated:"收益已激活",not_activated:"未激活（条件不满足或冷却中）",pending:"待核实",invalid:"无效",failed:"失败",
        lifecycle_started:"领域已开始",lifecycle_kept:"保留原领域",lifecycle_ended:"领域已结束",no_active_instance:"没有活动领域，无需结束"};
      const outcome=observer?(row.crossed===true?"已跨越阈值":row.crossed===false?"未跨越阈值":"观察结果未知"):
        row.type==="threshold_dispatch_failed"?`派发失败：${outcomes[row.outcome]||"结果未知"}${row.message||row.code?`；${row.message||row.code}`:""}`:
        row.type==="threshold_dispatch_completed"?(outcomes[row.outcome]||"执行结果未知"):"执行过程未知";
      const comparison={enter_gte:"向上达到",enter_lte:"向下达到"}[row.comparison]||"方向未知",owner=row.owner??source.owner;
      const quantity=row.input_kind==="counter_value_key"||(row.counter_observation&&typeof row.counter_observation==="object")?"消费累计":"资源余额";
      const values=[`${Number.isSafeInteger(t)?t/1000000:"—"} / ${{pre:"事件前",post:"事件后"}[row.phase]||"阶段未知"}`,
        `${(observer?row.event_id:row.source_event_id)??"—"} / ${(observer?row.run_id:row.source_run_id)??"—"}`,
        `${name} / ${row.source_id??source.source_id??"—"}`,outcome,`${quantity} ${comparison} ${row.threshold_atoms??"—"} / ${row.before??"—"} → ${row.after??"—"}`,owner?DS.character(owner,template):"—",
        ...(lifecycle?[`${row.lifecycle_id??"—"} / ${row.target_generation_uid??"—"}`]:[])];
      values.forEach((v,index)=>{const shown=lifecycle&&index===1?`${compact(observer?row.event_id:row.source_event_id)} / ${compact(observer?row.run_id:row.source_run_id)}`:
        lifecycle&&index===6?`${compact(row.lifecycle_id)} / ${compact(row.target_generation_uid)}`:v;
        const cell=UI.el("td",shown);if(shown!==v)cell.title=v;cell.style.overflowWrap="anywhere";cell.style.whiteSpace="normal";tr.append(cell);});table.append(tr);
    }
    const scroll=UI.el("div",null,"table-scroll");scroll.append(table);box.append(scroll);
    if(rows.length>250)box.append(UI.el("p",`已显示前 250 条，共 ${rows.length} 条。`));
    box.append(UI.details("完整阈值观察、调度授权、实际结果及来源核对",ledger));section.append(box);
  }
};
MC.renderAttributeCallbacks=(section,result,template)=>{
  const ledger=result.attribute_callbacks;if(!ledger)return;
  const box=UI.el("details"),table=UI.el("table",null,"grid"),head=UI.el("tr"),rows=ledger.trace||[];
  const stages={callback_plan:"计划已生成",callback_batch_prepared:"批次取样已准备",callback_committed:"回调已提交"};
  const names=new Map((ledger.source_adoption?.members||[]).map(r=>[r.rule_id,r.name_zh]));
  box.append(UI.el("summary",`属性回调执行（${rows.length} 条，只读）`),UI.el("p","计划、取样准备与实际提交分开记录；只有“回调已提交”表示已执行。手工顺序是计算假设，不是数据库已认证的游戏事实。实际取样数值见属性账，此处不增加伤害。"));
  ["秒数 / 来源窗口","父命中 / 执行标识","过程","规则 / 来源","提供者","批次 / 属性帧"].forEach(v=>head.append(UI.el("th",v)));table.append(head);
  for(const row of rows.slice(0,250)){
    const tr=UI.el("tr"),origin=Number.isInteger(row.origin)&&row.origin>=0?(row.origin===0?"完整输入（0）":`附加检查（${row.origin}）`):"窗口未知";
    const values=[`${Number.isFinite(row.time_us)?row.time_us/1000000:"—"} / ${origin}`,`${row.parent_event_id??"—"} / ${row.parent_run_id??"—"}`,
      stages[row.type]||"过程未知",`${names.get(row.rule_id)?DS.prose(names.get(row.rule_id),"来源规则")+`（${row.rule_id}）`:row.rule_id??"—"} / ${row.source_id??"—"}`,row.owner?DS.character(row.owner,template):"—",
      `${row.batch??"—"} / ${row.frame_id??"—"}`];
    values.forEach(v=>tr.append(UI.el("td",v)));table.append(tr);
  }
  const scroll=UI.el("div",null,"table-scroll");scroll.append(table);box.append(scroll);
  if(rows.length>250)box.append(UI.el("p",`已显示前 250 条，共 ${rows.length} 条。`));
  box.append(UI.details("完整回调计划、依赖足迹、实际提交与来源核对",ledger));section.append(box);
};
MC.renderAttributeLedger=(section,result,template)=>{
  const ledger=result.attribute_ledger;if(!ledger)return;
  const multi=ledger.contract==="attribute-ledger-v3",stats={em:"元素精通",atk:"攻击力"},bases={total:"总量",nonextra:"非额外量",base:"基础量"};
  const box=UI.el("details"),table=UI.el("table",null,"grid"),head=UI.el("tr"),rows=ledger.receipts||[];
  const phases={initial:"初始状态",pre:"事件前",post:"事件后",reaction_created:"反应产生时",reaction_damage_pre:"反应出伤前",capture:"面板取样",trigger:"触发取样",damage_projection:"出伤属性投影"};
  box.append(UI.el("summary",`属性取样账（${rows.length} 条，只读）`),UI.el("p","显示引擎实际读取的基底与最终值。来源窗口0对应完整输入，附加检查窗口不增加伤害汇总；取样本身不代表已计入伤害，最终以伤害报告为准。未知值保留为空；修改面板、来源分类或动作后需重新计算。"));
  ["秒数 / 来源窗口 / 阶段","角色",...(multi?["属性"]:[]),"取值口径","基底","实际取值","状态"].forEach(v=>head.append(UI.el("th",v)));table.append(head);
  for(const row of rows.slice(0,250)){
    const q=row.query||{},f=q.frame||{},origin=Number.isInteger(f.origin)&&f.origin>=0?(f.origin===0?"完整输入（0）":`附加检查（${f.origin}）`):"窗口未知",tr=UI.el("tr"),values=[`${Number.isFinite(f.t_us)?f.t_us/1000000:"—"} / ${origin} / ${phases[f.phase]||MC.label(f.phase)||"—"}`,
      DS.character(q.actor,template),...(multi?[stats[q.stat]||q.stat||"—"]:[]),multi?(bases[q.basis]||q.basis||"—"):MC.label(q.basis),row.baseline??"—",row.value??"—",{known:"已知",pending:"待核实",invalid:"无效"}[row.status]||row.status];
    values.forEach(v=>tr.append(UI.el("td",v??"—")));table.append(tr);
  }
  const scroll=UI.el("div",null,"table-scroll");scroll.append(table);box.append(scroll);if(rows.length>250)box.append(UI.el("p",`已显示前 250 条，共 ${rows.length} 条。`));
  if(multi){
    const ratios=[];for(const row of rows.slice(0,250))for(const item of row.contributions||[]){
      if(item.application?.kind==="recipient_base_fraction"&&ratios.length<100)ratios.push({row,item});
    }
    if(ratios.length){
      const details=UI.el("details"),grid=UI.el("table",null,"grid"),heading=UI.el("tr"),holder=UI.el("div",null,"table-scroll");
      details.append(UI.el("summary",`比例加算明细（${ratios.length} 条，只读）`),UI.el("p","展示前250条取样回执中的至多100项比例加算。比例先取样与封顶，再乘受益者基础攻击一次；空值代表尚未获得计算结果。完整值与引用见原始属性账。"));
      ["受益角色 / 属性","提供者 / 来源部件 / 收益","实际比例（1为100%）","使用的基础攻击力","乘后平值","输入回执 / 基础攻击力回执"].forEach(v=>heading.append(UI.el("th",v)));grid.append(heading);
      for(const {row,item} of ratios){const source=item.component||{},tr=UI.el("tr");[
        `${DS.character(row.query?.actor,template)} / ${stats[item.stat]||item.stat||"—"}`,
        `${DS.character(source.provider,template)} / ${source.part_id||"—"} / ${source.effect_id||"—"}`,
        item.raw_evaluated_value??"—",item.application_base_value??"—",item.evaluated_value??"—",
        `${item.input_receipt_id||"常量来源"} / ${item.application_base_receipt_id||"—"}`
      ].forEach(v=>tr.append(UI.el("td",v)));grid.append(tr);}
      holder.append(grid);details.append(holder);box.append(details);
    }
  }
  box.append(UI.details("完整属性账与来源组成",ledger));section.append(box);
};
MC.renderStateRuntime=(section,result)=>{
  const state=result.state_runtime;if(!state)return;
  const box=UI.el("details"),table=UI.el("table",null,"grid"),head=UI.el("tr"),rows=state.execution_trace||[];
  const positioned=state.contract==="finite-state-mechanics-bridge-v2";
  const compact=v=>typeof v==="string"&&v.length>36?`${v.slice(0,18)}…${v.slice(-8)}`:v??"—";
  if(positioned){table.style.tableLayout="fixed";table.style.width="100%";}
  const names=new Map((result.mechanism_execution?.model?.state_runtime?.lifecycles||[]).map(r=>[r.id,r.name_zh]));
  box.append(UI.el("summary",`生命周期与定时执行（${rows.length} 条，只读）`),UI.el("p","领域调度与实际收益分开记录；结束领域会停止后续发放，已有收益的残留时间见效果轴。这里的记录不额外增加伤害或Buff。"));
  if(positioned)box.append(UI.el("p","逐步提交与整批完成分开；签发或领取批次不等于执行成功。失败时仅显示已完成前缀，不将它当作完整轴结果。"));
  ["秒数","领域 / 定时器","次数","实际结果","操作 / 目标","来源动作",...(positioned?["控制序号 / 批次 / 完成步数"]:[])].forEach(v=>head.append(UI.el("th",v)));table.append(head);
  for(const row of rows.slice(0,250)){
    const tr=UI.el("tr"),payload=row.payload||{},values=[Number.isFinite(row.time_us)?row.time_us/1000000:"—",
      `${names.get(row.lifecycle_id)||row.lifecycle_id||"—"} / ${row.timer_id||"—"}`,row.ordinal??"—",
      ["state_dispatch_failed","state_control_failed","state_delivery_failed"].includes(row.type)?`失败：${row.message||row.reason||"详见完整回执"}`:
        row.type==="state_control_completed"?(positioned?"领域控制已提交":"累计控制已提交"):
        row.type==="state_batch_completed"?"控制批次已完成":
        {activated:"收益已激活",not_activated:"条件不满足或冷却中",resource_operation:"资源操作已处理",pending:"待核实",failed:"失败"}[row.outcome]||MC.label(row.outcome),
      `${MC.label(payload.kind||(positioned?row.kind:null))||"—"} / ${payload.ref_id||(positioned?row.lifecycle_id:null)||"—"}`,row.source_event_id||row.event_run_id||"—",
      ...(positioned?[`${row.control_sequence??"—"} / ${row.batch_id??"—"} / ${row.step_count??row.applied_steps??"—"}`]:[])];
    values.forEach((v,index)=>{const shown=positioned&&index===6?`${row.control_sequence??"—"} / ${compact(row.batch_id)} / ${row.step_count??row.applied_steps??"—"}`:
      positioned&&index===5?compact(v):v;
      const cell=UI.el("td",shown??"—");if(shown!==v)cell.title=v;if(positioned){cell.style.overflowWrap="anywhere";cell.style.whiteSpace="normal";}tr.append(cell);});table.append(tr);
  }
  const scroll=UI.el("div",null,"table-scroll");scroll.append(table);box.append(scroll);if(rows.length>250)box.append(UI.el("p",`已显示前 250 条，共 ${rows.length} 条。`));
  box.append(UI.details("完整生命周期调度与执行回执",state));section.append(box);
};
MC.renderTrace=(result,template)=>{
  const section=UI.el("section",null,"eq-card");section.append(UI.el("h3",`机制执行：${MC.label(result.mechanics_status)}`),
    UI.el("p","下方记录属于本次计算快照；输入变化后须重新计算。能量与动作的检查结论单独列示。"));
  if(result.mechanics_issues?.length)section.append(UI.details("机制问题定位",result.mechanics_issues));
  if(result.elemental?.evidence_tier==="experimental_reference")section.append(
    UI.el("p","本次元素状态采用实验参考模型；冻结量合并、事件聚合和取值时点含已接受的假设。"),
    UI.details("本次参考模型与采用依据",{contract:result.elemental.reference_contract,acceptance:result.elemental.reference_acceptance}));
  window.SC?.renderTrace(section,result);
  const rows=(result.mechanics_trace||[]).filter(r=>!["target_condition","snapshot_capture","snapshot_capture_reference"].includes(r.type)),table=UI.el("table"),head=UI.el("tr");
  ["秒数","过程","角色 / 规则","收益 / 原因"].forEach(v=>head.append(UI.el("th",v)));table.append(head);
  const names=new Map((RS.state.snapshot?.template?.mechanics_model?.rules||[]).map(r=>[r.id,r.name_zh]));
  const hits=new Map((result.hits||[]).map(h=>[h.id,h]));
  for(const row of rows.slice(0,250)){
    let detail=row.reason?DS.prose(MC.label(row.reason),"详见原始定位"):row.value??"";
    if(row.type==="quota")detail=`剩余次数 ${row.before} → ${row.after}${detail?` · ${detail}`:""}`;
    if(row.type==="activation")detail=`当前层数 ${row.layers}；到期秒数 ${(row.expires_at||[]).join("、")}；次数池 ${Object.entries(row.quota||{}).map(([cid,n])=>`${cid==="team"?"全队":DS.character(cid,template)} ${n}`).join("、")||"无"}`;
    if(row.beneficiary_selection && window.EFX) detail += `；${EFX.selectionDescription(row.beneficiary_selection,id=>DS.character(id,template)).join("；")}`;
    if(row.type==="expiry")detail=`层数 ${row.layers_before} → ${row.layers_after}`;
    if(row.type==="hit")detail=`${row.reported_damage?"已报告的命中（伤害汇总统计完整输入窗口）":"仅参与机制、不计伤害报告"}；生效收益 ${row.effect_count}`;
    if(row.type==="elemental_application"){
      const reactions=Array.isArray(row.reactions)?row.reactions:row.reaction?[row.reaction]:[];
      const reaction=reactions.map(r=>`${MC.label(r.type)}${r.reaction_element?`（${MC.label(r.reaction_element)}）`:""}${Number.isFinite(r.factor)?` ×${r.factor}`:""}`).join("、");
      detail=`${MC.label(row.reason)}；${MC.elementalStateText(row.before)} → ${MC.elementalStateText(row.after)}${reaction?`；${reaction}`:""}`;
    }
    if(row.type==="elemental_state_transition")detail=`${MC.label(row.reason)}；${MC.elementalStateText(row.before)} → ${MC.elementalStateText(row.after)}`;
    if(row.type==="reaction_damage_attempt")detail=`${row.allowed===true?"允许出伤":row.allowed===false?"伤害冷却阻止出伤":"伤害规则待核实"}；反应已独立记录，出伤受阻不撤销耗底。`;
    if(row.type==="reaction_damage")detail="独立派生的扩散伤害；提供者参数在反应产生时取值，敌方状态在出伤前取值。";
    const tr=UI.el("tr"),values=[row.t,DS.prose(row.type==="quota"?"次数消费":MC.label(row.type),"执行记录"),
      row.type==="hit"&&hits.has(row.event_id)?DS.character(hits.get(row.event_id).character,template):
        DS.prose(row.name_zh||names.get(row.rule_id),row.character?DS.character(row.character,template):"角色或机制见原始定位"),detail];
    values.forEach(v=>tr.append(UI.el("td",v??"—")));table.append(tr);
  }
  section.append(table);if(rows.length>250)section.append(UI.el("p",`已显示前 250 条，共 ${rows.length} 条，完整记录见下方。`));
  section.append(UI.details("完整机制流水（含事件定位）",result.mechanics_trace||[]),UI.details("属性取值与依赖",result.sampling_trace||[]));
  window.RR?.render(section,result,template);
  MC.renderSharedPools(section,result,template);MC.renderResourceCounters(section,result);MC.renderResourceCounterViews(section,result,template);MC.renderResourceThresholds(section,result,template);MC.renderAttributeCallbacks(section,result,template);MC.renderAttributeLedger(section,result,template);MC.renderStateRuntime(section,result);
  window.REM?.renderResult(section,result,template);
  window.SI?.renderResult(section,result,template);
  MC.renderTargetConditions(section,result,template);window.AE?.renderResult(section,result);return section;
};
MC.renderTargetConditions=(box,result,template)=>{
  const rows=result.target_condition_trace||[],sources=result.fixed_effect_sources||[];
  if(!rows.length&&!sources.length)return;
  const details=UI.el("details"),table=UI.el("table"),head=UI.el("tr");
  details.append(UI.el("summary",`逐击目标条件（${rows.length} 条，只读）`),
    UI.el("p","采用每击附着前的独立目标样本；与角色面板快照分开。人工覆盖不算作自动成立，改动动作或附着后需重新计算。"));
  ["秒数／检查窗口","来源／命中","命中前目标","自动条件","人工覆盖","最终采用"].forEach(v=>head.append(UI.el("th",v)));table.append(head);
  const hits=new Map((result.hits||[]).map(h=>[h.id,h]));
  for(const row of rows.slice(0,250)){
    const sample=row.target_sample,state=sample?.status==="known"?MC.elementalStateText(sample.state):"未知或不适用";
    const tr=UI.el("tr"),hit=hits.get(row.event_id),values=[`${row.t} 秒／${row.cycle}`,
      `${DS.fixedSource(row.fixed_source_id,sources)} · ${hit?DS.hit(hit,undefined,template):"事件见原始定位"}`,state,
      DS.targetTruth(row.automatic_value),row.override==="include"?"手工计入":row.override==="exclude"?"手工排除":"无",
      `${row.applied?"采用":"未采用"} · ${MC.label(row.reason)}${row.reported_damage===false?"（不计伤害报告）":""}`];
    values.forEach(v=>tr.append(UI.el("td",v)));table.append(tr);
  }
  details.append(table);if(rows.length>250)details.append(UI.el("p","仅展示前 250 条；完整记录可展开核对。"));
  details.append(UI.details("完整条件样本与事件定位",rows),UI.details("固定来源原始声明",sources));box.append(details);
};
MC.exportWorkbook=async()=>{
  RT.syncRevision();if(!PL.state.plan||!RS.isCurrent()||RS.state.pending){setStatus("请先计算当前原生方案，再导出活公式工作簿");return;}
  const r=await callApi("export_formula_workbook",UI.clone(PL.state.plan),RS.state.snapshot.name,RS.agentCandidateSessionId?.()??null);
  if(r.code){UI.error(r);return;}setStatus("活公式工作簿已导出；请查看工作簿内的支持范围和失效提示",{path:r.data.path});
};
MC.init=()=>{
  $("#btn-mechanics")?.addEventListener("click",()=>{MC.shown=!MC.shown;if(MC.shown&&window.RC){RC.shown=false;RC.render();}MC.render();});
  $("#btn-exp-formula")?.addEventListener("click",MC.exportWorkbook);
  return callApi("mechanics_capabilities").then(r=>{if(r.code){UI.error(r);return;}MC.caps=r.data;MC.render();});
};
