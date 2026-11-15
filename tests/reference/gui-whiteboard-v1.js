// Human-authored intent only. Derived events are projected from a verified return package.
"use strict";
window.WB = {
  state:{board:null,path:"",fileSha:null,savedJson:null,request:0,selectedActor:null,selectedAction:null,
    mode:"actions",effectFilter:"all",damageFocus:"events",scale:1,scroll:0,result:null,resultPath:null,pending:[],cursor:0,page:0,closing:false,
    agentPayloadRevision:0,agentBoardGeneration:0,agentResultGeneration:0,agentPayloadCache:null},
  types:[
    ["action.normal_attack","普通攻击"],["action.charged_attack","重击"],["action.plunging_attack","下落攻击"],
    ["action.elemental_skill","元素战技"],["action.elemental_burst","元素爆发"],["action.dash","冲刺"],
    ["action.jump","跳跃"],["action.switch","切换角色"],["action.wait","等待"]],
  colors:["#7ec4da","#ba99e8","#edbb78","#86c8a0"],
  panelFields:[["atk","攻击力"],["hp","生命值"],["def","防御力"],["em","元素精通"],
    ["crit_rate","暴击率 %",true],["crit_dmg","暴击伤害 %",true],["energy_recharge","元素充能效率 %",true]],
  elements:[["all","全伤害"],["physical","物理"],["pyro","火"],["hydro","水"],["electro","雷"],
    ["cryo","冰"],["anemo","风"],["geo","岩"],["dendro","草"]],
};
WB.themeColor=(token,fallback)=>window.ThemeController?.token?.(token)||fallback;
WB.refreshThemeColors=()=>{
  WB.colors=Array.from({length:4},(_,index)=>WB.themeColor(`color-axis-character-${index+1}`,["#7ec4da","#ba99e8","#edbb78","#86c8a0"][index]));
};
WB.refreshThemeColors();
WB.json=()=>window.JSONTransport ? JSONTransport.stringify(WB.state.board) : JSON.stringify(WB.state.board);
// Agent CAS checks run on every chat refresh.  Keep their identity entirely
// scalar and materialize the potentially large board/result payload only when
// an Agent request actually needs it.  The cached canonical JSON strings are
// the exact local content digests; the Host still computes and validates its
// cryptographic fingerprints at the trust boundary.
WB.invalidateAgentPayload=(boardReplaced=false,resultReplaced=false)=>{
  WB.state.agentPayloadRevision=(WB.state.agentPayloadRevision||0)+1;
  if(boardReplaced)WB.state.agentBoardGeneration=(WB.state.agentBoardGeneration||0)+1;
  if(resultReplaced)WB.state.agentResultGeneration=(WB.state.agentResultGeneration||0)+1;
  WB.state.agentPayloadCache=null;
};
WB.agentPayloadIdentity=()=>({
  payloadRevision:WB.state.agentPayloadRevision||0,
  boardGeneration:WB.state.agentBoardGeneration||0,
  resultGeneration:WB.state.agentResultGeneration||0,
  boardRevision:Number.isInteger(WB.state.board?.revision)?WB.state.board.revision:null,
});
WB.agentPayloadMatches=base=>{
  const current=WB.agentPayloadIdentity();return !!base&&base.documentRef===WB.state.board&&
    base.payloadRevision===current.payloadRevision&&base.boardGeneration===current.boardGeneration&&
    base.resultGeneration===current.resultGeneration&&base.boardRevision===current.boardRevision;
};
WB.agentPayloadSnapshot=()=>{
  const identity=WB.agentPayloadIdentity(),cache=WB.state.agentPayloadCache;
  if(cache&&cache.documentRef===WB.state.board&&cache.resultRef===WB.state.result&&
      cache.payloadRevision===identity.payloadRevision&&cache.boardGeneration===identity.boardGeneration&&
      cache.resultGeneration===identity.resultGeneration&&cache.boardRevision===identity.boardRevision)return cache;
  const document=JSONTransport.clone(WB.state.board),calculated=WB.state.result?JSONTransport.clone(WB.state.result):null,
    applicationPayload={kind:"whiteboard",board:document,calculated},json=JSONTransport.stringify(document),
    payloadJson=JSONTransport.stringify(applicationPayload);
  const snapshot={...identity,document,applicationPayload,json,payloadJson,documentRef:WB.state.board,resultRef:WB.state.result};
  WB.state.agentPayloadCache=snapshot;return snapshot;
};
WB.setAgentResult=result=>{
  const replaced=WB.state.result!==result;WB.state.result=result;WB.invalidateAgentPayload(false,replaced);return result;
};
WB.unappliedEditor=()=>!!WB.state.editorSession&&WB.state.editorSession.base!==WB.state.editorSession.json()||Object.keys(WB.state.equipmentDrafts||{}).length>0;
WB.dirty=()=>WB.unappliedEditor()||!!WB.state.board&&WB.json()!==WB.state.savedJson;
WB.token=()=>({board:WB.state.board,json:WB.json(),request:++WB.state.request,path:WB.state.path,sha:WB.state.fileSha});
WB.current=t=>t.board===WB.state.board&&t.json===WB.json()&&t.request===WB.state.request&&t.path===WB.state.path&&t.sha===WB.state.fileSha;
WB.actor=id=>WB.state.board?.team.find(c=>c.id===id);
WB.action=id=>WB.state.board?.actions.find(a=>a.id===id);
WB.typeName=id=>WB.types.find(row=>row[0]===id)?.[1]||"动作类别待补";
WB.actionName=a=>a.label||WB.typeName(a.action_type_id);
WB.topologyDraft=()=>WB.state.board?.event_topology_draft||WB.state.board?.event_topology||WB.state.board?.extensions?.event_topology_draft||WB.state.board?.extensions?.event_topology||null;
WB.numericDraft=()=>WB.state.board?.numeric_binding||WB.state.board?.numeric_model||WB.state.board?.extensions?.numeric_binding||WB.state.board?.extensions?.numeric_model||null;
WB.topologyReceipt=()=>window.AgentWorkbenchApp?.topologyReceipt?.(WB.state.board,WB.state.result)||WB.state.board?.event_topology_coverage_receipt||WB.state.board?.extensions?.event_topology_coverage_receipt||null;
WB.executionBindingReceipt=()=>window.AgentWorkbenchApp?.executionBindingReceipt?.(WB.state.board,WB.state.result)||WB.state.board?.event_topology_execution_binding_receipt||WB.state.board?.extensions?.event_topology_execution_binding_receipt||null;
WB.workflowState=()=>{
  if(!window.AgentWorkbenchApp?.workflowState)return null;const topology=WB.topologyDraft(),numeric=WB.numericDraft(),state=window.AgentWorkbenchApp.workflowState(null,null,WB.state.board,WB.state.result);
  if(!WB.state.result&&numeric&&state.index<3){state.stage="numeric_binding";state.index=3;state.energyStatus=state.energyStatus==="unknown"?"not_checked":state.energyStatus;}
  else if(!WB.state.result&&topology&&state.index<2){state.stage="event_topology";state.index=2;state.energyStatus="not_checked";}
  return state;
};
WB.topologyStats=()=>{const topology=WB.topologyDraft()||{},array=(...values)=>values.find(Array.isArray)||[];return {
  status:topology.stage_status||topology.status||"in_progress",nodes:array(topology.nodes,topology.events,topology.components).length,
  bindings:array(topology.bindings,topology.edges,topology.relations).length,
  unresolved:array(topology.unresolved_components,topology.pending_items,topology.unresolved).length,
};};
WB.bindingStats=()=>{const receipt=WB.executionBindingReceipt(),counts=receipt?.counts||{},coverage=receipt?.coverage||{},allMissing=Array.isArray(coverage.missing_node_ids)?coverage.missing_node_ids:[];return {
  receipt,status:String(receipt?.status||"").toLowerCase(),expected:counts.expected_nodes??counts.expected??0,bound:counts.bound_nodes??counts.bound??0,
  resolved:counts.resolved_bindings??counts.resolved??0,missing:allMissing.slice(0,20),
  missingTruncated:!!coverage.missing_node_ids_truncated||allMissing.length>20,issue:Array.isArray(receipt?.issues)?receipt.issues[0]:null,
};};
WB.bindingIssueText=issue=>{if(window.AgentWorkbenchApp?.bindingIssueText)return window.AgentWorkbenchApp.bindingIssueText(issue);const value=typeof issue==="string"?issue:issue?.message||issue?.code||"存在未说明的问题",text=String(value);return text.length>512?text.slice(0,512)+"…":text;};
WB.number=v=>typeof v==="number"&&Number.isFinite(v)?v.toLocaleString("zh-CN",{maximumFractionDigits:2}):"—";
WB.commandMenu=()=>window.AppContextMenu?.forDocument?.(document,window)||null;
WB.menuError=error=>setStatus(`操作未完成：${String(error?.message||error||"未知错误")}`);
WB.openCommandMenu=(target,label,items)=>{
  const menu=WB.commandMenu();if(!menu)return false;
  return menu.open({target,trigger:target,returnFocus:target,label,items,onError:WB.menuError});
};
WB.bindCommandMenu=(target,label,items)=>{
  const menu=WB.commandMenu();if(!menu)return false;
  menu.bind(target,{label,items,onError:WB.menuError});return true;
};
// A synchronous edit only proposes a document counter. Python Store / Agent
// application validates previous -> next and performs the real SHA / live CAS.
WB.nextRevisionProposal=()=>{
  const previous=WB.state.board?.revision,policy=WB.state.revisionPolicy;
  if(policy&&policy.contract_id!=="whiteboard-intent-revision-v1")throw new Error("当前白板编辑规则不匹配，请重新打开文档。");
  const step=policy?.proposal_step??1,next=previous+step;
  if(!Number.isSafeInteger(previous)||previous<0||step!==1||!Number.isSafeInteger(next)||next>(policy?.maximum??Number.MAX_SAFE_INTEGER))throw new Error("当前白板方案版本无效，请重新打开文档。");
  return next;
};
WB.touch=(proposal=WB.nextRevisionProposal())=>{WB.state.board.revision=proposal;WB.state.request++;if(WB.state.result)WB.state.staleResultPath=WB.state.resultPath;
  const replacedResult=WB.state.result!==null;WB.state.result=null;WB.invalidateAgentPayload(false,replacedResult);
  WB.state.resultProjection=null;WB.state.selectedDerived=null;WB.state.pending=[];WB.state.equipmentReport=null;};
WB.change=fn=>{const proposal=WB.nextRevisionProposal();fn(WB.state.board);WB.touch(proposal);WB.render();};
WB.sort=()=>WB.state.board.actions.sort((a,b)=>a.start-b.start);
WB.clampTime=value=>Math.round(Math.max(0,Math.min(WB.state.board.duration-0.001,value))*1000)/1000;
WB.setTime=(id,value)=>{
  const a=WB.action(id),rounded=Math.round(value*1000)/1000;
  if(!a||!Number.isFinite(value)||value<0||rounded>=WB.state.board.duration){setStatus("动作时间需在轴内（含零，不含轴末）。");return false;}
  WB.change(()=>{a.start=rounded;WB.sort();});return true;
};
WB.moveOrder=(id,direction)=>{
  const actions=WB.state.board.actions,i=actions.findIndex(a=>a.id===id),j=i+direction;
  if(i<0||j<0||j>=actions.length)return;
  WB.change(()=>{[actions[i].start,actions[j].start]=[actions[j].start,actions[i].start];[actions[i],actions[j]]=[actions[j],actions[i]];});
};
WB.addAction=type=>{
  const c=WB.actor(WB.state.selectedActor);if(!c){setStatus("请先在左侧添加并选择角色。");return;}
  const action={id:UI.id("action"),character:c.id,action_type_id:type,label:WB.typeName(type),
    start:WB.clampTime(WB.state.cursor),variant:"",notes:""};
  WB.change(board=>{board.actions.push(action);WB.sort();WB.state.selectedAction=action.id;WB.state.mode="actions";WB.state.addOpen=false;});
};
WB.removeAction=id=>{if(!WB.action(id))return;
  if(WB.capacityOwner?.(id)){setStatus("该动作已属于次数意图，请先删除或修改对应次数意图。");return;}
  if(WB.admissionOwner?.(id)){setStatus("该动作已属于动作接纳意图，请先删除或修改对应意图。");return;}
  WB.change(board=>{board.actions=board.actions.filter(a=>a.id!==id);WB.state.selectedAction=null;});};
WB.duplicateAction=id=>{const original=WB.action(id);if(!original)return;
  WB.change(board=>{const next={...UI.clone(original),id:UI.id("action"),start:WB.clampTime(original.start+0.1)};
    board.actions.push(next);WB.sort();WB.state.selectedAction=next.id;});};
