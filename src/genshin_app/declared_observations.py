"""Small declarative, read-only comparisons of saved evidence.

No expression evaluator, inferred game facts or acceptance policy lives here.
An author chooses the evidence, conditions and expected groups. Missing evidence
remains unknown, including empty traces from an incomplete execution.
"""
from __future__ import annotations

import math
from collections import defaultdict
from itertools import pairwise

from .equipment.files import json_bytes
from .equipment.schema import PlanError

CONTRACT = "genshin-declared-observations-v1"
OPS = frozenset({"eq", "ne", "lt", "lte", "gt", "gte", "in", "exists"})
KINDS = frozenset({"count", "compare", "min_gap", "unique"})
MISSING = object()


def observation_contract():
    return {"contract": CONTRACT, "authority": "advisory_only", "acceptance_effect": "none",
        "request": {"observations": "0..256 {id,source:{resource,pointer},where?,group_by?,expected_groups?,check,note?,source_ref?}",
            "resources": ["result", "plan", "report", "effects"],
            "where": "[{path,op,value}]; op eq/ne/lt/lte/gt/gte/in/exists; missing field is unknown, not false",
            "group_by": "optional JSON pointer fields on each saved row",
            "expected_groups": "optional arrays of expected group values; absent groups are never silently dropped",
            "checks": {"count": "{kind:count,min?:number,max?:number}",
                "compare": "{kind:compare,left:pointer,op:eq/ne/lt/lte/gt/gte,right?:pointer,value?:literal}",
                "min_gap": "{kind:min_gap,time:pointer,seconds:number}",
                "unique": "{kind:unique,fields:[pointer,...]}"}},
        "examples": [{"id": "reported_stellar_damage", "source": {"resource": "result", "pointer": "/hits"},
            "where": [{"path": "/damage_system", "op": "eq", "value": "stellar"}],
            "group_by": ["/character"], "expected_groups": [["actual-actor-id"]],
            "check": {"kind": "count", "min": 1}}],
        "interpretation": "只比较作者明确选择的保存证据；原字段值和含义须先查询。"
            "observed_match只表示本次比较成立，不证明游戏事实、完整性或计算可接纳。缺字段/缺流水返回unknown。"}


def _bad(path, expected):
    raise PlanError("观察请求格式不正确；这不影响原计算或候选。", "invalid_observation_request",
                    {"path": path, "expected": expected, "execution_state": "not_started",
                     "recovery": "读取assistance_contract.declared_observations并只修正该观察请求。"})


