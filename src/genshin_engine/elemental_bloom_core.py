"""Finite ordinary Dendro-core lifecycle used by Lunar Bloom replacement.

Lunar Bloom still creates ordinary Bloom cores.  Hyperbloom and Burgeon are
outside this iteration, but expiry and the five-core field limit must remain
real because they determine ordinary Bloom damage and later state.
"""
from __future__ import annotations

import copy

TICKS = 1_000_000
DEFAULT_CORE_LIFETIME_TICKS = 6 * TICKS
DEFAULT_FIELD_LIMIT = 5
ORDINARY_CORE_VARIANT = "ordinary"
ORDINARY_DAMAGE_RESET_TICKS = TICKS // 2
ORDINARY_DAMAGE_MAX_HITS = 2
MAX_REPLACEMENT_CORES = 256


class BloomCoreState:
    def __init__(self, *, lifetime_ticks: int = DEFAULT_CORE_LIFETIME_TICKS,
                 field_limit: int = DEFAULT_FIELD_LIMIT):
        if type(lifetime_ticks) is not int or lifetime_ticks <= 0:
            raise ValueError("草原核存续时间必须是正整数微秒。")
        if type(field_limit) is not int or field_limit <= 0:
            raise ValueError("草原核场上上限必须是正整数。")
        self.lifetime_ticks = lifetime_ticks
        self.field_limit = field_limit
        self.time = 0
        self.cores: list[dict] = []
        self.replacement_cores: list[dict] = []
        self.conversions: dict[tuple[str, str], dict] = {}
        self.revision = 0

    def restore(self, snapshot: BloomCoreState) -> None:
        """Restore a snapshot without replacing a shared battlefield object.

        A multi-target coordinator injects one ``BloomCoreState`` into every
        target runtime.  Rebinding one target to a deepcopy during rollback
        would split the five-core field limit, so restoration must mutate the
        original shared object in place.
        """
        if not isinstance(snapshot, BloomCoreState):
            raise TypeError("草原核战场快照类型无效。")
        self.__dict__.clear()
        self.__dict__.update(copy.deepcopy(snapshot.__dict__))

    def _damage_request(self, core: dict, time: int, reason: str) -> dict:
        return {
            "type": "ordinary_bloom_damage_request",
            "event_id": core["id"]+":detonate",
            "t": time/TICKS,
            "time_tick": time,
            "reaction_type": "bloom",
            "damage_system": "transformative",
            "element": "dendro",
            "target_id": core["target_id"],
            "area_id": core["area_id"],
            "owner": core["owner"],
            "parent_reaction_id": core["parent_reaction_id"],
            "contributor_ids": copy.deepcopy(core["contributor_ids"]),
            "core_variant": ORDINARY_CORE_VARIANT,
            "core_lifetime_seconds": self.lifetime_ticks/TICKS,
            "field_limit": self.field_limit,
            "damage_sequence": {
                "reset_seconds": ORDINARY_DAMAGE_RESET_TICKS/TICKS,
                "max_hits": ORDINARY_DAMAGE_MAX_HITS,
                "scope": ["owner", "target_id", "reaction_type"],
            },
            "reason": reason,
            "source": core["source"],
            "status": "ready_for_damage_projection",
        }

    def advance(self, time: int) -> list[dict]:
        if type(time) is not int or time < self.time:
            raise ValueError("草原核时间必须单调且为非负整数微秒。")
        due, kept = [], []
        for core in self.cores:
            if core["expires_at_tick"] <= time:
                due.append(self._damage_request(core, core["expires_at_tick"], "natural_expiry"))
            else:
                kept.append(core)
        expired = [core for core in self.replacement_cores if core["expires_at_tick"] <= time]
        replacements = [core for core in self.replacement_cores if core["expires_at_tick"] > time]
        conversions = {scope: value for scope, value in self.conversions.items() if value["ends_at_tick"] > time}
        if len(kept) != len(self.cores) or expired or len(conversions) != len(self.conversions):
            self.revision += 1
        self.cores = kept
        self.replacement_cores = replacements
        self.conversions = conversions
        self.time = time
        for core in expired:
            expiry = core["expires_at_tick"]
            due.append({"type": "bloom_replacement_core_expired", "event_id": core["id"] + ":expired",
                        "t": expiry/TICKS, "time_tick": expiry, "core": self._public(core),
                        "status": "executed"})
        return due

    def _replacement(self, core, conversion, time):
        expiry = time + conversion["lifetime_ticks"]
        return {**core, "core_variant": "replacement", "ordinary_core_id": core["id"],
                "conversion_event_id": conversion["event_id"], "conversion_owner": conversion["owner"],
                "id": f'{core["id"]}:replacement', "created_at": time/TICKS, "expires_at": expiry/TICKS,
                "expires_at_tick": expiry}

    def open_conversion(self, *, time, event_id, target_id, area_id, owner,
                        duration_ticks, lifetime_ticks, convert_existing):
        if self.time != time:
            raise ValueError("草原核转化必须在其元素状态已推进到的队列时刻执行。")
        if any(type(value) is not int or not 0 < value <= 10**15-time
               for value in (duration_ticks, lifetime_ticks)):
            raise ValueError("草原核转化范围和替换核有效期须为正且有界。")
        scope = target_id, area_id
        conversion = {"event_id": event_id, "owner": owner, "ends_at_tick": time + duration_ticks,
                      "lifetime_ticks": lifetime_ticks}
        old = [core for core in self.cores if (core["target_id"], core["area_id"]) == scope] if convert_existing else []
        if len(self.replacement_cores) + len(old) > MAX_REPLACEMENT_CORES:
            raise ValueError("替换草原核活动数量超过安全上限。")
        replacements = [self._replacement(core, conversion, time) for core in old]
        self.conversions[scope] = conversion
        if old:
            self.cores = [core for core in self.cores if core not in old]
            self.replacement_cores.extend(replacements)
        self.revision += 1
        return {"converted_core_ids": [core["id"] for core in old],
                "replacement_cores": [self._public(core) for core in replacements],
                "ends_at": conversion["ends_at_tick"]/TICKS}

    def absorb_replacements(self, *, time, target_id, area_id, owner, maximum):
        if self.time != time:
            raise ValueError("替换草原核吸收必须使用当前元素队列时刻。")
        selected = [core for core in self.replacement_cores if (core["target_id"], core["area_id"], core["conversion_owner"])
                    == (target_id, area_id, owner)][:maximum]
        if selected:
            self.replacement_cores = [core for core in self.replacement_cores if core not in selected]
            self.revision += 1
        return {"amount": len(selected), "absorbed_cores": [self._public(core) for core in selected]}

    def create(self, *, time: int, event_id: str, target_id: str, area_id: str,
               owner: str, parent_reaction_id: str, contributor_ids: list[str],
               source: str) -> dict:
        due = self.advance(time)
        values = (event_id, target_id, area_id, owner, parent_reaction_id, source)
        if not all(isinstance(value, str) and value for value in values):
            raise ValueError("草原核身份、区域、归属与来源必须完整。")
        if (not isinstance(contributor_ids, list) or not contributor_ids
                or any(not isinstance(value, str) or not value for value in contributor_ids)):
            raise ValueError("草原核必须保留至少一名有效附着贡献者。")
        if (any(core["id"] == event_id for core in self.cores)
                or any(core["ordinary_core_id"] == event_id for core in self.replacement_cores)):
            raise ValueError("草原核事件ID重复。")
        overflow = None
        conversion = self.conversions.get((target_id, area_id))
        if conversion is None and len(self.cores) >= self.field_limit:
            # The sixth core forces the oldest existing core to rupture before
            # the new object is inserted.  Stable creation order breaks ties.
            oldest = self.cores.pop(0)
            overflow = self._damage_request(oldest, time, "field_limit_oldest")
            due.append(overflow)
        core = {
            "id": event_id,
            "target_id": target_id,
            "area_id": area_id,
            "owner": owner,
            "parent_reaction_id": parent_reaction_id,
            "contributor_ids": list(dict.fromkeys(contributor_ids))[:4],
            "core_variant": ORDINARY_CORE_VARIANT,
            "source": source,
            "created_at": time/TICKS,
            "expires_at": (time+self.lifetime_ticks)/TICKS,
            "expires_at_tick": time+self.lifetime_ticks,
            "status": "active",
        }
        if conversion is None:
            self.cores.append(core)
        else:
            if len(self.replacement_cores) >= MAX_REPLACEMENT_CORES:
                raise ValueError("替换草原核活动数量超过安全上限。")
            core = self._replacement(core, conversion, time)
            self.replacement_cores.append(core)
        self.revision += 1
        return {
            "core": self._public(core),
            "generated_events": copy.deepcopy(due),
            "overflow_detonation": copy.deepcopy(overflow),
            "state": self.state(),
        }

    def next_deadline(self) -> int | None:
        return min([*(core["expires_at_tick"] for core in self.cores),
                    *(core["expires_at_tick"] for core in self.replacement_cores),
                    *(entry["ends_at_tick"] for entry in self.conversions.values())], default=None)

    @staticmethod
    def _public(core: dict) -> dict:
        row = copy.deepcopy(core)
        row.pop("expires_at_tick", None)
        return row

    def state(self) -> dict:
        state = {
            "t": self.time/TICKS,
            "cores": [self._public(core) for core in self.cores],
            "core_variant": ORDINARY_CORE_VARIANT,
            "field_limit": self.field_limit,
            "lifetime_seconds": self.lifetime_ticks/TICKS,
            "next_deadline": self.next_deadline()/TICKS if self.next_deadline() is not None else None,
            "revision": self.revision,
            "unsupported_core_reactions": ["hyperbloom", "burgeon"],
        }
        if self.replacement_cores or self.conversions:
            state["replacement_cores"] = [self._public(core) for core in self.replacement_cores]
            state["conversion_windows"] = [{"target_id": target, "area_id": area, **copy.deepcopy(window)}
                                           for (target, area), window in sorted(self.conversions.items())]
        return state