WB.actionMenuItems=id=>{
  const action=WB.action(id),actions=WB.state.board?.actions||[],index=actions.findIndex(row=>row.id===id),
    owner=action&&(WB.capacityOwner?.(id)||WB.admissionOwner?.(id));
  if(!action)return [];
  return [
    {id:"edit-action",label:"编辑动作",run:()=>WB.selectEvent({id:action.id,character:action.character,start:action.start})},
    {id:"move-action-earlier",label:"提前一位",enabled:index>0,disabledReason:index<=0?"已经是第一个动作":"",run:()=>WB.moveOrder(id,-1)},
    {id:"move-action-later",label:"延后一位",enabled:index>=0&&index<actions.length-1,disabledReason:index===actions.length-1?"已经是最后一个动作":"",run:()=>WB.moveOrder(id,1)},
    {id:"duplicate-action",label:"复制动作",run:()=>WB.duplicateAction(id)},
    {type:"separator"},
    {id:"remove-action",label:"删除动作",danger:true,enabled:!owner,disabledReason:owner?"请先删除或修改引用此动作的意图":"",run:()=>WB.removeAction(id)},
  ];
};
WB.setDuration=value=>{
  if(!Number.isFinite(value)||value<=0||value>3600){setStatus("轴长需大于零且不超过 3600 秒。");return false;}
  if(WB.state.board.actions.some(a=>a.start>=value)){setStatus("新轴长会截掉已有动作，请先调整末尾动作；不会自动删除或缩放动作。");return false;}
  WB.change(board=>{board.duration=value;WB.state.cursor=WB.clampTime(WB.state.cursor);});return true;
};
WB.replace=(data,saved=true,render=true)=>{
  if(data.revision_policy)WB.state.revisionPolicy=UI.clone(data.revision_policy);
  WB.state.request++;WB.state.board=data.board;WB.state.path=data.path||"";WB.state.fileSha=data.file_sha||null;
  WB.state.savedJson=saved?WB.json():null;WB.state.pending=data.pending||[];WB.state.result=null;WB.state.resultProjection=null;WB.state.resultPath=null;WB.state.staleResultPath=null;
  WB.invalidateAgentPayload(true,true);
  WB.state.equipmentDrafts={};WB.state.reusableResult=null;WB.state.equipmentReport=null;
  WB.state.selectedActor=data.board.team[0]?.id||null;WB.state.selectedAction=null;WB.state.selectedDerived=null;
  WB.state.mode="actions";WB.state.effectFilter="all";WB.state.damageFocus="events";WB.state.cursor=0;WB.state.scale=1;WB.state.scroll=0;WB.state.page=0;WB.state.addOpen=false;WB.state.derivedBack=null;if(render)WB.render();
};
// Applying a reviewed construction candidate replaces only the unsaved draft, never the saved file.
WB.applyAgentDraft=(board,pending=[],calculation=null,nativeDelivery=null)=>{
  const savedJson=WB.state.savedJson,path=WB.state.path,sha=WB.state.fileSha;
  if(WB.state.editorSession)WB.closeDialog(WB.state.editorSession.dialog);
  WB.replace({board,path,file_sha:sha,pending},false,false);WB.state.savedJson=savedJson;
  if(calculation){WB.setAgentResult(calculation.calculated);WB.state.resultPath=calculation.path;
    WB.state.reusableResult=nativeDelivery?{board:UI.clone(board),owner:nativeDelivery.owner,sessionId:nativeDelivery.sessionId,deliveryCapability:nativeDelivery.deliveryCapability}:{board:UI.clone(board),path:calculation.path};WB.state.mode="damage";}
  WB.render();
};
// Restore a lost native calculation projection without replacing or
// reinitializing the human-authored board.  In particular, selection, zoom,
// scroll, saved-file identity and the board object itself remain untouched.
WB.restoreAgentResultOnly=(calculation,nativeDelivery)=>{
  if(!calculation||typeof calculation!=="object"||!calculation.calculated||!nativeDelivery||typeof nativeDelivery.sessionId!=="string"||typeof nativeDelivery.deliveryCapability!=="string")return false;
  WB.setAgentResult(calculation.calculated);
  WB.state.reusableResult={board:UI.clone(WB.state.board),owner:nativeDelivery.owner,sessionId:nativeDelivery.sessionId,deliveryCapability:nativeDelivery.deliveryCapability};
  WB.state.mode="damage";WB.render();return true;
};
WB.dialog=(title,description)=>{
  // Whiteboard dialogs own drafts, pending promises or late-response guards.
  // They must be closed through their own explicit action, never displaced by
  // another feature opening a modal behind them.
  const d=window.AppDialogKernel?.create?.({title,description,className:"wb-dialog",onRequestClose:reason=>reason!=="replaced"})||null;
  if(!d){const message="已有窗口正在处理或保留未确认内容，请先完成或关闭该窗口后再试。";
    if(window.WS?.report)WS.report({factKey:"whiteboard.dialog.blocked",kind:"warning",message,scope:"local",projectToFooter:false});else setStatus(message);}
  return d;
};
WB.closeDialog=d=>{if(!d||d.wbClosed)return;d.wbCleanup?.();if(WB.state.editorSession?.dialog===d)WB.state.editorSession=null;d.wbClosed=true;
  if(typeof d.destroy==="function")d.destroy("whiteboard-close");else{d.close?.();d.remove?.();}};
WB.leave=()=>{
  if(WB.unappliedEditor())return new Promise(resolve=>{const d=WB.dialog("角色设置尚未应用","请返回角色设置并保存，或明确放弃本次角色设置后继续。"),finish=async discard=>{
    WB.closeDialog(d);if(!discard){if(!WB.state.editorSession){const id=Object.keys(WB.state.equipmentDrafts||{})[0];if(id)WB.editCharacter(id);}resolve(false);return;}
    if(WB.state.editorSession){WB.state.editorSession.dialog.wbDiscard=true;WB.closeDialog(WB.state.editorSession.dialog);}WB.state.equipmentDrafts={};resolve(await WB.leave());};
    if(!d){resolve(false);return;}d.appBody.append(UI.button("返回角色设置",()=>finish(false)),UI.button("放弃角色设置并继续",()=>finish(true)));d.addEventListener("cancel",()=>finish(false));});
  return !WB.dirty()?Promise.resolve(true):new Promise(resolve=>{
  const d=WB.dialog("白板尚未保存","可以先保存再继续，或明确放弃本次修改。切换页签会保留草稿。"),finish=v=>{WB.closeDialog(d);resolve(v);};
  if(!d){resolve(false);return;}d.appBody.append(UI.button("保存后继续",async()=>{if(await WB.save())finish(true);}),UI.button("放弃修改",()=>finish(true)),UI.button("继续编辑",()=>finish(false)));
  d.addEventListener("cancel",()=>finish(false));
});};
WB.requestClose=()=>{
  if(window.AgentWorkbenchApp?.ready)return window.AgentWorkbenchApp.requestClose();
  if(!WB.dirty()&&!window.PL?.dirty?.())return true;
  if(!WB.state.closing){WB.state.closing=true;(async()=>{try{if(await WB.leave()&&(!window.PL||await PL.leave()))await callApi("confirm_close");}
    finally{WB.state.closing=false;}})();}return false;
};
WB.newBoard=async(initial=false)=>{
  if(!initial&&!await WB.leave())return false;const token=WB.token(),r=await callApi("whiteboard_new");
  if(!WB.current(token))return false;if(r.code){UI.error(r);return false;}
  WB.replace(r.data,true);if(!initial)UI.tabs("whiteboard");return true;
};
WB.load=async path=>{
  if(!await WB.leave())return false;const token=WB.token(),r=await callApi("whiteboard_load",path);
  if(!WB.current(token))return false;if(r.code){UI.error(r);return false;}WB.replace(r.data,true);UI.tabs("whiteboard");setStatus("已加载创作白板，动作草稿可继续编辑。");return true;
};
WB.defaultPath=()=>`${RT.state.plansDir||"plans"}/${(WB.state.board.title||"排轴白板").replace(/[<>:"/\\|?*]/g,"_")}.whiteboard.json`;
WB.fileDialog=(mode,options={})=>{
  const save=mode==="save",d=WB.dialog(save?"另存创作白板":"打开创作白板",save?"保存可继续编辑的白板文件；不会改变当前动作和面板。":"输入 .whiteboard.json 文件路径。当前未保存内容会先询问处理方式。");
  if(!d){options.onAgentActionResult?.({status:"cancelled",message:"已有窗口尚未完成，未打开新的文件操作界面。"});return null;}
  const path=UI.input(save?(WB.state.path||WB.defaultPath()):"",()=>{});path.id="wb-path";
  window.WS?.bindAgentFileDialog?.(d,options);
  d.appBody.append(UI.field("白板文件路径",path),UI.button(save?"另存为":"打开",async()=>{
    if(!path.value.trim()){setStatus("请输入白板文件路径。");return;}
    const value=path.value.trim();try{if(save){if(await WB.save(value)){if(d.agentActionComplete)d.agentActionComplete("App已确认白板副本保存完成。");else WB.closeDialog(d);}}
      else{WB.closeDialog(d);const loaded=await WB.load(value);if(loaded)d.agentActionComplete?.("App已确认打开所选白板。");else d.agentActionFail?.("App未确认打开所选白板，当前文档保持不变。");}}
    catch(error){const message=String(error?.message||`${save?"另存":"打开"}白板时发生错误`);setStatus(message);d.agentActionFail?.(message);}
  }),UI.button("返回白板",()=>d.agentActionCancel?d.agentActionCancel():WB.closeDialog(d)));d.addEventListener("cancel",()=>d.agentActionCancel?d.agentActionCancel():WB.closeDialog(d));return d;
};
WB.openFile=options=>WB.fileDialog("open",options);
WB.saveAs=options=>WB.fileDialog("save",options);
WB.setMode=mode=>{if(!["actions","effects","damage"].includes(mode))return false;
  WB.state.mode=mode;WB.state.selectedDerived=null;WB.state.derivedBack=null;WB.render();return true;};
WB.openAdd=()=>{WB.state.mode="actions";WB.state.addOpen=true;WB.render();window.WS?.reveal?.("whiteboard","right");};
WB.fold=(label,key)=>{const d=UI.el("details",null,"wb-fold");d.open=!!WB.state[key];d.append(UI.el("summary",label));
  d.addEventListener("toggle",()=>{WB.state[key]=d.open;});return d;};
WB.checkStatus=value=>({passed:"通过",ready:"就绪",failed:"失败",pending:"待核实",invalid:"输入无效",not_checked:"未检查",not_verified:"未验证",partial:"部分完成",complete:"完整",calculated:"已计算",not_calculated:"未计算",unproven:"待证明"}[value]||"待核实");
WB.rawDetails=(label,value)=>{const d=UI.el("details",null,"wb-raw");d.append(UI.el("summary",label));let filled=false;
  d.addEventListener("toggle",()=>{if(d.open&&!filled){filled=true;d.append(UI.el("pre",JSON.stringify(value,null,2)));}});return d;};
