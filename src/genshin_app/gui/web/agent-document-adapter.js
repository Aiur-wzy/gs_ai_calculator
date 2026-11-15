(function (global) {
  "use strict";

  const KINDS = new Set(["whiteboard", "native"]);
  const isRecord = value => value !== null && typeof value === "object" && !Array.isArray(value);
  const freezeTree = value => {
    if (value && typeof value === "object" && !Object.isFrozen(value)) {
      for (const child of Object.values(value)) freezeTree(child);
      Object.freeze(value);
    }
    return value;
  };
  const boundedText = (value, fallback, limit) => String(value || fallback).slice(0, limit);

  function create(options = {}) {
    const transport = options.transport;
    if (!transport || typeof transport.clone !== "function" || typeof transport.stringify !== "function") {
      throw new TypeError("agent_document_transport_invalid");
    }
    const get = (name, fallback = null) => typeof options[name] === "function" ? options[name]() : options[name] || fallback;
    const cloneFrozen = value => freezeTree(transport.clone(value));

    const hasDraft = kind => {
      if (!KINDS.has(kind)) return true;
      const source = kind === "whiteboard" ? get("whiteboard") : get("plan");
      return Boolean(kind === "whiteboard" ? source?.unappliedEditor?.() : source?.draftChanged?.());
    };

    const capture = kind => {
      if (!KINDS.has(kind)) throw new TypeError("agent_document_kind_invalid");
      if (hasDraft(kind)) throw new Error("当前有尚未应用的装备与面板草稿。请先应用或明确放弃草稿，再让 Agent 处理；聊天内容会保留。");
      if (kind === "whiteboard") {
        const wb = get("whiteboard"), board = wb?.state?.board;
        if (!board) throw new Error("请先新建或打开创作白板。");
        if (typeof wb.agentPayloadSnapshot === "function") {
          const raw = wb.agentPayloadSnapshot();
          if (!isRecord(raw) || !isRecord(raw.document) || !isRecord(raw.applicationPayload) || typeof raw.json !== "string" || typeof raw.payloadJson !== "string") {
            throw new Error("创作白板没有返回可核验的 Agent 快照。");
          }
          return Object.freeze({kind,document:cloneFrozen(raw.document),applicationPayload:cloneFrozen(raw.applicationPayload),payloadJson:raw.payloadJson,
            payloadRevision:raw.payloadRevision,boardGeneration:raw.boardGeneration,resultGeneration:raw.resultGeneration,boardRevision:raw.boardRevision,
            key:board.board_id,json:raw.json,title:board.title||"未命名白板",path:wb.state.path,sha:wb.state.fileSha,sourceEpoch:wb.state.request,documentRef:board});
        }
        const applicationPayload={kind:"whiteboard",board:transport.clone(board),calculated:wb.state.result?transport.clone(wb.state.result):null};
        return Object.freeze({kind,document:cloneFrozen(board),applicationPayload:cloneFrozen(applicationPayload),payloadJson:transport.stringify(applicationPayload),key:board.board_id,json:transport.stringify(board),title:board.title||"未命名白板",path:wb.state.path,sha:wb.state.fileSha,sourceEpoch:wb.state.request,documentRef:board});
      }
      const pl=get("plan"),rt=get("rotation"),plan=pl?.state?.plan;
      if(!plan)throw new Error("请先打开原生装备方案；手工数值模板可先通过装备与面板建立原生方案，或使用创作白板。");
      const applicationPayload={kind:"native",plan:transport.clone(plan)},input=transport.clone(plan);delete input.resolution;delete input.resolved_template;
      return Object.freeze({kind:"native",document:cloneFrozen(input),applicationPayload:cloneFrozen(applicationPayload),payloadJson:transport.stringify(applicationPayload),key:plan.plan_id,json:pl.currentInputJson(),title:plan.manual_template?.meta?.name||"未命名方案",
        path:rt?.state?.path,sha:pl.state.fileSha,sourceEpoch:pl.state.sourceEpoch,savedJson:pl.state.savedJson,documentRef:plan});
    };

    // This is a display/capability projection, never an authorization token.
    // It intentionally reveals only whether a path exists, not the path itself.
    const workspace = (base = null, currentKind = null) => {
      const wb=get("whiteboard"),pl=get("plan"),rt=get("rotation"),rs=get("result"),ws=get("workspace");
      const kind=base?.kind||currentKind;if(!KINDS.has(kind))throw new TypeError("agent_document_kind_invalid");
      const board=kind==="whiteboard",document=base?.document||(board?wb?.state?.board:pl?.state?.plan),displayName=base?.title||(board?document?.title:document?.manual_template?.meta?.name)||"未命名方案";
      const dirty=board?Boolean(wb?.dirty?.()??true):Boolean(pl?.dirty?.()??true),hasPath=Boolean(board?wb?.state?.path:rt?.state?.path),canSave=Boolean(document),canExport=!board&&Boolean(rs?.state?.result)&&Boolean(rs?.isCurrent?.()??true)&&!rs?.state?.pending;
      return freezeTree({document_kind:kind,display_name:boundedText(displayName,"未命名方案",256),dirty,has_path:hasPath,current_view:boundedText(ws?.tab?.()||(board?"whiteboard":"rotation"),board?"whiteboard":"rotation",64),can_save:canSave,can_open:true,can_export:canExport});
    };

    const sameContent=(left,right)=>left?.kind===right?.kind&&left?.key===right?.key&&left?.json===right?.json&&left?.payloadJson===right?.payloadJson&&left?.documentRef===right?.documentRef;
    const same=(left,right)=>sameContent(left,right)&&left?.path===right?.path&&left?.sha===right?.sha&&left?.sourceEpoch===right?.sourceEpoch;
    const currentMatches=base=>{try{
      if(!base||!KINDS.has(base.kind)||hasDraft(base.kind))return false;
      const wb=get("whiteboard");if(base.kind==="whiteboard"&&typeof wb?.agentPayloadMatches==="function"&&base.payloadRevision!==undefined)return wb.agentPayloadMatches(base)&&base.path===wb.state.path&&base.sha===wb.state.fileSha&&base.sourceEpoch===wb.state.request;
      return same(base,capture(base.kind));
    }catch{return false;}};

    const hostSnapshot=base=>{
      if(!base||!KINDS.has(base.kind)||!isRecord(base.document)||!isRecord(base.applicationPayload))throw new TypeError("agent_document_snapshot_invalid");
      return freezeTree({document_kind:base.kind,document_id:boundedText(base.key,"",128),document:transport.clone(base.document),application_payload:transport.clone(base.applicationPayload),workspace:workspace(base,base.kind)});
    };

    // Restore remains a two-phase caller-owned operation. The adapter checks
    // the frozen baseline and exact document identity, then delegates one
    // Renderer mutation to an explicitly injected applier. It never infers a
    // Host restore schema or writes editor state on its own.
    const applyRestoreTarget = async (target, {expectedBase, apply} = {}) => {
      if(!expectedBase||!currentMatches(expectedBase))return freezeTree({applied:false,reason:"document_baseline_changed",snapshot:null});
      if(!isRecord(target)||target.kind!==expectedBase.kind||!isRecord(target.document)||!isRecord(target.applicationPayload)||typeof apply!=="function")return freezeTree({applied:false,reason:"restore_target_invalid",snapshot:null});
      const identity=target.kind==="whiteboard"?target.document.board_id:target.document.plan_id;if(identity!==expectedBase.key)return freezeTree({applied:false,reason:"restore_target_identity_mismatch",snapshot:null});
      const accepted=await apply(Object.freeze({kind:target.kind,document:cloneFrozen(target.document),applicationPayload:cloneFrozen(target.applicationPayload)}));
      if(accepted!==true)return freezeTree({applied:false,reason:"renderer_apply_unconfirmed",snapshot:null});
      const snapshot=capture(target.kind);return freezeTree({applied:true,reason:null,snapshot});
    };

    return Object.freeze({hasDraft,capture,workspace,sameContent,same,currentMatches,hostSnapshot,applyRestoreTarget});
  }

  global.AgentDocumentAdapter=Object.freeze({create});
})(window);
