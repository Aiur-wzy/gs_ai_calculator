"""Legacy sparse settings accepted only as a one-time DSH profile migration.

The values here intentionally mirror public configuration defaults in the
locked ``@deepseek-ai/*`` 0.2.1-rc.5 cohort. Active settings are owned by the
official DSH Settings/ConfigEditor and are not mirrored here. App security and domain
limits (provider retry, credentials, paths, tool authority and write
serialization) are not part of this user-configurable contract.
"""
from __future__ import annotations

import copy
import json
import math
import os
import re
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from genshin_app.agent_chat.native_model_catalog import DEFAULT_APP_MODEL_ALLOWLIST
from genshin_app.equipment.schema import PlanError
from genshin_db.locking import LockBusyError, file_lock

SETTINGS_CONTRACT = "genshin-native-dsh-settings-v1"
SETTINGS_VERSION = 1
SETTINGS_FILE = "native-agent-dsh-settings.json"
DEFAULT_PROFILE_RELATIVE_PATH = Path("native_agent_sessions_v2") / "dsh-production-v1"
DSH_RELEASE = "0.2.1-rc.5"
APPLY_SCOPE = "next_runtime"
MAX_SETTINGS_BYTES = 256 * 1024
MAX_GENERATION = 2**53 - 1
REVIEWED_MODEL_IDS = frozenset(
    row["model"] for row in DEFAULT_APP_MODEL_ALLOWLIST
)

_SOURCE = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")
_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_SECTIONS = frozenset({
    "compaction", "toolResultPruner", "agentLoop", "persistence",
    "sessionQuery", "projectionCache",
})
_COMPACTION_FIELDS = frozenset({
    "thresholdRatio", "headroomTokens", "retainRatio", "retainTokens",
    "summarizationProvider", "summarizationModel", "maxTokens",
    "compactionRetries", "maxOverflowRetries", "modelPolicies", "auto",
})
_MODEL_POLICY_FIELDS = frozenset({
    "provider", "model", "thresholdRatio", "headroomTokens", "retainRatio",
    "retainTokens", "summarizationProvider", "summarizationModel", "maxTokens",
    "compactionRetries", "maxOverflowRetries",
})
# Mirrored from @deepseek-ai/dsh-compaction-tool-result-pruner's public
# PRUNE_MARKER export.  The cross-language settings parity test and the DSH
# update runbook make a release change explicit instead of leaving a magic
# character count in validation.
_DSH_TOOL_RESULT_PRUNE_MARKER = "\n\n[... tool result middle pruned ...]\n\n"

DEFAULT_DSH_SETTINGS: dict[str, Any] = {
    "compaction": {
        "thresholdRatio": 0.8,
        "headroomTokens": 65_536,
        "retainRatio": 0.16,
        "summarizationProvider": "",
        "summarizationModel": "",
        "compactionRetries": 1,
        "maxOverflowRetries": 1,
        "modelPolicies": [],
        "auto": True,
    },
    "toolResultPruner": {
        "thresholdChars": 8_192,
        "headChars": 4_096,
        "tailChars": 1_024,
    },
    "agentLoop": {"maxParallelToolCalls": 10},
    "persistence": {"compression": "zstd", "listConcurrency": 3},
    "sessionQuery": {
        "openAt": "startup",
        "journalMode": "wal",
        "defaultLimit": 20,
        "maxLimit": 100,
        "snippetChars": 240,
        "readWindowMax": 50,
        "persistedReadConcurrency": 4,
        "preparedSessionCacheSize": 5,
    },
    # Upstream requires these two values and deliberately has no defaults.
    "projectionCache": {"writeEveryEvents": 200, "writeIntervalMs": 5_000},
}


def _source(package: str, field: str, *, kind: str = "dsh_default") -> dict[str, str]:
    return {
        "kind": kind,
        "package": package,
        "version": DSH_RELEASE,
        "field": field,
    }


