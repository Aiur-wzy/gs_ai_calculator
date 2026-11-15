"use strict";
// Native declarations and fresh previews only. Python owns projection and dispatch.
window.PA={kind:"calibrated_multi_contact_v1"};
PA.caps=()=>PL.state.caps?.periodic_attack_contract;
PA.commandMenu=()=>window.AppContextMenu?.forDocument?.(document,window)||null;
PA.menuError=error=>setStatus(`操作未完成：${String(error?.message||error||"未知错误")}`);
PA.openCommandMenu=(target,label,items)=>{
  const menu=PA.commandMenu();if(menu)return menu.open({target,trigger:target,returnFocus:target,label,items,onError:PA.menuError});
  const first=items.find(item=>item?.type!=="separator"&&item.danger!==true&&item.enabled!==false&&typeof item.run==="function");
  if(first)return first.run();setStatus("操作菜单暂不可用，请重新加载界面");return false;
};
PA.bindCommandMenu=(target,label,items)=>PA.commandMenu()?.bind(target,{label,items,onError:PA.menuError});
PA.attachCommandMenu=(target,label,items)=>{const more=UI.button("⋯",()=>PA.openCommandMenu(more,label,items()),"rc-row-menu");target.tabIndex=0;target.append(more);PA.bindCommandMenu(target,label,items);return target;};
PA.withCurrent=(rows,target,label,run)=>{const index=rows.indexOf(target);if(index<0){setStatus(`${label}已变化，请重新打开操作菜单`);return false;}return run(index);};
PA.is=profile=>profile?.kind===PA.kind;
PA.records=report=>report?.periodic_attack_compilations||[];
PA.kindName=value=>({periodic_callback_attack:"周期回调攻击",ability_start_attack:"独立启动攻击"}[value]||"攻击类型待核实");
PA.sourceFor=(plan,id)=>plan?.periodic_attack_sources?.find(s=>s.action_event_profile_id===id);
PA.owns=(plan,hitId)=>plan?.periodic_attack_sources?.find(s=>AE.profiles(plan).some(p=>p.id===s.action_event_profile_id&&PA.is(p)&&p.channels?.some(c=>c.prototype_hit_id===hitId)));
PA.projection=candidate=>candidate?.projection||candidate?.projection_summary;
PA.identity=candidate=>AM.identity(PA.projection(candidate));
PA.identityReady=identity=>identity?.status==="ready"&&typeof identity.tag==="string"&&identity.tag.trim().length>0;
PA.statusOptions=()=>["pending","assumed","confirmed"].map(value=>({value,label:RC.status(value)}));
PA.timeValid=value=>{
  if(value===null)return true;if(!Number.isFinite(value)||value<=0||value>1e9)return false;
  const [mantissa,exponent="0"]=String(value).toLowerCase().split("e"),parts=mantissa.split("."),power=6+Number(exponent)-(parts[1]?.length||0);
  return power>=0||BigInt(parts.join(""))%(10n**BigInt(-power))===0n;
};
PA.precisionValid=value=>value===null||(Number.isFinite(value)&&value>=0&&value<=1);
PA.idValid=value=>typeof value==="string"&&/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(value);
PA.newSchedule=kind=>kind==="single"?{kind,offset_seconds:null,precision_seconds:null,status:"pending",source:""}:
  {kind:"periodic",first_offset_seconds:null,count:null,precision_seconds:null,status:"pending",source:""};
