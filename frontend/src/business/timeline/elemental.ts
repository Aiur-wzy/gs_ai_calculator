import {reactionNames,list,finite,copy,key,reactionName} from "./shared";
import {type TimelineContext} from "./context";
export function projectElemental(context:TimelineContext) {
    const {t,r,operations,within,put,source}=context;
    // Simulated aura events include non-damage applications and ICD denials.
    // Special-state receipts live beside the elemental ledger in new engine
    // outputs.  Merge them here, while keeping compatibility with transitional
    // receipts that copied the same row into elemental.trace.
    // This merge belongs to the view. list() intentionally borrows its input;
    // never append special receipts to the fingerprint-bound engine ledger.
    const simulationTrace=[...list(r?.elemental?.trace)],simulationTraceKeys=new Set(simulationTrace.map(row=>
      key(row.type,row.event_id,row.time_tick,row.t,row.operation,row.reaction_type,row.special_reaction_type)));
    for(const row of list(r?.special_reactions?.trace)){
      const traceKey=key(row.type,row.event_id,row.time_tick,row.t,row.operation,row.reaction_type,row.special_reaction_type);
      if(!simulationTraceKeys.has(traceKey)){simulationTrace.push(row);simulationTraceKeys.add(traceKey);}
    }
    // They are effects-layer facts, never additional damage events.
    for (const row of simulationTrace.filter(row => within(row.t))) {
      if(String(row.type||"").startsWith("special_reaction_")){
        const specialType=row.reaction_type||row.special_reaction_type,reactionLabel=reactionNames[specialType]||"星月反应";
        const operations:Record<string,string>={establish_or_refresh_field:"建立或刷新星超导领域",count_successful_application:"记录领域内冰／雷附着",
          settle_four_second_window:"结算星超导领域窗口",expire_field:"星超导领域结束",expire_settlement_buff:"星超导结算效果结束",
          create_or_upgrade_vortex:"建立或升级星扩散风旋",create_vortex_from_packet:"建立星扩散风旋",
          advance_existing_vortex_from_packet_child:"推进星扩散风旋",remaining_packet_children_do_not_reseed:"星扩散剩余子反应不重复建立风旋",
          detonate_vortex:"星扩散风旋爆发",replace_pending_ticks:"替换月感电待结算跳数",
          extend_cloud_without_phase_reset:"延长月感电雷云（保留原跳数相位）",
          expire_area_thundercloud:"月感电雷云结束",
          schedule_aura_checked_ticks:"安排月感电实时底量检查",enqueue_party_dew_fill:"登记草露填充",
          periodic_dew_fill:"草露填充",consume_dew:"消费草露",create_moondrift_set:"生成月笼",advance_harmony:"推进月笼谐奏",
          resolve_full_cap_queue_boundary:"处理草露满额后的排队边界",
          expire_moondrift:"月笼到期",expire_moondrift_set:"月笼到期",generate_ordinary_dendro_core:"生成普通草原核"};
        const decisions:Record<string,string>={replace:"替换普通反应",ordinary:"保留普通反应",pending:"等待资料核实"};
        const layerSuffix=row.type==="special_reaction_damage_projection"&&specialType==="stellar_superconduct"&&Number.isInteger(row.settled_layers)
          ?`（${row.settled_layers}层，系数${finite(row.system_factor)?row.system_factor:"待核验"}）`:"";
        const label=row.type==="special_reaction_damage_projection"?`${reactionLabel}：能力直伤已投影${layerSuffix}`:
          operations[row.operation]||`${reactionLabel}${row.decision?`：${decisions[row.decision]||"受审决策"}`:"状态变化"}`;
        put("effects",{id:key("special-reaction",row.event_id,row.t,row.operation),kind:row.type==="special_reaction_decision"?"reaction":"condition",
          character:row.character||row.owner||null,...source(row.character||row.owner||null,"special-reaction",specialType,reactionLabel),
          label,start:row.t,element:row.element,
          detail:{...copy(row),interpretation:row.type==="special_reaction_damage_projection"&&specialType==="stellar_superconduct"
            ?"能力直伤已在事件时刻读取最近一次完成的星超导领域结算层数；调用方不能手工提供层数或系数，实际伤害在伤害视图显示。"
            :"星月专用状态机的执行回执；伤害请求及实际伤害在伤害视图单独显示。"}});continue;
      }
      if(row.type==="crystallize_shard_pickup"){
        put("effects",{id:key("crystallize-pickup",row.event_id,row.shard_id),kind:"condition",character:row.character||null,
          ...source(row.character||null,"elemental-state",row.shard_id,"普通结晶晶片"),
          label:`拾取${({pyro:"火",hydro:"水",cryo:"冰",electro:"雷"} as Record<string,string>)[row.element]||"元素"}晶片并刷新全队护盾`,
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
      const names:Record<string,string>={no_icd:"允许附着",first_hit:"首次附着",icd_timer_reset:"附着计时重置",icd_hit_sequence:"命中序列附着",icd_blocked:"附着冷却阻止",missed:"未命中",no_application:"无附着"};
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

}
