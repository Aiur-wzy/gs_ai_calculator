"use strict";

// The Renderer-side transactional store has no transport, subscription,
// persistence, command, or UI authority.  It is the sole production authority
// for admitted LegacyPoll facts and the acknowledged read cursor.
(function (global) {
  const deepFreeze = value => {
    if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
    for (const child of Object.values(value)) deepFreeze(child);
    return Object.freeze(value);
  };
  const cleanCode = value => typeof value === "string" && /^[a-z0-9_.:-]{1,128}$/.test(value) ? value : "event_store_failed";
  const TRANSACTIONAL_LIMITS = Object.freeze({sessions:128,events:1000,messages:500,tools:300,budgets:128,actions:100,diagnostics:256});
  const SESSION_ID = /^[A-Za-z0-9._:-]{1,128}$/;
  const failureResult = (code, diagnostics = []) => deepFreeze({
    ok:false, contract_version:"legacy_agent_poll_v1", partial:true,
    rejection_code:cleanCode(code), diagnostics:[...diagnostics],
  });
  const trimmedValues = (map, limit) => {
    const rows=[...map.values()];return rows.length<=limit?rows:rows.slice(rows.length-limit);
  };
  const refreshSet = (map,key,value) => {if(map.has(key))map.delete(key);map.set(key,value);};
  const boundedDiagnostics = diagnostics => {
    const marker="diagnostics_trimmed",rows=[...diagnostics];
    if(rows.length<=TRANSACTIONAL_LIMITS.diagnostics)return rows;
    const kept=rows.filter(row=>row!==marker).slice(-(TRANSACTIONAL_LIMITS.diagnostics-1));
    return [...kept,marker];
  };
  const mapBy = (rows, key) => new Map((rows||[]).filter(row=>row&&row[key]!=null).map(row=>[row[key],row]));
  const own = (value,key) => !!value&&typeof value==="object"&&Object.hasOwn(value,key);
  const overlayPresent = (prior,projected,envelope,keys) => {
    const next={...(prior||{})};for(const [target,source=target] of keys)if(own(envelope,source))next[target]=projected[target];return next;
  };

  function transactionalDraft(projection, prior, batchDiagnostics=[], envelope={}) {
    const reset=projection.source.reset_required===true,requested=projection.source.requested_after_seq;
    const baseline=reset?projection.baseline:null;
    if(prior&&!reset&&requested<prior.source.next_seq)throw new Error("source_cursor_regression");
    if(prior&&!reset&&requested>prior.source.next_seq)throw new Error("source_cursor_gap");
    const diagnostics=new Set([...(prior&&!reset?prior.diagnostics:[]),...batchDiagnostics]);
    const nextStoreRevision=(prior?.store_revision||0)+1;
    if(!prior&&!reset&&requested===0&&projection.source.next_seq>0)diagnostics.add("initial_increment_partial");
    const establishing=reset||!prior;
    const events=mapBy(establishing?[]:prior.events,"source_seq");
    const messages=mapBy(establishing?[]:prior.messages,"message_id");
    const tools=mapBy(reset||!prior?[]:prior.tools,"tool_id");
    const runChanged=!!prior&&own(envelope,"run_id")&&projection.session.run_id!==prior.session?.run_id;
    const budgets=mapBy(reset||!prior||runChanged?[]:prior.budget_statuses,"status");
    // During an active native turn the Host deliberately returns
    // app_actions=[] with app_actions_deferred=true because the action ledger
    // is behind the same idle checkpoint gate as the worker.  That is not an
    // authoritative empty list and must not erase the last reviewed refs.
    const actionListAuthoritative=own(envelope,"app_actions")&&envelope.app_actions_deferred!==true;
    const preserveDeferredReset=reset&&!!prior&&envelope.app_actions_deferred===true&&!own(envelope,"baseline");
    const actions=mapBy((reset&&!preserveDeferredReset)||!prior||actionListAuthoritative?[]:prior.app_action_refs,"action_id");
    if(envelope.app_actions_deferred===true)diagnostics.add("app_actions_deferred");
    if(establishing){
      for(const row of baseline?.messages||projection.baseline_messages||[]){
        if(!row.message_id){diagnostics.add("baseline_message_identity_missing");continue;}
        refreshSet(messages,row.message_id,{message_id:row.message_id,role:row.role,text:row.text,run_id:row.run_id,last_source_seq:row.last_source_seq??null,content_revision:row.last_source_seq??nextStoreRevision,source:"baseline"});
      }
      for(const row of baseline?.tools||[])refreshSet(tools,row.tool_id,{...row,content_revision:row.last_source_seq??nextStoreRevision,source:"baseline"});
      for(const row of baseline?.budget_statuses||[])refreshSet(budgets,row.status,{...row,content_revision:row.last_source_seq??nextStoreRevision,source:"baseline"});
      for(const row of baseline?.app_action_refs||[])if(row.action_id)refreshSet(actions,row.action_id,row);else diagnostics.add("app_action_identity_missing");
      for(const reason of baseline?.partial_reasons||[])diagnostics.add(`baseline_partial:${reason}`);
      if(reset&&!baseline?.tools?.length&&!projection.events.some(row=>row.source_type==="tool")&&((prior?.tools||[]).length||(projection.baseline_messages||[]).length||projection.session.history_truncated))diagnostics.add("reset_tool_history_partial");
    }
    // Native Router top-level usage is the authoritative cross-turn
    // aggregate.  Usage events are per-turn audit facts and must not overwrite
    // that aggregate later in the same batch.
    const aggregateUsagePresent=own(envelope,"usage");
    let usage=aggregateUsagePresent?projection.usage:reset||!prior?(baseline?.usage??projection.usage):prior.usage;
    let candidate=reset||!prior?projection.candidate_ref:own(envelope,"candidate")?projection.candidate_ref:prior.candidate_ref;
    let application=reset||!prior?projection.application_ref:own(envelope,"application_intent")?projection.application_ref:prior.application_ref;
    for(const row of projection.app_action_refs||[])if(row.action_id)actions.set(row.action_id,row);else diagnostics.add("app_action_identity_missing");
    for(const event of projection.events){
      if(!reset&&event.source_seq<=requested)throw new Error("source_event_not_after_cursor");
      const oldEvent=events.get(event.source_seq);
      if(oldEvent&&JSON.stringify(oldEvent)!==JSON.stringify(event))throw new Error("source_sequence_conflict");
      events.set(event.source_seq,event);
      const payload=event.semantic_payload||{},identity=event.source_identity||{};
      if(event.source_type==="message"||event.source_type==="delta"){
        const id=identity.message_id;if(!id){diagnostics.add("message_identity_missing");continue;}
        const old=messages.get(id);if(establishing&&event.source_type==="delta"&&old?.source==="baseline"){diagnostics.add("reset_delta_history_partial");continue;}
        const text=event.source_type==="delta"?`${old?.text||""}${payload.text||""}`:payload.text||"";
        refreshSet(messages,id,{message_id:id,role:payload.role||old?.role||null,text,run_id:event.run_id||old?.run_id||null,last_source_seq:event.source_seq,content_revision:event.source_seq,source:"event"});
      }else if(event.source_type==="tool"){
        const id=identity.tool_id;if(!id){diagnostics.add("tool_identity_missing");continue;}
        const old=tools.get(id);refreshSet(tools,id,{tool_id:id,name:payload.name||old?.name||null,state:payload.state||old?.state||null,message:payload.message??old?.message??null,run_id:event.run_id||old?.run_id||null,last_source_seq:event.source_seq,content_revision:event.source_seq});
      }else if(event.source_type==="usage"){
        if(!aggregateUsagePresent)usage=payload;
      }
      else if(event.source_type==="budget_status"){
        if(payload.status){
          if(event.run_id&&budgets.size){const existingRuns=new Set([...budgets.values()].map(row=>row.run_id).filter(Boolean));if(!existingRuns.size||[...existingRuns].some(id=>id!==event.run_id))budgets.clear();}
          refreshSet(budgets,payload.status,{...payload,last_source_seq:event.source_seq,run_id:event.run_id});
        }else diagnostics.add("budget_status_identity_missing");
      }else if(event.source_type==="candidate")candidate=payload;
      else if(event.source_type==="app_action"){
        if(identity.action_id)actions.set(identity.action_id,payload);else diagnostics.add("app_action_identity_missing");
      }
    }
    const eventRows=trimmedValues(new Map([...events.entries()].sort((a,b)=>a[0]-b[0])),TRANSACTIONAL_LIMITS.events);
    if(eventRows.length<events.size)diagnostics.add("event_history_trimmed");
    const messageRows=trimmedValues(messages,TRANSACTIONAL_LIMITS.messages);if(messageRows.length<messages.size)diagnostics.add("message_history_trimmed");
    const toolRows=trimmedValues(tools,TRANSACTIONAL_LIMITS.tools);if(toolRows.length<tools.size)diagnostics.add("tool_history_trimmed");
    const budgetRows=trimmedValues(budgets,TRANSACTIONAL_LIMITS.budgets);if(budgetRows.length<budgets.size)diagnostics.add("budget_history_trimmed");
    const actionRows=trimmedValues(actions,TRANSACTIONAL_LIMITS.actions);if(actionRows.length<actions.size)diagnostics.add("action_history_trimmed");
    const terminalState=new Set(["completed","cancelled","stopped","error","failed","interrupted"]);
    const terminalProjection=terminalState.has(envelope.terminal_status)||terminalState.has(envelope.state);
    const sessionPresence=terminalProjection&&projection.session.turn_state
      ? {...envelope,turn_state:projection.session.turn_state}
      : envelope;
    const session=reset||!prior?projection.session:overlayPresent(prior.session,projection.session,sessionPresence,[["run_id"],["state"],["terminal_status"],["active"],["kind"],["workflow"],["title"],["model"],["effort"],["provider_id"],["connection_kind"],["can_resume"],["history_truncated"],["conversation_state"],["turn_state"],["document_state"],["inbox_state"],["terminal_reason"]]);
    const connection=reset||!prior?projection.connection:overlayPresent(prior.connection,projection.connection,envelope,[["credential_state"],["provider_state"],["connection_state"],["runtime_incarnation"],["runtime_state"],["runtime_health"],["failure_code","failure_code"],["failure_code","runtime_failure_code"]]);
    const baselineRef=reset&&baseline?{baseline_seq:baseline.baseline_seq,completeness:baseline.completeness,partial_reasons:baseline.partial_reasons}:prior?.baseline_ref||null;
    return deepFreeze({
      contract_version:"legacy_agent_poll_v1",partial:true,store_revision:nextStoreRevision,snapshot_revision:nextStoreRevision,
      source:projection.source,session,connection,
      events:eventRows,messages:messageRows,tools:toolRows,usage,
      budget_statuses:budgetRows,candidate_ref:candidate,app_action_refs:actionRows,
      application_ref:application,baseline_ref:baselineRef,diagnostics:boundedDiagnostics(diagnostics),
    });
  }

  // R2A foundation only: this store deliberately has no timer, transport, DOM,
  // persistence, or command authority.  ``stage`` is externally side-effect
  // free; a caller may advance its network ACK only after ``commit`` succeeds.
  function createTransactionalLegacyStore() {
    const records=new Map(),transactions=new WeakMap();
    let lifecycleRevision=0;
    function stage(input={}){
      const adapted=global.AgentWorkbenchContract.adaptLegacyAgentPollV1({...input,require_requested_after_seq:true,require_event_coverage:true});if(!adapted.ok)return adapted;
      try{
        const id=adapted.projection.source.session_id,prior=records.get(id)||null;
        if(!prior&&records.size>=TRANSACTIONAL_LIMITS.sessions)return failureResult("session_limit",adapted.diagnostics);
        const next=transactionalDraft(adapted.projection,prior,adapted.diagnostics,input.envelope);
        // A staged transaction is only a prepare receipt.  It deliberately
        // exposes no ack_cursor: transport acknowledgement becomes trusted
        // only after the one-shot CAS commit below succeeds.
        const transaction=deepFreeze({
          ok:true,contract_version:adapted.contract_version,partial:true,session_id:id,
          base_revision:prior?.store_revision||0,next_revision:next.store_revision,
          snapshot_revision:next.snapshot_revision,
          requested_cursor:adapted.projection.source.requested_after_seq,
          next_cursor:adapted.projection.source.next_seq,diagnostics:[...adapted.diagnostics],
        });
        transactions.set(transaction,{id,base_revision:prior?.store_revision||0,next,lifecycle_revision:lifecycleRevision});return transaction;
      }catch(error){return failureResult(error?.message,adapted.diagnostics);}
    }
    function commit(transaction){
      if(!transaction||typeof transaction!=="object")return failureResult("transaction_invalid");
      const prepared=transactions.get(transaction);if(!prepared)return failureResult("transaction_invalid");transactions.delete(transaction);
      if(prepared.lifecycle_revision!==lifecycleRevision)return failureResult("transaction_lifecycle_stale");
      const current=records.get(prepared.id)||null,revision=current?.store_revision||0;if(revision!==prepared.base_revision)return failureResult("transaction_stale");
      if(!current&&records.size>=TRANSACTIONAL_LIMITS.sessions)return failureResult("session_limit");
      records.set(prepared.id,prepared.next);return deepFreeze({
        ok:true,contract_version:"legacy_agent_poll_v1",partial:true,session_id:prepared.id,
        store_revision:prepared.next.store_revision,snapshot_revision:prepared.next.snapshot_revision,
        ack_cursor:prepared.next.source.next_seq,snapshot:prepared.next,
      });
    }
    function snapshot(sessionId=null){
      if(sessionId!==null)return records.get(sessionId)||null;
      return deepFreeze({sessions:Object.fromEntries([...records.entries()]),session_count:records.size,session_limit:TRANSACTIONAL_LIMITS.sessions,lifecycle_revision:lifecycleRevision});
    }
    function drop(sessionId){
      if(!SESSION_ID.test(sessionId||""))return failureResult("session_id_invalid");
      const dropped=records.delete(sessionId);lifecycleRevision++;
      return deepFreeze({ok:true,contract_version:"legacy_agent_poll_v1",partial:true,session_id:sessionId,dropped,lifecycle_revision:lifecycleRevision});
    }
    function clear(){
      const dropped=records.size;records.clear();lifecycleRevision++;
      return deepFreeze({ok:true,contract_version:"legacy_agent_poll_v1",partial:true,dropped,lifecycle_revision:lifecycleRevision});
    }
    return Object.freeze({stage,commit,snapshot,drop,clear});
  }

  global.AgentWorkbenchStore = Object.freeze({createTransactionalLegacyStore,transactionalLimits:TRANSACTIONAL_LIMITS});
})(window);
