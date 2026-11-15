"use strict";
// Desktop commands and view preferences only. Saved plans never contain layout state.
const WorkspaceKernel=window.AppWorkspaceKernel;
const fallbackRegistry=report=>{const definitions=new Map();return {
  register(definition){if(definitions.has(definition.id))throw new TypeError(`command_id_duplicate:${definition.id}`);definitions.set(definition.id,definition);},
  resolve(id,context={}){const definition=definitions.get(id);if(!definition)return null;const read=value=>typeof value==="function"?value(context):value,
    visible=read(definition.visible)!==false,enabled=visible&&read(definition.enabled)!==false;return {...definition,label:String(read(definition.label)||id),shortLabel:String(read(definition.shortLabel)||read(definition.label)||id),shortcut:String(read(definition.shortcut)||""),visible,enabled,disabledReason:enabled?"":String(read(definition.disabledReason)||"当前条件不允许执行此操作。")};},
  list(context={},options={}){return Array.from(definitions.keys(),id=>this.resolve(id,context)).filter(command=>options.includeHidden||command.visible).sort((a,b)=>(a.order||0)-(b.order||0));},
  async invoke(id,context={}){const command=this.resolve(id,context);if(!command?.enabled){report({message:command?.disabledReason||"此操作不适用于当前页面。"});return {ok:false,status:"disabled",command};}try{const value=await command.run(context,command);return {ok:true,status:value===false?"cancelled":"success",command,value};}catch(error){report({message:"操作未完成，请检查提示后重试。",details:{error:String(error?.message||error)}});return {ok:false,status:"failed",command,error};}},
};};
window.WS = {layouts:new Map(),preferences:{},menu:null,ready:false,commandRegistry:null};
WS.sidebar={root:null,panel:null,content:null,context:null,history:null,historyDefault:null,
  toggle:null,collapse:null,resizer:null,theme:null,scrim:null,owner:null,ownedNode:null,overlayOpen:false};
