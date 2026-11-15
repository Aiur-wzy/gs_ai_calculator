"""Ordinary Crystallize reaction state.

This module deliberately owns only the reaction-wide gate and the shard
entities.  Aura selection and gauge consumption stay in the elemental state
machine so a blocked Crystallize can leave every aura untouched atomically.
"""
from __future__ import annotations

import copy
import json
import math
from functools import lru_cache
from importlib import resources

TICKS = 1_000_000
CRYSTALLIZE_GCD_TICKS = TICKS
DEFAULT_SHARD_LIFETIME_TICKS = 17_500_000
DEFAULT_SHIELD_LIFETIME_TICKS = 15 * TICKS
DEFAULT_OVERFLOW_CLEANUP_TICKS = 2 * TICKS
DEFAULT_STABLE_SHARD_LIMIT = 3
CRYSTALLIZABLE_ELEMENTS = ("pyro", "hydro", "cryo", "electro")


@lru_cache(maxsize=1)
def crystallize_profile() -> dict:
    """Load the reviewed compact reaction profile shipped with the runtime DB.

    The app must not depend on the multi-gigabyte upstream checkout at run time.
    The build therefore preserves only the reviewed level curve and mechanism
    provenance in ``genshin_db/resources/mechanics/reaction-profiles.json``.
    """
    path = resources.files("genshin_db").joinpath(
        "resources", "mechanics", "reaction-profiles.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    if (data.get("schema_version") != 1
            or data.get("profile_id") != "native.ordinary-crystallize.v1"
            or data.get("status") != "reviewed"):
        raise RuntimeError("运行时数据库中的普通结晶机制档案无效。")
    curve = data.get("shield_level_curve")
    if (not isinstance(curve, dict) or set(curve) != {str(i) for i in range(1, 101)}
            or any(isinstance(value, bool) or not isinstance(value, (int, float))
                   or not math.isfinite(value) or value <= 0 for value in curve.values())):
        raise RuntimeError("运行时数据库中的结晶护盾等级曲线不完整。")
    return data


def crystallize_shield_absorption(level: int, elemental_mastery: float) -> dict:
    """Return base and final ordinary-Crystallize shield absorption.

    ``level`` and ``elemental_mastery`` belong to the Geo character that
    triggered the reaction.  The pickup character does not become the owner.
    """
    if type(level) is not int or not 1 <= level <= 100:
        raise ValueError("普通结晶护盾只接受1至100级的触发角色。")
    if (isinstance(elemental_mastery, bool)
            or not isinstance(elemental_mastery, (int, float))
            or not math.isfinite(elemental_mastery) or elemental_mastery < 0):
        raise ValueError("普通结晶护盾元素精通必须是有限非负数。")
    profile = crystallize_profile()
    mechanics = profile["mechanics"]
    base = float(profile["shield_level_curve"][str(level)])
    bonus = 1 + (mechanics["shield_em_coefficient"] * elemental_mastery
                 / (elemental_mastery + mechanics["shield_em_divisor"]))
    return {
        "profile_id": profile["profile_id"],
        "profile_revision": profile["revision"],
        "level": level,
        "elemental_mastery": float(elemental_mastery),
        "base_absorption": base,
        "em_multiplier": bonus,
        "absorption": base * bonus,
    }


