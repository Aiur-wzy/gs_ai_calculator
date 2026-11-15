"use strict";
// Only explicit inputs and server-issued confirmations enter a native plan.
window.CAP={pageSize:50,results:new WeakMap()};
CAP.bindings=()=>PL.state.plan?.action_capacity_sources||[];
CAP.bound=id=>CAP.bindings().some(row=>row.ability_ids.includes(id));
CAP.sourceLabels={ready:"已确认",pending:"待补充"};
CAP.receiptLabels={committed:"已消费",rejected:"次数不足，拒绝施放"};
CAP.parentLabels={not_checked:"未检查次数",not_committed:"未提交",not_verified:"后续未验证"};
CAP.labels={...CAP.receiptLabels,...CAP.parentLabels};
CAP.label=(labels,value,fallback="待核实")=>typeof value==="string"&&Object.hasOwn(labels,value)?labels[value]:fallback;
CAP.integer=(value,min=0,max=100)=>Number.isInteger(value)&&value>=min&&value<=max;
CAP.time=value=>typeof value==="number"&&Number.isFinite(value)&&value>=0&&value<=1e9;
CAP.validQueue=value=>Array.isArray(value)&&value.every((v,i)=>CAP.time(v)&&(!i||v>=value[i-1]));
CAP.count=(value,min=0,max=100)=>CAP.integer(value,min,max)?value:"未记录，待核实";
CAP.text=value=>typeof value==="string"&&value.length?value:"未记录";
CAP.notice="次数及消费记录是本次计算的历史。修改装备、命座、天赋、动作、冷却或初始队列后须重新计算；最大次数不是全轴最多施放次数。";
CAP.queue=value=>CAP.validQueue(value)?value.length?value.map(v=>`${v} 秒`).join("、"):"显式空队列":Array.isArray(value)?"条件无效，待核实（原队列见完整原文）":"未记录，待核实";
CAP.conditionText=(selection={},context={})=>{
  const weapon=selection.weapon||{},value=n=>n??"待核实",variant=CAP.label({tap:"点按",hold:"长按",default:"默认形态"},selection.variant,"动作变体待核实");
  return [`角色 ${value(selection.character_level)} 级、突破 ${value(selection.ascension)}、${Array.isArray(selection.constellations)?`允许 ${selection.constellations.join("／")} 命`:`${value(selection.constellation)} 命`}；技能基级 ${value(selection.base_level)}（不含命座加级）；${variant}。`,
    `武器编号 ${value(weapon.id)}，${value(weapon.level)} 级，精炼 ${value(weapon.refinement)} 阶；${selection.artifacts==="all_empty"?"五槽均未装备圣遗物":"圣遗物存在状态待核实"}；${selection.team==="self_only"?"单人队伍":"队伍范围待核实"}。`,
    `${context.mode==="ordinary_open_world"?"普通开放世界":"场景待核实"}；${context.external_capacity_effects==="none"?"无外部容量效果":"外部容量效果待核实"}；${context.window_changes==="none"?"整个窗口无配装、命座、模式或状态重置等变化":"窗口变化条件待核实"}。`];
};
CAP.preview=(box,record,template)=>{
  if(!record){box.append(UI.el("p","请读取当前受审模型，核对实际配装、适用范围、开场状态与冷却假设后确认。"));return;}
  const p=record.projection||{},pool=p.pool||{},profile=p.profile||{},model=profile.model_record||{},claim=model.payload?.claim||{},cd=pool.members?.[0]?.cooldown||{},initial=pool.initial?.ready_in;
  const validCapacity=CAP.integer(p.capacity,1),validBase=CAP.integer(claim.base?.value,1),deltaTotal=p.capacity-claim.base?.value,
    opening=validCapacity&&CAP.validQueue(initial)&&initial.length<=p.capacity?p.capacity-initial.length:"待核实（条件无效或未记录）";
  box.append(UI.el("h4",`技能次数来源 · ${CAP.label(CAP.sourceLabels,record.status)}`),UI.el("p",`最大次数：${CAP.count(p.capacity,1)}；基础次数 ${CAP.count(claim.base?.value,1)}；激活增量 ${validCapacity&&validBase&&CAP.integer(deltaTotal,0,99)?deltaTotal:"待核实"}。`),
    UI.el("p",`来源：${model.id||"待核实"}，修订 ${model.revision??"待核实"}；适用角色 ${pool.actor?(template?DS.character(pool.actor,template):RC.owner(pool.actor)):"待核实"}，冷却组 ${pool.group||"待核实"}。`),
    UI.el("p",`开场恢复队列：${CAP.queue(initial)}；开场可用 ${opening} 次。`),
    UI.el("p",`本方案冷却假设：${CAP.time(cd.duration)?cd.duration:"待补"} 秒；${CAP.label({action_start:"动作开始",occupation_end:"占用结束"},cd.start,"起点待补")}计时；${CAP.label({sequential:"依次恢复",parallel:"各次独立恢复"},cd.recovery,"方式待补")}。容量模型不证明这些冷却参数。`));
  for(const issue of record.issues||[])box.append(UI.el("p",issue.message||"存在待核实输入","eq-error"));
  if(claim.base)box.append(UI.el("p",`基础次数来源：技能编号 ${claim.target?.skill_id??"待核实"} 的原始节点 ${claim.base.node_id??"待核实"}，字段 ${claim.base.pointer??"待核实"}。`));
  if(p.actual){box.append(UI.el("h5","本次实际选择"));CAP.conditionText(p.actual.selection,p.actual.context).forEach(text=>box.append(UI.el("p",text)));box.append(DS.raw("实际装备、命座、天赋与场景",p.actual));}
  if(claim.domain){box.append(UI.el("h5","模型允许的条件"));CAP.conditionText(claim.domain,claim.domain.context).forEach(text=>box.append(UI.el("p",text)));box.append(DS.raw("受审模型的精确适用条件",claim.domain));}
  const inventory=profile.competitor_inventory?.entries||[],required=claim.delta_refs||[];
  if(required.length){box.append(UI.el("h5","增量来源及本次激活"));for(const ref of required){const entry=inventory.find(item=>item.id===ref.id&&item.revision===ref.revision),delta=entry?.record?.payload?.claim||{},rank=delta.activation?.rank,
      disposition=entry?.disposition,active=entry?.status==="ready"&&disposition==="model_dependency_active",inactive=entry?.status==="ready"&&disposition==="model_dependency_inactive",amount=entry?.projection?.amount,
      validRank=CAP.integer(rank,1,6),consistent=validRank&&CAP.integer(amount,0,99)&&(active?amount>0:inactive&&amount===0);
    box.append(UI.el("p",`${ref.id} · 修订 ${ref.revision}：${consistent?(active?"已激活":"本次未激活"):active||inactive?"状态或数值不一致，待核实":"尚未证明激活状态"}；本次增加 ${consistent?amount:"待核实"} 次。${validRank?`来源条件为第 ${rank} 命座槽。`:"激活条件待核实。"}`));
    if(delta.source_claim?.source_path)box.append(UI.el("p",`原始来源：${delta.source_claim.source_path}${delta.source_claim.operation_pointer||""}。`));
  }}else if(claim.delta_refs)box.append(UI.el("p","当前模型没有引用额外次数增量。"));
  box.append(UI.el("p","上述模型只证明适用域内的静态最大次数；不会自动证明开场全满、冷却时长、点按与长按共池或伤害／产球。"));
  box.append(DS.raw("完整次数来源及当前审核材料",record));
};
CAP.entryDetail=(entry,template,reasons)=>{
  const row=entry.row,detail=UI.el("details",null,"capacity-ledger-row");detail.setAttribute("data-capacity-row",entry.key);
  if(entry.kind==="receipt"){
    const reason=row.reason?reasons.get(row.reason)||CAP.label({cooldown_unavailable:"技能冷却或可用次数尚未恢复"},row.reason,"请核对完整拒绝原因"):"",
      validCounts=CAP.integer(row.capacity,1)&&CAP.integer(row.before,0,row.capacity)&&CAP.integer(row.after,0,row.capacity),
      validQueues=validCounts&&CAP.validQueue(row.queue_before)&&CAP.validQueue(row.queue_after)&&row.queue_before.length===row.capacity-row.before&&row.queue_after.length===row.capacity-row.after,
      rejected=validQueues&&row.status==="rejected"&&row.before===0&&row.after===0&&row.queue_before.every((v,i)=>v===row.queue_after[i]),
      knownTransition=rejected||(validQueues&&row.status==="committed"&&row.before===row.after+1),
      ready=CAP.time(row.ready_at)?`${row.ready_at} 秒`:rejected&&row.ready_at===null?"未消费":"未记录，待核实",
      next=CAP.time(row.next_ready_at)?`${row.next_ready_at} 秒`:knownTransition&&row.next_ready_at===null&&row.queue_after.length===0&&row.after>0?"当前可用":"未记录，待核实";
    detail.append(UI.el("summary",`${CAP.time(row.t)?row.t:"未记录"} 秒 · ${DS.character(row.character,template)} · ${CAP.label(CAP.receiptLabels,row.status)} · ${CAP.count(row.before)} → ${CAP.count(row.after)} 次`),
      UI.el("p",`动作 ${CAP.text(row.action_id)}；能力 ${CAP.text(row.ability_id)}；最大 ${CAP.count(row.capacity,1)} 次；本次恢复时刻 ${ready}；下次可用 ${next}。`),
      UI.el("p",`消费前队列：${CAP.queue(row.queue_before)}；消费后队列：${CAP.queue(row.queue_after)}。${reason}`),UI.el("p",`来源绑定 ${CAP.text(row.binding_id)}。这里是实际提交或拒绝瞬间的记录，不是伤害或可编辑事件。`),DS.raw("完整消费回执",row));
  }else{const admission=row.capacity_admission;
    detail.append(UI.el("summary",`${row.time??row.t??"待核实"} 秒 · ${DS.character(row.character,template)} · 动作 ${CAP.text(row.action_id||row.id)} · ${CAP.label(CAP.parentLabels,admission.status)}`),
      UI.el("p","此前门槛或执行前缀未完成，不能推断消费前后的可用次数。"),UI.el("p",`来源绑定 ${admission.binding_id??"待核实"}；原因：${reasons.get(admission.reason)||"请核对完整未消费记录"}。`),DS.raw("完整未消费记录",row));}
  return detail;
};
CAP.ledger=(parent,check,template)=>{
  let active=true,entries=[],filtered=[],page=0,actor="",status="";
  const audit=check.action_capacity_consumption||{},receipts=audit.receipts||[],events=check.event_log||[],reasons=new Map((check.issues||[]).map(item=>[item.code,item.message]));
  receipts.forEach((row,index)=>entries.push({kind:"receipt",row,key:`receipt:${index}`,actor:row.character??null,status:row.status,time:row.t}));
  events.forEach((row,index)=>{const admission=row.capacity_admission;if(admission&&!["committed","rejected"].includes(admission.status))entries.push({kind:"parent",row,key:`parent:${index}`,actor:row.character??null,status:admission.status,time:row.time??row.t});});
  // A presentation-only index keeps original records and same-time source order.
  entries.sort((a,b)=>(Number.isFinite(a.time)?a.time:Infinity)-(Number.isFinite(b.time)?b.time:Infinity));
  const holder=UI.el("details",null,"capacity-ledger"),toolbar=UI.el("div",null,"eq-toolbar"),range=UI.el("p",null,"capacity-ledger-range"),rows=UI.el("div",null,"capacity-ledger-page"),nav=UI.el("div",null,"eq-toolbar");
  range.setAttribute("role","status");range.setAttribute("aria-live","polite");holder.append(UI.el("summary",`次数明细：${receipts.length} 条消费回执及 ${entries.length-receipts.length} 条未消费记录`));
  const actors=[...new Set(entries.map(entry=>entry.actor))],states=[...new Set(entries.map(entry=>entry.status))];
  const refresh=()=>{if(!active)return;filtered=entries.filter(entry=>(!actor||JSON.stringify(entry.actor)===actor)&&(!status||JSON.stringify(entry.status??null)===status));page=0;draw();};
  toolbar.append(UI.field("次数账本角色筛选",UI.select([{value:"",label:"全部角色"},...actors.map(value=>({value:JSON.stringify(value),label:value==null?"角色未记录":DS.character(value,template)}))],"",value=>{actor=value;refresh();})),
    UI.field("次数账本状态筛选",UI.select([{value:"",label:"全部状态"},...states.map(value=>({value:JSON.stringify(value??null),label:CAP.label(CAP.labels,value,"其他待核实状态")}))],"",value=>{status=value;refresh();})));
  const previous=UI.button("上一页",()=>{if(active&&page>0){page--;draw();}}),next=UI.button("下一页",()=>{if(active&&(page+1)*CAP.pageSize<filtered.length){page++;draw();}}),pageInfo=UI.el("span");
  const draw=()=>{if(!active)return;rows.innerHTML="";const start=page*CAP.pageSize,end=Math.min(start+CAP.pageSize,filtered.length);
    range.textContent=`原始记录共 ${entries.length} 条（消费回执 ${receipts.length} 条）；筛选后 ${filtered.length} 条；当前显示 ${filtered.length?start+1:0}–${end} 条。`;
    for(let index=start;index<end;index++)rows.append(CAP.entryDetail(filtered[index],template,reasons));
    if(!filtered.length)rows.append(UI.el("p","当前筛选没有记录。原始账本仍完整保留。"));
    previous.disabled=page===0;next.disabled=end>=filtered.length;pageInfo.textContent=`第 ${filtered.length?page+1:0} / ${Math.ceil(filtered.length/CAP.pageSize)} 页 · 每页最多 ${CAP.pageSize} 条`;
  };
  nav.append(previous,pageInfo,next);holder.append(toolbar,range,rows,nav,UI.el("p","筛选和翻页只改变显示，不改计算、总数或原始回执；每条记录可展开查看完整原文。"));parent.append(holder);refresh();
  return ()=>{active=false;entries=[];filtered=[];holder.innerHTML="";};
};
CAP.renderResult=(box,result,template)=>{
  const prior=CAP.results.get(box);if(prior){prior();CAP.results.delete(box);}
  const records=result?.action_capacity_compilations||[],check=result?.rotation_check||{},audit=check.action_capacity_consumption;
  if(!records.length&&!audit)return;
  const section=UI.el("details",null,"result-block");section.append(UI.el("summary","技能次数与实际消费"),UI.el("p",CAP.notice));
  records.forEach(record=>{const detail=UI.el("details");detail.append(UI.el("summary",`次数来源 ${record.id}`));CAP.preview(detail,record,template);section.append(detail);});
  let disposeLedger=()=>{};
  if(audit){section.append(UI.el("p",`已报告 ${audit.planned_actions??"待核实"} 个动作：已消费 ${audit.committed_actions??"待核实"}，拒绝 ${audit.rejected_actions??"待核实"}，未检查 ${audit.not_checked_actions??"待核实"}，未提交 ${audit.not_committed_actions??"待核实"}，后续未验证 ${audit.not_verified_actions??"待核实"}；窗口计划 ${audit.max_cycles_planned_actions??"待核实"}，未报告 ${audit.unreported_actions??"待核实"}。`));
    disposeLedger=CAP.ledger(section,check,template);
    if(audit.termination)section.append(UI.el("p","容量账本提前终止，不能视作完整窗口。","eq-error"),DS.raw("终止原因",audit.termination));
  }
  box.append(section);CAP.results.set(box,()=>{disposeLedger();section.innerHTML="";section.remove();});
};
CAP.ordinaryContext=()=>({contract:"static-action-capacity-context-v1",mode:"ordinary_open_world",external_capacity_effects:"none",window_changes:"none"});
CAP.contextKey=value=>value===null?"null":JSON.stringify(Object.keys(value).sort().map(key=>[key,value[key]]));
CAP.sameContext=(left,right)=>CAP.contextKey(left)===CAP.contextKey(right);
CAP.catalogError=response=>{
  const reason=response?.reason;
  if(reason==="capacity_context_conflict")return "多个次数意图的场景不一致，不能继续读取或应用；请逐项统一或清除场景。";
  if(reason==="capacity_catalog_changed"||reason==="snapshot_changed")return "容量目录在读取期间已更新，请从第一页重新读取。";
  if(reason==="capacity_catalog_cursor")return "容量目录分页已失效，请从第一页重新读取。";
  return response?.message||"容量目录读取失败。";
};
CAP.catalogGroups=[
  {key:"exact",label:"已审核 · 完全匹配",open:true,selectable:true},
  {key:"partial",label:"已审核 · 部分匹配",open:true,selectable:true},
  {key:"pending",label:"待审核或待处理",open:false},
  {key:"revoked",label:"已撤销",open:false},
  {key:"invalid",label:"合同无效",open:false},
  {key:"not_evaluated",label:"尚未评估",open:false},
  {key:"mismatch",label:"条件不匹配",open:false},
];
CAP.catalogGroup=item=>{
  if(item?.selection_status==="model_contract_invalid")return "invalid";
  if(item?.review?.status==="revoked")return "revoked";
  if(item?.review?.status!=="approved")return "pending";
  if(item?.declared_filter?.status==="not_evaluated")return "not_evaluated";
  if(item?.declared_filter?.status==="mismatch")return "mismatch";
  if(item?.selection_status!=="reference_selectable_for_fresh_preview")return "pending";
  if(item?.declared_filter?.status==="exact_match")return "exact";
  if(item?.declared_filter?.status==="partial_match")return "partial";
  return "invalid";
};
CAP.catalogSelectable=item=>item?.review?.status==="approved"&&item?.selection_status==="reference_selectable_for_fresh_preview"&&
  ["exact_match","partial_match"].includes(item?.declared_filter?.status)&&["exact","partial"].includes(CAP.catalogGroup(item));
