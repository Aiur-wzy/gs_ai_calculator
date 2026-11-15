"""Pure compound authoring, with explicit rather than guessed time relations."""
from __future__ import annotations

import copy
import math
import re

from .equipment.schema import PlanError
from .input_editing import at, patch_input, resolve_operation, tokens

TIME_FIELDS = frozenset({"t", "start", "end", "occupation_end", "switch_allowed_at", "credit_at"})


def _invalid(message, **details):
    raise PlanError(message, "authoring_fragment_invalid", details)


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _batch(plan, request, report):
    from .authoring_fragments import build_fragment

    payload = request.get("payload", {})
    steps = payload.get("steps")
    if set(payload) != {"steps"} or not isinstance(steps, list) or not 1 <= len(steps) <= 128:
        _invalid("batch需要1..128个明确步骤；步骤失败时不会返回可提交的部分修改。", path="payload.steps")
    seen, gaps = set(), []
    for index, step in enumerate(steps):
        if (not isinstance(step, dict) or set(step) != {"id", "kind", "payload"}
                or not isinstance(step.get("id"), str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,47}", step["id"]) is None
                or step["id"] in seen or not isinstance(step.get("payload"), dict)
                or not isinstance(step.get("kind"), str)
                or step["kind"] not in {"attack_bundle", "mechanics_fragment", "materialization_entries",
                                            "special_damage", "binding_refresh", "action_move"}):
            _invalid("每步需要唯一ASCII id、单个配方kind和payload；不支持嵌套batch。", path=f"payload.steps/{index}")
        seen.add(step["id"])
        nested = {**request, "kind": step["kind"], "request_id": request["request_id"] + "." + step["id"],
                  "payload": step["payload"]}
        try:
            changed, found = build_fragment(plan, nested, composition_report=report)
        except PlanError as error:
            raise PlanError(str(error), error.reason,
                            {"step_id": step["id"], "step_index": index, "cause": error.details,
                             "writes": False, "recovery": "修正此步后重新预览整批；此前步骤尚未写入候选。"}) from error
        plan.update(changed)
        gaps.extend({"step_id": step["id"], **row} for row in found)
    return plan, gaps


def _move(plan, payload):
    if set(payload) - {"action_id", "start", "relative_bindings"}:
        _invalid("改时仅接受action_id、start和明确的relative_bindings。", path="payload")
    identity, start = payload.get("action_id"), payload.get("start")
    rows = (plan.get("rotation_model") or {}).get("actions", [])
    if not isinstance(rows, list):
        _invalid("现有动作列表不是数组；请先用普通编辑修复结构。", path="/rotation_model/actions")
    matches = [(i, row) for i, row in enumerate(rows) if isinstance(row, dict) and row.get("id") == identity]
    if not isinstance(identity, str) or not identity or len(matches) != 1 or not _number(start) or start < 0:
        _invalid("需要唯一的现有动作ID及非负有限start。", action_id=identity, matches=len(matches), path="payload.start")
    index, action = matches[0]
    if not _number(action.get("start")):
        _invalid("原动作没有明确start，不能推算相对时差；请直接填写动作。", action_id=identity)
    delta = start - action["start"]
    bindings = payload.get("relative_bindings", [])
    if not isinstance(bindings, list) or len(bindings) > 1024:
        _invalid("relative_bindings须为至多1024条明确字段绑定。", path="payload.relative_bindings")
    operations, paths = [], set()

    def shift(pointer):
        if pointer in paths:
            _invalid("同一时间字段被绑定两次，未重复平移。", path=pointer)
        value = at(plan, tokens(pointer))
        if not _number(value) or not _number(value + delta) or value + delta < 0:
            _invalid("平移需要已有有限时间且结果非负。", path=pointer, before=value, delta=delta)
        operations.append({"op": "replace", "path": pointer, "value": value + delta})
        paths.add(pointer)

    for field in ("start", "occupation_end", "switch_allowed_at"):
        if field in action:
            shift(f"/rotation_model/actions/{index}/{field}")
    for bi, binding in enumerate(bindings):
        if (not isinstance(binding, dict) or set(binding) != {"collection", "id", "fields"}
                or not isinstance(binding.get("fields"), list) or not binding["fields"]
                or any(not isinstance(field, str) or field not in TIME_FIELDS for field in binding["fields"])):
            _invalid("每项绑定需collection、id、fields；fields仅可指明现有时间字段。", path=f"payload.relative_bindings/{bi}")
        for field in binding["fields"]:
            resolved = resolve_operation(plan, {"op": "replace", "path": "/" + field,
                "selector": {"collection": binding["collection"], "id": binding["id"]}})
            shift(resolved["path"])
    # These are review hints, never invented links. Relative hit patterns and
    # action_delay triggers are deliberately unchanged and expand at execution.
    gaps = []
    for collection in ("/rotation_model/events", "/mechanics_model/hit_events", "/mechanics_model/special_reaction_events"):
        try:
            members = at(plan, tokens(collection))
        except PlanError:
            continue
        if not isinstance(members, list):
            continue
        for ri, row in enumerate(members):
            if not isinstance(row, dict) or row.get("action_id") != identity:
                continue
            for field in TIME_FIELDS:
                pointer = f"{collection}/{ri}/{field}"
                if field in row and pointer not in paths:
                    gaps.append({"code": "absolute_time_not_moved", "path": pointer, "action_id": identity,
                                 "message": "此条引用了动作但使用绝对时间，未声明联动，因此保持原值；请核对。"})
            if collection == "/mechanics_model/hit_events":
                for hi, hit in enumerate(plan.get("manual_template", {}).get("hits", [])):
                    pointer = f"/manual_template/hits/{hi}/t"
                    if isinstance(hit, dict) and hit.get("id") == row.get("hit_id") and pointer not in paths:
                        gaps.append({"code": "absolute_time_not_moved", "path": pointer, "action_id": identity,
                                     "hit_id": row.get("hit_id"),
                                     "message": "动作已绑定此命中，但命中是绝对时间；未声明relative_bindings，保持原值。"})
    gaps.append({"code": "unbound_times_not_inferred", "action_id": identity,
                 "message": "没有明确动作关系的命中、Buff和接球绝对时间不会猜测平移；请自行核对。相对动作规则将在重算时展开。"})
    return patch_input(plan, operations), gaps


def build_operation(original, request, *, composition_report=None):
    plan = copy.deepcopy(original)
    if request["kind"] == "batch":
        plan, gaps = _batch(plan, request, composition_report)
    else:
        plan, gaps = _move(plan, request.get("payload", {}))
    return {key: value for key, value in plan.items() if value != original.get(key)}, gaps
