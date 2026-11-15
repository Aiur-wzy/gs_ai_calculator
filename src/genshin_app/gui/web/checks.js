"use strict";
// The editor persists declared inputs; all mechanics and checking stay in Python.
window.RC = {state:{request:0, result:null, inputJson:null}, caps:null, shown:false};
RC.status = value => ({not_checked:"未检查",invalid:"输入无效",pending:"待补充",failed:"失败",
  not_verified:"后续未验证",unproven:"尚未证明周期稳定",passed:"通过",confirmed:"已核实",assumed:"情景假设",
  triggered:"已触发",not_triggered:"本次未触发"}[value] || "未检查");
RC.eventName = value => ({energy_spawn:"生成元素微粒／晶球",energy_credit:"元素能量到账",
  energy_direct:"直接恢复能量",energy_spend:"消耗元素能量",cooldown_change:"改变技能冷却",
  action:"施放动作",mechanics_hit:"机制命中处理",mechanics_end:"动作结束处理",mechanics_snapshot_capture:"面板取样"}[value] || "待补充");
RC.model = () => window.PL?.state.plan?.rotation_model;
RC.currentJson = () => window.PL ? PL.currentInputJson() : JSON.stringify(RT.tpl());
RC.sourceEpoch = () => window.PL?.state.sourceEpoch ?? 0;
RC.owner = id => DS.character(id,RT.tpl(),PL.state.plan);
RC.options = () => (RT.tpl()?.characters || []).map(c=>({value:c.id,label:RC.owner(c.id)}));
RC.abilityName = ability => `${RC.owner(ability.character)} · ${RC.caps?.action_types?.find(a=>a.id===ability.action_type_id)?.name_zh || "动作"}${ability.variant?` · ${DS.prose(ability.variant)}`:""}${ability.cast_sequence?` · 连携第 ${ability.cast_sequence.stage}/${ability.cast_sequence.max_stages} 段`:""}`;
RC.invalidate = () => {RC.state.request++;RC.state.result=null;RC.state.inputJson=null;};
RC.touch = () => {RC.invalidate();RT.syncRevision();PL.renderStatus();RC.render();RT.renderTimeline?.();};
RC.ensurePlan = async () => {
  if(!RT.tpl()){setStatus("请先新建或加载方案");return false;}
  if(PL.state.draft){setStatus("请先应用或放弃装备草稿，再编辑动作模型");return false;}
  if(PL.state.plan)return true;
  const json=RC.currentJson(),epoch=RC.sourceEpoch(),r=await callApi("new_plan",UI.clone(RT.tpl()));
  if(json!==RC.currentJson()||epoch!==RC.sourceEpoch()||PL.state.draft){setStatus("方案已变化，请重试");return false;}
  if(r.code){UI.error(r);return false;}
  PL.replace(r.data.plan||r.data);return true;
};
RC.setModel = async model => {
  if(!RC.syncSwitches(model,true,false))return false;
  if(!await RC.ensurePlan())return false;
  RC.syncSwitches(model);
  PL.state.plan.plan_version=Math.max(2,PL.state.plan.plan_version);PL.state.plan.rotation_model=UI.clone(model);RC.touch();return true;
};
RC.derivedSwitches = model => {
  if(!model)return [];
  const chars=new Set((RT.tpl()?.characters||[]).map(c=>c.id)),switches=[];
  if(chars.has(model.initial_frontline))switches.push({t:0,character:model.initial_frontline});
  const abilities=new Map((model.abilities||[]).map(a=>[a.id,a]));
  (model.actions||[]).filter(a=>abilities.get(a.ability_id)?.action_type_id==='action.switch'
    &&Number.isFinite(a.start)&&a.start>=0&&Number.isInteger(a.order)&&chars.has(a.switch_to)
    &&(a.repeat?.mode==='each_cycle'||a.repeat?.mode==='once'||a.repeat?.mode==='cycles'&&a.repeat.cycles?.includes(0)))
    .sort((a,b)=>a.start-b.start||a.order-b.order).forEach(a=>switches.push({t:a.start,character:a.switch_to}));
  return switches;
};
RC.syncSwitches = (model=RC.model(),confirmChange=false,apply=true) => {
  if(!model||!RT.tpl())return true;
  const proposed=RC.derivedSwitches(model),current=RT.tpl().switches||[],first=RT.tpl().characters[0]?.id;
  const normalize=rows=>{const sorted=UI.clone(rows).sort((a,b)=>a.t-b.t);
    while(sorted[0]?.t===0&&sorted[0].character===first)sorted.shift();return JSON.stringify(sorted);};
  if(confirmChange&&normalize(proposed)!==normalize(current)
    &&!window.confirm('动作模型中的初始前台和切人动作将更新当前伤害模板的切人轨。是否采用这份动作模型？取消会保留原输入。'))return false;
  if(apply)RT.tpl().switches=proposed;return true;
};
RC.create = async () => {
  if(!RT.tpl()?.characters?.length){setStatus("请先在方案中添加角色");return;}
  const chars=RT.tpl().characters;
  await RC.setModel({schema_version:1,period:RT.tpl().loop_duration||20,max_cycles:3,
    initial_frontline:chars[0]?.id,characters:chars.map(c=>({id:c.id,element:null,
      energy_recharge:c.stats.energy_recharge??null,energy_capacity:null,initial_energy:null,
      status:"pending",source:"待核实角色元素、能量上限和初始能量"})),
    abilities:[],actions:[],events:[],initial_cooldowns:[],action_hit_links:[],
    energy_recharge_windows:[],gap_explanations:[],assumptions:[]});
};
RC.importModel = async () => {
  const path=window.prompt("动作与能量模型文件路径（模型或本项目检查／原生方案文件）");if(!path)return;
  const json=RC.currentJson(),epoch=RC.sourceEpoch(),request=++RC.state.request;
  const r=await callApi("load_rotation_model",path);
  if(request!==RC.state.request||json!==RC.currentJson()||epoch!==RC.sourceEpoch())return;
  if(r.code){UI.error(r);return;}
  if(RC.model()&&!window.confirm("用文件中的动作与能量模型替换当前模型？装备和伤害事件保留，初始前台与切人轨差异会另行确认。"))return;
  if(await RC.setModel(r.data.rotation_model))setStatus("已导入动作与能量模型，请核对角色和伤害事件引用后检查");
};
RC.run = async () => {
  if(!RT.tpl())return;
  if(PL.state.draft){setStatus("请先应用或放弃装备草稿再检查");return;}
  const request=++RC.state.request,json=RC.currentJson(),epoch=RC.sourceEpoch();
  const r=await callApi("check_rotation",UI.clone(PL.state.plan||RT.tpl()));
  if(request!==RC.state.request||json!==RC.currentJson()||epoch!==RC.sourceEpoch())return;
  if(r.code){UI.error(r);return;}
  RC.state.result=r.data;RC.state.inputJson=json;RC.state.sourceEpoch=epoch;RC.render();setStatus(`动作与能量检查：${RC.status(r.data.status)}`);
};
RC.number = (parent,label,row,key,readonly=false) => {
  const input=UI.input(row[key],v=>{if(!readonly)row[key]=v;},true);input.disabled=readonly;
  if(readonly)input.placeholder="由数据库来源填写";parent.append(UI.field(label,input));
};
RC.text = (parent,label,row,key) => parent.append(UI.field(label,UI.input(row[key],v=>{row[key]=v;})));
RC.select = (parent,label,row,key,options) => parent.append(UI.field(label,UI.select(options,row[key],v=>{row[key]=v;})));
RC.evidence = (parent,row) => {
  RC.select(parent,"证据状态",row,"status",["pending","confirmed","assumed"].map(v=>({value:v,label:RC.status(v)})));
  parent.append(UI.field("来源、版本及假设",UI.area(row.source,v=>{row.source=v;})));
};
RC.repeat = (parent,row) => {
  row.repeat ||= {mode:"each_cycle"};
  RC.select(parent,"重复方式",row.repeat,"mode",[{value:"each_cycle",label:"每轮重复"},{value:"once",label:"只执行一次"},{value:"cycles",label:"指定轮次"}]);
  parent.append(UI.field("指定轮次（从 1 开始，逗号分隔）",UI.input((row.repeat.cycles||[]).map(v=>v+1).join(","),v=>{
    row.repeat.cycles=v.split(/[,，]/).filter(x=>x.trim()).map(x=>Number(x.trim())-1);
  })));
};
RC.edit = (title,original,draw,commit) => {
  if(PL.state.draft){setStatus("请先处理装备草稿");return;}
  const baseline=RC.currentJson(), row=UI.clone(original);let dialog=null;
  dialog=window.AppDialogKernel?.create?.({title,className:"rotation-check-editor",
    onRequestClose:()=>{dialog._rcClosed=true;return true;}});
  if(!dialog){setStatus("当前还有一项需要先处理，请关闭后再编辑动作与能量");return null;}
  const close=reason=>{dialog._rcClosed=true;return dialog.finish(reason||"cancel");};
  const form=UI.el("div",null,"eq-form-grid");draw(form,row);dialog.appBody.append(form);
  const apply=UI.button("应用修改",()=>{if(baseline!==RC.currentJson()){setStatus("方案已变化，请重新打开编辑");return;}
    if(commit(row)===false)return;close("applied");RC.touch();}),cancel=UI.button("放弃并关闭",()=>close("cancel"));
  dialog.setActions(apply,cancel);dialog.focusInitial(cancel);return dialog;
};
RC.editAbility = index => {
  const model=RC.model(),old=model.abilities[index]||{id:UI.id("ability"),character:RT.tpl().characters[0]?.id,
    action_type_id:"action.elemental_skill",variant:"新动作",cooldown:null,energy:null,rules:[],status:"pending",source:"待补充"};
  const resourceBound=!!window.AR?.boundAbility(old.id),capacityBound=!!window.CAP?.bound(old.id);
  const plan=PL.state.plan,baseline=RC.currentJson(),sourceEpoch=RC.sourceEpoch();
  const editorToken=RC.state.abilityEditorRequest=(RC.state.abilityEditorRequest||0)+1;
  let abilityDialog=null;
  let initialReady=UI.clone(model.initial_cooldowns?.find(c=>c.character===old.character&&c.group===old.cooldown?.group)?.ready_in??null),initialEdited=false;
  abilityDialog=RC.edit("技能／动作定义",old,(form,row)=>{
    const profileBox=UI.el("div"),profileOutput=UI.el("div");
    let profile=null,profileEnvelope=null,selectedStage="",profileRequest=0,profileSelection=null;
    const selectionKey=()=>{const ref=plan?.equipment_inputs?.[row.character]?.character_ref;
      return JSON.stringify([row.character,row.action_type_id,ref?.db_character_id??null,ref?.version_line??null]);};
    const current=()=>RC.state.abilityEditorRequest===editorToken&&!abilityDialog?._rcClosed&&PL.state.plan===plan&&
      RC.currentJson()===baseline&&RC.sourceEpoch()===sourceEpoch&&!PL.state.draft;
    const clearProfile=()=>{profileRequest++;profile=null;profileEnvelope=null;selectedStage="";profileSelection=null;drawProfile();};
    const sequenceProblem=(sequence,policy=profile?.resource_policy)=>{
      if(!sequence)return null;
      if(sequence.stage===1){
        if(!row.cooldown)return "首段尚未声明普通冷却；引擎会拒绝该连携组，系统不会替你补冷却。";
        if(row.cooldown.start!=="action_start")return "首段普通冷却不是从动作开始计时；sequence_start 连携会被引擎拒绝。";
        if(policy&&row.cooldown.duration!==policy.sequence_cooldown_seconds)return `首段冷却时间必须与受审资料一致：应为 ${policy.sequence_cooldown_seconds} 秒，当前为 ${row.cooldown.duration??"未填写"} 秒。`;
        if(policy&&row.cooldown.charges!==policy.sequence_cooldown_charges)return `首段可用次数必须与受审资料一致：应为 ${policy.sequence_cooldown_charges}，当前为 ${row.cooldown.charges??"未填写"}。`;
        if(policy&&row.cooldown.recovery!==policy.sequence_cooldown_recovery)return `首段恢复方式必须与受审资料一致：应为 ${policy.sequence_cooldown_recovery}，当前为 ${row.cooldown.recovery??"未填写"}。`;
        return null;
      }
      if(row.cooldown!==null)return "续段仍声明了普通冷却；引擎会拒绝该连携组。请核实后自行移除，系统不会自动清空。";
      return null;
    };
    const sequenceSummary=sequence=>sequence?.stage===1?"首段在动作开始时进入普通冷却，同时开启下一段的最晚释放窗口。":
      sequence?.stage===sequence?.max_stages?"末段由续段窗口接纳，释放后结束本次连携；不会再次启动普通冷却。":
        sequence?"续段由上一段开启的窗口接纳，没有最短等待；成功释放后立即刷新下一段窗口。":null;
    const validProfile=(envelope,ref)=>{
      const value=envelope?.cast_sequence,common=value?.value,stages=value?.stages,policy=value?.resource_policy,snapshot=envelope?.snapshot;
      const fingerprint=v=>typeof v==="string"&&/^[0-9a-f]{64}$/.test(v),commit=v=>typeof v==="string"&&/^[0-9a-f]{40}$/.test(v);
      return envelope?.selection?.character_id===ref.db_character_id&&snapshot?.version_line===ref.version_line&&fingerprint(snapshot?.snapshot_id)&&
        commit(snapshot?.upstream_commit)&&fingerprint(envelope?.resource_fingerprint)&&fingerprint(value?.content_fingerprint)&&
        value?.contract==="cast-sequence-source-v1"&&value.status==="ready"&&typeof value.profile_id==="string"&&value.profile_id&&
        typeof common?.group==="string"&&common.group&&
        common.cooldown_start==="sequence_start"&&Number.isInteger(common.max_stages)&&common.max_stages>=2&&Array.isArray(stages)&&
        stages.length===common.max_stages&&stages.every((stage,i)=>stage?.stage===i+1&&Number.isInteger(stage.skill_id)&&
          (i===stages.length-1?stage.continuation_window===null:Number.isFinite(stage.continuation_window)&&stage.continuation_window>0))&&
        policy?.sequence_cooldown_owner_stage===1&&policy.continuations_bypass_active_cooldown===true&&
        Number.isFinite(policy.sequence_cooldown_seconds)&&policy.sequence_cooldown_seconds>0&&
        policy.sequence_cooldown_charges===1&&policy.sequence_cooldown_recovery==="sequential"&&
        policy.continuation_window_is_latest_deadline_not_minimum_wait===true&&
        policy.raw_followup_cdtime_is_not_an_independent_cast_cooldown===true&&
        policy.raw_followup_cdtime_is_not_a_minimum_stage_delay===true&&policy.terminal_stage_restarts_cooldown===false;
    };
    const chosenSequence=()=>{const stage=profile?.stages?.find(item=>item.stage===Number(selectedStage));if(!stage)return null;
      return {group:profile.value.group,stage:stage.stage,max_stages:profile.value.max_stages,
        continuation_window:stage.continuation_window,cooldown_start:profile.value.cooldown_start,
        source_binding:{contract:"cast-sequence-source-binding-v1",profile_id:profile.profile_id,
          character_id:profileEnvelope.selection.character_id,selected_skill_id:stage.skill_id,selected_stage:stage.stage,
          version_line:profileEnvelope.snapshot.version_line,snapshot_id:profileEnvelope.snapshot.snapshot_id,
          upstream_commit:profileEnvelope.snapshot.upstream_commit,content_fingerprint:profile.content_fingerprint,
          resource_fingerprint:profileEnvelope.resource_fingerprint,
          sequence_cooldown_seconds:profile.resource_policy.sequence_cooldown_seconds,
          sequence_cooldown_charges:profile.resource_policy.sequence_cooldown_charges,
          sequence_cooldown_recovery:profile.resource_policy.sequence_cooldown_recovery,
          verification_mode:"saved_metadata_only"}};};
    const drawProfile=()=>{
      profileBox.innerHTML="";profileBox.append(UI.el("h4","数据库受审连携资料"),
        UI.el("p","只从当前角色的数据库引用读取元素战技槽。系统不会从技能名、描述或原始 cdTime 猜测连携，也不会添加动作或调整时点。"));
      if(row.action_type_id!=="action.elemental_skill"){
        profileBox.append(UI.el("p","当前动作不是元素战技，未提供技能槽连携映射。"));return;
      }
      profileBox.append(UI.button("读取受审连携资料",readProfile),profileOutput);profileOutput.innerHTML="";
      if(!profile){profileOutput.append(UI.el("p","尚未读取；手工连携字段仍由引擎完整校验。"));return;}
      profile.stages.forEach(stage=>profileOutput.append(UI.el("p",`第 ${stage.stage}/${profile.value.max_stages} 段 · skill_id ${stage.skill_id} · ${stage.ability_name||"能力名待核对"} · ${stage.stage<profile.value.max_stages?`释放后 ${stage.continuation_window} 秒内可接下一段（可以立即接续）`:"末段，结束连携"} · 原始 cdTime ${stage.raw_cooldown_seconds} 仅作来源证据，不是续段等待`)));
      profileOutput.append(UI.field("应用的受审阶段",UI.select([{value:"",label:"请选择，系统不会自动采用"},
        ...profile.stages.map(stage=>({value:String(stage.stage),label:`第 ${stage.stage}/${profile.value.max_stages} 段`}))],selectedStage,v=>{selectedStage=v;drawProfile();})));
      const proposed=chosenSequence(),problem=sequenceProblem(proposed),summary=sequenceSummary(proposed);
      if(summary)profileOutput.append(UI.el("p",summary));if(problem)profileOutput.append(UI.el("p",problem,"eq-error"));
      profileOutput.append(UI.button("应用所选阶段到当前技能定义",()=>{
        if(!current()||selectionKey()!==profileSelection){setStatus("角色、方案或数据库状态已变化，请重新读取连携资料");return;}
        const value=chosenSequence();if(!value){setStatus("请先明确选择受审连携阶段");return;}
        const conflict=sequenceProblem(value);if(conflict){setStatus(conflict);drawProfile();return;}
        row.cast_sequence=UI.clone(value);drawSequence();drawProfile();
      }),UI.el("p","应用后保存来源身份和指纹供审计；当前动作检查不重新查询数据库，verification_mode 会明确记录为 saved_metadata_only。"),
      UI.details("受审连携来源、审核与字段策略",profileEnvelope));
    };
    const readProfile=async()=>{
      profileOutput.innerHTML="";
      const ref=plan?.equipment_inputs?.[row.character]?.character_ref;
      if(!ref||!Number.isInteger(ref.db_character_id)||!ref.version_line){profileOutput.append(UI.el("p","当前角色没有明确的数据库 character_ref，不能读取连携资料。","eq-error"));return;}
      const stamp=selectionKey(),token=++profileRequest;profile=null;profileEnvelope=null;selectedStage="";profileSelection=null;
      profileOutput.append(UI.el("p","正在读取当前数据库快照中的受审连携资料…"));let response;
      try{response=await callApi("query_runtime",{action:"action_resource_profile",character_id:ref.db_character_id,
        slot:"skill",version_line:ref.version_line});}catch(error){response={code:1,message:String(error)};}
      if(token!==profileRequest||!current()||selectionKey()!==stamp){setStatus("旧连携资料响应已忽略，请重新读取");return;}
      profileOutput.innerHTML="";
      if(response.code){profileOutput.append(UI.error(response));return;}
      if(response.data?.cast_sequence===undefined){profileOutput.append(UI.el("p","当前技能槽没有受审连携资料；不会从技能描述或 cdTime 自动推断。","eq-error"));return;}
      if(!validProfile(response.data,ref)){profileOutput.append(UI.el("p","连携资料契约、快照指纹或基础冷却策略不完整，未采用。","eq-error"));return;}
      profileEnvelope=UI.clone(response.data);profile=profileEnvelope.cast_sequence;profileSelection=stamp;drawProfile();
    };
    form.append(UI.field("角色",UI.select(RC.options(),row.character,v=>{row.character=v;clearProfile();})),
      UI.field("动作种类",UI.select((RC.caps?.action_types||[]).map(a=>({value:a.id,label:a.name_zh})),row.action_type_id,v=>{row.action_type_id=v;clearProfile();})));
    RC.text(form,"动作名称或变体",row,"variant");
    const cooldown=UI.el("div"),energy=UI.el("div"),sequence=UI.el("div");
    const drawCooldown=()=>{cooldown.innerHTML="";if(!row.cooldown)return;
      RC.text(cooldown,"共享冷却组",row.cooldown,"group");RC.number(cooldown,"冷却时间（秒）",row.cooldown,"duration",resourceBound);
      RC.number(cooldown,"最大可用次数",row.cooldown,"charges",resourceBound||capacityBound);
      if(capacityBound)cooldown.append(UI.el("p","最大次数由受审来源填写；冷却秒数、起点、恢复方式及开场队列是当前方案输入。"));
      cooldown.append(UI.field("冷却起点",UI.select([{value:"action_start",label:"动作开始"},{value:"occupation_end",label:"占用结束"}],row.cooldown.start,v=>{row.cooldown.start=v;drawSequence();drawProfile();})));
      RC.select(cooldown,"次数恢复方式",row.cooldown,"recovery",[{value:"sequential",label:"依次恢复"},{value:"parallel",label:"分别计时"}]);
      cooldown.append(UI.check("初始次数全部就绪",Array.isArray(initialReady)&&!initialReady.length,v=>{initialReady=v?[]:null;initialEdited=true;}),
        UI.field("初始未恢复次数的剩余秒数（逗号分隔）",UI.input(initialReady?.join(",")||"",v=>{initialReady=v.trim()?v.split(/[,，]/).map(Number):null;initialEdited=true;})));
    };
    const drawEnergy=()=>{energy.innerHTML="";if(!row.energy)return;RC.number(energy,"释放所需能量",row.energy,"required",resourceBound);RC.number(energy,"实际扣除能量",row.energy,"amount",resourceBound);};
    const drawSequence=()=>{sequence.innerHTML="";if(!row.cast_sequence)return;
      const bound=!!row.cast_sequence.source_binding;
      if(bound){sequence.append(UI.el("p",`连携组：${row.cast_sequence.group}；第 ${row.cast_sequence.stage}/${row.cast_sequence.max_stages} 段；续段窗口：${row.cast_sequence.continuation_window??"末段"}。`),
        UI.el("p","此定义带受审来源绑定，字段保持只读。来源仅为已保存审计元数据，动作检查不会自动 fresh 重验。"),
        UI.button("移除来源绑定并改为手工连携",()=>{delete row.cast_sequence.source_binding;drawSequence();}));
      }else{RC.text(sequence,"连携组",row.cast_sequence,"group");RC.number(sequence,"当前段数",row.cast_sequence,"stage");
        RC.number(sequence,"总段数",row.cast_sequence,"max_stages");RC.number(sequence,"续段窗口（秒）",row.cast_sequence,"continuation_window");}
      const problem=sequenceProblem(row.cast_sequence,row.cast_sequence.source_binding||undefined),summary=sequenceSummary(row.cast_sequence);
      sequence.append(UI.el("p","首段开始即进入普通冷却；后续段在各自的续段窗口内仍可释放，并在成功释放后刷新窗口。窗口是最晚期限，不是最短等待；动作占用结束后可以立即续段。"));
      if(summary)sequence.append(UI.el("p",summary));if(problem)sequence.append(UI.el("p",problem,"eq-error"));
      if(bound)sequence.append(UI.details("保存的连携来源绑定",row.cast_sequence.source_binding));};
    if(resourceBound)form.append(UI.el("p","冷却、次数及耗能由数据库来源填写；请在动作资源来源中预览或解绑。"),cooldown,energy);
    else form.append(UI.check("此动作有技能冷却",!!row.cooldown,v=>{row.cooldown=v?{group:row.id,duration:null,start:"action_start",charges:1,recovery:"sequential"}:null;drawCooldown();drawSequence();drawProfile();}),cooldown,
      UI.check("此动作需要元素能量",!!row.energy,v=>{row.energy=v?{required:null,amount:null}:null;drawEnergy();}),energy);
    form.append(UI.check("此动作属于多段连携",!!row.cast_sequence,v=>{row.cast_sequence=v?{group:row.cooldown?.group||row.id,stage:1,max_stages:2,continuation_window:null,cooldown_start:"sequence_start"}:null;drawSequence();}),sequence);
    drawCooldown();drawEnergy();drawSequence();drawProfile();form.append(profileBox);
    RC.evidence(form,row);if(row.rules?.length)form.append(DS.raw("已声明的触发规则（由模型文件维护）",row.rules));
  },row=>{
    const sameGroup=row.character===old.character&&row.cooldown?.group===old.cooldown?.group;
    if(row.cooldown&&(initialEdited||sameGroup)){
      model.initial_cooldowns=(model.initial_cooldowns||[]).filter(c=>!(c.character===row.character&&c.group===row.cooldown.group));
      if(initialReady!==null)model.initial_cooldowns.push({character:row.character,group:row.cooldown.group,ready_in:initialReady});}
    if(!row.cast_sequence)delete row.cast_sequence;
    if(index===undefined)model.abilities.push(row);else model.abilities[index]=row;
    RC.pruneCooldowns(model);RC.syncSwitches();});
};
RC.pruneCooldowns = model => {model.initial_cooldowns=(model.initial_cooldowns||[]).filter(c=>model.abilities.some(a=>a.character===c.character&&a.cooldown?.group===c.group));};
RC.editAction = index => {
  const model=RC.model();if(!model.abilities.length){setStatus("请先定义至少一种技能或动作");return;}
  const old=model.actions[index]||{id:UI.id("action"),ability_id:model.abilities[0].id,start:null,order:0,occupation_end:null,
    repeat:{mode:"each_cycle"},rule_outcomes:[],status:"pending",source:"待核对动作时长与触发"};
  RC.edit("施放动作",old,(form,row)=>{
    RC.select(form,"技能／动作",row,"ability_id",model.abilities.map(a=>({value:a.id,label:RC.abilityName(a)})));
    RC.number(form,"开始（秒）",row,"start");RC.number(form,"占用结束（秒）",row,"occupation_end");RC.number(form,"同一时刻的先后序号",row,"order");
    RC.select(form,"切换目标（仅切人动作）",row,"switch_to",[{value:"",label:"不切人"},...RC.options()]);
    RC.repeat(form,row);RC.evidence(form,row);
    if(row.rule_outcomes?.length)form.append(DS.raw("本次触发结论（由模型文件维护）",row.rule_outcomes));
  },row=>{if(!row.switch_to)delete row.switch_to;if(row.repeat.mode!=="cycles")delete row.repeat.cycles;
    if(index===undefined)model.actions.push(row);else model.actions[index]=row;RC.syncSwitches();});
};
RC.editEvent = (index,type="energy_spawn") => {
  const model=RC.model();
  const spendActions=model.actions.filter(a=>!window.AR?.autoAction(a.id));
  if(index===undefined&&type==="energy_spend"&&model.actions.length&&!spendActions.length){setStatus("所有已有动作均使用自动扣能；请先切回既有事件模式，系统回执不能作为输入事件。");return;}
  if(index===undefined&&type==="energy_credit"&&!model.events.some(e=>e.type==="energy_spawn")){setStatus("请先声明产生粒球的事件");return;}
  const defaults={energy_spawn:{particle_type:"particle",source_kind:"other",element:null,count:null,lost_count:null},
    energy_credit:{spawn_id:model.events.find(e=>e.type==="energy_spawn")?.id,count:null},
    energy_direct:{amounts:{}},energy_spend:{character:model.abilities.find(a=>a.id===spendActions[0]?.ability_id)?.character||RT.tpl().characters[0]?.id,action_id:spendActions[0]?.id,required:null,amount:null},
    cooldown_change:{character:RT.tpl().characters[0]?.id,group:"待补充",operation:"reset"}};
  const old=model.events[index]||{id:UI.id("energy"),type,order:1,...defaults[type],
    repeat:{mode:"each_cycle"},status:"pending",source:"待核对机制与时刻",...(type==="energy_credit"?{credit_at:null}:{t:null})};
  RC.edit(RC.eventName(old.type),old,(form,row)=>{
    RC.number(form,row.type==="energy_credit"?"实际到账时刻（秒）":"事件时刻（秒）",row,row.type==="energy_credit"?"credit_at":"t");
    RC.number(form,"同刻先后序号",row,"order");
    RC.select(form,"关联动作",row,"action_id",[{value:"",label:"无关联动作（怪物事件等）"},...(row.type==="energy_spend"?spendActions:model.actions).map(a=>({value:a.id,label:`${a.start} 秒 · ${RC.abilityName(model.abilities.find(b=>b.id===a.ability_id)||{})}`}))]);
    if(row.type==="energy_spawn"){
      RC.select(form,"粒球类型",row,"particle_type",[{value:"particle",label:"微粒"},{value:"orb",label:"晶球"}]);
      RC.select(form,"元素",row,"element",[...RC.elements(),{value:"clear",label:"无元素"}]);
      RC.number(form,"生成粒球数量",row,"count");RC.number(form,"明确丢失数量",row,"lost_count");
      RC.select(form,"数量口径",row,"count_mode",[{value:"",label:"按数量识别（小数为平均产球）"},{value:"fixed",label:"固定粒数"},{value:"average",label:"平均产球"}]);
      RC.select(form,"产生来源",row,"source_kind",[{value:"skill",label:"技能"},{value:"weapon",label:"武器"},{value:"enemy",label:"怪物"},{value:"other",label:"其他已声明来源"}]);
    }else if(row.type==="energy_credit"){
      RC.select(form,"来源粒球组",row,"spawn_id",model.events.filter(e=>e.type==="energy_spawn").map(e=>({value:e.id,label:`${e.t} 秒 · ${DS.label("elements",e.element)} · ${e.count??"?"} 个`})));RC.number(form,"到账粒球数量",row,"count");
    }else if(row.type==="energy_direct"){
      row.amounts ||= {};for(const c of RT.tpl().characters)RC.number(form,`${RC.owner(c.id)}直接回能（不乘充能效率）`,row.amounts,c.id);
    }else if(row.type==="energy_spend"){
      const resourceBound=!!window.AR?.boundSpend(old);
      RC.select(form,"角色",row,"character",RC.options());RC.number(form,"要求能量",row,"required",resourceBound);RC.number(form,"扣除能量",row,"amount",resourceBound);
      if(resourceBound)form.append(UI.el("p","此扣能事件的数字由动作资源来源填写，时间与顺序仍由本事件声明。"));
      else if(window.AR)form.append(UI.el("p","选择已绑定资源来源的动作时，要求和扣除数值由来源提供；自动扣能动作不能添加手工事件。"));
    }else{
      RC.select(form,"角色",row,"character",RC.options());RC.text(form,"冷却组",row,"group");
      RC.select(form,"操作",row,"operation",[{value:"reset",label:"重置全部次数"},{value:"reduce",label:"缩短冷却"},{value:"restore",label:"恢复指定次数"}]);RC.number(form,"秒数或次数",row,"amount");
    }
    RC.repeat(form,row);RC.evidence(form,row);
  },row=>{if(!row.action_id)delete row.action_id;if(row.element==="")row.element=null;if(row.repeat.mode!=="cycles")delete row.repeat.cycles;
    if(row.type==="energy_spawn"&&!row.count_mode)delete row.count_mode;
    if(row.type==="energy_spend"&&window.AR?.autoAction(row.action_id)){setStatus("该动作使用自动扣能，不能同时交付手工扣能事件；请先切回既有事件模式。");return false;}
    if(row.type==="energy_spend"&&window.AR?.boundSpend(row)){delete row.required;delete row.amount;}
    if(row.type==="cooldown_change"&&row.operation==="reset")delete row.amount;
    if(index===undefined)model.events.push(row);else model.events[index]=row;});
};
RC.elements = () => [{value:"",label:"待补充"},...["pyro","hydro","cryo","electro","anemo","geo","dendro"].map(value=>({value,label:DS.label("elements",value)}))];
RC.editCharacter = index => {
  const model=RC.model();RC.edit(`${RC.owner(model.characters[index].id)} · 能量初态`,model.characters[index],(form,row)=>{
    RC.select(form,"元素",row,"element",RC.elements());RC.number(form,"能量上限",row,"energy_capacity");RC.number(form,"首次起点能量",row,"initial_energy");
    form.append(UI.el("p","充能效率由本次装备合成面板提供；具体采用值显示在检查回执中。","hint"));RC.evidence(form,row);
  },row=>{if(!row.element)row.element=null;model.characters[index]=row;});
};
RC.commandMenu=()=>window.AppContextMenu?.forDocument?.(document,window)||null;
RC.openCommandMenu=(target,label,items)=>{
  const menu=RC.commandMenu();if(menu)return menu.open({target,trigger:target,returnFocus:target,label,items,onError:error=>setStatus(error?.message||"操作未完成")});
  const first=items.find(item=>item?.type!=="separator"&&item.enabled!==false&&typeof item.run==="function");return first?.run?.();
};
RC.bindCommandMenu=(target,label,items)=>RC.commandMenu()?.bind(target,{label,items,onError:error=>setStatus(error?.message||"操作未完成")});
RC.removeDefinition=(items,index)=>{if(!window.confirm("删除这条定义？有关联引用时，后续检查会要求修复引用。"))return false;
  items.splice(index,1);if(items===RC.model()?.abilities)RC.pruneCooldowns(RC.model());RC.syncSwitches();RC.touch();return true;};
