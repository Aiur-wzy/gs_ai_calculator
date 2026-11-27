"""Static target registration and dispatch over the elemental world runtime."""

from __future__ import annotations

from genshin_engine.elemental_schema import ELEMENTAL_SCHEMA, MULTI_AURA_RULESET
from genshin_engine.rotation_port import TICKS, rotation_issue
from genshin_engine.rotation_schema import STRING, obj

_AREAS = {"type": "array", "items": STRING, "minItems": 1, "maxItems": 32, "uniqueItems": True}
TARGET_SCENE_SCHEMA = obj({
    "schema_version": {"const": 1},
    "primary_area_ids": _AREAS,
    "additional_targets": {
        "type": "array", "minItems": 1, "maxItems": 31,
        "items": obj({"target": ELEMENTAL_SCHEMA["properties"]["target"], "area_ids": _AREAS},
                     ["target", "area_ids"]),
    },
}, ["schema_version", "primary_area_ids", "additional_targets"])


def target_ids(mechanics):
    primary = mechanics.get("elemental_model", {}).get("target", {}).get("id")
    scene = mechanics.get("target_scene")
    additional = {row["target"]["id"] for row in scene["additional_targets"]} if scene else set()
    return ({primary} if primary is not None else set()) | additional


def area_ids(mechanics, target_id):
    scene = mechanics["target_scene"]
    if target_id == mechanics["elemental_model"]["target"]["id"]:
        return scene["primary_area_ids"]
    return next((row["area_ids"] for row in scene["additional_targets"]
                 if row["target"]["id"] == target_id), ())


def validate_scene(model):
    scene = model.get("target_scene")
    if scene is None:
        return []
    from genshin_engine.elemental import validate_elemental_model

    path = "mechanics_model.target_scene"
    elemental = model.get("elemental_model", {})
    if elemental.get("ruleset") != MULTI_AURA_RULESET:
        return [rotation_issue("target_scene_ruleset", "静态多目标需要 gauge-multi-aura-v4。", path)]
    if len(target_ids(model)) != len(scene["additional_targets"]) + 1:
        return [rotation_issue("target_scene_duplicate_target", "场景中的目标 ID 不可重复。", path)]
    issues = []
    if not elemental["target"]["id"].strip():
        issues.append(rotation_issue("target_scene_blank_id", "场景目标 ID 不能为空白。", "elemental_model.target.id"))
    if any(not area.strip() for area in scene["primary_area_ids"]):
        issues.append(rotation_issue("target_scene_blank_id", "场景区域 ID 不能为空白。", path+".primary_area_ids"))
    for index, row in enumerate(scene["additional_targets"]):
        target_path = f"{path}.additional_targets.{index}"
        if not row["target"]["id"].strip():
            issues.append(rotation_issue("target_scene_blank_id", "场景目标 ID 不能为空白。", target_path+".target.id"))
        if any(not area.strip() for area in row["area_ids"]):
            issues.append(rotation_issue("target_scene_blank_id", "场景区域 ID 不能为空白。", target_path+".area_ids"))
        for issue in validate_elemental_model({**elemental, "target": row["target"]}):
            element_path = issue["path"].removeprefix("mechanics_model.")
            if not element_path.startswith("elemental_model.target"):
                continue
            issues.append({**issue, "path": target_path+"."+element_path.removeprefix("elemental_model.")})
    for key in ("fixed_effect_sources", "target_status_events", "target_status_rules",
                "elemental_state_events", "special_damage_echoes"):
        if model.get(key):
            issues.append(rotation_issue(
                "target_scene_unsupported", "静态多目标尚未接入该类目标状态操作，请核对公开能力范围。",
                "mechanics_model." + key, severity="pending"))
    for kind, replacement in model.get("special_reaction_model", {}).get("replacements", {}).items():
        if replacement.get("enabled") and kind != "stellar_swirl":
            issues.append(rotation_issue(
                "target_scene_special_reaction_pending", "当前静态多目标仅开放星扩散替换。普通反应仍按各目标结算。",
                "mechanics_model.special_reaction_model.replacements." + kind, severity="pending"))
    for row in model.get("special_reaction_events", []):
        reaction_type = row.get("ability_profile", {}).get("parameters", {}).get("reaction_type")
        if row["kind"] != "direct_stellar_damage" or reaction_type != "stellar_swirl":
            issues.append(rotation_issue("target_scene_special_operation_pending",
                "此星月操作尚未接入静态多目标；能力星扩散直伤可以声明已登记的目标。",
                row["id"], severity="pending"))
    return issues


