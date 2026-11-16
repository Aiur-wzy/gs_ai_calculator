import {type WhiteboardPorts} from "./ports";
export function createWhiteboardDocument(ports:WhiteboardPorts){
  const WB=ports.owner,UI=ports.ui,services=ports.services;
const json =()=>services.transport ? services.transport.stringify(WB.state.board) : JSON.stringify(WB.state.board);
// Agent CAS checks run on every chat refresh.  Keep their identity entirely
// scalar and materialize the potentially large board/result payload only when
// an Agent request actually needs it.  The cached canonical JSON strings are
// the exact local content digests; the Host still computes and validates its
// cryptographic fingerprints at the trust boundary.
const invalidateAgentPayload =(boardReplaced=false,resultReplaced=false)=>{
  WB.state.agentPayloadRevision=(WB.state.agentPayloadRevision||0)+1;
  if(boardReplaced)WB.state.agentBoardGeneration=(WB.state.agentBoardGeneration||0)+1;
  if(resultReplaced)WB.state.agentResultGeneration=(WB.state.agentResultGeneration||0)+1;
  WB.state.agentPayloadCache=null;
};
const agentPayloadIdentity =()=>({
  payloadRevision:WB.state.agentPayloadRevision||0,
  boardGeneration:WB.state.agentBoardGeneration||0,
  resultGeneration:WB.state.agentResultGeneration||0,
  boardRevision:Number.isInteger(WB.state.board?.revision)?WB.state.board.revision:null,
});
const agentPayloadMatches =(base:any)=>{
  const current=WB.agentPayloadIdentity();return !!base&&base.documentRef===WB.state.board&&
    base.payloadRevision===current.payloadRevision&&base.boardGeneration===current.boardGeneration&&
    base.resultGeneration===current.resultGeneration&&base.boardRevision===current.boardRevision;
};
const agentPayloadSnapshot =()=>{
  const identity=WB.agentPayloadIdentity(),cache=WB.state.agentPayloadCache;
  if(cache&&cache.documentRef===WB.state.board&&cache.resultRef===WB.state.result&&
      cache.payloadRevision===identity.payloadRevision&&cache.boardGeneration===identity.boardGeneration&&
      cache.resultGeneration===identity.resultGeneration&&cache.boardRevision===identity.boardRevision)return cache;
  const document=services.transport.clone(WB.state.board),calculated=WB.state.result?services.transport.clone(WB.state.result):null,
    applicationPayload={kind:"whiteboard",board:document,calculated},json=services.transport.stringify(document),
    payloadJson=services.transport.stringify(applicationPayload);
  const snapshot={...identity,document,applicationPayload,json,payloadJson,documentRef:WB.state.board,resultRef:WB.state.result};
  WB.state.agentPayloadCache=snapshot;return snapshot;
};
const setAgentResult =(result:any)=>{
  const replaced=WB.state.result!==result;WB.state.result=result;
  if(replaced)WB.state.expandedRows=new Set();
  WB.invalidateAgentPayload(false,replaced);return result;
};
const unappliedEditor =()=>!!WB.state.editorSession&&WB.state.editorSession.base!==WB.state.editorSession.json()||Object.keys(WB.state.equipmentDrafts||{}).length>0;
const dirty =()=>WB.unappliedEditor()||!!WB.state.board&&WB.json()!==WB.state.savedJson;
const token =()=>({board:WB.state.board,json:WB.json(),request:++WB.state.request,path:WB.state.path,sha:WB.state.fileSha});
const current =(t:any)=>t.board===WB.state.board&&t.json===WB.json()&&t.request===WB.state.request&&t.path===WB.state.path&&t.sha===WB.state.fileSha;
const actor =(id:any)=>WB.state.board?.team.find((c:any)=>c.id===id);
const action =(id:any)=>WB.state.board?.actions.find((a:any)=>a.id===id);
const typeName =(id:any)=>WB.types.find((row:any)=>row[0]===id)?.[1]||"动作类别待补";
const actionName =(a:any)=>a.label||WB.typeName(a.action_type_id);
const nextRevisionProposal =()=>{
  const previous=WB.state.board?.revision,policy=WB.state.revisionPolicy;
  if(policy&&policy.contract_id!=="whiteboard-intent-revision-v1")throw new Error("当前白板编辑规则不匹配，请重新打开文档。");
  const step=policy?.proposal_step??1,next=previous+step;
  if(!Number.isSafeInteger(previous)||previous<0||step!==1||!Number.isSafeInteger(next)||next>(policy?.maximum??Number.MAX_SAFE_INTEGER))throw new Error("当前白板方案版本无效，请重新打开文档。");
  return next;
};
const touch =(proposal=WB.nextRevisionProposal())=>{WB.state.board.revision=proposal;WB.state.request++;if(WB.state.result)WB.state.staleResultPath=WB.state.resultPath;
  const replacedResult=WB.state.result!==null;WB.state.result=null;WB.state.appliedResultFile=null;WB.invalidateAgentPayload(false,replacedResult);
  WB.state.resultProjection=null;WB.state.selectedDerived=null;WB.state.pending=[];WB.state.equipmentReport=null;};
const change =(fn:any)=>{const proposal=WB.nextRevisionProposal();fn(WB.state.board);WB.touch(proposal);WB.render();};
const replace =(data:any,saved=true,render=true)=>{
  if(data.revision_policy)WB.state.revisionPolicy=UI.clone(data.revision_policy);
  WB.state.request++;WB.state.board=data.board;WB.state.path=data.path||"";WB.state.fileSha=data.file_sha||null;
  WB.state.savedJson=saved?WB.json():null;WB.state.pending=data.pending||[];WB.state.result=null;WB.state.appliedResultFile=null;WB.state.resultProjection=null;WB.state.resultPath=null;WB.state.staleResultPath=null;
  WB.invalidateAgentPayload(true,true);
  WB.state.equipmentDrafts={};WB.state.reusableResult=null;WB.state.equipmentReport=null;
  WB.state.selectedActor=data.board.team[0]?.id||null;WB.state.selectedAction=null;WB.state.selectedDerived=null;
  WB.state.axisScrollTop={};WB.state.expandedRows=new Set();WB.state.axisPacked=null;
  WB.state.mode="actions";WB.state.effectFilter="all";WB.state.damageFocus="events";WB.state.cursor=0;WB.state.scale=1;WB.state.scroll=0;WB.state.page=0;WB.state.addOpen=false;WB.state.derivedBack=null;if(render)WB.render();
};
// Applying a reviewed construction candidate replaces only the unsaved draft, never the saved file.
const applyAgentDraft =(board:any,pending:any[]=[],calculation:any=null,nativeDelivery:any=null)=>{
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
const restoreAgentResultOnly =(calculation:any,nativeDelivery:any)=>{
  if(!calculation||typeof calculation!=="object"||!calculation.calculated||!nativeDelivery||typeof nativeDelivery.sessionId!=="string"||typeof nativeDelivery.deliveryCapability!=="string")return false;
  WB.setAgentResult(calculation.calculated);
  WB.state.reusableResult={board:UI.clone(WB.state.board),owner:nativeDelivery.owner,sessionId:nativeDelivery.sessionId,deliveryCapability:nativeDelivery.deliveryCapability};
  WB.state.mode="damage";WB.render();return true;
};

  return {json,invalidateAgentPayload,agentPayloadIdentity,agentPayloadMatches,agentPayloadSnapshot,setAgentResult,unappliedEditor,dirty,token,current,actor,action,typeName,actionName,nextRevisionProposal,touch,change,replace,applyAgentDraft,restoreAgentResultOnly};
}