WS.sidebarPages=Object.freeze([
  {tab:"whiteboard",label:"排轴白板",icon:"▦",description:"编辑角色、面板与动作意图"},
  {tab:"rotation",label:"计算模型（高级）",icon:"⚙",description:"检查并补充引擎输入"},
  {tab:"result",label:"计算报告",icon:"≡",description:"查看计算结果与导出"},
  {tab:"data",label:"资料库",icon:"▤",description:"查询角色、装备与版本资料"},
  {tab:"agent",label:"AI 助手",icon:"✦",description:"对话、任务和运行详情"},
]);
WS.statusCenter=WorkspaceKernel?.createStatusCenter?.({document,limit:24})||null;
WS.attentionShelf=WorkspaceKernel?.createAttentionShelf?.({document})||{set(){},clear(){},render(){}};
WS.report=record=>{if(WS.statusCenter?.publish)WS.statusCenter.publish(record);else if(typeof setStatus==="function")setStatus(record.message,record.details);};
WS.tab = () => document.querySelector(".tab.active")?.dataset.tab || "whiteboard";
WS.isBoard = () => WS.tab()==="whiteboard"||(WS.tab()==="result"&&window.RS?.sourceKind()==="whiteboard");
WS.isPlan = () => WS.tab()==="rotation"||(WS.tab()==="result"&&window.RS?.sourceKind()==="editor");
WS.axisKey = () => WS.tab()==="whiteboard"?"whiteboard":WS.tab()==="rotation"?"rotation":null;
WS.modalOpen = () => !!document.querySelector("dialog[open]");
WS.navigate = tab => { document.querySelector(`.tab[data-tab="${tab}"]`)?.click(); WS.sidebar.overlayOpen=false;WS.applySidebarState?.();WS.refresh(); };
WS.click = id => { const node=document.getElementById(id); if(node&&!node.disabled)node.click(); };
WS.canClick = id => { const node=document.getElementById(id); return !!node&&!node.disabled; };
WS.legacyCommandAdapters=Object.freeze({
  "add-hit":{button:"btn-add-hit",removeAfter:"advanced-event-command-migration"},
  "add-buff":{button:"btn-add-buff",removeAfter:"advanced-event-command-migration"},
  "add-switch":{button:"btn-add-switch",removeAfter:"advanced-event-command-migration"},
  "manual-copy":{button:"btn-manual-copy",removeAfter:"advanced-event-command-migration"},
  "checks":{button:"btn-rotation-checks",removeAfter:"expert-page-command-migration"},
  "mechanics":{button:"btn-mechanics",removeAfter:"expert-page-command-migration"},
  "export-json":{button:"btn-exp-json",removeAfter:"result-page-command-migration"},
  "export-xlsx":{button:"btn-exp-xlsx",removeAfter:"result-page-command-migration"},
  "export-formula":{button:"btn-exp-formula",removeAfter:"result-page-command-migration"},
  "export-csv":{button:"btn-exp-csv",removeAfter:"result-page-command-migration"},
  "export-resolved":{button:"btn-resolved",removeAfter:"result-page-command-migration"},
});
WS.runLegacyAdapter = commandId => {const adapter=WS.legacyCommandAdapters[commandId],node=adapter&&document.getElementById(adapter.button);if(!node||node.disabled)return false;node.click();return true;};
WS.persist = () => { try { localStorage.setItem("genshin.workspace.v1",JSON.stringify(WS.preferences)); } catch { /* Layout remains session-local. */ } };
WS.preference = (key,width=1280) => {
  if(!WS.preferences[key])WS.preferences[key]={left:205,right:292,leftHidden:key==="rotation"||width<1000,rightHidden:width<780};
  return WS.preferences[key];
};
WS.loadPreferences = () => {
  try { const saved=JSON.parse(localStorage.getItem("genshin.workspace.v1")||"{}");
    for(const key of ["rotation","whiteboard"])if(saved[key]&&typeof saved[key]==="object"){
      const value=saved[key];WS.preferences[key]={left:Number.isFinite(value.left)?Math.min(340,Math.max(160,value.left)):205,
        right:Number.isFinite(value.right)?Math.min(460,Math.max(230,value.right)):292,
        leftHidden:typeof value.leftHidden==="boolean"?value.leftHidden:key==="rotation",
        rightHidden:value.rightHidden===true};
    }
    const sidebarWidth=Number(saved.globalSidebar?.width);
    WS.preferences.globalSidebar={collapsed:saved.globalSidebar?.collapsed===true,
      width:Number.isFinite(sidebarWidth)?Math.min(280,Math.max(220,sidebarWidth)):240};
  } catch { WS.preferences={}; }
};
WS.sidebarPreference = () => {
  if(!WS.preferences.globalSidebar||typeof WS.preferences.globalSidebar!=="object")WS.preferences.globalSidebar={collapsed:false,width:240};
  const width=Number(WS.preferences.globalSidebar.width);WS.preferences.globalSidebar.width=Number.isFinite(width)?Math.min(280,Math.max(220,width)):240;
  return WS.preferences.globalSidebar;
};
WS.sidebarNewAction = async () => {
  const tab=WS.tab();
  if(tab==="whiteboard")return WS.invoke("new-board");
  if(tab==="result"&&WS.isBoard())return WS.invoke("new-board");
  if(WS.isPlan())return WS.invoke("new-plan");
  if(tab==="agent"){
    WS.navigate("agent");
    if(typeof window.AgentWorkbenchApp?.newTask==="function")return window.AgentWorkbenchApp.newTask();
    const button=document.querySelector("#agent-chat .ac-new-task, #agent-chat [data-agent-new-task]");
    if(button&&!button.disabled){button.click();return true;}
    WS.report({factKey:"sidebar.new.agent.unavailable",kind:"info",message:"AI 助手尚未准备完成，请稍后再新建会话。"});
    return false;
  }
  WS.report({factKey:"sidebar.new.unavailable",kind:"info",message:"当前页面没有可新建的文档。"});
  return false;
};
WS.updateSidebarLabels = nav => {
  for(const page of WS.sidebarPages){const button=nav.querySelector(`.tab[data-tab="${page.tab}"]`);if(!button)continue;
    button.replaceChildren();button.dataset.workspaceLabel=page.label;button.title=`${page.label}：${page.description}`;
    const icon=document.createElement("span"),label=document.createElement("span");icon.className="workspace-sidebar-icon";icon.setAttribute("aria-hidden","true");icon.textContent=page.icon;
    label.className="workspace-sidebar-label";label.textContent=page.label;button.append(icon,label);button.addEventListener("click",()=>{WS.sidebar.overlayOpen=false;WS.applySidebarState();});
  }
};
WS.applySidebarState = () => {
  const shell=WS.sidebar.root;if(!shell)return;
  const preference=WS.sidebarPreference(),collapsed=preference.collapsed===true;
  shell.style.setProperty("--workspace-sidebar-width",`${preference.width}px`);
  shell.classList.toggle("workspace-sidebar-collapsed",collapsed);
  shell.classList.toggle("workspace-sidebar-overlay-open",WS.sidebar.overlayOpen===true);
  WS.sidebar.collapse?.setAttribute("aria-pressed",String(collapsed));
  WS.sidebar.collapse?.setAttribute("aria-label",collapsed?"展开左侧导航":"收起左侧导航");
  if(WS.sidebar.collapse)WS.sidebar.collapse.textContent=collapsed?"›":"‹";
  WS.sidebar.toggle?.setAttribute("aria-expanded",String(WS.sidebar.overlayOpen===true));
  WS.sidebar.resizer?.setAttribute("aria-valuenow",String(Math.round(preference.width)));
};
WS.resizeGlobalSidebar = (width,{persist=true}={}) => {
  const preference=WS.sidebarPreference(),next=Math.min(280,Math.max(220,Number(width)||240));preference.width=next;
  WS.sidebar.root?.style.setProperty("--workspace-sidebar-width",`${next}px`);WS.sidebar.resizer?.setAttribute("aria-valuenow",String(Math.round(next)));
  if(persist)WS.persist();return next;
};
WS.toggleGlobalSidebar = (force=null) => {
  const narrow=typeof window.matchMedia==="function"&&window.matchMedia("(max-width: 959px)").matches;
  if(narrow){WS.sidebar.overlayOpen=typeof force==="boolean"?force:!WS.sidebar.overlayOpen;}
  else {const preference=WS.sidebarPreference();preference.collapsed=typeof force==="boolean"?!force:!preference.collapsed;WS.persist();}
  WS.applySidebarState();
};
WS.attachContextHistory = (node,{owner="agent"}={}) => {
  if(!WS.sidebar.history||!node)return false;
  if(WS.sidebar.ownedNode&&WS.sidebar.ownedNode!==node)WS.sidebar.ownedNode.hidden=true;
  WS.sidebar.owner=owner;WS.sidebar.ownedNode=node;WS.sidebar.history.append(node);WS.refreshSidebar();return true;
};
WS.releaseContextHistory = (node=WS.sidebar.ownedNode) => {
  if(!node||node!==WS.sidebar.ownedNode)return false;
  WS.sidebar.owner=null;WS.sidebar.ownedNode=null;node.hidden=true;WS.refreshSidebar();return true;
};
WS.refreshSidebar = () => {
  if(!WS.sidebar.root)return;
  const state=WS.pageChromeState(),page=WS.sidebarPages.find(item=>item.tab===state.context.tab);
  for(const button of WS.sidebar.panel.querySelectorAll(".tab[data-tab]"))button.setAttribute("aria-current",button.dataset.tab===state.context.tab?"page":"false");
  const heading=WS.sidebar.context?.querySelector(".workspace-sidebar-context-title"),summary=WS.sidebar.context?.querySelector(".workspace-sidebar-context-summary");
  if(heading)heading.textContent=state.context.tab==="agent"?"对话项目":state.context.tab==="data"?"资料范围":"当前文档";
  if(summary){summary.textContent=state.documentSummary;summary.title=state.documentSummary;}
  if(WS.sidebar.historyDefault){WS.sidebar.historyDefault.hidden=!!WS.sidebar.ownedNode&&WS.sidebar.owner===state.context.tab;
    const title=WS.sidebar.historyDefault.querySelector("strong"),detail=WS.sidebar.historyDefault.querySelector("span");if(title)title.textContent=page?.label||state.title;if(detail)detail.textContent=state.documentSummary;}
  if(WS.sidebar.ownedNode)WS.sidebar.ownedNode.hidden=WS.sidebar.owner!==state.context.tab;
  const disabled=state.context.tab==="data";const create=WS.sidebar.panel.querySelector(".workspace-sidebar-new");if(create){create.disabled=disabled;create.title=disabled?"资料库没有新建文档操作":"新建";}
};
WS.mountGlobalShell = () => {
  if(WS.sidebar.root?.isConnected)return WS.sidebar.root;
  const nav=document.querySelector(".workspace-nav"),chrome=document.getElementById("workspace-page-chrome"),attention=document.getElementById("workspace-attention-shelf"),main=document.querySelector("main"),footer=document.querySelector("footer");
  if(!nav||!chrome||!main||!nav.parentNode)return null;
  const shell=document.createElement("div"),sidebar=document.createElement("aside"),content=document.createElement("section"),context=document.createElement("section"),history=document.createElement("section"),defaultHistory=document.createElement("button"),newButton=document.createElement("button"),collapse=document.createElement("button"),resizer=document.createElement("div"),theme=document.createElement("label"),themeLabel=document.createElement("span"),themeSelect=document.createElement("select"),toggle=document.createElement("button"),scrim=document.createElement("button");
  shell.id="workspace-app-shell";shell.className="workspace-app-shell";sidebar.id="workspace-global-sidebar";sidebar.className="workspace-global-sidebar";sidebar.setAttribute("aria-label","页面与文档导航");
  content.className="workspace-content-shell";context.className="workspace-sidebar-context";history.id="workspace-contextual-history";history.className="workspace-contextual-history";history.setAttribute("aria-live","polite");
  newButton.type="button";newButton.className="workspace-sidebar-new";newButton.append(Object.assign(document.createElement("span"),{textContent:"+"}),Object.assign(document.createElement("span"),{textContent:"新建"}));newButton.addEventListener("click",()=>WS.sidebarNewAction());
  const contextTitle=document.createElement("strong"),contextSummary=document.createElement("span");contextTitle.className="workspace-sidebar-context-title";contextSummary.className="workspace-sidebar-context-summary";context.append(contextTitle,contextSummary);
  defaultHistory.type="button";defaultHistory.className="workspace-sidebar-current";const currentTitle=document.createElement("strong"),currentDetail=document.createElement("span");defaultHistory.append(currentTitle,currentDetail);defaultHistory.addEventListener("click",()=>WS.navigate(WS.tab()));history.append(defaultHistory);
  theme.className="workspace-theme-control";themeLabel.className="workspace-theme-label";themeLabel.textContent="主题";themeSelect.className="workspace-theme-select";themeSelect.setAttribute("aria-label","界面主题");
  for(const [value,label] of [["system","跟随系统"],["light","浅色"],["dark","深色"]]){const option=document.createElement("option");option.value=value;option.textContent=label;themeSelect.append(option);}
  themeSelect.value=window.ThemeController?.current?.().preference||"system";themeSelect.addEventListener("change",()=>window.ThemeController?.set?.(themeSelect.value));theme.append(themeLabel,themeSelect);
  document.addEventListener("genshin-theme-change",event=>{const value=event?.detail?.preference;if(["system","light","dark"].includes(value))themeSelect.value=value;});
  collapse.type="button";collapse.className="workspace-sidebar-collapse";collapse.addEventListener("click",()=>WS.toggleGlobalSidebar());
  resizer.className="workspace-sidebar-resizer";resizer.tabIndex=0;resizer.setAttribute("role","separator");resizer.setAttribute("aria-orientation","vertical");resizer.setAttribute("aria-label","调整左侧导航宽度");resizer.setAttribute("aria-valuemin","220");resizer.setAttribute("aria-valuemax","280");let sidebarDrag=null;
  resizer.addEventListener("pointerdown",event=>{if(event.button!==0)return;event.preventDefault();sidebarDrag={id:event.pointerId,x:event.clientX,width:WS.sidebarPreference().width};resizer.setPointerCapture?.(event.pointerId);});
  resizer.addEventListener("pointermove",event=>{if(sidebarDrag?.id!==event.pointerId)return;WS.resizeGlobalSidebar(sidebarDrag.width+event.clientX-sidebarDrag.x,{persist:false});});
  const finishSidebarResize=event=>{if(sidebarDrag?.id!==event.pointerId)return;resizer.releasePointerCapture?.(event.pointerId);sidebarDrag=null;WS.persist();};resizer.addEventListener("pointerup",finishSidebarResize);resizer.addEventListener("pointercancel",finishSidebarResize);
  resizer.addEventListener("keydown",event=>{if(!["ArrowLeft","ArrowRight","Home","End"].includes(event.key))return;event.preventDefault();WS.resizeGlobalSidebar(event.key==="Home"?220:event.key==="End"?280:WS.sidebarPreference().width+(event.key==="ArrowRight"?12:-12));});
  toggle.id="workspace-sidebar-toggle";toggle.type="button";toggle.className="workspace-sidebar-mobile-toggle";toggle.setAttribute("aria-controls",sidebar.id);toggle.setAttribute("aria-label","打开页面导航");toggle.textContent="☰";toggle.addEventListener("click",()=>WS.toggleGlobalSidebar());
  scrim.type="button";scrim.className="workspace-sidebar-scrim";scrim.setAttribute("aria-label","关闭页面导航");scrim.addEventListener("click",()=>WS.toggleGlobalSidebar(false));
  const parent=nav.parentNode;parent.insertBefore(shell,nav);WS.updateSidebarLabels(nav);sidebar.append(newButton,nav,context,history,theme,collapse);content.append(chrome);if(attention)content.append(attention);content.append(main);if(footer)content.append(footer);shell.append(sidebar,resizer,content,scrim);
  document.querySelector(".desktop-header")?.append(toggle);(document.getElementById("genshin-legacy-editor")||document.body).classList.add("workspace-shell-mounted");
  WS.sidebar={...WS.sidebar,root:shell,panel:sidebar,content,context,history,historyDefault:defaultHistory,toggle,collapse,resizer,theme:themeSelect,scrim};
  WS.applySidebarState();WS.refreshSidebar();
  if(typeof window.CustomEvent==="function")window.dispatchEvent?.(new CustomEvent("workspace:sidebar-mounted",{detail:{historyHost:history,attach:WS.attachContextHistory}}));
  return shell;
};
WS.applyLayout = record => {
  const {root,key,repaint}=record,p=WS.preference(key),width=root.clientWidth||1280;
  // DSH's optional Chat/sidebar changes the editor's available width without
  // changing window.innerWidth. Follow the measured island, not only @media.
  const compact=width<780,leftHidden=compact?record.compactSide!=="left":p.leftHidden,rightHidden=compact?record.compactSide!=="right":p.rightHidden;
  const budget=Math.max(230,width-336),left=leftHidden?0:Math.min(p.left,Math.max(160,budget-(rightHidden?0:230))),
    right=rightHidden?0:Math.min(p.right,Math.max(230,budget-left));
  root.style.setProperty("--workspace-left",`${left}px`);root.style.setProperty("--workspace-right",`${right}px`);
  root.classList.toggle("workspace-compact-panes",compact);
  root.classList.toggle("workspace-left-hidden",leftHidden);root.classList.toggle("workspace-right-hidden",rightHidden);
  root.classList.remove("team-collapsed");
  for(const side of ["left","right"]){const handle=root.querySelector(`[data-resize-side="${side}"]`);
    if(handle){handle.hidden=compact||(side==="left"?leftHidden:rightHidden);handle.setAttribute("aria-valuenow",String(Math.round(side==="left"?left:right)));}}
  document.querySelectorAll(`[data-workspace-controls="${key}"] button`).forEach(button=>{
    button.setAttribute("aria-pressed",String(!(button.dataset.side==="left"?leftHidden:rightHidden)));
  });
  if(key==="rotation")document.getElementById("timeline-team")?.setAttribute("aria-pressed",String(!leftHidden));
  repaint?.();
};
WS.resizePane = (key,side,value) => {
  if(!["left","right"].includes(side)||!Number.isFinite(value))return;
  const p=WS.preference(key),record=WS.layouts.get(key),width=record?.root.clientWidth||1280;
  const other=side==="left"?"right":"left",min=side==="left"?160:230,max=side==="left"?340:460;
  p[side]=Math.max(min,Math.min(max,value,width-336-(p[`${other}Hidden`]?0:p[other])));
  if(record)WS.applyLayout(record);WS.persist();
};
WS.toggle = (key,side) => {
  if(!key||!["left","right"].includes(side))return;
  const record=WS.layouts.get(key);
  if(record&&(record.root.clientWidth||1280)<780){record.compactSide=record.compactSide===side?null:side;WS.applyLayout(record);return;}
  const p=WS.preference(key);p[`${side}Hidden`]=!p[`${side}Hidden`];if(record)WS.applyLayout(record);WS.persist();
};
WS.reveal = (key,side) => {
  const record=WS.layouts.get(key);
  if(record&&(record.root.clientWidth||1280)<780){record.compactSide=side;WS.applyLayout(record);return;}
  if(WS.preference(key)[`${side}Hidden`])WS.toggle(key,side);
};
WS.resetLayout = () => {
  const key=WS.axisKey();if(!key)return;delete WS.preferences[key];
  WS.preference(key,WS.layouts.get(key)?.root.clientWidth);const record=WS.layouts.get(key);if(record){record.compactSide=null;WS.applyLayout(record);}WS.persist();
};
WS.mount = (root,{key,left,right,repaint}) => {
  if(!root)return;
  const previous=WS.layouts.get(key);
  if(previous?.root===root&&root.querySelector(left)?.querySelector(".ws-pane-close")&&root.querySelector(right)?.querySelector(".ws-pane-close")){
    previous.repaint=repaint;WS.applyLayout(previous);return;}
  previous?.observer?.disconnect();
  root.querySelectorAll(".ws-resizer,.ws-pane-close").forEach(node=>node.remove());
  root.classList.add("workspace-layout");root.dataset.workspace=key;
  WS.preference(key,root.clientWidth);
  const record={root,key,repaint};WS.layouts.set(key,record);
  for(const [side,selector,label] of [["left",left,"队伍"],["right",right,"详情"]]){
    const pane=root.querySelector(selector);if(!pane)continue;pane.classList.add(`ws-${side}-pane`);
    const close=UI.button("×",()=>WS.toggle(key,side),"ws-pane-close");close.title=`收起${label}`;close.setAttribute("aria-label",`收起${label}面板`);pane.append(close);
    const handle=UI.el("div",null,`ws-resizer ws-resizer-${side}`);handle.dataset.resizeSide=side;handle.tabIndex=0;
    handle.setAttribute("role","separator");handle.setAttribute("aria-orientation","vertical");handle.setAttribute("aria-label",`调整${label}面板宽度`);
    handle.setAttribute("aria-valuemin",side==="left"?"160":"230");handle.setAttribute("aria-valuemax",side==="left"?"340":"460");
    let drag=null;
    handle.addEventListener("pointerdown",e=>{if(e.button!==0)return;e.preventDefault();drag={id:e.pointerId,x:e.clientX,width:WS.preference(key)[side]};handle.setPointerCapture?.(e.pointerId);});
    handle.addEventListener("pointermove",e=>{if(drag?.id!==e.pointerId)return;WS.resizePane(key,side,drag.width+(e.clientX-drag.x)*(side==="left"?1:-1));});
    const end=e=>{if(drag?.id!==e.pointerId)return;handle.releasePointerCapture?.(e.pointerId);drag=null;};
    handle.addEventListener("pointerup",end);handle.addEventListener("pointercancel",end);
    handle.addEventListener("keydown",e=>{if(!["ArrowLeft","ArrowRight","Home"].includes(e.key))return;e.preventDefault();
      WS.resizePane(key,side,e.key==="Home"?(side==="left"?205:292):WS.preference(key)[side]+(e.key==="ArrowRight"?1:-1)*(side==="left"?1:-1)*16);});
    root.append(handle);
  }
  document.querySelectorAll(`[data-workspace-controls="${key}"]`).forEach(box=>{
    box.replaceChildren();for(const [side,label] of [["left","队伍"],["right","详情"]]){const button=UI.button(label,()=>WS.toggle(key,side),"workspace-pane-toggle");button.dataset.side=side;button.title=`显示或收起${label}面板`;box.append(button);}
  });
  if(typeof ResizeObserver!=="undefined"){record.observer=new ResizeObserver(()=>WS.applyLayout(record));record.observer.observe(root);}
  WS.applyLayout(record);WS.refresh();
};
WS.setMode = mode => {if(WS.isBoard())WB.setMode?.(mode);else if(WS.tab()==="rotation")TV.changeMode(mode);};
WS.dialog = (title,description) => {
  const dialog=window.AppDialogKernel?.create?.({title,description,className:"workspace-dialog"})||null;
  if(!dialog)WS.report?.({factKey:"workspace.dialog.blocked",kind:"warning",message:"请先处理当前打开的对话框。",details:{reason:window.AppDialogKernel?.getLastCreateResult?.()?.reason||"dialog_service_unavailable"}});
  return dialog;
};
WS.bindAgentFileDialog = (dialog,options={}) => {
  if(!dialog||typeof options?.onAgentActionResult!=="function"||typeof options.agentActionId!=="string")return dialog;
  dialog.dataset.agentActionId=options.agentActionId;const close=typeof dialog.finish==="function"?dialog.finish:()=>{dialog.close();dialog.remove();};let finished=false,settling=false,operationPending=false;
  dialog.releaseForOperation=()=>{operationPending=true;const accepted=close("file-operation",{allowBusy:true});if(accepted===false)operationPending=false;return accepted;};
  dialog.beginOperation=()=>{operationPending=true;dialog.setBusy?.(true);};
  const finish=(status,message)=>{if(finished||settling)return false;let accepted;settling=true;
    try{accepted=close(status,{allowBusy:status!=="cancelled"});}finally{settling=false;}
    if(accepted===false)return false;finished=true;options.onAgentActionResult({status,message});return true;};
  dialog.onClosed?.(reason=>{if(reason!=="file-operation"&&!operationPending)finish("cancelled","用户关闭了文件操作界面。");});
  dialog.endOperation=()=>{operationPending=false;dialog.setBusy?.(false);if(dialog.appFinished&&!finished)finish("failed","界面已关闭，App未确认本次文件操作的最终状态；请检查文件或操作记录，不要直接重复提交。");};
  dialog.agentActionComplete=message=>finish("completed",message||"文件操作已由App确认完成。");
  dialog.agentActionFail=message=>finish("failed",message||"App未能完成文件操作。");
  dialog.agentActionCancel=()=>finish("cancelled","用户关闭了文件操作界面。");
  dialog.finish=dialog.agentActionCancel;return dialog;
};
// Native user file picker; the loader and Store retain schema/dirty/SHA ownership.
const workspaceFileActions=()=>WS.nativeFiles||(WS.nativeFiles=GenshinBusinessUI.createWorkspaceFileActions({
 board:WB,editor:RT,workspace:WS,api:(method,...args)=>callApi(method,...args),query:selector=>$(selector),
 serialize:value=>JSONTransport.stringify(value),status:message=>setStatus(message),error:response=>UI.error(response)}));