DEFAULT_DSH_SETTING_SOURCES: dict[str, Any] = {
    "compaction": {
        field: _source("@deepseek-ai/dsh-compaction-basic", field)
        for field in DEFAULT_DSH_SETTINGS["compaction"]
    },
    "toolResultPruner": {
        field: _source("@deepseek-ai/dsh-compaction-tool-result-pruner", field)
        for field in DEFAULT_DSH_SETTINGS["toolResultPruner"]
    },
    "agentLoop": {
        field: _source("@deepseek-ai/dsh-agent-loop", field)
        for field in DEFAULT_DSH_SETTINGS["agentLoop"]
    },
    "persistence": {
        field: _source("@deepseek-ai/dsh-session-persistence-jsonl", field)
        for field in DEFAULT_DSH_SETTINGS["persistence"]
    },
    "sessionQuery": {
        field: _source("@deepseek-ai/dsh-session-query-sqlite", field)
        for field in DEFAULT_DSH_SETTINGS["sessionQuery"]
    },
    "projectionCache": {
        field: _source(
            "@deepseek-ai/dsh-session-projection-cache",
            field,
            kind="app_deployment_required",
        )
        for field in DEFAULT_DSH_SETTINGS["projectionCache"]
    },
}
DEFAULT_DSH_SETTING_SOURCES["compaction"]["maxTokens"] = {
    "kind": "dsh_derived_default",
    "package": "@deepseek-ai/dsh-compaction-basic",
    "version": DSH_RELEASE,
    "field": "maxTokens",
    "expression": "headroomTokens",
}

# A machine-readable settings form contract.  Numeric maxima are App-owned
# storage/operational bounds; they do not replace DSH's model-capacity checks.
DSH_SETTING_LIMITS: dict[str, Any] = {
    "compaction": {
        "thresholdRatio": {"type": "number", "exclusiveMin": 0, "max": 1},
        "headroomTokens": {"type": "integer", "min": 0, "max": 2**53 - 1},
        "retainRatio": {"type": "number", "exclusiveMin": 0, "max": 1},
        "retainTokens": {"type": "integer", "min": 0, "max": 2**53 - 1},
        "summarizationProvider": {"type": "reviewed_provider"},
        "summarizationModel": {
            "type": "installed_model",
            "values": sorted(REVIEWED_MODEL_IDS),
        },
        "maxTokens": {"type": "integer", "min": 1, "max": 2**53 - 1},
        "compactionRetries": {"type": "integer", "min": 0, "max": 16},
        "maxOverflowRetries": {"type": "integer", "min": 0, "max": 8},
        "modelPolicies": {"type": "model_policy_array", "maxItems": 32},
        "auto": {"type": "boolean"},
    },
    "toolResultPruner": {
        field: {"type": "integer", "min": 0 if field != "thresholdChars" else 1,
                "max": 2**53 - 1}
        for field in ("thresholdChars", "headChars", "tailChars")
    },
    "agentLoop": {
        "maxParallelToolCalls": {"type": "integer", "min": 1, "max": 128}
    },
    "persistence": {
        "listConcurrency": {"type": "integer", "min": 1, "max": 128},
    },
    "sessionQuery": {
        "openAt": {"type": "enum", "values": ["startup", "first-search", "never"]},
        "journalMode": {
            "type": "enum", "values": ["wal", "delete", "truncate", "persist"]
        },
        "defaultLimit": {"type": "integer", "min": 1, "max": 1_000_000},
        "maxLimit": {"type": "integer", "min": 1, "max": 1_000_000},
        "snippetChars": {"type": "integer", "min": 1, "max": 1_000_000},
        "readWindowMax": {"type": "integer", "min": 0, "max": 1_000_000},
        "persistedReadConcurrency": {
            "type": "integer", "min": 1, "max": 1_000_000
        },
        "preparedSessionCacheSize": {
            "type": "integer", "min": 1, "max": 1_000_000
        },
    },
    "projectionCache": {
        "writeEveryEvents": {"type": "integer", "min": 1, "max": 1_000_000},
        "writeIntervalMs": {"type": "integer", "min": 1, "max": 86_400_000},
    },
}


def _fail(message: str, reason: str = "native_dsh_settings_invalid", details=None):
    raise PlanError(message, reason, details)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            _fail("DSH设置包含重复字段", "native_dsh_settings_store_invalid")
        value[key] = item
    return value


def dsh_settings_path(profile_root: str | os.PathLike[str]) -> Path:
    return Path(profile_root).resolve() / SETTINGS_FILE


def default_dsh_profile_root(data_dir: str | os.PathLike[str]) -> Path:
    """Return the reviewed production profile root used by the GUI router."""
    return Path(data_dir).resolve() / DEFAULT_PROFILE_RELATIVE_PATH


def _object(value: object, name: str, *, store: bool = False) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail(f"{name}必须是对象",
              "native_dsh_settings_store_invalid" if store else "native_dsh_settings_invalid")
    return copy.deepcopy(dict(value))


