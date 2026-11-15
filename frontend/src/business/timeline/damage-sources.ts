import {chinese,clean,copy,finite,firstWindow,key,list,reactionNames} from "./shared";

export const isDirectSpecial = (event:any) => ["direct_stellar_damage", "direct_lunar_damage"].includes(event?.kind);

/** Read actual references, never infer an ability/component from a custom ID. */
export function createDamageSourceReader({result,executionTemplate,explanationDocument,actionMap,actionLabel}:any) {
  const executionHits = new Map<any,any>(list(executionTemplate?.hits).map(hit => [hit.id, hit]));
  const receipts = [...list(result?.mechanics_trace), ...list(result?.special_reactions?.trace)];
  const visibleReceipts = receipts.filter(row => (row.cycle ?? 0) === 0);
  const requests = new Map<any,any>(visibleReceipts.filter(row => row.type === "special_reaction_damage_request")
    .map(row => [row.event_id, row]));
  const projections = new Map<any,any>(visibleReceipts.filter(row => row.type === "special_reaction_damage_projection")
    .map(row => [row.hit_id, row]));
  const explanationSource = (hit:any) => {
    const event = explanationDocument?.events?.[hit?.explanation_ref];
    return event ? explanationDocument?.sources?.[event.source_ref] : null;
  };
  const receiptIndex = new Map<any,any[]>(), hitIndex = new Map<any,Set<any>>();
  const linkHit = (requestId:any, hitId:any) => {
    if (!requestId || !hitId) return;
    if (!hitIndex.has(requestId)) hitIndex.set(requestId, new Set());
    hitIndex.get(requestId)!.add(hitId);
  };
  // Index once: dense periodic sequences must not rescan the full trace for
  // every declaration when the user opens a view or changes a filter.
  for (const row of visibleReceipts) {
    for (const ref of new Set([row.event_id, row.request_id, row.special_reaction_request_id].filter(Boolean))) {
      if (!receiptIndex.has(ref)) receiptIndex.set(ref, []);
      receiptIndex.get(ref)!.push(row);
    }
    if (row.type === "special_reaction_damage_projection") linkHit(row.request_id, row.hit_id);
    linkHit(row.special_reaction_request_id, row.event_id);
  }
  for (const [hitId, input] of executionHits) {
    linkHit(input.extensions?.elemental?.special_reaction_request_id, hitId);
    linkHit(input.extensions?.special_reaction?.request_id, hitId);
  }
  for (const hit of list(result?.hits)) linkHit(explanationSource(hit)?.source_event_id, hit.id);
  const lineage = (hit:any, descriptor:any = {}) => {
    // Manual/legacy hits intentionally have no descriptor. The shared
    // projection passes null explicitly, which a default argument cannot fill.
    descriptor = descriptor ?? {};
    const documented = explanationSource(hit) || {};
    const execution = executionHits.get(hit.id) || hit;
    const elemental = execution.extensions?.elemental || {};
    const special = execution.extensions?.special_reaction || {};
    const projection = projections.get(hit.id) || {};
    const request = requests.get(elemental.special_reaction_request_id || special.request_id
      || projection.request_id || descriptor.request_id) || {};
    const binding = request.ability_profile?.ability_binding || special.ability_profile?.ability_binding
      || descriptor.ability_profile?.ability_binding || {};
    const actionId = documented.action_id || descriptor.action_id || elemental.action_id || projection.action_id || request.action_id;
    const action = actionMap.get(actionId);
    const abilityId = documented.ability_id || binding.ability_id || action?.a?.ability_id;
    const componentId = documented.component_id || binding.component_id || special.component || request.component || descriptor.component_id;
    return {
      character:documented.character || hit.character || request.owner || binding.character_id || action?.ability?.character,
      action_id:actionId ?? null, ability_id:abilityId ?? null, component_id:componentId ?? null,
      source_event_id:documented.source_event_id || descriptor.source_event_id || null,
      request_id:elemental.special_reaction_request_id || special.request_id || projection.request_id || request.event_id || descriptor.request_id || null,
      reaction_type:special.reaction_type || request.reaction_type || descriptor.reaction_type || hit.reaction?.type || null,
      component_name_zh:documented.component_name_zh || descriptor.component_name_zh || null,
      ability_label:action?.ability ? actionLabel(action.ability) : null,
      source_skill:documented.skill || null,
      special:["stellar", "lunar"].includes(execution.damage_system || documented.damage_system) || isDirectSpecial(descriptor),
    };
  };
  const label = (hit:any, descriptor?:any) => {
    const info = lineage(hit, descriptor);
    const ordinary = chinese(hit.hit_name, chinese(hit.skill, "伤害命中"));
    // Generic engine captions classify the formula, not the parent skill.
    const generic = /能力直伤/u.test(clean(hit.hit_name));
    if (!info.special || !info.ability_label || !generic) return ordinary;
    const component = chinese(info.component_name_zh, "能力伤害分量");
    const category = reactionNames[info.reaction_type] || (hit.damage_system === "lunar" ? "月曜直伤" : "星烁直伤");
    return `${info.ability_label} · ${component}（${category}）`;
  };
  const terminalFor = (event:any) => {
    // Execution IDs have an explicit cycle prefix. Construct an exact alias for
    // the first displayed window; never split the ID to recover game meaning.
    const aliases = new Set([event.id, `0:${event.id}`]);
    const matched = [...new Set([...aliases].flatMap(alias => receiptIndex.get(alias) || []))];
    const projected = matched.filter(row => row.type === "special_reaction_damage_projection");
    const hitIds = new Set([...aliases].flatMap(alias => [...(hitIndex.get(alias) || [])]));
    const terminal = matched.find(row => ["missed", "rejected", "suppressed", "failed", "pending", "not_verified"].includes(row.status))
      || matched.find(row => ["missed", "miss", "rejected", "suppressed"].includes(row.outcome)) || projected[0] || null;
    return {hitIds, terminal};
  };
  return {lineage,label,terminalFor};
}

