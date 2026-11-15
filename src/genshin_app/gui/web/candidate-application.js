// App-only Renderer application port. The reused AgentDocumentAdapter is a
// pure document/CAS module; no old Session, chat, Turn or workflow owner runs.
window.CandidateEditorApplication=Object.freeze({create(){
  const adapter=AgentDocumentAdapter.create({transport:JSONTransport,
    whiteboard:()=>window.WB,plan:()=>window.PL,rotation:()=>window.RT,result:()=>window.RS,workspace:()=>window.WS});
  const exists=kind=>kind==="whiteboard"?!!WB.state.board:!!PL.state.plan;
  const absence=kind=>{
    if(exists(kind)||adapter.hasDraft(kind))throw new Error("当前已有文档或未应用的装备草稿，不能创建候选文档。");
    return {kind,epoch:kind==="whiteboard"?WB.state.request:PL.state.sourceEpoch,request:kind==="whiteboard"?WB.state.agentPayloadRevision:PL.state.request};
  };
  const capture=kind=>exists(kind)?adapter.capture(kind):(absence(kind),null);
  return Object.freeze({capture,matches:snapshot=>adapter.currentMatches(snapshot),absence,
    async validateSnapshot(snapshot){
      if(!adapter.currentMatches(snapshot))throw new Error("当前正式文档已变化，请重新审核。");
      if(snapshot.kind==="native"&&snapshot.path&&snapshot.sha){
        const response=await callApi("inspect_plan_file",snapshot.path);
        if(response.code||response.data.file_sha!==snapshot.sha||!adapter.currentMatches(snapshot))throw new Error("磁盘方案已变化，当前候选未写入，请重新打开并核对文件。");
      }
    },
    createDocument(kind,seed,expectedAbsence){
      if(JSONTransport.stringify(absence(kind))!==JSONTransport.stringify(expectedAbsence))throw new Error("审核后新建或打开了文档，候选没有覆盖当前文档。");
      if(kind==="whiteboard")WB.replace({board:UI.clone(seed),path:"",file_sha:null,pending:[]},false,false);
      else PL.replace(UI.clone(seed),"",null,false);
      return adapter.capture(kind);
    },
    async applyReviewed(reviewed,base,conversationId){
      const kind=base.kind,document=kind==="whiteboard"?reviewed.board:reviewed.plan;
      const payload=kind==="whiteboard"?{kind,board:document,calculated:reviewed.calculated_delivery?.calculated||null}:{kind,plan:document};
      const target={kind,document,applicationPayload:payload};
      const applied=await adapter.applyRestoreTarget(target,{expectedBase:base,apply:async()=>{
        if(kind==="whiteboard"){
          const delivery=reviewed.calculated_delivery;
          if(delivery&&(Object.hasOwn(delivery,"path")||Object.hasOwn(delivery,"file_sha")||typeof delivery.delivery_capability!=="string"))throw new Error("计算交付凭据不完整，白板未更新。");
          WB.applyAgentDraft(UI.clone(document),reviewed.issues||[],delivery?UI.clone(delivery):null,delivery?{owner:"native_dsh",sessionId:conversationId,deliveryCapability:delivery.delivery_capability}:null);
        }else{
          PL.replace(UI.clone(document),base.path,base.sha,false);PL.state.savedJson=base.savedJson;PL.renderStatus();
        }
        return true;
      }});
      if(!applied.applied)throw new Error("正式文档已经变化，候选未能完成同步。请保留当前文档并核对同步状态。");
      return applied.snapshot;
    },
  });
}});
