import {list,finite,copy,key,chinese} from "./shared";
import {type TimelineContext} from "./context";
export function projectResources(context:TimelineContext & {trace:any[];displayed:Map<any,any>;}) {
    const {t,r,rules,pools,operations,consumers,within,note,put,source,hitLabel,displayed}=context;
    // Shared-pool receipts are authoritative for finite stock.  The effect axis
    // keeps actual changes and eligible empty-pool attempts, while unrelated
    // actor checks and cooldown noise remain in the complete technical ledger.
    let poolEventCount=0;
    for(const [poolTraceIndex,row] of list(r?.shared_pools?.trace).entries()){
      if(!within(row.t)||(row.cycle??0)!==0)continue;
      const receiptKind=row.kind??row.type,isConsume=receiptKind==="consume",isOperation=receiptKind==="operation";
      if(!isConsume&&!isOperation)continue;
      const accepted=finite(row.accepted)?row.accepted:0;
      const eligible=row.eligibility_reason==="eligible"||row.qualified===true&&row.reason!=="ineligible";
      if(isConsume&&!accepted&&!(eligible&&row.reason==="insufficient"))continue;
      if(isOperation&&!row.succeeded&&!accepted&&!(eligible&&row.reason==="insufficient"))continue;
      const pool=pools.get(row.pool_id)||{id:row.pool_id,name_zh:"共享资源",owner:null};
      const consumer=isConsume?consumers.get(row.consumer_id):null;
      const operation=isOperation?operations.get(row.operation_id):null;
      let rule=consumer?.eligibility_rule_id?rules.get(consumer.eligibility_rule_id):null;
      if(!rule&&row.source_id){
        const matches=[...rules.values()].filter(candidate=>candidate.source_id===row.source_id);
        if(matches.length===1)rule=matches[0];
      }
      const poolName=chinese(pool.name_zh,"共享资源"),owner=rule?.owner??pool.owner??null;
      const change=`${row.before??"—"}→${row.after??"—"}`;
      const operationLabel=isConsume
        ? accepted>0?`消费 ${accepted}（${change}）`:`库存不足（${change}）`
        : row.operation==="grant"?`补充 ${accepted}（${change}）`
          : row.operation==="set"?`设置库存（${change}）`
            : row.operation==="spend"?row.reason==="waived"?`免消耗（${change}）`:
              row.reason==="insufficient"?`库存不足（${change}）`:`扣除 ${accepted}（${change}）`:`资源变化（${change}）`;
      const hit=displayed.get(row.event_id)?.hit;
      const detail={...copy(row),type:receiptKind,pool_trace_index:poolTraceIndex,pool:copy(pool),pool_name:poolName,
        ...(consumer?{consumer:copy(consumer)}:{}),...(operation?{operation_spec:copy(operation)}:{}),
        owner,rule_id:rule?.id,source_name:rule?.name_zh||poolName,source:rule?.source||pool.source,
        evidence_status:rule?.status||consumer?.status||operation?.status||pool.status,
        beneficiaries_scope:copy(rule?.beneficiaries),resource_gate:copy(rule?.resource_gate),
        effects:isConsume&&accepted>0?copy(rule?.effects||[]):[],
        ...(hit?{trigger_hit:{id:hit.id,name_zh:hitLabel(hit),character:hit.character,skill:hit.skill}}:{}),
        interpretation:isConsume?accepted>0
          ?"本条是共享库存实际消费回执；对应规则只有在该次消费成功并通过命中筛选时才进入伤害。"
          :"本条命中符合消费资格，但共享库存不足，因此对应收益没有进入该次伤害。"
          :"本条是共享库存实际设置、补充或扣除回执；它改变后续可用库存，本身不额外造成伤害。"};
      put("effects",{id:key("shared-pool",poolTraceIndex,row.id),kind:"resource",
        character:row.actor??owner,...source(owner,"shared_pool",pool.id,rule?.name_zh||poolName),
        label:`${poolName} · ${operationLabel}`,start:row.t,detail});
      poolEventCount++;
    }
    if(poolEventCount)note("effects","共享库存点显示实际设置、补充、扣除、消费、成功免消耗，以及符合资格但库存不足的记录；不合格检查和冷却内尝试保留在完整共享资源流水中。");

    for(const [index,row] of list(r?.special_reactions?.trace).entries()){
      if(row.type!=="stellar_resource_receipt"||row.trial?.contract!=="probabilistic-resource-v1"||!within(row.t))continue;
      const trial=row.trial,owner=row.owner_id??row.owner;
      const outcome=trial.executed===false?"库存不足，未执行":trial.mode==="expected"?`期望触发 ${trial.expected_count} 次`:trial.outcome===true?"按触发处理":"按未触发处理";
      put("effects",{id:key("stellar-resource-trial",index,row.event_id),kind:"resource",character:owner,
        ...source(owner,"stellar_resource_trial",row.area_id,"星超导资源判定"),
        label:`资源判定 · ${outcome}`,start:row.t,detail:{...copy(row),
          interpretation:trial.mode==="expected"?"按期望次数折算资源，离散收益默认累计后向下取整；不是随机实战保证。":"逐事件指定的概率情景；库存、实际消费与返还分开记录，不代表必定触发。"}});
    }

    for(const [index,row] of list(r?.mechanics_trace).entries()){
      if(row.type!=="expected_discrete_count"||!within(row.t))continue;
      const rule=rules.get(row.rule_id),owner=rule?.owner??null;
      put("effects",{id:key("expected-discrete",index,row.event_id),kind:"resource",character:owner,
        ...source(owner,"expected_discrete_count",row.rule_id,rule?.name_zh||"期望次数"),
        label:`累计期望 ${row.expected_count} → 应用 ${row.applied_count} 次`,start:row.t,detail:copy(row)});
    }

}