WB.save=async path=>{
  if(WB.unappliedEditor()){setStatus("请先保存角色设置或明确放弃角色修改，再保存白板。");return false;}
  path=typeof path==="string"?path:($("#wb-path")?.value.trim()||WB.state.path||WB.defaultPath());
  const chatSnapshot=window.AgentWorkbenchApp?.beforeDocumentSave?.(),token=WB.token(),copy=UI.clone(WB.state.board);WB.state.activeSave=token;
  const r=await callApi("whiteboard_save",path,copy,path===WB.state.path?WB.state.fileSha:null);
  if(r.code){UI.error(r);return false;}if(!WB.current(token)){
    // A successful write advances the disk guard even when later edits keep the
    // document dirty. Never adopt a superseded save or another board's response.
    if(WB.state.activeSave===token&&token.board===WB.state.board&&token.path===WB.state.path&&token.sha===WB.state.fileSha&&token.json!==WB.json()){
      WB.state.path=r.data.path;WB.state.fileSha=r.data.file_sha;WB.render();}
    setStatus("所提交的白板副本已保存，但当前草稿随后有变化，仍需保存。");return false;}
  WB.state.path=r.data.path;WB.state.fileSha=r.data.file_sha;WB.state.savedJson=token.json;WB.state.pending=r.data.pending||[];if(r.data.revision_policy)WB.state.revisionPolicy=UI.clone(r.data.revision_policy);window.AgentWorkbenchApp?.documentSaved?.(chatSnapshot);WB.render();setStatus("创作白板已保存。",{path:r.data.path});return true;
};
WB.validate=async()=>{
  const token=WB.token(),r=await callApi("whiteboard_validate",UI.clone(WB.state.board));if(!WB.current(token))return;
  if(r.code){UI.error(r);return;}WB.state.pending=r.data.pending||[];if(r.data.revision_policy)WB.state.revisionPolicy=UI.clone(r.data.revision_policy);WB.render();setStatus("已检查白板结构；技能规则与可执行性仍需 Agent 核实。");
};
WB.handoff=()=>{
  if(WB.unappliedEditor()){setStatus("请先保存角色设置，再生成交接文件。");return;}
  const d=WB.dialog("保存交接文件给 Agent","将当前白板、待完善计算方案和中文任务说明保存到一个新目录。Agent 只补计算规则，保持你的动作、顺序和时点；若有冲突，另列建议。这里不会自动启动或发送给 Agent。");if(!d)return null;
  const path=UI.input(`${RT.state.plansDir||"plans"}/白板交接-${Date.now()}`,()=>{}),out=UI.el("div");
  d.appBody.append(UI.field("交接输出目录（使用新目录）",path),UI.button("生成交接文件",async()=>{
    const token=WB.token(),r=await callApi("whiteboard_handoff",UI.clone(WB.state.board),path.value.trim());if(d.wbClosed)return;
    out.innerHTML="";if(r.code){out.append(UI.error(r));return;}
    if(!WB.current(token)){out.append(UI.el("p","已保存所提交的交接副本；白板随后有变化，请用新目录重新交接。","wb-warning"));return;}
    const data=r.data;out.append(UI.el("p","交接文件已生成。将下面的任务说明路径交给 Agent："));
    for(const [label,key] of [["任务说明","instructions_path"],["用户白板","board_path"],["待完善计算方案","native_path"]]){
      const field=UI.area(data[key]||"",()=>{});field.readOnly=true;field.className="wb-path";out.append(UI.field(label,field));
      if(key==="instructions_path")out.append(UI.button("复制任务说明路径",async()=>{
        try{if(!window.navigator?.clipboard?.writeText)throw new Error("clipboard unavailable");await navigator.clipboard.writeText(field.value);setStatus("任务说明路径已复制。");}
        catch{field.focus?.();field.select?.();setStatus("路径已选中，请按 Ctrl + C 复制。");}}));}
    out.append(UI.el("p","Agent 完成后，在白板中载入完整计算交付 calculated.json。动作、装备或面板不符的回传会被拒绝。","wb-note"));
  }),out,UI.button("返回白板",()=>WB.closeDialog(d)));
  d.addEventListener("cancel",()=>WB.closeDialog(d));
};
WB.importResult=()=>{
  const d=WB.dialog("载入 Agent 计算结果","选择 Agent 输出的完整 calculated.json。App 会校验交付完整性和当前白板的动作、装备与面板。");if(!d)return null;
  const path=UI.input(WB.state.resultPath||"",()=>{}),out=UI.el("div");
  d.appBody.append(UI.field("完整计算交付文件路径",path),UI.button("校验并显示结果",async()=>{
    const token=WB.token(),r=await callApi("whiteboard_read_result",UI.clone(WB.state.board),path.value.trim());if(d.wbClosed||!WB.current(token))return;
    out.innerHTML="";if(r.code){out.append(UI.error(r));return;}
    WB.setAgentResult(r.data.calculated);WB.state.resultProjection=null;WB.state.resultPath=r.data.path;WB.state.staleResultPath=null;WB.state.mode="damage";WB.state.selectedDerived=null;
    WB.state.reusableResult={board:UI.clone(WB.state.board),path:r.data.path};WB.state.equipmentReport=null;
    WB.closeDialog(d);WB.render();setStatus("已载入经校验的计算回传。装备与面板修改可尝试应用重算；引擎无法完成的部分会列出交给 Agent。");
  }),out,UI.button("返回白板",()=>WB.closeDialog(d)));d.addEventListener("cancel",()=>WB.closeDialog(d));
};
WB.pick=({title,kind,weaponType="",selected,choose})=>{
  const line=WB.state.board.version_line,board=WB.state.board,d=WB.dialog(title);if(!d)return null;
  const search=UI.input("",()=>{}),list=UI.el("div",null,"wb-catalog"),nav=UI.el("div",null,"wb-toolbar");
  let page=1,request=0;const run=async()=>{
    const req=++request;list.innerHTML="";list.append(UI.el("p","正在查询资料…"));
    const name=search.value.trim(),method={character:"list_characters",weapon:"list_weapons",set:"list_artifact_sets"}[kind],
      args=kind==="character"?[name,"",0,line,page,50]:kind==="weapon"?[name,weaponType,0,"codex",line,page,50]:[name,0,"codex",line,page,50];
    const r=await callApi(method,...args);if(d.wbClosed||req!==request||board!==WB.state.board)return;list.innerHTML="";nav.innerHTML="";
    if(r.code){list.append(UI.error(r));return;}list.append(UI.el("p",`共 ${r.data.total} 项 · 第 ${page} 页`,"wb-note"));
    const elements={pyro:"火",hydro:"水",electro:"雷",cryo:"冰",anemo:"风",geo:"岩",dendro:"草",Fire:"火",Water:"水",Electric:"雷",Ice:"冰",Wind:"风",Rock:"岩",Grass:"草"};
    for(const item of r.data.items||[]){const element=item.element&&(elements[item.element]||(/^[火水雷冰风岩草]$/u.test(item.element)?item.element:"元素待补")),b=UI.button(`${item.name||"中文名待补"}${element?` · ${element}`:""}${item.rarity?` · ${item.rarity}星`:""}${selected===item.id?" · 已选择":""}`,async()=>{
      if(d.wbClosed||d.wbSelecting||board!==WB.state.board)return;await choose(item,d);});list.append(b);}
    const prev=UI.button("上一页",()=>{page--;run();}),next=UI.button("下一页",()=>{page++;run();});prev.disabled=page===1;next.disabled=page*50>=r.data.total;nav.append(prev,next);
  };
  const submit=()=>{page=1;run();};search.addEventListener("keydown",e=>{if(e.key==="Enter")submit();});
  d.appBody.append(UI.field("按中文名搜索",search),UI.button("搜索",submit),list,nav,UI.button("返回",()=>WB.closeDialog(d)));
  d.addEventListener("cancel",()=>WB.closeDialog(d));run();return d;
};
WB.addCharacter=()=>{
  if(WB.state.board.team.length>=4){setStatus("当前白板最多配置四名角色。");return;}
  WB.pick({title:"选择队伍角色",kind:"character",choose:async(item,d)=>{
    if(WB.state.board.team.some(c=>c.db_character_id===item.id)){setStatus("该角色已在队伍中。");return;}
    if(WB.state.board.team.length>=4){setStatus("队伍已满。");return;}
    const board=WB.state.board,line=board.version_line,c={id:UI.id("actor"),db_character_id:item.id,character_ref:{kind:"database",entity_type:"character",database_id:item.id,version_line:line},name:item.name,level:90,constellation:0,weapon:null,artifact_sets:[],panel_basis_mode:"database",
      panel:Object.fromEntries([...WB.panelFields.map(([key])=>[key,null]),...["base_atk","base_hp","base_def"].map(key=>[key,null]),["dmg_bonus",{}]]),notes:""};
    d.wbSelecting=true;setStatus("正在读取角色 90 级基础属性…");let message="已加入角色并填入可确认的基础白值；请选择武器并填写成品面板。";
    try{
      const r=await callApi("character_panel_basis",c.db_character_id,c.level,line,null);
      if(!r.code)for(const [key] of EW.baseFields){const value=r.data.panel_base?.[key];c.panel[key]=typeof value==="number"&&Number.isFinite(value)?value:null;}
      else message=`已加入角色，基础属性待补：${r.message||"资料读取失败"}。可在装备与面板中重新读取。`;
    }catch{message="已加入角色，基础资料读取失败；可在装备与面板中重新读取。";}
    finally{d.wbSelecting=false;}
    if(d.wbClosed||board!==WB.state.board||line!==board.version_line)return;
    if(board.team.length>=4||board.team.some(actor=>actor.db_character_id===item.id))return;
    WB.closeDialog(d);WB.change(board=>{board.team.push(c);WB.state.selectedActor=c.id;});
    setStatus(message);
  }});
};
WB.editCharacter=id=>{
  const original=WB.actor(id);if(!original)return;const board=WB.state.board,base=JSON.stringify(original),saved=WB.state.equipmentDrafts?.[id],
    retained=saved?.base===base?saved:null,d=WB.dialog(`${original.name} · 装备与初始面板`);if(!d)return null;
  const body=UI.el("div"),error=UI.el("div");
  d.classList.add("wb-equipment-dialog");let latestMeta={equipmentChanged:retained?.equipmentChanged||false,panelConfirmed:retained?.panelConfirmed||false};
  const editor=EW.mount(body,{actorDraft:retained?.draft||original,versionLine:board.version_line,weaponShape:"whiteboard",
    requirePanelConfirmation:retained?.equipmentChanged,panelConfirmed:retained?.panelConfirmed,
    isCurrent:()=>!d.wbClosed&&board===WB.state.board&&JSON.stringify(WB.actor(id))===base,
    onChange:(_,meta)=>{latestMeta=meta;error.textContent="";}});
  WB.state.editorSession={dialog:d,base,json:()=>JSON.stringify(editor.draft())};
  d.wbCleanup=()=>{if(!d.wbDiscard&&board===WB.state.board&&JSON.stringify(editor.draft())!==base){
      (WB.state.equipmentDrafts||={})[id]={base,draft:editor.draft(),equipmentChanged:latestMeta.equipmentChanged,panelConfirmed:latestMeta.panelConfirmed};
    }else if(WB.state.equipmentDrafts)delete WB.state.equipmentDrafts[id];editor.destroy();};
  const returnBack=()=>{WB.closeDialog(d);WB.render();},heading=UI.el("div",null,"ew-dialog-heading");
  heading.append(UI.button("← 返回白板",returnBack),UI.el("p","返回保留本次草稿；应用前不改变当前角色与计算结果。","ew-note"));
  const apply=async calculate=>{
    if(board!==WB.state.board||JSON.stringify(WB.actor(id))!==base){error.textContent="角色资料已变化，请返回后重新打开。";return;}
    const issues=editor.validate();if(issues.length){error.textContent=issues.join(" ");return;}
    const copy=editor.draft(),settingsJson=JSON.stringify(copy),candidate=UI.clone(board),token=WB.token();candidate.team[candidate.team.findIndex(c=>c.id===id)]=copy;
    error.textContent="正在检查角色设置…";const r=await callApi("whiteboard_validate",candidate);if(d.wbClosed||!WB.current(token))return;
    if(settingsJson!==JSON.stringify(editor.draft())){error.textContent="角色设置在检查期间变化，请再次保存以检查最新内容。";return;}
    if(r.code){error.innerHTML="";error.append(UI.error(r));return;}
    d.wbDiscard=true;WB.closeDialog(d);WB.change(current=>{current.team[current.team.findIndex(c=>c.id===id)]=copy;});
    if(calculate)await WB.recalculateEquipment();else setStatus("角色设置已应用到白板。可继续编辑，或使用“重新计算装备与面板”检查结果。");
  };
  const actions=UI.el("div",null,"ew-actions");actions.append(UI.button("应用并计算",()=>apply(true),"primary"),UI.button("保存角色设置",()=>apply(false)),
    UI.button("放弃本次修改",()=>{d.wbDiscard=true;WB.closeDialog(d);WB.render();},"danger"));
  d.appBody.append(heading,body,error,actions);d.addEventListener("cancel",event=>{event.preventDefault();returnBack();});
};
WB.recalculateEquipment=async()=>{
  if(WB.unappliedEditor()){setStatus("请先保存角色设置，再重新计算。");return;}
  const token=WB.token(),baseline=WB.state.reusableResult;
  WB.state.equipmentReport={state:"working",missing_inputs:[]};WB.render();
  const r=baseline?.deliveryCapability&&baseline?.sessionId
    ?baseline.owner==="native_dsh"
      ?await callApi("dsh_client_whiteboard_apply_equipment",{conversation_id:baseline.sessionId,delivery_capability:baseline.deliveryCapability,board:UI.clone(WB.state.board)})
      :await callApi("agent_whiteboard_apply_equipment",baseline.sessionId,baseline.deliveryCapability,UI.clone(WB.state.board))
    :await callApi("whiteboard_apply_equipment",UI.clone(WB.state.board),baseline?.board||null,baseline?.path||null);
  if(!WB.current(token))return;
  if(r.code){WB.state.equipmentReport={state:"pending",missing_inputs:[{message:r.message||"本次重算失败，请交给 Agent 核对。"}]};WB.render();UI.error(r);return;}
  WB.state.equipmentReport=r.data;WB.state.pending=r.data.missing_inputs||[];
  if(r.data.state==="ready"&&r.data.calculated){WB.setAgentResult(r.data.calculated);WB.state.resultProjection=null;WB.state.resultPath=null;WB.state.staleResultPath=null;
    WB.state.selectedDerived=null;setStatus("已按当前装备与面板重新计算。计算结果尚未保存为交付文件。");}
  else{WB.setAgentResult(null);WB.state.resultProjection=null;setStatus("当前设置已应用；部分计算需要 Agent 二次处理，请查看白板中的具体待完善项目。");}
  WB.render();
};
WB.saveEquipmentCandidate=()=>{
  const report=WB.state.equipmentReport;if(!report?.plan)return;const board=WB.state.board,json=WB.json(),
    d=WB.dialog("保存待完善方案给 Agent","保留当前已复用的规则、装备与面板，以及需要补正的计算项。把保存的方案交给 Agent，要求保持动作、顺序和时点，并完成检查、补充和重新计算。");if(!d)return null;
  const path=UI.input(`${RT.state.plansDir||"plans"}/白板换装待完善-${Date.now()}.plan.json`,()=>{}),out=UI.el("div");
  d.appBody.append(UI.field("新方案文件路径",path),UI.button("保存待完善方案",async()=>{
    if(board!==WB.state.board||json!==WB.json()||report!==WB.state.equipmentReport){out.textContent="白板已经变化，请返回后重新计算并生成最新候选。";return;}
    const destination=path.value.trim();if(!destination){out.textContent="请输入新方案文件路径。";return;}
    const r=await callApi("save_plan",destination,UI.clone(report.plan),null);if(d.wbClosed)return;out.innerHTML="";
    if(r.code){out.append(UI.error(r));return;}
    out.append(UI.el("p","已保存待完善方案。将此文件路径与下方问题交给 Agent；这里不会自动发送。"));
    const saved=UI.area(r.data.path||destination,()=>{});saved.readOnly=true;out.append(UI.field("交给 Agent 的方案路径",saved));
    for(const issue of report.missing_inputs||[])out.append(UI.el("p",issue.message||"需要核对的计算规则"));
  }),out,UI.button("返回白板",()=>WB.closeDialog(d)));d.addEventListener("cancel",()=>WB.closeDialog(d));
};
WB.removeCharacter=id=>{
  const actor=WB.actor(id);if(!actor)return;
  if((WB.state.board.capacity_intents||[]).some(intent=>intent.actor===id)){setStatus("该角色仍有次数意图，请先删除对应次数意图。");return;}
  if((WB.state.board.action_admission_intents||[]).some(intent=>intent.actor===id)){setStatus("该角色仍有动作接纳意图，请先删除对应意图。");return;}
  if(WB.state.board.actions.some(a=>a.character===id)){setStatus("该角色仍有动作，请先移除或转交这些动作，再移除角色。");return;}
  if(!window.confirm(`从白板移除${actor.name}？`))return;
  WB.change(board=>{board.team=board.team.filter(c=>c.id!==id);WB.state.selectedActor=board.team[0]?.id||null;});
};
WB.characterMenuItems=id=>{
  const actor=WB.actor(id),board=WB.state.board;if(!actor||!board)return [];
  const blocked=(board.capacity_intents||[]).some(intent=>intent.actor===id)
    ||(board.action_admission_intents||[]).some(intent=>intent.actor===id)
    ||board.actions.some(action=>action.character===id);
  return [
    {id:"select-character",label:"在队伍中选择",run:()=>{WB.state.selectedActor=id;WB.render();}},
    {id:"edit-character",label:"装备与面板",run:()=>WB.editCharacter(id)},
    {type:"separator"},
    {id:"remove-character",label:"移除角色",danger:true,enabled:!blocked,disabledReason:blocked?"请先处理该角色的动作或次数／接纳意图":"",run:()=>WB.removeCharacter(id)},
  ];
};
WB.capacityIntents=board=>Array.isArray((board||WB.state.board)?.capacity_intents)?(board||WB.state.board).capacity_intents:[];
WB.capacityOwner=(actionId,except=-1)=>WB.capacityIntents().find((intent,index)=>index!==except&&intent.action_ids.includes(actionId));
WB.capacityContextKey=value=>CAP.contextKey(value);
WB.capacityContextConflict=board=>{
  const contexts=WB.capacityIntents(board).map(intent=>intent.context).filter(value=>value!==null);
  return new Set(contexts.map(WB.capacityContextKey)).size>1;
};
WB.closeCapacityEditor=()=>{const session=WB.state.capacitySession;if(!session)return;WB.state.capacitySession=null;session.close();};
WB.removeCapacityIntent=index=>{
  const intent=WB.capacityIntents()[index];if(!intent)return false;
  if(!window.confirm(`删除次数意图 ${intent.id}？`))return false;
  WB.closeCapacityEditor();WB.change(board=>{board.capacity_intents.splice(index,1);});setStatus("次数意图已删除；旧目录响应与旧计算结果已失效。");return true;
};
WB.capacityIntentMenuItems=index=>WB.capacityIntents()[index]?[{id:"edit-capacity-intent",label:"编辑次数意图",run:()=>WB.editCapacityIntent(index)},
  {type:"separator"},{id:"remove-capacity-intent",label:"删除次数意图",danger:true,run:()=>WB.removeCapacityIntent(index)}]:[];
