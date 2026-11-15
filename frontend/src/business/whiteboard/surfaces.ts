import {type WhiteboardPorts} from "./ports";
export function createWhiteboardSurfaces(ports:WhiteboardPorts){
  const WB=ports.owner,UI=ports.ui,$=ports.query,callApi=ports.api,setStatus=ports.status,services=ports.services;
const commandMenu =()=>services.contextMenu?.forDocument?.(ports.document,ports.events)||null;
const menuError =(error:any)=>setStatus(`操作未完成：${String(error?.message||error||"未知错误")}`);
const openCommandMenu =(target:any,label:any,items:any)=>{
  const menu=WB.commandMenu();if(!menu)return false;
  return menu.open({target,trigger:target,returnFocus:target,label,items,onError:WB.menuError});
};
const bindCommandMenu =(target:any,label:any,items:any)=>{
  const menu=WB.commandMenu();if(!menu)return false;
  menu.bind(target,{label,items,onError:WB.menuError});return true;
};
// A synchronous edit only proposes a document counter. Python Store / Agent
// application validates previous -> next and performs the real SHA / live CAS.
const dialog =(title:any,description:any)=>{
  // Whiteboard dialogs own drafts, pending promises or late-response guards.
  // They must be closed through their own explicit action, never displaced by
  // another feature opening a modal behind them.
  const d=services.dialog?.create?.({title,description,className:"wb-dialog",onRequestClose:(reason:any)=>reason!=="replaced"})||null;
  d?.onClosed?.(()=>cleanupDialog(d));
  if(!d){const message="已有窗口正在处理或保留未确认内容，请先完成或关闭该窗口后再试。";
    if(services.workspace?.report)services.workspace.report({factKey:"whiteboard.dialog.blocked",kind:"warning",message,scope:"local",projectToFooter:false});else setStatus(message);}
  return d;
};
const cleanupDialog=(d:any)=>{if(!d||d.wbClosed)return;d.wbClosed=true;d.wbCleanup?.();if(WB.state.editorSession?.dialog===d)WB.state.editorSession=null;};
const closeDialog =(d:any)=>{if(!d||d.wbClosed)return;
  if(typeof d.destroy==="function")d.destroy("whiteboard-close");else{d.close?.();d.remove?.();}cleanupDialog(d);};
const leave =(options:any={})=>{
  if(WB.unappliedEditor())return new Promise(resolve=>{let settled=false;const d=WB.dialog("角色设置尚未应用","请返回角色设置并保存，或明确放弃本次角色设置后继续。"),finish=async (discard:any,returnEditor=false)=>{
    if(settled)return;settled=true;WB.closeDialog(d);if(!discard){if(returnEditor&&!WB.state.editorSession){const id=Object.keys(WB.state.equipmentDrafts||{})[0];if(id)WB.editCharacter(id);}resolve(false);return;}
    if(options.openIntent&&!options.openIntent.current()){resolve(false);return;}
    if(WB.state.editorSession){WB.state.editorSession.dialog.wbDiscard=true;WB.closeDialog(WB.state.editorSession.dialog);}WB.state.equipmentDrafts={};
    if(options.openIntent&&!options.openIntent.acceptDiscardedDrafts()){resolve(false);return;}resolve(await WB.leave(options));};
    if(!d){resolve(false);return;}d.onClosed?.(()=>finish(false));d.appBody.append(UI.button("返回角色设置",()=>finish(false,true)),UI.button("放弃角色设置并继续",()=>finish(true)));if(!d.onClosed)d.addEventListener("cancel",()=>finish(false));});
  return !WB.dirty()?Promise.resolve(true):new Promise(resolve=>{
  let settled=false;const d=WB.dialog("白板尚未保存","可以先保存再继续，或明确放弃本次修改。切换页签会保留草稿。"),finish=(v:any)=>{if(settled)return;settled=true;WB.closeDialog(d);resolve(v);};
  if(!d){resolve(false);return;}d.onClosed?.(()=>finish(false));d.appBody.append(UI.button("保存后继续",async()=>{if(await WB.save())finish(!options.openIntent||options.openIntent.acceptSavedIdentity());}),UI.button("放弃修改",()=>finish(true)),UI.button("继续编辑",()=>finish(false)));
  if(!d.onClosed)d.addEventListener("cancel",()=>finish(false));
});};
const requestClose =()=>{
  if(services.workbench?.ready)return services.workbench.requestClose();
  if(!WB.dirty()&&!services.plan?.dirty?.())return true;
  if(!WB.state.closing){WB.state.closing=true;(async()=>{try{if(await WB.leave()&&(!services.plan||await services.plan.leave()))await callApi("confirm_close");}
    finally{WB.state.closing=false;}})();}return false;
};
const fold =(label:any,key:any)=>{const d=UI.el("details",null,"wb-fold");d.open=!!WB.state[key];d.append(UI.el("summary",label));
  d.addEventListener("toggle",()=>{WB.state[key]=d.open;});return d;};
const checkStatus =(value:any)=>(({passed:"通过",ready:"就绪",failed:"失败",pending:"待核实",invalid:"输入无效",not_checked:"未检查",not_verified:"未验证",partial:"部分完成",complete:"完整",calculated:"已计算",not_calculated:"未计算",unproven:"待证明"} as Record<string,string>)[value]||"待核实");
const rawDetails =(label:any,value:any)=>{const d=UI.el("details",null,"wb-raw");d.append(UI.el("summary",label));let filled=false;
  d.addEventListener("toggle",()=>{if(d.open&&!filled){filled=true;d.append(UI.el("pre",JSON.stringify(value,null,2)));}});return d;};

  return {commandMenu,menuError,openCommandMenu,bindCommandMenu,dialog,closeDialog,leave,requestClose,fold,checkStatus,rawDetails};
}