RC.definitionMenuItems=(title,items,index,edit)=>[
  {id:`edit-${title}-${index}`,label:"编辑",run:()=>edit(index)},
  {type:"separator"},
  {id:`remove-${title}-${index}`,label:"删除",danger:true,run:()=>RC.removeDefinition(items,index)},
];
RC.list = (title,items,label,edit,add) => {
  const box=UI.el("details",null,"result-block");box.append(UI.el("summary",`${title}（${items.length}）`));
  if(add)box.append(UI.button(`添加${title}`,add));
  items.forEach((row,index)=>{const line=UI.el("div",null,"eq-toolbar rc-definition-row"),more=UI.button("⋯",()=>RC.openCommandMenu(more,`${title}操作`,RC.definitionMenuItems(title,items,index,edit)),"rc-row-menu");line.tabIndex=0;
    line.append(UI.el("span",label(row,index)),more);RC.bindCommandMenu(line,`${title}操作`,()=>RC.definitionMenuItems(title,items,index,edit));box.append(line);});
  return box;
};
RC.render = () => {
  const box=$("#rotation-checks");if(!box)return;box.innerHTML="";
  box.classList.toggle("hidden",!RC.shown||!!window.EQ?.showing);
  if(!RC.shown||window.EQ?.showing)return;
  const bar=UI.el("div",null,"eq-toolbar"),more=UI.el("details",null,"rc-toolbar-more");more.append(UI.el("summary","更多"),UI.button("导入动作模型",RC.importModel));
  bar.append(UI.button("← 返回时间轴",()=>{RC.shown=false;RC.render();}),UI.el("h3","动作与能量循环"),UI.button("检查动作与能量",RC.run),more);box.append(bar);
  window.XD?.render(box);
  const model=RC.model();
  if(!model){box.append(UI.el("p","当前方案尚未提供动作与能量模型。伤害事件不等于施放动作；计算成功不能证明持续循环。","hint"),UI.button("建立动作与能量模型",RC.create));return;}
  box.append(UI.el("p",PL.state.plan?.plan_version===3?
    "动作、机制与能量共同执行。命中关联和条件触发在机制面板中声明；元素层限于已接受的参考模型与范围，体力尚未模拟。":
    "先定义技能，再排列施放动作和能量事件。所有空值均待补充；怪物粒球须明确来源。检查独立于伤害计算，元素底与体力尚未模拟。","hint"));
  const settings=UI.el("div",null,"eq-toolbar");
  settings.append(UI.field("单轮时长（秒）",UI.input(model.period,v=>{model.period=v;RT.tpl().loop_duration=v;RC.syncSwitches();RC.touch();},true)),
    UI.field("检查轮数（3–20）",UI.input(model.max_cycles??3,v=>{model.max_cycles=v;RC.touch();},true)),
    UI.field("初始前台",UI.select(RC.options(),model.initial_frontline,v=>{model.initial_frontline=v;RC.syncSwitches();RC.touch();})));
  box.append(settings);
  const party=UI.el("div",null,"eq-toolbar");model.characters.forEach((c,i)=>party.append(UI.button(`${RC.owner(c.id)} · 初始 ${c.initial_energy??"待补"} / ${c.energy_capacity??"待补"}`,()=>RC.editCharacter(i))));box.append(party,RC.actionTrack(model));
  box.append(RC.list("技能定义",model.abilities,a=>RC.abilityName(a),RC.editAbility,()=>RC.editAbility()));
  window.AR?.render(box);
  window.CAP?.render(box);
  window.AA?.render?.(box);
  window.EG?.render(box);
  window.LHB?.render(box);
  window.MS?.render(box);
  box.append(RC.list("施放动作",model.actions,a=>`${a.start??"?"}–${a.occupation_end??"?"} 秒 · ${RC.abilityName(model.abilities.find(b=>b.id===a.ability_id)||{})}`,RC.editAction,()=>RC.editAction()));
  const eventBox=RC.list("能量事件",model.events,e=>`${e.credit_at??e.t??"?"} 秒 · ${RC.eventName(e.type)}`,RC.editEvent),eventAdd=UI.el("details",null,"rc-event-add");eventAdd.append(UI.el("summary","添加能量事件"));
  for(const type of (RC.caps?.event_types||[]))eventAdd.append(UI.button(`添加${RC.eventName(type)}`,()=>RC.editEvent(undefined,type)));eventBox.append(eventAdd);box.append(eventBox);
  const links=UI.el("details",null,"result-block");links.append(UI.el("summary","伤害与动作关联"));
  for(const [i,h] of (RT.tpl()?.hits||[]).entries()){
    const link=model.action_hit_links?.find(l=>l.hit_id===h.id);
    links.append(UI.field(`${h.t} 秒 · ${DS.hit(h,i,RT.tpl())}`,UI.select([{value:"",label:"尚未关联"},...model.actions.map(a=>({value:a.id,label:`${a.start} 秒 · ${RC.abilityName(model.abilities.find(b=>b.id===a.ability_id)||{})}`}))],link?.action_id||"",v=>{
      model.action_hit_links=(model.action_hit_links||[]).filter(l=>l.hit_id!==h.id);if(v)model.action_hit_links.push({action_id:v,hit_id:h.id});RC.touch();
    })));
  }box.append(links);
  if(RC.state.inputJson===RC.currentJson()&&RC.state.sourceEpoch===RC.sourceEpoch()&&RC.state.result)box.append(RC.renderResult(RC.state.result,RT.tpl()));
  box.append(DS.raw("完整动作与能量模型（复杂触发、跨轮延迟等由 Agent 文件维护）",model));
};
RC.renderResult = (check,template) => {
  const box=UI.el("div",null,"result-block");box.append(UI.el("h3",`动作与能量检查：${RC.status(check?.status||"not_checked")}`));
  const interpretation=check?.damage_interpretation;
  box.append(UI.el("p",(typeof interpretation==="string"?interpretation:interpretation?.message)||"当前结果未验证动作、冷却与能量循环。","hint"));if(!check)return box;
  const particleQuantity=check.energy_check?.particle_quantity;
  if(particleQuantity?.message)box.append(UI.el("p",particleQuantity.message,"hint"));
  const reactionVerification=interpretation?.reaction_verification;
  if(reactionVerification?.mode==="declared_not_simulated"){
    const warning=UI.el("div",null,"result-block warning");
    warning.append(
      UI.el("h4","反应尚未由元素引擎复核"),
      UI.el("p",`${reactionVerification.declared_reaction_hit_count??0} 个反应命中来自输入声明。当前结果没有验证命中前元素底、具体攻击的附着资格、ICD 计数身份或反应顺序。`),
      UI.el("p",reactionVerification.required_follow_up||"请补齐具体攻击组件的附着资料并重新计算。"),
      DS.raw("待复核反应与检查范围",reactionVerification));
    box.append(warning);
  }
  const detail=(title,rows,render)=>{if(!rows?.length)return;const part=UI.el("details");part.append(UI.el("summary",`${title}（${rows.length}）`));rows.forEach(r=>part.append(render(r)));box.append(part);};
  const automatic=check.automatic_energy_delivery;
  if(automatic){
    box.append(UI.el("h4","系统扣能交付"),UI.el("p",`已报告 ${automatic.planned_actions??"待核实"} 个动作：实际提交 ${automatic.committed_actions??"待核实"}，未提交 ${automatic.not_committed_actions??"待核实"}，后续未验证 ${automatic.not_verified_actions??"待核实"}。`),
      UI.el("p",`配置检查范围计划 ${automatic.max_cycles_planned_actions??"待核实"} 个动作，其中未报告 ${automatic.unreported_actions??"待核实"} 个。只统计已有动作；不表示额外轮次或伤害已执行。`),
      DS.raw("自动扣能协议、范围与预算",automatic));
  }
  detail("充能效率采用值",check.panel_bindings,r=>UI.el("p",`${DS.character(r.character,template)}：${r.value==null?"待补充":(r.value*100).toFixed(1)+"%"}`));
  detail("需要处理的问题",check.issues,r=>{const p=UI.el("div");p.append(DS.textBlock(r.message||"检查输入与来源"),DS.raw("定位与完整原因",r));return p;});
  detail("未检查的机制",check.unchecked_constraints,r=>DS.textBlock(typeof r==="string"?r:JSON.stringify(r)));
  detail("循环边界",check.cycles,r=>{const p=UI.el("div");p.append(UI.el("p",`第 ${r.cycle+1} 轮：${RC.status(r.status)}`));
    for(const [cid,end] of Object.entries(r.end_energy||{}))p.append(UI.el("p",`${DS.character(cid,template)}：${RC.fmt(r.start_energy?.[cid])} → ${RC.fmt(end)}`));
    p.append(DS.raw("本轮完整状态",r));return p;});
  detail("事件与能量流水",check.event_log,r=>{const p=UI.el("div"),automatic=r.delivery==="automatic_action_start";
    p.append(UI.el("p",`${r.t??r.time??""} 秒 · ${automatic?"系统扣能（动作接纳）":RC.eventName(r.type||r.kind)} · ${RC.status(r.status)}`));
    if(r.count_mode==="average")p.append(UI.el("p",`平均粒球数量 ${r.count}；剩余在途 ${r.remaining_count??"待核实"}。`));
    if(r.cast_sequence){const s=r.cast_sequence,base=s.base_cooldown;
      const admitted=r.status==="failed"?"续段未接纳":s.admission==="sequence_start"?"开启连携":s.admission==="continuation_window"?"通过续段窗口接纳":"连携状态待核实";
      p.append(UI.el("p",`连携第 ${s.stage}/${s.max_stages} 段：${admitted}${s.completed?"；连携已完成":s.next_stage?`；下一段为第 ${s.next_stage} 段，须在 ${RC.fmt(s.continuation_expires_at)} 秒前开始`:""}。`));
      if(base)p.append(UI.el("p",`首段普通冷却${base.running?"正在运行":"未运行"}${base.ready_at==null?"":`，${RC.fmt(base.ready_at)} 秒恢复`}；普通冷却不阻止有效续段。`));
      if(s.expected_stage)p.append(UI.el("p",`当前连携状态要求第 ${s.expected_stage} 段${s.expires_at==null?"":`，窗口截止 ${RC.fmt(s.expires_at)} 秒`}。`));}
    for(const e of r.energy||[])p.append(UI.el("p",`${DS.character(e.character,template)}：${RC.fmt(e.before)} + ${RC.fmt(e.gain)} → ${RC.fmt(e.after)}；溢出 ${RC.fmt(e.overflow)}；${e.on_field?"前台":"后台"}`));
    if(r.type==="energy_spend"&&r.before!=null)p.append(UI.el("p",`${DS.character(r.character,template)}：${r.committed_at_action_start?"动作提交前":"扣能前"} ${RC.fmt(r.before)}，要求 ${RC.fmt(r.required)}，扣除 ${RC.fmt(r.amount)}，缺口 ${RC.fmt(r.deficit)}，${r.committed_at_action_start?"提交瞬间":"扣能后"} ${RC.fmt(r.after)}`));
    if(r.committed_at_action_start)p.append(UI.el("p","此处是动作扣能瞬间的历史记录，不是后续退款后的当前能量；不能作为可编辑输入事件。"));
    if(automatic)p.append(UI.el("p",`关联动作 ${r.action_id??"待核实"}；父执行 ${r.parent_run_id??"待核实"}。系统回执不占用同刻顺序。`));
    if(r.energy_delivery){const status={committed:"已提交",not_committed:"未提交",not_verified:"后续未验证"}[r.energy_delivery.status]||"待核实";
      const reason=r.energy_delivery.reason,reasonText=check.issues?.find(issue=>issue.code===reason)?.message||
        {not_frontline:"施放角色不在前台",action_overlap:"动作占用冲突或尚不能切出",insufficient_energy:"动作开始时能量不足",cooldown_unavailable:"技能冷却或可用次数尚未恢复",cast_sequence_unavailable:"续段窗口不存在或已经过期",cast_sequence_stage:"连携段数与当前状态不一致",automatic_energy_receipt_limit:"自动扣能审计达到上限，当前动作未提交"}[reason]||"请查看完整原因";
      p.append(UI.el("p",`自动扣能：${status}；计划回执 ${r.energy_delivery.receipt_id??"待核实"}${reason?`；原因：${reasonText}`:""}。`));}
    p.append(DS.raw("完整事件与来源",r));return p;});
  detail("空档说明",check.gaps,r=>DS.raw("空档与说明",r));
  return box;
};
RC.fmt = v => v==null?"待补":String(Math.round(v*1000)/1000);
RC.actionTrack = model => {
  const wrap=UI.el("div",null,"action-track"),svg=document.createElementNS("http://www.w3.org/2000/svg","svg");
  const characters=RT.tpl().characters,period=model.period||20;
  svg.setAttribute("viewBox",`0 0 900 ${40+characters.length*42}`);svg.setAttribute("role","img");svg.setAttribute("aria-label","已声明施放动作与占用时间轴");
  const add=(tag,attrs,text)=>{const n=document.createElementNS("http://www.w3.org/2000/svg",tag);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,v);if(text!=null)n.textContent=text;svg.append(n);return n;};
  for(let i=0;i<=5;i++)add("text",{x:150+i*140,y:16,fill:"var(--color-axis-label-muted)","font-size":12},`${RC.fmt(period*i/5)} 秒`);
  characters.forEach((c,i)=>{const y=35+i*42;add("text",{x:4,y:y+17,fill:"var(--color-axis-label)","font-size":13},RC.owner(c.id));add("line",{x1:150,y1:y+12,x2:850,y2:y+12,stroke:"var(--color-axis-grid)"});
    model.actions.forEach((a,index)=>{const ability=model.abilities.find(b=>b.id===a.ability_id);if(ability?.character!==c.id||a.start==null||a.occupation_end==null)return;
      const node=add("rect",{x:150+700*a.start/period,y,width:Math.max(3,700*(a.occupation_end-a.start)/period),height:24,rx:3,fill:RT.charColor(c.id),tabindex:0,role:"button","aria-label":`${RC.abilityName(ability)} ${a.start} 至 ${a.occupation_end} 秒，编辑动作`});
      const title=document.createElementNS("http://www.w3.org/2000/svg","title");title.textContent=`${RC.abilityName(ability)} · ${a.start}–${a.occupation_end} 秒`;node.append(title);
      node.addEventListener("click",()=>RC.editAction(index));node.addEventListener("keydown",e=>{if(e.key==="Enter")RC.editAction(index);});
    });
  });wrap.append(svg);return wrap;
};
RC.init = async () => {const r=await callApi("rotation_capabilities");if(!r.code)RC.caps=r.data;
  $("#btn-rotation-checks")?.addEventListener("click",()=>{if(window.EQ?.showing){setStatus("请先从装备页返回");return;}RC.shown=!RC.shown;if(RC.shown&&window.MC){MC.shown=false;MC.render();}RC.render();});RC.render();};
