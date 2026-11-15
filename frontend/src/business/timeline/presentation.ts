import {list,finite,copy} from "./shared";
import {type TimelineContext} from "./context";
export function enrichTimeline(context:TimelineContext & {trace:any[];displayed:Map<any,any>;activations:any[];}) {
    const {t,r,model,actionMap,rules,characterName,readable,views,note,put,source,actionLabel,plan,describeEffect,trace,displayed,activations}=context;
    // Fixed external numbers are neither contacts nor effect triggers.
    for (const item of list(r?.external_damage?.items)) {
      put("damage", {id:`external:${item.id}`, kind:"external_damage", character:item.character,
        start:item.t, label:`外部计算${item.precision ? "（近似）" : ""} · ${item.label}`, value:item.expected,
        ...source(item.character, "external_damage", item.skill, `外部补充 · ${item.skill}`),
        detail:{...copy(item), explanation_ref:context.explanationDocument?.events?.[`external:${item.id}`]?`external:${item.id}`:null,
          explanation_fingerprint:context.explanationFingerprint, interpretation:item.precision
          ? "外部近似计算值；精度传播到相关小计、总伤和DPS。不触发引擎增益、附着、能量或次数。改变输入后需重新复核。"
          : "外部独立计算值；不触发引擎增益、附着、能量或次数。改变输入后需重新复核。"}});
    }
    if (list(r?.external_damage?.items).length) note("damage", "外部计算值用独立标记显示；只参与合计，不是引擎命中或机制事件。");
    // Presentation context comes from exact rule/instance/action identities. Do
    // not decode custom IDs or treat a generic rule caption as its actual effect.
    const activationsByIndex = new Map(activations.map(row => [row.trace_index, row]));
    const elementalProfiles = new Map(list(model.elemental_model?.profiles).map(profile => [profile.id, profile]));
    const bindingsByRule = new Map<any,any>();
    for (const binding of list(plan?.effect_bindings)) for (const part of list(binding.parts)) {
      const rid = part.rule?.kind === "mechanics" ? part.rule.rule_id : null;
      if (!rid) continue;
      if (!bindingsByRule.has(rid)) bindingsByRule.set(rid, []);
      bindingsByRule.get(rid).push({owner:copy(binding.owner), provider_character_id:binding.provider_character_id,
        label:part.label, source_refs:copy(binding.source_refs)});
    }
    const appliedByInstance = new Map<any,any>();
    for (const row of trace.filter(row => row.type === "effect_applied" && row.instance_id)) {
      if (!appliedByInstance.has(row.instance_id)) appliedByInstance.set(row.instance_id, []);
      appliedByInstance.get(row.instance_id).push(row);
    }
    for (const event of views.effects.events) {
      const detail = event.detail, rule = rules.get(detail.rule_id);
      const profile = elementalProfiles.get(detail.profile_id);
      if (profile) {
        detail.application_profile = copy(profile);
        detail.source ??= profile.source;
        detail.evidence_status ??= profile.status;
      }
      if (rule) {
        detail.rule = copy(rule);
        detail.source_name = rule.name_zh;
        detail.effects ??= copy(rule.effects);
        detail.source ??= rule.source;
        detail.trigger ??= copy(rule.trigger);
        detail.evidence_status ??= rule.status;
        detail.beneficiaries_scope ??= copy(rule.beneficiaries);
        if (bindingsByRule.has(detail.rule_id)) detail.source_bindings = copy(bindingsByRule.get(detail.rule_id));
      }
      const activation = activationsByIndex.get(detail.activation_trace_index);
      const eventId = detail.action_id || activation?.event_id || detail.event_id;
      const actionId = actionMap.has(eventId) ? eventId : displayed.get(eventId)?.descriptor?.action_id
        || displayed.get(eventId)?.trace?.action_id;
      const action = actionMap.get(actionId);
      if (action) detail.trigger_action = {id:action.a.id, name_zh:actionLabel(action.ability),
        character:action.ability.character, action_type_id:action.ability.action_type_id, start:action.a.start};
      if (event.kind === "coverage" && detail.instance_id) {
        const values = list(appliedByInstance.get(detail.instance_id)).filter(row =>
          row.rule_id === detail.rule_id && finite(row.sample_t) && row.sample_t >= event.start && row.sample_t < event.end!);
        detail.applied_value_count = values.length;
        detail.applied_values = values.slice(0,12).map(row => ({effect_id:row.effect_id, value:row.value,
          layers:row.layers, sample_t:row.sample_t, t:row.t, event_id:row.event_id,
          character:displayed.get(row.event_id)?.hit?.character}));
      }
      if (describeEffect && !detail.special_state_contract && event.kind!=="calculation_sampling") {
        detail.original_label = event.label;
        const description = describeEffect(event, {characterName});
        event.label = description.title || event.label;
        if (["coverage", "activation", "quota"].includes(event.kind)) {
          const originalName = detail.source_name || detail.buff?.name;
          event.sourceLabel = `${characterName(detail.owner ?? event.character)} · ${event.label}`
            + (originalName && !event.label.includes(originalName) ? ` · ${readable(originalName)}` : "");
        }
      }
    }
}
