"""Versioned, user-adjustable budgets for one native Agent turn.

The policy file stores only explicit user overrides.  Defaults therefore remain
maintainable by the application: a later policy version can add or tune a
default without rewriting every profile, while values the user chose stay
explicit.  A running turn consumes a frozen snapshot; writes made here are for
the next turn.
"""
from __future__ import annotations

import copy
import json
import os
import re
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from genshin_app.equipment.schema import PlanError
from genshin_db.locking import LockBusyError, file_lock

POLICY_CONTRACT = "genshin-native-turn-budget-policy-v1"
TURN_SNAPSHOT_CONTRACT = "genshin-native-turn-budget-snapshot-v1"
POLICY_ID = "complete_table_v1"
# Version 2 remained a design-only active-wall-clock draft.  Version 3 was the
# first runtime policy that charges DSH compaction as ordinary provider work.
# Version 4 removes the obsolete fixed tool-parallelism field; DSH settings
# own safe read concurrency while App mutation tools remain exclusive.
# Version 5 raises the complete-task envelope after real native runs showed
# that a healthy, tool-heavy turn can legitimately outgrow the former product
# ceilings.  These remain fail-safe capacities rather than completion targets.
# Version 6 raises the cumulative input ceiling for long runs with repeated
# cached prefixes. Its frozen snapshot schema and charge counting are unchanged.
# Context pressure itself is governed by the pinned DSH route policy rather
# than a second App-owned fixed-token threshold.
POLICY_VERSION = 6
APPLY_SCOPE = "next_turn"

MAX_POLICY_BYTES = 64 * 1024
MAX_GENERATION = 2**53 - 1
_SOURCE = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")
_RETIRED_FIELDS = frozenset({
    "context_prune_tokens", "context_stop_tokens", "parallel_tool_calls",
})

# Keep the long-running complete-task profile in one place: runtime, GUI and
# CLI all consume the same policy snapshot.  A user can still lower any
# adjustable field, while the defaults no longer interrupt a healthy run at
# the old 30-minute boundary.
DEFAULT_BUDGET: dict[str, int] = {
    "wall_clock_seconds": 7_200,
    "provider_steps": 4_096,
    "app_tool_calls": 3_072,
    "web_tool_calls": 1_024,
    "total_tool_calls": 4_096,
    "web_search_calls": 256,
    "web_search_queries": 1_024,
    "web_fetch_calls": 384,
    "per_request_output_tokens": 256_000,
    "cumulative_output_tokens": 2_000_000,
    "cumulative_input_soft_tokens": 64_000_000,
    "cumulative_input_hard_tokens": 256_000_000,
    "cumulative_uncached_input_tokens": 4_000_000,
    "retry_count": 0,
    "read_tool_calls": 2_048,
    "mutation_tool_calls": 1_024,
}

# Product limits are deliberately separate from defaults.  ``adjustable`` is
# also the public GUI/CLI contract; retry and parallelism remain correctness
# invariants rather than user budgets.
BUDGET_LIMITS: dict[str, dict[str, int | bool]] = {
    "wall_clock_seconds": {"min": 60, "max": 86_400, "adjustable": True},
    "provider_steps": {"min": 1, "max": 32_768, "adjustable": True},
    "app_tool_calls": {"min": 1, "max": 16_384, "adjustable": True},
    "web_tool_calls": {"min": 0, "max": 16_384, "adjustable": True},
    "total_tool_calls": {"min": 1, "max": 16_384, "adjustable": True},
    "web_search_calls": {"min": 0, "max": 4_096, "adjustable": True},
    "web_search_queries": {"min": 0, "max": 16_384, "adjustable": True},
    "web_fetch_calls": {"min": 0, "max": 4_096, "adjustable": True},
    "per_request_output_tokens": {"min": 1_024, "max": 256_000, "adjustable": True},
    "cumulative_output_tokens": {"min": 1_024, "max": 32_000_000, "adjustable": True},
    "cumulative_input_soft_tokens": {"min": 262_144, "max": 1_000_000_000, "adjustable": True},
    "cumulative_input_hard_tokens": {"min": 1_000_000, "max": 2_000_000_000, "adjustable": True},
    "cumulative_uncached_input_tokens": {"min": 65_536, "max": 256_000_000, "adjustable": True},
    "retry_count": {"min": 0, "max": 0, "adjustable": False},
    "read_tool_calls": {"min": 0, "max": 16_384, "adjustable": True},
    "mutation_tool_calls": {"min": 0, "max": 16_384, "adjustable": True},
}
BUDGET_FIELDS = tuple(DEFAULT_BUDGET)
ADJUSTABLE_BUDGET_FIELDS = tuple(
    name for name in BUDGET_FIELDS if BUDGET_LIMITS[name]["adjustable"] is True
)


