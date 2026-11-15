"""freeze resolution over one existing target state; no clock or queue."""

from __future__ import annotations

import copy

from genshin_engine.elemental_multi_contact import MultiAuraContact


def resolve_freezable_contact(contact: MultiAuraContact):
    state = contact.state
    row = contact.row
    element = contact.element
    gu = contact.gu
    freeze_child = contact.freeze_child
    before = freeze_child.state()
    raw_reactions, layers, receipt = state.apply_freezable(
        element, gu, row["event_id"], row["character"])
    row["freeze_update"] = receipt
    row["layer_consumptions"] = layers
    reactions = []
    if raw_reactions:
        raw = raw_reactions[0]
        trigger_after = max(0.0, float(gu) - raw["consumed_gu"])
        aura_before = before["ordinary_aura"]
        aura_after = receipt["after"]["ordinary_aura"]
        row["reaction_priority"] = {
            "policy": "freezable_freeze_state_v1",
            "route_id": f"{element}:{raw['aura_element']}>frozen",
            "trigger_element": element,
            "present_elements": [raw["aura_element"]],
            "route": [raw["aura_element"]],
            "ranked_candidates": [{
                "rank": 1, "element": raw["aura_element"],
                "status": "reacted", "skip_reason": None,
                "remaining_gu": aura_before["remaining_gu"],
                "aura_gu_before": aura_before["remaining_gu"],
                "aura_gu_after": (aura_after["remaining_gu"]
                                  if aura_after and aura_after["element"] == raw["aura_element"] else 0.0),
                "consumed_gu": raw["consumed_gu"],
                "trigger_gu_before": float(gu),
                "trigger_gu_after": trigger_after,
            }],
            "gauge_independent": True,
            "freeze_scenario_fingerprint": state.freeze_contract["fingerprint"],
            "scenario_origin": state.resolution_origin,
        }
        reaction = contact.runtime.multi_reaction(
            row, "freeze", raw["aura_element"], raw["consumed_gu"], gu,
            trigger_gu_before=float(gu), trigger_gu_after=trigger_after,
            aura_owner=raw.get("aura_owner"))
        reaction.update(
            reaction_element="cryo",
            new_frozen_gu=raw["new_frozen_gu"],
            previous_frozen_gu=raw["previous_frozen_gu"],
            effective_frozen_gu=raw["effective_frozen_gu"],
            freeze_operation=receipt["operation"],
            retained_ordinary_aura=copy.deepcopy(receipt["retained_ordinary_aura"]))
        reactions.append(reaction)
    contact.publish(reactions)
    if not reactions:
        row["outcome"] = receipt["operation"]


def resolve_frozen_anemo(contact: MultiAuraContact):
    state = contact.state
    row = contact.row
    gu = contact.gu
    raw_reactions, layers, receipt = state.apply_freezable_anemo(
        gu, row["event_id"], row["character"])
    row["freeze_layer_resolution"] = receipt
    row["layer_consumptions"] = layers
    row["reaction_priority"] = {
        "policy": receipt["policy"],
        "route_id": "anemo:" + ">".join(receipt["route"]),
        "trigger_element": "anemo",
        "present_elements": [item["reaction_element"] for item in receipt["ranked_candidates"]],
        "route": copy.deepcopy(receipt["route"]),
        "ranked_candidates": copy.deepcopy(receipt["ranked_candidates"]),
        "gauge_independent": True,
        "freeze_scenario_fingerprint": state.freeze_contract["fingerprint"],
        "scenario_origin": state.resolution_origin,
    }
    reactions = []
    for index, raw in enumerate(raw_reactions):
        candidates = [item for item in receipt["ranked_candidates"]
                      if item["status"] == "reacted"
                      and item["reaction_element"] == raw["reaction_element"]]
        trigger_before = candidates[0]["trigger_gu_before"] if candidates else float(gu)
        trigger_after = candidates[-1]["trigger_gu_after"] if candidates else 0.0
        owners = raw.get("aura_owners", [])
        aura_context = {
            "contributor_ids": copy.deepcopy(owners),
            **({"source_owner": owners[0]} if len(owners) == 1 else {}),
        }
        proposal = contact.runtime.multi_proposal(
            row, "swirl", raw["reaction_element"], raw["consumed_gu"], gu,
            aura=aura_context, index=index,
            trigger_gu_before=trigger_before,
            trigger_gu_after=trigger_after)
        reaction = contact.runtime.route_special_proposal(proposal, row)
        reaction.update(layers=copy.deepcopy(raw.get("layers", [])),
                        aura_owners=copy.deepcopy(owners),
                        layer_policy=receipt["policy"])
        reactions.append(reaction)
    contact.publish(reactions)
    if not reactions:
        row["outcome"] = "anemo_without_aura"

