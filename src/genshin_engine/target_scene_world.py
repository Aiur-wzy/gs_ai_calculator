"""Finite scene-only entry points for the shared elemental world."""

from __future__ import annotations

import copy

from genshin_engine.elemental_world import (
    _ACTIVE_ATOMIC_WORLD,
    ElementalWorldPending,
    ElementalWorldRuntime,
)


class StaticSceneWorld(ElementalWorldRuntime):
    def _advance_scene(self, time: int) -> None:
        """Advance a registered static scene; special deadlines use the audited world batch."""
        if type(time) is not int or time < self.time:
            raise ValueError("世界元素状态时间必须是单调非负整数微秒。")
        if self._transaction_depth or _ACTIVE_ATOMIC_WORLD.get() is not None:
            raise RuntimeError("场景时钟不能在其他世界事务内推进。")
        runtime = self.special_runtime
        deadline = runtime.next_deadline() if runtime else None
        if deadline is not None and deadline <= time:
            self.advance(time)
            return
        self._assert_world_clock_synchronized()
        before = [(target, target._transaction_snapshot(), target.generated_events)
                  for target in self._targets.values()]
        special_before = runtime.transaction_snapshot() if runtime else None
        clock = self.time
        try:
            generated = self._advance_targets_to(time)
            if runtime:
                unexpected = runtime.advance(time)
                deadline = runtime.next_deadline()
                if unexpected or (deadline is not None and deadline <= time):
                    raise ElementalWorldPending(
                        "world_scene_deadline_unscheduled", "场景出现了未排程的星月截止事件。")
            self.time = time
            self._assert_world_clock_synchronized()
            if generated:
                self.publish_generated_events(generated)
        except BaseException:
            self.time = clock
            for target, saved, pending in before:
                target.generated_events = pending
                target._restore_transaction(saved)
            if runtime:
                runtime.restore_transaction(special_before)
            raise

    def _attempt_scene_local(self, arguments: dict) -> dict:
        """Commit one independent local contact with rollback on publication failure."""
        if self._transaction_depth or _ACTIVE_ATOMIC_WORLD.get() is not None:
            raise RuntimeError("场景接触不能在其他世界事务内提交。")
        self._assert_world_clock_synchronized()
        if arguments["time"] != self.time:
            raise ValueError("场景接触必须在协调器已排定的时间点提交。")
        target = self.get_target(arguments["application"]["target_id"])
        before = target._transaction_snapshot()
        counters = copy.deepcopy((target.icd, target.counter_bindings, target.icd_rules))
        pending = target.generated_events
        try:
            row = target.attempt(**arguments)
            generated = target.drain_generated_events()
            self._assert_world_clock_synchronized()
            if generated:
                self.publish_generated_events(generated)
            return row
        except BaseException:
            target.generated_events = pending
            target._restore_transaction(before)
            target.icd, target.counter_bindings, target.icd_rules = counters
            raise