/** One explicit declaration is one preview, not an inferred periodic expansion. */
export function declaredSpecialHit(event:any, actionMap:Map<any,any>) {
  if (!isDirectSpecial(event) || typeof event.id !== "string" || !event.id || !finite(event.t)) return null;
  const action = actionMap.get(event.action_id), binding = event.ability_profile?.ability_binding;
  if (!action || !firstWindow(action.a) || !finite(action.a.start) || event.t < action.a.start) return null;
  const character = binding?.character_id || action.ability?.character;
  if (!character || (binding?.character_id && action.ability?.character !== binding.character_id)
    || (binding?.ability_id && action.a.ability_id !== binding.ability_id)) return null;
  const parameters = event.ability_profile?.parameters || {};
  const component = binding?.component_id || parameters.component;
  if (typeof component !== "string" || !component) return null;
  return {
    id:event.id, t:event.t, character, skill:action.a.ability_id,
    hit_name:chinese(event.name_zh || event.label || event.hit_name, "能力直伤"),
    damage_system:event.kind === "direct_stellar_damage" ? "stellar" : "lunar",
    damage_type_ids:["damage.reaction"],
    ...(typeof parameters.element === "string" ? {element:parameters.element} : {}),
  };
}

export function componentSource(info:any, owner:any) {
  if (!info.component_id) return {};
  const componentLabel = chinese(info.component_name_zh,
    `${info.ability_label || "技能"} · 分量（${clean(info.component_id)}）`);
  return {componentKey:key(owner, info.ability_id, info.component_id),
    componentLabel, componentId:info.component_id};
}

export const copyLineage = (info:any) => copy(Object.fromEntries(Object.entries(info)
  .filter(([name]) => !["special", "ability_label"].includes(name))));