class CrystallizeWorldState:
    """Battlefield-wide ordinary-Crystallize shards and party shield.

    The one-second reaction gate belongs to a concrete target and therefore
    stays on :class:`CrystallizeState`.  Spawned shards and the one party
    shield are world entities: shards from different targets share the field
    limit and picking up any new shard replaces the same party shield.
    """

    def __init__(self, *,
                 shard_lifetime_ticks: int = DEFAULT_SHARD_LIFETIME_TICKS,
                 shield_lifetime_ticks: int = DEFAULT_SHIELD_LIFETIME_TICKS,
                 overflow_cleanup_ticks: int = DEFAULT_OVERFLOW_CLEANUP_TICKS,
                 stable_shard_limit: int = DEFAULT_STABLE_SHARD_LIMIT):
        if type(shard_lifetime_ticks) is not int or shard_lifetime_ticks <= 0:
            raise ValueError("结晶晶片存续时间必须是正整数微秒。")
        if type(shield_lifetime_ticks) is not int or shield_lifetime_ticks <= 0:
            raise ValueError("结晶护盾存续时间必须是正整数微秒。")
        if type(overflow_cleanup_ticks) is not int or overflow_cleanup_ticks < 0:
            raise ValueError("结晶晶片超量清理延迟必须是非负整数微秒。")
        if type(stable_shard_limit) is not int or stable_shard_limit < 1:
            raise ValueError("结晶晶片稳定上限必须是正整数。")
        self.shard_lifetime_ticks = shard_lifetime_ticks
        self.shield_lifetime_ticks = shield_lifetime_ticks
        self.overflow_cleanup_ticks = overflow_cleanup_ticks
        self.stable_shard_limit = stable_shard_limit
        self.shards: list[dict] = []
        self.active_shield: dict | None = None
        self.overflow_since: int | None = None
        self.revision = 0

    def restore(self, snapshot: CrystallizeWorldState) -> None:
        if not isinstance(snapshot, CrystallizeWorldState):
            raise TypeError("结晶战场快照类型无效。")
        self.__dict__.clear()
        self.__dict__.update(copy.deepcopy(snapshot.__dict__))

    def advance(self, time: int) -> list[dict]:
        if type(time) is not int or time < 0:
            raise ValueError("结晶状态时间必须是非负整数微秒。")
        transitions: list[dict] = []
        kept = []
        for shard in self.shards:
            if shard["expires_at_ticks"] <= time:
                transitions.append({
                    "type": "crystallize_shard_expired",
                    "t": shard["expires_at_ticks"] / TICKS,
                    "target_id": shard["target_id"],
                    "shard_id": shard["id"],
                    "element": shard["element"],
                    "reason": "lifetime",
                })
            else:
                kept.append(shard)
        self.shards = kept
        if (self.active_shield is not None
                and self.active_shield["expires_at_ticks"] <= time):
            shield = self.active_shield
            transitions.append({
                "type": "crystallize_shield_expired",
                "t": shield["expires_at_ticks"] / TICKS,
                "target_id": shield["source_target_id"],
                "character": shield["picked_up_by"],
                "element": shield["element"],
                "source_reaction_id": shield["source_reaction_id"],
            })
            self.active_shield = None
        if len(self.shards) <= self.stable_shard_limit:
            self.overflow_since = None
        elif (self.overflow_since is not None
              and time >= self.overflow_since + self.overflow_cleanup_ticks):
            removed = self.shards[:-self.stable_shard_limit]
            self.shards = self.shards[-self.stable_shard_limit:]
            cleanup_time = self.overflow_since + self.overflow_cleanup_ticks
            transitions.extend({
                "type": "crystallize_shard_expired",
                "t": cleanup_time / TICKS,
                "target_id": shard["target_id"],
                "shard_id": shard["id"],
                "element": shard["element"],
                "reason": "field_limit",
            } for shard in removed)
            self.overflow_since = None
        if transitions:
            transitions.sort(key=lambda row: (
                row["t"], row["type"],
                row.get("shard_id", row.get("character", ""))))
            self.revision += 1
        return transitions

    def next_deadline(self) -> int | None:
        candidates = [shard["expires_at_ticks"] for shard in self.shards]
        if self.active_shield is not None:
            candidates.append(self.active_shield["expires_at_ticks"])
        if (len(self.shards) > self.stable_shard_limit
                and self.overflow_since is not None):
            candidates.append(self.overflow_since + self.overflow_cleanup_ticks)
        return min(candidates, default=None)

    def spawn(self, shard: dict, time: int) -> None:
        if any(existing["id"] == shard["id"] for existing in self.shards):
            raise ValueError("结晶晶片身份重复。")
        self.shards.append(copy.deepcopy(shard))
        if len(self.shards) > self.stable_shard_limit and self.overflow_since is None:
            self.overflow_since = time
        self.revision += 1

    def pickup(self, *, shard_id: str, character: str, target_id: str,
               time: int, absorption: float | None = None) -> dict:
        transitions = self.advance(time)
        index = next((i for i, shard in enumerate(self.shards)
                      if shard["id"] == shard_id), None)
        if index is None:
            raise ValueError("结晶晶片不存在或已经失效。")
        shard = self.shards[index]
        if shard["target_id"] != target_id:
            raise ValueError("结晶晶片不属于声明的目标。")
        if not isinstance(character, str) or not character:
            raise ValueError("拾取结晶晶片的角色必须明确。")
        if (absorption is not None
                and (isinstance(absorption, bool)
                     or not isinstance(absorption, (int, float))
                     or not math.isfinite(absorption) or absorption < 0)):
            raise ValueError("结晶护盾吸收量必须是有限非负数或空值。")
        shard = self.shards.pop(index)
        previous = copy.deepcopy(self.active_shield)
        inherited_absorption = shard.get("shield_absorption_sample")
        if absorption is None and inherited_absorption is not None:
            absorption = inherited_absorption["absorption"]
        shield = {
            "scope": "party",
            "picked_up_by": character,
            # Kept as a compatibility display alias.  It is not a shield key.
            "character": character,
            "element": shard["element"],
            "source_owner": shard["owner"],
            "source_target_id": shard["target_id"],
            "source_reaction_id": shard["source_reaction_id"],
            "picked_up_at": time / TICKS,
            "expires_at": (time + self.shield_lifetime_ticks) / TICKS,
            "expires_at_ticks": time + self.shield_lifetime_ticks,
            "absorption": float(absorption) if absorption is not None else None,
            "absorption_sample": copy.deepcopy(inherited_absorption),
            "trigger_stat_sample": copy.deepcopy(
                shard.get("trigger_stat_sample")),
            "same_element_absorption_multiplier": 2.5,
        }
        self.active_shield = shield
        self.revision += 1
        if len(self.shards) <= self.stable_shard_limit:
            self.overflow_since = None
        return {
            "type": "crystallize_shard_pickup",
            "t": time / TICKS,
            "target_id": target_id,
            "shard_id": shard_id,
            "element": shard["element"],
            "owner": shard["owner"],
            "character": character,
            "shield_scope": "party",
            "source_reaction_id": shard["source_reaction_id"],
            "shield": CrystallizeState._public_shield(shield),
            "replaced_shield": (CrystallizeState._public_shield(previous)
                                if previous else None),
            "transitions": transitions,
        }

    def state(self) -> dict:
        active = (CrystallizeState._public_shield(self.active_shield)
                  if self.active_shield else None)
        # ``shields`` is a deprecated compatibility projection.  Execution
        # authority is exclusively ``active_shield``.
        shields = ({active["picked_up_by"]: copy.deepcopy(active)}
                   if active else {})
        return {
            "shards": [CrystallizeState._public_shard(row)
                       for row in self.shards],
            "active_shield": active,
            "shields": shields,
            "shield_scope": "party",
            "stable_shard_limit": self.stable_shard_limit,
            "overflow_cleanup_seconds": self.overflow_cleanup_ticks / TICKS,
            "shard_lifetime_seconds": self.shard_lifetime_ticks / TICKS,
            "shield_lifetime_seconds": self.shield_lifetime_ticks / TICKS,
            "revision": self.revision,
        }


