import {list,finite,copy,key,chinese,projection,stateName} from "./shared";
import {type TimelineContext} from "./context";
import {samplingCoverage} from "./buff-presentation";
export function projectCoverage(context:TimelineContext & {trace:any[];displayed:Map<any,any>;}) {
    const {t,r,model,rules,receipt,pools,consumers,gateRecipients,characters,duration,views,note,put,source,ruleSource,executionLimit,result,executionTemplate,trace}=context;
    // Ordinary manual windows remain explicitly declared input, never inferred uptime.
    list(receipt ? [] : r && executionTemplate ? executionTemplate.buffs : t.buffs).forEach((buff, index) => {
      if (projection(buff)) { note("effects", "逐命中的机制数值投影不作持续条；持续状态以本次计算流水为准。"); return; }
      if (!finite(buff.start) || !finite(buff.end) || buff.end <= buff.start) return;
      const owner = buff.extensions?.owner ?? buff.target ?? null;
      const label = chinese(buff.name, chinese(buff.source, "手工增益"));
      const sampling=samplingCoverage(buff);
      put("effects", {id:key("manual-buff", buff.id || index), kind:sampling?.kind||"coverage", character:buff.target ?? null,
        ...source(owner, "manual", buff.id || index, label), label:`${label}（手工声明）`, start:buff.start, end:buff.end,
        detail:{buff:copy(buff),buff_id:buff.id,...(sampling?{presentation:sampling.presentation}:{}),
          interpretation:sampling?.interpretation||"手工声明的时间区间；选择器、前台限制与快照取样另见命中明细。"},
        ...(sampling?{label:`${label} · ${sampling.label}`} : {}),
        ...(!executionTemplate || executionTemplate === t ? {edit:{kind:"buff", index}} : {})});
    });
    const initial = r ? list(model.initial_state?.rules).flatMap((state, ri) => list(state.instances).map((inst, ii) => ({
      type:"activation", rule_id:state.rule_id, instance_id:`${state.rule_id}:initial:${ii}`,
      t:0, trace_index:-(ri * 10000 + ii + 1), origin:"initial_state", beneficiaries:copy(inst.beneficiaries),
      expires_at:copy(inst.remaining), quota:copy(inst.quota || {}), layers:list(inst.remaining).length,
    }))) : [];
    const activations = [...initial, ...trace.filter(row => row.type === "activation")];
    for (const row of trace.filter(row => row.type === "trigger_rejected"
      && row.reason === "no_eligible_beneficiary" && row.beneficiary_selection)) {
      const rule = rules.get(row.rule_id) || {};
      put("effects", {id:key("beneficiary-rejected", row.trace_index), kind:"condition", character:rule.owner ?? null,
        ...ruleSource(row.rule_id, row), label:`${chinese(rule.name_zh, "机制增益")} · 无合格接收者，未激活`,
        start:row.t, detail:{...copy(row), owner:rule.owner, beneficiaries:[],
          interpretation:"本次触发没有合格接收者；不生成持续条，也不刷新或清除既有残留。"}});
    }
    for (const row of activations) {
      const rule = rules.get(row.rule_id) || {};
      put("effects", {id:key("activation", row.trace_index), kind:"activation", character:rule.owner ?? null,
        ...ruleSource(row.rule_id, row), label:`${chinese(rule.name_zh || row.name_zh, "机制增益")} · ${row.origin === "initial_state" ? "初始已激活" : "激活或刷新"}`,
        start:row.t, detail:{...copy(row), owner:rule.owner, trigger:copy(rule.trigger), input_status:rule.status}});
      if (!row.instance_id) note("effects", "旧流水缺少机制实例标识：只展示激活点，不猜测持续区间。");
    }
    for (const row of trace.filter(row => row.type === "quota")) {
      const rule = rules.get(row.rule_id) || {};
      put("effects", {id:key("quota", row.trace_index), kind:"quota", character:row.pool === "team" ? null : row.pool,
        ...ruleSource(row.rule_id, row), label:`${chinese(rules.get(row.rule_id)?.name_zh, "机制增益")} · ${row.consumed ? "次数消耗" : "次数不足"} ${row.before}→${row.after}`,
        start:row.t, detail:{...copy(row), owner:rule.owner, interpretation:"本条为次数消费回执；是否实际受益另见命中计算。"}});
    }
    const instances = new Map<string,any[]>();
    for (const row of activations.filter(row => row.instance_id)) {
      if (!instances.has(row.instance_id)) instances.set(row.instance_id, []);
      instances.get(row.instance_id)!.push(row);
    }
    for (const [instance, rows] of receipt ? [] : instances) {
      rows.sort((a, b) => a.t - b.t || a.trace_index - b.trace_index);
      rows.forEach((activation, ai) => {
        const rule = rules.get(activation.rule_id) || {};
        const expires = list(activation.expires_at).filter(finite), next = rows[ai + 1];
        if (!expires.length) return;
        const naturalEnd = Math.max(...expires);
        const expiry = trace.filter(row => row.type === "expiry" && row.instance_id === instance
          && row.layers_after === 0 && row.t >= activation.t && row.trace_index > activation.trace_index);
        const end = Math.min(naturalEnd, next?.t ?? duration, duration, executionLimit, ...expiry.map(row => row.t));
        if (end <= activation.t) return;
        for (const recipient of gateRecipients(rule,activation.beneficiaries)) {
          const quota = activation.quota || {}, pool = Object.hasOwn(quota, "team") ? "team" : recipient;
          const limited = Object.hasOwn(quota, pool);
          const changes = trace.filter(row => row.type === "quota" && row.instance_id === instance && row.pool === pool
            && row.trace_index > activation.trace_index && (!next || row.trace_index < next.trace_index)
            && row.t >= activation.t && row.t < end).sort((a, b) => a.t - b.t || a.trace_index - b.trace_index);
          const cuts = [...new Set([activation.t, end, ...expires.filter(t => t > activation.t && t < end),
            ...changes.map(row => row.t)])].sort((a, b) => a - b);
          for (let i = 0; i < cuts.length - 1; i++) {
            const start = cuts[i], stop = cuts[i + 1];
            const latest = changes.filter(row => row.t <= start).at(-1);
            const remaining = limited ? latest?.after ?? quota[pool] : null;
            if (stop <= start || (limited && remaining <= 0)) continue;
            const name = chinese(rule.name_zh || activation.name_zh, "机制增益");
            put("effects", {id:key("coverage", instance, activation.trace_index, recipient, i), kind:"coverage", character:recipient,
              ...ruleSource(activation.rule_id, activation), label:`${name} · ${limited ? "次数可用" : "规则有效"}`,
              start, end:stop, detail:{owner:rule.owner, rule_id:activation.rule_id, instance_id:instance,
                activation_trace_index:activation.trace_index, rule_expires_at:naturalEnd, remaining_quota:remaining,
                layers:expires.filter(t => t > start).length, beneficiaries_scope:copy(rule.beneficiaries),
                ...(activation.beneficiary_selection ? {beneficiary_selection:copy(activation.beneficiary_selection)} : {}),
                filters:list(rule.effects).filter(e => e.filter).map(e => ({effect_id:e.id, filter:copy(e.filter)})),
                interpretation:"声明模型的激活状态；前台、命中类型、外部条件和快照仍须另验，非每击必定受益。"}});
          }
        }
      });
    }
    if (initial.length) note("effects", "起点活跃实例来自本次执行模型明确给出的初态；不推断前一轮触发过程。");
    if (trace.length) note("effects", "区间为声明规则执行状态；实际命中收益、拒绝原因与快照取值见逐击计算明细。");
    if (executionLimit < duration) note("effects", "执行在输入窗口内中断，持续条仅展示中断前已验证部分。");
    // A refresh or a 5→4 receipt changes state, not continuous availability.
    // Keep these state changes in details and their own point events instead of
    // creating dozens of visually separate bars for one uninterrupted instance.
    const merged:any[] = [], tails = new Map<any,any>();
    const stateSlice = (event:any) => ({start:event.start, end:event.end,
      remaining_quota:event.detail.remaining_quota, layers:event.detail.layers,
      resource_balance:copy(event.detail.resource_balance),
      rule_expires_at:event.detail.rule_expires_at, activation_trace_index:event.detail.activation_trace_index,
      ...(event.detail.beneficiary_selection ? {beneficiary_selection:copy(event.detail.beneficiary_selection)} : {})});
    for (const event of views.effects.events) {
      if (event.kind !== "coverage" || !event.detail.instance_id) { merged.push(event); continue; }
      const group = key(event.character, event.detail.instance_id), previous = tails.get(group);
      if (previous && previous.end === event.start) {
        previous.detail.states.push(stateSlice(event));
        previous.end = event.end;
        previous.detail.rule_expires_at = event.detail.rule_expires_at;
        if (previous.detail.remaining_quota !== event.detail.remaining_quota) previous.detail.remaining_quota = null;
        if(JSON.stringify(previous.detail.resource_balance)!==JSON.stringify(event.detail.resource_balance))previous.detail.resource_balance=null;
        if (previous.detail.layers !== event.detail.layers) previous.detail.layers = null;
      } else {
        event.detail.states = [stateSlice(event)];
        merged.push(event); tails.set(group, event);
      }
    }
    views.effects.events = merged;
    if (receipt) {
      for (const state of list(receipt.states)) {
        const stateRule=rules.get(state.rule_id),gateRef=state.resource_gate||stateRule?.resource_gate;
        const gateConsumer=gateRef?.consumer_id?consumers.get(gateRef.consumer_id):null;
        if(gateConsumer?.actor_relation==="owner"&&state.beneficiary!==state.owner)continue;
        if(gateConsumer?.actor_relation==="other_frontline"&&state.beneficiary===state.owner)continue;
        if(list(gateConsumer?.filter?.characters).length&&!gateConsumer.filter.characters.includes(state.beneficiary))continue;
        const gatePool=gateConsumer? pools.get(gateConsumer.pool_id):null;
        const resourceGate=(state.resource_gate||gateConsumer)?{
          consumer_id:gateConsumer?.id??gateRef?.consumer_id,pool_id:gateConsumer?.pool_id??gateRef?.pool_id,
          actor_relation:gateConsumer?.actor_relation??gateRef?.actor_relation,amount:gateConsumer?.amount??gateRef?.amount,
          filter:copy(gateConsumer?.filter??gateRef?.filter),status:gateConsumer?.status??gateRef?.status,
          source_id:gateConsumer?.source_id??gateRef?.source_id,pool:copy(gatePool??gateRef?.pool),
          receipt_field:"shared_pools.trace"}:null;
        let previous:any = null;
        for (const [index, slice] of list(state.slices).entries()) {
          if (!finite(slice.start) || !finite(slice.end) || slice.end <= slice.start
            || !["active", "exhausted"].includes(slice.status)) continue;
          const label = chinese(state.name_zh, "机制增益"), detail = {
            receipt_id:state.id, buff_id:state.buff_id, owner:state.owner, rule_id:state.rule_id, source_id:state.source_id, source_name:state.name_zh,
            instance_id:state.instance_id, source:state.source, origin:state.origin, evidence_status:state.evidence_status,
            state_status:slice.status, beneficiaries_scope:copy(state.beneficiaries_scope),
            filters:copy(state.filters), effects:copy(state.effects), buff:copy(state.buff),
            resource_gate:copy(resourceGate),
            effect_applicability:copy(state.effect_applicability), trigger:copy(state.trigger), remaining_quota:slice.remaining_quota,
            resource_balance:copy(slice.resource_balance),
            layers:slice.layers, quota_pool:slice.quota_pool, rule_expires_at:slice.rule_expires_at,
            activation_trace_index:slice.activation_trace_index, end_reason:slice.end_reason,
            states:[copy(slice)], interpretation:"引擎记录的有限持续状态；规则有效不代表每次命中都受益，仍需检查命中类型、前台、条件与快照。",
            ...(slice.beneficiary_selection ? {beneficiary_selection:copy(slice.beneficiary_selection)} : {}),
          };
          if (previous && previous.end === slice.start && previous.detail.state_status === slice.status) {
            previous.end = Math.min(duration, slice.end); previous.detail.states.push(copy(slice));
            previous.detail.end_reason = slice.end_reason; previous.detail.rule_expires_at = slice.rule_expires_at;
            if (previous.detail.remaining_quota !== slice.remaining_quota) previous.detail.remaining_quota = null;
            if(JSON.stringify(previous.detail.resource_balance)!==JSON.stringify(slice.resource_balance))previous.detail.resource_balance=null;
            if (previous.detail.layers !== slice.layers) previous.detail.layers = null;
            continue;
          }
          previous = {id:key("buff-state", state.id, index), kind:"coverage", character:state.beneficiary ?? null,
            ...source(state.owner, "rule", state.rule_id, label), label:`${label} · ${stateName(slice.status)}`,
            start:slice.start, end:slice.end, detail};
          if(state.origin === "declared_interval") {
            Object.assign(previous, source(state.owner, "declared", state.buff_id || state.source_id || state.id, label));
            previous.label=`${label} · 手工声明`;
            previous.detail.interpretation="明确声明的有限持续区间；不代表引擎验证了触发，命中能否受益仍需检查目标、前台与选择器。";
            const sampling=samplingCoverage(state.buff);
            if(sampling){previous.kind=sampling.kind;previous.label=`${label} · ${sampling.label}`;
              previous.detail.presentation=sampling.presentation;previous.detail.buff_id=state.buff_id;
              previous.detail.interpretation=sampling.interpretation;}
          }
          put("effects", previous);
        }
      }
      for (const message of list(receipt.notes)) if (typeof message === "string") note("effects", message);
    }
    const samplingCount=new Set(views.effects.events.filter(event=>event.kind==="calculation_sampling")
      .map(event=>event.detail.buff_id||event.id)).size;
    views.effects.coverage = {
      ...(samplingCount?{sampling_count:samplingCount}:{}),
      origin:receipt ? "engine" : r ? "legacy" : result ? "stale" : "not_calculated",
      excluded:copy(list(receipt?.excluded)),
      message:!r ? result ? "计算结果已失效，请计算当前方案后查看持续 Buff。" : "尚未计算当前方案；计算后自动展开已触发的持续 Buff。"
        : views.effects.events.some(event => event.kind === "coverage")
          ? receipt ? "持续状态由引擎生成；灰色表示次数或共享库存已耗尽，不计作可用区间。" : "旧结果按明确的激活流水或手工时间区间显示；重新计算可获得完整状态。"
          : receipt ? "本次计算没有可绘制的有限持续 Buff；未显示的来源和原因见下方说明。"
            : "本次结果没有可靠的持续区间。逐命中数值和无独立时长的条件收益不转换为持续条。",
    };
    return {activations};
}
