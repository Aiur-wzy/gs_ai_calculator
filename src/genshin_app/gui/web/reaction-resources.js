/* Readonly reaction/resource links. No new events, damage, or source approval. */
const RR = window.RR = {};
RR.states={no_match:'未匹配，未尝试收入',unknown:'触发条件待核',planned:'已计划，尚未提交',failed:'操作失败',committed_inconsistent:'已有提交，关联待核',committed:'已提交资源操作',blocked_lifecycle:'领域资格不满足，未尝试收入'};
RR.contracts=new Set(['ordinary-reaction-resource-v1','ordinary-reaction-resource-v2']);
RR.reactions={melt:'融化',vaporize:'蒸发',swirl:'扩散'};
RR.integer=value=>Number.isSafeInteger(value)&&value>=0;
RR.qualification=(result,audit,op,o)=>{
 const gates=(audit.program?.lifecycle_gates||[]).filter(g=>g?.operation_id===op.operation_id),q=op.lifecycle_qualification;
 if(!gates.length&&q==null)return null;
 const g=gates.length===1?gates[0]:{},lives=(result.resource_reaction_execution?.mechanics_model?.state_runtime?.lifecycles||[]).filter(l=>l?.id===g.lifecycle_id),life=lives.length===1?lives[0]:{};
 const row={lifecycle_id:g.lifecycle_id,name:life.name_zh||g.lifecycle_id||'领域未记录',source_id:life.source_id,source_owner:life.owner,reason:'领域资格待核',issue:null};
 if(gates.length!==1){row.issue='领域资格与合同关联不唯一';return row;}
 const adoption=result.resource_reaction_source_adoption,bindings=(Array.isArray(adoption?.lifecycle_gates)?adoption.lifecycle_gates:[]).filter(b=>b&&b.gate&&Object.keys(b.gate).length===Object.keys(g).length&&Object.entries(g).every(([k,v])=>b.gate[k]===v)),binding=bindings.length===1?bindings[0]:{};
 if(lives.length!==1||bindings.length!==1||adoption?.contract!=='ordinary-reaction-resource-adoption-v2'||typeof life.source_id!=='string'||!life.source_id||typeof life.owner!=='string'||!life.owner||binding.lifecycle_id!==g.lifecycle_id||binding.source_id!==life.source_id||binding.source_owner!==life.owner||life.owner!==op.source_owner||binding.status!=='ready'||binding.lifecycle_source_status!=='ready'){
  row.issue='领域定义、来源与采用记录关联不一致';return row;
 }
 if(q==null&&['no_match','unknown'].includes(op.state)){row.reason='触发未匹配或待核，未检查领域';return row;}
 const expected={operation_id:op.operation_id,pool_id:op.pool_id,lifecycle_id:g.lifecycle_id,reaction_run_id:o.reaction_run_id,parent_run_id:o.parent_run_id,time_us:o.time_us,origin:o.origin,window:g.window,generation_policy:g.generation_policy,authority:'actual_completed_state2_current_generation'};
 if(!q||typeof q.qualified!=='boolean'||Object.entries(expected).some(([k,v])=>q[k]!==v)){row.issue='领域资格与实际反应关联不一致';return row;}
 Object.assign(row,{qualified:q.qualified,generation_uid:q.generation_uid,revision:q.revision,established_us:q.established_us,end_us:q.end_us});
 row.reason={eligible:'领域生效，允许尝试收入',not_started:'领域尚未创建',not_established:'领域尚未建立',ended:'领域已结束或不在有效窗口'}[q.reason]||'资格原因未分类，见原始记录';
 const t=q.time_us,created=q.created_us,start=q.established_us,end=q.end_us;
 if(!RR.integer(t)||!RR.integer(q.origin))row.issue='领域资格的反应时点或窗口身份无效';
 else if(q.reason==='not_started'){
  if(q.qualified||q.phase!=='not_started'||['generation_uid','revision','created_us','established_us','end_us'].some(k=>q[k]!==null))row.issue='未创建领域不能带有已建立的状态';
 }else if(!['eligible','not_established','ended'].includes(q.reason)||!['created','established','closing','ended'].includes(q.phase)||typeof q.generation_uid!=='string'||!q.generation_uid||!RR.integer(q.revision)||q.revision<1||![created,start,end].every(RR.integer)||!(created<=start&&start<end&&t>=created))row.issue='领域资格的状态、代标识或时间窗无效';
 else{
  const eligible=q.phase==='established'&&start<=t&&t<end,reason=eligible?'eligible':q.phase==='created'&&t<end?'not_established':'ended';
  if(q.qualified!==eligible||q.reason!==reason)row.issue='领域资格与已记录阶段、判定窗口矛盾';
 }
 if(op.state==='blocked_lifecycle'&&q.qualified||['committed','committed_inconsistent'].includes(op.state)&&!q.qualified)row.issue='领域资格与资源提交状态矛盾';
 return row;
};
RR.rows=result=>{
 const audit=result.resource_reactions,trace=result.shared_pools?.trace||[];
 if(!RR.contracts.has(audit?.contract)||![audit.occurrences,audit.operations,trace].every(Array.isArray))return [];
 const index=new Map(),claims=new Map(),rows=[];
 audit.occurrences.forEach((o,i)=>{if(o&&RR.integer(o.id)){const a=index.get(o.id)||[];a.push({i,o});index.set(o.id,a);}});
 audit.operations.forEach((op,i)=>{
  if(!op||typeof op!=='object')return;
  const matches=RR.integer(op.occurrence_id)?index.get(op.occurrence_id)||[]:[],o=matches.length===1?matches[0].o:{};
  const row={path:`/resource_reactions/operations/${i}`,operation:op,occurrence:o,receipts:[],issue:matches.length===1?null:matches.length?'反应记录不唯一':'反应记录缺失',state:RR.states[op.state]||'操作状态未说明'};
  if(audit.contract==='ordinary-reaction-resource-v2'){row.qualification=RR.qualification(result,audit,op,o);if(row.qualification?.issue)row.issue=row.issue||row.qualification.issue;}
  if(!['committed','committed_inconsistent'].includes(op.state)){if(op.actual_end!=null)row.issue='未提交状态与实际资源区间矛盾';rows.push(row);return;}
  const begin=op.ledger_begin,end=op.actual_end,count=op.expected_count;
  if(![begin,end,count].every(RR.integer)||!Array.isArray(op.keys)||op.keys.some(k=>typeof k!=='string'||!k)||new Set(op.keys).size!==op.keys.length||end<begin||end>trace.length||end-begin!==count||count!==op.keys.length){row.issue='资源流水区间不完整';rows.push(row);return;}
  const seenKeys=new Set();
  for(let j=begin;j<end;j++){
   const receipt=trace[j],expected={type:'operation',operation:'grant',phase:'post',operation_id:op.operation_id,pool_id:op.pool_id,source_id:op.source_id,run_id:o.reaction_run_id,event_id:o.reaction_event_id,t_us:o.time_us};
   if(!receipt||Object.entries(expected).some(([k,v])=>receipt[k]!==v)||!op.keys.includes(receipt.key)){row.issue='资源流水与反应操作不一致';break;}
   if(seenKeys.has(receipt.key)){row.issue='资源键重复，区间未完整覆盖声明对象';break;}seenKeys.add(receipt.key);
   let label='资源尝试，结果见原账';
   if(receipt.reason==='cooldown')label='冷却拒绝';else if(typeof receipt.accepted==='number'&&receipt.accepted>0)label='实际收入';else if(receipt.accepted===0&&receipt.reason==='granted')label='已尝试，实际收入为0';
   row.receipts.push({receipt,label,path:`/shared_pools/trace/${j}`});
   const owners=claims.get(j)||[];owners.push(row);claims.set(j,owners);
  }
  if(row.issue)row.receipts=[];
  rows.push(row);
 });
 for(const owners of claims.values())if(owners.length>1)for(const row of owners){row.issue='同一资源流水被重复引用';row.receipts=[];}
 return rows;
};
RR.render=(section,result,template)=>{
 const audit=result.resource_reactions,adoption=result.resource_reaction_source_adoption;
 if(!RR.contracts.has(audit?.contract)&&!adoption)return false;
 const rows=RR.rows(result),box=UI.el('details'),adopt={ready:'已采用',excluded:'已排除',pending:'待确认',partial:'部分采用'};
 const verified={verified:'保存记录一致',pending:'待核验',failed:'核验失败'};
 box.append(UI.el('summary',`反应资源联动（${rows.length} 次操作，只读）`),UI.el('p','按实际反应关联原资源账；请求与实际接受分开。此处不增加伤害或再次加资源。'));
 box.append(UI.el('p',`来源：${adopt[adoption?.status]||'未明确'}；发生记录：${audit?verified[audit.verification_status]||'待核验':'无执行记录'}。保存一致性不认证当前数据库或游戏事实。`));
 const prefix=result.mechanics?.complete_first_cycle===false||result.rotation_check?.damage_interpretation?.kind==='executed_prefix';
 const windowText=prefix||audit?.verified_window==='executed_prefix'?'已执行前缀':audit?.verified_window==='complete_reported_input'?'本次完整声明窗口':'范围未明确';
 if(audit)box.append(UI.el('p',`核验范围：${windowText}，不表示已验证能量循环。`));
 CF.table(box,['秒数 / 父事件','反应 / 触发角色','资源来源角色 / 来源','操作 / 资源池','状态 / 关联','原账接受量'],rows,row=>{
  const o=row.occurrence,op=row.operation,t=RR.integer(o.time_us)?o.time_us/1000000:'未记录';
  const amounts=row.receipts.map(({receipt:r,label})=>`${r.key??'未记录'}：${label}，请求 ${r.requested??'未记录'} / 接受 ${r.accepted??'未记录'} / 浪费 ${r.wasted??'未记录'}；每单位原子 ${r.unit_scale??'未记录'}`).join('；');
  return [`${t} / ${o.parent_event_id??'未记录'}`,`${RR.reactions[o.reaction_type]||'未记录'} / ${DS.character(o.owner,template)}`,`${DS.character(op.source_owner,template)} / ${op.source_id??'未记录'}`,`${op.operation_id??'未记录'} / ${op.pool_id??'未记录'}`,row.issue||row.state,amounts||'无可关联的实际资源流水'];
 });
 if(audit?.contract==='ordinary-reaction-resource-v2'){
  box.append(UI.el('p','领域资格按每次反应发生时的实际状态判断；窗口包含开始、不含结束。未来建立时间不表示已经生效。'));
  CF.table(box,['父事件 / 领域','领域来源角色 / 来源','资格 / 判定窗口','当前代 / 修订'],rows.filter(r=>r.qualification),row=>{
   const q=row.qualification,start=RR.integer(q.established_us)?q.established_us/1000000:'未记录',end=RR.integer(q.end_us)?q.end_us/1000000:'未记录';
   return [`${row.occurrence.parent_event_id??'未记录'} / ${q.name}`,`${DS.character(q.source_owner,template)} / ${q.source_id??'未记录'}`,`${q.issue||q.reason}；[${start}, ${end}) 秒`,`${typeof q.generation_uid==='string'?q.generation_uid.slice(0,28)+'…':'未记录'} / ${RR.integer(q.revision)?q.revision:'未记录'}`];
  });
 }
 box.append(UI.details('完整只读反应、来源与资源关联',{resource_reactions:audit,resource_reaction_source_adoption:adoption}));section.append(box);return true;
};
