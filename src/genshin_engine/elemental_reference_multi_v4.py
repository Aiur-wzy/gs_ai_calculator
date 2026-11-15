"""Frozen v4 multi-aura resolver reference; no production fallback."""

from __future__ import annotations

import copy
from types import FunctionType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from genshin_engine.elemental import (  # noqa: TC004 - injected canonical globals
        _EPS,
        _REACTIONS,
        ElementalPending,
    )
    from genshin_engine.elemental_schema import (  # noqa: TC004 - injected canonical globals
        MULTI_AURA_ELEMENTS,
        MULTI_AURA_RULESET,
    )
    from genshin_engine.rotation_port import (
        TICKS,  # noqa: TC004 - injected canonical globals
    )


def _canonical_apply_multi_v4(self, profile, row):
        """Apply one v4 contact against the finite multi-aura state.

        Multi-layer selection follows the versioned fixed reaction route and
        is independent of mutable GU magnitudes.  Remaining trigger GU may
        continue to the next routed layer.
        """
        element, gu = profile["element"], profile["gauge_units"]
        state = self.multi_state

        if state.freezable and {"hydro", "cryo"}.issubset(state.auras):
            raise ElementalPending(
                "freezable_frozen_state_required",
                "可冻结对象已有无先后证据的水冰共存初态，无法重建Frozen量与底层余量。",
                row["event_id"], {"freeze_scenario": copy.deepcopy(state.freeze_contract)})
        if (not state.freezable and state.unfreezable_resolution == "mutual_cancel"
                and {"hydro", "cryo"}.issubset(state.auras)):
            raise ElementalPending(
                "freeze_resolution_conflict",
                "mutual_cancel场景出现了未经相消的水冰共存初态。",
                row["event_id"], {"freeze_scenario": copy.deepcopy(state.freeze_contract)})

        def route(elements):
            selected, audit = state.reaction_route(element, elements)
            row["reaction_priority"] = audit
            if selected is None:
                raise ElementalPending(
                    "reaction_priority_unknown",
                    "该入射元素与多底组合没有已受审的固定反应优先路由。",
                    row["event_id"],
                    {"reaction_priority": copy.deepcopy(audit)})
            return selected

        def publish(reactions):
            row["reactions"] = reactions
            row["reaction"] = reactions[0] if reactions else None
            row["outcome"] = "reaction" if reactions else "aura_updated"

        def route_step(index, **values):
            candidates = row.get("reaction_priority", {}).get("ranked_candidates", [])
            if index < len(candidates):
                candidates[index].update(values)

        def skip_remaining(start, reason, trigger_gu):
            candidates = row.get("reaction_priority", {}).get("ranked_candidates", [])
            for index in range(start, len(candidates)):
                candidates[index].update(status="skipped", skip_reason=reason,
                                         trigger_gu_before=trigger_gu,
                                         trigger_gu_after=trigger_gu)

        freeze_child = state.freeze_state
        frozen_active = bool(freeze_child and freeze_child.frozen)
        non_freeze_auras = [name for name in state.auras if name not in ("hydro", "cryo")]
        if frozen_active and (element not in ("hydro", "cryo", "anemo") or non_freeze_auras):
            raise ElementalPending(
                "unsupported_frozen_reaction",
                "Frozen与本次元素/共存底的交互尚未纳入v4；事件已原子回滚。",
                row["event_id"], {"trigger_element": element,
                                  "coexisting_auras": sorted(non_freeze_auras),
                                  "frozen": copy.deepcopy(freeze_child.frozen),
                                  "freeze_scenario": copy.deepcopy(state.freeze_contract)})

        if (state.freezable and freeze_child and element in ("hydro", "cryo")
                and not non_freeze_auras):
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
                reaction = self._multi_reaction(
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
            publish(reactions)
            if not reactions:
                row["outcome"] = receipt["operation"]
            return

        if (element == "anemo" and freeze_child
                and (freeze_child.aura or freeze_child.frozen) and not non_freeze_auras):
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
                proposal = self._multi_proposal(
                    row, "swirl", raw["reaction_element"], raw["consumed_gu"], gu,
                    aura=aura_context, index=index,
                    trigger_gu_before=trigger_before,
                    trigger_gu_after=trigger_after)
                reaction = self._route_special_proposal(proposal, row)
                reaction.update(layers=copy.deepcopy(raw.get("layers", [])),
                                aura_owners=copy.deepcopy(owners),
                                layer_policy=receipt["policy"])
                reactions.append(reaction)
            publish(reactions)
            if not reactions:
                row["outcome"] = "anemo_without_aura"
            return

        if element == "anemo":
            selected = route(MULTI_AURA_ELEMENTS)
            if not selected:
                row["outcome"] = "anemo_without_aura"
                return
            remaining = float(gu)
            reactions = []
            for index, aura in enumerate(selected):
                if remaining <= _EPS:
                    skip_remaining(index, "trigger_exhausted", remaining)
                    break
                aura_element = aura["element"]
                aura_before = aura["remaining_gu"]
                before = remaining
                consumed = min(aura_before, .5 * remaining)
                remaining_after = max(0.0, remaining - 2 * consumed)
                proposal = self._multi_proposal(
                    row, "swirl", aura_element, consumed, gu, aura=aura,
                    index=index, trigger_gu_before=before,
                    trigger_gu_after=remaining_after)
                reaction = self._route_special_proposal(proposal, row)
                actual = state.consume(aura_element, consumed)
                if abs(actual-consumed) > _EPS:
                    raise RuntimeError("扩散提案与元素底提交不一致。")
                remaining = remaining_after
                route_step(index, status="reacted", skip_reason=None,
                           consumed_gu=consumed, aura_gu_before=aura_before,
                           aura_gu_after=max(0.0, aura_before-consumed),
                           trigger_gu_before=before, trigger_gu_after=remaining)
                reactions.append(reaction)
            publish(reactions)
            return

        if element == "geo":
            selected = route(MULTI_AURA_ELEMENTS)
            if not selected:
                row["outcome"] = "geo_without_aura"
                return
            remaining = float(gu)
            reactions, shards, attempts = [], [], []
            for index, aura in enumerate(selected):
                if remaining <= _EPS:
                    skip_remaining(index, "trigger_exhausted", remaining)
                    break
                aura_element = aura["element"]
                lunar_candidate = (
                    aura_element == "hydro"
                    and self._special_enabled("lunar_crystallize"))
                admission = (None if lunar_candidate
                             else self.crystallize_state.admit(self.time))
                attempt = {
                    "route_index": index,
                    "aura_element": aura_element,
                    "trigger_gu_before": remaining,
                    "allowed": None if admission is None else admission["allowed"],
                    "reason": ("special_route_pending" if admission is None
                               else admission["reason"]),
                    "next_reaction_t": (self.crystallize_state.next_reaction_time/TICKS
                                        if admission is None else admission["next_reaction_t"]),
                    "gauge_consumed": False,
                }
                if admission is not None and not admission["allowed"]:
                    route_step(index, status="skipped", skip_reason="crystallize_gcd",
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
                proposal = self._multi_proposal(
                    row, "crystallize", aura_element, consumed, gu,
                    aura=aura, index=index, trigger_gu_before=before,
                    trigger_gu_after=remaining_after)
                reaction = self._route_special_proposal(proposal, row)
                is_special = reaction.get("route") == "special"
                if not is_special and admission is None:
                    admission = self.crystallize_state.admit(self.time)
                    attempt.update(
                        allowed=admission["allowed"], reason=admission["reason"],
                        next_reaction_t=admission["next_reaction_t"])
                    if not admission["allowed"]:
                        route_step(index, status="skipped", skip_reason="crystallize_gcd",
                                   trigger_gu_before=remaining,
                                   trigger_gu_after=remaining,
                                   aura_gu_before=aura_before,
                                   aura_gu_after=aura_before)
                        attempts.append(attempt)
                        continue
                actual = state.consume(aura_element, consumed)
                if abs(actual-consumed) > _EPS:
                    raise RuntimeError("结晶提案与元素底提交不一致。")
                remaining = remaining_after
                aura_after = (state.auras[aura_element]["remaining_gu"]
                              if aura_element in state.auras else 0.0)
                route_step(index, status="reacted", skip_reason=None,
                           consumed_gu=consumed, aura_gu_before=aura_before,
                           aura_gu_after=aura_after,
                           trigger_gu_before=before, trigger_gu_after=remaining)
                event_id = row["stable_event_id"] + f":reaction:{index}"
                run_id = row["run_id"] + f":reaction:{index}"
                if is_special:
                    attempt.update(
                        allowed=True,
                        reason="special_replacement_bypasses_crystallize_gcd",
                        next_reaction_t=self.crystallize_state.next_reaction_time/TICKS,
                    )
                else:
                    trigger_sample = self._require_crystallize_trigger_sample(row)
                    committed = self.crystallize_state.commit(
                        time=self.time, event_id=event_id, run_id=run_id,
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
            publish(reactions)
            if not reactions:
                row["outcome"] = "crystallize_gcd_blocked"
            return

        opposite = ({"hydro": "cryo", "cryo": "hydro"}.get(element))
        if opposite and opposite in state.auras:
            if state.freezable:
                raise ElementalPending(
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
            return

        electro_opposite = {"hydro": "electro", "electro": "hydro"}.get(element)
        if electro_opposite and electro_opposite in state.auras:
            unrelated = sorted(name for name in state.auras
                               if name not in {"hydro", "electro"})
            if unrelated:
                route(("hydro", "cryo"))
                raise ElementalPending(
                    "unsupported_reaction",
                    "水雷反应与额外元素底的优先级尚未接入星月事务。",
                    row["event_id"], {"coexisting_auras": unrelated,
                                      "reaction_priority": copy.deepcopy(
                                          row["reaction_priority"])})
            selected = route((electro_opposite,))
            aura = selected[0]
            proposal = self._multi_proposal(
                row, "electro_charged", electro_opposite, 0.0, gu,
                aura=aura, trigger_gu_before=float(gu),
                trigger_gu_after=float(gu))
            reaction = self._route_special_proposal(proposal, row)
            update = state.add_or_refresh(
                element, gu, row["event_id"], row["character"])
            route_step(0, status="reacted", skip_reason=None,
                       consumed_gu=0.0,
                       aura_gu_before=aura["remaining_gu"],
                       aura_gu_after=aura["remaining_gu"],
                       trigger_gu_before=float(gu), trigger_gu_after=float(gu))
            reaction.update(coexisting_aura_update=update,
                            initial_gauge_semantics="retain_hydro_electro_dual_aura")
            publish([reaction])
            return

        superconduct_opposite = {"cryo": "electro", "electro": "cryo"}.get(element)
        if superconduct_opposite and superconduct_opposite in state.auras:
            unrelated = sorted(name for name in state.auras
                               if name not in {"cryo", "electro"})
            if unrelated:
                raise ElementalPending(
                    "unsupported_reaction",
                    "冰雷反应与额外元素底的优先级尚未接入星月事务。",
                    row["event_id"], {"coexisting_auras": unrelated})
            selected = route((superconduct_opposite,))
            aura = selected[0]
            aura_before = aura["remaining_gu"]
            consumed = min(aura_before, float(gu))
            trigger_after = max(0.0, float(gu)-consumed)
            proposal = self._multi_proposal(
                row, "superconduct", superconduct_opposite, consumed, gu,
                aura=aura, trigger_gu_before=float(gu),
                trigger_gu_after=trigger_after)
            reaction = self._route_special_proposal(proposal, row)
            actual = state.consume(superconduct_opposite, consumed)
            if abs(actual-consumed) > _EPS:
                raise RuntimeError("超导提案与元素底提交不一致。")
            route_step(0, status="reacted", skip_reason=None,
                       consumed_gu=consumed,
                       aura_gu_before=aura_before,
                       aura_gu_after=max(0.0, aura_before-consumed),
                       trigger_gu_before=float(gu),
                       trigger_gu_after=trigger_after)
            publish([reaction])
            return

        bloom_opposite = {"hydro": "dendro", "dendro": "hydro"}.get(element)
        if bloom_opposite and bloom_opposite in state.auras:
            unrelated = sorted(name for name in state.auras
                               if name not in {"hydro", "dendro"})
            if unrelated:
                raise ElementalPending(
                    "unsupported_reaction",
                    "水草反应与额外元素底的优先级尚未接入星月事务。",
                    row["event_id"], {"coexisting_auras": unrelated})
            selected = route((bloom_opposite,))
            aura = selected[0]
            cost = 2.0 if (bloom_opposite, element) == ("hydro", "dendro") else .5
            aura_before = aura["remaining_gu"]
            consumed = min(aura_before, float(gu)*cost)
            trigger_after = max(0.0, float(gu)-consumed/cost)
            proposal = self._multi_proposal(
                row, "bloom", bloom_opposite, consumed, gu,
                aura=aura, trigger_gu_before=float(gu),
                trigger_gu_after=trigger_after)
            reaction = self._route_special_proposal(proposal, row)
            actual = state.consume(bloom_opposite, consumed)
            if abs(actual-consumed) > _EPS:
                raise RuntimeError("绽放提案与元素底提交不一致。")
            route_step(0, status="reacted", skip_reason=None,
                       consumed_gu=consumed,
                       aura_gu_before=aura_before,
                       aura_gu_after=max(0.0, aura_before-consumed),
                       trigger_gu_before=float(gu),
                       trigger_gu_after=trigger_after)
            core = self._create_bloom_core(reaction, row)
            if core:
                row["bloom_core_created"] = copy.deepcopy(core["core"])
            publish([reaction])
            return

        compatible = {
            "pyro": ("hydro", "cryo"),
            "hydro": ("pyro",),
            "cryo": ("pyro",),
            "electro": (),
            "dendro": (),
        }[element]
        selected = route(compatible)
        if selected:
            remaining = float(gu)
            reactions = []
            for index, aura in enumerate(selected):
                if remaining <= _EPS:
                    skip_remaining(index, "trigger_exhausted", remaining)
                    break
                aura_element = aura["element"]
                aura_owner = aura.get("source_owner")
                kind, factor, cost = _REACTIONS[(aura_element, element)]
                aura_before = aura["remaining_gu"]
                before = remaining
                consumed = state.consume(aura_element, remaining * cost)
                remaining = max(0.0, remaining - consumed / cost)
                route_step(index, status="reacted", skip_reason=None,
                           consumed_gu=consumed, aura_gu_before=aura_before,
                           aura_gu_after=max(0.0, aura_before-consumed),
                           trigger_gu_before=before, trigger_gu_after=remaining)
                reaction = self._multi_reaction(
                    row, kind, aura_element, consumed, gu, index=index,
                    trigger_gu_before=before, trigger_gu_after=remaining,
                    aura_owner=aura_owner)
                reaction["factor"] = factor
                reactions.append(reaction)
            publish(reactions)
            return

        incompatible = [aura for aura in state.auras if aura != element]
        if incompatible:
            raise ElementalPending(
                "unsupported_reaction",
                "v4尚未实现该元素组合，未假装无反应或允许非法共存。",
                row["event_id"], {"aura_elements": sorted(incompatible),
                                  "trigger_element": element})
        row["outcome"] = state.add_or_refresh(element, gu, row["event_id"], row["character"])


def build_reference_multi_resolver_v4(namespace: dict[str, Any]) -> FunctionType:
    source = _canonical_apply_multi_v4
    clone = FunctionType(source.__code__, namespace, "_apply_multi", source.__defaults__, source.__closure__)
    clone.__kwdefaults__ = copy.deepcopy(source.__kwdefaults__)
    clone.__annotations__ = copy.deepcopy(source.__annotations__)
    return clone

