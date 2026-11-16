import {actionNames,damageNames,reactionNames,elementNames,list,finite,copy,key,zh,clean,chinese,reactionName,firstWindow,projection,stateName,endReason} from "./shared";
import {componentSource,createDamageSourceReader} from "./damage-sources";
import {buffPresentation} from "./buff-presentation";
export type JsonRecord=Record<string,any>;
export interface TimelineInput {template?:JsonRecord|null;plan?:JsonRecord|null;result?:JsonRecord|null;executionModel?:JsonRecord|null;executionTemplate?:JsonRecord|null;current?:boolean;resultAvailability?:"not_loaded";describeEffect?:(event:TimelineEvent,context:{characterName:(id:any)=>string})=>JsonRecord;}
export type ViewName="actions"|"effects"|"damage";
export interface TimelineEvent extends JsonRecord {id:string;kind:string;start:number;end?:number;detail:JsonRecord;label:string;}
export interface TimelineView {events:TimelineEvent[];notes:string[];coverage?:JsonRecord;}
export type TimelineViews=Record<ViewName,TimelineView>;
export function createTimelineContext({template={},plan=null,result=null,executionModel=null,executionTemplate=null,current=false,resultAvailability,describeEffect}:TimelineInput={}) {
    const t = template?.manual_template || template || {};
    const r = current ? (result?.result || result) : null;
    const explanationDocument = r?.damage_explanations?.contract === "damage-explanation-v1"
      && r.damage_explanations.schema_version === 1 ? r.damage_explanations : null;
    const explanationFingerprint = explanationDocument ? r?.damage_explanation_fingerprint ?? null : null;
    const rotation = (r?.rotation_check?.resolved_rotation_model || plan?.rotation_model || {});
    const model = (current && executionModel
      ? executionModel.mechanics_model || executionModel : plan?.mechanics_model) || {};
    const actions = list(rotation.actions), abilities = new Map(list(rotation.abilities).map(a => [a.id, a]));
    const actionMap = new Map(actions.map((a, index) => [a.id, {a, index, ability:abilities.get(a.ability_id) || {}}]));
    const rules = new Map(list(model.rules).map(rule => [rule.id, rule]));
    const receipt = r?.buff_state_timeline?.contract === "buff-state-timeline-v1"
      && r.buff_state_timeline.version === 1 ? r.buff_state_timeline : null;
    const declaredBuffs=new Map([...list(t.buffs),...list(current&&executionTemplate?.buffs),
      ...list(receipt?.states).map(state=>state.buff).filter(Boolean)].map(buff=>[buff.id,buff]));
    const manualBuffPresentation=(id:any)=>buffPresentation(declaredBuffs.get(id));
    const manualBuffSource=(id:any)=>{
      const buff=declaredBuffs.get(id);if(!buff)return null;
      return {owner:buff.extensions?.owner??buff.target,source_id:buff.extensions?.source_id,
        source_name:chinese(buff.name,chinese(buff.source,"手工增益"))};
    };
    // The calculation receipt carries names and ownership even when an exported
    // result no longer contains the compiled execution model.
    for (const state of list(receipt?.states)) if (state.rule_id && !rules.has(state.rule_id)) rules.set(state.rule_id,
      {id:state.rule_id, owner:state.owner, name_zh:state.name_zh, trigger:state.trigger, status:state.evidence_status,
        source:state.source, source_id:state.source_id, effects:state.effects, beneficiaries:state.beneficiaries_scope,
        resource_gate:copy(state.resource_gate)});
    const poolModel=model.shared_pools||{}, pools=new Map(list(poolModel.pools).map(row=>[row.id,row]));
    const operations=new Map(list(poolModel.operations).map(row=>[row.id,row]));
    const consumers=new Map(list(poolModel.consumers).map(row=>[row.id,row]));
    for(const state of list(receipt?.states))if(state.resource_gate){
      const gate=state.resource_gate,pool=gate.pool;
      if(pool?.id&&!pools.has(pool.id))pools.set(pool.id,pool);
      if(gate.consumer_id&&!consumers.has(gate.consumer_id))consumers.set(gate.consumer_id,
        {id:gate.consumer_id,pool_id:gate.pool_id,actor_relation:gate.actor_relation,amount:gate.amount,
          filter:copy(gate.filter),source_id:gate.source_id,status:gate.status,eligibility_rule_id:state.rule_id});
    }
    const gateConsumerFor=(rule:any)=>rule?.resource_gate?.consumer_id?consumers.get(rule.resource_gate.consumer_id):null;
    const gateRecipients=(rule:any,recipients:any[])=>{
      const consumer=gateConsumerFor(rule);let accepted=list(recipients);
      if(consumer?.actor_relation==="owner")accepted=accepted.filter(id=>id===rule.owner);
      else if(consumer?.actor_relation==="other_frontline")accepted=accepted.filter(id=>id!==rule.owner);
      if(list(consumer?.filter?.characters).length)accepted=accepted.filter(id=>consumer.filter.characters.includes(id));
      return accepted;
    };
    const characters = list(t.characters).map((c, i) => ({id:c.id, label:chinese(c.name, `角色 ${i + 1}`)}));
    const characterName = (id:any) => characters.find(c => c.id === id)?.label || "全队";
    const readable = (value:unknown) => {
      let text = clean(value);
      for (const c of characters) {
        if (!c.id || c.id === c.label) continue;
        const escaped = String(c.id).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        text = text.replace(new RegExp(`(?<![A-Za-z0-9_])${escaped}(?![A-Za-z0-9_])`, "gu"), c.label);
      }
      return text.replace(/A4/gu, "突破四固有天赋").replace(/A1/gu, "突破一固有天赋");
    };
    const candidates = [...actions.map(a => a.occupation_end), ...list(t.hits).map(h => h.t),
      ...list(model.special_reaction_events).filter(event => finite(event.t)).map(event => event.t + 0.000001),
      ...list(t.buffs).filter(b => !projection(b)).map(b => b.end)].filter(finite);
    const reportWindow = r?.damage_report_window?.contract === "damage-report-window-v1" ? r.damage_report_window : null;
    const legacyDuration = finite(receipt?.duration) && receipt.duration > 0 ? receipt.duration
      : finite(rotation.period) && rotation.period > 0 ? rotation.period
      : finite(t.loop_duration) && t.loop_duration > 0 ? t.loop_duration : Math.max(10, ...candidates);
    const duration = reportWindow ? Math.max(legacyDuration, finite(rotation.period)?rotation.period:0,
      ...actions.map(a=>a.occupation_end).filter(finite), finite(reportWindow.end)?reportWindow.end:0) : legacyDuration;
    const views:TimelineViews = {actions:{events:[], notes:[]}, effects:{events:[], notes:[]}, damage:{events:[], notes:[]}};
    const within = (time:unknown) => finite(time) && time >= 0 && (reportWindow ? time <= duration : time < duration);
    const note = (view:ViewName, text:string) => { if (!views[view].notes.includes(text)) views[view].notes.push(text); };
    if(reportWindow && finite(reportWindow.end))note("damage",`DPS统计截止 ${reportWindow.end} 秒；其后动作和状态仅展示或验证，不计入该窗口总伤。`);
    const put = (view:ViewName, event:TimelineEvent) => {
      if (!within(event.start)) return;
      if(view==="damage" && reportWindow && finite(reportWindow.end) && event.start>reportWindow.end)return;
      if (finite(event.end)) event.end = Math.min(duration, Math.max(event.start, event.end));
      views[view].events.push(event);
    };
    const source = (owner:any, type:string, id:any, label:string) => ({sourceKey:key(owner ?? null, type, id),
      sourceLabel:`${characterName(owner)} · ${readable(label)}`});
    const actionLabel = (ability:any) => {
      const type = actionNames[ability.action_type_id] || "动作";
      const name = ability.name_zh || ability.name;
      return zh(name) ? clean(name) : [name && /^[A-Z][0-9]*$/u.test(name) ? name : null,
        type, zh(ability.variant) ? ability.variant : null].filter(Boolean).join(" · ");
    };
    const damageSources = createDamageSourceReader({result:r, executionTemplate:current ? executionTemplate : null,
      explanationDocument, actionMap, actionLabel});
    const hitLineage = damageSources.lineage;
    const hitLabel = (hit:any, descriptor?:any) => {
      const info = hitLineage(hit, descriptor);
      return info.special ? damageSources.label(hit, descriptor) : chinese(hit.hit_name, chinese(hit.skill,
        list(hit.damage_type_ids).map(id => damageNames[id]).find(Boolean) || "伤害命中"));
    };
    const hitSource = (hit:any, descriptor:any) => {
      const info = hitLineage(hit, descriptor), ability = actionMap.get(info.action_id)?.ability;
      const label = ability ? actionLabel(ability) : info.special ? "技能来源未保存" : chinese(info.source_skill || hit.skill,
        list(hit.damage_type_ids).map(id => damageNames[id]).find(Boolean) || "伤害技能");
      return {...source(info.character, "skill", info.ability_id || (info.special ? "unclassified" : hit.skill) || "unclassified", label),
        ...componentSource(info, info.character)};
    };
    const ruleSource = (rid:any, row:any) => {
      const rule = rules.get(rid) || {};
      return source(rule.owner ?? null, "rule", rid, chinese(rule.name_zh || row?.name_zh, "机制增益"));
    };
    const logs = list(r?.rotation_check?.event_log).filter(row => within(row.t));
    const executionLimit = Math.min(duration, ...logs.filter(row => ["failed", "not_verified"].includes(row.status)).map(row => row.t));
    const actionLogs = new Map(logs.filter(row => row.type === "action" && (row.source_cycle ?? 0) === 0).map(row => [row.action_id || row.id, row]));
    const cancelled = new Map(logs.filter(row => row.cancelled_action && row.status === "passed")
      .map(row => [row.cancelled_action.id, row.t]));
    if (!r) {
      const message = result ? "计算结果已失效；这里只显示当前输入，不使用旧数值或机制流水。" : resultAvailability==="not_loaded"
        ? "已有计算记录，当前尚未载入效果和伤害详情；这里只显示显式输入，不能据此判断某段伤害未执行。"
        : "尚未计算；这里只显示显式输入。自动档案的各段伤害需计算后查看，不代表动作已执行。";
      for (const view of Object.keys(views) as ViewName[]) note(view, message);
    }
    return {t,r,rotation,model,actions,abilities,actionMap,rules,receipt,reportWindow,manualBuffPresentation,manualBuffSource,poolModel,pools,operations,consumers,gateConsumerFor,gateRecipients,characters,characterName,readable,duration,views,within,note,put,source,actionLabel,hitLabel,hitSource,hitLineage,damageSources,explanationDocument,explanationFingerprint,ruleSource,logs,executionLimit,actionLogs,cancelled,template,plan,result,executionModel,executionTemplate,current,describeEffect};
}
export type TimelineContext=ReturnType<typeof createTimelineContext>;