def _known(value: Mapping[str, Any], allowed: frozenset[str], name: str,
           *, store: bool = False) -> None:
    unknown = set(value) - allowed
    if unknown:
        _fail(
            f"{name}包含未知字段",
            "native_dsh_settings_store_invalid" if store else "native_dsh_settings_invalid",
            {"unknown_fields": sorted(map(str, unknown))},
        )


def _integer(value: object, name: str, minimum: int = 0,
             maximum: int = 2**53 - 1) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        _fail(f"{name}必须是{minimum}到{maximum}的整数", details={"field": name})
    return value


def _ratio(value: object, name: str) -> float:
    if (type(value) not in {int, float} or isinstance(value, bool)
            or not math.isfinite(value) or not 0 < value <= 1):
        _fail(f"{name}必须是(0, 1]内的数值", details={"field": name})
    return float(value)


def _route_pair(value: Mapping[str, Any], name: str) -> None:
    provider = value.get("summarizationProvider")
    model = value.get("summarizationModel")
    if provider is None and model is None:
        return
    if (not isinstance(provider, str) or not isinstance(model, str)
            or (provider == "") != (model == "")):
        _fail(f"{name}摘要模型必须成对设置")
    if provider and (
        provider != "deepseek-official"
        or _MODEL_ID.fullmatch(model) is None
        or model not in REVIEWED_MODEL_IDS
    ):
        _fail(f"{name}摘要模型不在受审DeepSeek路由范围内")


def _compaction_policy(value: object, name: str, *, model_policy: bool = False,
                       store: bool = False) -> dict[str, Any]:
    result = _object(value, name, store=store)
    _known(result, _MODEL_POLICY_FIELDS if model_policy else _COMPACTION_FIELDS,
           name, store=store)
    if model_policy and (
        result.get("provider") != "deepseek-official"
        or not isinstance(result.get("model"), str)
        or _MODEL_ID.fullmatch(result["model"]) is None
        or result["model"] not in REVIEWED_MODEL_IDS
    ):
        _fail(f"{name}目标模型不在受审DeepSeek路由范围内")
    if "thresholdRatio" in result:
        result["thresholdRatio"] = _ratio(result["thresholdRatio"], f"{name}.thresholdRatio")
    if "headroomTokens" in result:
        result["headroomTokens"] = _integer(result["headroomTokens"],
                                             f"{name}.headroomTokens")
    if "retainRatio" in result:
        result["retainRatio"] = _ratio(result["retainRatio"], f"{name}.retainRatio")
    if "retainTokens" in result:
        result["retainTokens"] = _integer(result["retainTokens"], f"{name}.retainTokens")
    if "retainRatio" in result and "retainTokens" in result:
        _fail(f"{name}不能同时设置retainRatio和retainTokens")
    if "maxTokens" in result:
        result["maxTokens"] = _integer(result["maxTokens"], f"{name}.maxTokens", 1)
    if "compactionRetries" in result:
        result["compactionRetries"] = _integer(
            result["compactionRetries"], f"{name}.compactionRetries", 0, 16
        )
    if "maxOverflowRetries" in result:
        result["maxOverflowRetries"] = _integer(
            result["maxOverflowRetries"], f"{name}.maxOverflowRetries", 0, 8
        )
    _route_pair(result, name)
    if not model_policy and "auto" in result and type(result["auto"]) is not bool:
        _fail(f"{name}.auto必须是布尔值")
    if not model_policy and "modelPolicies" in result:
        rows = result["modelPolicies"]
        if not isinstance(rows, list) or len(rows) > 32:
            _fail(f"{name}.modelPolicies必须是不超过32项的数组")
        seen: set[tuple[str, str]] = set()
        checked = []
        for index, row in enumerate(rows):
            policy = _compaction_policy(
                row, f"{name}.modelPolicies[{index}]", model_policy=True, store=store
            )
            key = (policy["provider"], policy["model"])
            if key in seen:
                _fail(f"{name}.modelPolicies包含重复模型")
            seen.add(key)
            checked.append(policy)
        result["modelPolicies"] = checked
    return result


def _merge_simple(defaults: Mapping[str, Any], raw: object, allowed: frozenset[str],
                  name: str, *, store: bool) -> dict[str, Any]:
    override = _object(raw, name, store=store)
    _known(override, allowed, name, store=store)
    result = copy.deepcopy(dict(defaults))
    result.update(override)
    return result