def _fail(message: str, reason: str = "native_turn_budget_invalid", details=None):
    raise PlanError(message, reason, details)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("预算配置包含重复字段", "native_turn_budget_store_invalid")
        result[key] = value
    return result


def _policy_path(data_dir: str | os.PathLike[str]) -> Path:
    return Path(data_dir).resolve() / "settings" / "native-agent-budget.json"


def budget_policy_path(data_dir: str | os.PathLike[str]) -> Path:
    """Return the App-owned policy path without creating it."""
    return _policy_path(data_dir)


def _integer(value: object, field: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        _fail(
            f"预算字段 {field} 必须是 {minimum} 到 {maximum} 的整数",
            details={"field": field, "minimum": minimum, "maximum": maximum},
        )
    return value


def validate_budget_snapshot(value: object) -> dict[str, int]:
    """Validate an effective budget at every Python trust boundary."""
    if not isinstance(value, Mapping) or set(value) != set(BUDGET_FIELDS):
        _fail(
            "预算快照字段不完整或包含未知字段",
            details={
                "required_fields": list(BUDGET_FIELDS),
                "actual_fields": sorted(map(str, value.keys())) if isinstance(value, Mapping) else None,
            },
        )
    normalized: dict[str, int] = {}
    for field in BUDGET_FIELDS:
        limit = BUDGET_LIMITS[field]
        normalized[field] = _integer(value[field], field, int(limit["min"]), int(limit["max"]))

    def require(condition: bool, message: str, fields: tuple[str, ...]) -> None:
        if not condition:
            _fail(message, details={"fields": list(fields)})

    require(normalized["app_tool_calls"] <= normalized["total_tool_calls"],
            "App工具预算不能超过总工具预算", ("app_tool_calls", "total_tool_calls"))
    require(normalized["web_tool_calls"] <= normalized["total_tool_calls"],
            "联网工具预算不能超过总工具预算", ("web_tool_calls", "total_tool_calls"))
    require(normalized["total_tool_calls"] <= normalized["provider_steps"],
            "总工具预算不能超过提供方步骤预算", ("total_tool_calls", "provider_steps"))
    require(normalized["web_search_calls"] <= normalized["web_tool_calls"],
            "搜索调用预算不能超过联网工具预算", ("web_search_calls", "web_tool_calls"))
    require(normalized["web_fetch_calls"] <= normalized["web_tool_calls"],
            "网页读取预算不能超过联网工具预算", ("web_fetch_calls", "web_tool_calls"))
    require(normalized["web_search_queries"] <= 4 * normalized["web_search_calls"],
            "搜索词预算不能超过每次搜索四条的协议容量",
            ("web_search_queries", "web_search_calls"))
    require(normalized["read_tool_calls"] <= normalized["app_tool_calls"],
            "只读App工具预算不能超过App工具总预算", ("read_tool_calls", "app_tool_calls"))
    require(normalized["mutation_tool_calls"] <= normalized["app_tool_calls"],
            "变更App工具预算不能超过App工具总预算", ("mutation_tool_calls", "app_tool_calls"))
    require(normalized["per_request_output_tokens"] <= normalized["cumulative_output_tokens"],
            "单次输出预算不能超过累计输出预算",
            ("per_request_output_tokens", "cumulative_output_tokens"))
    require(normalized["cumulative_input_soft_tokens"] <= normalized["cumulative_input_hard_tokens"],
            "累计输入软警告必须不高于硬上限",
            ("cumulative_input_soft_tokens", "cumulative_input_hard_tokens"))
    require(normalized["cumulative_uncached_input_tokens"] <= normalized["cumulative_input_hard_tokens"],
            "未缓存输入预算不能超过累计输入硬上限",
            ("cumulative_uncached_input_tokens", "cumulative_input_hard_tokens"))
    return normalized


def _validate_overrides(value: object, *, store: bool = False) -> dict[str, int]:
    reason = "native_turn_budget_store_invalid" if store else "native_turn_budget_invalid"
    if not isinstance(value, Mapping):
        _fail("预算覆盖必须是JSON对象", reason)
    unknown = set(value) - set(BUDGET_FIELDS)
    if unknown:
        _fail("预算覆盖包含未知字段", reason, {"unknown_fields": sorted(map(str, unknown))})
    normalized: dict[str, int] = {}
    for field, raw in value.items():
        limit = BUDGET_LIMITS[field]
        if limit["adjustable"] is not True:
            _fail(f"预算字段 {field} 在当前策略中不可调整", reason, {"field": field})
        try:
            normalized[field] = _integer(raw, field, int(limit["min"]), int(limit["max"]))
        except PlanError as exc:
            if store:
                raise PlanError(str(exc), reason, exc.details) from exc
            raise
    effective = dict(DEFAULT_BUDGET)
    effective.update(normalized)
    try:
        validate_budget_snapshot(effective)
    except PlanError as exc:
        if store:
            raise PlanError(str(exc), reason, exc.details) from exc
        raise
    # Equal-to-default values are not explicit preferences.  Removing them is
    # what lets later policy defaults flow through after reset or migration.
    return {field: value for field, value in normalized.items() if value != DEFAULT_BUDGET[field]}


def _builtin_snapshot() -> dict[str, Any]:
    return {
        "contract": POLICY_CONTRACT,
        "policy_id": POLICY_ID,
        "policy_version": POLICY_VERSION,
        "generation": 0,
        "effective": copy.deepcopy(DEFAULT_BUDGET),
        "overrides": {},
        "retired_overrides": {},
        "follows_defaults": True,
        "source": "builtin",
        "updated_at": None,
        "defaults": copy.deepcopy(DEFAULT_BUDGET),
        "limits": copy.deepcopy(BUDGET_LIMITS),
        "apply_scope": APPLY_SCOPE,
    }


def default_budget_policy() -> dict[str, Any]:
    return _builtin_snapshot()


def _decode_stored(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object)
    except PlanError:
        raise
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise PlanError(
            "预算配置不是有效的UTF-8 JSON",
            "native_turn_budget_store_invalid",
        ) from exc
    required = {
        "contract", "policy_id", "base_policy_version", "generation",
        "overrides", "source", "updated_at",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        _fail("预算配置结构不合法", "native_turn_budget_store_invalid")
    if value.get("contract") != POLICY_CONTRACT or value.get("policy_id") != POLICY_ID:
        _fail("预算配置策略身份不匹配", "native_turn_budget_store_invalid")
    base_version = value.get("base_policy_version")
    if type(base_version) is not int or not 1 <= base_version <= POLICY_VERSION:
        _fail("预算配置版本不受支持", "native_turn_budget_store_invalid")
    generation = value.get("generation")
    if type(generation) is not int or not 1 <= generation <= MAX_GENERATION:
        _fail("预算配置代数不合法", "native_turn_budget_store_invalid")
    source = value.get("source")
    if not isinstance(source, str) or _SOURCE.fullmatch(source) is None:
        _fail("预算配置来源不合法", "native_turn_budget_store_invalid")
    updated_at = value.get("updated_at")
    if not isinstance(updated_at, str):
        _fail("预算配置更新时间不合法", "native_turn_budget_store_invalid")
    try:
        parsed = datetime.fromisoformat(updated_at)
    except (OverflowError, ValueError) as exc:
        raise PlanError("预算配置更新时间不合法", "native_turn_budget_store_invalid") from exc
    if parsed.tzinfo is None:
        _fail("预算配置更新时间不合法", "native_turn_budget_store_invalid")
    raw_overrides = value.get("overrides")
    if not isinstance(raw_overrides, Mapping):
        _fail("预算覆盖必须是JSON对象", "native_turn_budget_store_invalid")
    retired_overrides = {
        field: copy.deepcopy(raw_overrides[field])
        for field in _RETIRED_FIELDS
        if field in raw_overrides
    }
    active_overrides = {
        field: copy.deepcopy(item)
        for field, item in raw_overrides.items()
        if field not in _RETIRED_FIELDS
    }
    overrides = _validate_overrides(active_overrides, store=True)
    return {
        "generation": generation,
        "overrides": overrides,
        "retired_overrides": retired_overrides,
        "source": source,
        "updated_at": updated_at,
    }


def _read_stored(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_POLICY_BYTES + 1)
    except OSError as exc:
        raise PlanError("无法读取预算配置", "native_turn_budget_store_invalid") from exc
    if len(raw) > MAX_POLICY_BYTES:
        _fail("预算配置超过64KiB", "native_turn_budget_store_invalid")
    return _decode_stored(raw)


def _snapshot(stored: dict[str, Any] | None) -> dict[str, Any]:
    result = _builtin_snapshot()
    if stored is None:
        return result
    effective = dict(DEFAULT_BUDGET)
    effective.update(stored["overrides"])
    result.update(
        generation=stored["generation"],
        effective=validate_budget_snapshot(effective),
        overrides=copy.deepcopy(stored["overrides"]),
        follows_defaults=not stored["overrides"],
        source=stored["source"],
        updated_at=stored["updated_at"],
        retired_overrides=copy.deepcopy(stored.get("retired_overrides", {})),
    )
    return result


def load_native_turn_budget_policy(data_dir: str | os.PathLike[str]) -> dict[str, Any]:
    path = _policy_path(data_dir)
    if not path.exists():
        return _snapshot(None)
    try:
        with file_lock(path.with_name(".native-agent-budget.lock")):
            return _snapshot(_read_stored(path))
    except LockBusyError as exc:
        raise PlanError("预算配置正在被修改，请稍后重试", "native_turn_budget_store_busy") from exc


def _encode_store(*, generation: int, overrides: dict[str, int], source: str, updated_at: str) -> bytes:
    value = {
        "contract": POLICY_CONTRACT,
        "policy_id": POLICY_ID,
        "base_policy_version": POLICY_VERSION,
        "generation": generation,
        "overrides": overrides,
        "source": source,
        "updated_at": updated_at,
    }
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temp_path = Path(name)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    except OSError as exc:
        raise PlanError("无法写入预算配置", "native_turn_budget_store_write_failed") from exc
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def update_native_turn_budget_policy(
    data_dir: str | os.PathLike[str],
    overrides: Mapping[str, int],
    *,
    expected_generation: int | None = None,
    source: str = "gui",
) -> dict[str, Any]:
    """Atomically replace the complete sparse override map.

    Callers doing a patch (the CLI does) first merge against ``snapshot[
    'overrides']``.  This replacement contract keeps GUI save/reset behavior
    deterministic and prevents a stale form from accidentally retaining fields.
    """
    if not isinstance(source, str) or _SOURCE.fullmatch(source) is None:
        _fail("预算修改来源不合法")
    if expected_generation is not None and (
        type(expected_generation) is not int or not 0 <= expected_generation <= MAX_GENERATION
    ):
        _fail("期望预算代数不合法")
    normalized = _validate_overrides(overrides)
    path = _policy_path(data_dir)
    try:
        with file_lock(path.with_name(".native-agent-budget.lock"), timeout=5):
            stored = _read_stored(path)
            actual_generation = stored["generation"] if stored is not None else 0
            if expected_generation is not None and expected_generation != actual_generation:
                _fail(
                    "预算配置已被其他窗口修改，请重新载入",
                    "native_turn_budget_generation_conflict",
                    {"expected_generation": expected_generation, "actual_generation": actual_generation},
                )
            if actual_generation >= MAX_GENERATION:
                _fail("预算配置代数已耗尽", "native_turn_budget_store_write_failed")
            generation = actual_generation + 1
            updated_at = _now()
            _atomic_write(path, _encode_store(
                generation=generation,
                overrides=normalized,
                source=source,
                updated_at=updated_at,
            ))
            return _snapshot({
                "generation": generation,
                "overrides": normalized,
                "source": source,
                "updated_at": updated_at,
            })
    except LockBusyError as exc:
        raise PlanError("预算配置正在被修改，请稍后重试", "native_turn_budget_store_busy") from exc


def reset_native_turn_budget_policy(
    data_dir: str | os.PathLike[str],
    *,
    expected_generation: int | None = None,
    source: str = "gui",
) -> dict[str, Any]:
    """Follow current and future defaults while preserving mutation generation."""
    return update_native_turn_budget_policy(
        data_dir, {}, expected_generation=expected_generation, source=source
    )


def validate_turn_budget_snapshot(value: object) -> dict[str, Any]:
    required = {"contract", "policy_id", "policy_version", "generation", "effective"}
    if not isinstance(value, Mapping) or set(value) != required:
        _fail("冻结预算快照结构不合法")
    if value.get("contract") != TURN_SNAPSHOT_CONTRACT or value.get("policy_id") != POLICY_ID:
        _fail("冻结预算快照身份不匹配")
    policy_version = value.get("policy_version")
    if type(policy_version) is not int or policy_version not in {5, POLICY_VERSION}:
        _fail("冻结预算快照版本不受支持")
    generation = value.get("generation")
    if type(generation) is not int or not 0 <= generation <= MAX_GENERATION:
        _fail("冻结预算快照代数不合法")
    return {
        "contract": TURN_SNAPSHOT_CONTRACT,
        "policy_id": POLICY_ID,
        "policy_version": policy_version,
        "generation": generation,
        "effective": validate_budget_snapshot(value.get("effective")),
    }


def freeze_turn_budget(
    policy_or_data_dir: Mapping[str, Any] | str | os.PathLike[str],
) -> dict[str, Any]:
    """Return the minimal immutable payload copied into a turn request."""
    if isinstance(policy_or_data_dir, Mapping):
        if policy_or_data_dir.get("contract") == TURN_SNAPSHOT_CONTRACT:
            return validate_turn_budget_snapshot(policy_or_data_dir)
        if set(BUDGET_FIELDS) == set(policy_or_data_dir):
            policy = {"generation": 0, "effective": policy_or_data_dir}
        else:
            policy = policy_or_data_dir
    else:
        policy = load_native_turn_budget_policy(policy_or_data_dir)
    generation = policy.get("generation")
    if type(generation) is not int or not 0 <= generation <= MAX_GENERATION:
        _fail("预算策略代数不合法")
    frozen = {
        "contract": TURN_SNAPSHOT_CONTRACT,
        "policy_id": POLICY_ID,
        "policy_version": POLICY_VERSION,
        "generation": generation,
        "effective": validate_budget_snapshot(policy.get("effective")),
    }
    return validate_turn_budget_snapshot(frozen)


# Short aliases for GUI/application callers; long names document the owning
# native-turn boundary and remain the preferred external API.
load_budget_policy = load_native_turn_budget_policy
update_budget_policy = update_native_turn_budget_policy
reset_budget_policy = reset_native_turn_budget_policy


__all__ = [
    "ADJUSTABLE_BUDGET_FIELDS",
    "APPLY_SCOPE",
    "BUDGET_FIELDS",
    "BUDGET_LIMITS",
    "DEFAULT_BUDGET",
    "POLICY_CONTRACT",
    "POLICY_ID",
    "POLICY_VERSION",
    "TURN_SNAPSHOT_CONTRACT",
    "budget_policy_path",
    "default_budget_policy",
    "freeze_turn_budget",
    "load_budget_policy",
    "load_native_turn_budget_policy",
    "reset_budget_policy",
    "reset_native_turn_budget_policy",
    "update_budget_policy",
    "update_native_turn_budget_policy",
    "validate_budget_snapshot",
    "validate_turn_budget_snapshot",
]
