import {type WhiteboardPorts} from "./ports";
export function createWhiteboardWorkflow(ports:WhiteboardPorts){
  const WB=ports.owner,services=ports.services;
const topologyDraft =()=>WB.state.board?.event_topology_draft||WB.state.board?.event_topology||WB.state.board?.extensions?.event_topology_draft||WB.state.board?.extensions?.event_topology||null;
const numericDraft =()=>WB.state.board?.numeric_binding||WB.state.board?.numeric_model||WB.state.board?.extensions?.numeric_binding||WB.state.board?.extensions?.numeric_model||null;
const topologyReceipt =()=>services.workbench?.topologyReceipt?.(WB.state.board,WB.state.result)||WB.state.board?.event_topology_coverage_receipt||WB.state.board?.extensions?.event_topology_coverage_receipt||null;
const executionBindingReceipt =()=>services.workbench?.executionBindingReceipt?.(WB.state.board,WB.state.result)||WB.state.board?.event_topology_execution_binding_receipt||WB.state.board?.extensions?.event_topology_execution_binding_receipt||null;
const workflowState =()=>{
  if(!services.workbench?.workflowState)return null;const topology=WB.topologyDraft(),numeric=WB.numericDraft(),state=services.workbench.workflowState(null,null,WB.state.board,WB.state.result);
  if(!WB.state.result&&numeric&&state.index<3){state.stage="numeric_binding";state.index=3;state.energyStatus=state.energyStatus==="unknown"?"not_checked":state.energyStatus;}
  else if(!WB.state.result&&topology&&state.index<2){state.stage="event_topology";state.index=2;state.energyStatus="not_checked";}
  return state;
};
const topologyStats =()=>{const topology=WB.topologyDraft()||{},array=(...values:any[])=>values.find(Array.isArray)||[];return {
  status:topology.stage_status||topology.status||"in_progress",nodes:array(topology.nodes,topology.events,topology.components).length,
  bindings:array(topology.bindings,topology.edges,topology.relations).length,
  unresolved:array(topology.unresolved_components,topology.pending_items,topology.unresolved).length,
};};
const bindingStats =()=>{const receipt=WB.executionBindingReceipt(),counts=receipt?.counts||{},coverage=receipt?.coverage||{},allMissing=Array.isArray(coverage.missing_node_ids)?coverage.missing_node_ids:[];return {
  receipt,status:String(receipt?.status||"").toLowerCase(),expected:counts.expected_nodes??counts.expected??0,bound:counts.bound_nodes??counts.bound??0,
  resolved:counts.resolved_bindings??counts.resolved??0,missing:allMissing.slice(0,20),
  missingTruncated:!!coverage.missing_node_ids_truncated||allMissing.length>20,issue:Array.isArray(receipt?.issues)?receipt.issues[0]:null,
};};
const bindingIssueText =(issue:any)=>{if(services.workbench?.bindingIssueText)return services.workbench.bindingIssueText(issue);const value=typeof issue==="string"?issue:issue?.message||issue?.code||"存在未说明的问题",text=String(value);return text.length>512?text.slice(0,512)+"…":text;};

  return {topologyDraft,numericDraft,topologyReceipt,executionBindingReceipt,workflowState,topologyStats,bindingStats,bindingIssueText};
}
