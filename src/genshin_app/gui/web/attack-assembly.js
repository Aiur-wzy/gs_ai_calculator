"use strict";
// Assemble native declarations only. This editor never accepts a source or saves a file.
window.ASA={};
ASA.caps=()=>{const c=PL.state.caps?.attack_source_assembly_contract;return c?.contract==="source-action-assembly-v1"&&c.schema_version===1&&c.auto_accept===false?c:null;};
ASA.equal=(left,right)=>{
  if(left===right)return true;
  if(!left||!right||typeof left!=="object"||typeof right!=="object"||Array.isArray(left)!==Array.isArray(right))return false;
  const a=Object.keys(left).sort(),b=Object.keys(right).sort();
  return a.length===b.length&&a.every((key,i)=>key===b[i]&&ASA.equal(left[key],right[key]));
};
ASA.need=(condition,message)=>{if(!condition)throw Error(message);};
ASA.digest=async value=>{
  ASA.need(globalThis.crypto?.subtle&&typeof TextEncoder!=="undefined","当前界面缺少安全指纹接口，无法核对装配映射。");
  const bytes=new TextEncoder().encode(JSON.stringify(value)),hash=await crypto.subtle.digest("SHA-256",bytes);
  return Array.from(new Uint8Array(hash),v=>v.toString(16).padStart(2,"0")).join("");
};
ASA.checkedProposal=async(original,request,data)=>{
  const a=data?.assembly,plan=data?.plan,report=data?.report,fail="装配回复与请求的增量范围不一致，未应用。";
  ASA.need(a?.schema_version===1&&["created","existing"].includes(a.status)&&plan&&report,fail);
  const selected=request.ability_ids.map(id=>original.rotation_model.abilities.find(row=>row.id===id)),first=selected[0];
  ASA.need(first&&new Set(request.ability_ids).size===selected.length&&selected.every(row=>row&&row.action_type_id==="action.elemental_skill"&&row.character===first.character&&row.variant===first.variant),fail);
  ASA.need(["default","tap","hold"].includes(first.variant),fail);
  const ids=[...request.ability_ids].sort(),suffix=await ASA.digest(request.profile_id),hid="assembly.hit."+suffix,app="assembly.application."+suffix;
  ASA.need(a.profile_id===request.profile_id&&a.prototype_hit_id===hid&&a.application_profile_id===app&&a.actor===first.character&&a.variant===first.variant&&ASA.equal(a.ability_ids,ids),fail);
  for(const key of ["base_input_fingerprint","input_fingerprint"])ASA.need(typeof a[key]==="string"&&/^[0-9a-f]{64}$/.test(a[key]),fail);
  const hit={id:hid,damage_type_ids:UI.clone(request.damage_type_ids),t:0,character:first.character,skill:"元素战技",
    hit_name:{default:"默认形态命中",tap:"点按命中",hold:"长按命中"}[first.variant],damage_system:"classic",scaling_stat:request.scaling_stat,count:1};
  const profile={id:request.profile_id,kind:"calibrated_single_contact_v1",prototype_hit_id:hid,variant:first.variant,
    calibration:UI.clone(request.calibration),application_profile_id:app,contact_profile_id:request.contact_profile_id,default_landed:request.default_landed,
    self_sampling:request.self_sampling,accepted_profile_fingerprint:null};
  const source={action_event_profile_id:request.profile_id,version_line:request.version_line,selection:UI.clone(request.selection),candidate_id:request.candidate_id,
    application_mode:request.application_mode,purpose:"single_stat_damage_multiplier",status:request.status,source:request.source};
  if(request.application_mode==="sequence")source.counter_tag=request.counter_tag;
  if(request.verification)source.verification=UI.clone(request.verification);
  if(request.research_report)source.research_report=UI.clone(request.research_report);
  const bindings=ids.map(ability_id=>({ability_id,profile_id:request.profile_id})),expected=UI.clone(original);
  if(a.status==="created"){
    ASA.need(!(expected.action_event_profiles||[]).some(p=>p.id===profile.id)&&!expected.manual_template.hits.some(h=>h.id===hid)
      &&!(expected.action_event_bindings||[]).some(b=>ids.includes(b.ability_id))
      &&!(expected.mechanics_model?.elemental_model?.profiles||[]).some(p=>p.id===app),fail);
    expected.manual_template.hits.push(hit);
    (expected.action_event_profiles||=[]).push(profile);(expected.action_event_bindings||=[]).push(...bindings);
    expected.action_event_overrides||=[];(expected.attack_metadata_sources||=[]).push(source);
    expected.resolution={state:"pending"};expected.resolved_template=null;
    ASA.need(ASA.equal(a.created,{hits:1,profiles:1,bindings:bindings.length,sources:1}),fail);
  }else{
    const existing=(expected.action_event_profiles||[]).find(p=>p.id===profile.id);
    ASA.need(existing&&ASA.equal({...existing,accepted_profile_fingerprint:null},profile)
      &&ASA.equal(expected.manual_template.hits.find(h=>h.id===hid),hit)
      &&ASA.equal((expected.attack_metadata_sources||[]).find(s=>s.action_event_profile_id===profile.id),source)
      &&ASA.equal((expected.action_event_bindings||[]).filter(b=>ids.includes(b.ability_id)||b.profile_id===profile.id).sort((x,y)=>x.ability_id<y.ability_id?-1:x.ability_id>y.ability_id?1:0),bindings),fail);
    ASA.need(ASA.equal(a.created,{hits:0,profiles:0,bindings:0,sources:0}),fail);
  }
  // Compare all original fields, including old objects and cache policy, rather than trusting a returned plan.
  ASA.need(ASA.equal(plan,expected),fail);
  ASA.need(report.plan&&["ready","pending"].includes(report.state)&&ASA.equal(PL.input(report.plan),PL.input(expected)),"预览方案与装配草稿不一致，未应用。");
  const records=report.action_event_compilations,metadata=report.attack_metadata_compilations;
  ASA.need(Array.isArray(records)&&records.length===expected.action_event_profiles.length&&new Set(records.map(r=>r.profile_id)).size===records.length
    &&expected.action_event_profiles.every(p=>records.some(r=>r.profile_id===p.id)),"缺少完整动作档案预览，未应用。");
  ASA.need(Array.isArray(metadata)&&metadata.length===expected.attack_metadata_sources.length&&new Set(metadata.map(r=>r.action_event_profile_id)).size===metadata.length
    &&expected.attack_metadata_sources.every(s=>metadata.some(r=>r.action_event_profile_id===s.action_event_profile_id)),"缺少完整攻击来源预览，未应用。");
  return expected;
};
ASA.types={"damage.normal_attack":"普通攻击伤害","damage.charged_attack":"重击伤害","damage.plunging_attack":"下落攻击伤害",
  "damage.elemental_skill":"元素战技伤害","damage.elemental_burst":"元素爆发伤害","damage.reaction":"独立反应伤害","damage.other":"已明确的其他伤害"};
