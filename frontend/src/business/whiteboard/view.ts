import {type WhiteboardPorts} from "./ports";
import {timelineLabelWidth} from "../timeline/geometry";
export function createWhiteboardView(ports:WhiteboardPorts){
  const WB=ports.owner,UI=ports.ui,$=ports.query,services=ports.services;
const themeColor =(token:any,fallback:any)=>services.theme?.token?.(token)||fallback;
const refreshThemeColors =()=>{
  WB.colors=Array.from({length:4},(_,index)=>WB.themeColor(`color-axis-character-${index+1}`,["#7ec4da","#ba99e8","#edbb78","#86c8a0"][index]));
};
const number =(v:any)=>typeof v==="number"&&Number.isFinite(v)?v.toLocaleString("zh-CN",{maximumFractionDigits:2}):"—";
const setMode =(mode:any)=>{if(!["actions","effects","damage"].includes(mode))return false;
  WB.state.mode=mode;WB.state.evidenceAction=false;WB.state.selectedDerived=null;WB.state.derivedBack=null;WB.render();return true;};
const openAdd =()=>{WB.state.mode="actions";WB.state.addOpen=true;WB.render();services.workspace?.reveal?.("whiteboard","right");};
const render =()=>{
  const root=$("#tab-whiteboard");if(!root)return;const b=WB.state.board,oldTrack=WB.state.wrap,active=ports.document.activeElement;
  const trackFocused=oldTrack?.contains?.(active),focusId=trackFocused?active?.getAttribute?.("data-axis-event"):null;
  root.innerHTML="";
  if(!b){
    WB.state.wrap=null;WB.state.svg=null;WB.state.labelSvg=null;
    const empty=UI.el("section",null,"wb-empty wb-start");empty.setAttribute("aria-labelledby","wb-start-title");
    const title=UI.el("h2","尚未打开创作白板");title.id="wb-start-title";
    const actions=UI.el("div",null,"wb-start-actions"),create=UI.button("新建创作白板",async()=>{
      if(services.workspace?.invoke)await services.workspace.invoke("new-board");else await WB.newBoard();
    },"primary"),open=UI.button("打开白板…",async()=>{
      if(services.workspace?.invoke)await services.workspace.invoke("open");else WB.openFile();
    });
    actions.append(create,open);empty.append(title,UI.el("p","从左上角“文件”菜单新建或打开白板。新建只建立本地文档；第一次向 Agent 发送有效消息后才会建立聊天历史。","wb-note"),actions);
    root.append(empty);services.workspace?.refresh?.();return;
  }
  const head=UI.el("div",null,"wb-header"),title=UI.input(b.title,(v:any)=>WB.change((board:any)=>board.title=v));title.className="wb-title";
  const status=UI.el("span",`${WB.dirty()?"● 尚未保存":WB.state.path?"已保存":"新白板"} · ${b.actions.length} 个动作`,"wb-status");
  status.title=WB.state.path||"保存后可再次打开此白板";
  head.append(UI.el("span","创作白板","wb-document-kind"),UI.field("白板名称",title),UI.field("轴长 / 秒",UI.input(b.duration,WB.setDuration,true)),status);
  const report=b.damage_report||{contract:"damage-report-window-v1",mode:"last_active_damage"};
  head.append(UI.field("DPS截止",UI.select([{value:"last_active_damage",label:"末次主动命中"},{value:"explicit",label:"用户指定时间"}],report.mode,(mode:any)=>WB.change((board:any)=>{
    board.damage_report={...report,mode};if(mode==="explicit")board.damage_report.end=board.duration;else delete board.damage_report.end;
  }))));
  if(report.mode==="explicit")head.append(UI.field("截止 / 秒",UI.input(report.end,(value:any)=>{
    if(!Number.isFinite(value)||value<=0||value>b.duration){ports.status("截止需大于零且不超过动作执行轴长。延长审计不会自动延长DPS。");return;}
    WB.change((board:any)=>board.damage_report={...report,end:value});
  },true)));
  const layoutControls=UI.el("span",null,"workspace-layout-buttons");layoutControls.setAttribute("data-workspace-controls","whiteboard");head.append(layoutControls);
  const layout=UI.el("div",null,"wb-layout"),team=UI.el("aside",null,"wb-team"),center=UI.el("div",null,"wb-center"),inspector=UI.el("aside",null,"wb-inspector");
  team.setAttribute("aria-label","白板队伍与装备");inspector.setAttribute("aria-label","白板动作编辑与只读详情");
  root.append(head);const workflowState=WB.workflowState();if(workflowState){const stage=services.workbench?.renderStageTracker?.(workflowState);if(stage){stage.classList.add("wb-stage-flow");root.append(stage);}}root.append(layout);layout.append(team,center,inspector);WB.renderTeam(team);WB.renderCenter(center);WB.renderInspector(inspector);
  services.workspace?.mount?.(layout,{key:"whiteboard",left:".wb-team",right:".wb-inspector",context:b,
    rightContext:WB.state.mode==="actions"?WB.state.selectedAction||(WB.state.addOpen?"adding":null):WB.state.selectedDerived?.id||null,repaint:()=>WB.draw()});
  if(trackFocused)WB.restoreAxisFocus(WB.state.wrap,focusId);
  services.workbench?.documentChanged?.();
};
const renderTeam =(box:any)=>{
  const b=WB.state.board;box.append(UI.el("h3","队伍与初始面板"));
  b.team.forEach((c:any,i:any)=>{const card=UI.button("",()=>{WB.state.selectedActor=c.id;WB.render();},`wb-team-card${WB.state.selectedActor===c.id?" active":""}`);
    const equipment=c.artifact_sets.map((s:any)=>`${s.name} ${s.count} 件`).join(" + ")||"套装未选",
      panel=`攻击 ${c.panel.atk??"待填"} · 充能 ${c.panel.energy_recharge==null?"待填":`${WB.number(c.panel.energy_recharge*100)}%`}`;
    card.style.setProperty?.("--wb-color",WB.colors[i]);card.append(UI.el("strong",c.name),UI.el("span",`${c.level} 级 · ${c.constellation} 命 · ${c.weapon?.name||"武器未选"}`));
    card.title=`${equipment}；${panel}`;
    if(c.character_ref?.kind==="provisional"||c.weapon?.entity_ref?.kind==="provisional"||(c.artifact_sets||[]).some((s:any)=>s.entity_ref?.kind==="provisional")){
      card.append(UI.el("span","临时资料 · 仅限当前方案 · 正式结果将标注近似","wb-warning"));
    }
    WB.bindCommandMenu(card,`${c.name}操作`,()=>WB.characterMenuItems(c.id));box.append(card);
    if(WB.state.selectedActor===c.id){const details=WB.fold("套装与面板摘要","teamSummaryOpen");details.append(UI.el("p",equipment,"wb-note"),UI.el("p",panel,"wb-note"));box.append(details);
      const tools=UI.el("div",null,"wb-toolbar"),more=UI.button("更多…",()=>WB.openCommandMenu(more,`${c.name}操作`,WB.characterMenuItems(c.id)),"wb-menu-trigger");
      tools.append(UI.button("装备与面板",()=>WB.editCharacter(c.id)),more);box.append(tools);}
  });
  const add=UI.button("＋ 添加角色",WB.addCharacter);add.disabled=b.team.length>=4;box.append(add);
  if(b.team.length){const reapply=UI.button("重新计算装备与面板",WB.recalculateEquipment);reapply.disabled=WB.state.equipmentReport?.state==="working";box.append(reapply);}
  if(WB.unappliedEditor())box.append(UI.el("p","有装备与面板草稿尚未应用，重新打开对应角色可继续编辑。","wb-warning"));
  const report=WB.state.equipmentReport;
  if(report){const notice=UI.el("section",null,"ew-agent-report");
    notice.append(UI.el("strong",report.state==="working"?"正在重算当前装备与面板…":report.state==="ready"?"当前装备与面板已重新计算":"需要 Agent 二次处理"));
    if(report.state==="pending"){
      notice.append(UI.el("p","当前设置已保留；旧计算不会作为新装备的结果显示。"));
      for(const issue of (report.missing_inputs||[]).slice(0,8))notice.append(UI.el("p",issue.message||"存在需要核对的计算规则。"));
      if((report.missing_inputs||[]).length>8)notice.append(UI.el("p",`另有 ${report.missing_inputs.length-8} 项，可展开下方待完善项目查看。`));
      notice.append(report.plan?UI.button("保存待完善方案给 Agent",WB.saveEquipmentCandidate):UI.button("保存交接文件给 Agent",WB.handoff));
    }else if(report.state==="ready"){
      notice.append(UI.el("p","已有规则已重用。请同时查看机制、动作与能量检查；结果尚未保存为交付文件。"));
      if(report.agent_followup?.length){notice.append(UI.el("strong","仍需 Agent 核实的项目"));
        for(const issue of report.agent_followup.slice(0,8))notice.append(UI.el("p",typeof issue==="string"?issue:issue.message||issue.detail||"存在需要核对的计算检查。"));
        if(report.agent_followup.length>8){const all=UI.el("details");all.append(UI.el("summary",`展开全部 ${report.agent_followup.length} 项`));
          for(const issue of report.agent_followup)all.append(UI.el("p",typeof issue==="string"?issue:issue.message||issue.detail||"存在需要核对的计算检查。"));notice.append(all);}}
    }
    box.append(notice);}
  WB.renderCapacityIntents(box);WB.renderAdmissionIntents(box);
  const notes=UI.area(b.notes,(value:any)=>WB.change((board:any)=>board.notes=value)),memo=WB.fold("排轴目标与备注","notesOpen");memo.append(UI.field("排轴目标与备注",notes));box.append(memo);
  if(WB.state.pending.length){const d=UI.el("details");d.append(UI.el("summary",`待完善项目（${WB.state.pending.length}）`));
    for(const issue of WB.state.pending)d.append(UI.el("p",issue.message,"wb-note"));box.append(d);}
};
const renderCenter =(box:any)=>{
  const nav=UI.el("div",null,"wb-toolbar wb-mode-tabs");nav.setAttribute("aria-label","白板时间轴视图");
  for(const [key,label] of [["actions","动作轴"],["effects","效果轴"],["damage","伤害轴"]]){
    const button=UI.button(label,()=>WB.setMode(key),WB.state.mode===key?"active":"");button.setAttribute("aria-pressed",WB.state.mode===key);nav.append(button);
  }
  nav.append(UI.el("span",WB.state.mode==="actions"?"可编辑":"只读","wb-mode-state"));box.append(nav);
  const tools=UI.el("div",null,"wb-toolbar wb-timeline-tools");
  if(WB.state.mode==="actions")tools.append(UI.button("＋ 添加动作",WB.openAdd));
  tools.append(UI.button("−",()=>WB.zoom(1/1.3)),UI.el("span",`${Math.round(WB.state.scale*100)}%`,"wb-scale"),
    UI.button("＋",()=>WB.zoom(1.3)),UI.button("适应全轴",()=>{WB.state.scale=1;WB.state.scroll=0;WB.render();}));
  tools.append(UI.button(WB.state.labelsCollapsed?"展开轴标签":"收起轴标签",()=>{WB.state.labelsCollapsed=!WB.state.labelsCollapsed;WB.render();}));
  if(WB.state.mode==="effects"){
    const filters=UI.el("div",null,"wb-toolbar wb-effect-filters");filters.setAttribute("aria-label","效果视图筛选");
    for(const [key,label] of [["all","全部效果"],["coverage","持续 Buff"],["points","命中与触发"]]){
      const button=UI.button(label,()=>{WB.state.effectFilter=key;WB.state.selectedDerived=null;WB.render();},WB.state.effectFilter===key?"active":"");
      button.setAttribute("aria-pressed",WB.state.effectFilter===key);filters.append(button);
    }filters.append(UI.button(WB.state.showCalculationSampling?"隐藏采样区间":"显示采样区间",()=>{
      WB.state.showCalculationSampling=!WB.state.showCalculationSampling;WB.state.effectFilter="all";WB.render();}));tools.append(filters);
  }
  if(WB.state.mode==="damage"){
    const filters=UI.el("div",null,"wb-toolbar wb-effect-filters");filters.setAttribute("aria-label","伤害视图筛选");
    for(const [key,label] of [["events","伤害事件"],["all","含计算记录"]]){
      const button=UI.button(label,()=>{WB.state.damageFocus=key;WB.state.selectedDerived=null;WB.state.derivedBack=null;WB.render();},WB.state.damageFocus===key?"active":"");
      button.setAttribute("aria-pressed",WB.state.damageFocus===key);filters.append(button);
    }tools.append(filters);
  }
  const help=WB.fold("操作说明","helpOpen");help.className+=" wb-help";
  help.append(UI.el("p",WB.state.mode==="actions"?"拖动动作调整时点；点空白设置添加位置。Ctrl + 滚轮缩放，拖动空白或中键平移。选中后方向键微调，Shift 为 1 秒。":"Ctrl + 滚轮缩放，拖动空白或中键平移；点击标记查看右侧详情。此视图只读。","wb-note"));
  if(WB.state.mode==="effects")help.append(UI.el("p","横条为有明确起止时间的持续 Buff，灰色条表示次数耗尽，菱形为命中与触发点。无明确持续时间的条件效果不绘制横条。","wb-note"));
  tools.append(help);box.append(tools);
  if(WB.state.result){const state=WB.workflowState(),r=WB.state.result.result,total=r.calculation_totals||r.summary||{},summary=UI.el("div",null,"wb-summary");
    if(state?.diagnostic)summary.append(UI.el("strong","严格计算未通过：仅保留诊断，不可作为正式结果"),UI.el("span","请修复动作、能量、数值或绑定阻断项后重新计算。"));
    else summary.append(UI.el("span",`计算总伤 ${WB.number(total.total_damage)}`),UI.el("strong",`DPS ${WB.number(total.dps)}`));
    summary.append(UI.el("p",r.validation_scope?.summary||"按保存输入计算；旧结果未保存统一验证范围，不代表游戏机制完整。","wb-note"));
    if(WB.state.reusableResult?.owner==="native_dsh"&&typeof WB.saveAppliedResult==="function"){
      summary.append(UI.button("计算结果另存为…",()=>void WB.saveAppliedResult()));
      const saved=WB.state.appliedResultFile;
      if(saved?.calculated===WB.state.result)summary.append(UI.el("span",`结果文件：${saved.path}`,"wb-path"));
    }
    const checks=WB.fold(`检查与假设 · ${(r.mechanics_issues||[]).length} 项问题`,"checksOpen");checks.className+=" wb-checks";
    const checkLine=UI.el("p",`动作检查：${WB.checkStatus(r.rotation_check?.action_check?.status)} · 能量检查：${WB.checkStatus(r.rotation_check?.energy_check?.status)} · 机制检查：${WB.checkStatus(r.mechanics_status)}`,"wb-note");
    if([r.rotation_check?.action_check?.status,r.rotation_check?.energy_check?.status,r.mechanics_status].some(v=>!["passed","ready","complete"].includes(v)))checkLine.className+=" wb-warning";
    checks.append(checkLine,UI.el("p","白板意图已匹配；游戏机制仍以 Agent 的核对范围为准。","wb-note"));
    for(const issue of (r.mechanics_issues||[]).slice(0,8))if(issue.message)checks.append(UI.el("p",issue.message,"wb-note"));
    checks.append(WB.rawDetails("原始检查记录",{rotation_status:r.rotation_check?.status,energy:r.rotation_check?.energy_check,mechanics_issues:r.mechanics_issues,assumptions:r.assumptions}));summary.append(checks);box.append(summary);
  }
  if(WB.state.staleResultPath){const stale=WB.fold("白板已修改，原计算结果已失效","staleOpen");stale.className+=" wb-warning";
    stale.append(UI.el("p","原交付文件仍保留，当前不显示旧数值；请重新交接计算。","wb-note"),UI.el("p",WB.state.staleResultPath,"wb-path"));box.append(stale);}
  if(WB.state.result&&WB.workflowState()?.diagnostic&&WB.state.mode!=="actions"){
    const diagnostic=UI.el("div",null,"wb-empty");diagnostic.append(UI.el("h3","严格计算诊断"),
      UI.el("p","动作、能量、数值或绑定检查未通过。诊断尝试不会绘制伤害轴或效果轴，也不能作为正式结果应用。","wb-warning"),
      UI.button("交给 Agent 修复并重算",WB.handoff));box.append(diagnostic);WB.state.wrap=null;WB.state.svg=null;return;
  }
  if(!WB.state.result&&WB.state.mode!=="actions"){
    const topology=WB.topologyDraft(),empty=UI.el("div",null,"wb-empty");
    if(topology){const stats=WB.topologyStats(),status=services.workbench?.stageStatusText?.(stats.status)||(({complete:"本阶段完成",ready:"本阶段完成",partial:"仍有缺项",in_progress:"正在完善"} as Record<string,string>)[stats.status]||"状态待确认"),binding=WB.bindingStats(),acceptance=WB.workflowState()?.calculationAcceptance||"not_requested";
      empty.append(UI.el("h3","事件拓扑草稿"),UI.el("p","拓扑结构只描述已识别事件、生命周期和关系；伤害数值会在数值范式和严格计算阶段披露。","wb-note"),
        UI.el("h4","拓扑结构"),UI.el("p",`事件节点 ${stats.nodes} · 拓扑关系 ${stats.bindings} · 待完善 ${stats.unresolved} · ${status}`,stats.unresolved?"wb-warning":"wb-note"),
        UI.el("h4","执行绑定"));
      if(binding.receipt){const label=services.workbench?.bindingGateText?.(binding.receipt,WB.workflowState()?.index??2)||`${binding.status||"状态待确认"} · 已绑定 ${binding.bound}/${binding.expected} · 已解析 ${binding.resolved}`;
        empty.append(UI.el("p",label,binding.status==="complete"?"wb-note":"wb-warning"));
        if(binding.missing.length)empty.append(UI.el("p",`缺失节点：${binding.missing.join("、")}${binding.missingTruncated?"（仅显示前几项）":""}`,"wb-warning"));
        if(binding.issue)empty.append(UI.el("p",`首个问题：${WB.bindingIssueText(binding.issue)}`,"wb-warning"));
        empty.append(UI.el("p","执行绑定回执只核验当前输入的引用覆盖和新鲜度；角色特有语义仍由 Agent 声明，未由 App 或引擎验证。","wb-note"));
      }else empty.append(UI.el("p","尚未返回执行绑定回执；拓扑关系不能代替执行输入绑定。","wb-warning"));
      empty.append(UI.el("h4","计算接纳"),UI.el("p",services.workbench?.acceptanceText?.(acceptance)||(({accepted:"可应用",rejected:"已拒绝",diagnostic_only:"仅诊断，不可应用",not_requested:"尚未请求"} as Record<string,string>)[acceptance]||"尚未请求"),"wb-note"));
    }else empty.append(UI.el("h3","等待计算结果或事件拓扑"),UI.el("p","尚无与当前白板匹配的计算回传或事件拓扑。动作检查点之后，先让 Agent 建立攻击、Buff、附着、反应候选和周期事件的结构，再进入数值完善与严格计算。","wb-note"));
    const actions=UI.el("div",null,"wb-toolbar");actions.append(UI.button("交给 Agent 完善",WB.handoff),UI.button("载入计算回传",WB.importResult));empty.append(actions);box.append(empty);WB.state.wrap=null;WB.state.svg=null;return;
  }
  if(WB.state.mode==="effects"&&WB.state.result){WB.project();const info=WB.state.resultProjection?.views.effects.coverage;
    if(info){box.append(UI.el("p",info.message,"wb-note"));
      if(info.excluded?.length){const details=UI.el("details",null,"wb-fold");details.append(UI.el("summary",`未显示为持续条的来源（${info.excluded.length}）`));
        for(const item of info.excluded)details.append(UI.el("p",`${/[\u3400-\u9fff]/u.test(item.name_zh||"")?item.name_zh:"增益来源"}：${item.message||"没有可靠的有限持续时间"}`,"wb-note"));box.append(details);}}}
  if(WB.state.mode==="actions"&&!WB.state.board.team.length){
    const empty=UI.el("div",null,"wb-empty");empty.append(UI.el("h3","添加角色，开始排轴"),
      UI.el("p","先选择队伍角色，设置装备与初始面板，再添加动作并调整释放时点。","wb-note"),UI.button("＋ 添加角色",WB.addCharacter));
    box.append(empty);WB.state.wrap=null;WB.state.svg=null;return;
  }
  const wrap=UI.el("div",null,"wb-track");wrap.id="wb-track";wrap.tabIndex=0;wrap.setAttribute("aria-label","白板时间轴；动作可拖动，效果和伤害只读");
  const shell=UI.el("div",null,"wb-axis-shell"),labels=UI.el("div",null,"wb-label-pane");labels.style.width=`${timelineLabelWidth(WB.state.labelsCollapsed)}px`;
  const labelSvg=ports.document.createElementNS("http://www.w3.org/2000/svg","svg");labels.append(labelSvg);
  const svg=ports.document.createElementNS("http://www.w3.org/2000/svg","svg");svg.id="wb-svg";wrap.append(svg);shell.append(labels,wrap);box.append(shell);
  WB.state.labelSvg=labelSvg;WB.state.wrap=wrap;WB.state.svg=svg;
  const mode=WB.state.mode,initialScroll={left:WB.state.scroll||0,top:WB.state.axisScrollTop?.[mode]||0};
  wrap.addEventListener("scroll",()=>{if(WB.state.wrap!==wrap)return;
    WB.state.scroll=wrap.scrollLeft;WB.state.axisScrollTop ||= {};WB.state.axisScrollTop[mode]=wrap.scrollTop;
    if(WB.state.drawQueued===wrap)return;const request=ports.document.defaultView?.requestAnimationFrame;
    if(!request){WB.draw();return;}WB.state.drawQueued=wrap;request.call(ports.document.defaultView,()=>{
      if(WB.state.drawQueued===wrap)WB.state.drawQueued=null;if(WB.state.wrap===wrap)WB.draw();});});
  wrap.addEventListener("wheel",(e:any)=>{if(e.ctrlKey){e.preventDefault();WB.zoom(e.deltaY<0?1.15:1/1.15,e.clientX-wrap.getBoundingClientRect().left);}
    else if(e.shiftKey){e.preventDefault();wrap.scrollLeft+=e.deltaY;}},{passive:false});
  wrap.addEventListener("pointerdown",(e:any)=>{if(e.button===0||e.button===1)WB.beginPointer(e,null);});
  wrap.addEventListener("pointermove",WB.movePointer);wrap.addEventListener("pointerup",WB.endPointer);wrap.addEventListener("pointercancel",(e:any)=>WB.endPointer(e,true));
  wrap.addEventListener("keydown",(e:any)=>{
    if(e.defaultPrevented||(e.target!==wrap&&!e.target?.hasAttribute?.("data-axis-event")))return;
    if(e.key==="+"||e.key==="="){e.preventDefault();WB.zoom(1.3);return;}if(e.key==="-"){e.preventDefault();WB.zoom(1/1.3);return;}
    if(WB.state.mode!=="actions")return;const action=WB.action(WB.state.selectedAction);if(!action)return;
    if(["ArrowLeft","ArrowRight"].includes(e.key)){e.preventDefault();WB.setTime(action.id,WB.clampTime(action.start+(e.key==="ArrowLeft"?-1:1)*(e.shiftKey?1:0.1)));}
  });WB.draw(initialScroll);
  WB.state.scroll=wrap.scrollLeft;WB.state.axisScrollTop ||= {};WB.state.axisScrollTop[mode]=wrap.scrollTop;
};
const init =async()=>{
  WB.render();
  ports.events.addEventListener("resize",()=>WB.draw());
  ports.document.addEventListener?.("genshin-theme-change",()=>{WB.refreshThemeColors();WB.draw();});
};

  return {themeColor,refreshThemeColors,number,setMode,openAdd,render,renderTeam,renderCenter,init};
}
