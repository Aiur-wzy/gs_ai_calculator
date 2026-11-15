/* Read-only executed counter observations. No timeline events or extra damage. */
const CF = window.CF = {};
CF.isReport=ledger=>ledger?.contract==='resource-counter-views-self-frame-v2';
CF.mode=row=>row.frame_selection==='historical_reference'?'输出快照':row.frame_selection==='current_no_self_reference'?'当前累计（未指定输出快照）':row.domain==='provider'&&row.sample==='current'?'提供者当前累计':'取样口径未说明';
CF.gate=row=>{
  if(!Object.hasOwn(row,'gate_qualification')&&!Object.hasOwn(row,'frame_selection')&&row.domain==='provider'&&row.sample==='current')return '提供者当前取样（资格见原资源读数）';
  const gate=row.gate_qualification||{};
  if(!['event_run_id','instance_id'].every(k=>typeof gate[k]==='string'&&gate[k]))return '门槛回执不完整';
  if(gate.event_run_id!==row.run_id||gate.instance_id!==row.instance_id)return '门槛关联不一致';
  if(gate.kind==='not_required')return '不需要次数门槛';
  if(gate.kind==='shared_pool'&&typeof gate.consumer_id==='string'&&gate.consumer_id)return `当前共享资源消费已通过：${gate.consumer_id}`;
  if(gate.kind==='quota'&&typeof gate.rule_id==='string'&&gate.rule_id)return `当前有限次数已通过：${gate.rule_id}`;
  return '门槛未说明';
};
CF.index=rows=>{const map=new Map();for(const row of rows){if(typeof row.frame_id==='string'){const list=map.get(row.frame_id)||[];list.push(row);map.set(row.frame_id,list);}}return map;};
CF.capture=(row,index)=>{
  if(row.frame_selection!=='historical_reference')return '无需历史捕获';
  const matches=index.get(row.frame_id)||[];
  if(matches.length!==1)return matches.length?'捕获回执不唯一':'捕获回执缺失';
  const context=row.capture_context,actual=matches[0].context;
  if(!context||!actual||Object.keys(context).length!==Object.keys(actual).length||Object.keys(context).some(k=>context[k]!==actual[k]))return '捕获关系不一致';
  const time=Number.isSafeInteger(context.time_us)?`${context.time_us/1000000}秒`:'时点未说明';
  return `${time} / ${context.event_id??'事件未说明'} / ${row.frame_id}`;
};
CF.table=(box,headers,rows,values)=>{
  const table=UI.el('table',null,'grid'),head=UI.el('tr');headers.forEach(v=>head.append(UI.el('th',v)));table.append(head);
  for(const row of rows.slice(0,250)){const tr=UI.el('tr');values(row).forEach(v=>tr.append(UI.el('td',v??'—')));table.append(tr);}
  const scroll=UI.el('div',null,'table-scroll');scroll.append(table);box.append(scroll);
  if(rows.length>250)box.append(UI.el('p',`已显示前 250 条，共 ${rows.length} 条。`));
};
CF.render=(section,result,template)=>{
  const ledger=result.resource_counter_views;if(!CF.isReport(ledger))return false;
  const capture=ledger.historical_capture,frames=capture&&typeof capture==='object'?capture.capture_trace||[]:[],index=CF.index(frames),rows=ledger.trace||[],box=UI.el('details');
  box.append(UI.el('summary',`资源读数流水（${rows.length} 条，只读）`),UI.el('p','历史值与当前消费资格分开。合成收益已包含层数，此处不额外增加伤害。'));
  CF.table(box,['秒数','规则 / 收益','接收角色','取样口径 / 历史捕获','当前次数门槛','原始原子 / 每单位原子','采用读数','单层值 / 层数 / 合成收益'],rows,row=>{
    const read=row.reading||{},layers=row.frame_selection==='historical_reference'?'捕获时冻结层数':'当前层数';
    return [row.t,`${row.rule_id??'—'} / ${row.effect_id??'—'}`,DS.character(row.character,template),`${CF.mode(row)} / ${CF.capture(row,index)}`,CF.gate(row),
      `${read.raw_atoms??'—'} / ${read.unit_scale??'—'}`,row.source_value,`${row.single_value??row.value??'—'} / ${layers} ${row.layer_count??'—'} / ${row.combined_value??'—'}`];
  });
  box.append(UI.details('完整资源读数与当前消费资格',ledger));section.append(box);
  if(capture&&typeof capture==='object'){
    const frameBox=UI.el('details');frameBox.append(UI.el('summary',`累计输出快照（${frames.length} 次，只读）`),UI.el('p','含附加检查窗口。捕获成功不代表后续命中已通过当前次数门槛；不增加伤害。'));
    CF.table(frameBox,['秒数','来源窗口','捕获事件 / 执行标识','快照标识','冻结成员数 / 独立读数数'],frames,row=>[row.t,row.cycle,`${row.event_id??'—'} / ${row.run_id??'—'}`,row.frame_id,`${row.member_count??'—'} / ${row.reading_count??'—'}`]);
    frameBox.append(UI.el('p',`快照存储：${capture.snapshot_storage_bytes??'—'} 字节；实际编码检查：${capture.verification_bytes??'—'} 字节（含失败准备，不等于捕获成功次数）。`),UI.details('完整只读捕获账与工作限制',capture));section.append(frameBox);
  }
  return true;
};