WS.openFile=options=>workspaceFileActions().openFile(options);
WS.saveAs=options=>workspaceFileActions().saveAs(options);
WS.help = () => {
  const dialog=WS.dialog("工作台操作","先选择工作区，再选择时间轴；文件和计算操作统一从顶部进入。"),list=UI.el("dl",null,"workspace-help-list");
  if(!dialog)return null;
  for(const [label,value] of [["动作轴","查看施放顺序。排轴白板中可拖动动作，点击后在右侧调整。"],["效果轴","查看命中、触发和持续 Buff；切换“持续Buff”可专看覆盖区间。"],["伤害轴","查看伤害发生的时点和数值，点击查看来源与计算明细。"],["详情与空间","单击选择，右侧显示详情；拖动两侧分隔线调宽，视图菜单可收起面板或恢复布局。"],["缩放与移动","Ctrl＋滚轮缩放，拖动空白或中键平移；适应全轴回到完整时间范围。"],["快捷键","Ctrl＋O 打开，Ctrl＋S 保存，Ctrl＋Shift＋S 另存；Ctrl＋1 / 2 / 3 切换当前工作区的三种轴。"],["排轴白板与计算模型","排轴白板记录用户动作意图；计算模型（高级）保留原生方案和手工数值输入。两份草稿分别保存。"]]){list.append(UI.el("dt",label),UI.el("dd",value));}
  dialog.appBody.append(list);dialog.setActions(UI.button("知道了",()=>dialog.finish(),"primary"));return dialog;
};
WS.commandContext = () => ({tab:WS.tab(),board:WS.isBoard(),plan:WS.isPlan(),axis:WS.axisKey(),
  havePlan:!!window.RT?.tpl?.(),haveBoard:!!window.WB?.state.board,
  boardDirty:!!window.WB?.dirty?.(),planDirty:!!window.PL?.dirty?.(),calculationPending:!!window.RS?.state?.pending});
