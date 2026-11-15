"""Exact, read-only attenuation source projection, separate from attack binding."""
from __future__ import annotations

import math
from decimal import ROUND_HALF_EVEN, Decimal

from genshin_db.runtime_catalog import RuntimeConfigError, dumps, excel_key, fingerprint

SOURCE_PATH = "ExcelBinOutput/AttackAttenuationExcelConfigData.json"
PROJECTION_SEMANTICS = "finite-binary-application-sequence-v1"
DAMAGE_SEMANTICS = "finite-binary-damage-sequence-v1"
ATTENUATION_LIMITS = {"max_source_rows": 4096, "max_bytes": 65536,
                      "max_executable_sequence_length": 64, "max_reset_ticks": 10**15}


def attenuation_capabilities():
    return {"profile_version": 1, "selection": "按group精确匹配，区分大小写，不推断攻击或共享组归属",
            "projection_semantics": PROJECTION_SEMANTICS, "limits": dict(ATTENUATION_LIMITS),
            "channels": {"application": "durabilitySequence（默认，旧输出兼容）", "damage": "damageSequence（独立伤害通道）"},
            "execution_scope": "有限二元durabilitySequence与正有效计时；非二元原文可查但execution.pending",
            "rounding": "解析后数值的十进制往返表示，half-even取最近微秒；回执raw/rounded/error，允许绝对误差<=0.5微秒",
            "notes": ["group是上游规则模板名，不自动成为角色攻击共享状态键",
                      "二元数值1.0/0.0仅在投影中规范成整数1/0，原始记录保持；布尔不是合法二元系数",
                      "damageSequence/enbreakSequence仅保留原文，不投影为附着序列",
                      "execution.ready不证明角色攻击与该规则的关系或完整游戏机制"]}


def _projection(record, channel="application"):
    if channel not in ("application", "damage"):
        raise ValueError("channel必须为application或damage")
    field = "durabilitySequence" if channel == "application" else "damageSequence"
    key = "durability_sequence" if channel == "application" else "damage_sequence"
    semantics = PROJECTION_SEMANTICS if channel == "application" else DAMAGE_SEMANTICS
    issues = []
    sequence = record.get(field)
    binary = isinstance(sequence, list) and bool(sequence) and all(
        type(v) in (int, float) and v in (0, 1) for v in sequence)
    if not isinstance(sequence, list) or not sequence:
        issues.append({"code": "missing_" + key})
    elif len(sequence) > ATTENUATION_LIMITS["max_executable_sequence_length"]:
        issues.append({"code": "unsupported_sequence_length", "length": len(sequence)})
    if isinstance(sequence, list) and sequence and not binary:
        issues.append({"code": "non_binary_" + key,
                       "message": "未解释非二元系数的附着语义，保留原始资料，不能按真假值截断。" if channel == "application" else "非二元伤害系数仅保留原文，不能按真假值截断。"})

    raw_time = record.get("resetCycle")
    ticks = rounded = error = None
    if type(raw_time) not in (int, float) or (type(raw_time) is float and not math.isfinite(raw_time)) or raw_time <= 0:
        issues.append({"code": "invalid_reset_cycle"})
    else:
        decimal_time = Decimal(str(raw_time))
        decimal_ticks = decimal_time * 1_000_000
        integer_ticks = decimal_ticks.to_integral_value(rounding=ROUND_HALF_EVEN)
        if not 1 <= integer_ticks <= ATTENUATION_LIMITS["max_reset_ticks"]:
            issues.append({"code": "unsupported_reset_tick_range"})
        else:
            ticks = int(integer_ticks)
            rounded_decimal = integer_ticks / 1_000_000
            rounded, error = float(rounded_decimal), float(rounded_decimal - decimal_time)
            if abs(rounded_decimal - decimal_time) > Decimal("0.0000005"):
                issues.append({"code": "reset_rounding_error"})
    projection = {"semantics": semantics,
                  "reset_cycle_seconds_raw": raw_time, "reset_cycle_ticks": ticks,
                  "reset_cycle_seconds_rounded": rounded, "rounding_error_seconds": error,
                  "rounding_method": "decimal_roundtrip_half_even_to_microseconds",
                  "max_absolute_rounding_error_seconds": 0.0000005,
                  key: [int(v) for v in sequence] if binary else None,
                  "sequence_exhausted_coefficient": 0}
    return projection, issues