class CrystallizeState:
    """Per-target one-second gate bound to shared battlefield state.

    The one-second gate is shared by every owner and aura element on this
    target.  A blocked attempt is not a reaction: it consumes no gauge and
    creates no shard.  Callers must invoke :meth:`admit` before mutating aura
    state, then :meth:`commit` only after the selected aura consumption has
    succeeded.
    """

    def __init__(self, target_id: str, *, world: CrystallizeWorldState | None = None,
                 shard_lifetime_ticks: int = DEFAULT_SHARD_LIFETIME_TICKS,
                 shield_lifetime_ticks: int = DEFAULT_SHIELD_LIFETIME_TICKS,
                 overflow_cleanup_ticks: int = DEFAULT_OVERFLOW_CLEANUP_TICKS,
                 stable_shard_limit: int = DEFAULT_STABLE_SHARD_LIMIT):
        if not isinstance(target_id, str) or not target_id:
            raise ValueError("结晶目标 ID 必须是非空字符串。")
        self.target_id = target_id
        if world is not None and not isinstance(world, CrystallizeWorldState):
            raise ValueError("结晶共享战场必须是CrystallizeWorldState。")
        self.world = world or CrystallizeWorldState(
            shard_lifetime_ticks=shard_lifetime_ticks,
            shield_lifetime_ticks=shield_lifetime_ticks,
            overflow_cleanup_ticks=overflow_cleanup_ticks,
            stable_shard_limit=stable_shard_limit,
        )
        self.next_reaction_time = 0
        self.revision = 0

    @property
    def shards(self):
        return self.world.shards

    @property
    def shields(self):
        active = self.world.active_shield
        return ({active["picked_up_by"]: active} if active else {})

    @property
    def active_shield(self):
        return self.world.active_shield

    @property
    def stable_shard_limit(self):
        return self.world.stable_shard_limit

    @property
    def overflow_cleanup_ticks(self):
        return self.world.overflow_cleanup_ticks

    @property
    def shard_lifetime_ticks(self):
        return self.world.shard_lifetime_ticks

    @property
    def shield_lifetime_ticks(self):
        return self.world.shield_lifetime_ticks

    def restore(self, snapshot: CrystallizeState) -> None:
        if not isinstance(snapshot, CrystallizeState):
            raise TypeError("结晶状态快照类型无效。")
        self.next_reaction_time = snapshot.next_reaction_time
        self.revision = snapshot.revision
        self.world.restore(snapshot.world)

    def copy(self) -> CrystallizeState:
        return copy.deepcopy(self)

    def advance(self, time: int) -> list[dict]:
        if type(time) is not int or time < 0:
            raise ValueError("结晶状态时间必须是非负整数微秒。")
        return self.world.advance(time)

    def admit(self, time: int) -> dict:
        transitions = self.advance(time)
        allowed = time >= self.next_reaction_time
        return {
            "allowed": allowed,
            "reason": "crystallize_ready" if allowed else "crystallize_gcd",
            "target_id": self.target_id,
            "t": time / TICKS,
            "next_reaction_t": self.next_reaction_time / TICKS,
            "transitions": transitions,
        }

    def next_deadline(self) -> int | None:
        """Return the earliest shard, shield, or overflow-cleanup boundary."""
        return self.world.next_deadline()

    def commit(self, *, time: int, event_id: str, run_id: str, action_id: str,
               owner: str, element: str, source: str, consumed_gu: float,
               trigger_gu: float, trigger_level: int | None = None,
               trigger_em: float | None = None,
               trigger_stat_sample: dict | None = None) -> dict:
        admission = self.admit(time)
        if not admission["allowed"]:
            raise ValueError("结晶仍在目标级 1 秒反应冷却内，不能提交反应。")
        if element not in CRYSTALLIZABLE_ELEMENTS:
            raise ValueError("普通结晶只支持火、水、冰、雷晶片。")
        if not all(isinstance(value, str) and value for value in
                   (event_id, run_id, action_id, owner, source)):
            raise ValueError("结晶反应身份与来源必须完整。")
        if (not isinstance(consumed_gu, (int, float)) or isinstance(consumed_gu, bool)
                or consumed_gu <= 0 or not isinstance(trigger_gu, (int, float))
                or isinstance(trigger_gu, bool) or trigger_gu <= 0):
            raise ValueError("结晶元素量必须是有限正数。")
        if (trigger_level is None) != (trigger_em is None):
            raise ValueError("结晶护盾取样必须同时提供触发角色等级与元素精通。")
        shield_sample = (crystallize_shield_absorption(trigger_level, trigger_em)
                         if trigger_level is not None else None)
        if (trigger_stat_sample is not None
                and (not isinstance(trigger_stat_sample, dict)
                     or trigger_stat_sample.get("level") != trigger_level
                     or trigger_stat_sample.get("em") != float(trigger_em))):
            raise ValueError("结晶触发属性取样与等级/精通参数不一致。")

        shard_id = event_id + ":shard"
        shard = {
            "id": shard_id,
            "target_id": self.target_id,
            "element": element,
            "owner": owner,
            "source_reaction_id": event_id,
            "spawned_at": time / TICKS,
            "expires_at": (time + self.shard_lifetime_ticks) / TICKS,
            "expires_at_ticks": time + self.shard_lifetime_ticks,
            "status": "available",
            "shield_absorption_sample": copy.deepcopy(shield_sample),
            "trigger_stat_sample": copy.deepcopy(trigger_stat_sample),
        }
        self.world.spawn(shard, time)
        self.next_reaction_time = time + CRYSTALLIZE_GCD_TICKS
        self.revision += 1
        reaction = {
            "id": event_id,
            "run_id": run_id,
            "parent_event_id": event_id.rsplit(":reaction", 1)[0],
            "action_id": action_id,
            "type": "crystallize",
            "reaction_element": element,
            "trigger_element": "geo",
            "trigger_gu": float(trigger_gu),
            "consumed_gu": float(consumed_gu),
            "owner": owner,
            "target_id": self.target_id,
            "source": source,
            "damage": False,
            "shard_id": shard_id,
            "gcd_seconds": CRYSTALLIZE_GCD_TICKS / TICKS,
        }
        return {"reaction": reaction, "shard": copy.deepcopy(shard),
                "next_reaction_t": self.next_reaction_time / TICKS,
                "transitions": admission["transitions"]}

    def pickup(self, *, shard_id: str, character: str, time: int,
               absorption: float | None = None) -> dict:
        return self.world.pickup(
            shard_id=shard_id, character=character,
            target_id=self.target_id, time=time, absorption=absorption)

    @staticmethod
    def _public_shard(shard: dict) -> dict:
        row = copy.deepcopy(shard)
        row.pop("expires_at_ticks", None)
        return row

    @staticmethod
    def _public_shield(shield: dict) -> dict:
        row = copy.deepcopy(shield)
        row.pop("expires_at_ticks", None)
        return row

    def state(self) -> dict:
        world = self.world.state()
        return {
            "target_id": self.target_id,
            "next_reaction_t": self.next_reaction_time / TICKS,
            **world,
            "target_gate_revision": self.revision,
            "revision": self.revision + self.world.revision,
        }