def _pointer(value):
    return isinstance(value, str) and len(value) <= 2048 and (value == "" or value.startswith("/"))


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate_observations(rows):
    try:
        if len(json_bytes(rows)) > 256 * 1024:
            _bad("observations", "finite JSON up to256KiB")
    except (ValueError, TypeError, RecursionError) as error:
        if isinstance(error, PlanError):
            raise
        _bad("observations", "finite JSON up to256KiB")
    if not isinstance(rows, list) or len(rows) > 256:
        _bad("observations", "0..256 observations")
    seen = set()
    for i, row in enumerate(rows):
        path = f"observations/{i}"
        if (not isinstance(row, dict) or set(row) - {"id", "source", "where", "group_by", "expected_groups", "check", "note", "source_ref"}
                or not isinstance(row.get("id"), str) or not 1 <= len(row["id"]) <= 128 or row["id"] in seen):
            _bad(path, "unique id plus source/check and optional where/group_by/expected_groups/note/source_ref")
        seen.add(row["id"])
        source = row.get("source")
        if (not isinstance(source, dict) or set(source) != {"resource", "pointer"}
                or not isinstance(source["resource"], str)
                or source["resource"] not in {"result", "plan", "report", "effects"} or not _pointer(source["pointer"])):
            _bad(path + "/source", "{resource:result|plan|report|effects,pointer:JSON pointer to saved array}")
        filters = row.get("where", [])
        if not isinstance(filters, list) or len(filters) > 32:
            _bad(path + "/where", "0..32 explicit comparisons")
        for f in filters:
            if (not isinstance(f, dict) or set(f) != {"path", "op", "value"} or not _pointer(f["path"])
                    or not isinstance(f["op"], str) or f["op"] not in OPS or f["op"] == "in" and not isinstance(f["value"], list)
                    or f["op"] == "exists" and type(f["value"]) is not bool):
                _bad(path + "/where", "{path,op,value}; in needs list; exists needs boolean")
        groups = row.get("group_by", [])
        if not isinstance(groups, list) or len(groups) > 8 or any(not _pointer(p) for p in groups):
            _bad(path + "/group_by", "0..8 JSON pointers")
        expected = row.get("expected_groups", [])
        if (not isinstance(expected, list) or len(expected) > 1024
                or any(not isinstance(g, list) or len(g) != len(groups) for g in expected)):
            _bad(path + "/expected_groups", "0..1024 lists with one value for each group_by field")
        check = row.get("check")
        if not isinstance(check, dict) or not isinstance(check.get("kind"), str) or check["kind"] not in KINDS:
            _bad(path + "/check", "count, compare, min_gap or unique")
        kind = check["kind"]
        if kind == "count":
            if (set(check) - {"kind", "min", "max"} or not ({"min", "max"} & check.keys())
                    or any(not _number(v) or v < 0 for k, v in check.items() if k != "kind")
                    or check.get("min", 0) > check.get("max", math.inf)):
                _bad(path + "/check", "count with finite nonnegative min/max, min<=max")
        elif kind == "compare":
            if (set(check) - {"kind", "left", "op", "right", "value"} or not _pointer(check.get("left"))
                    or not isinstance(check.get("op"), str) or check["op"] not in OPS - {"exists", "in"}
                    or ("right" in check) == ("value" in check) or "right" in check and not _pointer(check["right"])):
                _bad(path + "/check", "compare with left pointer, comparison op, exactly one right pointer or literal value")
        elif kind == "min_gap":
            if (set(check) != {"kind", "time", "seconds"} or not _pointer(check["time"])
                    or not _number(check["seconds"]) or check["seconds"] < 0):
                _bad(path + "/check", "{kind:min_gap,time:pointer,seconds:finite nonnegative number}")
        elif (set(check) != {"kind", "fields"} or not isinstance(check["fields"], list)
                or not 1 <= len(check["fields"]) <= 16 or any(not _pointer(p) for p in check["fields"])):
            _bad(path + "/check", "unique with 1..16 fields as JSON pointers")
        for field in ("note", "source_ref"):
            if field in row and (not isinstance(row[field], str) or len(row[field]) > 4096):
                _bad(path + "/" + field, "text at most4096 characters")


def _get(value, pointer):
    if pointer == "":
        return value
    for segment in pointer.split("/")[1:]:
        segment = segment.replace("~1", "/").replace("~0", "~")
        try:
            if isinstance(value, list):
                if not segment.isascii() or not segment.isdecimal():
                    return MISSING
                value = value[int(segment)]
            elif isinstance(value, dict):
                value = value[segment]
            else:
                return MISSING
        except (KeyError, IndexError, ValueError):
            return MISSING
    return value


def _compare(left, op, right):
    if op == "exists":
        return (left is not MISSING) == right
    if left is MISSING or right is MISSING:
        return None
    if op == "eq":
        return type(left) is type(right) and left == right or _number(left) and _number(right) and left == right
    if op == "ne":
        return not _compare(left, "eq", right)
    if op == "in":
        return any(_compare(left, "eq", value) for value in right)
    if not _number(left) or not _number(right):
        return None
    return {"lt": left < right, "lte": left <= right, "gt": left > right, "gte": left >= right}[op]