def validate_dsh_settings_overrides(value: object, *, store: bool = False) -> dict[str, Any]:
    overrides = _object(value, "DSH设置覆盖", store=store)
    _known(overrides, _SECTIONS, "DSH设置覆盖", store=store)

    compaction = _compaction_policy(overrides.get("compaction", {}), "compaction",
                                    store=store)
    effective_compaction = copy.deepcopy(DEFAULT_DSH_SETTINGS["compaction"])
    effective_compaction.update(compaction)
    if "retainTokens" in compaction:
        effective_compaction.pop("retainRatio", None)
    if "retainRatio" in compaction:
        effective_compaction.pop("retainTokens", None)
    if ("retainRatio" in effective_compaction
            and effective_compaction["retainRatio"] >= effective_compaction["thresholdRatio"]):
        _fail("compaction.retainRatio必须低于thresholdRatio")
    for index, policy in enumerate(effective_compaction["modelPolicies"]):
        threshold = policy.get("thresholdRatio", effective_compaction["thresholdRatio"])
        retained = policy.get("retainRatio")
        if retained is None and "retainTokens" not in policy:
            retained = effective_compaction.get("retainRatio")
        if retained is not None and retained >= threshold:
            _fail(f"compaction.modelPolicies[{index}]保留比例必须低于触发比例")

    pruner = _merge_simple(
        DEFAULT_DSH_SETTINGS["toolResultPruner"],
        overrides.get("toolResultPruner", {}),
        frozenset({"thresholdChars", "headChars", "tailChars"}),
        "toolResultPruner", store=store,
    )
    pruner["thresholdChars"] = _integer(pruner["thresholdChars"],
                                         "toolResultPruner.thresholdChars", 1)
    pruner["headChars"] = _integer(pruner["headChars"], "toolResultPruner.headChars")
    pruner["tailChars"] = _integer(pruner["tailChars"], "toolResultPruner.tailChars")
    if (
        pruner["headChars"]
        + len(_DSH_TOOL_RESULT_PRUNE_MARKER)
        + pruner["tailChars"]
        > pruner["thresholdChars"]
    ):
        _fail("toolResultPruner保留区加DSH标记后不能超过阈值")

    agent = _merge_simple(DEFAULT_DSH_SETTINGS["agentLoop"],
                          overrides.get("agentLoop", {}),
                          frozenset({"maxParallelToolCalls"}), "agentLoop", store=store)
    agent["maxParallelToolCalls"] = _integer(agent["maxParallelToolCalls"],
                                               "agentLoop.maxParallelToolCalls", 1, 128)

    persistence = _merge_simple(DEFAULT_DSH_SETTINGS["persistence"],
                                overrides.get("persistence", {}),
                                frozenset({"listConcurrency"}),
                                "persistence", store=store)
    persistence["listConcurrency"] = _integer(
        persistence["listConcurrency"], "persistence.listConcurrency", 1, 128
    )

    query = _merge_simple(
        DEFAULT_DSH_SETTINGS["sessionQuery"], overrides.get("sessionQuery", {}),
        frozenset(DEFAULT_DSH_SETTINGS["sessionQuery"]), "sessionQuery", store=store,
    )
    if query["openAt"] not in {"startup", "first-search", "never"}:
        _fail("sessionQuery.openAt不受支持")
    if query["journalMode"] not in {"wal", "delete", "truncate", "persist"}:
        _fail("sessionQuery.journalMode不受支持")
    for field in ("defaultLimit", "maxLimit", "snippetChars",
                  "persistedReadConcurrency", "preparedSessionCacheSize"):
        query[field] = _integer(query[field], f"sessionQuery.{field}", 1, 1_000_000)
    query["readWindowMax"] = _integer(query["readWindowMax"],
                                       "sessionQuery.readWindowMax", 0, 1_000_000)
    if query["defaultLimit"] > query["maxLimit"]:
        _fail("sessionQuery.defaultLimit不能超过maxLimit")

    cache = _merge_simple(DEFAULT_DSH_SETTINGS["projectionCache"],
                          overrides.get("projectionCache", {}),
                          frozenset({"writeEveryEvents", "writeIntervalMs"}),
                          "projectionCache", store=store)
    cache["writeEveryEvents"] = _integer(cache["writeEveryEvents"],
                                          "projectionCache.writeEveryEvents", 1, 1_000_000)
    cache["writeIntervalMs"] = _integer(cache["writeIntervalMs"],
                                         "projectionCache.writeIntervalMs", 1, 86_400_000)

    # Preserve only explicit, validated preferences; defaults remain inherited.
    normalized = copy.deepcopy(overrides)
    if "compaction" in normalized:
        normalized["compaction"] = compaction
    canonical: dict[str, Any] = {}
    for section, values in normalized.items():
        kept = {}
        for field, item in values.items():
            if section == "compaction" and field == "maxTokens":
                derived = compaction.get(
                    "headroomTokens", DEFAULT_DSH_SETTINGS["compaction"]["headroomTokens"]
                )
                if item == derived:
                    continue
            elif (
                field in DEFAULT_DSH_SETTINGS[section]
                and item == DEFAULT_DSH_SETTINGS[section][field]
            ):
                continue
            kept[field] = copy.deepcopy(item)
        if kept:
            canonical[section] = kept
    return canonical


