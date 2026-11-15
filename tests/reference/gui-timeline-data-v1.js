// Read-only timeline projections. These views do not infer game mechanics.
"use strict";

window.TD = (() => {
  const actionNames = {"action.elemental_skill":"元素战技", "action.elemental_burst":"元素爆发",
    "action.normal_attack":"普通攻击", "action.charged_attack":"重击", "action.plunging_attack":"下落攻击",
    "action.dash":"冲刺", "action.jump":"跳跃", "action.switch":"切换角色", "action.wait":"等待"};
  const damageNames = {"damage.elemental_skill":"元素战技", "damage.elemental_burst":"元素爆发",
    "damage.normal_attack":"普通攻击", "damage.charged_attack":"重击", "damage.plunging_attack":"下落攻击",
    "damage.reaction":"反应伤害"};
  const reactionNames = {amplify:"增幅反应", swirl:"扩散", vaporize:"蒸发", melt:"融化", overload:"超载", superconduct:"超导",
    electro_charged:"感电", frozen:"冻结", freeze:"冻结", shatter:"碎冰", burning:"燃烧", bloom:"绽放",
    hyperbloom:"超绽放", burgeon:"烈绽放", aggravate:"超激化", spread:"蔓激化", crystallize:"结晶",
    stellar_superconduct:"星超导", stellar_swirl:"星扩散", stellar_swirl_anemo:"星扩散·风",
    stellar_swirl_cryo_lv2:"星扩散·1级风旋爆发", stellar_swirl_cryo_lv3:"星扩散·2级风旋爆发",
    lunar_electro:"月感电", lunar_bloom:"月绽放", lunar_crystallize:"月结晶"};
  const elementNames = {pyro:"火", hydro:"水", anemo:"风", electro:"雷", dendro:"草", cryo:"冰", geo:"岩", physical:"物理"};
  const list = value => Array.isArray(value) ? value : [];
  const finite = value => typeof value === "number" && Number.isFinite(value);
  const copy = value => value == null ? value : JSON.parse(JSON.stringify(value));
  const key = (...parts) => JSON.stringify(parts);
  const zh = value => /[\u3400-\u9fff]/u.test(String(value || ""));
  const clean = value => String(value || "").replace(/^机制派生(?:收益)?[·：:\s]*/u, "");
  const chinese = (value, fallback) => zh(clean(value)) ? clean(value) : fallback;
  const reactionName = reaction => reaction?.type === "swirl" && elementNames[reaction.reaction_element]
    ? `${elementNames[reaction.reaction_element]}扩散`
    : reactionNames[reaction?.type] || "反应";
  const firstWindow = item => item?.repeat?.mode !== "cycles" || list(item.repeat.cycles).includes(0);
  const projection = buff => String(buff.id || "").startsWith("mechanics:")
    || buff.source === "声明式机制逐事件投影" || /^机制派生收益/u.test(buff.name || "");
  const stateName = status => ({active:"规则有效", exhausted:"次数耗尽", declared:"手工声明"}[status] || "已记录状态");
  const endReason = reason => ({refresh:"激活或刷新",layer_expiry:"部分层数到期",quota_change:"次数变化",
    natural_expiry:"自然到期",execution_limit:"执行检查中断",window_end:"到达轴末"}[reason] || "记录结束");

  function build({template = {}, plan = null, result = null, executionModel = null, executionTemplate = null, current = false} = {}) {
    const t = template?.manual_template || template || {};
    const r = current ? (result?.result || result) : null;
    const rotation = (r?.rotation_check?.resolved_rotation_model || plan?.rotation_model || {});
    const model = (current && executionModel
      ? executionModel.mechanics_model || executionModel : plan?.mechanics_model) || {};
    const actions = list(rotation.actions), abilities = new Map(list(rotation.abilities).map(a => [a.id, a]));
    const actionMap = new Map(actions.map((a, index) => [a.id, {a, index, ability:abilities.get(a.ability_id) || {}}]));
    const rules = new Map(list(model.rules).map(rule => [rule.id, rule]));
    const receipt = r?.buff_state_timeline?.contract === "buff-state-timeline-v1"
      && r.buff_state_timeline.version === 1 ? r.buff_state_timeline : null;
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
    const gateConsumerFor=rule=>rule?.resource_gate?.consumer_id?consumers.get(rule.resource_gate.consumer_id):null;
    const gateRecipients=(rule,recipients)=>{
      const consumer=gateConsumerFor(rule);let accepted=list(recipients);
      if(consumer?.actor_relation==="owner")accepted=accepted.filter(id=>id===rule.owner);
      else if(consumer?.actor_relation==="other_frontline")accepted=accepted.filter(id=>id!==rule.owner);
      if(list(consumer?.filter?.characters).length)accepted=accepted.filter(id=>consumer.filter.characters.includes(id));
      return accepted;
    };
    const characters = list(t.characters).map((c, i) => ({id:c.id, label:chinese(c.name, `角色 ${i + 1}`)}));
    const characterName = id => characters.find(c => c.id === id)?.label || "全队";
    const readable = value => {
      let text = clean(value);
      for (const c of characters) {
        if (!c.id || c.id === c.label) continue;
        const escaped = String(c.id).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        text = text.replace(new RegExp(`(?<![A-Za-z0-9_])${escaped}(?![A-Za-z0-9_])`, "gu"), c.label);
      }
      return text.replace(/A4/gu, "突破四固有天赋").replace(/A1/gu, "突破一固有天赋");
    };
    const candidates = [...actions.map(a => a.occupation_end), ...list(t.hits).map(h => h.t), ...list(t.buffs).filter(b => !projection(b)).map(b => b.end)].filter(finite);
    const duration = finite(receipt?.duration) && receipt.duration > 0 ? receipt.duration
      : finite(rotation.period) && rotation.period > 0 ? rotation.period
      : finite(t.loop_duration) && t.loop_duration > 0 ? t.loop_duration : Math.max(10, ...candidates);
    const views = {actions:{events:[], notes:[]}, effects:{events:[], notes:[]}, damage:{events:[], notes:[]}};
    const within = time => finite(time) && time >= 0 && time < duration;
    const note = (view, text) => { if (!views[view].notes.includes(text)) views[view].notes.push(text); };
    const put = (view, event) => {
      if (!within(event.start)) return;
      if (finite(event.end)) event.end = Math.min(duration, Math.max(event.start, event.end));
      views[view].events.push(event);
    };
    const source = (owner, type, id, label) => ({sourceKey:key(owner ?? null, type, id),
      sourceLabel:`${characterName(owner)} · ${readable(label)}`});
    const actionLabel = ability => {
      const type = actionNames[ability.action_type_id] || "动作";
      const name = ability.name_zh || ability.name;
      return zh(name) ? clean(name) : [name && /^[A-Z][0-9]*$/u.test(name) ? name : null,
        type, zh(ability.variant) ? ability.variant : null].filter(Boolean).join(" · ");
    };
    const hitLabel = hit => chinese(hit.hit_name, chinese(hit.skill,
      list(hit.damage_type_ids).map(id => damageNames[id]).find(Boolean) || "伤害命中"));
    const hitSource = (hit, descriptor) => {
      const ability = actionMap.get(descriptor?.action_id)?.ability;
      const label = chinese(hit.skill, ability ? actionLabel(ability)
        : list(hit.damage_type_ids).map(id => damageNames[id]).find(Boolean) || "伤害技能");
      return source(hit.character, "skill", hit.skill || ability?.id || "unclassified", label);
    };
    const ruleSource = (rid, row) => {
      const rule = rules.get(rid) || {};
      return source(rule.owner ?? null, "rule", rid, chinese(rule.name_zh || row?.name_zh, "机制增益"));
    };
    const logs = list(r?.rotation_check?.event_log).filter(row => within(row.t));
    const executionLimit = Math.min(duration, ...logs.filter(row => ["failed", "not_verified"].includes(row.status)).map(row => row.t));
    const actionLogs = new Map(logs.filter(row => row.type === "action" && (row.source_cycle ?? 0) === 0).map(row => [row.action_id || row.id, row]));
    const cancelled = new Map(logs.filter(row => row.cancelled_action && row.status === "passed")
      .map(row => [row.cancelled_action.id, row.t]));
    if (!r) {
      const message = result ? "计算结果已失效；这里只显示当前输入，不使用旧数值或机制流水。" : "尚未计算；这里只显示显式输入。自动档案的各段伤害需计算后查看，不代表动作已执行。";
      for (const view of Object.keys(views)) note(view, message);
    }
    for (const [index, a] of actions.entries()) {
      if (!firstWindow(a) || !within(a.start)) continue;
      const ability = abilities.get(a.ability_id) || {}, row = actionLogs.get(a.id);
      const isSwitch = ability.action_type_id === "action.switch";
      const label = actionLabel(ability);
      const detail = {action_id:a.id, ability_id:a.ability_id, input_status:a.status, source:a.source,
        input_order:a.order,
        execution_status:row?.status || (r ? "not_verified" : "not_calculated"), declared_occupation_end:a.occupation_end,
        execution:copy(row || null), switch_to:a.switch_to};
      let end = finite(row?.occupation_end) ? row.occupation_end : a.occupation_end;
      if (cancelled.has(a.id)) { end = Math.min(end, cancelled.get(a.id)); detail.cancelled_at = end; }
      put("actions", {id:key("action", a.id), kind:isSwitch ? "switch" : "action", character:ability.character ?? null,
        ...source(ability.character, "action", ability.id || a.ability_id, label),
        label:isSwitch ? `切换至${characterName(a.switch_to)}` : label, start:a.start,
        ...(finite(end) && !isSwitch ? {end} : {}), detail, edit:{kind:"action", index}});
    }
    if (!actions.length) {
      note("actions", "没有技能释放动作模型；命中时点不能替代施放动作。");
      list(t.switches).forEach((sw, index) => put("actions", {id:key("switch", index), kind:"switch", character:sw.character,
        ...source(sw.character, "action", "switch", "切换角色"), label:`切换至${characterName(sw.character)}`,
        start:sw.t, detail:{origin:"manual_switch"}, ...(!plan ? {edit:{kind:"switch", index}} : {})}));
    }

    // Pattern prototypes are materials: their placeholder t is never a hit.
    const rawHits = new Map(list(t.hits).map((hit, index) => [hit.id || key("legacy", index), {hit, index}]));
    const executionHits = new Map(list(current && executionTemplate?.hits).map(hit => [hit.id, hit]));
    const projected = new Map();
    const addHit = (hit, index, descriptor, derived) => {
      if (!within(hit.t)) return;
      projected.set(hit.id || key("legacy", index), {hit, index, descriptor, derived});
    };
    if (plan?.plan_version === 3 || list(model.hit_patterns).length || list(model.hit_events).length) {
      for (const descriptor of list(model.hit_events)) {
        const raw = rawHits.get(descriptor.hit_id), a = actionMap.get(descriptor.action_id)?.a;
        if (!raw || !a || !firstWindow(a) || !finite(a.start) || !finite(raw.hit.t) || raw.hit.t < a.start) continue;
        addHit(raw.hit, raw.index, descriptor, true);
      }
      for (const pattern of list(model.hit_patterns)) {
        const raw = rawHits.get(pattern.prototype_hit_id), a = actionMap.get(pattern.action_id)?.a;
        if (!raw || !a || !firstWindow(a) || !finite(a.start)) continue;
        list(pattern.offsets).forEach((offset, i) => {
          if (!finite(offset) || offset < 0) return;
          addHit({...raw.hit, id:`${pattern.id}:${i + 1}`, t:Math.round((a.start + offset) * 1e6) / 1e6, count:1},
            raw.index, pattern, true);
        });
      }
      if (!projected.size && rawHits.size) note("effects", "尚无可靠的动作命中映射；未把技能素材的占位时间显示为出伤时点。");
    } else {
      for (const raw of rawHits.values()) addHit(raw.hit, raw.index, null, false);
    }
    const trace = list(r?.mechanics_trace).map((row, index) => ({...row, trace_index:index})).filter(row => within(row.t));
    const tracesByHit = new Map(), samplesByHit = new Map();
    const collect = (map, row) => {
      const id = key(row.event_id, row.t);
      if (!map.has(id)) map.set(id, []);
      map.get(id).push(row);
    };
    trace.filter(row => ["effect_applied", "effect_rejected", "manual_effect_applied", "quota"].includes(row.type))
      .forEach(row => collect(tracesByHit, row));
    list(r?.sampling_trace).filter(row => within(row.t)).forEach(row => collect(samplesByHit, row));
    const calculated = new Map(list(r?.hits).map((hit, index) => [hit.id || key("legacy", index), hit]));
    const displayed = new Map(projected);
    for (const [id, hit] of calculated) {
      const existing = displayed.get(id);
      displayed.set(id, {...existing, hit:{...existing?.hit, ...hit}, index:existing?.index, derived:existing?.derived ?? true});
    }
    for (const row of trace.filter(row => ["hit","reaction_damage"].includes(row.type) && (row.cycle ?? 0) === 0)) {
      const existing = displayed.get(row.event_id);
      if (existing) {
        existing.trace = row;
        if(row.type==="reaction_damage"){
          existing.hit.element=row.reaction_element;
          existing.descriptor={...existing.descriptor,action_id:row.action_id};
        }
      }
    }
    for (const [id, entry] of displayed) {
      const {hit, descriptor} = entry, calc = calculated.get(id);
      if (r?.mechanics_trace && !calc && !entry.trace) continue;
      const label = hitLabel(hit), groups = hitSource(hit, descriptor);
      const detail = {hit_id:id, action_id:descriptor?.action_id, input:copy(projected.get(id)?.hit || null),
        trace_index:entry.trace?.trace_index,
        execution_status:entry.trace?.status || (calc ? "calculated" : "not_calculated"),
        reported_damage:entry.trace?.reported_damage ?? descriptor?.report ?? true, origin:entry.derived ? "derived" : "manual"};
      const parameters = {...executionHits.get(list(t.hits)[entry.index]?.id), ...hit};
      detail.hit_parameters = Object.fromEntries(["multiplier", "scaling_stat", "count", "damage_type_ids", "element"]
        .filter(name => parameters[name] != null).map(name => [name, copy(parameters[name])]));
      if(entry.trace?.type==="reaction_damage")detail.reaction=copy(entry.trace);
      const edit = !plan && !entry.derived && Number.isInteger(entry.index) ? {edit:{kind:"hit", index:entry.index}} : {};
      put("effects", {id:key("hit", id), kind:"hit", character:hit.character, ...groups, label, start:hit.t,
        element:hit.element, detail:copy(detail), ...edit});
      if (hit.reaction?.type && !r?.elemental) {
        put("effects", {id:key("reaction", id), kind:"reaction", character:hit.character, ...groups,
          label:`${reactionNames[hit.reaction.type] || "反应"}（输入声明）`, start:hit.t, element:hit.element,
          detail:{...copy(detail), reaction:copy(hit.reaction), interpretation:"输入者声明的反应；未模拟元素底、附着或附着冷却。"}});
        note("effects", "反应点来自已有反应标注，不代表引擎模拟了元素底或附着。");
      }
      if (detail.reported_damage === false && !calc) continue;
      const fixedValue = calc?.calculation_override;
      const fixedSuffix = fixedValue
        ? (fixedValue.precision?.kind === "approximate" ? "（受审近似值）" : "（受审固定值）") : "";
      put("damage", {id:key("damage", id), kind:"damage", character:hit.character, ...groups,
        label:calc ? `${label}${fixedSuffix}` : `${label}（待计算）`, start:hit.t, element:hit.element,
        ...(finite(calc?.expected) ? {value:calc.expected} : {}), detail:copy(detail), ...edit});
      if (calc) {
        const effectRows=tracesByHit.get(key(id, hit.t))||[];
        const appliedEffects=effectRows.filter(row=>["effect_applied","manual_effect_applied"].includes(row.type)).map(row=>{
          const rule=rules.get(row.rule_id),effect=list(rule?.effects).find(item=>item.id===row.effect_id);
          return {...copy(row),owner:rule?.owner,source_name:rule?.name_zh,
            evidence_status:rule?.status,benefit_type_id:effect?.benefit_type_id??row.benefit_type_id,
            declared_value:copy(effect?.value),domain:row.domain??effect?.domain};
        });
        put("damage", {id:key("calculation", id), kind:"calculation", character:hit.character, ...groups,
          label:`${label} · 计算明细`, start:hit.t, element:hit.element,
          detail:{hit_id:id, trace_index:entry.trace?.trace_index, hit:copy(calc), zones:copy(calc.zones || {}), matched_effect_refs:copy(calc.matched_effect_refs || []),
            ...(fixedValue ? {calculation_override:copy(fixedValue), interpretation:fixedValue.precision?.kind === "approximate"
              ? "该单事件使用有来源的受审近似固定值；角色、技能、总伤和DPS继承近似标记，状态后继仍由引擎事件决定。"
              : "该单事件使用有来源的受审固定值；汇总由逐事件账重新派生，状态后继仍由引擎事件决定。"} : {}),
            effects:copy(effectRows),applied_effects:appliedEffects,sampling:copy(samplesByHit.get(key(id, hit.t)) || [])}});
      }
    }

    // Simulated aura events include non-damage applications and ICD denials.
    // Special-state receipts live beside the elemental ledger in new engine
    // outputs.  Merge them here, while keeping compatibility with transitional
    // receipts that copied the same row into elemental.trace.
    const simulationTrace=list(r?.elemental?.trace),simulationTraceKeys=new Set(simulationTrace.map(row=>
      key(row.type,row.event_id,row.time_tick,row.t,row.operation,row.reaction_type,row.special_reaction_type)));
    for(const row of list(r?.special_reactions?.trace)){
      const traceKey=key(row.type,row.event_id,row.time_tick,row.t,row.operation,row.reaction_type,row.special_reaction_type);
      if(!simulationTraceKeys.has(traceKey)){simulationTrace.push(row);simulationTraceKeys.add(traceKey);}
    }
    // They are effects-layer facts, never additional damage events.
    for (const row of simulationTrace.filter(row => within(row.t))) {
      if(String(row.type||"").startsWith("special_reaction_")){
        const specialType=row.reaction_type||row.special_reaction_type,reactionLabel=reactionNames[specialType]||"星月反应";
        const operations={establish_or_refresh_field:"建立或刷新星超导领域",count_successful_application:"记录领域内冰／雷附着",
          settle_four_second_window:"结算四秒星超导窗口",expire_field:"星超导领域结束",expire_settlement_buff:"星超导结算效果结束",
          create_or_upgrade_vortex:"建立或升级星扩散风旋",create_vortex_from_packet:"建立星扩散风旋",
          advance_existing_vortex_from_packet_child:"推进星扩散风旋",remaining_packet_children_do_not_reseed:"星扩散剩余子反应不重复建立风旋",
          detonate_vortex:"星扩散风旋爆发",replace_pending_ticks:"替换月感电待结算跳数",
          extend_cloud_without_phase_reset:"延长月感电雷云（保留原跳数相位）",
          expire_area_thundercloud:"月感电雷云结束",
          schedule_aura_checked_ticks:"安排月感电实时底量检查",enqueue_party_dew_fill:"登记草露填充",
          periodic_dew_fill:"草露填充",consume_dew:"消费草露",create_moondrift_set:"生成月笼",advance_harmony:"推进月笼谐奏",
          resolve_full_cap_queue_boundary:"处理草露满额后的排队边界",
          expire_moondrift:"月笼到期",expire_moondrift_set:"月笼到期",generate_ordinary_dendro_core:"生成普通草原核"};
        const decisions={replace:"替换普通反应",ordinary:"保留普通反应",pending:"等待资料核实"};
        const layerSuffix=row.type==="special_reaction_damage_projection"&&specialType==="stellar_superconduct"&&Number.isInteger(row.settled_layers)
          ?`（${row.settled_layers}层，系数${finite(row.system_factor)?row.system_factor:"待核验"}）`:"";
        const label=row.type==="special_reaction_damage_projection"?`${reactionLabel}：能力直伤已投影${layerSuffix}`:
          operations[row.operation]||`${reactionLabel}${row.decision?`：${decisions[row.decision]||"受审决策"}`:"状态变化"}`;
        put("effects",{id:key("special-reaction",row.event_id,row.t,row.operation),kind:row.type==="special_reaction_decision"?"reaction":"condition",
          character:row.character||row.owner||null,...source(row.character||row.owner||null,"special-reaction",specialType,reactionLabel),
          label,start:row.t,element:row.element,
          detail:{...copy(row),interpretation:row.type==="special_reaction_damage_projection"&&specialType==="stellar_superconduct"
            ?"能力直伤已在事件时刻读取最近一次完成的四秒星超导结算层数；调用方不能手工提供层数或系数，实际伤害在伤害视图显示。"
            :"星月专用状态机的执行回执；伤害请求及实际伤害在伤害视图单独显示。"}});continue;
      }
      if(row.type==="crystallize_shard_pickup"){
        put("effects",{id:key("crystallize-pickup",row.event_id,row.shard_id),kind:"condition",character:row.character||null,
          ...source(row.character||null,"elemental-state",row.shard_id,"普通结晶晶片"),
          label:`拾取${({pyro:"火",hydro:"水",cryo:"冰",electro:"雷"})[row.element]||"元素"}晶片并刷新全队护盾`,
          start:row.t,element:row.element,
          detail:{...copy(row),interpretation:"真实晶片已被消费；新护盾替换全队当前普通结晶护盾，并继承造片时触发者等级与元素精通的吸收量取样。"}});continue;
      }
      if(row.type==="crystallize_shard_expired"||row.type==="crystallize_shield_expired"){
        const shard=row.type==="crystallize_shard_expired";
        put("effects",{id:key("crystallize-expiry",row.event_id,row.shard_id,row.t),kind:"condition",character:row.character||null,
          ...source(row.character||null,"elemental-state",row.target_id,"普通结晶状态"),
          label:shard?"结晶晶片消失":"全队结晶护盾结束",start:row.t,element:row.element,
          detail:{...copy(row),interpretation:shard?"晶片因存续时间或战场数量限制消失。":"当前全队普通结晶护盾到期。"}});continue;
      }
      if(row.type==="elemental_state_transition"){
        put("effects",{id:key("elemental-state",row.event_id,row.t,row.reason),kind:"condition",character:null,
          ...source(null,"elemental-state",row.target_id,"元素状态"),label:"元素底 / 冻结状态变化",start:row.t,
          detail:{...copy(row),interpretation:"参考模型的状态维护事件；不新增攻击、反应伤害或动作。"}});continue;
      }
      if(row.type==="ordinary_bloom_damage_admission"){
        put("effects",{id:key("ordinary-bloom-admission",row.event_id,row.t),kind:"condition",character:row.owner||null,
          ...source(row.owner||null,"reaction-damage","bloom","普通绽放伤害段"),
          label:row.allowed===true?"草原核爆炸伤害允许":"草原核爆炸受伤害段限制",start:row.t,element:"dendro",
          detail:{...copy(row),interpretation:"草原核已按时爆炸；同一生成者与目标的普通绽放伤害在0.5秒内最多计入两段，受限时不撤销核的消失。"}});continue;
      }
      if(row.type==="reaction_damage_attempt"){
        put("effects",{id:key("reaction-damage-check",row.event_id),kind:"condition",character:row.character,
          ...source(row.character,"reaction-damage",row.reaction_element,"扩散伤害冷却"),
          label:row.allowed===true?"扩散出伤允许":row.allowed===false?"扩散出伤受冷却限制":"扩散伤害规则待核实",start:row.t,element:row.reaction_element,
          detail:{...copy(row),interpretation:"伤害资格与反应成立分别计算；出伤受抑制不撤销扩散和耗底。"}});continue;
      }
      const names={no_icd:"允许附着",first_hit:"首次附着",icd_timer_reset:"附着计时重置",icd_hit_sequence:"命中序列附着",icd_blocked:"附着冷却阻止",missed:"未命中",no_application:"无附着"};
      if(Array.isArray(row.reactions)){
        put("effects",{id:key("elemental",row.event_id),kind:"condition",character:row.character,
          ...source(row.character,"elemental",row.profile_id,"附着与元素底"),label:`附着：${names[row.reason]||"参考模型判定"}`,start:row.t,element:row.element,
          detail:{...copy(row),interpretation:"含普通底、冻结量及历史；逐层消耗是诊断记录，不另计伤害。"}});
        for(const reaction of row.reactions)put("effects",{id:key("elemental-reaction",reaction.id),kind:"reaction",character:row.character,
          ...source(row.character,"elemental",row.profile_id,"附着与元素底"),label:`${reactionName(reaction)}（参考模型）`,start:row.t,element:reaction.reaction_element||row.element,
          detail:{...copy(reaction),package_id:row.package_id,parent_event_id:row.event_id,layer_consumptions:copy(row.layer_consumptions||[]),interpretation:"按模型公开事件聚合；伤害另看伤害视图，不按消费层数重复计数。"}});
        continue;
      }
      const label=row.reaction?`${reactionName(row.reaction)}（引擎模拟）`:`附着：${names[row.reason]||"待核实"}`;
      put("effects",{id:key("elemental",row.event_id),kind:row.reaction?"reaction":"condition",character:row.character,
        ...source(row.character,"elemental",row.profile_id,"附着与元素底"),label,start:row.t,element:row.element,
        detail:{...copy(row),interpretation:"根据明确档案与初态的模拟结果；前后元素底包含本次时刻的自然衰减。"}});
    }

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
      if(isOperation&&!row.succeeded&&!accepted)continue;
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
            : row.operation==="spend"?`扣除 ${accepted}（${change}）`:`资源变化（${change}）`;
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
    if(poolEventCount)note("effects","共享库存点只显示实际设置、补充、扣除、消费，以及符合资格但库存不足的记录；不合格检查和冷却内补充尝试保留在完整共享资源流水中。");

    // Ordinary manual windows remain explicitly declared input, never inferred uptime.
    list(receipt ? [] : r && executionTemplate ? executionTemplate.buffs : t.buffs).forEach((buff, index) => {
      if (projection(buff)) { note("effects", "逐命中的机制数值投影不作持续条；持续状态以本次计算流水为准。"); return; }
      if (!finite(buff.start) || !finite(buff.end) || buff.end <= buff.start) return;
      const owner = buff.extensions?.owner ?? buff.target ?? null;
      const label = chinese(buff.name, chinese(buff.source, "手工增益"));
      put("effects", {id:key("manual-buff", buff.id || index), kind:"coverage", character:buff.target ?? null,
        ...source(owner, "manual", buff.id || index, label), label:`${label}（手工声明）`, start:buff.start, end:buff.end,
        detail:{buff:copy(buff), interpretation:"手工声明的时间区间；选择器、前台限制与快照取样另见命中明细。"},
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
    const instances = new Map();
    for (const row of activations.filter(row => row.instance_id)) {
      if (!instances.has(row.instance_id)) instances.set(row.instance_id, []);
      instances.get(row.instance_id).push(row);
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
    const merged = [], tails = new Map();
    const stateSlice = event => ({start:event.start, end:event.end,
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
        let previous = null;
        for (const [index, slice] of list(state.slices).entries()) {
          if (!finite(slice.start) || !finite(slice.end) || slice.end <= slice.start
            || !["active", "exhausted"].includes(slice.status)) continue;
          const label = chinese(state.name_zh, "机制增益"), detail = {
            receipt_id:state.id, owner:state.owner, rule_id:state.rule_id, source_id:state.source_id, source_name:state.name_zh,
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
          }
          put("effects", previous);
        }
      }
      for (const message of list(receipt.notes)) if (typeof message === "string") note("effects", message);
    }
    views.effects.coverage = {
      origin:receipt ? "engine" : r ? "legacy" : result ? "stale" : "not_calculated",
      excluded:copy(list(receipt?.excluded)),
      message:!r ? result ? "计算结果已失效，请计算当前方案后查看持续 Buff。" : "尚未计算当前方案；计算后自动展开已触发的持续 Buff。"
        : views.effects.events.some(event => event.kind === "coverage")
          ? receipt ? "持续状态由引擎生成；灰色表示次数或共享库存已耗尽，不计作可用区间。" : "旧结果按明确的激活流水或手工时间区间显示；重新计算可获得完整状态。"
          : receipt ? "本次计算没有可绘制的有限持续 Buff；未显示的来源和原因见下方说明。"
            : "本次结果没有可靠的持续区间。逐命中数值和无独立时长的条件收益不转换为持续条。",
    };
    // Fixed external numbers are neither contacts nor effect triggers.
    for (const item of list(r?.external_damage?.items)) {
      put("damage", {id:`external:${item.id}`, kind:"external_damage", character:item.character,
        start:item.t, label:`外部计算${item.precision ? "（近似）" : ""} · ${item.label}`, value:item.expected,
        ...source(item.character, "external_damage", item.skill, `外部补充 · ${item.skill}`),
        detail:{...copy(item), interpretation:item.precision
          ? "外部近似计算值；精度传播到相关小计、总伤和DPS。不触发引擎增益、附着、能量或次数。改变输入后需重新复核。"
          : "外部独立计算值；不触发引擎增益、附着、能量或次数。改变输入后需重新复核。"}});
    }
    if (list(r?.external_damage?.items).length) note("damage", "外部计算值用独立标记显示；只参与合计，不是引擎命中或机制事件。");
    // Presentation context comes from exact rule/instance/action identities. Do
    // not decode custom IDs or treat a generic rule caption as its actual effect.
    const activationsByIndex = new Map(activations.map(row => [row.trace_index, row]));
    const elementalProfiles = new Map(list(model.elemental_model?.profiles).map(profile => [profile.id, profile]));
    const bindingsByRule = new Map();
    for (const binding of list(plan?.effect_bindings)) for (const part of list(binding.parts)) {
      const rid = part.rule?.kind === "mechanics" ? part.rule.rule_id : null;
      if (!rid) continue;
      if (!bindingsByRule.has(rid)) bindingsByRule.set(rid, []);
      bindingsByRule.get(rid).push({owner:copy(binding.owner), provider_character_id:binding.provider_character_id,
        label:part.label, source_refs:copy(binding.source_refs)});
    }
    const appliedByInstance = new Map();
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
          row.rule_id === detail.rule_id && finite(row.sample_t) && row.sample_t >= event.start && row.sample_t < event.end);
        detail.applied_value_count = values.length;
        detail.applied_values = values.slice(0,12).map(row => ({effect_id:row.effect_id, value:row.value,
          layers:row.layers, sample_t:row.sample_t, t:row.t, event_id:row.event_id,
          character:displayed.get(row.event_id)?.hit?.character}));
      }
      if (window.EFX) {
        detail.original_label = event.label;
        const description = window.EFX.describe(event, {characterName});
        event.label = description.title || event.label;
        if (["coverage", "activation", "quota"].includes(event.kind)) {
          const originalName = detail.source_name || detail.buff?.name;
          event.sourceLabel = `${characterName(detail.owner ?? event.character)} · ${event.label}`
            + (originalName && !event.label.includes(originalName) ? ` · ${readable(originalName)}` : "");
        }
      }
    }
    const order = event => event.detail.trace_index ?? event.detail.activation_trace_index ?? event.detail.input_order ?? Number.MAX_SAFE_INTEGER;
    // Stable sort retains insertion order when two items have the same evidence.
    for (const view of Object.values(views)) {
      for (const event of view.events) event.label = readable(event.label);
      view.events.sort((a, b) => a.start - b.start || order(a) - order(b));
    }
    return {duration, characters, views};
  }
  // Presentation only: equal windows for multiple recipients share one bar.
  // Different quota states, instances, refresh histories and gaps stay separate.
  function groupCoverage(events = [], characters = []) {
    const out = [], groups = new Map();
    for (const original of list(events)) {
      if (original.kind !== "coverage") { out.push(original); continue; }
      const comparison = {...original.detail}; delete comparison.receipt_id;
      const signature = key(original.sourceKey, original.start, original.end, comparison);
      let grouped = groups.get(signature);
      if (!grouped) {
        grouped = {...copy(original), character:original.detail?.owner ?? original.character,
          characters:[], detail:{...copy(original.detail), recipients:[]}};
        groups.set(signature, grouped); out.push(grouped);
      }
      if (!grouped.characters.includes(original.character)) grouped.characters.push(original.character);
      grouped.detail.recipients.push({character:original.character, event_id:original.id,
        detail:copy(original.detail)});
    }
    const names = new Map(list(characters).map(c => [c.id, c.label || c.name]));
    for (const event of groups.values()) event.beneficiary_names = event.characters.map(id =>
      chinese(names.get(id), id == null ? "按目标选择器" : "接收角色"));
    return out;
  }
  return {build, groupCoverage, stateName, endReason};
})();