class TargetSceneRuntime:
    def __init__(self, model, special_runtime):
        from genshin_engine.target_scene_world import StaticSceneWorld

        self.world = StaticSceneWorld(special_runtime=special_runtime)
        elemental = model["elemental_model"]
        self.primary = self.world.register_target(
            elemental, area_ids=model["target_scene"]["primary_area_ids"])
        self.targets = {self.primary.target: self.primary}
        for row in model["target_scene"]["additional_targets"]:
            runtime = self.world.register_target({**elemental, "target": row["target"]},
                                                 area_ids=row["area_ids"])
            self.targets[runtime.target] = runtime
        self.trace = []
        self.cursor = 0
        self.input_window_state = None
        self.stellar_swirl_enabled = bool(model.get("special_reaction_model", {}).get(
            "replacements", {}).get("stellar_swirl", {}).get("enabled"))

    def advance(self, time):
        from genshin_engine.elemental_world import ElementalWorldPending

        if time == self.world.time:
            return
        before = self._trace_positions()
        try:
            self.world._advance_scene(time)
        except ElementalWorldPending as error:
            raise _as_mechanics_pending(error, "mechanics_model.target_scene") from error
        self._collect_trace(before)

    def _trace_positions(self):
        return {target_id: len(runtime.trace) for target_id, runtime in self.targets.items()}

    def _collect_trace(self, before, cycle=None, contact_order=None):
        rows = [row for target_id, count in sorted(before.items())
                for row in self.targets[target_id].trace[count:]]
        if contact_order is None:
            rows.sort(key=lambda row: row.get("t", self.world.time/TICKS))
        else:
            rows.sort(key=lambda row: (row.get("t", self.world.time/TICKS),
                                      contact_order.get(row.get("event_id"), len(contact_order))))
        if cycle is not None:
            for row in rows:
                row["cycle"] = cycle
        self.trace.extend(rows)

    def boundary(self, time, next_cycle):
        # World snapshots include absolute clock and selection identities. This
        # finite scene does not assert that those states repeat in later cycles.
        result = self.world.state()
        if next_cycle == 1:
            self.input_window_state = result
        return result

    def attach_result(self, result, resolved):
        targets = {
            hit["id"]: hit["extensions"]["target_scene"]["target_id"]
            for hit in resolved["hits"]
        }
        by_target = dict.fromkeys(self.targets, 0.0)
        for hit in [*result["hits"], *result.get("damage_report_audit_hits", [])]:
            hit["target_id"] = targets[hit["id"]]
        for hit in result["hits"]:
            by_target[hit["target_id"]] += hit["expected"]
        result["summary"]["by_target"] = by_target
        result["target_scene"] = {
            "contract": "static-target-scene-v1", "enemy_panel": "shared_template_enemy",
            "ordinary_swirl_scope": "origin_target", "ordinary_bloom_damage_scope": "declared_core_target",
            "input_window_state": self.input_window_state, "final_checked_state": self.world.state(),
        }

    def next_state_deadline(self, horizon):
        return min((due for runtime in self.targets.values()
                    if (due := runtime.next_state_deadline(horizon)) is not None),
                   key=lambda row: (row["time"], row.get("state_kind", ""), row["revision"]), default=None)

    def drain_generated_events(self):
        pulled = self.world.drain_generated_events(self.cursor)
        self.cursor = pulled["cursor"]
        return pulled["events"]

    def actual_application(self, receipt):
        target = self.targets[receipt["target_id"]]
        for row in reversed(target.trace):
            if row["type"] == "elemental_application" and row["event_id"] == receipt["event_id"]:
                return row
        from genshin_engine.elemental import ElementalPending

        raise ElementalPending("target_scene_application_receipt_missing",
            "找不到该目标已提交的实际附着。", receipt["event_id"], {"target_id": receipt["target_id"]})

    @staticmethod
    def _cryo_swirl(target, arguments):
        application = arguments["application"]
        if (target.profiles[application["profile_id"]]["element"] != "anemo"
                or not application["landed"] or not application.get("attempted", True)):
            return False
        preview = target.preview_copy().attempt(**arguments)
        return any(reaction["type"] == "swirl" and reaction["aura_element"] == "cryo"
                   for reaction in preview["reactions"])

    @staticmethod
    def _packet_child(arguments, index, size, cycle, *, world_parent=None):
        application = arguments["application"]
        parent = world_parent or application.get("atomic_parent_id", arguments["event_id"])
        # Keep first-window damage handles stable, and give later World
        # publications a unique occurrence without changing the action link.
        if cycle:
            parent = f"{cycle}:{parent}"
        return {
            **{key: arguments[key] for key in ("event_id", "action_id", "actor",
                                               "damage_element", "contact", "trigger_stat_sample")},
            "stable_event_id": arguments["event_id"] if cycle else arguments["stable_event_id"],
            "time_tick": arguments["time"], "packet_child_order": index,
            "application": {**application, "application_packet_size": size,
                            "atomic_parent_id": parent},
        }

    def attempt_packet(self, arguments, event_id, cycle, world_parent):
        from genshin_engine.elemental_world import ElementalWorldPending

        try:
            cryo, local = [], []
            for args in arguments:
                target = self.world.get_target(args["application"]["target_id"])
                (cryo if self._cryo_swirl(target, args) else local).append(args)
            if not cryo:
                return None
            before = self._trace_positions()
            # Target-specific contacts share the opening world state. Handle
            # the local children before the audited packet can detonate and
            # write delayed Cryo to targets outside its qualifying subset.
            receipts = [self.world._attempt_scene_local(args) for args in local]
            children = [self._packet_child(args, index, len(cryo), cycle, world_parent=world_parent)
                        for index, args in enumerate(cryo)]
            receipts.extend(self.world.attempt_stellar_swirl_packet(children)["application_receipts"])
            contact_order = {args["event_id"]: index for index, args in enumerate(arguments)}
            self._collect_trace(before, cycle, contact_order)
            return {row["event_id"]: row for row in receipts}
        except ElementalWorldPending as error:
            raise _as_mechanics_pending(error, event_id) from error

    def attempt(self, arguments, event_id, cycle):
        from genshin_engine.elemental_world import ElementalWorldPending

        application = arguments["application"]
        before = self._trace_positions()
        try:
            target = self.world.get_target(application["target_id"])
            runtime = self.world.special_runtime
            if runtime is not None and self.stellar_swirl_enabled and self._cryo_swirl(target, arguments):
                child = self._packet_child(arguments, 0, 1, cycle)
                receipt, = self.world.attempt_stellar_swirl_packet([child])["application_receipts"]
            else:
                receipt = self.world._attempt_scene_local(arguments)
            self._collect_trace(before, cycle)
            return receipt
        except ElementalWorldPending as error:
            raise _as_mechanics_pending(error, event_id) from error


def _as_mechanics_pending(error, path):
    from genshin_engine.elemental import ElementalPending

    reserved = {"code", "message", "severity", "path"}
    details = {key: value for key, value in error.issue.items() if key not in reserved}
    return ElementalPending(error.issue["code"], str(error), path, details)