CAP.applyCatalogSelection=(row,item)=>{
  if(!CAP.catalogSelectable(item)||!item.model_ref||!item.target)throw Error("只能选择已审核且当前条件完全或部分匹配的模型引用。");
  row.model_ref=UI.clone(item.model_ref);row.target=UI.clone(item.target);
  if(Object.hasOwn(row,"accepted_fingerprint"))row.accepted_fingerprint=null;
  return row;
};
CAP.catalogBrowser=(holder,options)=>{
  let active=true,serial=0;
  const fail=response=>{holder.innerHTML="";const error=UI.el("section",null,"capacity-catalog-error");error.setAttribute("role","alert");
    error.append(UI.el("strong","容量目录无法使用"),UI.el("p",CAP.catalogError(response)));
    if(response?.details)error.append(DS.raw("错误定位",response.details));holder.append(error);};
  const draw=(items,total)=>{holder.innerHTML="";holder.className="capacity-catalog";holder.append(UI.el("p",`目录共 ${total} 项，已完整读取 ${items.length} 项。筛选与分组只改变显示。`,"capacity-catalog-status"));
    for(const group of CAP.catalogGroups){const rows=items.filter(item=>CAP.catalogGroup(item)===group.key),section=UI.el("details",null,`capacity-catalog-group capacity-catalog-${group.key}`);
      section.open=group.open;section.append(UI.el("summary",`${group.label}（${rows.length}）`));
      if(!rows.length)section.append(UI.el("p","当前分组没有项目。","wb-note"));
      for(const item of rows){const ref=item.model_ref||{},display=item.display||{},skill=display.skill?.value||"技能名称待补",card=UI.el("article",null,"capacity-catalog-card");
        card.append(UI.el("strong",`${skill} · ${ref.id||"模型ID待补"} / 修订 ${ref.revision??"待补"}`),
          UI.el("p",`角色：${display.character?.value||item.target?.character_id||"待补"}；武器：${display.weapon?.value||item.domain?.weapon?.id||"待补"}；匹配状态：${group.label}。`));
        if(group.selectable){const choose=UI.button("选择此模型",()=>options.onSelect(item));choose.setAttribute("aria-label",`选择模型 ${ref.id||"未知ID"}`);card.append(choose);}
        const advanced=UI.el("details",null,"capacity-catalog-advanced");advanced.append(UI.el("summary","高级详情与原始 ID"),
          UI.el("p",`模型 ID：${ref.id||"待补"}；修订：${ref.revision??"待补"}；角色 ID：${item.target?.character_id??"待补"}；技能组 ID：${item.target?.skill_depot_id??"待补"}；技能 ID：${item.target?.skill_id??"待补"}。`),
          DS.raw("完整只读目录记录",item));card.append(advanced);section.append(card);}
      holder.append(section);}
  };
  const countMetadata=page=>{const counts=page.counts;
    if(!counts||typeof counts!=="object"||Array.isArray(counts)||!CAP.integer(counts.filtered_models,0,Number.MAX_SAFE_INTEGER))return null;
    const entries=Object.entries(counts);if(entries.some(([,value])=>!CAP.integer(value,0,Number.MAX_SAFE_INTEGER)))return null;
    if(Object.hasOwn(counts,"returned")&&counts.returned!==page.items.length)return null;
    return {total:counts.filtered_models,stable:JSON.stringify(entries.filter(([key])=>key!=="returned").sort(([left],[right])=>left.localeCompare(right)))};
  };
  const reload=async()=>{const attempt=++serial;holder.innerHTML="";const loading=UI.el("p","正在读取全部容量目录…","capacity-catalog-status");loading.setAttribute("role","status");holder.append(loading);
    let cursor=null,total=null,countKey=null,items=[],seen=new Set();
    try{do{if(!active||attempt!==serial||!options.current())return false;
      const response=await callApi(options.method,UI.clone(options.document()),options.actor(),[...options.memberIds()],cursor,16,"all");
      if(!active||attempt!==serial||!options.current())return false;if(response.code){fail(response);return false;}
      const page=response.data;if(!page||!Array.isArray(page.items)||typeof page.has_more!=="boolean"){fail({message:"容量目录返回格式无效。"});return false;}
      const metadata=countMetadata(page);if(!metadata){fail({message:"容量目录计数元数据无效。"});return false;}
      if(total===null){total=metadata.total;countKey=metadata.stable;}else if(metadata.total!==total||metadata.stable!==countKey){fail({message:"容量目录分页计数不一致，请从第一页重新读取。"});return false;}
      items.push(...page.items);if(items.length>total){fail({message:`容量目录计数不一致：应有 ${total} 项，实际已读取 ${items.length} 项。`});return false;}
      if(!page.has_more){if(page.next_cursor!==null){fail({message:"容量目录分页游标无效。"});return false;}cursor=null;break;}
      if(!page.items.length||typeof page.next_cursor!=="string"||!page.next_cursor||page.next_cursor===cursor||seen.has(page.next_cursor)){fail({message:"容量目录分页游标无效。"});return false;}
      seen.add(page.next_cursor);cursor=page.next_cursor;
    }while(cursor);
    if(total!==items.length){fail({message:`容量目录计数不一致：应有 ${total} 项，实际读取 ${items.length} 项。`});return false;}draw(items,total);return true;
    }catch(error){if(active&&attempt===serial&&options.current())fail({message:`容量目录读取失败：${error.message}`});return false;}};
  return {reload,invalidate(){serial++;holder.innerHTML="";},dispose(){active=false;serial++;holder.innerHTML="";}};
};
CAP.propose=(original,row,index,{takeover=false,contextConfirmed=false,context,initial}={})=>{
  const plan=UI.clone(original),abilities=plan.rotation_model?.abilities||[];
  if(!row.ability_ids.length||new Set(row.ability_ids).size!==row.ability_ids.length)throw Error("请明确选择所有共用此次数池的能力，不能重复。");
  const members=row.ability_ids.map(id=>abilities.find(a=>a.id===id));
  if(members.some(a=>!a||a.character!==row.actor||a.action_type_id!=="action.elemental_skill"||a.variant!=="tap"||!a.cooldown))throw Error("当前模型仅支持明确点按战技；请先核实动作种类、变体及冷却参数。");
  const group=members[0].cooldown.group;
  if(!group||members.some(a=>a.cooldown.group!==group))throw Error("绑定成员必须明确属于同一角色、同一冷却组。");
  if(abilities.filter(a=>a.character===row.actor&&a.cooldown?.group===group).some(a=>!row.ability_ids.includes(a.id)))throw Error("请列全该冷却组的所有本地能力；不可遗漏共池成员。");
  if((plan.action_resource_sources||[]).some(source=>row.ability_ids.includes(source.ability_id)))throw Error("原动作资源来源已经拥有次数，请先处理双重拥有。");
  if(members.some(a=>Object.hasOwn(a.cooldown,"charges"))&&!takeover)throw Error("原能力有手填次数；请明确勾选将次数交由受审模型填写。");
  const scene=context===undefined?(contextConfirmed?CAP.ordinaryContext():null):context;
  if(!scene)throw Error("次数场景未确认；请明确使用普通开放世界预设后再预览或应用。");
  if(!row.model_ref?.id||!Number.isInteger(row.model_ref.revision)||!Number.isInteger(row.target?.character_id)||!Number.isInteger(row.target?.skill_depot_id)||!Number.isInteger(row.target?.skill_id))throw Error("请先从容量目录选择一个可用模型。");
  for(const ability of members)delete ability.cooldown.charges;
  plan.action_capacity_context=UI.clone(scene);
  plan.action_capacity_sources ||= [];if(index===undefined)plan.action_capacity_sources.push(UI.clone(row));else plan.action_capacity_sources[index]=UI.clone(row);
  if(initial!==undefined){plan.rotation_model.initial_cooldowns=(plan.rotation_model.initial_cooldowns||[]).filter(x=>!(x.character===row.actor&&x.group===group));
    if(initial!==null)plan.rotation_model.initial_cooldowns.push({character:row.actor,group,ready_in:UI.clone(initial)});}
  return plan;
};
CAP.edit=index=>{
  if(PL.state.draft){setStatus("请先应用或放弃装备草稿");return;}const original=PL.state.plan;
  if(original?.plan_version!==3){setStatus("受审技能次数需要原生版本3方案");return;}
  const abilities=original.rotation_model?.abilities||[],existing=CAP.bindings()[index],first=existing?abilities.find(a=>a.id===existing.ability_ids[0]):abilities.find(a=>a.action_type_id==="action.elemental_skill"&&!CAP.bound(a.id));
  if(!first){setStatus("请先建立点按战技定义；本页不自动猜测技能或创建动作。");return;}
  const gear=original.equipment_inputs?.[first.character],ref=gear?.character_ref;
  let row=existing?UI.clone(existing):{id:UI.id("capacity"),actor:first.character,ability_ids:[first.id],version_line:ref?.version_line||"release",model_ref:{id:"",revision:1},target:{character_id:ref?.db_character_id??null,skill_depot_id:null,skill_id:null,variant:"tap"},accepted_fingerprint:null},
    takeover=false,context=original.action_capacity_context?UI.clone(original.action_capacity_context):null,contextForAll=!!original.action_capacity_context,
    initial=UI.clone(original.rotation_model.initial_cooldowns?.find(x=>x.character===first.character&&x.group===first.cooldown?.group)?.ready_in??null),closed=false,revision=0,request=0,shown=null;
  const totalIntents=CAP.bindings().length+(existing?0:1),baseline=PL.currentInputJson(),epoch=RC.sourceEpoch(),path=RT.state.path,sha=PL.state.fileSha,current=()=>!closed&&PL.state.plan===original&&PL.currentInputJson()===baseline&&RC.sourceEpoch()===epoch&&RT.state.path===path&&PL.state.fileSha===sha&&!PL.state.draft;
  let browser=null;
  const form=UI.el("div"),selected=UI.el("section",null,"capacity-selected"),scene=UI.el("section",null,"capacity-scene"),catalog=UI.el("section",null,"capacity-catalog-host"),preview=UI.el("div"),errors=UI.el("div");
  const invalidate=()=>{if(closed)return true;closed=true;request++;browser?.dispose();return true;};
  const dialog=window.AppDialogKernel?.create?.({title:"受审技能最大次数",
    description:"目录只提供模型引用和精确目标，不返回或写入容量值。选择后仍需预览、确认并由后端重新核验。",
    className:"capacity-catalog-dialog",onRequestClose:invalidate})||null;
  if(!dialog){setStatus("当前还有窗口正在处理，请先完成或关闭后再编辑技能次数来源");return;}
  const close=()=>{invalidate();return dialog.finish?.("cancel")??true;};
  const validIdentity=()=>typeof row.model_ref?.id==="string"&&row.model_ref.id.length>0&&Number.isInteger(row.model_ref.revision)&&
    Number.isInteger(row.target?.character_id)&&Number.isInteger(row.target?.skill_depot_id)&&Number.isInteger(row.target?.skill_id);
  const clearIdentity=()=>{row.model_ref={id:"",revision:1};row.target={character_id:ref?.db_character_id??null,skill_depot_id:null,skill_id:null,variant:"tap"};row.accepted_fingerprint=null;};
  const renderSelected=()=>{selected.innerHTML="";selected.append(UI.el("h4","当前模型引用"),UI.el("p",row.model_ref?.id?`${row.model_ref.id} / 修订 ${row.model_ref.revision}；技能 ID ${row.target?.skill_id??"待补"}`:"尚未从目录选择模型。"));};
  const changed=refresh=>{revision++;request++;shown=null;row.accepted_fingerprint=null;preview.innerHTML="";errors.innerHTML="";renderSelected();if(refresh&&browser){browser.invalidate();
      if(row.ability_ids.length)browser.reload();else catalog.append(UI.el("p","请至少选择一个元素战技能力。","wb-note"));}};
  const setContext=(next,forAll)=>{const semanticChange=!CAP.sameContext(context,next);context=next===null?null:UI.clone(next);contextForAll=forAll;
    if(semanticChange){clearIdentity();changed(true);}renderScene();};
  const renderScene=()=>{scene.innerHTML="";scene.append(UI.el("h4","次数场景"),UI.el("p",context?"当前项使用：普通开放世界；无外部次数加成，窗口内无装备、命座、模式或状态重置变化。":"次数场景未确认","capacity-scene-status"));
    scene.append(UI.button("一键使用普通开放世界预设",()=>setContext(CAP.ordinaryContext(),totalIntents===1||!!original.action_capacity_context)));
    if(totalIntents>1)scene.append(UI.button(`将预设应用到全部 ${totalIntents} 个次数意图`,()=>setContext(CAP.ordinaryContext(),true)));
    scene.append(UI.button("清除场景",()=>setContext(null,false)));};
  const field=(title,value,save,numeric=false)=>form.append(UI.field(title,UI.input(value,v=>{save(v);changed(false);},numeric)));
  for(const ability of abilities.filter(a=>a.character===row.actor&&a.action_type_id==="action.elemental_skill"))form.append(UI.check(`绑定 ${RC.abilityName(ability)} · ${ability.id}`,row.ability_ids.includes(ability.id),v=>{row.ability_ids=row.ability_ids.filter(id=>id!==ability.id);if(v)row.ability_ids.push(ability.id);clearIdentity();changed(true);}));
  form.append(UI.check("将所选能力的手填次数交由受审模型填写",false,v=>{takeover=v;changed(false);}),UI.check("明确开场次数全部就绪",Array.isArray(initial)&&!initial.length,v=>{initial=v?[]:null;changed(false);}));
  field("初始未恢复次数的剩余秒数（逗号分隔；空白为未知）",initial?.join(",")||"",v=>{initial=v.trim()?v.split(/[,，]/).map(Number):null;});
  const catalogDocument=()=>{const plan=UI.clone(original);if(context)plan.action_capacity_context=UI.clone(context);else delete plan.action_capacity_context;
    plan.action_capacity_sources=(plan.action_capacity_sources||[]).filter((_item,i)=>i!==index);if(validIdentity())plan.action_capacity_sources.push(UI.clone(row));
    if(!plan.action_capacity_sources.length)delete plan.action_capacity_sources;return plan;};
  browser=CAP.catalogBrowser(catalog,{document:catalogDocument,actor:()=>row.actor,memberIds:()=>row.ability_ids,method:"action_capacity_model_candidates",current,
    onSelect:item=>{CAP.applyCatalogSelection(row,item);changed(false);setStatus("已选择目录模型；尚未写入方案或容量值。");}});
  const run=async mode=>{
    if(!current()){setStatus("方案已变化，请重新打开次数来源编辑");return;}errors.innerHTML="";let proposed;
    if(context&&totalIntents>1&&!original.action_capacity_context&&!contextForAll){errors.append(UI.el("p","原生方案只有一个全局次数场景。当前预设只属于本项；请明确点击“应用到全部”后再写入方案。","eq-error"));return;}
    try{proposed=CAP.propose(original,row,index,{takeover,context,initial});}catch(error){errors.append(UI.el("p",error.message,"eq-error"));return;}
    const prior=shown,attempt=++request,version=revision,valid=()=>attempt===request&&version===revision&&current();
    if(mode==="confirm"&&!prior){errors.append(UI.el("p","请先读取来源并核对预览。","eq-error"));return;}
    let response;try{response=await callApi("compose_equipment_plan",proposed);}catch(error){if(valid())errors.append(UI.el("p",`读取失败：${error.message}`,"eq-error"));return;}
    if(!valid()){setStatus("旧次数来源响应已忽略，请重新读取");return;}if(response.code){errors.append(UI.error(response));return;}
    const record=response.data.action_capacity_compilations?.find(item=>item.id===row.id),fp=record?.required_confirmation?.accepted_fingerprint;
    preview.innerHTML="";CAP.preview(preview,record);shown=typeof fp==="string"&&/^[0-9a-f]{64}$/.test(fp)?fp:null;
    if(mode==="preview")return;
    if(mode==="confirm"){if(!shown||shown!==prior){row.accepted_fingerprint=null;errors.append(UI.el("p","来源尚未就绪或已变化，请核对新预览后再次确认。","eq-error"));return;}row.accepted_fingerprint=shown;setStatus("已确认所示次数来源，应用时再次核验当前资料。");return;}
    if(response.data.state==="invalid"||!record||row.accepted_fingerprint!==shown||record.status!=="ready"){errors.append(UI.el("p","当前次数来源仍未确认或已变化；请补齐实际装备与初始状态，再读取、确认和应用。","eq-error"));return;}
    original.action_capacity_context=proposed.action_capacity_context;original.action_capacity_sources=proposed.action_capacity_sources;original.rotation_model=proposed.rotation_model;close();RC.touch();setStatus("受审次数来源已应用，请重新计算并检查真实消费。");
  };
  renderSelected();renderScene();dialog.appBody.append(form,selected,scene,UI.el("h4","容量模型目录"),UI.button("重新读取全部目录",()=>browser.reload()),catalog,errors,preview,
    UI.button("读取并预览",()=>run("preview")),UI.button("确认所示来源",()=>run("confirm")),UI.button("应用次数来源",()=>run("apply")),UI.button("返回且不应用",close));
  dialog.focusInitial?.();browser.reload();
};
CAP.render=box=>{if(PL.state.plan?.plan_version!==3)return;const bindings=CAP.bindings(),unconfirmed=PL.state.plan.action_capacity_context?0:bindings.length,section=UI.el("details",null,"result-block");
  section.append(UI.el("summary",`受审技能次数来源（${bindings.length}）`),UI.el("p","目录选择只写模型引用与精确目标；容量值仍由后端预览和计算生成。冷却与开场队列须明确。"),
    UI.el("p",unconfirmed?`次数场景未确认 · ${unconfirmed} 个次数意图`:bindings.length?"次数场景已确认":"次数场景未确认 · 当前没有次数意图","capacity-scene-status"),UI.button("绑定受审最大次数",()=>CAP.edit()));
  bindings.forEach((row,index)=>section.append(UI.el("p",`${RC.owner(row.actor)} · ${row.model_ref.id} / 修订 ${row.model_ref.revision}`),UI.button("核对或修改次数来源",()=>CAP.edit(index))));box.append(section);};