def resolve_dsh_settings(overrides: object) -> dict[str, Any]:
    normalized = validate_dsh_settings_overrides(overrides)
    effective = copy.deepcopy(DEFAULT_DSH_SETTINGS)
    for section, values in normalized.items():
        effective[section].update(copy.deepcopy(values))
    if "retainTokens" in normalized.get("compaction", {}):
        effective["compaction"].pop("retainRatio", None)
    if "retainRatio" in normalized.get("compaction", {}):
        effective["compaction"].pop("retainTokens", None)
    if "maxTokens" not in normalized.get("compaction", {}):
        effective["compaction"]["maxTokens"] = effective["compaction"]["headroomTokens"]
    return effective


def _field_sources(overrides: Mapping[str, Any], source: str) -> dict[str, Any]:
    result = copy.deepcopy(DEFAULT_DSH_SETTING_SOURCES)
    for section, values in overrides.items():
        section_sources = result.setdefault(section, {})
        for field in values:
            section_sources[field] = {
                "kind": "user_override",
                "source": source,
                "field": field,
            }
    return result


def _snapshot(stored: Mapping[str, Any] | None = None) -> dict[str, Any]:
    overrides = {} if stored is None else copy.deepcopy(stored["overrides"])
    source = "builtin" if stored is None else stored["source"]
    return {
        "contract": SETTINGS_CONTRACT,
        "policy_version": SETTINGS_VERSION,
        "generation": 0 if stored is None else stored["generation"],
        "source": source,
        "updated_at": None if stored is None else stored["updated_at"],
        "overrides": overrides,
        "follows_defaults": not overrides,
        "effective": resolve_dsh_settings(overrides),
        "defaults": copy.deepcopy(DEFAULT_DSH_SETTINGS),
        "default_sources": copy.deepcopy(DEFAULT_DSH_SETTING_SOURCES),
        "field_sources": _field_sources(overrides, source),
        "limits": copy.deepcopy(DSH_SETTING_LIMITS),
        "apply_scope": APPLY_SCOPE,
        "restart_required": stored is not None,
    }


def _decode(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object)
    except PlanError:
        raise
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise PlanError("DSH设置不是有效UTF-8 JSON", "native_dsh_settings_store_invalid") from exc
    required = {
        "contract", "base_policy_version", "generation", "overrides", "source", "updated_at"
    }
    if not isinstance(value, Mapping) or set(value) != required:
        _fail("DSH设置文件结构不合法", "native_dsh_settings_store_invalid")
    if value["contract"] != SETTINGS_CONTRACT or value["base_policy_version"] != SETTINGS_VERSION:
        _fail("DSH设置版本不受支持", "native_dsh_settings_store_invalid")
    generation = value["generation"]
    if type(generation) is not int or not 1 <= generation <= MAX_GENERATION:
        _fail("DSH设置代数不合法", "native_dsh_settings_store_invalid")
    if not isinstance(value["source"], str) or _SOURCE.fullmatch(value["source"]) is None:
        _fail("DSH设置来源不合法", "native_dsh_settings_store_invalid")
    if not isinstance(value["updated_at"], str):
        _fail("DSH设置时间不合法", "native_dsh_settings_store_invalid")
    try:
        timestamp = datetime.fromisoformat(value["updated_at"])
    except (OverflowError, ValueError) as exc:
        raise PlanError("DSH设置时间不合法", "native_dsh_settings_store_invalid") from exc
    if timestamp.tzinfo is None:
        _fail("DSH设置时间不合法", "native_dsh_settings_store_invalid")
    try:
        overrides = validate_dsh_settings_overrides(value["overrides"], store=True)
    except PlanError as exc:
        raise PlanError(str(exc), "native_dsh_settings_store_invalid", exc.details) from exc
    return {
        "generation": generation,
        "overrides": overrides,
        "source": value["source"],
        "updated_at": value["updated_at"],
    }


