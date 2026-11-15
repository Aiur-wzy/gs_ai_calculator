"""amplify resolution over one existing target state; no clock or queue."""

from __future__ import annotations

from genshin_engine.elemental_multi_contact import MultiAuraContact


def resolve_amplify_or_refresh(contact: MultiAuraContact):
    state = contact.state
    row = contact.row
    element = contact.element
    gu = contact.gu
    compatible = {
        "pyro": ("hydro", "cryo"),
        "hydro": ("pyro",),
        "cryo": ("pyro",),
        "electro": (),
        "dendro": (),
    }[element]
    selected = contact.route(compatible)
    if selected:
        remaining = float(gu)
        reactions = []
        for index, aura in enumerate(selected):
            if remaining <= contact.epsilon:
                contact.skip_remaining(index, "trigger_exhausted", remaining)
                break
            aura_element = aura["element"]
            aura_owner = aura.get("source_owner")
            kind, factor, cost = contact.reaction_table[(aura_element, element)]
            aura_before = aura["remaining_gu"]
            before = remaining
            consumed = state.consume(aura_element, remaining * cost)
            remaining = max(0.0, remaining - consumed / cost)
            contact.route_step(index, status="reacted", skip_reason=None,
                       consumed_gu=consumed, aura_gu_before=aura_before,
                       aura_gu_after=max(0.0, aura_before-consumed),
                       trigger_gu_before=before, trigger_gu_after=remaining)
            reaction = contact.runtime.multi_reaction(
                row, kind, aura_element, consumed, gu, index=index,
                trigger_gu_before=before, trigger_gu_after=remaining,
                aura_owner=aura_owner)
            reaction["factor"] = factor
            reactions.append(reaction)
        contact.publish(reactions)
        return

    incompatible = [aura for aura in state.auras if aura != element]
    if incompatible:
        raise contact.pending_error(
            "unsupported_reaction",
            "v4尚未实现该元素组合，未假装无反应或允许非法共存。",
            row["event_id"], {"aura_elements": sorted(incompatible),
                              "trigger_element": element})
    row["outcome"] = state.add_or_refresh(element, gu, row["event_id"], row["character"])