PA.migrateMapping=m=>{
  const legacy=m.counter_tag||m.shared_group||"";
  m.application_mode=window.AM?.mode?AM.mode(m):(m.application_mode==="unknown"||!m.application_mode?"pending":m.application_mode);
  if(legacy)Object.defineProperty(m,"_rememberedCounterTag",{value:legacy,writable:true});
  if(m.application_mode==="sequence"&&legacy)m.counter_tag=legacy;else delete m.counter_tag;
  delete m.shared_group;delete m.attenuation_group;
  return m;
};
PA.rememberCounter=(mapping,value)=>{if(Object.hasOwn(mapping,"_rememberedCounterTag"))mapping._rememberedCounterTag=value;else Object.defineProperty(mapping,"_rememberedCounterTag",{value,writable:true});};
PA.verification=m=>{
  if(!m._verificationDraft)Object.defineProperty(m,"_verificationDraft",{value:UI.clone(m.verification||{locator:"",version:"",reviewed_at:"",component_scope:""}),writable:true});
  if(!m._verificationStatus)Object.defineProperty(m,"_verificationStatus",{value:m.verification?.status==="confirmed"?"confirmed":"pending",writable:true});
  return {draft:m._verificationDraft,status:m._verificationStatus};
};
PA.syncVerification=m=>{
  const state=PA.verification(m),complete=["locator","version","reviewed_at","component_scope"].every(key=>(state.draft[key]||"").trim());delete m.verification;
  if(m.application_mode!=="pending"&&state.status==="confirmed"&&complete)m.verification={status:"confirmed",...UI.clone(state.draft),confirmed_fields:m.application_mode==="sequence"?["application_mode","counter_tag","shared_scope"]:["application_mode","shared_scope"]};
  return {status:state.status,complete};
};
PA.renderReceipt=(box,report)=>{
  PA.records(report).forEach(row=>{
    const card=UI.el("section",null,"eq-card"),query=row.query||{};
    card.append(UI.el("h4","周期攻击数据库来源"),UI.el("p",`${row.status==="ready"?"所选资料就绪":"资料待完善"}；仅部分技能范围。创建物与参数的资料关联不证明动作能原生启动这些攻击。`),
      UI.el("p","倍率、元素、附着规则和可证周期由来源提供；首击、次数、生命周期、共享计数与取样仍须明确校准。"));
    (query.selected_candidates||[]).forEach(candidate=>{
      const p=candidate.projection||{},element=(window.AM?.elements||{})[p.element?.value]||"未知",timer=p.timer||{};
      card.append(UI.el("p",`${PA.kindName(candidate.channel_kind)}：${element}；原始附着量 ${p.gauge?.value??"未知"} GU；倍率 ${p.parameter?.value??"未知"}；${timer.status==="ready"?`来源周期 ${timer.seconds} 秒`:"启动时点需校准"}。`));
    });
    (row.verification_tasks||[]).forEach(task=>window.AM?.renderVerificationTask?.(card,task,task.next_action));
    AE.issues(card,row.issues,"周期来源待办");AE.issues(card,query.issues,"资料查询待办");
    card.append(UI.details("创建来源、挂载、参数、未支持分支和完整证据",row));box.append(card);
  });
};
PA.editProfile=index=>{
  if(!PA.caps()){setStatus("当前接口未提供周期动作档案");return;}
  const s=AE.session(index===undefined?"新增周期动作档案":"编辑周期动作档案");if(!s)return;
  const profile=index===undefined?{id:UI.id("periodic-profile"),kind:PA.kind,variant:"default",lifecycle:{establishment_offset_seconds:null,instance_end_offset_seconds:null,precision_seconds:null,status:"pending",source:"",owner_and_target_survive:null,no_interrupt:null,no_refresh_replacement_or_early_despawn:null},channels:[],accepted_profile_fingerprint:null}:s.local.action_event_profiles[index];
  if(!PA.is(profile)){s.close();return;}if(index===undefined)s.local.action_event_profiles.push(profile);
  const original=s.original,previous=AE.profiles(original).find(p=>p.id===profile.id),oldSource=PA.sourceFor(original,profile.id);
  const source=UI.clone(oldSource||{action_event_profile_id:profile.id,version_line:"release",selection:{character_id:null,slot:"burst",constellation:null},origin_reference_id:null,channels:[],purpose:"calibrated_periodic_attack",status:"pending",source:""});
  source.channels.forEach(PA.migrateMapping);
  profile.channels.forEach(c=>{if(!source.channels.some(m=>m.channel_id===c.channel_id))source.channels.push({channel_id:c.channel_id,candidate_id:null,application_mode:"pending"});});
  const restore={},body=UI.el("div"),queryBox=UI.el("div"),originBox=UI.el("div"),candidateBox=UI.el("div");
  let originQuery=null,channelQuery=null,queryAttempt=0,readSelection=null;
  const selectionStamp=()=>JSON.stringify([source.version_line,source.selection]);
  const channelStamp=()=>JSON.stringify([selectionStamp(),source.origin_reference_id]);
  const mapping=id=>source.channels.find(c=>c.channel_id===id);
  const changed=(selection=false)=>{
    queryAttempt++;s.changed(profile);
    if(selection){originQuery=null;channelQuery=null;readSelection=null;source.origin_reference_id=null;source.channels.forEach(c=>c.candidate_id=null);drawOrigins();drawCandidates();}
  };
  const retired=()=>[...new Set((previous?.channels||[]).map(c=>c.prototype_hit_id))].filter(id=>!profile.channels.some(c=>c.prototype_hit_id===id));
  const selectionValid=()=>{
    if(!Number.isSafeInteger(source.selection.character_id)||source.selection.character_id<1)throw Error("请明确数据库角色身份。");
    if(!Number.isInteger(source.selection.constellation)||source.selection.constellation<0||source.selection.constellation>6)throw Error("请填写实际命座；不能为通过检查改成零命。");
    if(source.selection.talent_level!==undefined&&(!Number.isInteger(source.selection.talent_level)||source.selection.talent_level<1||source.selection.talent_level>15))throw Error("请填写有效天赋等级；零命缺省为10级。");
    if(source.selection.skill_depot_id!==undefined&&(!Number.isSafeInteger(source.selection.skill_depot_id)||source.selection.skill_depot_id<1))throw Error("技能组身份须为有效整数或留空。");
  };
  const addChannel=()=>{
    if(profile.channels.length>=8){setStatus("每个周期档案最多八个通道");return;}
    const id=UI.id("channel");profile.channels.push({channel_id:id,prototype_hit_id:"",application_profile_id:UI.id("application"),contact_profile_id:"",schedule:PA.newSchedule("periodic"),default_landed:null,self_sampling:"current"});
    source.channels.push({channel_id:id,candidate_id:null,application_mode:"pending"});changed();drawChannels();drawCandidates();
  };
  const removeChannel=channel=>{
    return PA.withCurrent(profile.channels,channel,"攻击通道",index=>{profile.channels.splice(index,1);source.channels=source.channels.filter(c=>c.channel_id!==channel.channel_id);changed();drawChannels();drawCandidates();return true;});
  };
  const drawOrigins=()=>{
    originBox.innerHTML="";
    const candidates=originQuery?.creation_candidates||[];
    originBox.append(UI.field("精确创建来源",UI.select([{value:"",label:"尚未选择，保留待完善"},...candidates.map((c,i)=>({value:c.origin_reference_id,label:`创建来源 ${i+1} · 对象 ${c.gadget_id??"待核实"} · 仅资料相关`})),
      ...(source.origin_reference_id&&!candidates.some(c=>c.origin_reference_id===source.origin_reference_id)?[{value:source.origin_reference_id,label:"已保存的来源（本次尚未发现核对）"}]:[])],source.origin_reference_id||"",v=>{
        source.origin_reference_id=v||null;channelQuery=null;readSelection=null;source.channels.forEach(c=>c.candidate_id=null);changed();drawCandidates();
      })));
    if(originQuery){AE.issues(originBox,originQuery.issues,"发现过程待办");originBox.append(UI.details("所有创建候选、受阻路径与原始身份",originQuery));}
  };
  const drawCandidates=()=>{
    candidateBox.innerHTML="";
    if(!channelQuery){candidateBox.append(UI.el("p","选定创建来源后读取攻击通道；不会自动采用唯一候选。"));return;}
    const candidates=channelQuery.candidates||[];
    profile.channels.forEach((channel,i)=>{
      const selected=mapping(channel.channel_id);if(!selected)return;
      candidateBox.append(UI.field(`通道 ${i+1} 的数据库攻击候选`,UI.select([{value:"",label:"尚未选择，保留待完善"},...candidates.map((c,j)=>({value:c.candidate_id,label:`候选 ${j+1} · ${PA.kindName(c.channel_kind)} · ${c.status==="ready"?"局部可用":"待核实"}`}))],selected.candidate_id||"",v=>{
        selected.candidate_id=v||null;const identity=PA.identity(candidates.find(c=>c.candidate_id===selected.candidate_id));
        if(selected.application_mode==="sequence"&&PA.identityReady(identity))selected.counter_tag=identity.tag;
        changed();drawCandidates();drawChannels();
      })));
      const candidate=candidates.find(c=>c.candidate_id===selected.candidate_id);
      if(candidate){candidateBox.append(UI.el("p",`通道 ${i+1} 当前调度：${channel.schedule.kind==="periodic"?"周期":"独立单次"}；来源类型：${PA.kindName(candidate.channel_kind)}。两者须一致。`));AE.issues(candidateBox,candidate.issues,"所选候选待办");}
    });
    AE.issues(candidateBox,channelQuery.issues,"攻击来源待办");candidateBox.append(UI.details("完整通道候选、参数与未支持分支",channelQuery));
  };
  const read=async stage=>{
    try{selectionValid();if(stage==="channels"&&!source.origin_reference_id)throw Error("请先明确选择创建来源。");}catch(error){queryBox.innerHTML="";queryBox.append(UI.el("p",error.message,"eq-error"));return;}
    const stamp=channelStamp(),attempt=++queryAttempt,payload={action:"periodic_attack_profile",version_line:source.version_line,...UI.clone(source.selection)};
    if(stage==="channels")payload.origin_reference_id=source.origin_reference_id;
    queryBox.innerHTML="";let response;
    try{response=await callApi("query_runtime",payload);}catch(error){if(s.current()&&attempt===queryAttempt)queryBox.append(UI.el("p",`读取失败：${error.message}`,"eq-error"));return;}
    if(!s.current()||attempt!==queryAttempt||stamp!==channelStamp()){setStatus("旧周期来源响应已忽略，请重新读取");return;}
    if(response.code){queryBox.append(UI.error(response));return;}
    const data=response.data,rows=stage==="origins"?data?.creation_candidates:data?.candidates,key=stage==="origins"?"origin_reference_id":"candidate_id";
    const selection=data?.selection,identityMatches=selection?.character_id===source.selection.character_id&&selection?.constellation===source.selection.constellation&&
      (source.selection.skill_id===undefined||selection?.skill_id===source.selection.skill_id)&&(source.selection.skill_depot_id===undefined||selection?.skill_depot_id===source.selection.skill_depot_id)&&
      (source.selection.slot===undefined||selection?.slot===source.selection.slot||selection?.matching_slots?.includes(source.selection.slot));
    if(!identityMatches||data?.mode!==(stage==="origins"?"origin_discovery":"channel_discovery")||!Array.isArray(rows)||rows.length>32||rows.some(c=>typeof c?.[key]!=="string"||stage==="origins"&&!/^[0-9a-f]{64}$/.test(c[key]))||new Set(rows.map(c=>c[key])).size!==rows.length){queryBox.append(UI.el("p","接口候选列表不完整、身份不符或超限，未采用。","eq-error"));return;}
    if(stage==="origins"){
      originQuery=UI.clone(data);
      if(source.origin_reference_id&&!rows.some(c=>c.origin_reference_id===source.origin_reference_id)){source.origin_reference_id=null;channelQuery=null;readSelection=null;source.channels.forEach(c=>c.candidate_id=null);changed();}
      drawOrigins();drawCandidates();
    }else{
      channelQuery=UI.clone(data);readSelection=stamp;
      source.channels.forEach(c=>{if(c.candidate_id&&!rows.some(r=>r.candidate_id===c.candidate_id)){c.candidate_id=null;s.changed(profile);}});drawCandidates();
    }
  };
  const drawChannels=()=>{
    body.innerHTML="";const hits=original.manual_template?.hits||[],contacts=original.mechanics_model?.elemental_model?.contact_profiles||[];
    profile.channels.forEach((channel,i)=>{
      const card=UI.el("section",null,"eq-card"),m=mapping(channel.channel_id),schedule=channel.schedule,candidate=(channelQuery?.candidates||[]).find(c=>c.candidate_id===m?.candidate_id),projection=PA.projection(candidate),identity=PA.identity(candidate),identityReady=PA.identityReady(identity),rule=projection?.attenuation?.group||projection?.attenuation?.profile?.projection?.group||"待数据库候选确认",verification=m&&PA.verification(m);
      card.append(UI.el("h4",`攻击通道 ${i+1}`),UI.field("共享伤害原型",UI.select(AE.options(hits,channel.prototype_hit_id,h=>DS.hit(h,undefined,original.manual_template)),channel.prototype_hit_id,v=>{channel.prototype_hit_id=v;changed();drawChannels();})),
        UI.field("接触档案",UI.select(AE.options(contacts,channel.contact_profile_id,c=>c.source||c.id),channel.contact_profile_id,v=>{channel.contact_profile_id=v;changed();})),
        UI.field("调度方式",UI.select([{value:"periodic",label:"周期回调（间隔来自数据库）"},{value:"single",label:"独立启动的单次攻击"}],schedule.kind,v=>{channel.schedule=PA.newSchedule(v);changed();drawChannels();drawCandidates();})));
      if(schedule.kind==="periodic")card.append(UI.field("首击距动作开始（秒）",UI.input(schedule.first_offset_seconds,v=>{schedule.first_offset_seconds=v;changed();},true)),UI.field("本次实例接触次数",UI.input(schedule.count,v=>{schedule.count=v;changed();},true)));
      else card.append(UI.field("本次攻击距动作开始（秒）",UI.input(schedule.offset_seconds,v=>{schedule.offset_seconds=v;changed();},true)));
      card.append(UI.field("通道记录精度（秒）",UI.input(schedule.precision_seconds,v=>{schedule.precision_seconds=v;changed();},true)),
        UI.field("通道校准状态",UI.select(PA.statusOptions(),schedule.status,v=>{schedule.status=v;changed();})),UI.field("通道校准依据",UI.area(schedule.source,v=>{schedule.source=v;changed();})),
        UI.field("默认命中判断",UI.select(AE.landedOptions,AE.landedValue(channel.default_landed),v=>{channel.default_landed=v===""?null:v==="true";changed();})),
        UI.field("自身属性取样",UI.select([{value:"current",label:"每次命中时取当前属性"},{value:"action_start",label:"采用动作开始时的属性"},{value:"action_capture",label:"采用该动作明确声明的面板取样"}],channel.self_sampling,v=>{channel.self_sampling=v;changed();})),
        UI.field("元素附着模式",UI.select(AM.applicationModes,m?.application_mode||"pending",v=>{if(m.counter_tag)PA.rememberCounter(m,m.counter_tag);m.application_mode=v;delete m.counter_tag;delete m.verification;if(v==="pending"){m._verificationStatus="pending";source.status="pending";}else if(v==="sequence")m.counter_tag=identityReady?identity.tag:m._rememberedCounterTag||"";changed();drawChannels();})),
        ...(m?.application_mode==="sequence"?[UI.field("附着计数标签（ICD Tag）",UI.input(m?.counter_tag||"",v=>{m.counter_tag=v;PA.rememberCounter(m,v);changed();})),UI.el("p",`附着规则组（数据库候选）：${rule}。${AM.identityText(candidate?.projection)}`)]:[UI.el("p",m?.application_mode==="pending"?`计数标签与规则组：待核实，不会用于确定性计算。${m._rememberedCounterTag?` 已加载旧计数标签 ${m._rememberedCounterTag}，须审阅模式后才会迁移提交。`:""}`:"计数标签与规则组：此附着模式不适用。")]),
        UI.field("逐段结构化证据状态",UI.select([{value:"pending",label:identityReady&&m?.application_mode==="sequence"?"数据库已确认计数身份（无需重复外部证据）":"待核实（不会伪造确认）"},{value:"confirmed",label:"已由结构化证据确认"}],verification?.status||"pending",v=>{m._verificationStatus=v;changed();drawChannels();})),
        ...(m?.application_mode!=="pending"&&verification?.status==="confirmed"?[
          UI.field("证据类型与定位（URL、本地路径或原始 pointer）",UI.input(verification.draft.locator,v=>{verification.draft.locator=v;changed();})),
          UI.field("适用游戏版本或固定 commit",UI.input(verification.draft.version,v=>{verification.draft.version=v;changed();})),
          UI.field("检索/复核日期（YYYY-MM-DD）",UI.input(verification.draft.reviewed_at,v=>{verification.draft.reviewed_at=v;changed();})),
          UI.field("具体攻击 component_scope 与共享范围",UI.area(verification.draft.component_scope,v=>{verification.draft.component_scope=v;changed();})),
          UI.el("p",`确认字段：${m?.application_mode==="sequence"?"application_mode、counter_tag、shared_scope":"application_mode、shared_scope"}。研究日志本身不能绕过后端证据门槛。`)
        ]:[]),
        UI.el("p","旧 shared_group 加载后迁移为 counter_tag；应用时只保存 canonical 字段。共享身份仍须有具体攻击段证据。"));
      const items=()=>[{id:`remove-periodic-channel-${channel.channel_id}`,label:"移除攻击通道",danger:true,run:()=>removeChannel(channel)}];
      body.append(PA.attachCommandMenu(card,`攻击通道 ${i+1} 操作`,items));
    });
    retired().forEach(id=>{const hit=hits.find(h=>h.id===id);body.append(UI.field(`退出来源接管的原型元素：${hit?DS.hit(hit,undefined,original.manual_template):id}`,UI.select([{value:"",label:"请明确选择退回手工后的元素"},...Object.entries(AM.elements).map(([value,label])=>({value,label}))],restore[id]||"",v=>{restore[id]=v;changed();})));});
  };
  const lifecycle=profile.lifecycle;
  s.form.append(UI.el("p","零命、有效10级爆发的有限来源档案。创建关联尚未证明原生派发；按本轴校准执行，不补动作、次数或持续时间。"),
    UI.field("动作变体声明",UI.select(["default","tap","hold"].map(value=>({value,label:AE.variant(value)})),profile.variant,v=>{profile.variant=v;changed();})),
    UI.field("实例建立距动作开始（秒）",UI.input(lifecycle.establishment_offset_seconds,v=>{lifecycle.establishment_offset_seconds=v;changed();},true)),
    UI.field("实例结束距动作开始（秒）",UI.input(lifecycle.instance_end_offset_seconds,v=>{lifecycle.instance_end_offset_seconds=v;changed();},true)),
    UI.field("生命周期记录精度（秒）",UI.input(lifecycle.precision_seconds,v=>{lifecycle.precision_seconds=v;changed();},true)),
    UI.field("生命周期核实状态",UI.select(PA.statusOptions(),lifecycle.status,v=>{lifecycle.status=v;changed();})),UI.field("生命周期依据",UI.area(lifecycle.source,v=>{lifecycle.source=v;changed();})));
  for(const [key,label] of [["owner_and_target_survive","持有者与目标在实例内存活"],["no_interrupt","没有提前中断"],["no_refresh_replacement_or_early_despawn","没有刷新替换或提前消失"]])s.form.append(UI.field(label,UI.select([{value:"",label:"尚未核实"},{value:"true",label:"明确成立"},{value:"false",label:"明确不成立"}],AE.landedValue(lifecycle[key]),v=>{lifecycle[key]=v===""?null:v==="true";changed();})));
  s.form.append(body,UI.button("添加攻击通道",addChannel),UI.el("h4","数据库来源"),
    UI.field("资料线",UI.select([{value:"release",label:"主要资料线（沿用当前库）"},{value:"beta",label:"第二资料线"}],source.version_line,v=>{source.version_line=v;changed(true);})),
    UI.field("数据库角色身份",UI.input(source.selection.character_id,v=>{source.selection.character_id=v;changed(true);},true)),
    UI.field("实际命座",UI.input(source.selection.constellation,v=>{source.selection.constellation=v;changed(true);},true)),
    UI.field("有效天赋等级（零命留空默认10级）",UI.input(source.selection.talent_level,v=>{if(v===null)delete source.selection.talent_level;else source.selection.talent_level=v;changed(true);},true)),
    UI.field("技能组身份（多个技能组时必填）",UI.input(source.selection.skill_depot_id,v=>{if(v===null)delete source.selection.skill_depot_id;else source.selection.skill_depot_id=v;changed(true);},true)),
    UI.el("p",source.selection.skill_id?`沿用精确技能身份 ${source.selection.skill_id}，仅受支持爆发范围可执行。`:"查询元素爆发槽。选择候选只表示资料关联，条件分支是否成立由后端逐项检查。"),
    UI.button("发现相关创建来源",()=>read("origins")),originBox,UI.button("读取所选来源的攻击通道",()=>read("channels")),queryBox,candidateBox,
    UI.field("来源采用状态",UI.select(PA.statusOptions(),source.status,v=>{source.status=v;changed();})),UI.field("来源用途与共享关系依据",UI.area(source.source,v=>{source.source=v;changed();})),
    UI.el("p","应用时，这些通道原型的手填元素、倍率及独立数值/附着来源将由当前复合来源接管。原生只保存选择和校准，不保存数据库原文或派生命中。"));
  s.transform=plan=>{
    source.channels.forEach(PA.syncVerification);if(source.channels.some(m=>m.application_mode==="pending"))source.status="pending";
    plan.periodic_attack_sources=[...(original.periodic_attack_sources||[]).filter(r=>r.action_event_profile_id!==profile.id),UI.clone(source)];
    const owned=new Set(profile.channels.map(c=>c.prototype_hit_id)),apps=new Set(profile.channels.map(c=>c.application_profile_id));
    for(const hit of plan.manual_template.hits){if(owned.has(hit.id)){delete hit.element;delete hit.multiplier;}else if(retired().includes(hit.id)&&restore[hit.id])hit.element=restore[hit.id];}
    if(plan.hit_parameter_sources)plan.hit_parameter_sources=plan.hit_parameter_sources.filter(r=>!owned.has(r.hit_id));
    if(plan.elemental_sources)plan.elemental_sources=plan.elemental_sources.filter(r=>!apps.has(r.profile_id));
    if(plan.mechanics_model?.elemental_model)plan.mechanics_model.elemental_model.profiles=plan.mechanics_model.elemental_model.profiles.filter(r=>!apps.has(r.id));
  };
  s.commit=(plan,proposed)=>{
    plan.periodic_attack_sources=UI.clone(proposed.periodic_attack_sources);
    const touched=new Set([...profile.channels.map(c=>c.prototype_hit_id),...retired()]);
    for(const hit of plan.manual_template.hits)if(touched.has(hit.id)){const next=proposed.manual_template.hits.find(h=>h.id===hit.id);for(const key of ["element","multiplier"]){delete hit[key];if(Object.hasOwn(next,key))hit[key]=next[key];}}
    for(const key of ["hit_parameter_sources","elemental_sources"])if(Object.hasOwn(proposed,key))plan[key]=UI.clone(proposed[key]);
    if(plan.mechanics_model?.elemental_model)plan.mechanics_model.elemental_model.profiles=UI.clone(proposed.mechanics_model.elemental_model.profiles);
  };
  s.validate=()=>{
    selectionValid();
    if(!profile.channels.length||profile.channels.length>8)throw Error("请保留一至八个攻击通道。");
    if(![lifecycle.establishment_offset_seconds,lifecycle.instance_end_offset_seconds].every(PA.timeValid)||!PA.precisionValid(lifecycle.precision_seconds))throw Error("生命周期时点须为精确微秒正数，精度须在0至1秒；未知可留空。");
    if(!lifecycle.source.trim()||!source.source.trim())throw Error("请填写生命周期与来源采用依据；尚未核实的项可保留待完善状态。");
    for(const key of ["channel_id","prototype_hit_id","application_profile_id"])if(new Set(profile.channels.map(c=>c[key])).size!==profile.channels.length)throw Error("各通道须拥有独立标识、伤害原型和附着输出槽位。");
    const selected=source.channels.map(c=>c.candidate_id).filter(Boolean);if(new Set(selected).size!==selected.length)throw Error("不同通道不能重复采用同一攻击候选。");
    for(const channel of profile.channels){const q=channel.schedule,m=mapping(channel.channel_id);
      if(![channel.channel_id,channel.prototype_hit_id,channel.application_profile_id,channel.contact_profile_id].every(PA.idValid)||!original.manual_template.hits.some(h=>h.id===channel.prototype_hit_id)||!original.mechanics_model?.elemental_model?.contact_profiles.some(c=>c.id===channel.contact_profile_id))throw Error("请为每个通道关联已有伤害原型与接触档案。");
      if(!PA.timeValid(q.kind==="periodic"?q.first_offset_seconds:q.offset_seconds)||!PA.precisionValid(q.precision_seconds)||q.kind==="periodic"&&q.count!==null&&(!Number.isInteger(q.count)||q.count<1||q.count>256))throw Error("通道时点须精确到微秒，次数为1至256整数，精度为0至1秒；未知可留空。");
      if(!q.source.trim())throw Error("请填写每个通道的校准依据。");
      if(!m||!AM.applicationModes.some(mode=>mode.value===m.application_mode))throw Error("请为每个通道明确附着模式；未知值将保持待核实。");
      const candidate=(channelQuery?.candidates||[]).find(c=>c.candidate_id===m.candidate_id),identity=PA.identity(candidate),identityReady=PA.identityReady(identity),evidence=PA.syncVerification(m);
      if(m.application_mode==="sequence"){
        if(!(m.counter_tag||"").trim())throw Error("有限序列通道必须填写附着计数标签（ICD Tag）。");
        if(identityReady&&m.counter_tag.trim()!==identity.tag)throw Error(`计数标签必须与数据库确认的 attenuationTag（${identity.tag}）一致。`);
        if(!identityReady&&(evidence.status!=="confirmed"||!evidence.complete))throw Error("数据库未确认计数身份时，有限序列通道必须提供已确认的结构化证据，包括定位、版本、日期与 component_scope。");
      }else if(m.application_mode!=="pending"&&(evidence.status!=="confirmed"||!evidence.complete))throw Error("已确认的周期附着模式必须提供结构化逐段证据，包括定位、版本、日期与 component_scope。");
    }
    if(source.origin_reference_id&&readSelection!==channelStamp())throw Error("请先读取当前创建来源的攻击通道，再预览或应用。");
    if(retired().some(id=>!restore[id]))throw Error("退出来源接管的原型须明确补回元素；不会复制上次计算结果。");
  };
  s.validateReport=(report,proposed)=>{const expected=proposed.periodic_attack_sources||[],records=PA.records(report);if(records.length!==expected.length||expected.some(r=>!records.some(c=>c.action_event_profile_id===r.action_event_profile_id)))throw Error("接口没有返回完整周期来源回执，未应用。");};
  if(index===undefined)addChannel();else drawChannels();drawOrigins();drawCandidates();AE.controls(s,profile.id);
};
PA.editOverride=index=>{
  if(!PA.caps())return;const s=AE.session(index===undefined?"添加周期接触例外":"编辑周期接触例外");if(!s)return;
  const row=index===undefined?{kind:PA.kind,action_id:"",contacts:[]}:s.local.action_event_overrides[index];if(row?.kind!==PA.kind){s.close();return;}
  if(index===undefined)s.local.action_event_overrides.push(row);
  const profiles=new Map(s.local.action_event_profiles.filter(PA.is).map(p=>[p.id,p])),bindings=new Map(s.local.action_event_bindings.filter(b=>profiles.has(b.profile_id)).map(b=>[b.ability_id,b.profile_id])),actions=(s.original.rotation_model?.actions||[]).filter(a=>bindings.has(a.ability_id));
  const body=UI.el("div"),selected=()=>profiles.get(bindings.get(actions.find(a=>a.id===row.action_id)?.ability_id));
  const removeContact=contact=>PA.withCurrent(row.contacts,contact,"接触例外",index=>{row.contacts.splice(index,1);s.changed();draw();return true;});
  const draw=()=>{body.innerHTML="";const profile=selected();row.contacts.forEach((contact,i)=>{const card=UI.el("section",null,"eq-card");
    card.append(UI.field("攻击通道",UI.select([{value:"",label:"请选择"},...(profile?.channels||[]).map((c,j)=>({value:c.channel_id,label:`通道 ${j+1}`}))],contact.channel_id,v=>{contact.channel_id=v;s.changed();})),
      UI.field("接触序号（从1开始）",UI.input(contact.ordinal,v=>{contact.ordinal=v;s.changed();},true)),
      UI.field("本次命中例外",UI.select([{value:"inherit",label:"沿用档案，不覆盖"},...AE.landedOptions],Object.hasOwn(contact,"landed")?AE.landedValue(contact.landed):"inherit",v=>{if(v==="inherit")delete contact.landed;else contact.landed=v===""?null:v==="true";s.changed();})),
      UI.field("本次同刻顺序（空白不覆盖）",UI.input(contact.order,v=>{if(v===null)delete contact.order;else contact.order=v;s.changed();},true)),
      UI.field("例外核实状态",UI.select(PA.statusOptions(),contact.status,v=>{contact.status=v;s.changed();})),UI.field("本次例外的依据",UI.area(contact.source,v=>{contact.source=v;s.changed();})));
    const items=()=>[{id:`remove-periodic-contact-${contact.channel_id||"pending"}-${contact.ordinal??"pending"}`,label:"移除接触例外",danger:true,run:()=>removeContact(contact)}];
    body.append(PA.attachCommandMenu(card,`接触例外 ${i+1} 操作`,items));});};
  s.form.append(UI.field("本轴实际周期动作",UI.select(AE.options(actions,row.action_id,a=>AE.actionName(a.id,s.original)),row.action_id,v=>{row.action_id=v;row.contacts=[];s.changed();draw();})),body,
    UI.button("添加接触例外",()=>{if(!selected()){setStatus("请先选择已绑定周期档案的实际动作");return;}if(row.contacts.length>=2048)return;row.contacts.push({channel_id:"",ordinal:null,landed:null,status:"pending",source:""});s.changed();draw();}));
  s.validate=()=>{
    const profile=selected();if(!profile||!row.contacts.length)throw Error("请选定实际动作并至少添加一项接触例外。");const keys=new Set();
    for(const c of row.contacts){const channel=profile.channels.find(x=>x.channel_id===c.channel_id),key=JSON.stringify([c.channel_id,c.ordinal]);
      if(!channel||!Number.isInteger(c.ordinal)||c.ordinal<1||c.ordinal>(channel.schedule.kind==="single"?1:channel.schedule.count??256)||keys.has(key))throw Error("例外须指向有效且不重复的通道接触序号。");keys.add(key);
      if(!Object.hasOwn(c,"landed")&&!Object.hasOwn(c,"order"))throw Error("例外至少改变命中或同刻顺序。");if(Object.hasOwn(c,"order")&&(!Number.isInteger(c.order)||Math.abs(c.order)>1e9))throw Error("同刻顺序须为范围内整数。");if(!c.source.trim())throw Error("请填写每个接触例外的依据。");
    }
  };draw();AE.controls(s);
};
PA.dropProfile=index=>{
  const s=AE.session("移除周期档案及来源");if(!s)return;const row=s.local.action_event_profiles[index];if(!PA.is(row)){s.close();return;}
  const restore={},owned=new Set(row.channels.map(c=>c.prototype_hit_id)),abilities=new Set(s.local.action_event_bindings.filter(b=>b.profile_id===row.id).map(b=>b.ability_id)),actions=new Set((s.original.rotation_model?.actions||[]).filter(a=>abilities.has(a.ability_id)).map(a=>a.id));
  s.local.action_event_profiles.splice(index,1);s.local.action_event_bindings=s.local.action_event_bindings.filter(b=>b.profile_id!==row.id);s.local.action_event_overrides=s.local.action_event_overrides.filter(o=>!actions.has(o.action_id));
  s.form.append(UI.el("p","将移除此档案、来源、绑定与相应例外。实际动作和伤害原型保留；请为退出接管的原型选择元素，倍率及附着需另行补齐。"));
  for(const id of owned)s.form.append(UI.field(`原型 ${id} 的手工元素`,UI.select([{value:"",label:"请明确选择"},...Object.entries(AM.elements).map(([value,label])=>({value,label}))],"",v=>{restore[id]=v;s.changed();})));
  s.transform=plan=>{plan.periodic_attack_sources=(plan.periodic_attack_sources||[]).filter(r=>r.action_event_profile_id!==row.id);for(const hit of plan.manual_template.hits)if(owned.has(hit.id)&&restore[hit.id])hit.element=restore[hit.id];};
  s.commit=(plan,proposed)=>{plan.periodic_attack_sources=UI.clone(proposed.periodic_attack_sources);for(const hit of plan.manual_template.hits)if(owned.has(hit.id))hit.element=proposed.manual_template.hits.find(h=>h.id===hit.id).element;};
  s.validate=()=>{if([...owned].some(id=>!restore[id]))throw Error("请逐个明确补回原型元素，不能从旧结果猜测。");};AE.controls(s);
};