ASA.selectionValid=selection=>{
  ASA.need(Number.isSafeInteger(selection.character_id)&&selection.character_id>0,"请明确数据库角色身份。");
  ASA.need(Number.isInteger(selection.constellation)&&selection.constellation>=0&&selection.constellation<=6,"请填写实际命座，不会默认零命。");
  ASA.need((selection.slot==="skill")!==Object.hasOwn(selection,"skill_id"),"请采用战技槽或精确技能身份其中一种。");
  for(const key of ["skill_id","skill_depot_id"])if(Object.hasOwn(selection,key))ASA.need(Number.isSafeInteger(selection[key])&&selection[key]>0,"技能及技能组身份须为有效整数。");
  if(Object.hasOwn(selection,"talent_level"))ASA.need(Number.isInteger(selection.talent_level)&&selection.talent_level>=1&&selection.talent_level<=15,"有效天赋等级须为1至15的整数。");
};
ASA.edit=()=>{
  if(PL.state.draft){setStatus("请先应用或放弃装备草稿，再建立动作档案");return;}
  const original=PL.state.plan;
  if(original?.plan_version!==3||!ASA.caps()){setStatus("当前接口不支持来源装配，请使用原有动作档案入口");return;}
  const base=UI.clone(original),baseline=PL.currentInputJson(),epoch=PL.state.sourceEpoch,path=RT.state.path,sha=PL.state.fileSha,caps=JSON.stringify(PL.state.caps);
  const abilities=base.rotation_model?.abilities||[],elemental=base.mechanics_model?.elemental_model;
  const row={schema_version:1,profile_id:UI.id("source-action"),ability_ids:[],version_line:"release",selection:{character_id:null,slot:"skill",constellation:null},candidate_id:null,
    damage_type_ids:[],scaling_stat:"",calibration:{offset_seconds:null,precision_seconds:null,status:"pending",source:""},contact_profile_id:"",application_mode:"pending",
    default_landed:null,self_sampling:"",status:"pending",source:""};
  const verification={locator:"",version:"",reviewed_at:"",component_scope:""};
  let verificationStatus="pending",rememberedTag="",closed=false,revision=0,request=0,query=null,candidates=[],readSelection=null,shown=null,primary="";
  const abilityBox=UI.el("div"),selectionBox=UI.el("div"),candidateBox=UI.el("div"),form=UI.el("div"),preview=UI.el("div"),errors=UI.el("div");
  const current=()=>!closed&&PL.state.plan===original&&!PL.state.draft&&PL.currentInputJson()===baseline&&PL.state.sourceEpoch===epoch&&RT.state.path===path&&PL.state.fileSha===sha&&JSON.stringify(PL.state.caps)===caps;
  const invalidate=()=>{if(closed)return true;closed=true;request++;return true;};
  const dialog=window.AppDialogKernel?.create?.({title:"从数据库建立动作档案",
    description:"选择已有战技能力和攻击资料，再填写必要声明。无需先建命中原型或附着输出槽；不会自动选择候选、补动作或确认游戏事实。",
    className:"mc-dialog",onRequestClose:invalidate})||null;
  if(!dialog){setStatus("当前还有窗口正在处理，请先完成或关闭后再建立动作档案");return;}
  const close=()=>dialog.finish?.("cancel")??false;
  const check=()=>{if(current())return true;setStatus("方案、文件或数据库接口已变化，请重新打开来源装配");return false;};
  const selector=()=>JSON.stringify([row.version_line,row.selection]);
  const changed=(selection=false)=>{revision++;request++;shown=null;preview.innerHTML="";errors.innerHTML="";
    if(selection){query=null;candidates=[];readSelection=null;row.candidate_id=null;drawCandidates();}};
  const statusOptions=["pending","assumed","confirmed"].map(value=>({value,label:RC.status(value)}));
  const drawAbilities=()=>{
    abilityBox.innerHTML="";
    const possible=abilities.filter(a=>a.action_type_id==="action.elemental_skill");
    abilityBox.append(UI.field("采用来源的战技能力",UI.select(AE.options(possible,primary,a=>`${RC.abilityName(a)} · ${AE.variant(a.variant)}`),primary,value=>{
      const previous=abilities.find(a=>a.id===primary);
      primary=value;row.ability_ids=value?[value]:[];const ability=abilities.find(a=>a.id===value),ref=base.equipment_inputs?.[ability?.character]?.character_ref;
      row.version_line=ref?.version_line||"release";row.selection={character_id:ref?.db_character_id??null,slot:"skill",constellation:null};
      if(previous&&(previous.character!==ability?.character||previous.variant!==ability?.variant)){
        Object.assign(row,{damage_type_ids:[],scaling_stat:"",application_mode:"pending",status:"pending",source:"",default_landed:null,self_sampling:""});delete row.counter_tag;delete row.verification;
        rememberedTag="";verificationStatus="pending";for(const key of Object.keys(verification))verification[key]="";
        row.calibration={offset_seconds:null,precision_seconds:null,status:"pending",source:""};drawForm();
      }
      changed(true);drawAbilities();drawSelection();})));
    const first=abilities.find(a=>a.id===primary);if(!first)return;
    abilityBox.append(UI.el("p",`提供者：${DS.character(first.character,base.manual_template,base)}；变体：${AE.variant(first.variant)}。沿用能力声明，不由候选名称推断。`));
    possible.filter(a=>a.id!==first.id&&a.character===first.character&&a.variant===first.variant).forEach(a=>abilityBox.append(
      UI.check(`同时关联：${RC.abilityName(a)}`,row.ability_ids.includes(a.id),value=>{row.ability_ids=value?[...row.ability_ids,a.id]:row.ability_ids.filter(id=>id!==a.id);row.ability_ids.sort();changed();drawAbilities();})));
    abilityBox.append(UI.el("p",`当前选择关联 ${(base.rotation_model.actions||[]).filter(a=>row.ability_ids.includes(a.ability_id)).length} 个已有动作；不会添加、移动或复制动作。`));
  };
  const drawSelection=()=>{
    selectionBox.innerHTML="";
    const set=(key,value)=>{if(value===null)delete row.selection[key];else row.selection[key]=value;changed(true);};
    selectionBox.append(UI.field("资料线",UI.select([{value:"release",label:"主要资料线（沿用当前库）"},{value:"beta",label:"第二资料线"}],row.version_line,v=>{row.version_line=v;changed(true);})),
      UI.field("数据库角色身份",UI.input(row.selection.character_id,v=>{row.selection.character_id=v;changed(true);},true)),
      UI.field("实际命座",UI.input(row.selection.constellation,v=>{row.selection.constellation=v;changed(true);},true)),
      UI.field("有效天赋等级（零命留空默认10级）",UI.input(row.selection.talent_level,v=>set("talent_level",v),true)),
      UI.field("技能组身份（需要时填写）",UI.input(row.selection.skill_depot_id,v=>set("skill_depot_id",v),true)),
      UI.field("技能选择方式",UI.select([{value:"slot",label:"元素战技槽"},{value:"id",label:"精确技能身份"}],Object.hasOwn(row.selection,"skill_id")?"id":"slot",v=>{
        if(v==="id"){delete row.selection.slot;row.selection.skill_id=null;}else{delete row.selection.skill_id;row.selection.slot="skill";}changed(true);drawSelection();})));
    if(Object.hasOwn(row.selection,"skill_id"))selectionBox.append(UI.field("精确技能身份",UI.input(row.selection.skill_id,v=>{row.selection.skill_id=v;changed(true);},true)));
  };
  const drawCandidates=()=>{
    candidateBox.innerHTML="";
    if(!query){candidateBox.append(UI.el("p","先读取当前技能的候选；即使只有一个，也需要明确选择。"));return;}
    candidateBox.append(UI.field("本次采用的精确攻击候选",UI.select([{value:"",label:"尚未选择，保留待完善"},...candidates.map((c,i)=>({value:c.candidate_id,
      label:`候选 ${i+1} · ${AM.elements[c.projection?.element?.value]||"元素待核实"} · ${AM.identity(c.projection).status==="ready"?"计数标签已确认":"计数标签待核实"}`}))],row.candidate_id||"",v=>{row.candidate_id=v||null;
        const identity=AM.identity(candidates.find(c=>c.candidate_id===row.candidate_id)?.projection);if(row.application_mode==="sequence"&&identity.status==="ready"){row.counter_tag=identity.tag;rememberedTag=identity.tag;}
        changed();drawCandidates();drawForm();})),
      UI.el("p","候选只提供其已证范围的资料；不会自动确认单攻击力用途、变体或动画时点。计数标签只认 attenuationTag 或有来源的逐攻击段声明。"));
    const chosen=candidates.find(c=>c.candidate_id===row.candidate_id);if(chosen){candidateBox.append(UI.el("p",AM.identityText(chosen.projection),AM.identity(chosen.projection).status==="ready"?null:"eq-error"));
      if(chosen.projection?.attack_tag?.value)candidateBox.append(UI.el("p",`攻击标签 attackTag：${chosen.projection.attack_tag.value}；它不参与附着计数身份。`));
      AE.issues(candidateBox,chosen.issues,"候选待办");candidateBox.append(UI.details("所选候选与精确来源",chosen));}
    AE.issues(candidateBox,query.issues,"查询待办");
  };
  const read=async()=>{
    if(!check())return;errors.innerHTML="";
    try{ASA.need(row.ability_ids.length,"请先选择一个已有战技能力。");ASA.selectionValid(row.selection);}catch(error){errors.append(UI.el("p",error.message,"eq-error"));return;}
    const stamp=selector(),attempt=++request,version=revision;let response;
    try{response=await callApi("query_runtime",{action:"attack_metadata_profile",version_line:row.version_line,...UI.clone(row.selection)});}catch(error){response={code:1,message:String(error)};}
    if(!current()||attempt!==request||version!==revision||stamp!==selector()){if(!closed)setStatus("旧候选响应已忽略，请重新读取");return;}
    if(response.code){errors.append(UI.error(response));return;}
    const data=response.data,items=data?.candidates;
    const selected=data?.selection;
    if(!Array.isArray(items)||items.length>32||items.some(c=>typeof c.candidate_id!=="string"||!c.candidate_id)||new Set(items.map(c=>c.candidate_id)).size!==items.length
      ||data.snapshot?.version_line!==row.version_line||selected?.character_id!==row.selection.character_id||selected?.constellation!==row.selection.constellation
      ||["skill_id","skill_depot_id","slot"].some(key=>Object.hasOwn(row.selection,key)&&row.selection[key]!==selected?.[key])){
      errors.append(UI.el("p","候选回复不完整或角色、技能、资料线不一致，未采用。","eq-error"));return;}
    query=UI.clone(data);candidates=query.candidates;readSelection=stamp;row.candidate_id=null;changed();drawCandidates();
  };
  const validate=()=>{
    ASA.selectionValid(row.selection);ASA.need(row.ability_ids.length>0,"请选择已有能力。");
    ASA.need(readSelection===selector(),"请先读取当前技能的攻击候选。");
    ASA.need(row.scaling_stat==="atk"&&row.damage_type_ids.length>0,"请明确单攻击力用途及伤害分类；系统不会代填。");
    ASA.need(row.contact_profile_id&&elemental?.contact_profiles?.some(p=>p.id===row.contact_profile_id),"请选择已有接触档案；本入口不自动创建接触规则。");
    ASA.need(row.self_sampling==="current"||row.self_sampling==="action_start","请选择自身收益取值时点。");
    ASA.need(row.source.trim(),"请填写用途与附着模式依据。");
    ASA.need(row.application_mode!=="sequence"||typeof row.counter_tag==="string"&&row.counter_tag.trim(),"有限序列模式必须声明附着计数标签（ICD Tag）。");
    ASA.need(row.application_mode!=="pending"||row.status==="pending","待核实附着模式必须保持待核实状态。");
    const chosen=candidates.find(c=>c.candidate_id===row.candidate_id),identity=AM.identity(chosen?.projection);
    ASA.need(row.application_mode!=="sequence"||identity.status!=="ready"||row.counter_tag.trim()===identity.tag,`计数标签必须与数据库确认的 attenuationTag（${identity.tag}）一致；不能用 attackTag 或动作类别替代。`);
    const needsEvidence=(row.application_mode==="sequence"&&identity.status!=="ready")||row.application_mode==="no_icd"||row.application_mode==="no_application";
    const complete=["locator","version","reviewed_at","component_scope"].every(key=>(verification[key]||"").trim());delete row.verification;
    if(needsEvidence&&verificationStatus==="confirmed"){
      ASA.need(complete,"已确认的外部结论必须填写证据类型/定位、适用版本或commit、复核日期和具体组件范围。");
      row.verification={status:"confirmed",...UI.clone(verification),confirmed_fields:row.application_mode==="sequence"?["application_mode","counter_tag","shared_scope"]:["application_mode","shared_scope"]};
    }
    ASA.need(!needsEvidence||row.status!=="confirmed"||verificationStatus==="confirmed","已确认的附着结论必须同时提供完整的逐段结构化证据；不确定时请保持待核实或假设。");
    const c=row.calibration;ASA.need(Number.isFinite(c.offset_seconds)&&c.offset_seconds>0&&c.offset_seconds<=60&&Number.isFinite(c.precision_seconds)&&c.precision_seconds>=0&&c.precision_seconds<=1&&c.source.trim(),"请填写有效接触时点、记录精度和校准依据；状态可以保留待核实。");
  };
  const run=async apply=>{
    if(!check())return;errors.innerHTML="";
    try{validate();ASA.need(!apply||shown?.revision===revision,"请先预览当前装配草稿，再应用。");}catch(error){errors.append(UI.el("p",error.message,"eq-error"));return;}
    const submitted=UI.clone(row),attempt=++request,version=revision;let response;
    try{response=await callApi("assemble_attack_source",UI.clone(base),UI.clone(submitted));}catch(error){response={code:1,message:String(error)};}
    if(!current()||attempt!==request||version!==revision){if(!closed)setStatus("旧装配响应已忽略，请重新预览");return;}
    if(response.code){errors.append(UI.error(response));return;}
    let proposal;try{proposal=await ASA.checkedProposal(base,submitted,response.data);}catch(error){if(current()&&attempt===request&&version===revision)errors.append(UI.el("p",error.message,"eq-error"));return;}
    if(!current()||attempt!==request||version!==revision){if(!closed)setStatus("旧装配校验已忽略，请重新预览");return;}
    const report=response.data.report;preview.innerHTML="";
    preview.append(UI.el("h4","装配草稿"),UI.el("p","仅添加共享原型、档案、能力绑定和攻击来源。尚未接受来源，尚未保存文件。"),
      UI.details("本次新增对象映射",response.data.assembly));AE.renderReceipt(preview,report,proposal);shown={revision};
    if(!apply)return;
    // Only independently checked native additions are copied. Report-derived execution is display-only.
    original.manual_template.hits=UI.clone(proposal.manual_template.hits);
    for(const key of ["action_event_profiles","action_event_bindings","action_event_overrides","attack_metadata_sources"])if(Object.hasOwn(proposal,key))original[key]=UI.clone(proposal[key]);
    original.resolution={state:"pending"};original.resolved_template=null;close();MC.touch();
    setStatus("来源装配草稿已应用，尚未接受或保存；请在原动作档案入口预览并确认来源");
  };
  const drawForm=()=>{form.innerHTML="";const chosen=candidates.find(c=>c.candidate_id===row.candidate_id),databaseIdentity=AM.identity(chosen?.projection);
    const modeWarning=row.application_mode==="sequence"&&chosen&&databaseIdentity.status!=="ready"&&row.status!=="confirmed"
      ?UI.el("p","数据库没有该攻击分量的 attenuationTag。只有带版本、来源定位和采纳理由的逐段证据并标为已确认，才能执行；假设状态只生成待核实草稿。","eq-error"):null;
    const modeFields=row.application_mode==="sequence"?[UI.field("附着计数标签（ICD Tag）",UI.input(row.counter_tag||"",v=>{row.counter_tag=v;rememberedTag=v;changed();})),
      UI.el("p","计数标签决定具体攻击间的共享计数；附着规则组只提供重置与序列，attackTag 不能代替 attenuationTag。"),modeWarning]:
    [UI.el("p",row.application_mode==="no_icd"?"无 ICD 需要已确认的逐攻击组件正向证据；每次命中均允许附着。":row.application_mode==="no_application"?"无附着需要已确认的逐攻击组件正向证据；这与资料缺失不同。":"待核实会阻止确定性计算；Agent须查询这一具体攻击段。",row.application_mode==="pending"||row.status!=="confirmed"?"eq-error":null)];
    form.append(UI.field("伤害分类声明",UI.select([{value:"",label:"请选择实际伤害类型"},...Object.entries(ASA.types).map(([value,label])=>({value,label}))],row.damage_type_ids[0]||"",v=>{row.damage_type_ids=v?[v]:[];changed();})),
    UI.field("倍率用途声明",UI.select([{value:"",label:"尚未明确"},{value:"atk",label:"单一攻击力倍率"}],row.scaling_stat,v=>{row.scaling_stat=v;changed();})),
    UI.field("从动作开始到接触的秒数",UI.input(row.calibration.offset_seconds,v=>{row.calibration.offset_seconds=v;changed();},true)),
    UI.field("记录精度（秒）",UI.input(row.calibration.precision_seconds,v=>{row.calibration.precision_seconds=v;changed();},true)),
    UI.field("校准依据状态",UI.select(statusOptions,row.calibration.status,v=>{row.calibration.status=v;changed();})),
    UI.field("校准来源、固定状态和适用范围",UI.area(row.calibration.source,v=>{row.calibration.source=v;changed();})),
    UI.field("接触档案",UI.select(AE.options(elemental?.contact_profiles||[],row.contact_profile_id,(p,i)=>`接触档案 ${i+1} · ${p.freeze_interaction==="none"?"不削减冻结量":"范围待核实"}`),row.contact_profile_id,v=>{row.contact_profile_id=v;changed();})),
    UI.field("元素附着模式",UI.select(AM.applicationModes,row.application_mode,v=>{row.application_mode=v;if(v==="sequence")row.counter_tag=rememberedTag;else delete row.counter_tag;if(v==="pending")row.status="pending";changed();drawForm();})),...modeFields.filter(Boolean),
    UI.field("默认命中判断",UI.select(AE.landedOptions,AE.landedValue(row.default_landed),v=>{row.default_landed=v===""?null:v==="true";changed();})),
    UI.field("自身收益取值时点",UI.select([{value:"",label:"请选择"},{value:"current",label:"每次命中时"},{value:"action_start",label:"动作开始的前置收益后、后置收益前"}],row.self_sampling,v=>{row.self_sampling=v;changed();})),
    UI.field("用途与共享关系的核实状态",UI.select(statusOptions,row.status,v=>{row.status=v;changed();drawForm();})),
    UI.field("用途与逐攻击段附着依据（版本、来源定位、检索日期与采纳理由）",UI.area(row.source,v=>{row.source=v;changed();})),
    UI.field("逐段结构化证据状态",UI.select([{value:"pending",label:"待核实（不生成伪证据）"},{value:"confirmed",label:"已逐段核实"}],verificationStatus,v=>{verificationStatus=v;changed();drawForm();})),
    UI.field("证据类型与定位（URL、本地路径或原始 pointer）",UI.input(verification.locator,v=>{verification.locator=v;changed();})),
    UI.field("适用游戏版本或固定 commit",UI.input(verification.version,v=>{verification.version=v;changed();})),
    UI.field("检索/复核日期（YYYY-MM-DD）",UI.input(verification.reviewed_at,v=>{verification.reviewed_at=v;changed();})),
    UI.field("具体攻击 component_scope 与共享范围",UI.area(verification.component_scope,v=>{verification.component_scope=v;changed();})),
    UI.el("p",`confirmed_fields 由模式生成：${row.application_mode==="sequence"?"application_mode、counter_tag、shared_scope":row.application_mode==="pending"?"待核实模式不生成证据":"application_mode、shared_scope"}；界面不会代替你确认。`));};
  dialog.appBody.append(abilityBox,selectionBox,UI.button("读取攻击候选",read),candidateBox,form,preview,errors,
    UI.button("预览装配草稿",()=>run(false)),UI.button("应用装配草稿",()=>run(true)),UI.button("放弃并关闭",close));
  if(elemental?.ruleset!=="gauge-freeze-reference-v1")errors.append(UI.el("p","请先在元素编辑器设置并接受当前支持的冻结参考模型；本入口不会自动切换。","eq-error"));
  drawAbilities();drawSelection();drawCandidates();drawForm();dialog.focusInitial?.();
};
