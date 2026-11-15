"""Explicit experimental C/H/F reference state; no database or character logic."""
from __future__ import annotations

import copy
import math

TICKS = 1_000_000
EPS = 1e-12


class FreezeState:
    def __init__(self, initial):
        self.aura = copy.deepcopy(initial["ordinary_aura"])
        self.frozen = copy.deepcopy(initial["frozen"])
        self.history = float(initial["freeze_history_seconds"])
        self.time = 0.0
        self.revision = 0

    def state(self):
        return {"ordinary_aura": copy.deepcopy(self.aura), "frozen": copy.deepcopy(self.frozen),
                "freeze_history_seconds": self.history, "freeze_decay_gu_per_second": .4 + .1 * self.history}

    def _durations(self):
        pending = []
        if self.aura:
            pending.append((self.aura["remaining_gu"] / self.aura["decay_gu_per_second"], "ordinary_aura_expired"))
        if self.frozen:
            v = .4 + .1 * self.history
            # Rationalized root avoids cancellation for very small positive F.
            f = self.frozen["remaining_gu"]
            pending.append((2 * f / (math.sqrt(v*v + .2*f) + v), "frozen_expired"))
        elif self.history > 0:
            pending.append((self.history / 2, "freeze_history_recovered"))
        return pending

    def next_deadline(self, horizon_ticks=None):
        pending = self._durations()
        if not pending:
            return None
        dt, reason = min(pending)
        absolute = self.time + dt
        if not math.isfinite(absolute) or (horizon_ticks is not None and absolute > horizon_ticks/TICKS):
            return None  # No representable deadline inside this finite execution.
        tick = math.ceil(absolute * TICKS - 1e-8)
        return {"time": tick, "revision": self.revision, "reason": reason}

    def advance(self, ticks):
        end = ticks / TICKS
        if end + EPS < self.time:
            raise ValueError("冻结状态时间不能倒退。")
        transitions = []
        while True:
            pending = self._durations()
            duration, reason = min(pending) if pending else (math.inf, None)
            if self.time >= end - EPS and duration > EPS:
                break
            due = duration <= end - self.time + EPS
            dt = max(0., min(end - self.time, duration))
            before = self.state()
            if self.aura:
                self.aura["remaining_gu"] = max(0., self.aura["remaining_gu"] - dt*self.aura["decay_gu_per_second"])
            if self.frozen:
                self.frozen["remaining_gu"] = max(0., self.frozen["remaining_gu"] - (.4+.1*self.history)*dt - .05*dt*dt)
                self.history += dt
            else:
                self.history = max(0., self.history - 2*dt)
            self.time += dt
            if due:
                if reason == "ordinary_aura_expired":
                    self.aura = None
                elif reason == "frozen_expired":
                    self.frozen = None
                else:
                    self.history = 0.
                self.revision += 1
                transitions.append({"type": "elemental_state_transition", "reason": reason,
                                    "t": math.ceil(self.time*TICKS-1e-8)/TICKS,
                                    "before": before, "after": self.state()})
        self.time = end
        return transitions

    def apply(self, element, gu, source, source_owner=None):
        layers, reactions = [], []
        if element == "anemo":
            remaining = float(gu)
            for name, state in ((self.aura["element"] if self.aura else "ordinary", self.aura), ("frozen", self.frozen)):
                if not state or remaining <= EPS:
                    continue
                consumed = min(state["remaining_gu"], .5*remaining)
                if consumed <= EPS:
                    continue
                before = state["remaining_gu"]
                state["remaining_gu"] -= consumed
                remaining = max(0., remaining - 2*consumed)
                layer = {"layer": name, "reaction_element": "cryo" if name == "frozen" else name,
                         "before_gu": before, "after_gu": max(0., state["remaining_gu"]),
                          "consumed_gu": consumed, "remaining_trigger_gu": remaining,
                          **({"source_owner": state["source_owner"]} if state.get("source_owner") else {})}
                layers.append(layer)
                if state["remaining_gu"] <= EPS:
                    if name == "frozen":
                        self.frozen = None
                    else:
                        self.aura = None
            for reaction_element in ("hydro", "cryo"):
                parts = [p for p in layers if p["reaction_element"] == reaction_element]
                if parts:
                    reactions.append({"type": "swirl", "reaction_element": reaction_element,
                                      "aura_element": reaction_element, "consumed_gu": sum(p["consumed_gu"] for p in parts),
                                      "layers": copy.deepcopy(parts),
                                      "aura_owners": list(dict.fromkeys(
                                          p["source_owner"] for p in parts if p.get("source_owner")))})
        elif self.aura and self.aura["element"] != element:
            aura_owner = self.aura.get("source_owner")
            consumed = min(self.aura["remaining_gu"], gu)
            old = self.frozen["remaining_gu"] if self.frozen else 0.
            fresh = 2*consumed
            self.frozen = {"remaining_gu": max(old, fresh),
                           "source": source if fresh >= old else self.frozen["source"],
                           **({"source_owner": source_owner} if fresh >= old and source_owner else
                              ({"source_owner": self.frozen["source_owner"]}
                               if self.frozen and self.frozen.get("source_owner") else {}))}
            self.aura["remaining_gu"] -= consumed
            reactions.append({"type": "freeze", "reaction_element": "cryo", "aura_element": self.aura["element"],
                              "consumed_gu": consumed, "new_frozen_gu": fresh, "previous_frozen_gu": old,
                              "effective_frozen_gu": max(old, fresh),
                              **({"aura_owner": aura_owner} if aura_owner else {}),
                              **({"trigger_owner": source_owner} if source_owner else {})})
            if self.aura["remaining_gu"] <= EPS:
                self.aura = None
        else:
            gauge = .8*gu
            if self.aura:
                if gauge > self.aura["remaining_gu"]:
                    self.aura.update(remaining_gu=gauge, source=source)
                    if source_owner:
                        self.aura["source_owner"] = source_owner
            else:
                self.aura = {"element": element, "remaining_gu": gauge,
                             "decay_gu_per_second": gauge/(2.5*gu+7), "source": source,
                             **({"source_owner": source_owner} if source_owner else {})}
        self.revision += 1
        return reactions, layers