WS.commandGroups=[{id:"file",label:"文件"},{id:"edit",label:"编辑"},{id:"view",label:"视图"},{id:"calculate",label:"计算与交接"},{id:"tools",label:"工具"},{id:"help",label:"帮助"}];
WS.registerCommands = () => {
  if(WS.commandRegistry)return WS.commandRegistry;
  WS.commandRegistry=WorkspaceKernel?.createCommandRegistry?.({report:WS.report})||fallbackRegistry(WS.report);
  const add=(group,section,order,id,label,run,enabled=true,shortcut="",extra={})=>WS.commandRegistry.register({group,section,order,id,label,run,enabled,shortcut,...extra});
  const unavailable="当前页面或文档状态不允许执行此操作。";
  add("file","new",10,"new-board","新建创作白板",()=>WB.newBoard(),true,"",{shortLabel:"新建"});
  add("file","new",20,"new-plan","新建装备方案",async()=>{const before=RT.tpl();await PL.startNew();if(RT.tpl()!==before)WS.navigate("rotation");});
  add("file","new",30,"new-template","新建手工数值模板",async()=>{const before=RT.tpl();await RT.newTemplate();if(RT.tpl()!==before)WS.navigate("rotation");});
  add("file","io",40,"open","打开…",WS.openFile,true,"Ctrl+O",{shortLabel:"打开"});
  add("file","io",50,"save",context=>context.board?"保存白板":"保存方案",()=>{
    // The first user save chooses a real location; existing documents keep
    // their ordinary Store/SHA path. Explicit domain save(path) is unchanged.
    const hasPath=WS.isBoard()?!!WB.state.path:!!RT.state.path;
    return hasPath?(WS.isBoard()?WB.save():RT.saveTemplate()):WS.saveAs();
  },context=>context.board?context.haveBoard:context.plan&&context.havePlan,"Ctrl+S",{shortLabel:"保存",disabledReason:unavailable});
  add("file","io",60,"save-as","另存为…",WS.saveAs,context=>context.board?context.haveBoard:context.plan&&context.havePlan,"Ctrl+Shift+S",{disabledReason:unavailable});
  add("edit","document",110,"equipment",context=>context.board?"角色、装备与面板…":"装备与面板…",()=>WS.isBoard()?(WB.state.selectedActor?WB.editCharacter(WB.state.selectedActor):WB.addCharacter()):PL.begin(),context=>context.board?context.haveBoard:context.plan&&context.havePlan,"",{disabledReason:"请先新建或打开可编辑文档。"});
  add("edit","document",120,"add-action","添加动作…",()=>WB.openAdd(),context=>context.board&&context.haveBoard,"",{shortLabel:"添加动作",level:"primary",disabledReason:"请先新建或打开创作白板。"});
  add("edit","events",130,"add-hit","添加手工伤害事件",()=>{WS.navigate("rotation");WS.runLegacyAdapter("add-hit");WS.reveal("rotation","right");},context=>context.plan&&context.havePlan&&WS.canClick("btn-add-hit"),"",{disabledReason:unavailable});
  add("edit","events",140,"add-buff","添加手工增益",()=>{WS.navigate("rotation");WS.runLegacyAdapter("add-buff");WS.reveal("rotation","right");},context=>context.plan&&context.havePlan&&WS.canClick("btn-add-buff"),"",{disabledReason:unavailable});
  add("edit","events",150,"add-switch","添加切人",()=>{WS.navigate("rotation");WS.runLegacyAdapter("add-switch");WS.reveal("rotation","right");},context=>context.plan&&context.havePlan&&WS.canClick("btn-add-switch"),"",{disabledReason:unavailable});
  add("edit","events",160,"manual-copy","建立手工数值副本",()=>WS.runLegacyAdapter("manual-copy"),context=>context.plan&&context.havePlan&&WS.canClick("btn-manual-copy"),"",{disabledReason:unavailable});
  [["whiteboard","排轴白板"],["rotation","计算模型（高级）"],["result","计算报告"],["data","资料库"],["agent","AI 助手"]].forEach(([tab,label],index)=>add("view","pages",210+index,`view-${tab}`,label,()=>WS.navigate(tab)));
  ["actions","effects","damage"].forEach((mode,index)=>add("view","axis",230+index,`axis-${mode}`,["动作轴","效果轴","伤害轴"][index],()=>WS.setMode(mode),context=>!!context.axis,`Ctrl+${index+1}`,{disabledReason:"当前页面没有时间轴视图。"}));
  add("view","layout",250,"team","显示 / 收起队伍",()=>WS.toggle(WS.axisKey(),"left"),context=>!!context.axis);
  add("view","layout",260,"details","显示 / 收起详情",()=>WS.toggle(WS.axisKey(),"right"),context=>!!context.axis);
  add("view","layout",270,"reset-layout","恢复默认布局",WS.resetLayout,context=>!!context.axis);
  add("view","assistant",280,"agent-chat","显示 / 收起 Agent 聊天",()=>window.AgentWorkbenchApp?.toggle?.());
  add("calculate","main",310,"calculate","计算当前方案",()=>RS.runCalc(),context=>context.plan&&context.havePlan&&!context.calculationPending,"",{shortLabel:context=>context.calculationPending?"计算中…":"计算",level:"primary",disabledReason:context=>context.calculationPending?"计算正在进行，请等待完成。":"请先打开可计算方案。"});
  add("calculate","board",320,"validate-board","检查白板草稿",()=>WB.validate(),context=>context.board&&context.haveBoard,"",{disabledReason:"请先新建或打开创作白板。"});
  add("calculate","board",330,"agent-complete","在 AI 助手继续…",()=>window.AgentWorkbenchApp?.open?.(),context=>(context.board&&context.haveBoard)||(context.plan&&context.havePlan));
  add("calculate","board",340,"handoff","生成外部 Agent 交接文件…",()=>WB.handoff(),context=>context.board&&context.haveBoard,"",{disabledReason:"请先新建或打开创作白板。"});
  add("calculate","board",350,"return","载入 Agent 计算结果…",()=>WB.importResult(),context=>context.board&&context.haveBoard);
  [["json","结构化结果"],["xlsx","结果快照表"],["formula","活公式工作簿"],["csv","文本表格"]].forEach(([format,label],index)=>add("calculate","export",360+index,`export-${format}`,`导出${label}`,()=>WS.runLegacyAdapter(`export-${format}`),context=>(context.tab==="result"||context.plan)&&WS.canClick(`btn-exp-${format}`),"",{disabledReason:"当前结果尚未就绪或已经失效。"}));
  add("calculate","export",370,"export-resolved","导出数值模板",()=>WS.runLegacyAdapter("export-resolved"),context=>context.plan&&WS.canClick("btn-resolved"));
  add("tools","inspect",410,"checks","动作与能量…",()=>{WS.navigate("rotation");WS.runLegacyAdapter("checks");},context=>context.plan&&context.havePlan);
  add("tools","inspect",420,"mechanics","机制规则与过程…",()=>{WS.navigate("rotation");WS.runLegacyAdapter("mechanics");},context=>context.plan&&context.havePlan);
  add("tools","data",430,"data-search","搜索资料",()=>DV.refreshList(),context=>context.tab==="data"&&WS.canClick("data-search"),"",{shortLabel:"搜索"});
  add("tools","data",435,"check-updates","检查更新",()=>{WS.navigate("data");return DV.checkDatabaseUpdate();});
  add("tools","data",440,"update-db","查看安装或更新选项",()=>DV.updateDatabase(),context=>context.tab==="data"&&WS.canClick("data-update"),"",{disabledReason:"当前没有可执行的更新选项。"});
  add("tools","data",450,"annotations","追加机制资料",()=>DV.openAnnotationManager(),context=>context.tab==="data"&&WS.canClick("data-annotations"));
  add("help","main",510,"help","工作台操作说明",WS.help);
  return WS.commandRegistry;
};
WS.commands = () => {
  const registry=WS.registerCommands(),context=WS.commandContext(),commands=registry.list(context),groups=[];
  for(const group of WS.commandGroups){const values=commands.filter(command=>command.group===group.id),items=[];let section=null;
    for(const command of values){if(section!==null&&section!==command.section)items.push(null);section=command.section;items.push(command);}
    groups.push({label:group.label,items});}
  return groups;
};
WS.invoke = async id => {
  WS.closeMenu();const result=await WS.registerCommands().invoke(id,WS.commandContext());WS.refresh();return result.ok;
};
WS.closeMenu = (focus=false) => {if(!WS.menu)return;const {popup,button}=WS.menu;
  if(typeof popup.finish==="function")return popup.finish("menubar-close",{restoreFocus:focus});
  popup.remove();button.setAttribute("aria-expanded","false");WS.menu=null;if(focus)button.focus();};
