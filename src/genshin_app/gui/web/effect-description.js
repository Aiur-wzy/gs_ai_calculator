"use strict";

// Read-only explanations of explicit inputs and engine receipts. Never infer
// game facts from character names, source identifiers or free-form prose.
window.EFX = (() => {
  const list = value => Array.isArray(value) ? value : [];
  const finite = value => typeof value === "number" && Number.isFinite(value);
  const own = (obj, key) => Object.prototype.hasOwnProperty.call(obj || {}, key);
  const text = value => typeof value === "string" ? value.trim() : "";
  const unique = values => [...new Set(values.filter(Boolean))];
  const changes = values => values.filter((value, index) => index === 0 || value !== values[index - 1]);
  const num = value => finite(value) ? String(Number(value.toFixed(6))) : "未提供";
  const percent = value => `${num(value * 100)}%`;
  const chinese = (value, fallback) => /[\u3400-\u9fff]/u.test(text(value)) ? text(value) : fallback;
  const elements = {all:"全部", physical:"物理", pyro:"火", hydro:"水", electro:"雷", cryo:"冰", anemo:"风", geo:"岩", dendro:"草"};
  const stats = {atk:"攻击力", hp:"生命值", def:"防御力", em:"元素精通", base_atk:"基础攻击力", base_hp:"基础生命值", base_def:"基础防御力", energy_recharge:"元素充能效率"};
  const actions = {"action.elemental_skill":"元素战技", "action.elemental_burst":"元素爆发", "action.normal_attack":"普通攻击", "action.charged_attack":"重击", "action.plunging_attack":"下落攻击", "action.switch":"切换角色", "action.dash":"冲刺", "action.jump":"跳跃", "action.wait":"等待"};
  const damages = Object.fromEntries(Object.entries(actions).map(([key, value]) => [key.replace("action.", "damage."), `${value}伤害`]));
  Object.assign(damages, {"damage.reaction":"独立反应伤害", "damage.other":"其他已明确伤害"});
  const reactions = {amplify:"增幅反应", vaporize:"蒸发", melt:"融化", swirl:"扩散", frozen:"冻结", freeze:"冻结", overload:"超载", superconduct:"超导", electro_charged:"感电", burning:"燃烧", bloom:"绽放", hyperbloom:"超绽放", burgeon:"烈绽放", aggravate:"超激化", spread:"蔓激化", shatter:"碎冰", shattered:"碎冰", crystallize:"结晶", lunar_bloom:"月绽放", lunar_electro:"月感电", lunar_crystallize:"月结晶", stellar_swirl:"直伤星扩散", stellar_swirl_anemo:"反应星扩散·风", stellar_swirl_cryo_lv2:"反应星扩散·1级风旋爆发", stellar_swirl_cryo_lv3:"反应星扩散·2级风旋爆发", stellar_superconduct:"星超导"};
  const events = {action_start:"动作开始", action_end:"动作结束", switch:"切换角色", hit:"伤害命中", condition:"外部条件事件", elemental_reaction:"元素反应成立", reaction_damage:"独立反应伤害事件"};
  const ending = {natural_expiry:"自然到期", refresh:"激活或刷新", layer_expiry:"部分层数到期", quota_change:"剩余次数变化", execution_limit:"执行检查中断", window_end:"到达输入轴末"};
  const zones = {base_add:["伤害基础平值加算","flat"], stat_add:["面板平值加算","flat"], stat_pct:["基础面板比例加算","ratio"], dmg_bonus:["伤害加成","ratio"], crit_rate:["暴击率加成","ratio"], crit_dmg:["暴击伤害加成","ratio"], em:["元素精通加成","flat"], reaction_bonus:["反应伤害加成","ratio"], reaction_flat:["反应基础平值加算","flat"], lunar_base:["月曜基础增伤","ratio"], lunar_elevate:["月曜擢升加成","ratio"], stellar_base:["星烁基础增伤","ratio"], stellar_elevate:["星烁擢升加成","ratio"], def_reduction:["防御力降低","ratio"], def_ignore:["无视防御","ratio"], res_shred:["抗性降低","ratio"], base_mult:["伤害基础倍率乘算","multiplier"], lunar_mult:["月曜大权乘算","multiplier"], stellar_mult:["星烁大权乘算","multiplier"], energy_recharge:["元素充能效率加成","ratio"]};

  function benefit(effect) {
    let zone = effect?.zone, stat = effect?.stat;
    // These are registered benefit categories, never arbitrary source IDs.
    if (text(effect?.benefit_type_id).startsWith("benefit.")) {
      const parts = effect.benefit_type_id.split(".");
      if (parts.length !== 3 || !own(zones, parts[1])) return {label:"未识别的收益", unit:null};
      [, zone, stat] = parts;
    }
    if (!own(zones, zone)) return {label:chinese(effect?.name, "未识别的收益"), unit:null};
    const allowed = ["stat_add", "stat_pct"].includes(zone) ? ["atk", "hp", "def"]
      : ["dmg_bonus", "res_shred"].includes(zone) ? Object.keys(elements)
        : zone === "reaction_bonus" ? ["all", ...Object.keys(reactions)] : ["all"];
    stat ??= "all";
    if (!allowed.includes(stat)) return {label:"未识别的收益", unit:null};
    let label = zones[zone][0];
    if (zone === "dmg_bonus") label = stat === "all" ? "伤害加成" : `${elements[stat]}伤加成`;
    if (zone === "res_shred") label = stat === "all" ? "全抗降低" : `${elements[stat]}抗降低`;
    if (zone === "stat_add") label = `${stats[stat]}平值加成`;
    if (zone === "stat_pct") label = `${stats[stat]}比例加成`;
    if (zone === "reaction_bonus" && stat !== "all") label = `${reactions[stat]}伤害加成`;
    return {zone, stat, label, unit:zones[zone][1]};
  }
  function magnitude(value, info) {
    if (!finite(value)) return "数值未提供";
    if (info.unit === "ratio") return percent(value);
    if (info.unit === "multiplier") return `×${num(value)}`;
    return `${num(value)}${info.unit === "flat" ? "" : "（单位未确认）"}`;
  }
  function expression(value, info, owner, characterName) {
    if (finite(value)) return magnitude(value, info);
    if (value?.kind !== "stat" || !own(stats, value.stat) || !["owner", "recipient"].includes(value.subject) || !finite(value.factor)) return "数值或取值公式未完整提供";
    const subject = value.subject === "owner" ? `提供者${owner ? `（${characterName(owner)}）` : ""}` : "受益角色";
    let formula = `${subject}的${stats[value.stat]} × ${num(value.factor)}`;
    if (finite(value.add) && value.add !== 0) formula += ` ${value.add < 0 ? "−" : "+"} ${num(Math.abs(value.add))}`;
    if (finite(value.min)) formula += `，下限 ${num(value.min)}`;
    if (finite(value.max)) formula += `，上限 ${num(value.max)}`;
    formula += info.unit === "ratio" ? "；结果按比例换算为百分数（1 = 100%）" : info.unit === "multiplier" ? "；结果作为乘算倍率" : info.unit ? "；结果为平值" : "；结果单位未确认";
    formula += `；${{current:"按当前面板取样", trigger:"按触发时面板取样", event:"按指定事件面板取样"}[value.sample] || "取样时点未提供"}`;
    return formula;
  }
  function filterDescription(filter, characterName) {
    if (!filter || typeof filter !== "object") return [];
    const out = [];
    if (filter.event) out.push(`${events[filter.event] || "未识别的事件"}${{pre:"之前", post:"之后"}[filter.phase] || ""}`);
    if (list(filter.characters).length) out.push(`角色：${filter.characters.map(characterName).join("、")}`);
    if (list(filter.action_type_ids).length) out.push(`动作类型：${filter.action_type_ids.map(id => actions[id] || "自定义动作类型（见技术信息）").join("、")}`);
    if (list(filter.damage_type_ids).length) out.push(`伤害类型：${filter.damage_type_ids.map(id => damages[id] || "自定义伤害类型（见技术信息）").join("、")}`);
    if (list(filter.elements).length) out.push(`伤害元素：${filter.elements.map(id => elements[id] || "未识别元素").join("、")}`);
    if (list(filter.reaction_types).length) out.push(`反应类型：${filter.reaction_types.map(id => reactions[id] || "未识别反应").join("、")}`);
    if (list(filter.reaction_elements).length) out.push(`反应元素：${filter.reaction_elements.map(id => elements[id] || "未识别元素").join("、")}`);
    if (list(filter.action_ids).length) out.push(`限定 ${filter.action_ids.length} 个指定动作（标识见技术信息）`);
    if (list(filter.event_ids).length) out.push(`限定 ${filter.event_ids.length} 个指定事件（标识见技术信息）`);
    if (filter.condition) out.push(`需外部条件成立：${chinese(filter.condition, "条件中文名称未提供，标识见技术信息")}`);
    if (filter.owner_presence === "frontline") out.push("提供者必须在前台");
    return out;
  }
  function titleFor(effects) {
    const infos = effects.map(benefit), labels = [];
    for (const info of infos) {
      if (["res_shred", "dmg_bonus"].includes(info.zone)) {
        const members = infos.filter(other => other.zone === info.zone);
        const label = members.length > 1 && members.every(other => other.stat !== "all")
          ? `${unique(members.map(other => elements[other.stat])).join("／")}${info.zone === "res_shred" ? "减抗" : "伤害加成"}` : info.label;
        labels.push(label);
      } else labels.push(info.label);
    }
    return unique(labels).join("、");
  }
  function auraSummary(state) {
    if (state == null) return "无元素底";
    if (typeof state !== "object") return "元素底未提供";
    if (state.knowledge === "unknown") return "元素底未知（缺少已知状态，不能解释为无元素底）";
    const aura = own(state, "ordinary_aura") ? state.ordinary_aura : own(state, "aura") ? state.aura : state.element ? state : undefined;
    const parts = [];
    if (aura === null) parts.push("无普通元素底");
    else if (aura) parts.push(`${elements[aura.element] || "未识别元素"}底 ${num(aura.remaining_gu)} GU`);
    if (state.frozen === null) parts.push("无冻结量");
    else if (state.frozen) parts.push(`冻结量 ${num(state.frozen.remaining_gu)} GU`);
    if (finite(state.freeze_history_seconds)) parts.push(`冻结历史 ${num(state.freeze_history_seconds)} 秒`);
    return parts.join("；") || "元素底信息未完整提供";
  }

  function appendIcdAudit(out, detail, characterName) {
    const audit = detail?.icd_audit;
    if (!audit || typeof audit !== "object") return;
    if (audit.mode === "counter") {
      const actor = list(audit.shared_key).length ? characterName(audit.shared_key[0]) : "角色未提供";
      const target = list(audit.shared_key).length > 2 ? text(audit.shared_key[2]) || "目标未提供" : "目标未提供";
      out.conditions.push(`附着计数器：${actor} + 计数标签「${text(audit.counter_tag) || "未提供"}」 + 目标「${target}」独立计数；规则组「${text(audit.rule_group) || "未提供"}」只定义序列与重置规则。`);
      if (finite(audit.attempt_number)) {
        const position = finite(audit.sequence_index) ? audit.sequence_index + 1 : audit.attempt_number;
        const length = finite(audit.sequence_length) ? ` / ${num(audit.sequence_length)}` : "";
        const coefficient = finite(audit.coefficient) ? `，本位置系数 ${num(audit.coefficient)}` : "";
        out.values.push(`本次是该计数器第 ${num(audit.attempt_number)} 次尝试（序列位置 ${num(position)}${length}）${coefficient}；${audit.consumed === false ? "未推进" : "已推进"}计数。`);
      }
      if (finite(audit.anchor_t) || finite(audit.reset_at) || finite(audit.reset_seconds)) {
        const timing = [];
        if (finite(audit.anchor_t)) timing.push(`当前轮起点 ${num(audit.anchor_t)} 秒`);
        if (finite(audit.reset_at)) timing.push(`重置时刻 ${num(audit.reset_at)} 秒`);
        if (finite(audit.reset_seconds)) timing.push(`重置间隔 ${num(audit.reset_seconds)} 秒`);
        out.conditions.push(`附着计数时序：${timing.join("；")}。`);
      }
      if (audit.legacy_group === true) out.notes.push("该记录来自旧版 group 兼容字段；当前接口应分别提供计数标签 counter_tag 与规则组 rule_group。 ");
    } else if (audit.mode === "none") {
      out.conditions.push("该具体攻击分量的档案声明无附着冷却；本次不创建或推进附着计数器。");
    } else if (audit.mode === "unknown") {
      out.conditions.push("该具体攻击分量的附着规则待核实：需要补齐计数标签、规则组、命中序列与重置时间，不能仅按普攻／战技／爆发类别推断。");
    } else if (audit.mode === "not_applicable") {
      out.conditions.push("该事件未参与附着计数，也未推进任何附着序列。");
    }
  }

  function eligibilityDescription(scope = {}, characterName = id => chinese(id, "角色名称未提供")) {
    if (!scope.eligibility) return [];
    const eligibility = scope.eligibility, parts = [];
    if (scope.mode === "frontline") parts.push(scope.scope === "locked" ? "在触发时选择前台角色" : "仅当前前台可用，激活名单仍须符合资格");
    if (eligibility.exclude_owner === true) parts.push("排除提供者本人");
    if (list(eligibility.characters).length) parts.push(`资格限定：${eligibility.characters.map(characterName).join("、")}`);
    parts.push(scope.scope === "locked" ? "接收角色在触发时锁定；切走后保留至到期，不转给新前台" : "持续条表示激活状态；仅当前前台可用，切走后不提供该项收益");
    return parts;
  }

  function selectionDescription(selection, characterName = id => chinese(id, "角色名称未提供")) {
    if (selection?.contract !== "beneficiary-eligibility-v1") return [];
    const parts = [`选人时点：${num(selection.selection_t)} 秒；当时前台：${selection.frontline == null ? "未提供" : characterName(selection.frontline)}`];
    parts.push(`基础候选：${list(selection.base_candidates).map(characterName).join("、") || "无"}`);
    parts.push(`实际接收：${list(selection.recipients).map(characterName).join("、") || "无人符合资格；本次未激活，不刷新既有残留"}`);
    const reasons = {excluded_owner:"提供者被排除", not_in_whitelist:"不在资格名单内"};
    if (list(selection.excluded).length) parts.push(`排除记录：${selection.excluded.map(row => `${characterName(row.character)}（${reasons[row.reason] || "原因见原始记录"}）`).join("、")}`);
    return parts;
  }

  function describe(event = {}, context = {}) {
    event ||= {};
    const d = event.detail || {}, rule = context.rule || d.rule || {}, buff = d.buff || {};
    const characterName = id => text(typeof context.characterName === "function" ? context.characterName(id) : "") || chinese(id, "角色名称未提供");
    const owner = d.owner ?? rule.owner ?? buff.extensions?.owner;
    const name = text(d.source_name || rule.name_zh || buff.name || context.source?.name_zh);
    const fallback = chinese(event.label, "效果记录");
    const out = {title:fallback, source:"", effects:[], duration:"", values:[], samples:[], conditions:[], evidence:[], notes:[]};
    const declared = d.origin === "declared_interval" || !!d.buff;
    const effects = list(d.effects).length ? d.effects : list(rule.effects).length ? rule.effects : list(buff.effects);
    const resources = list(rule.resources);
    const rejectedSelection = event.kind === "condition" && d.reason === "no_eligible_beneficiary";
    const resourceChange = event.kind === "resource";
    const mechanism = ["coverage", "activation", "quota"].includes(event.kind) || rejectedSelection || resourceChange;
    const who = owner != null ? characterName(owner) : event.character != null ? characterName(event.character) : "提供者未提供";
    const sourceLabel = name || chinese(event.sourceLabel, mechanism ? "来源名称未提供" : fallback);
    out.source = sourceLabel === who || sourceLabel.startsWith(`${who} · `) ? sourceLabel : `${who} · ${sourceLabel}`;
    const bindingKinds = [];
    for (const binding of list(d.source_bindings)) {
      const bindingOwner = binding.owner || {};
      const category = bindingOwner.kind === "artifact_set"
        ? finite(bindingOwner.required_count) ? `圣遗物 ${num(bindingOwner.required_count)} 件套` : "圣遗物套装（件数未提供）"
        : {weapon:"武器", character_skill:"角色技能／天赋", manual:"人工规则"}[bindingOwner.kind] || "来源类别未提供";
      bindingKinds.push(category);
      if (text(binding.label)) out.evidence.push(`绑定说明：${binding.label}`);
      if (text(bindingOwner.note)) out.evidence.push(`来源备注：${bindingOwner.note}`);
      for (const ref of list(binding.source_refs)) {
        const fields = typeof ref === "string" ? [ref] : [
          text(ref?.note) ? `说明：${ref.note}` : "", text(ref?.path) ? `资料路径：${ref.path}` : "",
          text(ref?.table) ? `数据表：${ref.table}` : "",
        ].filter(Boolean);
        if (fields.length) out.evidence.push(`引用记录（不代表本次已读取）：${fields.join("；")}`);
      }
    }
    if (bindingKinds.length) out.source += `；来源类别：${unique(bindingKinds).join("、")}`;
    const triggerAction = d.trigger_action;
    if (triggerAction) out.source += `；关联动作：${triggerAction.character ? `${characterName(triggerAction.character)}的` : ""}${chinese(triggerAction.name_zh, actions[triggerAction.action_type_id] || "动作名称未提供")}${finite(triggerAction.start) ? `（${num(triggerAction.start)} 秒）` : ""}`;
    const trigger = filterDescription(d.trigger || rule.trigger, characterName);
    if (trigger.length) out.conditions.push(`触发：${trigger.join("；")}`);
    if (resourceChange) {
      const poolName = chinese(d.pool_name || d.pool?.name_zh, "共享资源");
      const accepted = finite(d.accepted) ? d.accepted : 0;
      const change = `${finite(d.before) ? num(d.before) : "未提供"} → ${finite(d.after) ? num(d.after) : "未提供"}`;
      const operation = d.type === "consume"
        ? accepted > 0 ? "实际消费" : d.reason === "insufficient" ? "库存不足" : "消费未生效"
        : d.operation === "grant" ? "实际补充" : d.operation === "set" ? "设置库存"
          : d.operation === "spend" ? "实际扣除" : "资源变化";
      out.title = `${poolName} · ${operation}`;
      if (d.type === "consume" && accepted > 0) {
        out.effects.push("本次合格命中已实际消费共享库存；只有这类成功消费回执才允许对应收益进入该次伤害。");
        for (const effect of effects) {
          const info = benefit(effect);
          out.effects.push(`${info.label}${info.zone === "base_add" ? "；在伤害基础区加入平值，不增加角色面板攻击力" : ""}。`);
          out.values.push(`${info.label}：${expression(effect.value, info, owner, characterName)}。`);
        }
      } else if (d.type === "consume") {
        out.effects.push(d.reason === "insufficient"
          ? "本次命中符合消费资格，但共享库存不足；对应收益没有进入该次伤害。"
          : "本次共享库存消费没有生效；原因见结果与技术记录。");
      } else {
        out.effects.push("本条记录改变后续可用的共享库存，本身不额外造成伤害，也不等于当场应用了库存所门控的收益。");
      }
      out.values.push(`库存余额：${change}；请求 ${finite(d.requested) ? num(d.requested) : "未提供"}，实际 ${finite(d.accepted) ? num(d.accepted) : "未提供"}。`);
      if (finite(d.unit_scale) && d.unit_scale !== 1) out.values.push(`每一显示单位包含 ${num(d.unit_scale)} 个整数原子；本记录保留原子数量。`);
      if (finite(d.unfulfilled) && d.unfulfilled > 0) out.values.push(`未扣足：${num(d.unfulfilled)}。`);
      if (finite(d.wasted) && d.wasted > 0) out.values.push(`达到容量后溢出：${num(d.wasted)}。`);
      const selector = d.consumer?.filter || d.operation_spec?.trigger;
      const filters = filterDescription(selector, characterName);
      if (filters.length) out.conditions.push(`${d.type === "consume" ? "消费资格" : "库存变化触发"}：${filters.join("；")}`);
      const relation = {owner:"仅提供者本人",frontline:"仅当前前台角色",other_frontline:"仅提供者以外的当前前台角色",party_member:"任一队伍角色"}[d.consumer?.actor_relation];
      if (relation) out.conditions.push(`可消费角色：${relation}。`);
      if (d.trigger_hit) out.conditions.push(`关联命中：${characterName(d.trigger_hit.character)}的${chinese(d.trigger_hit.name_zh, "伤害命中")}。`);
      out.notes.push("效果轴只投影实际资源变化和合格但库存不足的消费；冷却内补充尝试与不合格角色检查保留在完整共享资源流水中。");
    } else if (mechanism) {
      const resourceNames = resources.map(resource => resource.kind === "energy_direct" ? "直接回能" : resource.kind === "particles" ? resource.particle_type === "orb" ? "元素晶球生成" : "元素微粒生成" : "资源事件");
      const title = unique([titleFor(effects), ...resourceNames]).join("、") || chinese(event.label, "收益内容未提供");
      const resourceGate = d.resource_gate || rule.resource_gate;
      const visibleTitle = resourceGate && name ? `${chinese(name, title)} · ${title}` : title;
      out.title = `${visibleTitle}${event.kind === "activation" ? d.origin === "initial_state" ? " · 初始已激活" : " · 激活或刷新" : event.kind === "quota" ? d.consumed === false ? " · 次数不足" : " · 次数消耗" : d.state_status === "exhausted" ? resourceGate ? " · 共享库存耗尽" : " · 次数耗尽" : ""}${resourceGate ? " · 受共享库存门控" : ""}`;
      if (rejectedSelection) out.title = `${title} · 无合格接收者，未激活`;
      if (!effects.length && !resources.length) { out.effects.push("收益内容未提供；需补充规则或重新计算后查看。"); out.values.push("收益数值未提供。"); }
      for (const resource of resources) {
        if (resource.kind === "energy_direct") {
          out.effects.push("直接恢复元素能量；不作为伤害或元素微粒计算。");
          const amounts = Object.entries(resource.amounts || {}).filter(([, amount]) => finite(amount));
          out.values.push(amounts.length ? `声明回能：${amounts.map(([id, amount]) => `${characterName(id)} ${num(amount)} 点`).join("、")}。` : "直接回能数值未提供。");
        } else if (resource.kind === "particles") {
          const particle = resource.particle_type === "orb" ? "元素晶球" : "元素微粒";
          out.effects.push(`生成${resource.element === "clear" ? "无元素" : elements[resource.element] || "元素种类未提供的"}${particle}；实际角色能量收入另取决于接球与充能规则。`);
          out.values.push(`声明生成数量：${num(resource.count)} 个${particle}。`);
          if (finite(resource.travel_time)) out.conditions.push(`资源到达延迟：${num(resource.travel_time)} 秒。`);
        } else out.effects.push("资源事件类别未识别，详见技术信息。");
        if (finite(resource.delay)) out.conditions.push(`资源生成延迟：触发事件后 ${num(resource.delay)} 秒（与规则收益的生效延迟分别计时）。`);
      }
      for (const [index, effect] of effects.entries()) {
        const info = benefit(effect), app = list(d.effect_applicability).find(item => item.effect_id === effect.id);
        const domain = effect.domain || app?.domain || (["res_shred", "def_reduction", "def_ignore"].includes(info.zone) ? "enemy" : declared ? "self" : null);
        const domainText = {enemy:"作用于敌方，按命中时敌方状态取值", provider:"提供者侧收益，按对应命中／反应的取样约定检查；受益角色由范围另行决定", self:"受益角色自身计算域，按该次命中的动态／快照规则取样；不表示仅提供者本人受益"}[domain] || "收益作用域未提供";
        out.effects.push(`${info.label}；${domainText}${info.zone === "base_add" ? "；在伤害基础区加入平值，不增加角色面板攻击力" : ""}${info.zone === "stat_pct" ? "；按对应基础面板增加属性" : ""}。`);
        out.values.push(`${info.label}：${declared ? "声明值" : "每层声明值"} ${expression(effect.value, info, owner, characterName)}。`);
        const filters = filterDescription(effect.filter || list(d.filters).find(item => item.effect_id === effect.id)?.filter, characterName);
        const selector = effect.applies_to;
        if (selector?.mode === "types") filters.push(`适用伤害：${list(selector.damage_type_ids).map(id => damages[id] || "自定义伤害类型（见技术信息）").join("、") || "类型未提供"}`);
        else if (selector?.mode === "hits") filters.push(`仅适用 ${list(selector.hit_ids).length} 个指定命中（标识见技术信息）`);
        else if (selector?.mode === "none") filters.push("不应用于任何命中");
        else if (selector?.mode === "all") filters.push("命中选择器允许全部伤害；其他条件仍需满足");
        if (filters.length) out.conditions.push(`${info.label}筛选：${filters.join("；")}`);
        if (app?.message) out.conditions.push(`${info.label}作用范围：${app.message}`);
        const applied = list(d.applied_values).filter(item => item.effect_id === effect.id && finite(item.value));
        const redundantConstant = applied.length && finite(effect.value)
          && applied.every(item => item.value === effect.value && (item.layers ?? d.layers) === 1);
        if (applied.length && !redundantConstant) {
          const values = applied.map(item => item.value), low = Math.min(...values), high = Math.max(...values);
          out.values.push(`${info.label}的关联命中投影值：${magnitude(low, info)}${high !== low ? ` ～ ${magnitude(high, info)}` : ""}（已含层数；${applied.length} 条关联取样，不代表整个区间恒定；投影不等于该次伤害公式实际使用）。`);
          const samples = applied.filter(item => finite(item.t) || finite(item.sample_t)).slice(0, 3);
          for (const item of samples) out.samples.push(`${info.label}投影示例：${finite(item.t) ? `${num(item.t)} 秒命中` : "命中时刻未提供"}${finite(item.sample_t) ? `，使用 ${num(item.sample_t)} 秒的收益状态` : ""}，投影值 ${magnitude(item.value, info)}${item.character ? `（${characterName(item.character)}）` : ""}；是否进入该次伤害公式需查看伤害明细。`);
        }
        if (!info.unit) out.notes.push(`第 ${index + 1} 项收益类型或单位未识别；保留原始数值，不推断百分比。`);
      }
      const names = rejectedSelection ? [] : list(event.beneficiary_names).length ? event.beneficiary_names
        : Array.isArray(d.beneficiaries) ? d.beneficiaries.map(characterName)
          : list(event.characters).length ? event.characters.map(characterName) : event.character != null ? [characterName(event.character)] : [];
      if (names.length) out.conditions.push(`实例接收角色：${unique(names).join("、")}`);
      else if (Array.isArray(d.beneficiaries)) out.conditions.push("本次激活记录没有接收角色。");
      const scope = d.beneficiaries_scope || rule.beneficiaries || {};
      if (scope.eligibility) {
        out.conditions.push(...eligibilityDescription(scope, characterName));
      }
      const selections = [d.beneficiary_selection, ...list(d.states).map(s => s.beneficiary_selection)]
        .filter(s => s?.contract === "beneficiary-eligibility-v1");
      const uniqueSelections = [...new Map(selections.map(s => [JSON.stringify(s), s])).values()];
      for (const selection of uniqueSelections.slice(0, 12)) out.conditions.push(...selectionDescription(selection, characterName));
      if (uniqueSelections.length > 12) out.notes.push(`本段共 ${uniqueSelections.length} 条选人记录，此处显示前 12 条，其余见完整技术信息。`);
      if (scope.eligibility && !uniqueSelections.length) out.notes.push("未提供触发时的选人回执；仅显示已记录的实例接收者，不推测当时前台或被排除者。");
      if (scope.scope === "current_frontline" || buff.scope === "frontline") out.conditions.push("角色侧收益仅在符合角色范围且当前在前台时可用；敌方收益按自身作用范围另判。");
      else if (scope.scope === "locked") out.conditions.push("实例角色范围在触发时锁定；敌方收益按自身作用范围另判。");
      if (list(scope.characters).length) out.conditions.push(`规则限定角色：${scope.characters.map(characterName).join("、")}`);
      if (buff.target != null) out.conditions.push(`手工声明目标：${characterName(buff.target)}（敌方减益仍按敌方作用范围解释）`);
      if (finite(d.layers)) out.values.push(`此段层数：${num(d.layers)} 层。`);
      else if (list(d.states).some(s => finite(s.layers))) out.values.push(`层数变化：${changes(d.states.filter(s => finite(s.layers)).map(s => `${num(s.layers)} 层`)).join(" → ")}。`);
      if (finite(d.remaining_quota)) out.values.push(`此段剩余次数：${num(d.remaining_quota)} 次${d.quota_pool === "team" ? "（全队共享）" : ""}。`);
      else if (list(d.states).some(s => finite(s.remaining_quota))) out.values.push(`剩余次数变化：${changes(d.states.filter(s => finite(s.remaining_quota)).map(s => num(s.remaining_quota))).join(" → ")} 次。`);
      if (rule.quota) out.conditions.push(`次数池：${rule.quota.scope === "team" ? "全队共享" : "每个受益角色独立"}，容量 ${num(rule.quota.capacity)} 次；${{reset:"刷新时重置", add:"刷新时增加", keep:"刷新时保留"}[rule.quota.refresh] || "刷新方式未提供"}。`);
      if (rule.quota?.consume) out.conditions.push(`次数消耗条件：${filterDescription(rule.quota.consume, characterName).join("；")}`);
      if (resourceGate) {
        const gatePool = resourceGate.pool, gateName = chinese(gatePool?.name_zh, "共享库存");
        const relation = {owner:"仅提供者本人",frontline:"仅当前前台角色",other_frontline:"仅提供者以外的当前前台角色",party_member:"任一队伍角色"}[resourceGate.actor_relation];
        const capacity = finite(gatePool?.capacity) ? `，玩法容量 ${num(gatePool.capacity)}`
          : gatePool?.capacity === "unbounded" ? "，未声明玩法容量上限（仍受引擎安全界保护）" : "";
        out.conditions.push(`共享库存门控：${gateName}${capacity}；实际是否受益以每次命中的共享资源消费回执为准。`);
        if (relation) out.conditions.push(`可消费角色：${relation}。`);
        const gateFilters = filterDescription(resourceGate.filter, characterName);
        if (gateFilters.length) out.conditions.push(`共享库存消费条件：${gateFilters.join("；")}`);
        if (d.resource_balance && finite(d.resource_balance.remaining)) {
          const scale=finite(d.resource_balance.unit_scale)&&d.resource_balance.unit_scale>0?d.resource_balance.unit_scale:1;
          out.values.push(`此段共享库存：${num(d.resource_balance.remaining/scale)}；下一次消费需要 ${num(d.resource_balance.required/scale)}。`);
        } else if (list(d.states).some(state=>finite(state.resource_balance?.remaining))) {
          const balances=changes(d.states.filter(state=>finite(state.resource_balance?.remaining)).map(state=>{
            const scale=finite(state.resource_balance.unit_scale)&&state.resource_balance.unit_scale>0?state.resource_balance.unit_scale:1;
            return num(state.resource_balance.remaining/scale);
          }));
          out.values.push(`共享库存变化：${balances.join(" → ")}。`);
        }
        out.notes.push("持续条只表示规则期限；库存为零时规则仍可处于期限内，但不会向该次命中提供收益。库存获得、消费与补充见效果轴的共享资源变化点。");
      }
      if (event.kind === "quota") out.values.push(`本次次数：${num(d.before)} → ${num(d.after)}${d.consumed === false ? "（次数不足，未消费）" : ""}。`);
      if (d.state_status === "exhausted") out.effects.push(resourceGate
        ? "该段共享库存不足以支付下一次消费，不再计作可用收益；规则期限尚未结束。"
        : "该段次数已经耗尽，不再计作可用收益；规则期限尚未结束。");
      if (finite(d.applied_value_count) && d.applied_value_count > list(d.applied_values).length) out.notes.push(`关联投影共 ${num(d.applied_value_count)} 条，此处仅据前 ${list(d.applied_values).length} 条取样生成说明；完整数据见计算流水。`);
      if (declared) out.notes.push("此持续区间为明确的手工声明，不代表引擎验证了触发。");
      else if (rejectedSelection) out.notes.push("触发已被处理，但没有合格接收者；未创建收益实例，也不清除其他角色已有的残留。");
      else out.notes.push(`${event.kind === "coverage" ? "持续条表示规则状态" : "本记录表示规则激活或次数变化"}；每次命中是否受益仍取决于前台、筛选、次数及快照取样。`);
    } else if (event.kind === "hit") {
      const input = {...d.input, ...d.hit_parameters};
      out.effects.push(`${elements[event.element || input.element] || "元素未提供"}伤害命中事件；${list(input.damage_type_ids).map(id => damages[id] || "自定义伤害类型").join("、") || "伤害类别未提供"}。`);
      if (finite(input.multiplier)) out.values.push(`伤害倍率：${percent(input.multiplier)}${stats[input.scaling_stat] ? ` × ${stats[input.scaling_stat]}` : "（计量属性未提供）"}。`);
      else out.values.push("伤害倍率未提供；最终伤害及收益取值请查看伤害轴。");
      if (finite(input.count)) out.values.push(`聚合命中次数：${num(input.count)}。`);
      if (d.reported_damage === false) out.notes.push("此事件未纳入伤害合计。");
      out.notes.push("命中时间点不等于施放动作，也不单独证明附着或反应成立。");
    } else if (event.kind === "reaction") {
      const reaction = d.reaction || d;
      const type = reaction.type;
      out.effects.push(`${reactions[type] || chinese(event.label, "元素反应")}；${elements[reaction.reaction_element || event.element] || "反应元素未提供"}${reaction.reaction_element || event.element ? "元素" : ""}。`);
      if (finite(reaction.factor)) out.values.push(`反应基础倍率：×${num(reaction.factor)}（不是最终伤害倍率）。`);
      if (finite(reaction.consumed_gu)) out.values.push(`消耗元素量：${num(reaction.consumed_gu)} GU。`);
      if (!out.values.length) out.values.push("此记录未提供独立数值；反应出伤与伤害数值请查看伤害轴。");
      out.notes.push("反应成立与是否产生独立伤害分别判定，不按元素层消耗条数重复计算伤害。");
    } else if (event.kind === "condition") {
      const reasons = {no_icd:"档案声明无附着冷却，本次允许附着", first_hit:"本组首次附着", icd_timer_reset:"附着冷却计时重置后允许附着", icd_hit_sequence:"按命中序列允许附着", icd_blocked:"受附着冷却限制，本次未附着", missed:"本次未命中", no_application:"档案声明本事件无元素附着"};
      out.effects.push(reasons[d.reason] || chinese(event.label, "条件状态记录"));
      if (typeof d.allowed === "boolean") out.effects.push(d.type === "reaction_damage_attempt"
        ? d.allowed ? "本次允许产生反应伤害。" : "本次反应伤害受冷却限制；已成立反应不会因此撤销。"
        : d.allowed ? "本次判定允许元素附着；不代表必定发生反应或反应伤害。" : "本次判定不允许元素附着。");
      if (finite(d.application_gu)) out.values.push(`本次附着量：${num(d.application_gu)} GU。`);
      else if (finite(d.gauge_units)) out.values.push(`声明附着量：${num(d.gauge_units)} GU。`);
      else if (finite(d.application_profile?.gauge_units)) out.values.push(`档案声明附着量：${num(d.application_profile.gauge_units)} GU（本次是否实际附着仍以判定为准）。`);
      if (typeof d.value === "boolean") out.values.push(`条件判定：${d.value ? "成立" : "不成立"}。`);
      if (!out.values.length) out.values.push("此记录为状态判定，无直接伤害数值。");
    } else {
      out.effects.push("该效果类型暂无专用中文解释；可查看完整技术信息。");
      out.values.push("未推断此记录的数值单位。");
    }
    appendIcdAudit(out, d, characterName);
    if (d.type === "elemental_application" && typeof d.application_allowed === "boolean"
      && typeof d.reaction_occurred === "boolean") {
      if (d.application_allowed && !d.reaction_occurred) out.effects.push("本次允许元素附着，但没有发生元素反应；反应结果取决于命中前元素底及反应规则。");
      else if (d.application_allowed && d.reaction_occurred) out.effects.push("本次允许元素附着，并在当前元素底上实际触发了元素反应。");
      else if (!d.application_allowed) out.effects.push("本次未通过附着判定，因此不会由这次攻击分量触发元素反应。");
    }
    if (["reaction", "condition"].includes(event.kind)) {
      if (own(d, "before")) out.conditions.push(`事件前元素底：${auraSummary(d.before)}`);
      if (own(d, "after")) out.conditions.push(`事件后元素底：${auraSummary(d.after)}`);
    }
    if (finite(event.start) && finite(event.end) && event.end > event.start) {
      out.duration = `${num(event.start)} ～ ${num(event.end)} 秒，当前显示段 ${num(event.end - event.start)} 秒（结束时刻不含）`;
      if (finite(rule.timing?.duration)) out.duration += `；每次触发声明持续 ${num(rule.timing.duration)} 秒`;
      if (finite(d.rule_expires_at)) out.duration += `；当前规则到期点 ${num(d.rule_expires_at)} 秒`;
      if (d.end_reason) out.duration += `；本段结束：${ending[d.end_reason] || "结束原因未识别"}`;
      const activations = unique(list(d.states).filter(s => finite(s.activation_time) && finite(s.rule_expires_at))
        .map(s => `${s.origin === "initial_state" ? "初态记录" : "激活／刷新"} ${num(s.activation_time)} 秒 → 到期 ${num(s.rule_expires_at)} 秒`));
      if (activations.length > 1) out.duration += `；此段合并了 ${activations.length} 条激活／刷新记录，显示段长度不是单次持续时间`;
      if (activations.length) out.notes.push(`持续状态记录：${activations.join("；")}。`);
      if (list(d.states).some(s => s.origin === "initial_state")) out.notes.push("初态期限只表示轴起点的剩余时间，不能用于推断技能的完整持续时间。");
    } else {
      out.duration = finite(event.start) ? `${num(event.start)} 秒发生的时间点，无独立持续区间` : "发生时间或持续区间未提供";
      if (finite(rule.timing?.duration)) out.duration += `；关联规则每次触发声明持续 ${num(rule.timing.duration)} 秒`;
    }
    if (finite(rule.timing?.delay)) out.conditions.push(`触发后延迟 ${num(rule.timing.delay)} 秒生效。`);
    if (finite(rule.timing?.cooldown) && rule.timing.cooldown > 0) out.conditions.push(`规则触发冷却 ${num(rule.timing.cooldown)} 秒（从${rule.timing.cooldown_anchor === "attempt" ? "触发尝试" : "触发成功"}计时）。`);
    const evidenceStatus = d.profile_status || d.evidence_status || d.input_status || rule.status;
    if (evidenceStatus) out.evidence.push(`声明证据状态：${{confirmed:"已确认", assumed:"采用假设", unknown:"尚未核实", pending:"待核实", declared:"手工声明"}[evidenceStatus] || "状态未识别"}。`);
    for (const source of unique([text(d.source), text(rule.source), text(buff.source), text(context.source)])) out.evidence.push(`依据原文：${source}`);
    if (!out.evidence.length) out.evidence.push("当前记录未提供机制依据原文。");
    if (text(d.interpretation)) out.notes.push(d.interpretation);
    for (const key of ["effects", "values", "samples", "conditions", "evidence", "notes"]) out[key] = unique(out[key]);
    return out;
  }
  function describeAppliedBenefit(record = {}) {
    const info = benefit({benefit_type_id:record.benefit_type_id});
    return {label:info.label, value:magnitude(record.value, info)};
  }
  return {describe, describeAppliedBenefit, eligibilityDescription, selectionDescription};
})();
