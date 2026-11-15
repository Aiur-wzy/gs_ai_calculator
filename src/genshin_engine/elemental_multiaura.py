"""Finite ordinary multi-aura state for the versioned v4 ruleset.

This module owns state and deterministic layer selection only.  It deliberately
does not assign damage formulae or character-specific behaviour.  Callers must
still reject reaction pairs whose semantics have not been implemented.
"""
from __future__ import annotations

import copy
import hashlib
import json

from genshin_engine.elemental_freeze import FreezeState

TICKS = 1_000_000
EPS = 1e-12
AURA_ELEMENTS = ("pyro", "hydro", "cryo", "electro", "dendro")
# Fixed, versioned routes for every multi-layer state admitted by v4 today.
# A future electro-charged state can reuse the reviewed geo route without
# deriving priority from mutable gauge values.
REACTION_PRIORITY_ROUTES = {
    ("anemo", frozenset(("hydro", "cryo"))): ("hydro", "cryo"),
    ("geo", frozenset(("hydro", "cryo"))): ("hydro", "cryo"),
    ("pyro", frozenset(("hydro", "cryo"))): ("hydro", "cryo"),
    ("electro", frozenset(("hydro", "cryo"))): ("hydro", "cryo"),
    ("anemo", frozenset(("electro", "hydro"))): ("electro", "hydro"),
    ("geo", frozenset(("electro", "hydro"))): ("electro", "hydro"),
}