def _evaluate(items, check, *, complete, unknown_count):
    kind = check["kind"]
    violations, samples, unknown = [], [], unknown_count
    def record(target, item):
        if len(target) < 8:
            target.append(item)
    violation_count = 0
    if kind == "count":
        actual = len(items)
        different = actual < check.get("min", 0) or actual > check.get("max", math.inf)
        state = "observed_difference" if different else "observed_match"
        # Missing matches or absent filters can hide both extra and missing rows.
        if actual > check.get("max", math.inf):
            state = "observed_difference"  # Already-observed excess cannot be undone by unknown rows.
        elif unknown or not complete and (actual < check.get("min", 0) or "max" in check) or not items:
            state = ("unknown" if unknown or not complete else
                     "not_observed_in_complete_window" if different else "observed_match")
        return {"status": state, "actual_count": actual, "unknown_rows": unknown,
                "samples": [ref for ref, _ in items[:8]]}
    if kind == "min_gap":
        valid = []
        for ref, row in items:
            value = _get(row, check["time"])
            if not _number(value):
                unknown += 1
            else:
                valid.append((value, ref))
        valid.sort()
        minimum = None
        for left, right in pairwise(valid):
            gap = right[0] - left[0]
            minimum = gap if minimum is None else min(minimum, gap)
            if gap + 1e-9 < check["seconds"]:
                violation_count += 1
                record(violations, {"left_ref": left[1], "right_ref": right[1], "actual_gap": gap})
        details = {"minimum_observed_gap": minimum, "comparison_count": max(0, len(valid) - 1)}
        if len(valid) < 2:
            unknown += 1
    else:
        seen = {}
        comparisons = 0
        for ref, row in items:
            if kind == "unique":
                values = [_get(row, pointer) for pointer in check["fields"]]
                if any(v is MISSING for v in values):
                    unknown += 1
                    continue
                key = json_bytes(values)
                correct = key not in seen
                sample = {"source_ref": ref, "previous_ref": seen.get(key), "values": values}
                seen.setdefault(key, ref)
            else:
                left = _get(row, check["left"])
                right = _get(row, check["right"]) if "right" in check else check["value"]
                correct = _compare(left, check["op"], right)
                if correct is None:
                    unknown += 1
                    continue
                sample = {"source_ref": ref, "left": left, "right": right}
            comparisons += 1
            if correct:
                record(samples, sample)
            else:
                violation_count += 1
                record(violations, sample)
        details = {"comparison_count": comparisons}
    return {"status": "observed_difference" if violation_count else "unknown" if unknown or not items else "observed_match",
            "violation_count": violation_count, "unknown_rows": unknown, "samples": samples,
            "counterexamples": violations, **details}


def observe_declared(rows, *, plan, result, report=None, effects=None, limit=50, offset=0):
    from genshin_engine.execution_control import check_execution_cancelled
    validate_observations(rows)
    resources = {"plan": plan, "result": result, "report": report or {}, "effects": effects or {}}
    complete = ((result.get("rotation_check") or {}).get("damage_interpretation") or {}).get("complete_first_cycle") is True
    output = []
    for request in rows:
        check_execution_cancelled()
        source = request["source"]
        selected = _get(resources[source["resource"]], source["pointer"])
        base = {"id": request["id"], "source": source, "note": request.get("note"), "source_ref": request.get("source_ref"),
                "evidence_scope": "declared_input_only" if source["resource"] == "plan" else "saved_evidence_only",
                "complete_execution_window": complete, "authority": "advisory_only", "acceptance_effect": "none"}
        if not isinstance(selected, list):
            output.append({**base, "status": "unknown", "reason": "source_missing_or_not_array", "groups": []})
            continue
        groups, unknown = defaultdict(list), 0
        group_values = {}
        for index, item in enumerate(selected):
            if index % 1024 == 0:
                check_execution_cancelled()
            comparisons = [_compare(_get(item, f["path"]), f["op"], f["value"]) for f in request.get("where", [])]
            if False in comparisons:
                continue
            if None in comparisons:
                unknown += 1
                continue
            values = [_get(item, p) for p in request.get("group_by", [])]
            if any(value is MISSING for value in values):
                unknown += 1
                continue
            key = json_bytes(values)
            group_values[key] = values
            groups[key].append((source["pointer"] + "/" + str(index), item))
        for values in request.get("expected_groups", [[]] if not request.get("group_by") else []):
            key = json_bytes(values)
            group_values[key] = values
            groups.setdefault(key, [])
        source_complete = source["resource"] == "plan" or complete
        evaluated = [{"group": group_values[key], **_evaluate(items, request["check"], complete=source_complete, unknown_count=unknown)}
                     for key, items in groups.items()]
        states = {item["status"] for item in evaluated}
        status = ("observed_difference" if states & {"observed_difference", "not_observed_in_complete_window"} else
                  "unknown" if not states or "unknown" in states else "observed_match")
        output.append({**base, "status": status, "unknown_rows": unknown, "source_row_count": len(selected),
                       "group_count": len(evaluated), "groups": evaluated[offset:offset + limit],
                       "source_scope_complete": source_complete,
                       "next_offset": offset + limit if offset + limit < len(evaluated) else None,
                       "scope_note": "仅核对提供的字段和分组；省略expected_groups时不能判断某组是否整体缺失。"})
    return {"contract": CONTRACT, "authority": "advisory_only", "acceptance_effect": "none", "items": output}
