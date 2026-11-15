import {type TimelineDomPorts,createTimelineDom} from "./dom";
import {renderDamageExplanation} from "./damage-detail";
export function createTimelineDetail(ports:TimelineDomPorts){
  const TV=ports.view,RT=ports.editor,$=ports.query,TD=ports.projection,services=ports.services;
  const {viewKind,viewName,viewStatus,viewEl,viewSeconds,viewTime}=createTimelineDom(ports);
  const LABEL_W=210,RULER_H=44;
const showDetail = (selection:any,back:any=null,page=0) => {
  const box=$("#timeline-detail");if(!box)return;
  // 只读详情不应保留上一次手工事件的编辑卡；选择状态不是方案输入。
  if(!["hit","buff","switch"].includes(selection.edit?.kind) && RT.sel()){
    RT.state.selection=null;
    RT.updateTimelineSelection();
    RT.renderPropPanel();
  }
  box.innerHTML="";TV.detailSelection=selection;TV.updateDetailSelection();
  services.workspace?.reveal?.("rotation","right");
  const button=(label:any,fn:any)=>{const n=viewEl("button",label);n.type="button";n.addEventListener("click",fn);return n;};
  if(selection.events){
    const events=selection.events, size=40, pages=Math.ceil(events.length/size);
    box.appendChild(viewEl("div","密集时间点","tl-detail-eyebrow"));
    box.appendChild(viewEl("h4",`${events.length} 条聚合记录 · 第 ${page+1}/${pages} 页`));
    if(TV.mode==="damage"&&events.every((event:any)=>["damage","external_damage"].includes(event.kind)&&Number.isFinite(event.value)))
      box.appendChild(viewEl("p",`本组伤害合计：${Math.round(events.reduce((sum:any,event:any)=>sum+event.value,0)).toLocaleString("zh-CN")}`,"tl-detail-damage"));
    box.appendChild(viewEl("p","选择一条记录查看详情；放大时间轴可分开相邻时间点。","tl-detail-hint"));
    for(const event of events.slice(page*size,(page+1)*size)){
      const entry=button(`${viewTime(event)} · ${viewName(event.label)}${event.beneficiary_names?.length?` · ${event.beneficiary_names.join("、")}`:""}${TV.mode==="damage"&&Number.isFinite(event.value)?` · ${Math.round(event.value).toLocaleString("zh-CN")}`:""}`,
        ()=>TV.showDetail(event,{selection,page}));entry.className="tl-detail-record";box.appendChild(entry);
    }
    if(page>0)box.appendChild(button("上一页",()=>TV.showDetail(selection,null,page-1)));
    if(page+1<pages)box.appendChild(button("下一页",()=>TV.showDetail(selection,null,page+1)));
    return;
  }
  const event=selection;
  if(back?.selection)box.appendChild(button("← 返回聚合记录",()=>TV.showDetail(back.selection,null,back.page)));
  if(back?.event)box.appendChild(button("← 返回伤害事件",()=>TV.showDetail(back.event,back.back)));
  box.appendChild(viewEl("div",`${viewKind(event.kind).label} · ${event.edit?"输入可编辑":"只读记录"}`,"tl-detail-eyebrow"));
  box.appendChild(viewEl("h4",viewName(event.label)));
  const explanation=TV.mode==="effects" && !event.detail?.special_state_contract && services.effects && services.display?.renderEffectDescription
    ? services.effects.describe(event,{characterName:(id:any)=>TV.characterNames?.get(id)||"未指定角色"}) : null;
  if(explanation)box.appendChild(services.display.renderEffectDescription(explanation));
  const card=viewEl("div",null,"tl-detail-card"),fields=viewEl("dl",null,"tl-detail-fields");
  const field=(label:any,value:any)=>{if(value==null||value==="")return;fields.append(viewEl("dt",`${label}：`),viewEl("dd",String(value)));};
  if(!explanation && services.effects && event.detail?.beneficiaries_scope?.eligibility){
    for(const line of services.effects.eligibilityDescription(event.detail.beneficiaries_scope,(id:any)=>TV.characterNames?.get(id)||"未指定角色"))field("受益资格",line);
    for(const line of services.effects.selectionDescription(event.detail.beneficiary_selection,(id:any)=>TV.characterNames?.get(id)||"未指定角色"))field("实际选人",line);
  }
  field("角色",TV.characterNames?.get(event.character)||"全队 / 未指定角色");
  field("时间",viewTime(event));
  if(event.detail?.special_state_contract){
    if(Number.isFinite(event.detail.next_settlement_at))field("下次领域结算",`${viewSeconds(event.detail.next_settlement_at)}秒`);
    field("适用说明","领域状态显示不等于该角色每击必然受益，实际收益以逐击计算回执为准。");
    field("层数用途",event.detail.kind==="field_accumulator"?"窗口内累计，等待结算":"最近完成结算，供伤害读取");
    field("本段层数",event.detail.layers);
    field("伤害基础系数",event.detail.system_factor);
    if(Number.isFinite(event.detail.ordinary_cryo_electro_bonus))
      field("普通冰／雷增益",`${Number((event.detail.ordinary_cryo_electro_bonus*100).toFixed(3))}%`);
    field("形成原因",({establish_or_refresh_field:"领域建立或刷新",count_successful_application:"有效附着计层",
      settle_four_second_window:"四秒窗口完成结算"} as Record<string,string>)[event.detail.cause]||"已记录状态变化");
    field("结束原因",({layer_change:"层数变化",refresh:"领域刷新",settlement_clear:"窗口结算并清空累计",
      settlement:"下一窗口结算",field_expiry:"领域到期",buff_expiry:"结算增益到期",window_end:"到达轴末",
      execution_limit:"执行中断",state_observation_incomplete:"后续状态资料不完整",declared_expiry_unobserved:"已声明期限到达，缺少后续观察"} as Record<string,string>)[event.detail.end_reason]||"状态结束");
  }
  if(Number.isFinite(event.end)&&event.end>event.start)field("区间长度",`${viewSeconds(event.end-event.start)}秒（结束时刻不含）`);
  if(!explanation)field("来源",viewName(event.sourceLabel));
  if(event.element)field("元素",services.display?.label?.("elements",event.element)||event.element);
  if(event.detail?.execution_status)field("执行状态",viewStatus(event.detail.execution_status));
  if(event.kind==="coverage"&&event.beneficiary_names?.length)field(event.detail?.receipt_id?"实例角色范围":"接收角色",event.beneficiary_names.join("、"));
  if(event.detail?.presentation?.purpose==="calculation_sampling"){
    field("展示用途","作者标记的计算采样窗口，非游戏Buff寿命");field("原因",event.detail.presentation.reason);
  }
  if(event.kind==="coverage"){
    if(event.detail?.state_status)field("区间状态",TD.stateName(event.detail.origin==="declared_interval"?"declared":event.detail.state_status));
    if(event.detail?.remaining_quota!=null)field("本段剩余次数",event.detail.remaining_quota);
    if(Number.isFinite(event.detail?.resource_balance?.remaining)){const balance=event.detail.resource_balance,scale=Number.isFinite(balance.unit_scale)&&balance.unit_scale>0?balance.unit_scale:1;
      field("本段共享库存",`${viewSeconds(balance.remaining/scale)}${Number.isFinite(balance.required)?`（下一次需要 ${viewSeconds(balance.required/scale)}）`:""}`);}
    if(event.detail?.layers!=null)field("本段层数",event.detail.layers);
    if(event.detail?.end_reason)field("区间结束原因",TD.endReason(event.detail.end_reason));
  }
  const damage=TV.mode==="damage"?(Number.isFinite(event.value)?event.value:event.detail?.hit?.expected):null;
  if(Number.isFinite(damage))card.appendChild(viewEl("p",`期望伤害：${Math.round(damage).toLocaleString("zh-CN")}`,"tl-detail-damage"));
  card.appendChild(fields);box.appendChild(card);
  if(TV.mode==="damage"&&["damage","calculation","external_damage"].includes(event.kind))
    renderDamageExplanation(box,event,ports);
  if(event.kind==="coverage"){
    if(event.detail?.beneficiaries_scope?.scope==="current_frontline")box.appendChild(viewEl("p","该区间表示效果已激活，只有符合前台限制的角色命中时才受益。"));
    for(const scope of event.detail?.effect_applicability||[])if(scope.message)box.appendChild(viewEl("p",`收益作用范围：${scope.message}`));
  }
  if(TV.mode==="damage"&&event.kind==="damage"&&event.detail?.hit_id){
    const related=TV.projection.views.damage.events.find((row:any)=>row.kind==="calculation"&&row.detail?.hit_id===event.detail.hit_id&&row.start===event.start);
    if(related)box.appendChild(button("查看这次伤害的计算明细",()=>TV.showDetail(related,{event,back})));
  }
  if(event.kind==="calculation"&&event.detail?.zones){
    const zones=viewEl("dl",null,"tl-detail-fields tl-detail-zones");
    for(const [name,value] of Object.entries(event.detail.zones as Record<string,any>))if(Number.isFinite(value))zones.append(
      viewEl("dt",services.display?.label?.("result_zones",name)||name),viewEl("dd",Number(value.toFixed(5)).toLocaleString("zh-CN")));
    if(zones.children.length){box.append(viewEl("h5","计算分区"),zones);}
  }
  const benefitRows=(rows:any[],title:string,note:string,open:boolean)=>{
    if(!rows?.length)return;
    const details=viewEl("details"),list=viewEl("ul");details.open=open;
    details.appendChild(viewEl("summary",`${title}（${rows.length} 项）`));
    for(const row of rows.slice(0,40)){
      const described=services.effects?.describeAppliedBenefit?.(row)||{label:"机制收益",value:Number.isFinite(row.value)?String(row.value):"数值未提供"};
      const readableSource=/[\u3400-\u9fff]/u.test(String(row.source_name||""))?row.source_name:
        row.type==="manual_effect_applied"?"手工收益（来源未记录）":"机制收益";
      const owner=row.owner?TV.characterNames?.get(row.owner):null;
      list.appendChild(viewEl("li",`${owner?`${owner} · `:""}${readableSource}：${described.label} ${described.value}`
        +(row.consumption_status==="not_consumed"?"；已物化，本次公式未消费":row.consumption_status==="unknown"?"；已物化，公式消费记录不足，未核实":"")
        +(row.presentation?.purpose==="calculation_sampling"?`；计算采样（非游戏Buff寿命）：${row.presentation.reason}`:"")));
    }
    details.appendChild(list);
    if(rows.length>40)details.appendChild(viewEl("p","这里只显示前 40 项，完整回执见原始记录。"));
    details.appendChild(viewEl("p",note,"tl-detail-hint"));
    box.appendChild(details);
  };
  if(event.kind==="calculation"){
    const fixed=!!(event.detail?.calculation_override||event.detail?.hit?.calculation_override);
    benefitRows(event.detail?.applied_effects,fixed?"自动计算基线实际消费的收益":"本次公式实际消费的收益",
      fixed?"这些项目属于固定值补正前的自动计算基线，不用于反推补正后的有效结果。":
      "按本次计算保存的公式消费记录精确关联；消费表示公式读取，不代表必定带来正向边际收益。",true);
    benefitRows(event.detail?.materialized_effects,"运行时已物化、未确认由本次公式消费的项目",
      "保留原运行记录；物化不等于本次公式生效。未消费或旧记录无法核实的项目不会计入上述实际消费列表。",false);
  }
  for(const key of ["message","note","reason","meaning","interpretation"]){const value=event.detail?.[key];
    if(typeof value==="string")box.appendChild(viewEl("p",value));}
  if(event.kind==="coverage" && Array.isArray(event.detail?.states)){
    const activations=new Map(),states=event.detail.states;
    for(const state of states){
      if(!Number.isInteger(state.activation_trace_index) || !Number.isFinite(state.start) || !Number.isFinite(state.rule_expires_at))continue;
      // 次数消费和层数到期可切分同一次激活，不能把每个 state 都当作刷新。
      const key=JSON.stringify([state.activation_trace_index,state.rule_expires_at]);
      if(!activations.has(key))activations.set(key,state);
    }
    const entries=[...activations.values()],seconds=(n:any)=>Number(n.toFixed(4));
    if(entries.length>1){
      box.appendChild(viewEl("p",`此连续可用段由 ${entries.length} 次激活/刷新合并形成；整段长度 ${seconds(event.end-event.start)} 秒包含后续刷新，单次到期分别见下列记录。`));
      const history=viewEl("details"),list=viewEl("ol");history.open=true;
      history.appendChild(viewEl("summary",`激活/刷新明细（${entries.length} 次）`));
      for(const [i,state] of entries.slice(0,8).entries())list.appendChild(viewEl("li",
        `${seconds(state.activation_time??state.start)}秒：${state.activation_trace_index<0?"初态登记":i===0?"激活":"刷新"}，规则到期 ${seconds(state.rule_expires_at)}秒。`));
      history.appendChild(list);
      if(entries.length>8)history.appendChild(viewEl("p","这里只显示前 8 次，完整状态记录见下方原始记录。"));
      box.appendChild(history);
    }else if(entries.length===1){const state=entries[0];
      box.appendChild(viewEl("p",`${seconds(state.activation_time??state.start)}秒${state.activation_trace_index<0?"初态登记":"激活"}，规则到期 ${seconds(state.rule_expires_at)}秒；本段未合并后续刷新。`));
    }
    if(entries.length && states.length>entries.length)box.appendChild(viewEl("p","本段还包含次数或层数等状态变化；这些变化不计为刷新。"));
    if(event.detail.receipt_id){const history=viewEl("details");history.appendChild(viewEl("summary",`状态变化（${states.length} 段）`));
      for(const state of states.slice(0,40)){const balance=state.resource_balance,scale=Number.isFinite(balance?.unit_scale)&&balance.unit_scale>0?balance.unit_scale:1;
        history.appendChild(viewEl("p",`${seconds(state.start)}～${seconds(state.end)}秒：${TD.stateName(state.status)}；${state.layers} 层${state.remaining_quota!=null?`；剩余 ${state.remaining_quota} 次`:""}${Number.isFinite(balance?.remaining)?`；共享库存 ${seconds(balance.remaining/scale)}`:""}；结束原因：${TD.endReason(state.end_reason)}`));}
      if(states.length>40)history.appendChild(viewEl("p","其余切片见原始记录。"));box.appendChild(history);}
  }
  if(event.edit){
    if(event.edit.kind==="action" && services.rotation?.editAction)box.appendChild(button("编辑此动作",()=>services.rotation.editAction(event.edit.index)));
    else if(["hit","buff","switch"].includes(event.edit.kind))box.appendChild(button("编辑此事件",()=>RT.select(event.edit.kind,event.edit.index)));
  }
  const raw=viewEl("details"), summary=viewEl("summary","原始记录（按需展开）");raw.appendChild(summary);
  let populated=false;raw.addEventListener("toggle",()=>{if(raw.open&&!populated){populated=true;
    raw.appendChild(viewEl("pre",JSON.stringify(event.detail??{},null,2)));}});box.appendChild(raw);
};


  return {showDetail};
}
