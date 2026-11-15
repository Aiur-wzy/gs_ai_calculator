import {type WhiteboardPorts} from "./ports";
export function createWhiteboardInspector(ports:WhiteboardPorts){
  const WB=ports.owner,UI=ports.ui,$=ports.query,setStatus=ports.status,services=ports.services;
const renderDerived =(box:any)=>{
  const event=WB.state.selectedDerived;box.append(UI.el("h3",WB.state.mode==="effects"?"效果详情":"伤害详情"));
  if(!event){box.append(UI.el("div","点击时间轴上的区间或标记，在这里查看来源、时间与计算说明。","wb-empty-detail"));return;}
  if(event.events){
    box.append(UI.el("h4",`${event.events.length} 条相邻记录`),UI.el("p","仅合并显示位置，未合并伤害或改动时点。选择记录查看明细。","wb-note"));
    if(WB.state.mode==="damage"){
      const damages=event.events.filter((e:any)=>e.kind!=="calculation"&&Number.isFinite(e.value));
      if(damages.length)box.append(UI.el("p",`组内合计：${WB.number(damages.reduce((sum:any,e:any)=>sum+e.value,0))}（${damages.length} 条伤害事件，计算记录不重复计入）`));
    }
    const page=Math.min(WB.state.groupPage||0,Math.max(0,Math.ceil(event.events.length/40)-1));
    for(const item of event.events.slice(page*40,page*40+40))box.append(UI.button(`${WB.number(item.start)} 秒 · ${item.label}${item.value!=null?` · ${WB.number(item.value)}`:""}`,()=>{
      WB.state.derivedBack=event;WB.selectEvent(item,true);},"wb-detail-choice"));
    if(event.events.length>40){const nav=UI.el("div",null,"wb-toolbar"),prev=UI.button("上一页",()=>{WB.state.groupPage=page-1;WB.render();}),next=UI.button("下一页",()=>{WB.state.groupPage=page+1;WB.render();});
      prev.disabled=page===0;next.disabled=(page+1)*40>=event.events.length;nav.append(prev,UI.el("span",`${page+1} / ${Math.ceil(event.events.length/40)}`),next);box.append(nav);}return;
  }
  if(WB.state.derivedBack)box.append(UI.button("← 返回相邻记录",()=>{WB.state.selectedDerived=WB.state.derivedBack;WB.state.derivedBack=null;WB.render();}));
  const detail=event.detail||{},facts=UI.el("dl",null,"wb-facts"),fact=(label:any,value:any)=>{if(value!=null&&value!=="")facts.append(UI.el("dt",label),UI.el("dd",value));};
  box.append(UI.el("h4",event.label));
  const explanation=WB.state.mode==="effects" && services.effects && services.display?.renderEffectDescription
    ? services.effects.describe(event,{characterName:(id:any)=>WB.actor(id)?.name||"未指定角色"}) : null;
  if(explanation)box.append(services.display.renderEffectDescription(explanation));
  if(!explanation && services.effects && detail.beneficiaries_scope?.eligibility){
    for(const line of services.effects.eligibilityDescription(detail.beneficiaries_scope,(id:any)=>WB.actor(id)?.name||"未指定角色"))fact("受益资格",line);
    for(const line of services.effects.selectionDescription(detail.beneficiary_selection,(id:any)=>WB.actor(id)?.name||"未指定角色"))fact("实际选人",line);
  }
  fact("时间",`${WB.number(event.start)} 秒${event.end>event.start?` → ${WB.number(event.end)} 秒`:""}`);
  if(!explanation)fact("来源",event.sourceLabel||WB.actor(event.character)?.name||"见计算来源");
  if(event.kind==="coverage"){
    // Keep labels as complete Chinese phrases for screen readers and search.
    box.append(UI.el("p",`来源：${event.sourceLabel||"见计算来源"}`,"wb-coverage-source"),UI.el("p",`${detail.receipt_id?"实例角色范围":"接收角色"}：${WB.recipients(event)}`),UI.el("p",`区间长度：${WB.number(event.end-event.start)} 秒`));
    if(detail.state_status)fact("区间状态",services.projection.stateName(detail.origin==="declared_interval"?"declared":detail.state_status));
    if(detail.end_reason)fact("区间结束原因",services.projection.endReason(detail.end_reason));
    if(detail.remaining_quota!=null)fact("本段剩余次数",WB.number(detail.remaining_quota));
    if(detail.layers!=null)fact("本段层数",WB.number(detail.layers));
    if(detail.beneficiaries_scope?.scope==="current_frontline")fact("受益范围","仅符合前台限制的角色命中时受益");
    for(const scope of detail.effect_applicability||[])if(scope.message)box.append(UI.el("p",`收益作用范围：${scope.message}`,"wb-note"));
  }
  if(event.value!=null)fact(event.kind==="external_damage"?"外部补充伤害":"期望伤害",WB.number(event.value));
  if(detail.execution_status)fact("执行状态",WB.checkStatus(detail.execution_status));
  if(typeof detail.allowed==="boolean")fact("判定结果",detail.allowed?"允许":"不允许");
  if(detail.before!=null&&detail.after!=null&&typeof detail.before!=="object")fact("次数变化",`${WB.number(detail.before)} → ${WB.number(detail.after)}`);
  const hit=detail.hit||detail.calculation?.hit;if(hit){for(const [key,label] of [["expected","期望伤害"],["noncrit","未暴击伤害"],["crit","暴击伤害"]])if(Number.isFinite(hit[key])&&(key!=="expected"||event.value==null))fact(label,WB.number(hit[key]));}
  box.append(facts);
  if(event.kind==="coverage"){
    box.append(UI.el("p",detail.state_status==="exhausted"?"该段仍在计时，但可用次数已经耗尽；不表示当前仍可提供收益。":"区间表示已记录的持续状态；命中能否受益还取决于命中类型、前台、次数、条件与快照。","wb-note"));
    const unique=new Map();for(const state of detail.states||[])if(Number.isFinite(state.start)&&Number.isFinite(state.rule_expires_at)&&Number.isInteger(state.activation_trace_index))unique.set(`${state.activation_trace_index}:${state.rule_expires_at}`,state);
    if(unique.size){const history=UI.el("details",null,"wb-fold"),items=[...unique.values()];history.append(UI.el("summary",`激活与刷新 · ${items.length} 次`));
      if(items.length>1)box.append(UI.el("p","此连续区间包含多次刷新，其总长度不是单次技能持续时间。","wb-note"));
      for(const [i,state] of items.slice(0,12).entries())history.append(UI.el("p",`${WB.number(state.activation_time??state.start)} 秒${state.activation_trace_index<0?"初态登记":i===0?"激活":"刷新"}；规则到期 ${WB.number(state.rule_expires_at)} 秒`,"wb-note"));
      if(items.length>12)history.append(UI.el("p","其余记录见原始状态。","wb-note"));box.append(history);}
    if(detail.receipt_id){const history=UI.el("details",null,"wb-fold");history.append(UI.el("summary",`状态变化 · ${(detail.states||[]).length} 段`));
      for(const state of (detail.states||[]).slice(0,40))history.append(UI.el("p",`${WB.number(state.start)} → ${WB.number(state.end)} 秒：${services.projection.stateName(state.status)}；${state.layers} 层${state.remaining_quota!=null?`；剩余 ${state.remaining_quota} 次`:""}；结束原因：${services.projection.endReason(state.end_reason)}`,"wb-note"));
      if(detail.states.length>40)history.append(UI.el("p","其余切片见原始记录。","wb-note"));box.append(history);}
  }
  for(const key of ["message","note","meaning","interpretation"])if(typeof detail[key]==="string"&&/[\u3400-\u9fff]/u.test(detail[key]))box.append(UI.el("p",detail[key],"wb-note"));
  const technical=UI.el("details",null,"wb-fold");technical.append(UI.el("summary","计算依据与原始记录"),UI.el("p","本页只读；发现漏项可交给 Agent 补正规则后重新交付。","wb-note"));
  if(detail.calculation)technical.append(WB.rawDetails("本次命中计算明细",detail.calculation));
  technical.append(WB.rawDetails("展开原始记录",detail));box.append(technical);
};
const renderInspector =(box:any)=>{
  if(WB.state.mode!=="actions"){WB.renderDerived(box);return;}
  const actor=WB.actor(WB.state.selectedActor),action=WB.action(WB.state.selectedAction);
  if(action){const fields=UI.el("div",null,"wb-action-fields"),capacityOwner=WB.capacityOwner(action.id),admissionOwner=WB.admissionOwner(action.id),lockedOwner=capacityOwner||admissionOwner,actorSelect=UI.select(WB.state.board.team.map((c:any)=>({value:c.id,label:c.name})),action.character,(value:any)=>WB.change(()=>action.character=value)),
    typeSelect=UI.select(WB.types.map(([value,label]:any)=>({value,label})),action.action_type_id,(value:any)=>WB.change(()=>{action.action_type_id=value;action.label=WB.typeName(value);action.variant="";}));
    actorSelect.disabled=!!lockedOwner;typeSelect.disabled=!!lockedOwner;fields.append(UI.el("h3","动作详情"),UI.field("动作角色",actorSelect),UI.field("动作类型",typeSelect),
    UI.field("动作名称",UI.input(action.label,(value:any)=>WB.change(()=>action.label=value))),UI.field("释放时点 / 秒",UI.input(action.start,(value:any)=>WB.setTime(action.id,value),true)));
    const variants=[{value:"",label:"未指定（由 Agent 核实）"},{value:"default",label:"默认释放"},{value:"tap",label:"点按"},{value:"hold",label:"长按"},{value:"custom",label:"其他（在备注说明）"}];
    if(action.variant&&!variants.some(v=>v.value===action.variant))variants.push({value:action.variant,label:action.variant});
    const variantSelect=UI.select(variants,action.variant,(value:any)=>WB.change(()=>action.variant=value));variantSelect.disabled=!!lockedOwner;
    fields.append(UI.field("操作变体",variantSelect),UI.field("操作细节 / 段数 / 取消方式",UI.area(action.notes,(value:any)=>WB.change(()=>action.notes=value))));
    if(capacityOwner)fields.append(UI.el("p",`动作属于次数意图 ${capacityOwner.id}；如需更换角色、动作类型或变体，请先修改或删除该次数意图。`,"wb-warning"));
    if(admissionOwner)fields.append(UI.el("p",`动作属于动作接纳意图 ${admissionOwner.id}；如需更换角色、动作类型或变体，请先修改或删除该意图。`,"wb-warning"));
    const tools=UI.el("div",null,"wb-toolbar"),more=UI.button("动作操作…",()=>WB.openCommandMenu(more,`${WB.actionName(action)}操作`,WB.actionMenuItems(action.id)),"wb-menu-trigger");tools.append(more);
    fields.append(tools,UI.el("p","调整顺序会交换相邻动作的时间槽；同秒则只调整先后。技能的真实占用、冷却和资源条件由 Agent 核实。","wb-note"));box.append(fields);
  }
  if(!action)box.append(UI.el("h3","动作详情"),UI.el("div","点击时间轴上的动作进行编辑；也可以添加新动作。","wb-empty-detail"));
  const add=WB.fold(actor?`添加动作 · ${actor.name}`:"添加动作 · 先选择角色","addOpen"),grid=UI.el("div",null,"wb-action-add");
  add.append(UI.field("添加动作时点 / 秒",UI.input(WB.state.cursor,(value:any)=>{if(Number.isFinite(value)&&value>=0&&value<WB.state.board.duration){WB.state.cursor=value;WB.render();}
    else setStatus("添加时点需位于当前轴内。");},true)));
  WB.types.forEach(([id,label]:any)=>{const button=UI.button(label,()=>WB.addAction(id));button.disabled=!actor;grid.append(button);});add.append(grid);box.append(add);
  const actions=WB.state.board.actions,order=WB.fold(`动作顺序 · ${actions.length} 项`,"orderOpen"),page=Math.min(WB.state.page,Math.max(0,Math.ceil(actions.length/40)-1));WB.state.page=page;order.className+=" wb-order";
  actions.slice(page*40,page*40+40).forEach((a:any,index:any)=>{
    const row=UI.el("div",null,"wb-order-row"),open=UI.button(`${WB.number(a.start)}秒 · ${WB.actor(a.character)?.name||"角色"} · ${WB.actionName(a)}`,
      ()=>WB.selectEvent({id:a.id,character:a.character,start:a.start}),a.id===WB.state.selectedAction?"active":""),more=UI.button("⋯",()=>WB.openCommandMenu(more,`${WB.actionName(a)}操作`,WB.actionMenuItems(a.id)),"wb-menu-trigger");
    row.append(UI.el("span",`${page*40+index+1}`),open,more);WB.bindCommandMenu(row,`${WB.actionName(a)}操作`,()=>WB.actionMenuItems(a.id));order.append(row);
  });if(actions.length>40){const nav=UI.el("div",null,"wb-toolbar"),prev=UI.button("前 40 项",()=>{WB.state.page--;WB.render();}),next=UI.button("后 40 项",()=>{WB.state.page++;WB.render();});
    prev.disabled=page===0;next.disabled=(page+1)*40>=actions.length;nav.append(prev,next);order.append(nav);}box.append(order);
};

  return {renderDerived,renderInspector};
}