def _read(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PlanError("无法读取DSH设置", "native_dsh_settings_store_invalid") from exc
    if len(raw) > MAX_SETTINGS_BYTES:
        _fail("DSH设置文件超过256KiB", "native_dsh_settings_store_invalid")
    return _decode(raw)


def load_native_dsh_settings(profile_root: str | os.PathLike[str]) -> dict[str, Any]:
    _assert_legacy_migration_pending(profile_root)
    path = dsh_settings_path(profile_root)
    if not path.exists():
        return _snapshot(None)
    try:
        with file_lock(path.with_name(".native-agent-dsh-settings.lock")):
            return _snapshot(_read(path))
    except LockBusyError as exc:
        raise PlanError("DSH设置正在被修改，请稍后重试", "native_dsh_settings_store_busy") from exc


def _assert_legacy_migration_pending(profile_root) -> None:
    marker = Path(profile_root) / "runtime-profile" / "legacy-settings-migration.json"
    if marker.exists():
        _fail("这份旧设置已完成首次迁移，不能再作为活动配置读取或修改。请使用官方 DSH 设置页面或活动设置 CLI。",
              "native_dsh_settings_migrated", {"active_owner": "dsh_config_editor",
                                               "legacy_role": "one_time_migration_only"})


def _encode(*, generation: int, overrides: Mapping[str, Any], source: str,
            updated_at: str) -> bytes:
    value = {
        "contract": SETTINGS_CONTRACT,
        "base_policy_version": SETTINGS_VERSION,
        "generation": generation,
        "overrides": copy.deepcopy(dict(overrides)),
        "source": source,
        "updated_at": updated_at,
    }
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def _atomic_write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temporary = Path(name)
        with os.fdopen(fd, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        raise PlanError("无法写入DSH设置", "native_dsh_settings_store_write_failed") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def update_native_dsh_settings(
    profile_root: str | os.PathLike[str],
    overrides: Mapping[str, Any],
    *,
    expected_generation: int | None = None,
    source: str = "gui",
) -> dict[str, Any]:
    _assert_legacy_migration_pending(profile_root)
    if not isinstance(source, str) or _SOURCE.fullmatch(source) is None:
        _fail("DSH设置修改来源不合法")
    if expected_generation is not None and (
        type(expected_generation) is not int or not 0 <= expected_generation <= MAX_GENERATION
    ):
        _fail("期望DSH设置代数不合法")
    normalized = validate_dsh_settings_overrides(overrides)
    path = dsh_settings_path(profile_root)
    try:
        with file_lock(path.with_name(".native-agent-dsh-settings.lock"), timeout=5):
            _assert_legacy_migration_pending(profile_root)
            stored = _read(path)
            generation = 0 if stored is None else stored["generation"]
            if expected_generation is not None and expected_generation != generation:
                _fail(
                    "DSH设置已被其他窗口修改，请重新载入",
                    "native_dsh_settings_generation_conflict",
                    {"expected_generation": expected_generation, "actual_generation": generation},
                )
            if generation >= MAX_GENERATION:
                _fail("DSH设置代数已耗尽", "native_dsh_settings_store_write_failed")
            next_stored = {
                "generation": generation + 1,
                "overrides": normalized,
                "source": source,
                "updated_at": _now(),
            }
            _atomic_write(path, _encode(**next_stored))
            return _snapshot(next_stored)
    except LockBusyError as exc:
        raise PlanError("DSH设置正在被修改", "native_dsh_settings_store_busy") from exc


def reset_native_dsh_settings(
    profile_root: str | os.PathLike[str],
    *,
    expected_generation: int | None = None,
    source: str = "gui",
) -> dict[str, Any]:
    return update_native_dsh_settings(
        profile_root, {}, expected_generation=expected_generation, source=source
    )


__all__ = [
    "APPLY_SCOPE", "DEFAULT_DSH_SETTINGS", "DEFAULT_DSH_SETTING_SOURCES",
    "DEFAULT_PROFILE_RELATIVE_PATH",
    "DSH_RELEASE", "DSH_SETTING_LIMITS", "REVIEWED_MODEL_IDS",
    "SETTINGS_CONTRACT", "SETTINGS_FILE",
    "SETTINGS_VERSION", "default_dsh_profile_root", "dsh_settings_path",
    "load_native_dsh_settings",
    "reset_native_dsh_settings", "resolve_dsh_settings",
    "update_native_dsh_settings", "validate_dsh_settings_overrides",
]