WS.openMenu = (index,focus=false) => {
  WS.closeMenu();window.AppContextMenu?.forDocument?.(document,window)?.close?.();const button=document.querySelectorAll(".workspace-menu-button")[index],menu=WS.commands()[index];if(!button||!menu)return;
  const popup=UI.el("div",null,"workspace-menu-popup");popup.setAttribute("role","menu");popup.setAttribute("aria-label",menu.label);
  menu.items.forEach(item=>{if(!item){const separator=UI.el("div",null,"workspace-menu-separator");separator.setAttribute("role","separator");popup.append(separator);return;}
    const entry=UI.button("",()=>WS.invoke(item.id),"workspace-menu-item");entry.dataset.command=item.id;entry.setAttribute("role","menuitem");entry.disabled=!item.enabled;entry.tabIndex=-1;
    entry.append(UI.el("span",item.label));if(item.shortcut)entry.append(UI.el("kbd",item.shortcut));popup.append(entry);
  });
  const choices=()=>Array.from(popup.querySelectorAll("button:not(:disabled)"));
  const surface=window.AppSurfaceKernel?.create?.({surface:popup,trigger:button,returnFocus:button,kind:"popover",role:"menu",ariaLabel:menu.label,
    initialFocus:focus?choices()[0]:null,removeOnClose:true,dismissOnOutside:true,dismissOnScroll:true,
    onClosed:()=>{if(WS.menu?.popup===popup)WS.menu=null;}});
  if(!surface)return;
  const rect=button.getBoundingClientRect(),margin=8;
  popup.style.maxHeight=`${Math.max(48,window.innerHeight-margin*2)}px`;
  popup.style.left=`${Math.max(margin,Math.min(rect.left,window.innerWidth-popup.offsetWidth-margin))}px`;
  popup.style.top=`${Math.max(margin,Math.min(rect.bottom+3,window.innerHeight-popup.offsetHeight-margin))}px`;
  WS.menu={index,button,popup};
  popup.addEventListener("keydown",e=>{const items=choices(),current=items.indexOf(document.activeElement);if(["ArrowDown","ArrowUp","Home","End"].includes(e.key)){e.preventDefault();items[e.key==="Home"?0:e.key==="End"?items.length-1:(current+(e.key==="ArrowDown"?1:-1)+items.length)%items.length]?.focus();}
    else if(["ArrowLeft","ArrowRight"].includes(e.key)){e.preventDefault();WS.openMenu((index+(e.key==="ArrowRight"?1:-1)+WS.commands().length)%WS.commands().length,true);}
    else if(e.key==="Escape"){e.preventDefault();WS.closeMenu(true);}else if(e.key==="Tab")WS.closeMenu();});
  if(focus)choices()[0]?.focus();
};
WS.pageChromeState = () => {
  const context=WS.commandContext(),labels=WS.sidebar.root?{whiteboard:"排轴白板",rotation:"计算模型（高级）",result:"计算报告",data:"资料库",agent:"AI 助手"}:{whiteboard:"创作白板",rotation:"高级编辑",result:"计算报告",data:"资料库",agent:"AI 助手"};
  let documentSummary="不依赖当前文档";
  if(context.board){const board=window.WB?.state?.board;documentSummary=!board?"尚未打开文档":`${WB.dirty?.()?"尚未保存 · ":WB.state.path?"已保存 · ":"新白板 · "}${board.title||"未命名白板"}`;}
  else if(context.plan){const template=window.RT?.tpl?.();documentSummary=!template?"尚未打开方案":`${window.PL?.dirty?.()?"尚未保存 · ":RT.state.path?"已保存 · ":"新方案 · "}${DS.prose(template.meta?.name||"未命名方案","方案中文名待补")}`;}
  else if(context.tab==="data")documentSummary="公共资料与版本维护";
  else if(context.tab==="agent")documentSummary="对话、任务和运行详情";
  return {context,title:labels[context.tab]||"工作区",documentSummary};
};
WS.quickConfiguration = context => {
  if(context.board&&!context.haveBoard)return {direct:["new-board","open"],more:[]};
  if(context.tab==="result")return {direct:context.plan?["calculate"]:[],more:["export-json","export-xlsx","export-formula","export-csv","export-resolved","save-as"]};
  if(context.board)return {direct:[...(context.boardDirty?["save"]:[]),"add-action"],more:["equipment","validate-board","agent-complete","handoff","return","save-as"]};
  if(context.tab==="rotation")return {direct:[...(context.planDirty?["save"]:[]),"calculate"],more:["equipment","checks","mechanics","add-hit","add-buff","add-switch","save-as"]};
  if(context.tab==="data")return {direct:[],more:["update-db","annotations"]};
  return {direct:[],more:[]};
};
WS.openQuickMore = button => {
  WS.closeMenu();
  const registry=WS.registerCommands(),context=WS.commandContext(),ids=WS.quickConfiguration(context).more,
    commands=ids.map(id=>registry.resolve(id,context)).filter(command=>command?.visible);
  if(!commands.length)return false;
  if(!window.AppContextMenu?.forDocument){const tools=WS.commandGroups.findIndex(group=>group.id==="tools");WS.openMenu(Math.max(0,tools),true);return true;}
  return AppContextMenu.forDocument(document,window).open({target:button,trigger:button,returnFocus:button,label:"更多操作",items:commands.map(command=>({
    id:command.id,label:command.label,enabled:command.enabled,disabledReason:command.disabledReason,danger:command.danger,run:()=>WS.invoke(command.id),
  })),onError:error=>WS.report({factKey:"quick.more.failed",kind:"error",message:"无法执行所选操作。",details:{error:String(error?.message||error)}})});
};
WS.refresh = () => {
  if(!WS.ready)return;
  const title=document.getElementById("workspace-plan-title"),template=window.RT?.tpl?.();
  if(title){const name=template?.meta?.name||"未打开方案",dirty=!!window.PL?.dirty?.();title.textContent=`${dirty?"● ":""}${DS.prose(name,"方案中文名待补")}`;title.title=RT.state.path||"尚未保存";}
  const chrome=WS.pageChromeState(),pageTitle=document.getElementById("workspace-page-title"),documentSummary=document.getElementById("workspace-document-summary"),statusSummary=document.getElementById("workspace-status-summary"),pageChrome=document.getElementById("workspace-page-chrome");
  if(pageTitle)pageTitle.textContent=chrome.title;if(documentSummary)documentSummary.textContent=chrome.documentSummary;if(statusSummary)statusSummary.textContent=chrome.documentSummary;
  if(pageChrome)pageChrome.dataset.page=chrome.context.tab;
  const quick=document.getElementById("workspace-quick");if(!quick){WS.refreshSidebar();return;}
  const registry=WS.registerCommands(),configuration=WS.quickConfiguration(chrome.context),items=configuration.direct.map(id=>registry.resolve(id,chrome.context)).filter(command=>command?.visible),
    more=configuration.more.map(id=>registry.resolve(id,chrome.context)).filter(command=>command?.visible),signature=JSON.stringify({page:chrome.context.tab,direct:items.map(item=>[item.id,item.label,item.enabled]),more:more.map(item=>[item.id,item.enabled])});
  if(quick.dataset.signature===signature){WS.statusCenter?.render?.();WS.attentionShelf?.render?.();WS.refreshSidebar();return;}quick.dataset.signature=signature;quick.replaceChildren();
  for(const item of items.slice(0,2)){const button=UI.button(item.shortLabel,()=>WS.invoke(item.id),item.level==="primary"?"primary":"");button.disabled=!item.enabled;button.title=item.enabled?item.label:item.disabledReason;button.dataset.command=item.id;quick.append(button);}
  if(more.length){const button=UI.button("更多",()=>WS.openQuickMore(button),"workspace-more-button");button.setAttribute("aria-haspopup","menu");button.setAttribute("aria-expanded","false");quick.append(button);}
  WS.statusCenter?.render?.();WS.attentionShelf?.render?.();WS.refreshSidebar();
};
WS.init = () => {
  if(WS.ready)return;WS.ready=true;WS.loadPreferences();WS.registerCommands();const bar=document.getElementById("workspace-menubar");
  WS.mountGlobalShell();
  WS.commands().forEach((menu,index)=>{const button=UI.button(menu.label,()=>WS.menu?.index===index?WS.closeMenu():WS.openMenu(index,true),"workspace-menu-button");button.setAttribute("role","menuitem");button.setAttribute("aria-haspopup","menu");button.setAttribute("aria-expanded","false");button.tabIndex=index===0?0:-1;
    button.addEventListener("pointerenter",()=>{if(WS.menu&&WS.menu.index!==index)WS.openMenu(index);});
    button.addEventListener("keydown",e=>{if(["ArrowDown","Enter"," "].includes(e.key)){e.preventDefault();WS.openMenu(index,true);}else if(["ArrowLeft","ArrowRight"].includes(e.key)){e.preventDefault();const buttons=bar.querySelectorAll("button"),next=(index+(e.key==="ArrowRight"?1:-1)+buttons.length)%buttons.length;buttons.forEach((b,i)=>b.tabIndex=i===next?0:-1);buttons[next].focus();}else if(e.key==="Escape")WS.closeMenu(true);});bar.append(button);
  });
  document.addEventListener("pointerdown",e=>{if(WS.menu&&!WS.menu.popup.contains(e.target)&&!bar.contains(e.target))WS.closeMenu();});
  window.addEventListener("blur",()=>WS.closeMenu());
  window.addEventListener("keydown",e=>{
    if(document.getElementById("genshin-legacy-editor")?.hidden)return;
    if(e.defaultPrevented||WS.modalOpen())return;
    if(e.key==="Escape"&&WS.menu){e.preventDefault();WS.closeMenu(true);return;}
    if(!(e.ctrlKey||e.metaKey)||e.altKey)return;const key=e.key.toLowerCase();
    const id=key==="o"?"open":key==="s"?(e.shiftKey?"save-as":"save"):["1","2","3"].includes(key)?`axis-${["actions","effects","damage"][Number(key)-1]}`:null;
    if(id){e.preventDefault();if(key==="s")document.activeElement?.blur?.();WS.invoke(id);}
  });
  WS.mount(document.getElementById("rotation-main"),{key:"rotation",left:"#team-panel",right:".timeline-inspector",repaint:()=>window.TV?.requestPaint?.()});
  WS.refresh();
};
