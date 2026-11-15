"""coexistence resolution over one existing target state; no clock or queue."""

from __future__ import annotations

import copy

from genshin_engine.elemental_multi_contact import MultiAuraContact


def resolve_hydro_cryo(contact: MultiAuraContact, opposite):
    state = contact.state
    row = contact.row
    element = contact.element
    gu = contact.gu
    non_freeze_auras = contact.non_freeze_auras
    if state.freezable:
        raise contact.pending_error(
            "unsupported_freezable_coexistence",
            "可冻结目标同时含本轮未接纳的额外元素底，无法安全进入Frozen状态。",
            row["event_id"], {"freeze_scenario": copy.deepcopy(state.freeze_contract),
                              "aura_element": opposite,
                              "trigger_element": element,
                              "coexisting_auras": sorted(non_freeze_auras)})
    resolution = state.unfreezable_resolution
    receipt = {"type": "unfreezable_hydro_cryo_resolution",
               "resolution": resolution,
               "origin": state.resolution_origin,
               "contract_fingerprint": state.freeze_contract["fingerprint"],
               "aura_element": opposite, "trigger_element": element,
               "trigger_gu": gu, "reaction_occurred": False}
    if resolution == "coexist":
        receipt["aura_update"] = state.add_or_refresh(
            element, gu, row["event_id"], row["character"])
        receipt["consumed_gu"] = 0.0
        receipt["surviving_auras"] = ["hydro", "cryo"]
        row["outcome"] = "unfreezable_coexist"
    else:
        consumed, survivor = state.cancel_hydro_cryo(
            element, gu, row["event_id"], row["character"])
        receipt.update(consumed_gu=consumed, surviving_aura=survivor,
                       surviving_auras=[survivor] if survivor else [])
        row["outcome"] = "unfreezable_mutual_cancel"
    row["freeze_resolution"] = receipt
    row["reaction"] = None
    row["reactions"] = []


def resolve_hydro_electro(contact: MultiAuraContact, electro_opposite):
    state = contact.state
    row = contact.row
    element = contact.element
    gu = contact.gu
    unrelated = sorted(name for name in state.auras
                       if name not in {"hydro", "electro"})
    if unrelated:
        contact.route(("hydro", "cryo"))
        raise contact.pending_error(
            "unsupported_reaction",
            "水雷反应与额外元素底的优先级尚未接入星月事务。",
            row["event_id"], {"coexisting_auras": unrelated,
                              "reaction_priority": copy.deepcopy(
                                  row["reaction_priority"])})
    selected = contact.route((electro_opposite,))
    aura = selected[0]
    proposal = contact.runtime.multi_proposal(
        row, "electro_charged", electro_opposite, 0.0, gu,
        aura=aura, trigger_gu_before=float(gu),
        trigger_gu_after=float(gu))
    reaction = contact.runtime.route_special_proposal(proposal, row)
    update = state.add_or_refresh(
        element, gu, row["event_id"], row["character"])
    contact.route_step(0, status="reacted", skip_reason=None,
               consumed_gu=0.0,
               aura_gu_before=aura["remaining_gu"],
               aura_gu_after=aura["remaining_gu"],
               trigger_gu_before=float(gu), trigger_gu_after=float(gu))
    reaction.update(coexisting_aura_update=update,
                    initial_gauge_semantics="retain_hydro_electro_dual_aura")
    contact.publish([reaction])


def resolve_cryo_electro(contact: MultiAuraContact, superconduct_opposite):
    state = contact.state
    row = contact.row
    gu = contact.gu
    unrelated = sorted(name for name in state.auras
                       if name not in {"cryo", "electro"})
    if unrelated:
        raise contact.pending_error(
            "unsupported_reaction",
            "冰雷反应与额外元素底的优先级尚未接入星月事务。",
            row["event_id"], {"coexisting_auras": unrelated})
    selected = contact.route((superconduct_opposite,))
    aura = selected[0]
    aura_before = aura["remaining_gu"]
    consumed = min(aura_before, float(gu))
    trigger_after = max(0.0, float(gu)-consumed)
    proposal = contact.runtime.multi_proposal(
        row, "superconduct", superconduct_opposite, consumed, gu,
        aura=aura, trigger_gu_before=float(gu),
        trigger_gu_after=trigger_after)
    reaction = contact.runtime.route_special_proposal(proposal, row)
    actual = state.consume(superconduct_opposite, consumed)
    if abs(actual-consumed) > contact.epsilon:
        raise RuntimeError("超导提案与元素底提交不一致。")
    contact.route_step(0, status="reacted", skip_reason=None,
               consumed_gu=consumed,
               aura_gu_before=aura_before,
               aura_gu_after=max(0.0, aura_before-consumed),
               trigger_gu_before=float(gu),
               trigger_gu_after=trigger_after)
    contact.publish([reaction])


def resolve_hydro_dendro(contact: MultiAuraContact, bloom_opposite):
    state = contact.state
    row = contact.row
    element = contact.element
    gu = contact.gu
    unrelated = sorted(name for name in state.auras
                       if name not in {"hydro", "dendro"})
    if unrelated:
        raise contact.pending_error(
            "unsupported_reaction",
            "水草反应与额外元素底的优先级尚未接入星月事务。",
            row["event_id"], {"coexisting_auras": unrelated})
    selected = contact.route((bloom_opposite,))
    aura = selected[0]
    cost = 2.0 if (bloom_opposite, element) == ("hydro", "dendro") else .5
    aura_before = aura["remaining_gu"]
    consumed = min(aura_before, float(gu)*cost)
    trigger_after = max(0.0, float(gu)-consumed/cost)
    proposal = contact.runtime.multi_proposal(
        row, "bloom", bloom_opposite, consumed, gu,
        aura=aura, trigger_gu_before=float(gu),
        trigger_gu_after=trigger_after)
    reaction = contact.runtime.route_special_proposal(proposal, row)
    actual = state.consume(bloom_opposite, consumed)
    if abs(actual-consumed) > contact.epsilon:
        raise RuntimeError("绽放提案与元素底提交不一致。")
    contact.route_step(0, status="reacted", skip_reason=None,
               consumed_gu=consumed,
               aura_gu_before=aura_before,
               aura_gu_after=max(0.0, aura_before-consumed),
               trigger_gu_before=float(gu),
               trigger_gu_after=trigger_after)
    core = contact.runtime.create_bloom_core(reaction, row)
    if core:
        row["bloom_core_created"] = copy.deepcopy(core["core"])
    contact.publish([reaction])

