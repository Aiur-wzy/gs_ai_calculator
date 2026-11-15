"""swirl resolution over one existing target state; no clock or queue."""

from __future__ import annotations

from genshin_engine.elemental_multi_contact import MultiAuraContact
from genshin_engine.elemental_schema import MULTI_AURA_ELEMENTS


def resolve_anemo(contact: MultiAuraContact):
    state = contact.state
    row = contact.row
    gu = contact.gu
    selected = contact.route(MULTI_AURA_ELEMENTS)
    if not selected:
        row["outcome"] = "anemo_without_aura"
        return
    remaining = float(gu)
    reactions = []
    for index, aura in enumerate(selected):
        if remaining <= contact.epsilon:
            contact.skip_remaining(index, "trigger_exhausted", remaining)
            break
        aura_element = aura["element"]
        aura_before = aura["remaining_gu"]
        before = remaining
        consumed = min(aura_before, .5 * remaining)
        remaining_after = max(0.0, remaining - 2 * consumed)
        proposal = contact.runtime.multi_proposal(
            row, "swirl", aura_element, consumed, gu, aura=aura,
            index=index, trigger_gu_before=before,
            trigger_gu_after=remaining_after)
        reaction = contact.runtime.route_special_proposal(proposal, row)
        actual = state.consume(aura_element, consumed)
        if abs(actual-consumed) > contact.epsilon:
            raise RuntimeError("扩散提案与元素底提交不一致。")
        remaining = remaining_after
        contact.route_step(index, status="reacted", skip_reason=None,
                   consumed_gu=consumed, aura_gu_before=aura_before,
                   aura_gu_after=max(0.0, aura_before-consumed),
                   trigger_gu_before=before, trigger_gu_after=remaining)
        reactions.append(reaction)
    contact.publish(reactions)
    return

