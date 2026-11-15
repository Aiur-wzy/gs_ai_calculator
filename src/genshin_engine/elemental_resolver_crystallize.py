"""crystallize resolution over one existing target state; no clock or queue."""

from __future__ import annotations

import copy

from genshin_engine.elemental_multi_contact import MultiAuraContact
from genshin_engine.elemental_schema import MULTI_AURA_ELEMENTS, MULTI_AURA_RULESET
from genshin_engine.rotation_port import TICKS


def resolve_geo(contact: MultiAuraContact):
    state = contact.state
    row = contact.row
    gu = contact.gu
    selected = contact.route(MULTI_AURA_ELEMENTS)
    if not selected:
        row["outcome"] = "geo_without_aura"
        return
    remaining = float(gu)
    reactions, shards, attempts = [], [], []
    for index, aura in enumerate(selected):
        if remaining <= contact.epsilon:
            contact.skip_remaining(index, "trigger_exhausted", remaining)
            break
        aura_element = aura["element"]
        lunar_candidate = (
            aura_element == "hydro"
            and contact.runtime.special_enabled("lunar_crystallize"))
        admission = (None if lunar_candidate
                     else contact.runtime.crystallize_state.admit(contact.runtime.time))
        attempt = {
            "route_index": index,
            "aura_element": aura_element,
            "trigger_gu_before": remaining,
            "allowed": None if admission is None else admission["allowed"],
            "reason": ("special_route_pending" if admission is None
                       else admission["reason"]),
            "next_reaction_t": (contact.runtime.crystallize_state.next_reaction_time/TICKS
                                if admission is None else admission["next_reaction_t"]),
            "gauge_consumed": False,
        }
        if admission is not None and not admission["allowed"]:
            contact.route_step(index, status="skipped", skip_reason="crystallize_gcd",
                       trigger_gu_before=remaining, trigger_gu_after=remaining,
                       aura_gu_before=aura["remaining_gu"],
                       aura_gu_after=aura["remaining_gu"])
            attempts.append(attempt)
            # A blocked ordinary candidate consumes neither aura nor
            # trigger gauge.  Keep walking the route so a future
            # replacement candidate can bypass this ordinary GCD.
            continue
        before = remaining
        aura_before = aura["remaining_gu"]
        consumed = min(aura_before, .5 * remaining)
        remaining_after = max(0.0, remaining - 2 * consumed)
        proposal = contact.runtime.multi_proposal(
            row, "crystallize", aura_element, consumed, gu,
            aura=aura, index=index, trigger_gu_before=before,
            trigger_gu_after=remaining_after)
        reaction = contact.runtime.route_special_proposal(proposal, row)
        is_special = reaction.get("route") == "special"
        if not is_special and admission is None:
            admission = contact.runtime.crystallize_state.admit(contact.runtime.time)
            attempt.update(
                allowed=admission["allowed"], reason=admission["reason"],
                next_reaction_t=admission["next_reaction_t"])
            if not admission["allowed"]:
                contact.route_step(index, status="skipped", skip_reason="crystallize_gcd",
                           trigger_gu_before=remaining,
                           trigger_gu_after=remaining,
                           aura_gu_before=aura_before,
                           aura_gu_after=aura_before)
                attempts.append(attempt)
                continue
        actual = state.consume(aura_element, consumed)
        if abs(actual-consumed) > contact.epsilon:
            raise RuntimeError("结晶提案与元素底提交不一致。")
        remaining = remaining_after
        aura_after = (state.auras[aura_element]["remaining_gu"]
                      if aura_element in state.auras else 0.0)
        contact.route_step(index, status="reacted", skip_reason=None,
                   consumed_gu=consumed, aura_gu_before=aura_before,
                   aura_gu_after=aura_after,
                   trigger_gu_before=before, trigger_gu_after=remaining)
        event_id = row["stable_event_id"] + f":reaction:{index}"
        run_id = row["run_id"] + f":reaction:{index}"
        if is_special:
            attempt.update(
                allowed=True,
                reason="special_replacement_bypasses_crystallize_gcd",
                next_reaction_t=contact.runtime.crystallize_state.next_reaction_time/TICKS,
            )
        else:
            trigger_sample = contact.runtime.require_crystallize_trigger_sample(row)
            committed = contact.runtime.crystallize_state.commit(
                time=contact.runtime.time, event_id=event_id, run_id=run_id,
                action_id=row["action_id"], owner=row["character"],
                element=aura_element, source=row["source"],
                consumed_gu=consumed, trigger_gu=gu,
                trigger_level=trigger_sample["level"],
                trigger_em=trigger_sample["em"],
                trigger_stat_sample=trigger_sample)
            ordinary = committed["reaction"]
            ordinary.update({
                key: copy.deepcopy(value) for key, value in reaction.items()
                if key not in {"id", "run_id", "type", "source",
                               "consumed_gu", "trigger_gu", "owner",
                               "target_id", "action_id"}
            })
            ordinary.update(
                parent_run_id=row["run_id"], trigger_owner=row["character"],
                aura_owner=aura.get("source_owner"), ruleset=MULTI_AURA_RULESET,
                selection_policy="fixed_reaction_priority_v1",
                route_id=row["reaction_priority"]["route_id"],
                route_index=index, trigger_gu_before=before,
                trigger_gu_after=remaining,
                trigger_stat_sample=copy.deepcopy(trigger_sample))
            reaction = ordinary
            shards.append(committed["shard"])
        reactions.append(reaction)
        attempt.update(gauge_consumed=True, consumed_gu=consumed,
                       trigger_gu_after=remaining)
        if not is_special:
            attempt["shard_id"] = committed["shard"]["id"]
        attempts.append(attempt)
    row["crystallize_attempts"] = attempts
    row["crystallize_shards"] = shards
    contact.publish(reactions)
    if not reactions:
        row["outcome"] = "crystallize_gcd_blocked"
    return