def _assemble_attenuation_profile(conn, state, nodes, group, channel="application"):
    from genshin_db.runtime_queries import (
        _definition_summary,
        _identifier,
        _lookup,
    )

    _identifier(group, "group")
    if not group.strip():
        raise ValueError("group不能仅包含空白")
    version_line = state["version_line"]
    rows = nodes.source(SOURCE_PATH)[1]
    if not isinstance(rows, list):
        raise RuntimeConfigError("衰减配置根不是数组", "dataset_invalid", 500)
    if len(rows) > ATTENUATION_LIMITS["max_source_rows"]:
        raise RuntimeConfigError("衰减配置表行数超限", "response_too_large", 413)
    if state["indexer_version"] in ("4", "5", "6", "7"):
        definitions = _lookup(conn, version_line, "excel_record", {"group": group}, SOURCE_PATH)
        if len(definitions) != 1:
            raise RuntimeConfigError("衰减组不存在或不唯一", "not_found" if not definitions else "ambiguous_reference",
                                     404 if not definitions else 409, {"group": group, "count": len(definitions)})
        pointer = definitions[0]["json_pointer"]
        token = pointer[1:] if isinstance(pointer, str) and pointer.startswith("/") else ""
        if not token.isascii() or not token.isdecimal() or str(int(token)) != token or int(token) >= len(rows):
            raise RuntimeConfigError("衰减组定义位置无效", "dataset_invalid", 500)
        index = int(token)
        raw = rows[index]
        if not isinstance(raw, dict) or raw.get("group") != group:
            raise RuntimeConfigError("衰减组索引与原始group不符", "dataset_invalid", 500)
    else:
        # Version 2/3 retain their historical identity, usually source_index.
        matches = [(i, row) for i, row in enumerate(rows) if isinstance(row, dict) and row.get("group") == group]
        if len(matches) != 1:
            raise RuntimeConfigError("衰减组不存在或不唯一", "not_found" if not matches else "ambiguous_reference",
                                     404 if not matches else 409, {"group": group, "count": len(matches)})
        index, raw = matches[0]
        definitions = _lookup(conn, version_line, "excel_record",
                              excel_key(SOURCE_PATH, raw, index, indexer_version=state["indexer_version"]), SOURCE_PATH)
    if len(definitions) != 1 or definitions[0]["json_pointer"] != f"/{index}":
        raise RuntimeConfigError("衰减组定义索引不唯一或位置不符", "dataset_invalid", 500)
    row = definitions[0]
    record, origin = nodes.node(row)
    if record != raw:
        raise RuntimeConfigError("衰减组原始记录不符", "dataset_invalid", 500)
    projection, issues = _projection(record, channel)
    result = {"profile_version": 1, "status": "ready", "group": group, "snapshot": state,
              "source": {**origin, "definition_id": row["definition_id"], "node_sha256": row["node_sha256"],
                         "definition": _definition_summary(row),
                         "field_pointers": {name: f"/{index}/{name}" for name in
                                            ("group", "resetCycle", "durabilitySequence" if channel == "application" else "damageSequence") if name in record}},
              "raw_record": record, "projection": projection,
              "execution": {"status": "pending" if issues else "ready", "semantics": projection["semantics"],
                            "issues": issues, "attack_binding": "not_evaluated"},
              "limits": dict(ATTENUATION_LIMITS),
              "notes": attenuation_capabilities()["notes"]}
    # Stable compile receipt: UI wording, issue order, and explanatory notes
    # are not source identity. Source hashes and projection semantics are.
    identity = {
        "profile_version": result["profile_version"], "snapshot_id": state["snapshot_id"],
        "group": group, "source": {key: result["source"][key] for key in
                                    ("definition_id", "source_path", "json_pointer", "source_sha256", "node_sha256")},
        "projection": projection, "execution_status": result["execution"]["status"],
    }
    if channel == "damage":
        result["channel"] = identity["channel"] = channel
        result["notes"] = ["damageSequence是独立伤害系数，不能作为附着序列。",
                           "ready仅表示二元序列投影，不证明攻击归属或完整能力执行。"]
    result["content_fingerprint"] = fingerprint(identity)
    if len(dumps(result).encode("utf-8")) > ATTENUATION_LIMITS["max_bytes"]:
        raise RuntimeConfigError("衰减资料响应字节数超限", "response_too_large", 413)
    return result


def get_runtime_attenuation_profile(db_path, *, group, channel="application", version_line="release", snapshot_id=None):
    from genshin_db.runtime_queries import _identifier, _Nodes, _read

    _identifier(group, "group")
    if channel not in ("application", "damage"):
        raise ValueError("channel必须为application或damage")
    with _read(db_path, version_line, snapshot_id) as (conn, state):
        return _assemble_attenuation_profile(conn, state, _Nodes(conn, version_line), group, channel)
