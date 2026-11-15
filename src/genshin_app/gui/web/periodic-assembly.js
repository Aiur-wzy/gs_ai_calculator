"use strict";
// Native assembly only: the existing periodic editor owns subsequent review and acceptance.
window.PAS={};
PAS.caps=()=>{const c=PL.state.caps?.periodic_source_assembly_contract;return c?.contract==="source-periodic-assembly-v1"&&c.schema_version===1&&c.auto_accept===false?c:null;};
PAS.applicationModes=()=>{const allowed=PAS.caps()?.request_schema?.properties?.channels?.items?.properties?.application_mode?.enum||["pending","sequence","no_icd","no_application"];
  const modes=AM.applicationModes.filter(mode=>allowed.includes(mode.value));return modes.sort((a,b)=>(a.value==="pending"?-1:b.value==="pending"?1:0));};
PAS.rememberCounter=(channel,value)=>{if(Object.hasOwn(channel,"_rememberedCounterTag"))channel._rememberedCounterTag=value;else Object.defineProperty(channel,"_rememberedCounterTag",{value,writable:true});};
PAS.order=(rows,key)=>[...rows].sort((a,b)=>a[key]<b[key]?-1:a[key]>b[key]?1:0);
PAS.verification=channel=>{
  if(!channel._verificationDraft)Object.defineProperty(channel,"_verificationDraft",{value:UI.clone(channel.verification||{locator:"",version:"",reviewed_at:"",component_scope:""}),writable:true});
  if(!channel._verificationStatus)Object.defineProperty(channel,"_verificationStatus",{value:channel.verification?.status==="confirmed"?"confirmed":"pending",writable:true});
  return {draft:channel._verificationDraft,status:channel._verificationStatus};
};
PAS.syncVerification=channel=>{
  const state=PAS.verification(channel),complete=["locator","version","reviewed_at","component_scope"].every(key=>(state.draft[key]||"").trim());
  delete channel.verification;
  if(channel.application_mode!=="pending"&&state.status==="confirmed"&&complete)channel.verification={status:"confirmed",...UI.clone(state.draft),confirmed_fields:channel.application_mode==="sequence"?["application_mode","counter_tag","shared_scope"]:["application_mode","shared_scope"]};
  return {status:state.status,complete};
};
PAS.checkedProposal=async(original,request,data)=>{
  const a=data?.assembly,report=data?.report,fail="多通道装配回复与请求的增量范围不一致，未应用。",need=ASA.need;
  need(a?.contract==="source-periodic-assembly-v1"&&a.schema_version===1&&["created","existing"].includes(a.status)&&data.plan&&report,fail);
  const abilities=request.ability_ids.map(id=>original.rotation_model.abilities.find(row=>row.id===id)),first=abilities[0],ids=[...request.ability_ids].sort();
  need(first&&new Set(ids).size===ids.length&&abilities.every(row=>row&&row.action_type_id==="action.elemental_burst"&&row.character===first.character&&row.variant===first.variant)&&["default","tap","hold"].includes(first.variant),fail);
  need(request.channels.length>=1&&request.channels.length<=8&&new Set(request.channels.map(c=>c.candidate_id)).size===request.channels.length,fail);
  const hits=[],channels=[],mappings=[],routes=[];
  for(const c of PAS.order(request.channels,"candidate_id")){
    need(typeof c.candidate_id==="string"&&c.candidate_id.length>0,fail);
    const hash=await ASA.digest(["source-periodic-assembly-v1",request.profile_id,c.candidate_id]),channel_id="assembly.channel."+hash,prototype_hit_id="assembly.hit."+hash,application_profile_id="assembly.application."+hash;
    routes.push({candidate_id:c.candidate_id,channel_id,prototype_hit_id,application_profile_id,schedule_kind:c.schedule.kind});
    hits.push({id:prototype_hit_id,t:0,character:first.character,skill:"元素爆发",hit_name:c.schedule.kind==="periodic"?"周期接触":"单次接触",damage_type_ids:UI.clone(request.damage_type_ids),damage_system:"classic",scaling_stat:request.scaling_stat,count:1});
    channels.push({channel_id,prototype_hit_id,application_profile_id,contact_profile_id:c.contact_profile_id,default_landed:c.default_landed,self_sampling:c.self_sampling,schedule:UI.clone(c.schedule)});
    mappings.push({channel_id,candidate_id:c.candidate_id,...(c.application_mode==="sequence"?{counter_tag:c.counter_tag}:{}),application_mode:c.application_mode,...(c.verification?{verification:UI.clone(c.verification)}:{}),...(c.research_report?{research_report:UI.clone(c.research_report)}:{})});
  }
  need(a.actor===first.character&&a.variant===first.variant&&a.profile_id===request.profile_id&&ASA.equal(a.ability_ids,ids)&&ASA.equal(a.channels,routes),fail);
  for(const key of ["base_input_fingerprint","input_fingerprint"])need(typeof a[key]==="string"&&/^[0-9a-f]{64}$/.test(a[key]),fail);
  const profile={id:request.profile_id,kind:PA.kind,variant:first.variant,lifecycle:UI.clone(request.lifecycle),channels,accepted_profile_fingerprint:null};
  const source={action_event_profile_id:profile.id,version_line:request.version_line,selection:UI.clone(request.selection),origin_reference_id:request.origin_reference_id,channels:mappings,purpose:"calibrated_periodic_attack",status:request.status,source:request.source};
  const bindings=ids.map(ability_id=>({ability_id,profile_id:profile.id})),expected=UI.clone(original);
  if(a.status==="created"){
    need(!(expected.action_event_profiles||[]).some(p=>p.id===profile.id)&&!expected.manual_template.hits.some(h=>hits.some(n=>h.id===n.id))
      &&!(expected.action_event_bindings||[]).some(b=>ids.includes(b.ability_id))&&!(expected.periodic_attack_sources||[]).some(s=>s.action_event_profile_id===profile.id),fail);
    expected.manual_template.hits.push(...hits);(expected.action_event_profiles||=[]).push(profile);(expected.action_event_bindings||=[]).push(...bindings);
    expected.action_event_overrides||=[];(expected.periodic_attack_sources||=[]).push(source);expected.resolution={state:"pending"};expected.resolved_template=null;
    need(ASA.equal(a.created,{hits:hits.length,profiles:1,bindings:ids.length,sources:1,channels:channels.length}),fail);
  }else{
    const oldProfiles=(expected.action_event_profiles||[]).filter(p=>p.id===profile.id),oldSources=(expected.periodic_attack_sources||[]).filter(s=>s.action_event_profile_id===profile.id);
    need(oldProfiles.length===1&&oldSources.length===1,fail);
    need(ASA.equal({...oldProfiles[0],channels:PAS.order(oldProfiles[0].channels,"channel_id"),accepted_profile_fingerprint:null},{...profile,channels:PAS.order(channels,"channel_id")})
      &&ASA.equal({...oldSources[0],channels:PAS.order(oldSources[0].channels,"candidate_id")},source)
      &&hits.every(h=>expected.manual_template.hits.filter(old=>old.id===h.id).length===1&&ASA.equal(expected.manual_template.hits.find(old=>old.id===h.id),h))
      &&ASA.equal(PAS.order((expected.action_event_bindings||[]).filter(b=>ids.includes(b.ability_id)||b.profile_id===profile.id),"ability_id"),bindings),fail);
    need(ASA.equal(a.created,{hits:0,profiles:0,bindings:0,sources:0,channels:0}),fail);
  }
  need(ASA.equal(data.plan,expected),fail);
  need(report.plan&&["ready","pending"].includes(report.state)&&ASA.equal(PL.input(report.plan),PL.input(expected)),"预览方案与多通道草稿不一致，未应用。");
  for(const [field,key,input,inputKey] of [["action_event_compilations","profile_id","action_event_profiles","id"],["periodic_attack_compilations","action_event_profile_id","periodic_attack_sources","action_event_profile_id"],["attack_metadata_compilations","action_event_profile_id","attack_metadata_sources","action_event_profile_id"]]){
    const targets=expected[input]||[],records=report[field]||[];
    need(Array.isArray(records)&&records.length===targets.length&&new Set(records.map(r=>r[key])).size===records.length&&targets.every(p=>records.some(r=>r[key]===p[inputKey])),"缺少完整动作档案或攻击来源回执，未应用。");
  }
  const s=a.source_summary;
  need(s&&s.whole_source_status==="partial"&&s.action_creation_association==="unproven"&&s.dispatch_semantics_status==="not_proven"&&ASA.equal(s.requested_candidate_ids,routes.map(c=>c.candidate_id))
    &&(s.omission_status==="not_evaluated"&&s.omitted_candidate_ids===null||s.omission_status==="known"&&Array.isArray(s.omitted_candidate_ids)&&new Set(s.omitted_candidate_ids).size===s.omitted_candidate_ids.length&&s.omitted_candidate_ids.every(id=>typeof id==="string"&&!routes.some(c=>c.candidate_id===id))),fail);
  const sourceRecord=report.periodic_attack_compilations.find(r=>r.action_event_profile_id===profile.id),q=sourceRecord.query||{};
  const omitted=q.mode==="selected"&&Array.isArray(q.omitted_candidates)?[...q.omitted_candidates].sort():q.mode==="channel_discovery"&&Array.isArray(q.candidates)?q.candidates.map(c=>c.candidate_id).filter(id=>!routes.some(c=>c.candidate_id===id)).sort():null;
  need(s.status===(sourceRecord.status||"pending")&&s.query_mode===(q.mode??null)&&ASA.equal(s.omitted_candidate_ids,omitted)&&s.omission_status===(omitted===null?"not_evaluated":"known"),fail);
  return expected;
};
PAS.lifecycle=()=>({establishment_offset_seconds:null,instance_end_offset_seconds:null,precision_seconds:null,status:"pending",source:"",owner_and_target_survive:null,no_interrupt:null,no_refresh_replacement_or_early_despawn:null});
PAS.selectionValid=selection=>{
  ASA.need((selection.slot==="burst")!==Object.hasOwn(selection,"skill_id"),"请采用爆发槽或精确技能身份其中一种。");
  ASA.selectionValid({...selection,...(selection.slot==="burst"?{slot:"skill"}:{})});
};
PAS.edit=()=>{
  if(PL.state.draft){setStatus("请先应用或放弃装备草稿，再建立多通道档案");return;}
  const original=PL.state.plan;
  if(original?.plan_version!==3||!PAS.caps()||!PA.caps()){setStatus("当前接口不支持多通道来源装配，请使用原有周期档案入口");return;}
  const base=UI.clone(original),baseline=PL.currentInputJson(),epoch=PL.state.sourceEpoch,path=RT.state.path,sha=PL.state.fileSha,caps=JSON.stringify(PL.state.caps);
  const abilities=base.rotation_model?.abilities||[],elemental=base.mechanics_model?.elemental_model;
  const row={schema_version:1,profile_id:UI.id("source-periodic"),ability_ids:[],version_line:"release",selection:{character_id:null,slot:"burst",constellation:null},origin_reference_id:null,
    damage_type_ids:[],scaling_stat:"",lifecycle:PAS.lifecycle(),channels:[],status:"pending",source:""};
  let closed=false,revision=0,attempt=0,shown=null,primary="",origins=[],candidates=[],query=null,readStamp=null;
  const abilityBox=UI.el("div"),selectionBox=UI.el("div"),originBox=UI.el("div"),candidateBox=UI.el("div"),channelBox=UI.el("div"),form=UI.el("div"),preview=UI.el("div"),errors=UI.el("div");
  const current=()=>!closed&&PL.state.plan===original&&!PL.state.draft&&PL.currentInputJson()===baseline&&PL.state.sourceEpoch===epoch&&RT.state.path===path&&PL.state.fileSha===sha&&JSON.stringify(PL.state.caps)===caps;
  const check=()=>{if(current())return true;setStatus("方案、文件或数据库接口已变化，请重新打开多通道装配");return false;};
  const invalidate=()=>{if(closed)return true;closed=true;attempt++;return true;};
  const dialog=window.AppDialogKernel?.create?.({title:"从数据库建立多通道档案",
    description:"选择已有爆发能力与精确来源，一次生成所选通道的原型和引用。无需先建伤害原型；不补动作、不迁移手工交付。",
    className:"mc-dialog",onRequestClose:invalidate})||null;
  if(!dialog){setStatus("当前还有窗口正在处理，请先完成或关闭后再建立多通道档案");return;}
  const close=()=>dialog.finish?.("cancel")??false;
  const stamp=()=>JSON.stringify([row.version_line,row.selection,row.origin_reference_id]);
  const changed=()=>{revision++;attempt++;shown=null;preview.innerHTML="";errors.innerHTML="";};
  const resetSource=(identity=false)=>{changed();query=null;candidates=[];readStamp=null;row.channels=[];
    if(identity){origins=[];row.origin_reference_id=null;row.lifecycle=PAS.lifecycle();row.damage_type_ids=[];row.scaling_stat="";row.status="pending";row.source="";drawForm();}
    drawOrigins();drawCandidates();drawChannels();};
  const drawAbilities=()=>{
    abilityBox.innerHTML="";const options=abilities.filter(a=>a.action_type_id==="action.elemental_burst");
    abilityBox.append(UI.field("采用来源的爆发能力",UI.select(AE.options(options,primary,a=>`${RC.abilityName(a)} · ${AE.variant(a.variant)}`),primary,id=>{
      primary=id;row.ability_ids=id?[id]:[];const selected=abilities.find(a=>a.id===id),ref=base.equipment_inputs?.[selected?.character]?.character_ref;
      row.version_line=ref?.version_line||"release";row.selection={character_id:ref?.db_character_id??null,slot:"burst",constellation:null};resetSource(true);drawAbilities();drawSelection();
    })));
    const first=abilities.find(a=>a.id===primary);if(!first)return;
    abilityBox.append(UI.el("p",`提供者：${DS.character(first.character,base.manual_template,base)}；变体：${AE.variant(first.variant)}。沿用既有能力声明。`));
    options.filter(a=>a.id!==first.id&&a.character===first.character&&a.variant===first.variant).forEach(a=>abilityBox.append(UI.check(`同时关联：${RC.abilityName(a)}`,row.ability_ids.includes(a.id),v=>{
      row.ability_ids=v?[...row.ability_ids,a.id]:row.ability_ids.filter(id=>id!==a.id);row.ability_ids.sort();changed();drawAbilities();
    })));
    abilityBox.append(UI.el("p",`关联 ${(base.rotation_model.actions||[]).filter(a=>row.ability_ids.includes(a.ability_id)).length} 个已有动作；不会补写动作、耗能或同刻次序。`));
  };
  const drawSelection=()=>{
    selectionBox.innerHTML="";const set=(key,value)=>{if(value===null)delete row.selection[key];else row.selection[key]=value;resetSource(true);};
    selectionBox.append(UI.field("资料线",UI.select([{value:"release",label:"主要资料线"},{value:"beta",label:"第二资料线"}],row.version_line,v=>{row.version_line=v;resetSource(true);})),
      UI.field("数据库角色身份",UI.input(row.selection.character_id,v=>{row.selection.character_id=v;resetSource(true);},true)),
      UI.field("实际命座",UI.input(row.selection.constellation,v=>{row.selection.constellation=v;resetSource(true);},true)),
      UI.field("有效天赋等级（零命留空默认10级）",UI.input(row.selection.talent_level,v=>set("talent_level",v),true)),
      UI.field("技能组身份（需要时填写）",UI.input(row.selection.skill_depot_id,v=>set("skill_depot_id",v),true)),
      UI.field("技能选择方式",UI.select([{value:"slot",label:"元素爆发槽"},{value:"id",label:"精确技能身份"}],Object.hasOwn(row.selection,"skill_id")?"id":"slot",v=>{
        if(v==="id"){delete row.selection.slot;row.selection.skill_id=null;}else{delete row.selection.skill_id;row.selection.slot="burst";}resetSource(true);drawSelection();
      })));
    if(Object.hasOwn(row.selection,"skill_id"))selectionBox.append(UI.field("精确技能身份",UI.input(row.selection.skill_id,v=>set("skill_id",v),true)));
  };
  const drawOrigins=()=>{
    originBox.innerHTML="";originBox.append(UI.field("精确创建来源",UI.select([{value:"",label:"请选择已读取的来源"},...origins.map((o,i)=>({value:o.origin_reference_id,label:`来源 ${i+1} · ${o.status==="ready"?"局部资料可用":"关联待核实"}`}))],row.origin_reference_id||"",v=>{row.origin_reference_id=v||null;row.lifecycle=PAS.lifecycle();row.status="pending";row.source="";resetSource();drawForm();})));
    const selected=origins.find(o=>o.origin_reference_id===row.origin_reference_id);if(selected)originBox.append(UI.details("创建来源详情",selected));
  };
  const drawCandidates=()=>{
    candidateBox.innerHTML="";
    if(!query){candidateBox.append(UI.el("p","先读取所选来源的通道，再明确勾选；即使只有一个，也不会自动采用。"));return;}
    for(const [i,c] of candidates.entries()){
      const supported=["periodic_callback_attack","ability_start_attack"].includes(c.channel_kind);
      if(supported)candidateBox.append(UI.check(`采用候选 ${i+1} · ${PA.kindName(c.channel_kind)}`,row.channels.some(ch=>ch.candidate_id===c.candidate_id),v=>{
        if(v){if(row.channels.length>=8){errors.append(UI.el("p","最多选择八个通道。","eq-error"));drawCandidates();return;}
          const identity=PA.identity(c),channel={candidate_id:c.candidate_id,schedule:PA.newSchedule(c.channel_kind==="periodic_callback_attack"?"periodic":"single"),contact_profile_id:"",default_landed:null,self_sampling:"",application_mode:"pending"};
          if(PA.identityReady(identity))PAS.rememberCounter(channel,identity.tag);
          row.channels.push(channel);PAS.verification(channel);
        }else row.channels=row.channels.filter(ch=>ch.candidate_id!==c.candidate_id);
        changed();drawChannels();
      }));
      candidateBox.append(UI.details(`候选 ${i+1} 的完整来源与待办`,c));
    }
    AE.issues(candidateBox,query.issues,"来源待办");candidateBox.append(UI.el("p","仅装配明确选中的通道。整技能仍是部分覆盖；资料关联不证明动作原生启动、终结时点或共享计数关系。"));
  };
  const drawChannels=()=>{
    channelBox.innerHTML="";
    row.channels.forEach((c,i)=>{
      const q=c.schedule,candidate=candidates.find(item=>item.candidate_id===c.candidate_id),projection=PA.projection(candidate),identity=PA.identity(candidate),identityReady=PA.identityReady(identity),rule=projection?.attenuation?.group||projection?.attenuation?.profile?.projection?.group||"待数据库候选确认",verification=PAS.verification(c),card=UI.el("section",null,"eq-card");card.append(UI.el("h4",`攻击通道 ${i+1} · ${q.kind==="periodic"?"周期接触":"单次接触"}`),UI.el("p","伤害原型、附着输出槽及关联由装配服务生成；无需预先建立。"));
      if(q.kind==="periodic")card.append(UI.field("首击距动作开始（秒）",UI.input(q.first_offset_seconds,v=>{q.first_offset_seconds=v;changed();},true)),UI.field("本次实例接触次数",UI.input(q.count,v=>{q.count=v;changed();},true)));
      else card.append(UI.field("本次攻击距动作开始（秒）",UI.input(q.offset_seconds,v=>{q.offset_seconds=v;changed();},true)));
      card.append(UI.field("通道记录精度（秒）",UI.input(q.precision_seconds,v=>{q.precision_seconds=v;changed();},true)),
        UI.field("通道校准状态",UI.select(PA.statusOptions(),q.status,v=>{q.status=v;changed();})),UI.field("通道校准依据",UI.area(q.source,v=>{q.source=v;changed();})),
        UI.field("接触档案",UI.select(AE.options(elemental?.contact_profiles||[],c.contact_profile_id,(_,j)=>`接触档案 ${j+1}`),c.contact_profile_id,v=>{c.contact_profile_id=v;changed();})),
        UI.field("默认命中判断",UI.select(AE.landedOptions,AE.landedValue(c.default_landed),v=>{c.default_landed=v===""?null:v==="true";changed();})),
        UI.field("自身属性取样",UI.select([{value:"",label:"请选择"},{value:"current",label:"每次命中时取当前属性"},{value:"action_start",label:"采用动作开始时的属性"},{value:"action_capture",label:"采用该动作明确声明的面板取样"}],c.self_sampling,v=>{c.self_sampling=v;changed();})),
        UI.field("元素附着模式",UI.select(PAS.applicationModes(),c.application_mode,v=>{if(c.counter_tag)PAS.rememberCounter(c,c.counter_tag);c.application_mode=v;delete c.counter_tag;delete c.verification;if(v==="sequence")c.counter_tag=identityReady?identity.tag:c._rememberedCounterTag||"";changed();drawChannels();})),
        ...(c.application_mode==="sequence"?[UI.field("附着计数标签（ICD Tag）",UI.input(c.counter_tag||"",v=>{c.counter_tag=v;PAS.rememberCounter(c,v);changed();})),UI.el("p",`附着规则组（数据库候选）：${rule}。${AM.identityText(candidate?.projection)}`)]:[UI.el("p",c.application_mode==="pending"?"计数标签与规则组：待核实，不会用于确定性计算。":"计数标签与规则组：此附着模式不适用。")]),
        UI.field("逐段结构化证据状态",UI.select([{value:"pending",label:identityReady&&c.application_mode==="sequence"?"数据库已确认计数身份（无需重复外部证据）":"待核实（不会伪造确认）"},{value:"confirmed",label:"已由结构化证据确认"}],verification.status,v=>{c._verificationStatus=v;changed();drawChannels();})),
        ...(c.application_mode!=="pending"&&verification.status==="confirmed"?[
          UI.field("证据类型与定位（URL、本地路径或原始 pointer）",UI.input(verification.draft.locator,v=>{verification.draft.locator=v;changed();})),
          UI.field("适用游戏版本或固定 commit",UI.input(verification.draft.version,v=>{verification.draft.version=v;changed();})),
          UI.field("检索/复核日期（YYYY-MM-DD）",UI.input(verification.draft.reviewed_at,v=>{verification.draft.reviewed_at=v;changed();})),
          UI.field("具体攻击 component_scope 与共享范围",UI.area(verification.draft.component_scope,v=>{verification.draft.component_scope=v;changed();})),
          UI.el("p",`确认字段：${c.application_mode==="sequence"?"application_mode、counter_tag、shared_scope":"application_mode、shared_scope"}。仅填写说明或 assumed 状态不会获得执行权限。`)
        ]:[]),
        UI.button(`移除通道 ${i+1}`,()=>{row.channels=row.channels.filter(other=>other!==c);changed();drawChannels();drawCandidates();}));
      channelBox.append(card);
    });
  };
  const drawForm=()=>{
    const life=row.lifecycle;form.innerHTML="";
    form.append(UI.field("伤害分类声明",UI.select([{value:"",label:"请选择实际用途"},{value:"damage.elemental_burst",label:"元素爆发伤害"}],row.damage_type_ids[0]||"",v=>{row.damage_type_ids=v?[v]:[];changed();})),
      UI.field("倍率用途声明",UI.select([{value:"",label:"尚未明确"},{value:"atk",label:"经典单一攻击力倍率，每次单击"}],row.scaling_stat,v=>{row.scaling_stat=v;changed();})),
      UI.field("实例建立距动作开始（秒）",UI.input(life.establishment_offset_seconds,v=>{life.establishment_offset_seconds=v;changed();},true)),
      UI.field("实例结束距动作开始（秒）",UI.input(life.instance_end_offset_seconds,v=>{life.instance_end_offset_seconds=v;changed();},true)),
      UI.field("生命周期记录精度（秒）",UI.input(life.precision_seconds,v=>{life.precision_seconds=v;changed();},true)),
      UI.field("生命周期核实状态",UI.select(PA.statusOptions(),life.status,v=>{life.status=v;changed();})),UI.field("生命周期依据",UI.area(life.source,v=>{life.source=v;changed();})));
    for(const [key,label] of [["owner_and_target_survive","持有者与目标在实例内存活"],["no_interrupt","没有提前中断"],["no_refresh_replacement_or_early_despawn","没有刷新替换或提前消失"]])form.append(UI.field(label,UI.select([{value:"",label:"尚未核实"},{value:"true",label:"明确成立"},{value:"false",label:"明确不成立"}],AE.landedValue(life[key]),v=>{life[key]=v===""?null:v==="true";changed();})));
    form.append(UI.field("来源采用状态",UI.select(PA.statusOptions(),row.status,v=>{row.status=v;changed();})),UI.field("来源用途与共享关系依据",UI.area(row.source,v=>{row.source=v;changed();})));
  };
  const read=async mode=>{
    if(!check())return;errors.innerHTML="";
    try{ASA.need(row.ability_ids.length,"请先选择已有爆发能力。");PAS.selectionValid(row.selection);if(mode==="channels")ASA.need(row.origin_reference_id,"请明确选择创建来源。");}catch(e){errors.append(UI.el("p",e.message,"eq-error"));return;}
    // A new discovery invalidates earlier candidates and every dependent calibration card.
    if(mode==="origins"){row.origin_reference_id=null;origins=[];}resetSource();
    const version=revision,id=++attempt,key=stamp(),req={action:"periodic_attack_profile",version_line:row.version_line,...UI.clone(row.selection)};
    if(mode==="channels")req.origin_reference_id=row.origin_reference_id;
    let response;try{response=await callApi("query_runtime",req);}catch(e){response={code:1,message:String(e)};}
    if(!current()||attempt!==id||revision!==version||stamp()!==key){if(!closed)setStatus("旧周期来源响应已忽略，请重新读取");return;}
    if(response.code){errors.append(UI.error(response));return;}
    const d=response.data,s=d?.selection,items=mode==="origins"?d?.creation_candidates:d?.candidates,field=mode==="origins"?"origin_reference_id":"candidate_id";
    const valid=s&&s.character_id===row.selection.character_id&&s.constellation===row.selection.constellation
      &&["skill_id","skill_depot_id"].every(k=>!Object.hasOwn(row.selection,k)||s[k]===row.selection[k])
      &&(!Object.hasOwn(row.selection,"talent_level")||d.level?.effective_level===row.selection.talent_level)
      &&(!row.selection.slot||s.slot===row.selection.slot||(s.matching_slots||[]).includes(row.selection.slot));
    if(d?.snapshot?.version_line!==row.version_line||!valid||d.mode!==(mode==="origins"?"origin_discovery":"channel_discovery")||!Array.isArray(items)||items.length>32
      ||items.some(c=>typeof c[field]!=="string"||!c[field])||new Set(items.map(c=>c[field])).size!==items.length
      ||mode==="channels"&&d.source_graph?.creation_bundle?.origin?.legacy_reference?.reference_id!==row.origin_reference_id){errors.append(UI.el("p","周期来源回复不完整或身份、模式不一致，未采用。","eq-error"));return;}
    if(mode==="origins"){origins=items;drawOrigins();AE.issues(originBox,d.issues,"发现待办");}
    else{query=d;candidates=items;readStamp=key;drawCandidates();}
  };
  const validate=()=>{
    ASA.need(row.ability_ids.length,"请选择已有爆发能力。");PAS.selectionValid(row.selection);
    ASA.need(row.channels.length>=1&&row.channels.length<=8&&readStamp===stamp(),"请读取当前来源并明确选择一至八个通道。");
    ASA.need(ASA.equal(row.damage_type_ids,["damage.elemental_burst"])&&row.scaling_stat==="atk","请明确本批支持的爆发分类与单攻击力用途。");
    const life=row.lifecycle;ASA.need([life.establishment_offset_seconds,life.instance_end_offset_seconds].every(PA.timeValid)&&PA.precisionValid(life.precision_seconds),"生命周期时点须为精确微秒正数、记录精度为0至1秒；未知可留空。");
    ASA.need(life.source.trim()&&row.source.trim(),"请填写生命周期及来源用途依据；未核实可明确写待补并保留 pending。");
    for(const c of row.channels){const q=c.schedule,candidate=candidates.find(item=>item.candidate_id===c.candidate_id),identity=PA.identity(candidate),identityReady=PA.identityReady(identity);
      ASA.need(candidate&&q.kind===(candidate.channel_kind==="periodic_callback_attack"?"periodic":"single"),"通道与来源候选类型不一致。");
      ASA.need(PA.timeValid(q.kind==="periodic"?q.first_offset_seconds:q.offset_seconds)&&PA.precisionValid(q.precision_seconds)&&(q.kind!=="periodic"||q.count===null||Number.isInteger(q.count)&&q.count>=1&&q.count<=256),"通道时点须为精确微秒正数、次数1至256、精度0至1秒；未知可留空。");
      ASA.need(elemental?.contact_profiles?.some(p=>p.id===c.contact_profile_id)&&["current","action_start","action_capture"].includes(c.self_sampling),"请为每个通道明确已有接触档案与自身取样方式。");
      ASA.need(PAS.applicationModes().some(mode=>mode.value===c.application_mode)&&q.source.trim(),"请填写每通道的校准依据与当前装配契约支持的附着模式。");
      if(c.application_mode==="sequence"){ASA.need((c.counter_tag||"").trim(),"有限序列模式必须填写附着计数标签。");if(identityReady)ASA.need(c.counter_tag.trim()===identity.tag,`计数标签必须与数据库确认的 attenuationTag（${identity.tag}）一致。`);}
      const evidence=PAS.syncVerification(c),needsEvidence=c.application_mode==="no_icd"||c.application_mode==="no_application"||c.application_mode==="sequence"&&!identityReady;
      if(needsEvidence)ASA.need(evidence.status==="confirmed"&&evidence.complete,"数据库未确认该附着结论时，周期通道必须提供结构化逐段证据，包括定位、版本、日期与 component_scope。");
    }
  };
  const run=async apply=>{
    if(!check())return;errors.innerHTML="";
    try{validate();ASA.need(!apply||shown===revision,"请先预览当前多通道装配草稿，再应用。");}catch(e){errors.append(UI.el("p",e.message,"eq-error"));return;}
    const submitted=UI.clone(row),version=revision,id=++attempt;let response;
    try{response=await callApi("assemble_periodic_source",UI.clone(base),submitted);}catch(e){response={code:1,message:String(e)};}
    if(!current()||attempt!==id||revision!==version){if(!closed)setStatus("旧多通道装配响应已忽略，请重新预览");return;}
    if(response.code){errors.append(UI.error(response));return;}
    let proposal;try{proposal=await PAS.checkedProposal(base,submitted,response.data);}catch(e){if(current()&&attempt===id&&revision===version)errors.append(UI.el("p",e.message,"eq-error"));return;}
    if(!current()||attempt!==id||revision!==version){if(!closed)setStatus("旧多通道装配校验已忽略，请重新预览");return;}
    const {assembly,report}=response.data,s=assembly.source_summary;preview.innerHTML="";
    preview.append(UI.el("h4","多通道装配草稿"),UI.el("p",`已选择 ${assembly.channels.length} 个通道；整源仍为部分覆盖，创建关联和游戏原生派发尚未证明。`),
      UI.el("p",s.omission_status==="known"?`本次未选入 ${s.omitted_candidate_ids.length} 个来源候选。`:"其它候选尚未核定，不能当作没有遗漏。"),
      UI.el("p","应用只写原生草稿，不接受来源或保存文件。时点、取样、逐通道附着身份和取消边界仍按声明核验。"),UI.details("生成对象映射与来源摘要",assembly));
    PA.renderReceipt(preview,report);AE.renderReceipt(preview,report,proposal);shown=revision;
    if(!apply)return;
    if(assembly.status==="created"){
      original.manual_template.hits=UI.clone(proposal.manual_template.hits);
      for(const key of ["action_event_profiles","action_event_bindings","action_event_overrides","periodic_attack_sources"])if(Object.hasOwn(proposal,key))original[key]=UI.clone(proposal[key]);
      original.resolution={state:"pending"};original.resolved_template=null;close();MC.touch();
    }else close();
    setStatus("多通道草稿已应用，尚未新增接受或保存；请在周期档案入口预览并综合确认");
  };
  dialog.appBody.append(abilityBox,selectionBox,UI.button("发现相关创建来源",()=>read("origins")),originBox,UI.button("读取所选来源的攻击通道",()=>read("channels")),candidateBox,channelBox,form,preview,errors,
    UI.button("预览多通道装配草稿",()=>run(false)),UI.button("应用多通道装配草稿",()=>run(true)),UI.button("放弃并关闭",close));
  drawAbilities();drawSelection();drawOrigins();drawCandidates();drawChannels();drawForm();
  dialog.focusInitial?.();
};
