import {reactionNames,list,finite,copy,key,firstWindow} from "./shared";
import {type TimelineContext} from "./context";
import {copyLineage,declaredSpecialHit} from "./damage-sources";
export function projectHits(context:TimelineContext) {
    const {t,r,model,actionMap,rules,within,note,put,hitLabel,hitSource,hitLineage,damageSources,
      explanationFingerprint,plan,executionTemplate,current}=context;
    // Pattern prototypes are materials: their placeholder t is never a hit.
    const rawHits = new Map(list(t.hits).map((hit, index) => [hit.id || key("legacy", index), {hit, index}]));
    const executionHits = new Map(list(current && executionTemplate?.hits).map(hit => [hit.id, hit]));
    const projected = new Map<any,any>();
    const addHit = (hit:any, index:any, descriptor:any, derived:boolean) => {
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
    const tracesByHit = new Map(), samplesByHit = new Map<any,any>();
    const collect = (map:Map<any,any>, row:any) => {
      const id = key(row.event_id, row.t);
      if (!map.has(id)) map.set(id, []);
      map.get(id).push(row);
    };
    trace.filter(row => ["effect_applied", "effect_rejected", "manual_effect_applied", "quota"].includes(row.type))
      .forEach(row => collect(tracesByHit, row));
    list(r?.sampling_trace).filter(row => within(row.t)).forEach(row => collect(samplesByHit, row));
    const calculated = new Map(list(r?.hits).map((hit, index) => [hit.id || key("legacy", index), hit]));
    const declarationStates = new Map<any,any>();
    for (const event of list(model.special_reaction_events)) {
      const hit = declaredSpecialHit(event, actionMap);
      if (!hit || !within(hit.t)) continue;
      const evidence = r ? damageSources.terminalFor(event) : null;
      // Successful execution owns its actual hit identity. Never retain a
      // second declaration preview beside the calculated/projection receipt.
      if (evidence && [...evidence.hitIds].some(id => calculated.has(id))) continue;
      if (projected.has(hit.id)) {
        note("damage", "特殊伤害声明与已有命中重用同一身份；未重复预览，请核对输入。");
        continue;
      }
      const parameters = event.ability_profile?.parameters || {};
      const descriptor = {...event, source_event_id:event.id,
        component_id:event.ability_profile?.ability_binding?.component_id || parameters.component,
        reaction_type:parameters.reaction_type, report:true};
      addHit(hit, undefined, descriptor, true);
      declarationStates.set(hit.id, {preview:!r, terminal:evidence?.terminal ?? null});
    }
    // Descriptors can be amended by actual execution receipts below, including
    // non-reporting contacts absent from result.hits. Own each mutable entry
    // rather than changing a prototype borrowed from the input template.
    const displayed = new Map([...projected].map(([id,entry])=>[id,{...entry,hit:{...entry.hit}}]));
    for (const [id, hit] of calculated) {
      const existing = displayed.get(id);
      displayed.set(id, {...existing, hit:{...existing?.hit, ...hit}, index:existing?.index, derived:existing?.derived ?? true});
    }
    for (const row of trace.filter(row => ["hit","reaction_damage"].includes(row.type) && (row.cycle ?? 0) === 0)) {
      const existing = displayed.get(row.event_id);
      if (existing) {
        existing.trace = row;
        if(row.type==="reaction_damage"){
          if (row.reaction_element) existing.hit.element=row.reaction_element;
          existing.descriptor={...existing.descriptor,action_id:row.action_id ?? existing.descriptor?.action_id};
        }
      }
    }
    for (const [id, entry] of displayed) {
      const {hit, descriptor} = entry, calc = calculated.get(id);
      const declaration = declarationStates.get(id);
      if (r?.mechanics_trace && !calc && !entry.trace && !declaration) continue;
      const label = hitLabel(hit, descriptor), groups = hitSource(hit, descriptor), lineage = hitLineage(hit, descriptor);
      const executionStatus = declaration
        ? declaration.preview ? "not_calculated" : declaration.terminal?.status || declaration.terminal?.outcome || "not_verified"
        : entry.trace?.status || (calc ? "calculated" : "not_calculated");
      const detail:any = {hit_id:id, action_id:lineage.action_id, source_lineage:copyLineage(lineage), input:copy(projected.get(id)?.hit || null),
        trace_index:entry.trace?.trace_index,
        execution_status:executionStatus,
        ...(calc && explanationFingerprint ? {explanation_ref:calc.explanation_ref ?? null, explanation_fingerprint:explanationFingerprint} : {}),
        ...(declaration ? {declared_special:true, preview:declaration.preview, declaration_id:descriptor.id,
          execution:copy(declaration.terminal), interpretation:declaration.preview
            ? "显式特殊伤害声明，仅预览已给定时点和来源；尚未验证、未计算伤害或运行期层数，也不展开未知次数。"
            : "当前结果未提供该声明的计伤项；按实际回执显示状态，无回执时仅标未验证，不使用旧预览冒充成功。"} : {}),
        reported_damage:entry.trace?.reported_damage ?? descriptor?.report ?? true, origin:entry.derived ? "derived" : "manual"};
      const parameters = {...executionHits.get(list(t.hits)[entry.index]?.id), ...hit};
      detail.hit_parameters = Object.fromEntries(["multiplier", "scaling_stat", "count", "damage_type_ids", "element"]
        .filter(name => parameters[name] != null).map(name => [name, copy(parameters[name])]));
      if(entry.trace?.type==="reaction_damage")detail.reaction=copy(entry.trace);
      const edit = !plan && !entry.derived && Number.isInteger(entry.index) ? {edit:{kind:"hit", index:entry.index}} : {};
      const statusLabels:Record<string,string> = {not_verified:"未验证",missed:"未命中",miss:"未命中",rejected:"未接纳",
        suppressed:"已抑制",failed:"执行失败",pending:"待核实",executed:"未计入伤害"};
      const displayLabel = declaration ? `${label}（${declaration.preview ? "待计算" : statusLabels[executionStatus] || "未计入伤害"}）` : label;
      put("effects", {id:key("hit", id), kind:"hit", character:hit.character, ...groups, label:displayLabel, start:hit.t,
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
        label:calc ? `${label}${fixedSuffix}` : declaration ? displayLabel : `${label}（待计算）`, start:hit.t, element:hit.element,
        ...(finite(calc?.expected) ? {value:calc.expected} : {}), detail:copy(detail), ...edit});
      if (calc) {
        const effectRows:any[]=tracesByHit.get(key(id, hit.t))||[];
        const refs=calc.matched_effect_refs;
        const knownConsumption=Array.isArray(refs)&&refs.every(ref=>ref&&typeof ref.buff_id==="string"&&!!ref.buff_id
          &&typeof ref.effect_id==="string"&&!!ref.effect_id);
        const matchedIds=new Map<string,number>();
        if(knownConsumption)for(const ref of refs)matchedIds.set(ref.effect_id,(matchedIds.get(ref.effect_id)||0)+1);
        const materializedEffects=effectRows.filter(row=>["effect_applied","manual_effect_applied"].includes(row.type)).map(row=>{
          const rule=rules.get(row.rule_id),effect=list(rule?.effects).find(item=>item.id===row.effect_id);
          const presentation=context.manualBuffPresentation(row.buff_id);
          const manualSource=row.type==="manual_effect_applied"?context.manualBuffSource(row.buff_id):null;
          // A runtime application materializes a candidate; only the final
          // formula's exact compiled effect identity proves consumption.
          const projected=row.projected_effect_id,matched=matchedIds.get(projected)||0;
          const consumption=knownConsumption&&typeof projected==="string"&&!!projected&&matched<2
            ? matched===1?"consumed":"not_consumed":"unknown";
          return {...copy(row),owner:rule?.owner??row.owner??manualSource?.owner,
            source_name:row.source_name||rule?.name_zh||manualSource?.source_name,
            ...(row.source_id||rule?.source_id||manualSource?.source_id?
              {source_id:row.source_id||rule?.source_id||manualSource?.source_id}:{}),
            ...(presentation?{presentation}:{}),
            consumption_status:consumption,
            evidence_status:rule?.status,benefit_type_id:effect?.benefit_type_id??row.benefit_type_id,
            declared_value:copy(effect?.value),domain:row.domain??effect?.domain};
        });
        const appliedEffects=materializedEffects.filter(row=>row.consumption_status==="consumed");
        const otherMaterialized=materializedEffects.filter(row=>row.consumption_status!=="consumed");
        put("damage", {id:key("calculation", id), kind:"calculation", character:hit.character, ...groups,
          label:`${label} · 计算明细`, start:hit.t, element:hit.element,
          detail:{hit_id:id, action_id:lineage.action_id, source_lineage:copyLineage(lineage),
            ...(explanationFingerprint ? {explanation_ref:calc.explanation_ref ?? null, explanation_fingerprint:explanationFingerprint} : {}),
            trace_index:entry.trace?.trace_index, hit:copy(calc), zones:copy(calc.zones || {}), matched_effect_refs:copy(calc.matched_effect_refs || []),
            ...(fixedValue ? {calculation_override:copy(fixedValue), interpretation:fixedValue.precision?.kind === "approximate"
              ? "该单事件使用有来源的受审近似固定值；角色、技能、总伤和DPS继承近似标记，状态后继仍由引擎事件决定。"
              : "该单事件使用有来源的受审固定值；汇总由逐事件账重新派生，状态后继仍由引擎事件决定。"} : {}),
            effects:copy(effectRows),applied_effects:appliedEffects,
            ...(otherMaterialized.length?{materialized_effects:otherMaterialized}:{}),
            sampling:copy(samplesByHit.get(key(id, hit.t)) || [])}});
      }
    }

    return {trace,displayed};
}