WB.renderCapacityIntents=box=>{
  const intents=WB.capacityIntents(),section=UI.el("section",null,"wb-capacity-intents"),unknown=intents.filter(intent=>intent.context===null).length;
  section.append(UI.el("h3",`技能次数意图（${intents.length}）`),
    UI.el("p",unknown?`次数场景未确认 · 尚有 ${unknown} 个次数意图未确认场景`:intents.length?"全部次数意图已明确场景":"次数场景未确认 · 当前没有次数意图",unknown?"wb-warning":"wb-note"));
  if(WB.capacityContextConflict())section.append(UI.el("p","多个次数意图的场景不一致，保存、目录与后续计算都会被阻断；请逐项统一或清除。","wb-danger"));
  intents.forEach((intent,index)=>{const actor=WB.actor(intent.actor),actions=intent.action_ids.map(id=>WB.action(id)?.label||id),card=UI.el("article",null,"wb-capacity-intent"),
      more=UI.button("⋯",()=>WB.openCommandMenu(more,`${intent.id}操作`,WB.capacityIntentMenuItems(index)),"wb-menu-trigger");card.tabIndex=0;
    card.append(UI.el("strong",`${actor?.name||intent.actor} · ${intent.model_ref?.id||"模型待选"}`),
      UI.el("p",`${actions.join("、")||"动作待选"} · ${intent.context?"普通开放世界":"次数场景未确认"}`),more);
    WB.bindCommandMenu(card,`${intent.id}操作`,()=>WB.capacityIntentMenuItems(index));section.append(card);});
  const capacitySchema=[2,3].includes(WB.state.board.schema_version),add=UI.button("＋ 新建次数意图",()=>WB.editCapacityIntent());add.disabled=!capacitySchema||!WB.state.board.actions.some(action=>action.action_type_id==="action.elemental_skill");section.append(add);
  if(!capacitySchema)section.append(UI.el("p","当前白板版本不支持次数意图；请重新加载后端升级结果。","wb-warning"));
  else if(!WB.state.board.actions.some(action=>action.action_type_id==="action.elemental_skill"))section.append(UI.el("p","请先在动作轴添加至少一个元素战技动作。","wb-note"));
  box.append(section);
};
WB.editCapacityIntent=index=>{
  const board=WB.state.board,intents=WB.capacityIntents(),original=index===undefined?null:intents[index];
  if(![2,3].includes(board?.schema_version)){setStatus("当前白板版本不支持次数意图，请重新加载升级后的白板。");return null;}
  const eligibleActors=board.team.filter(actor=>board.actions.some(action=>action.character===actor.id&&action.action_type_id==="action.elemental_skill"));
  if(!eligibleActors.length){setStatus("请先为角色添加元素战技动作。");return null;}
  WB.closeCapacityEditor();const initialActor=original?.actor||(eligibleActors.some(actor=>actor.id===WB.state.selectedActor)?WB.state.selectedActor:eligibleActors[0].id),
    draft=original?UI.clone(original):{id:UI.id("capacity-intent"),actor:initialActor,action_ids:[],version_line:board.version_line,
      model_ref:{id:"",revision:1},target:{character_id:null,skill_depot_id:null,skill_id:null,variant:"tap"},cooldown:null,initial_ready_in:null,context:null},
    baseline=WB.json(),d=WB.dialog(original?"编辑技能次数意图":"新建技能次数意图","从同一角色的元素战技动作中明确选择共用次数池，再从只读目录选择精确模型与目标。目录不写入容量值。");if(!d)return null;
  const actorBox=UI.el("div"),actionsBox=UI.el("fieldset",null,"wb-capacity-actions"),selected=UI.el("section",null,"capacity-selected"),
    scene=UI.el("section",null,"capacity-scene"),catalog=UI.el("section",null,"capacity-catalog-host"),errors=UI.el("div"),closed=()=>d.wbClosed||WB.state.board!==board||WB.json()!==baseline;
  let browser=null;
  const validIdentity=()=>typeof draft.model_ref?.id==="string"&&draft.model_ref.id.length>0&&Number.isInteger(draft.model_ref.revision)&&draft.model_ref.revision>=1&&
    Number.isInteger(draft.target?.character_id)&&Number.isInteger(draft.target?.skill_depot_id)&&Number.isInteger(draft.target?.skill_id)&&typeof draft.target?.variant==="string";
  const catalogDocument=()=>{const copy=UI.clone(board);copy.capacity_intents=(copy.capacity_intents||[]).filter((_intent,i)=>i!==index);
    if(validIdentity())copy.capacity_intents.push(UI.clone(draft));return copy;};
  const renderSelected=()=>{selected.innerHTML="";selected.append(UI.el("h4","当前模型引用"),UI.el("p",validIdentity()?`${draft.model_ref.id} / 修订 ${draft.model_ref.revision}；角色 ID ${draft.target.character_id}；技能组 ID ${draft.target.skill_depot_id}；技能 ID ${draft.target.skill_id}`:"尚未从目录选择模型。"));};
  const canQuery=()=>draft.action_ids.length>0&&!draft.action_ids.some(id=>WB.capacityOwner(id,index));
  const refreshCatalog=()=>{browser.invalidate();if(canQuery())browser.reload();else catalog.append(UI.el("p","请先选择一个或多个未被其他次数意图占用的元素战技动作。","wb-note"));};
  const clearIdentity=()=>{draft.model_ref={id:"",revision:1};draft.target={character_id:null,skill_depot_id:null,skill_id:null,variant:"tap"};delete draft.accepted_fingerprint;renderSelected();};
  const renderActions=()=>{actionsBox.innerHTML="";actionsBox.append(UI.el("legend","共用此次数池的元素战技动作"));
    const actions=board.actions.filter(action=>action.character===draft.actor&&action.action_type_id==="action.elemental_skill");
    for(const action of actions){const owner=WB.capacityOwner(action.id,index),line=UI.check(`${action.label||WB.typeName(action.action_type_id)} · 动作 ID ${action.id}${owner?` · 已属于 ${owner.id}`:""}`,draft.action_ids.includes(action.id),value=>{
        if(owner){setStatus("该动作已属于另一个次数意图，不能重复拥有。");return;}draft.action_ids=draft.action_ids.filter(id=>id!==action.id);if(value)draft.action_ids.push(action.id);clearIdentity();errors.innerHTML="";refreshCatalog();});
      const control=line.children[1];if(owner){control.disabled=true;control.checked=false;}actionsBox.append(line);}
  };
  const renderActor=()=>{actorBox.innerHTML="";actorBox.append(UI.field("次数意图角色",UI.select(eligibleActors.map(actor=>({value:actor.id,label:`${actor.name} · 角色 ID ${actor.db_character_id}`})),draft.actor,value=>{
      draft.actor=value;draft.action_ids=[];draft.version_line=board.version_line;clearIdentity();renderActions();errors.innerHTML="";refreshCatalog();})));};
  const setContext=next=>{if(CAP.sameContext(draft.context,next)){renderScene();return;}draft.context=next===null?null:UI.clone(next);clearIdentity();errors.innerHTML="";renderScene();refreshCatalog();};
  const renderScene=()=>{scene.innerHTML="";scene.append(UI.el("h4","次数场景"),UI.el("p",draft.context?"当前意图使用普通开放世界预设。":"次数场景未确认","capacity-scene-status"),
      UI.button("一键使用普通开放世界预设",()=>setContext(CAP.ordinaryContext())),
      UI.button("清除场景",()=>setContext(null)));};
  browser=CAP.catalogBrowser(catalog,{document:catalogDocument,actor:()=>draft.actor,memberIds:()=>draft.action_ids,method:"whiteboard_action_capacity_model_candidates",current:()=>!closed(),
    onSelect:item=>{CAP.applyCatalogSelection(draft,item);renderSelected();errors.innerHTML="";setStatus("已选择精确模型引用和目标；容量值仍由后端计算。");}});
  const close=()=>{browser.dispose();if(WB.state.capacitySession?.dialog===d)WB.state.capacitySession=null;WB.closeDialog(d);};
  WB.state.capacitySession={dialog:d,close};d.wbCleanup=()=>browser.dispose();
  const save=()=>{errors.innerHTML="";const actor=WB.actor(draft.actor),actions=draft.action_ids.map(id=>WB.action(id));let message="";
    if(closed())message="白板已变化，请关闭后重新编辑次数意图。";
    else if(!actor)message="次数意图角色不存在。";
    else if(!draft.action_ids.length||new Set(draft.action_ids).size!==draft.action_ids.length)message="请至少选择一个不重复的元素战技动作。";
    else if(actions.some(action=>!action||action.character!==draft.actor||action.action_type_id!=="action.elemental_skill"))message="所选动作必须存在、属于同一角色且均为元素战技。";
    else if(draft.action_ids.some(id=>WB.capacityOwner(id,index)))message="一个动作不能由多个次数意图重复拥有。";
    else if(!validIdentity()||draft.target.character_id!==actor.db_character_id)message="请先从目录选择与当前角色匹配的精确模型引用和目标。";
    else {const variant=draft.target.variant;if(actions.some(action=>action.variant&&action.variant!==variant))message="目录目标变体与所选动作变体不一致。";}
    const proposed=UI.clone(board);if(!message){proposed.capacity_intents=(proposed.capacity_intents||[]).filter((_intent,i)=>i!==index);proposed.capacity_intents.push(UI.clone(draft));if(WB.capacityContextConflict(proposed))message="多个非空次数场景必须完全一致；请统一或清除场景。";}
    if(message){errors.append(UI.el("p",message,"eq-error"));return false;}
    WB.closeDialog(d);WB.state.capacitySession=null;WB.change(current=>{if(index===undefined)current.capacity_intents.push(UI.clone(draft));else current.capacity_intents[index]=UI.clone(draft);});setStatus("次数意图已保存到白板；重新计算后才会生成容量值。");return true;};
  renderActor();renderActions();renderSelected();renderScene();d.appBody.append(actorBox,actionsBox,selected,scene,UI.el("h4","容量模型目录"),UI.button("重新读取全部目录",refreshCatalog),catalog,errors,
    UI.button("保存次数意图",save));if(original)d.appBody.append(UI.button("删除次数意图",()=>WB.removeCapacityIntent(index),"wb-danger"));d.appBody.append(UI.button("返回且不保存",close));d.addEventListener("cancel",close);
  if(canQuery())browser.reload();else catalog.append(UI.el("p","请先选择一个或多个未被其他次数意图占用的元素战技动作。","wb-note"));return d;
};
WB.admissionIntents=board=>Array.isArray((board||WB.state.board)?.action_admission_intents)?(board||WB.state.board).action_admission_intents:[];
WB.admissionOwner=(actionId,except=-1)=>WB.admissionIntents().find((intent,index)=>index!==except&&intent.action_id===actionId);
WB.admissionContext=()=>({contract:"static-action-admission-context-v1",mode:"ordinary_open_world",external_cooldown_effects:"none",external_energy_cost_effects:"none"});
WB.closeAdmissionEditor=()=>{const session=WB.state.admissionSession;if(!session)return;WB.state.admissionSession=null;session.close();};
WB.removeAdmissionIntent=index=>{const intent=WB.admissionIntents()[index];if(!intent)return false;
  if(!window.confirm(`删除动作接纳意图 ${intent.id}？`))return false;WB.closeAdmissionEditor();
  WB.change(board=>board.action_admission_intents.splice(index,1));setStatus("动作接纳意图已删除；旧目录响应、确认与计算结果已失效。");return true;};