class MultiAuraState:
    """Independently decaying, bounded elemental layers on one target."""

    def __init__(self, initial_auras=(), *, freezable,
                 unfreezable_resolution="coexist",
                 resolution_origin="default_scenario",
                 legacy_mode=None):
        if type(freezable) is not bool:
            raise ValueError("多底模型必须显式声明目标是否可冻结。")
        if (not freezable
                and unfreezable_resolution not in ("coexist", "mutual_cancel")):
            raise ValueError("不可冻结处理必须是coexist或mutual_cancel。")
        if resolution_origin not in ("default_scenario", "manual_override",
                                     "legacy_mode_migration", "not_applicable"):
            raise ValueError("不可冻结处理来源无法审计。")
        self.auras = {aura["element"]: copy.deepcopy(aura) for aura in initial_auras}
        for aura in self.auras.values():
            owners = list(dict.fromkeys(aura.get("contributor_ids", [])))
            if not owners and aura.get("source_owner"):
                owners = [aura["source_owner"]]
            if len(owners) == 1:
                aura["contributor_allocations"] = [{
                    "owner": owners[0],
                    "remaining_gu": float(aura["remaining_gu"]),
                    "last_application_at": 0.0,
                    "source": aura["source"],
                }]
                aura["owner_residual_status"] = "tracked"
                aura["contributor_ids"] = owners
            else:
                # A flat multi-owner list cannot prove each owner's residual.
                # Keep ordinary aura execution available, while lunar
                # Electro will fail closed until real applications establish
                # an attributed ledger.
                aura["contributor_allocations"] = []
                aura["owner_residual_status"] = (
                    "unattributed_initial_aura" if not owners
                    else "ambiguous_initial_contributors")
                if owners:
                    aura["contributor_ids"] = owners[:4]
        self.freezable = freezable
        self.unfreezable_resolution = (None if self.freezable else unfreezable_resolution)
        self.resolution_origin = ("not_applicable" if self.freezable else resolution_origin)
        self.legacy_mode = legacy_mode
        contract = {
            "contract": "target-freeze-scenario-v1",
            "freezable": self.freezable,
            "unfreezable_resolution": self.unfreezable_resolution,
            "resolution_origin": self.resolution_origin,
            "legacy_mode": legacy_mode,
        }
        contract["fingerprint"] = hashlib.sha256(json.dumps(
            contract, ensure_ascii=False, sort_keys=True,
            separators=(",", ":")).encode()).hexdigest()
        self.freeze_contract = contract
        # A freezable v4 target uses the reviewed continuous FreezeState as a
        # child state machine.  One ordinary Hydro/Cryo layer remains visible
        # through ``auras`` for the ordinary multi-aura API; Frozen gauge and
        # history stay authoritative in the child.  An ambiguous H+C initial
        # declaration cannot establish contact order and is rejected/pended by
        # the model validator/runtime before this adapter is used.
        hydro_cryo = [copy.deepcopy(self.auras[element]) for element in ("hydro", "cryo")
                      if element in self.auras]
        self.freeze_state = None
        if self.freezable and len(hydro_cryo) <= 1:
            self.freeze_state = FreezeState({
                "ordinary_aura": hydro_cryo[0] if hydro_cryo else None,
                "frozen": None,
                "freeze_history_seconds": 0,
            })
            self._sync_from_freeze()
        self.time = 0.0

    def _sync_from_freeze(self):
        if self.freeze_state is None:
            return
        self.auras.pop("hydro", None)
        self.auras.pop("cryo", None)
        if self.freeze_state.aura:
            # One mutable ordinary layer, not two independently writable copies.
            # Synchronization occurs only at explicit state commits, never reads.
            aura = self.freeze_state.aura
            self.auras[aura["element"]] = aura

    def _commit_ordinary_aura(self, element):
        if self.freeze_state is not None and element in ("hydro", "cryo"):
            self.freeze_state.aura = self.auras.get(element)
            self.freeze_state.revision += 1

    def _reconcile_freeze_ownership(self, previous, incoming=None):
        """Preserve the attributed ordinary layer across child-state commits."""
        current = self.freeze_state.aura
        if current is None:
            return
        if previous and previous["element"] == current["element"]:
            if previous.get("owner_residual_status") != "tracked":
                return
            allocations = copy.deepcopy(previous.get("contributor_allocations", []))
            if incoming is not None and incoming[0] == current["element"]:
                _, raw_gu, source, owner = incoming
                if not owner:
                    current.update(contributor_allocations=[],
                                   owner_residual_status="unattributed_application")
                    return
                found = next((a for a in allocations if a["owner"] == owner), None)
                if found is None:
                    allocations.append({"owner": owner, "remaining_gu": self.application_gauge(raw_gu),
                                        "last_application_at": self.time, "source": source})
                else:
                    found.update(remaining_gu=max(found["remaining_gu"], self.application_gauge(raw_gu)),
                                 last_application_at=self.time, source=source)
            else:
                consumed = max(0.0, previous["remaining_gu"] - current["remaining_gu"])
                for allocation in allocations:
                    allocation["remaining_gu"] = max(0.0, allocation["remaining_gu"] - consumed)
            current.update(contributor_allocations=allocations, owner_residual_status="tracked")
            self._sync_owner_residual_projection(current)
        elif incoming is not None and incoming[0] == current["element"]:
            _, _, source, owner = incoming
            current.update(contributor_allocations=([
                {"owner": owner, "remaining_gu": current["remaining_gu"],
                 "last_application_at": self.time, "source": source}] if owner else []),
                owner_residual_status="tracked" if owner else "unattributed_application")
            if owner:
                self._sync_owner_residual_projection(current)

    def _freeze_snapshot(self):
        if self.freeze_state is None:
            return {
                "ordinary_aura": None,
                "frozen": None,
                "freeze_history_seconds": None,
                "freeze_decay_gu_per_second": None,
            }
        return self.freeze_state.state()

    def state(self):
        layers = [copy.deepcopy(self.auras[element]) for element in AURA_ELEMENTS
                  if element in self.auras]
        coexist = "hydro" in self.auras and "cryo" in self.auras
        freeze = self._freeze_snapshot()
        return {
            "auras": layers,
            # Capability and the unfreezable resolution are separate facts.
            # v4 does not pretend that persistent Frozen duration is proven
            # merely because hydro and cryo layers coexist.
            "freeze_capability": "freezable" if self.freezable else "unfreezable",
            "unfreezable_resolution": self.unfreezable_resolution,
            "unfreezable_resolution_origin": self.resolution_origin,
            "freeze_scenario_contract": copy.deepcopy(self.freeze_contract),
            "coexisting_hydro_cryo": coexist,
            "frozen": copy.deepcopy(freeze["frozen"]),
            "freeze_history_seconds": freeze["freeze_history_seconds"],
            "freeze_decay_gu_per_second": freeze["freeze_decay_gu_per_second"],
            "freeze_runtime": ("reviewed_freeze_state_v1" if self.freeze_state
                               else "initial_order_pending" if self.freezable
                               else "not_applicable"),
        }

    def advance(self, ticks):
        end = ticks / TICKS
        if end + EPS < self.time:
            raise ValueError("多元素底状态时间不能倒退。")
        elapsed = max(0.0, end - self.time)
        for element, aura in list(self.auras.items()):
            if self.freeze_state is not None and element in ("hydro", "cryo"):
                continue
            decay = elapsed * aura["decay_gu_per_second"]
            allocations = aura.get("contributor_allocations", [])
            if aura.get("owner_residual_status") == "tracked" and allocations:
                for allocation in allocations:
                    allocation["remaining_gu"] = max(
                        0.0, allocation["remaining_gu"]-decay)
                aura["contributor_allocations"] = [
                    allocation for allocation in allocations
                    if allocation["remaining_gu"] > EPS]
                self._sync_owner_residual_projection(aura)
            else:
                aura["remaining_gu"] -= decay
            if aura["remaining_gu"] <= EPS:
                del self.auras[element]
        previous = copy.deepcopy(self.freeze_state.aura) if self.freeze_state else None
        transitions = self.freeze_state.advance(ticks) if self.freeze_state else []
        if self.freeze_state is not None:
            self._reconcile_freeze_ownership(previous)
        self._sync_from_freeze()
        self.time = end
        return transitions

    def next_deadline(self, horizon_ticks=None):
        return self.freeze_state.next_deadline(horizon_ticks) if self.freeze_state else None

    def apply_freezable(self, element, raw_gu, source, source_owner=None):
        """Apply Hydro/Cryo through the reviewed Frozen/history state machine."""
        if self.freeze_state is None or element not in ("hydro", "cryo"):
            raise ValueError("可冻结适配器只接收已建立的水/冰状态。")
        before = self.freeze_state.state()
        reactions, layers = self.freeze_state.apply(
            element, raw_gu, source, source_owner=source_owner)
        self._reconcile_freeze_ownership(
            before["ordinary_aura"], (element, raw_gu, source, source_owner))
        self._sync_from_freeze()
        after = self.freeze_state.state()
        if reactions:
            previous = reactions[0]["previous_frozen_gu"]
            operation = "frozen_created" if previous <= EPS else "frozen_refreshed"
        elif before["ordinary_aura"] is None:
            operation = "ordinary_aura_applied"
        else:
            operation = "ordinary_aura_refreshed"
        receipt = {
            "type": "freezable_freeze_update",
            "operation": operation,
            "trigger_element": element,
            "trigger_gu": raw_gu,
            "before": before,
            "after": after,
            "history_preserved": before["freeze_history_seconds"] == after["freeze_history_seconds"],
            "retained_ordinary_aura": copy.deepcopy(after["ordinary_aura"]),
            "contract_fingerprint": self.freeze_contract["fingerprint"],
        }
        return reactions, layers, receipt

    def apply_freezable_anemo(self, raw_gu, source, source_owner=None):
        """Consume the underlying ordinary layer before the Frozen layer."""
        if self.freeze_state is None:
            raise ValueError("可冻结风接触需要已建立的冻结子状态。")
        before = self.freeze_state.state()
        planned = []
        if before["ordinary_aura"]:
            planned.append((before["ordinary_aura"]["element"],
                            before["ordinary_aura"]["element"]))
        if before["frozen"]:
            planned.append(("frozen", "cryo"))
        reactions, layers = self.freeze_state.apply(
            "anemo", raw_gu, source, source_owner=source_owner)
        self._reconcile_freeze_ownership(before["ordinary_aura"])
        self._sync_from_freeze()
        after = self.freeze_state.state()
        actual = {layer["layer"]: layer for layer in layers}
        remaining = float(raw_gu)
        candidates = []
        for index, (layer_name, reaction_element) in enumerate(planned):
            layer = actual.get(layer_name)
            if layer:
                candidate = {
                    "rank": index + 1, "layer": layer_name,
                    "reaction_element": reaction_element,
                    "status": "reacted", "skip_reason": None,
                    "aura_gu_before": layer["before_gu"],
                    "aura_gu_after": layer["after_gu"],
                    "consumed_gu": layer["consumed_gu"],
                    "trigger_gu_before": remaining,
                    "trigger_gu_after": layer["remaining_trigger_gu"],
                }
                remaining = layer["remaining_trigger_gu"]
            else:
                state = before["frozen"] if layer_name == "frozen" else before["ordinary_aura"]
                candidate = {
                    "rank": index + 1, "layer": layer_name,
                    "reaction_element": reaction_element,
                    "status": "skipped", "skip_reason": "trigger_exhausted",
                    "aura_gu_before": state["remaining_gu"],
                    "aura_gu_after": state["remaining_gu"],
                    "consumed_gu": 0.0,
                    "trigger_gu_before": remaining,
                    "trigger_gu_after": remaining,
                }
            candidates.append(candidate)
        return reactions, layers, {
            "type": "freezable_anemo_layers",
            "policy": "ordinary_aura_then_frozen_v1",
            "route": [f"{layer}:{element}" for layer, element in planned],
            "ranked_candidates": candidates,
            "before": before,
            "after": after,
            "contract_fingerprint": self.freeze_contract["fingerprint"],
        }

    def reaction_route(self, trigger_element, elements):
        """Return a reviewed fixed route and its audit receipt.

        Gauge magnitude never selects the first layer.  Unknown multi-layer
        combinations return ``None`` so the caller can stop as pending.
        """
        present = frozenset(element for element in elements if element in self.auras)
        if not present:
            route = ()
            route_id = "empty"
        elif len(present) == 1:
            route = tuple(present)
            route_id = "single-layer"
        else:
            route = REACTION_PRIORITY_ROUTES.get((trigger_element, present))
            route_id = f"{trigger_element}:" + ">".join(route or ())
        receipt = {
            "policy": "fixed_reaction_priority_v1",
            "route_id": route_id,
            "trigger_element": trigger_element,
            "present_elements": sorted(present),
            "route": list(route or ()),
            "ranked_candidates": [
                {"rank": rank, "element": element,
                 "remaining_gu": self.auras[element]["remaining_gu"],
                 "status": "candidate", "skip_reason": None}
                for rank, element in enumerate(route or (), 1)
            ],
            "gauge_independent": True,
            "freeze_scenario_fingerprint": self.freeze_contract["fingerprint"],
            "scenario_origin": self.resolution_origin,
        }
        return ([self.auras[element] for element in route] if route is not None else None,
                receipt)

    @staticmethod
    def application_gauge(raw_gu):
        return .8 * raw_gu

    @staticmethod
    def decay_rate(raw_gu):
        gauge = MultiAuraState.application_gauge(raw_gu)
        return gauge / (2.5 * raw_gu + 7)

    @staticmethod
    def _sync_owner_residual_projection(aura):
        allocations = sorted(
            (allocation for allocation in aura.get("contributor_allocations", [])
             if allocation["remaining_gu"] > EPS),
            key=lambda item: (item["last_application_at"], item["owner"]))
        aura["contributor_allocations"] = allocations
        if not allocations:
            aura["remaining_gu"] = 0.0
            aura["contributor_ids"] = []
            aura.pop("source_owner", None)
            return
        strongest = max(
            allocations,
            key=lambda item: (item["remaining_gu"], item["last_application_at"],
                              item["owner"]))
        # Same-element per-owner residuals overlap.  The visible aura gauge is
        # their maximum, never their sum.
        aura["remaining_gu"] = strongest["remaining_gu"]
        aura["source_owner"] = strongest["owner"]
        aura["source"] = strongest["source"]
        aura["contributor_ids"] = [
            item["owner"] for item in allocations][:4]

    def contributor_allocations(self, element):
        aura = self.auras.get(element)
        if aura is None:
            return []
        if aura.get("owner_residual_status") != "tracked":
            return None
        return copy.deepcopy(aura.get("contributor_allocations", []))

    def add_or_refresh(self, element, raw_gu, source, source_owner=None):
        if (self.freeze_state is not None and element in ("hydro", "cryo")
                and ("cryo" if element == "hydro" else "hydro") in self.auras):
            raise ValueError("可冻结目标的水冰接触须通过冻结状态操作提交。")
        gauge = self.application_gauge(raw_gu)
        rate = self.decay_rate(raw_gu)
        aura = self.auras.get(element)
        if aura is None:
            self.auras[element] = {"element": element, "remaining_gu": gauge,
                                   "decay_gu_per_second": rate, "source": source,
                                   **({"source_owner": source_owner,
                                      "contributor_ids": [source_owner],
                                      "contributor_allocations": [{
                                          "owner": source_owner,
                                          "remaining_gu": gauge,
                                          "last_application_at": self.time,
                                          "source": source,
                                      }],
                                      "owner_residual_status": "tracked"}
                                      if source_owner else {
                                          "contributor_allocations": [],
                                          "owner_residual_status": "unattributed_application"})}
            self._commit_ordinary_aura(element)
            return "aura_applied"
        if source_owner and aura.get("owner_residual_status") == "tracked":
            allocation = next((item for item in aura["contributor_allocations"]
                               if item["owner"] == source_owner), None)
            if allocation is None:
                aura["contributor_allocations"].append({
                    "owner": source_owner, "remaining_gu": gauge,
                    "last_application_at": self.time, "source": source,
                })
            else:
                # Reuse the reviewed same-element refresh rule per owner.
                # We do not add gauges or invent a second decay clock.  Even
                # a weaker successful application refreshes the independent
                # five-second contributor recency fact.
                allocation["remaining_gu"] = max(
                    allocation["remaining_gu"], gauge)
                allocation["last_application_at"] = self.time
                allocation["source"] = source
            self._sync_owner_residual_projection(aura)
        else:
            if source_owner:
                aura["owner_residual_status"] = "ambiguous_prior_residual"
                aura["contributor_ids"] = list(dict.fromkeys(
                    [*aura.get("contributor_ids", []), source_owner]))[:4]
            if gauge > aura["remaining_gu"]:
                aura["remaining_gu"] = gauge
                aura["source"] = source
                if source_owner:
                    aura["source_owner"] = source_owner
        # All owners of this element share the decay basis established by the
        # first active application.  A refresh never creates another clock.
        self._commit_ordinary_aura(element)
        return "aura_refreshed"

    def consume(self, element, amount):
        aura = self.auras[element]
        consumed = min(aura["remaining_gu"], amount)
        allocations = aura.get("contributor_allocations", [])
        if aura.get("owner_residual_status") == "tracked" and allocations:
            for allocation in allocations:
                allocation["remaining_gu"] = max(
                    0.0, allocation["remaining_gu"]-consumed)
            aura["contributor_allocations"] = [
                allocation for allocation in allocations
                if allocation["remaining_gu"] > EPS]
            self._sync_owner_residual_projection(aura)
        else:
            aura["remaining_gu"] -= consumed
        if aura["remaining_gu"] <= EPS:
            del self.auras[element]
        self._commit_ordinary_aura(element)
        return consumed

    def cancel_hydro_cryo(self, incoming_element, raw_gu, source, source_owner=None):
        """Apply the explicit non-freezable hydro/cryo mutual-cancel mode."""
        opposite = "cryo" if incoming_element == "hydro" else "hydro"
        incoming = self.application_gauge(raw_gu)
        old = copy.deepcopy(self.auras[opposite])
        existing = old["remaining_gu"]
        consumed = min(existing, incoming)
        remaining = incoming - existing
        survivor = None
        if remaining > EPS:
            del self.auras[opposite]
            self.auras[incoming_element] = {
                "element": incoming_element,
                "remaining_gu": remaining,
                "decay_gu_per_second": self.decay_rate(raw_gu),
                "source": source,
                **({"source_owner": source_owner,
                    "contributor_ids": [source_owner],
                    "contributor_allocations": [{
                        "owner": source_owner, "remaining_gu": remaining,
                        "last_application_at": self.time, "source": source,
                    }], "owner_residual_status": "tracked"}
                   if source_owner else {
                       "contributor_allocations": [],
                       "owner_residual_status": "unattributed_application"}),
            }
            survivor = incoming_element
        elif existing - incoming > EPS:
            self.consume(opposite, incoming)
            survivor = opposite
        else:
            del self.auras[opposite]
        return consumed, survivor