WB.admissionIntentMenuItems=index=>WB.admissionIntents()[index]?[{id:"edit-admission-intent",label:"编辑动作接纳意图",run:()=>WB.editAdmissionIntent(index)},
  {type:"separator"},{id:"remove-admission-intent",label:"删除动作接纳意图",danger:true,run:()=>WB.removeAdmissionIntent(index)}]:[];
WB.renderAdmissionIntents=box=>{
  const intents=WB.admissionIntents(),section=UI.el("section",null,"wb-capacity-intents"),pending=intents.filter(row=>row.context===null||row.initial_ready_in===null).length;
  section.append(UI.el("h3",`动作接纳意图（${intents.length}）`),UI.el("p",pending?`尚有 ${pending} 项未明确场景或开场冷却；不会自动当作全部就绪`:intents.length?"全部意图已显式选择场景与开场状态":"尚未绑定受审动作门禁",pending?"wb-warning":"wb-note"));
  intents.forEach((intent,index)=>{const actor=WB.actor(intent.actor),action=WB.action(intent.action_id),card=UI.el("article",null,"wb-capacity-intent"),
      more=UI.button("⋯",()=>WB.openCommandMenu(more,`${intent.id}操作`,WB.admissionIntentMenuItems(index)),"wb-menu-trigger");card.tabIndex=0;
    card.append(UI.el("strong",`${actor?.name||intent.actor} · ${action?.label||intent.action_id}`),
      UI.el("p",`${intent.model_ref?.id||"模型待选"} / 修订 ${intent.model_ref?.revision??"待选"} · ${intent.context&&Array.isArray(intent.initial_ready_in)?"普通开放世界，开场显式就绪":"场景或开场状态待确认"}`),
      more);WB.bindCommandMenu(card,`${intent.id}操作`,()=>WB.admissionIntentMenuItems(index));section.append(card);});
  const eligible=WB.state.board.version_line==="release"&&WB.state.board.team.some(actor=>actor.constellation===0&&WB.state.board.actions.some(action=>action.character===actor.id&&action.action_type_id==="action.elemental_burst"&&action.variant==="default"&&!WB.admissionOwner(action.id)));
  const add=UI.button("＋ 新建动作接纳意图",()=>WB.editAdmissionIntent());add.disabled=WB.state.board.schema_version!==3||!eligible;section.append(add);
  if(WB.state.board.schema_version!==3)section.append(UI.el("p","当前白板尚未升级到版本 3；重新加载升级结果后才可保存动作接纳意图。","wb-warning"));
  else if(!eligible)section.append(UI.el("p","首片需要 release 资料线、0 命角色和已明确为“默认释放”的元素爆发动作。","wb-note"));box.append(section);
};
WB.admissionCatalogBrowser=(holder,options)=>{
  let active=true,serial=0;const groups=[
    ["exact","已审核 · 完全匹配",true],["partial","已审核 · 部分匹配",true],["pending","待审核或待处理",false],
    ["revoked","已撤销",false],["stale","资料已变化",false],["conflict","存在冲突",false],["not_evaluated","尚未评估",false]];
  const fail=response=>{holder.innerHTML="";const box=UI.el("section",null,"capacity-catalog-error");box.setAttribute("role","alert");
    box.append(UI.el("strong","动作接纳目录无法使用"),UI.el("p",response?.message||"请检查动作、角色和当前数据库。"));if(response?.details)box.append(DS.raw("错误定位",response.details));holder.append(box);};
  const draw=(items,total)=>{holder.innerHTML="";holder.className="capacity-catalog";holder.append(UI.el("p",`目录共 ${total} 项，已完整读取 ${items.length} 项；目录不提供可直接执行的确认。`,"capacity-catalog-status"));
    for(const [key,label,open] of groups){const rows=items.filter(item=>(item.catalog_group||"not_evaluated")===key),section=UI.el("details",null,`capacity-catalog-group capacity-catalog-${key}`);section.open=open;section.append(UI.el("summary",`${label}（${rows.length}）`));
      if(!rows.length)section.append(UI.el("p","当前分组没有项目。","wb-note"));for(const item of rows){const ref=item.model_ref||{},target=item.target||{},display=item.display||{},card=UI.el("article",null,"capacity-catalog-card"),selectable=["exact","partial"].includes(key)&&item.selection_status==="reference_selectable_for_fresh_preview";
        card.append(UI.el("strong",`${display.skill?.value||"技能名称待补"} · ${ref.id||"模型 ID 待补"} / 修订 ${ref.revision??"待补"}`),UI.el("p",`角色：${display.character?.value||target.character_id||"待补"}；技能组 ID ${target.skill_depot_id??"待补"}；技能 ID ${target.skill_id??"待补"}。`));
        if(selectable)card.append(UI.button("选择此模型引用",()=>options.onSelect(item)));const detail=UI.el("details",null,"capacity-catalog-advanced");detail.append(UI.el("summary","高级详情与原始 ID"),DS.raw("完整只读目录记录",item));card.append(detail);section.append(card);}holder.append(section);}};
  const reload=async()=>{const attempt=++serial;holder.innerHTML="";holder.append(UI.el("p","正在完整读取动作接纳目录…"));let cursor=null,items=[],total=null;const seen=new Set();
    try{do{const response=await callApi("whiteboard_action_admission_model_candidates",options.document(),options.actor(),options.action(),cursor,16,"all");
        if(!active||attempt!==serial||!options.current())return false;if(response.code){fail(response);return false;}const page=response.data,reported=page.counts?.filtered_models;
        if(!Number.isInteger(reported)||reported<0||!Array.isArray(page.items)||typeof page.has_more!=="boolean"){fail({message:"目录响应缺少可信的分页字段。"});return false;}if(total===null)total=reported;else if(total!==reported){fail({message:"目录分页期间计数发生变化，请重新读取。"});return false;}
        items.push(...page.items);if(page.has_more){if(!page.items.length||typeof page.next_cursor!=="string"||!page.next_cursor||page.next_cursor===cursor||seen.has(page.next_cursor)){fail({message:"动作接纳目录分页游标无效。"});return false;}seen.add(page.next_cursor);cursor=page.next_cursor;}else{if(page.next_cursor!==null){fail({message:"动作接纳目录末页游标无效。"});return false;}cursor=null;}}
      while(cursor);if(items.length!==total){fail({message:`目录计数不一致：应有 ${total} 项，实际读取 ${items.length} 项。`});return false;}draw(items,total);return true;}
    catch(error){if(active&&attempt===serial&&options.current())fail({message:`目录读取失败：${error.message}`});return false;}};
  return {reload,invalidate(){serial++;holder.innerHTML="";},dispose(){active=false;serial++;holder.innerHTML="";}};
};
WB.editAdmissionIntent=index=>{
  const board=WB.state.board,intents=WB.admissionIntents(),original=index===undefined?null:intents[index];if(board?.schema_version!==3){setStatus("动作接纳意图需要版本 3 白板。");return null;}
  const eligibleActors=board.team.filter(actor=>actor.constellation===0&&board.version_line==="release"&&board.actions.some(action=>action.character===actor.id&&action.action_type_id==="action.elemental_burst"&&action.variant==="default"));
  if(!eligibleActors.length){setStatus("请先为 0 命角色添加并明确一个 default 元素爆发动作。");return null;}WB.closeAdmissionEditor();
  const initialActor=original?.actor||(eligibleActors.some(actor=>actor.id===WB.state.selectedActor)?WB.state.selectedActor:eligibleActors[0].id),firstAction=board.actions.find(action=>action.character===initialActor&&action.action_type_id==="action.elemental_burst"&&action.variant==="default"&&!WB.admissionOwner(action.id,index)),
    draft=original?UI.clone(original):{id:UI.id("admission-intent"),actor:initialActor,action_id:firstAction?.id||"",version_line:"release",selection:{character_id:eligibleActors.find(actor=>actor.id===initialActor).db_character_id,skill_depot_id:null,skill_id:null,slot:"burst",constellation:0},variant:"default",model_ref:{id:"",revision:1},initial_ready_in:null,context:null},
    baseline=WB.json(),d=WB.dialog(original?"编辑动作接纳意图":"新建动作接纳意图","从动作轴选择一个明确的 default 元素爆发，再从完整只读目录选择受审模型。场景与开场状态不会默认填写。");if(!d)return null;
  const form=UI.el("div"),selected=UI.el("section"),scene=UI.el("section"),catalog=UI.el("section"),errors=UI.el("div"),closed=()=>d.wbClosed||WB.state.board!==board||WB.json()!==baseline;
  let browser=null;const validIdentity=()=>typeof draft.model_ref?.id==="string"&&draft.model_ref.id.length>0&&Number.isInteger(draft.model_ref.revision)&&Number.isInteger(draft.selection?.character_id)&&Number.isInteger(draft.selection?.skill_depot_id)&&Number.isInteger(draft.selection?.skill_id)&&draft.selection.slot==="burst"&&draft.selection.constellation===0;
  const clearIdentity=()=>{const actor=WB.actor(draft.actor);draft.model_ref={id:"",revision:1};draft.selection={character_id:actor?.db_character_id??null,skill_depot_id:null,skill_id:null,slot:"burst",constellation:0};};
  const renderSelected=()=>{selected.innerHTML="";selected.append(UI.el("h4","当前模型引用"),UI.el("p",validIdentity()?`${draft.model_ref.id} / 修订 ${draft.model_ref.revision}；技能组 ID ${draft.selection.skill_depot_id}；技能 ID ${draft.selection.skill_id}`:"尚未从目录选择模型。"));};
  const catalogDocument=()=>{const copy=UI.clone(board);copy.action_admission_intents=(copy.action_admission_intents||[]).filter((_row,i)=>i!==index);if(validIdentity())copy.action_admission_intents.push(UI.clone(draft));return copy;};
  const current=()=>!closed(),refresh=()=>{browser.invalidate();if(draft.action_id)browser.reload();};
  const renderForm=()=>{form.innerHTML="";form.append(UI.field("动作接纳角色",UI.select(eligibleActors.map(actor=>({value:actor.id,label:`${actor.name} · 角色 ID ${actor.db_character_id}`})),draft.actor,value=>{draft.actor=value;draft.action_id="";clearIdentity();renderForm();renderSelected();refresh();})));
    const actions=board.actions.filter(action=>action.character===draft.actor&&action.action_type_id==="action.elemental_burst"&&action.variant==="default"&&!WB.admissionOwner(action.id,index));if(original&&!actions.some(row=>row.id===original.action_id)){const currentAction=WB.action(original.action_id);if(currentAction)actions.push(currentAction);}
    form.append(UI.field("绑定的元素爆发动作",UI.select([{value:"",label:"请选择动作"},...actions.map(action=>({value:action.id,label:`${action.label} · ${action.start} 秒 · ${action.id}`}))],draft.action_id,value=>{draft.action_id=value;clearIdentity();renderSelected();refresh();})));};
  const renderScene=()=>{scene.innerHTML="";scene.append(UI.el("h4","场景与开场冷却"),UI.el("p",draft.context&&Array.isArray(draft.initial_ready_in)?"已显式选择普通开放世界，开场全部就绪。":"未确认；context 与 initial_ready_in 均保持 null，不会形成完整计算。",draft.context?"wb-note":"wb-warning"),
      UI.button("使用普通开放世界预设（开场全部就绪）",()=>{draft.context=WB.admissionContext();draft.initial_ready_in=[];renderScene();}),
      UI.button("清除为未知",()=>{draft.context=null;draft.initial_ready_in=null;renderScene();}));};
  browser=WB.admissionCatalogBrowser(catalog,{document:catalogDocument,actor:()=>draft.actor,action:()=>draft.action_id,current,onSelect:item=>{const target=item.target||{},actor=WB.actor(draft.actor);draft.model_ref=UI.clone(item.model_ref);draft.selection={character_id:target.character_id,skill_depot_id:target.skill_depot_id,skill_id:target.skill_id,slot:"burst",constellation:0};
      if(draft.selection.character_id!==actor?.db_character_id){clearIdentity();setStatus("目录模型角色与白板角色不一致，未采用。");}renderSelected();}});
  const close=()=>{browser.dispose();if(WB.state.admissionSession?.dialog===d)WB.state.admissionSession=null;WB.closeDialog(d);};WB.state.admissionSession={dialog:d,close};d.wbCleanup=()=>browser.dispose();
  const save=()=>{errors.innerHTML="";const actor=WB.actor(draft.actor),action=WB.action(draft.action_id);let message="";
    if(closed())message="白板已变化，请关闭后重新编辑。";else if(!actor||actor.constellation!==0||board.version_line!=="release")message="首片只支持 release 资料线的 0 命角色。";
    else if(!action||action.character!==draft.actor||action.action_type_id!=="action.elemental_burst"||action.variant!=="default")message="请选择属于该角色且已明确 default 变体的元素爆发动作。";
    else if(WB.admissionOwner(draft.action_id,index))message="一个动作不能由多个动作接纳意图重复拥有。";else if(!validIdentity()||draft.selection.character_id!==actor.db_character_id)message="请从目录选择与当前角色匹配的精确模型引用。";
    else if((draft.context===null)!==(draft.initial_ready_in===null))message="场景与开场冷却必须共同由显式预设写入或共同保持未知。";if(message){errors.append(UI.el("p",message,"eq-error"));return false;}
    WB.closeDialog(d);WB.state.admissionSession=null;WB.change(currentBoard=>{if(index===undefined)currentBoard.action_admission_intents.push(UI.clone(draft));else currentBoard.action_admission_intents[index]=UI.clone(draft);});setStatus("动作接纳意图已保存；执行确认仍需 fresh preview，白板本身不会签发。" );return true;};
  renderForm();renderSelected();renderScene();d.appBody.append(form,selected,scene,UI.el("h4","动作接纳模型目录"),UI.button("重新读取全部目录",()=>browser.reload()),catalog,errors,UI.button("保存动作接纳意图",save));if(original)d.appBody.append(UI.button("删除动作接纳意图",()=>WB.removeAdmissionIntent(index),"wb-danger"));d.appBody.append(UI.button("返回且不保存",close));d.addEventListener("cancel",close);if(draft.action_id)browser.reload();return d;
};
WB.render=()=>{
  const root=$("#tab-whiteboard");if(!root)return;const b=WB.state.board;root.innerHTML="";
  if(!b){
    const empty=UI.el("section",null,"wb-empty wb-start");empty.setAttribute("aria-labelledby","wb-start-title");
    const title=UI.el("h2","尚未打开创作白板");title.id="wb-start-title";
    const actions=UI.el("div",null,"wb-start-actions"),create=UI.button("新建创作白板",async()=>{
      if(window.WS?.invoke)await WS.invoke("new-board");else await WB.newBoard();
    },"primary"),open=UI.button("打开白板…",async()=>{
      if(window.WS?.invoke)await WS.invoke("open");else WB.openFile();
    });
    actions.append(create,open);empty.append(title,UI.el("p","从左上角“文件”菜单新建或打开白板。新建只建立本地文档；第一次向 Agent 发送有效消息后才会建立聊天历史。","wb-note"),actions);
    root.append(empty);window.WS?.refresh?.();return;
  }
  const head=UI.el("div",null,"wb-header"),title=UI.input(b.title,v=>WB.change(board=>board.title=v));title.className="wb-title";
  const status=UI.el("span",`${WB.dirty()?"● 尚未保存":WB.state.path?"已保存":"新白板"} · ${b.actions.length} 个动作`,"wb-status");
  status.title=WB.state.path||"保存后可再次打开此白板";
  head.append(UI.el("span","创作白板","wb-document-kind"),UI.field("白板名称",title),UI.field("轴长 / 秒",UI.input(b.duration,WB.setDuration,true)),status);
  const layoutControls=UI.el("span",null,"workspace-layout-buttons");layoutControls.setAttribute("data-workspace-controls","whiteboard");head.append(layoutControls);
  const layout=UI.el("div",null,"wb-layout"),team=UI.el("aside",null,"wb-team"),center=UI.el("div",null,"wb-center"),inspector=UI.el("aside",null,"wb-inspector");
  team.setAttribute("aria-label","白板队伍与装备");inspector.setAttribute("aria-label","白板动作编辑与只读详情");
  root.append(head);const workflowState=WB.workflowState();if(workflowState){const stage=window.AgentWorkbenchApp?.renderStageTracker?.(workflowState);if(stage){stage.classList.add("wb-stage-flow");root.append(stage);}}root.append(layout);layout.append(team,center,inspector);WB.renderTeam(team);WB.renderCenter(center);WB.renderInspector(inspector);
  window.WS?.mount?.(layout,{key:"whiteboard",left:".wb-team",right:".wb-inspector",repaint:()=>WB.draw()});
  window.AgentWorkbenchApp?.documentChanged?.();
};
WB.renderTeam=box=>{
  const b=WB.state.board;box.append(UI.el("h3","队伍与初始面板"));
  b.team.forEach((c,i)=>{const card=UI.button("",()=>{WB.state.selectedActor=c.id;WB.render();},`wb-team-card${WB.state.selectedActor===c.id?" active":""}`);
    card.style.setProperty?.("--wb-color",WB.colors[i]);card.append(UI.el("strong",c.name),UI.el("span",`${c.level} 级 · ${c.constellation} 命 · ${c.weapon?.name||"武器未选"}`),
      UI.el("span",c.artifact_sets.map(s=>`${s.name} ${s.count} 件`).join(" + ")||"套装未选"),UI.el("span",`攻击 ${c.panel.atk??"待填"} · 充能 ${c.panel.energy_recharge==null?"待填":`${WB.number(c.panel.energy_recharge*100)}%`}`));
    if(c.character_ref?.kind==="provisional"||c.weapon?.entity_ref?.kind==="provisional"||(c.artifact_sets||[]).some(s=>s.entity_ref?.kind==="provisional")){
      card.append(UI.el("span","临时资料 · 仅限当前方案 · 正式结果将标注近似","wb-warning"));
    }
    WB.bindCommandMenu(card,`${c.name}操作`,()=>WB.characterMenuItems(c.id));box.append(card);
    if(WB.state.selectedActor===c.id){const tools=UI.el("div",null,"wb-toolbar"),more=UI.button("更多…",()=>WB.openCommandMenu(more,`${c.name}操作`,WB.characterMenuItems(c.id)),"wb-menu-trigger");
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
  const notes=UI.area(b.notes,value=>WB.change(board=>board.notes=value)),memo=WB.fold("排轴目标与备注","notesOpen");memo.append(UI.field("排轴目标与备注",notes));box.append(memo);
  if(WB.state.pending.length){const d=UI.el("details");d.append(UI.el("summary",`待完善项目（${WB.state.pending.length}）`));
    for(const issue of WB.state.pending)d.append(UI.el("p",issue.message,"wb-note"));box.append(d);}
};
WB.project=()=>{
  if(WB.state.mode==="actions")return {lanes:WB.state.board.team.map(c=>({id:c.id,label:c.name})),events:WB.state.board.actions.map(a=>({id:a.id,character:a.character,start:a.start,label:WB.actionName(a),action:a}))};
  if(!WB.state.result)return {lanes:[],events:[]};const calculated=WB.state.result;
  WB.state.resultProjection ||= TD.build({plan:calculated.plan,template:calculated.plan.resolved_template||calculated.plan.manual_template,result:calculated.result,
      executionModel:calculated.report?.mechanics_model,executionTemplate:calculated.report?.mechanics_input_template,current:true});
  let events=WB.state.resultProjection.views[WB.state.mode].events;const lanes=[];
  if(WB.state.mode==="effects"){
    events=TD.groupCoverage(events,WB.state.board.team);
    if(WB.state.effectFilter==="coverage")events=events.filter(e=>e.kind==="coverage");
    if(WB.state.effectFilter==="points")events=events.filter(e=>e.kind!=="coverage");
    // Interval lanes precede point lanes. Distinct overlapping states need their
    // own row; this layout never joins time windows or changes their evidence.
    const ends=new Map(),mapped=[];
    for(const event of [...events.filter(e=>e.kind==="coverage"),...events.filter(e=>e.kind!=="coverage")]){
      const coverage=event.kind==="coverage",source=event.sourceKey||event.character||"team",key=`${coverage?"coverage":"points"}:${source}`;
      let slot=0;if(coverage){const rows=ends.get(key)||[];slot=rows.findIndex(end=>end<=event.start);if(slot<0)slot=rows.length;rows[slot]=event.end;ends.set(key,rows);}
      const id=`${key}:${slot}`,label=event.sourceLabel||WB.actor(event.character)?.name||"全队";
      if(!lanes.some(l=>l.id===id))lanes.push({id,label,kind:coverage?"coverage":"points"});
      mapped.push({...event,lane:id});
    }
    return {lanes,events:mapped};
  }
  if(WB.state.mode==="damage"){
    const calculations=new Map(events.filter(e=>e.kind==="calculation").map(e=>[`${e.detail?.hit_id}:${e.start}`,e]));
    events=events.filter(e=>WB.state.damageFocus==="all"||e.kind!=="calculation").map(e=>{
      const calculation=e.kind==="damage"?calculations.get(`${e.detail?.hit_id}:${e.start}`):null;
      return calculation?{...e,detail:{...e.detail,calculation:calculation.detail}}:e;
    });
  }
  for(const event of events){const id=event.sourceKey||event.character||"team";if(!lanes.some(l=>l.id===id))lanes.push({id,label:event.sourceLabel||WB.actor(event.character)?.name||"全队"});}
  return {lanes,events:events.map(e=>({...e,lane:e.sourceKey||e.character||"team"}))};
};
WB.renderCenter=box=>{
  const nav=UI.el("div",null,"wb-toolbar wb-mode-tabs");nav.setAttribute("aria-label","白板时间轴视图");
  for(const [key,label] of [["actions","动作轴"],["effects","效果轴"],["damage","伤害轴"]]){
    const button=UI.button(label,()=>WB.setMode(key),WB.state.mode===key?"active":"");button.setAttribute("aria-pressed",WB.state.mode===key);nav.append(button);
  }
  nav.append(UI.el("span",WB.state.mode==="actions"?"可编辑":"只读","wb-mode-state"));box.append(nav);
  const tools=UI.el("div",null,"wb-toolbar wb-timeline-tools");
  if(WB.state.mode==="actions")tools.append(UI.button("＋ 添加动作",WB.openAdd));
  tools.append(UI.button("−",()=>WB.zoom(1/1.3)),UI.el("span",`${Math.round(WB.state.scale*100)}%`,"wb-scale"),
    UI.button("＋",()=>WB.zoom(1.3)),UI.button("适应全轴",()=>{WB.state.scale=1;WB.state.scroll=0;WB.render();}));
  if(WB.state.mode==="effects"){
    const filters=UI.el("div",null,"wb-toolbar wb-effect-filters");filters.setAttribute("aria-label","效果视图筛选");
    for(const [key,label] of [["all","全部效果"],["coverage","持续 Buff"],["points","命中与触发"]]){
      const button=UI.button(label,()=>{WB.state.effectFilter=key;WB.state.selectedDerived=null;WB.render();},WB.state.effectFilter===key?"active":"");
      button.setAttribute("aria-pressed",WB.state.effectFilter===key);filters.append(button);
    }tools.append(filters);
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
    if(topology){const stats=WB.topologyStats(),status=window.AgentWorkbenchApp?.stageStatusText?.(stats.status)||({complete:"本阶段完成",ready:"本阶段完成",partial:"仍有缺项",in_progress:"正在完善"}[stats.status]||"状态待确认"),binding=WB.bindingStats(),acceptance=WB.workflowState()?.calculationAcceptance||"not_requested";
      empty.append(UI.el("h3","事件拓扑草稿"),UI.el("p","拓扑结构只描述已识别事件、生命周期和关系；伤害数值会在数值范式和严格计算阶段披露。","wb-note"),
        UI.el("h4","拓扑结构"),UI.el("p",`事件节点 ${stats.nodes} · 拓扑关系 ${stats.bindings} · 待完善 ${stats.unresolved} · ${status}`,stats.unresolved?"wb-warning":"wb-note"),
        UI.el("h4","执行绑定"));
      if(binding.receipt){const label=window.AgentWorkbenchApp?.bindingGateText?.(binding.receipt,WB.workflowState()?.index??2)||`${binding.status||"状态待确认"} · 已绑定 ${binding.bound}/${binding.expected} · 已解析 ${binding.resolved}`;
        empty.append(UI.el("p",label,binding.status==="complete"?"wb-note":"wb-warning"));
        if(binding.missing.length)empty.append(UI.el("p",`缺失节点：${binding.missing.join("、")}${binding.missingTruncated?"（仅显示前几项）":""}`,"wb-warning"));
        if(binding.issue)empty.append(UI.el("p",`首个问题：${WB.bindingIssueText(binding.issue)}`,"wb-warning"));
        empty.append(UI.el("p","执行绑定回执只核验当前输入的引用覆盖和新鲜度；角色特有语义仍由 Agent 声明，未由 App 或引擎验证。","wb-note"));
      }else empty.append(UI.el("p","尚未返回执行绑定回执；拓扑关系不能代替执行输入绑定。","wb-warning"));
      empty.append(UI.el("h4","计算接纳"),UI.el("p",window.AgentWorkbenchApp?.acceptanceText?.(acceptance)||({accepted:"可应用",rejected:"已拒绝",diagnostic_only:"仅诊断，不可应用",not_requested:"尚未请求"}[acceptance]||"尚未请求"),"wb-note"));
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
  const svg=document.createElementNS("http://www.w3.org/2000/svg","svg");svg.id="wb-svg";wrap.append(svg);box.append(wrap);
  WB.state.wrap=wrap;WB.state.svg=svg;wrap.scrollLeft=WB.state.scroll;wrap.addEventListener("scroll",()=>{WB.state.scroll=wrap.scrollLeft;WB.draw();});
  wrap.addEventListener("wheel",e=>{if(e.ctrlKey){e.preventDefault();WB.zoom(e.deltaY<0?1.15:1/1.15,e.clientX-wrap.getBoundingClientRect().left);}
    else if(e.shiftKey){e.preventDefault();wrap.scrollLeft+=e.deltaY;}},{passive:false});
  wrap.addEventListener("pointerdown",e=>{if(e.button===0||e.button===1)WB.beginPointer(e,null);});
  wrap.addEventListener("pointermove",WB.movePointer);wrap.addEventListener("pointerup",WB.endPointer);wrap.addEventListener("pointercancel",e=>WB.endPointer(e,true));
  wrap.addEventListener("keydown",e=>{
    if(e.target!==wrap)return;if(e.key==="+"||e.key==="="){e.preventDefault();WB.zoom(1.3);return;}if(e.key==="-"){e.preventDefault();WB.zoom(1/1.3);return;}
    if(WB.state.mode!=="actions")return;const action=WB.action(WB.state.selectedAction);if(!action)return;
    if(["ArrowLeft","ArrowRight"].includes(e.key)){e.preventDefault();WB.setTime(action.id,WB.clampTime(action.start+(e.key==="ArrowLeft"?-1:1)*(e.shiftKey?1:0.1)));}
  });WB.draw();
};
WB.zoom=(factor,anchor)=>{
  const wrap=WB.state.wrap,width=wrap?.clientWidth||700,x=anchor??width/2,old=WB.state.scale,next=Math.max(1,Math.min(40,old*factor));
  const left=WB.state.geometry?.left||125;WB.state.scale=next;WB.state.scroll=Math.max(0,(WB.state.scroll+x-left)*next/old-x+left);WB.render();
};
WB.svg=(tag,attrs,text)=>{const n=document.createElementNS("http://www.w3.org/2000/svg",tag);for(const [key,value] of Object.entries(attrs))n.setAttribute(key,String(value));
  if(text!=null)n.textContent=text;return n;};
WB.draw=()=>{
  const {wrap,svg}=WB.state;if(!wrap||!svg)return;const b=WB.state.board,left=WB.state.mode==="effects"?205:125,available=Math.max(260,(wrap.clientWidth||700)-left-35),pps=available/b.duration*WB.state.scale,
    width=left+b.duration*pps+35,projection=WB.project(),lanes=projection.lanes.length?projection.lanes:[{id:"empty",label:"等待添加"}],laneHeight=WB.state.mode==="actions"?108:62,
    height=Math.max(220,46+lanes.length*laneHeight);
  WB.state.geometry={left,pps,width};svg.innerHTML="";svg.setAttribute("width",width);svg.setAttribute("height",height);
  const visibleStart=Math.max(0,(wrap.scrollLeft-left-150)/pps),visibleEnd=(wrap.scrollLeft+(wrap.clientWidth||700)+150-left)/pps;
  let step=1;while(step*pps<60)step=step<5?step*2:step*2.5;
  const laneFill=(lane,i)=>lane.kind==="coverage"
    ?WB.themeColor("color-axis-coverage-lane","#213036")
    :WB.themeColor(i%2?"color-axis-lane-odd":"color-axis-lane-even",i%2?"#1c2430":"#202937");
  lanes.forEach((lane,i)=>svg.append(WB.svg("rect",{x:0,y:40+i*laneHeight,width,height:laneHeight-3,fill:laneFill(lane,i)})));
  for(let t=Math.max(0,Math.floor(visibleStart/step)*step);t<=Math.min(b.duration,visibleEnd);t+=step){const x=left+t*pps;svg.append(WB.svg("line",{x1:x,y1:25,x2:x,y2:height,stroke:WB.themeColor("color-axis-grid","#303847")}),
    WB.svg("text",{x,y:18,fill:WB.themeColor("color-axis-label-muted","#adb9cc"),"font-size":11},`${WB.number(t)} 秒`));}
  const visible=projection.events.filter(e=>e.start<=visibleEnd&&(e.end??e.start)>=visibleStart),cap=800,slots=new Map(),crowded=new Map(),shown=[];
  if(WB.state.mode==="actions")shown.push(...visible);
  else {const buckets=new Map();for(const event of visible){
    if(event.end>event.start){shown.push(event);continue;}
    const key=`${event.lane||event.character}:${Math.floor(event.start*pps/18)}`;
    if(!buckets.has(key)){const group={...event,events:[]};buckets.set(key,group);shown.push(group);}buckets.get(key).events.push(event);
  }for(let i=0;i<shown.length;i++){const group=shown[i];if(!group.events)continue;
    if(group.events.length===1)shown[i]=group.events[0];else shown[i]={...group,id:`group:${group.events[0].id}`,kind:"group",label:`${group.events.length} 条相邻记录`,value:null};}}
  for(const event of shown.slice(0,cap)){
    const lane=lanes.findIndex(l=>l.id===(event.lane||event.character)),y=40+Math.max(0,lane)*laneHeight,x=left+event.start*pps,
      actorIndex=b.team.findIndex(c=>c.id===event.character),color=event.detail?.state_status==="exhausted"?WB.themeColor("color-axis-exhausted","#89919c"):WB.colors[Math.max(0,actorIndex)%4],selected=WB.state.mode==="actions"?event.id===WB.state.selectedAction:event.id===WB.state.selectedDerived?.id,
      coverage=event.kind==="coverage",range=`${WB.number(event.start)}${event.end>event.start?` → ${WB.number(event.end)}`:""} 秒`,
      description=`${event.sourceLabel||WB.actor(event.character)?.name||"全队"} · ${event.label} · ${range}${coverage?` · ${event.detail?.receipt_id?"实例角色范围":"接收角色"}：${WB.recipients(event)}`:""}${event.value!=null?` · ${WB.number(event.value)} 伤害`:""}`,
      g=WB.svg("g",{class:WB.state.mode==="actions"?"wb-action":coverage?"wb-derived wb-coverage":"wb-derived",tabindex:0,role:"button","aria-label":description});
    g.append(WB.svg("title",{},description));
    if(WB.state.mode==="actions"){
      const ends=slots.get(lane)||[-Infinity,-Infinity,-Infinity],slot=ends.findIndex(end=>end<x-5),cardWidth=Math.max(48,Math.min(98,event.label.length*12+16));
      if(slot<0){if(!crowded.has(lane))crowded.set(lane,{x,events:[]});crowded.get(lane).events.push(event);continue;}
      ends[slot]=x+cardWidth+5;slots.set(lane,ends);const top=y+8+slot*27;
      g.append(WB.svg("rect",{x:x-4,y:top,width:cardWidth,height:22,rx:5,fill:color,stroke:selected?WB.themeColor("color-axis-selected","#fff"):WB.themeColor("color-axis-event-border","#182230"),"stroke-width":selected?3:1}),
        WB.svg("text",{x:x+3,y:top+16,fill:WB.themeColor("color-axis-event-text","#14202a"),"font-size":12,"pointer-events":"none"},event.label.slice(0,7)));
      g.addEventListener("pointerdown",e=>{if(e.button===1){WB.beginPointer(e,null);return;}WB.beginPointer(e,event.id);});
      WB.bindCommandMenu(g,`${event.label}操作`,()=>WB.actionMenuItems(event.id));
    }else if(event.end>event.start){const barWidth=Math.max(3,(event.end-event.start)*pps);g.append(WB.svg("rect",{x,y:y+14,width:barWidth,height:28,rx:4,fill:color,opacity:0.78,stroke:selected?WB.themeColor("color-axis-selected","#fff"):color,"stroke-width":selected?2:1}));
      if(coverage){const label=WB.svg("svg",{x:x+1,y:y+15,width:Math.max(1,barWidth-2),height:26,overflow:"hidden","pointer-events":"none"});
        label.append(WB.svg("text",{x:5,y:18,fill:WB.themeColor("color-axis-event-text","#12242b"),"font-size":11},`${event.detail?.state_status==="exhausted"?"耗尽 · ":""}${range}`));g.append(label);}}
    else if(event.events){g.append(WB.svg("rect",{x:x-8,y:y+14,width:Math.max(22,String(event.events.length).length*7+12),height:23,rx:5,fill:color,stroke:selected?WB.themeColor("color-axis-selected","#fff"):WB.themeColor("color-axis-event-border","#172330")}),
      WB.svg("text",{x:x-2,y:y+30,fill:WB.themeColor("color-axis-event-text","#14202a"),"font-size":11,"pointer-events":"none"},String(event.events.length)));}
    else{g.append(WB.svg("path",{d:`M ${x} ${y+15} l 7 11 l -7 11 l -7 -11 z`,fill:event.kind==="external_damage"?WB.themeColor("color-axis-reaction","#eaa8c9"):color,stroke:selected?WB.themeColor("color-axis-selected","#fff"):"none"}));}
    if(WB.state.mode!=="actions")g.addEventListener("pointerdown",e=>{
      // Let the following native click select this read-only event. Starting a
      // blank-space drag would redraw its node on pointerup before click fires.
      if(e.button===0){e.stopPropagation();WB.state.dragMoved=false;}
    });
    if(WB.state.mode==="damage"&&event.value!=null)g.append(WB.svg("text",{x:x+9,y:y+32,fill:WB.themeColor("color-axis-label","#dce7f4"),"font-size":11},WB.number(event.value)));
    g.addEventListener("click",e=>{e.stopPropagation();if(WB.state.dragMoved)return;WB.selectEvent(event);});
    g.addEventListener("keydown",e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();WB.selectEvent(event);}});svg.append(g);
  }
  for(const [lane,group] of crowded){const marker=WB.svg("g",{class:"wb-derived",tabindex:0,role:"button","aria-label":`查看同区域 ${group.events.length} 个动作`}),y=40+lane*laneHeight+99;
    marker.append(WB.svg("text",{x:Math.max(left,group.x),y,fill:WB.themeColor("color-axis-warning","#f4cd84"),"font-size":11},`＋ ${group.events.length} 项动作`));
    const open=()=>WB.showCrowded(group.events);marker.addEventListener("click",open);marker.addEventListener("keydown",e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();open();}});svg.append(marker);}
  // Source labels stay readable when panning every timeline mode.
  lanes.forEach((lane,i)=>{const y=40+i*laneHeight,pinned=true,labelX=wrap.scrollLeft;
    if(pinned)svg.append(WB.svg("rect",{x:labelX,y,width:left-5,height:laneHeight-3,fill:laneFill(lane,i)}));
    const label=WB.svg("svg",{x:labelX+8,y,width:left-20,height:laneHeight-3,overflow:"hidden"});
    label.append(WB.svg("title",{},lane.label));
    if(lane.kind){const [owner,...parts]=lane.label.split(" · ");label.append(WB.svg("text",{x:0,y:17,fill:WB.themeColor("color-axis-label","#d1dce9"),"font-size":12},owner),
      WB.svg("text",{x:0,y:34,fill:WB.themeColor("color-axis-label","#d1dce9"),"font-size":12},parts.join(" · ")),
      WB.svg("text",{x:0,y:51,fill:lane.kind==="coverage"?WB.themeColor("color-axis-buff","#92d8b6"):WB.themeColor("color-axis-label-muted","#879cb7"),"font-size":10},lane.kind==="coverage"?"持续 Buff":"命中与触发"));}
    else label.append(WB.svg("text",{x:0,y:31,fill:WB.themeColor("color-axis-label","#d1dce9"),"font-size":12},lane.label.length>14?`${lane.label.slice(0,13)}…`:lane.label));svg.append(label);});
  if(shown.length>cap)svg.append(WB.svg("text",{x:wrap.scrollLeft+150,y:height-9,fill:WB.themeColor("color-axis-warning","#f2c678"),"font-size":12},`当前窗口共 ${shown.length} 项，先显示 ${cap} 项；放大后可查看更多。`));
  if(WB.state.mode==="actions"){const x=left+WB.state.cursor*pps;svg.append(WB.svg("line",{x1:x,y1:25,x2:x,y2:height,stroke:WB.themeColor("color-axis-cursor","#e5bd72"),"stroke-dasharray":"4 4"}));}
  if(WB.state.pointer?.id&&WB.state.pointer.next!=null){const x=left+WB.state.pointer.next*pps;svg.append(WB.svg("line",{x1:x,y1:25,x2:x,y2:height,stroke:WB.themeColor("color-axis-selected","#fff2b5"),"stroke-width":2}),
    WB.svg("text",{x:x+5,y:38,fill:WB.themeColor("color-axis-selected","#fff2b5"),"font-size":12},`${WB.number(WB.state.pointer.next)} 秒`));}
};
WB.showCrowded=events=>{
  const d=WB.dialog("密集动作","只调整卡片显示位置，不改变动作时点。选择一项后在右侧编辑；全部动作也可在顺序表查找。");if(!d)return null;
  const body=UI.el("div",null,"wb-catalog");let page=0;
  const render=()=>{body.innerHTML="";for(const event of events.slice(page*40,page*40+40))body.append(UI.button(`${WB.number(event.start)} 秒 · ${WB.actor(event.character)?.name||"角色"} · ${event.label}`,()=>{
    WB.closeDialog(d);WB.selectEvent(event);}));const controls=UI.el("div",null,"wb-toolbar"),prev=UI.button("前 40 项",()=>{page--;render();}),next=UI.button("后 40 项",()=>{page++;render();});
    prev.disabled=page===0;next.disabled=(page+1)*40>=events.length;controls.append(prev,next);body.append(controls);};
  d.appBody.append(body,UI.button("返回白板",()=>WB.closeDialog(d)));d.addEventListener("cancel",()=>WB.closeDialog(d));render();
};
WB.selectEvent=(event,fromGroup=false)=>{
  if(!fromGroup)WB.state.derivedBack=null;if(event.events)WB.state.groupPage=0;
  if(WB.state.mode==="actions"){WB.state.selectedAction=event.id;WB.state.selectedActor=event.character;WB.state.cursor=event.start;}
  else {WB.state.selectedDerived=event;if(event.events)WB.state.derivedBack=null;}WB.render();window.WS?.reveal?.("whiteboard","right");
};
WB.recipients=event=>{
  const names=event.beneficiary_names||event.characters?.map(id=>WB.actor(id)?.name||"角色资料未匹配");
  return names?.length?names.join("、"):WB.actor(event.character)?.name||"按计算来源的目标条件生效";
};
WB.beginPointer=(e,id)=>{
  if(e.button!==0&&e.button!==1)return;if(id&&WB.state.mode!=="actions")return;
  e.preventDefault();e.stopPropagation();const wrap=WB.state.wrap;WB.state.dragMoved=false;
  WB.state.pointer={id,board:WB.state.board,revision:WB.state.board.revision,pointerId:e.pointerId,x:e.clientX,scroll:wrap.scrollLeft,
    original:id?WB.action(id)?.start:null,next:null};wrap.setPointerCapture?.(e.pointerId);
};
WB.movePointer=e=>{
  const drag=WB.state.pointer;if(!drag||drag.pointerId!==e.pointerId)return;const delta=e.clientX-drag.x;
  if(Math.abs(delta)>3)WB.state.dragMoved=true;
  if(drag.id){drag.next=WB.clampTime(drag.original+delta/WB.state.geometry.pps);WB.draw();setStatus(`释放动作将移动到 ${WB.number(drag.next)} 秒`);}
  else WB.state.wrap.scrollLeft=Math.max(0,drag.scroll-delta);
};
WB.endPointer=(e,cancel=false)=>{
  const drag=WB.state.pointer;if(!drag||drag.pointerId!==e.pointerId)return;WB.state.pointer=null;
  WB.state.wrap.releasePointerCapture?.(e.pointerId);
  if(cancel||drag.board!==WB.state.board||drag.revision!==WB.state.board.revision)return;
  if(drag.id){WB.state.selectedAction=drag.id;WB.state.selectedActor=WB.action(drag.id)?.character;window.WS?.reveal?.("whiteboard","right");
    if(drag.next!=null&&WB.state.dragMoved){WB.setTime(drag.id,drag.next);return;}WB.render();}
  else if(!WB.state.dragMoved){const rect=WB.state.wrap.getBoundingClientRect();WB.state.cursor=WB.clampTime((e.clientX-rect.left+WB.state.wrap.scrollLeft-WB.state.geometry.left)/WB.state.geometry.pps);WB.render();}
};
WB.renderDerived=box=>{
  const event=WB.state.selectedDerived;box.append(UI.el("h3",WB.state.mode==="effects"?"效果详情":"伤害详情"));
  if(!event){box.append(UI.el("div","点击时间轴上的区间或标记，在这里查看来源、时间与计算说明。","wb-empty-detail"));return;}
  if(event.events){
    box.append(UI.el("h4",`${event.events.length} 条相邻记录`),UI.el("p","仅合并显示位置，未合并伤害或改动时点。选择记录查看明细。","wb-note"));
    if(WB.state.mode==="damage"){
      const damages=event.events.filter(e=>e.kind!=="calculation"&&Number.isFinite(e.value));
      if(damages.length)box.append(UI.el("p",`组内合计：${WB.number(damages.reduce((sum,e)=>sum+e.value,0))}（${damages.length} 条伤害事件，计算记录不重复计入）`));
    }
    const page=Math.min(WB.state.groupPage||0,Math.max(0,Math.ceil(event.events.length/40)-1));
    for(const item of event.events.slice(page*40,page*40+40))box.append(UI.button(`${WB.number(item.start)} 秒 · ${item.label}${item.value!=null?` · ${WB.number(item.value)}`:""}`,()=>{
      WB.state.derivedBack=event;WB.selectEvent(item,true);},"wb-detail-choice"));
    if(event.events.length>40){const nav=UI.el("div",null,"wb-toolbar"),prev=UI.button("上一页",()=>{WB.state.groupPage=page-1;WB.render();}),next=UI.button("下一页",()=>{WB.state.groupPage=page+1;WB.render();});
      prev.disabled=page===0;next.disabled=(page+1)*40>=event.events.length;nav.append(prev,UI.el("span",`${page+1} / ${Math.ceil(event.events.length/40)}`),next);box.append(nav);}return;
  }
  if(WB.state.derivedBack)box.append(UI.button("← 返回相邻记录",()=>{WB.state.selectedDerived=WB.state.derivedBack;WB.state.derivedBack=null;WB.render();}));
  const detail=event.detail||{},facts=UI.el("dl",null,"wb-facts"),fact=(label,value)=>{if(value!=null&&value!=="")facts.append(UI.el("dt",label),UI.el("dd",value));};
  box.append(UI.el("h4",event.label));
  const explanation=WB.state.mode==="effects" && window.EFX && window.DS?.renderEffectDescription
    ? EFX.describe(event,{characterName:id=>WB.actor(id)?.name||"未指定角色"}) : null;
  if(explanation)box.append(DS.renderEffectDescription(explanation));
  if(!explanation && window.EFX && detail.beneficiaries_scope?.eligibility){
    for(const line of EFX.eligibilityDescription(detail.beneficiaries_scope,id=>WB.actor(id)?.name||"未指定角色"))fact("受益资格",line);
    for(const line of EFX.selectionDescription(detail.beneficiary_selection,id=>WB.actor(id)?.name||"未指定角色"))fact("实际选人",line);
  }
  fact("时间",`${WB.number(event.start)} 秒${event.end>event.start?` → ${WB.number(event.end)} 秒`:""}`);
  if(!explanation)fact("来源",event.sourceLabel||WB.actor(event.character)?.name||"见计算来源");
  if(event.kind==="coverage"){
    // Keep labels as complete Chinese phrases for screen readers and search.
    box.append(UI.el("p",`来源：${event.sourceLabel||"见计算来源"}`,"wb-coverage-source"),UI.el("p",`${detail.receipt_id?"实例角色范围":"接收角色"}：${WB.recipients(event)}`),UI.el("p",`区间长度：${WB.number(event.end-event.start)} 秒`));
    if(detail.state_status)fact("区间状态",TD.stateName(detail.origin==="declared_interval"?"declared":detail.state_status));
    if(detail.end_reason)fact("区间结束原因",TD.endReason(detail.end_reason));
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
      for(const state of (detail.states||[]).slice(0,40))history.append(UI.el("p",`${WB.number(state.start)} → ${WB.number(state.end)} 秒：${TD.stateName(state.status)}；${state.layers} 层${state.remaining_quota!=null?`；剩余 ${state.remaining_quota} 次`:""}；结束原因：${TD.endReason(state.end_reason)}`,"wb-note"));
      if(detail.states.length>40)history.append(UI.el("p","其余切片见原始记录。","wb-note"));box.append(history);}
  }
  for(const key of ["message","note","meaning","interpretation"])if(typeof detail[key]==="string"&&/[\u3400-\u9fff]/u.test(detail[key]))box.append(UI.el("p",detail[key],"wb-note"));
  const technical=UI.el("details",null,"wb-fold");technical.append(UI.el("summary","计算依据与原始记录"),UI.el("p","本页只读；发现漏项可交给 Agent 补正规则后重新交付。","wb-note"));
  if(detail.calculation)technical.append(WB.rawDetails("本次命中计算明细",detail.calculation));
  technical.append(WB.rawDetails("展开原始记录",detail));box.append(technical);
};
WB.renderInspector=box=>{
  if(WB.state.mode!=="actions"){WB.renderDerived(box);return;}
  const actor=WB.actor(WB.state.selectedActor),action=WB.action(WB.state.selectedAction);
  if(action){const fields=UI.el("div",null,"wb-action-fields"),capacityOwner=WB.capacityOwner(action.id),admissionOwner=WB.admissionOwner(action.id),lockedOwner=capacityOwner||admissionOwner,actorSelect=UI.select(WB.state.board.team.map(c=>({value:c.id,label:c.name})),action.character,value=>WB.change(()=>action.character=value)),
    typeSelect=UI.select(WB.types.map(([value,label])=>({value,label})),action.action_type_id,value=>WB.change(()=>{action.action_type_id=value;action.label=WB.typeName(value);action.variant="";}));
    actorSelect.disabled=!!lockedOwner;typeSelect.disabled=!!lockedOwner;fields.append(UI.el("h3","动作详情"),UI.field("动作角色",actorSelect),UI.field("动作类型",typeSelect),
    UI.field("动作名称",UI.input(action.label,value=>WB.change(()=>action.label=value))),UI.field("释放时点 / 秒",UI.input(action.start,value=>WB.setTime(action.id,value),true)));
    const variants=[{value:"",label:"未指定（由 Agent 核实）"},{value:"default",label:"默认释放"},{value:"tap",label:"点按"},{value:"hold",label:"长按"},{value:"custom",label:"其他（在备注说明）"}];
    if(action.variant&&!variants.some(v=>v.value===action.variant))variants.push({value:action.variant,label:action.variant});
    const variantSelect=UI.select(variants,action.variant,value=>WB.change(()=>action.variant=value));variantSelect.disabled=!!lockedOwner;
    fields.append(UI.field("操作变体",variantSelect),UI.field("操作细节 / 段数 / 取消方式",UI.area(action.notes,value=>WB.change(()=>action.notes=value))));
    if(capacityOwner)fields.append(UI.el("p",`动作属于次数意图 ${capacityOwner.id}；如需更换角色、动作类型或变体，请先修改或删除该次数意图。`,"wb-warning"));
    if(admissionOwner)fields.append(UI.el("p",`动作属于动作接纳意图 ${admissionOwner.id}；如需更换角色、动作类型或变体，请先修改或删除该意图。`,"wb-warning"));
    const tools=UI.el("div",null,"wb-toolbar"),more=UI.button("动作操作…",()=>WB.openCommandMenu(more,`${WB.actionName(action)}操作`,WB.actionMenuItems(action.id)),"wb-menu-trigger");tools.append(more);
    fields.append(tools,UI.el("p","调整顺序会交换相邻动作的时间槽；同秒则只调整先后。技能的真实占用、冷却和资源条件由 Agent 核实。","wb-note"));box.append(fields);
  }
  if(!action)box.append(UI.el("h3","动作详情"),UI.el("div","点击时间轴上的动作进行编辑；也可以添加新动作。","wb-empty-detail"));
  const add=WB.fold(actor?`添加动作 · ${actor.name}`:"添加动作 · 先选择角色","addOpen"),grid=UI.el("div",null,"wb-action-add");
  add.append(UI.field("添加动作时点 / 秒",UI.input(WB.state.cursor,value=>{if(Number.isFinite(value)&&value>=0&&value<WB.state.board.duration){WB.state.cursor=value;WB.render();}
    else setStatus("添加时点需位于当前轴内。");},true)));
  WB.types.forEach(([id,label])=>{const button=UI.button(label,()=>WB.addAction(id));button.disabled=!actor;grid.append(button);});add.append(grid);box.append(add);
  const actions=WB.state.board.actions,order=WB.fold(`动作顺序 · ${actions.length} 项`,"orderOpen"),page=Math.min(WB.state.page,Math.max(0,Math.ceil(actions.length/40)-1));WB.state.page=page;order.className+=" wb-order";
  actions.slice(page*40,page*40+40).forEach((a,index)=>{
    const row=UI.el("div",null,"wb-order-row"),open=UI.button(`${WB.number(a.start)}秒 · ${WB.actor(a.character)?.name||"角色"} · ${WB.actionName(a)}`,
      ()=>WB.selectEvent({id:a.id,character:a.character,start:a.start}),a.id===WB.state.selectedAction?"active":""),more=UI.button("⋯",()=>WB.openCommandMenu(more,`${WB.actionName(a)}操作`,WB.actionMenuItems(a.id)),"wb-menu-trigger");
    row.append(UI.el("span",`${page*40+index+1}`),open,more);WB.bindCommandMenu(row,`${WB.actionName(a)}操作`,()=>WB.actionMenuItems(a.id));order.append(row);
  });if(actions.length>40){const nav=UI.el("div",null,"wb-toolbar"),prev=UI.button("前 40 项",()=>{WB.state.page--;WB.render();}),next=UI.button("后 40 项",()=>{WB.state.page++;WB.render();});
    prev.disabled=page===0;next.disabled=(page+1)*40>=actions.length;nav.append(prev,next);order.append(nav);}box.append(order);
};
WB.init=async()=>{
  WB.render();
  window.addEventListener("resize",()=>WB.draw());
  document.addEventListener?.("genshin-theme-change",()=>{WB.refreshThemeColors();WB.draw();});
};
