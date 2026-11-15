"""Offline-verifiable proof for Agent-authored generic execution inputs.

The Composer report is an internal diagnostic surface.  A formal calculation
must instead carry the exact reviewed materialization receipt, effective input
and runtime installation receipt in the result package, plus one bounded proof
in ``result_receipt``.  This module never queries the database and never grants
execution authority; it only binds data that the generic runtime already
installed successfully.
"""
from __future__ import annotations

import copy
import json
from typing import Any

from .files import fingerprint

CONTRACT_ID = "agent-execution-result-proof-v1"
SCHEMA_VERSION = 1
MAX_PROOF_BYTES = 16 * 1024 * 1024

MATERIALIZATION_FIELD = "agent_execution_materialization_receipt"
DOCUMENT_FIELD = "agent_execution_materialization_document"
EFFECTIVE_INPUT_FIELD = "agent_execution_effective_input"
RUNTIME_RECEIPT_FIELD = "agent_execution_runtime_receipt"
PROOF_FIELD = "agent_execution_proof"

_MATERIALIZATION_KEYS = frozenset({
    "contract_id", "schema_version", "status", "materialization_id",
    "plan_input_fingerprint", "topology_fingerprint", "handler_set_fingerprint",
    "character_semantics", "database_access", "coverage",
    "uncovered_supported_node_ids", "issues", "receipt_fingerprint",
})
_EFFECTIVE_KEYS = frozenset({
    "contract_id", "schema_version", "materialization_id", "authority",
    "plan_input_fingerprint", "topology_fingerprint", "handler_set_fingerprint",
    "entries", "materialization_receipt_fingerprint", "bundle_fingerprint",
})
_EFFECTIVE_ENTRY_KEYS = frozenset({
    "entry_id", "family", "handler", "node_ids", "coverage_keys", "payload",
    "source_fingerprints",
})
_RUNTIME_KEYS = frozenset({
    "contract_id", "schema_version", "adapter_version", "status",
    "effective_input_fingerprint", "entries", "coverage", "binding_diagnostics",
    "effective_graph", "database_access", "character_identity_dispatch",
    "receipt_fingerprint",
})
_RUNTIME_ENTRY_KEYS = frozenset({
    "entry_id", "family", "adapter_id", "adapter_version", "status",
    "coverage_keys", "installed", "issues", "adapter_fingerprint",
})
_PROOF_KEYS = frozenset({
    "contract_id", "schema_version", "materialization_id",
    "materialization_document_fingerprint", "materialization_receipt_fingerprint",
    "effective_input_fingerprint", "runtime_receipt_fingerprint",
    "topology_fingerprint", "entries", "installed_entry_count",
    "installed_coverage_keys", "database_access", "proof_fingerprint",
})
_PROOF_ENTRY_KEYS = frozenset({
    "entry_id", "family", "handler", "node_ids", "coverage_keys",
    "source_fingerprints", "payload_fingerprint", "adapter_id",
    "adapter_version", "installed_fingerprint",
})


def _closed_keys(value: Any, allowed: frozenset[str], label: str) -> None:
    if not isinstance(value, dict):
        raise TypeError(label + "不是对象")
    unknown = set(value) - allowed
    missing = allowed - set(value)
    if unknown or missing:
        raise ValueError(
            label + "字段集合不符合当前合同"
            + ("；未知=" + ",".join(sorted(unknown)) if unknown else "")
            + ("；缺少=" + ",".join(sorted(missing)) if missing else "")
        )


def _validate_closed_envelopes(materialization: dict, effective: dict,
                               runtime: dict, proof: dict | None = None) -> None:
    _closed_keys(materialization, _MATERIALIZATION_KEYS, "Agent材料化回执")
    _closed_keys(effective, _EFFECTIVE_KEYS, "Agent有效输入")
    _closed_keys(runtime, _RUNTIME_KEYS, "Agent运行时回执")
    entries = effective.get("entries")
    if not isinstance(entries, list):
        raise TypeError("Agent有效输入条目不是列表")
    for index, row in enumerate(entries):
        _closed_keys(row, _EFFECTIVE_ENTRY_KEYS, f"Agent有效输入条目[{index}]")
        _closed_keys(row.get("handler"), frozenset({"handler_id", "version"}),
                     f"Agent有效输入处理器[{index}]")
    runtime_entries = runtime.get("entries")
    if not isinstance(runtime_entries, list):
        raise TypeError("Agent运行时条目不是列表")
    for index, row in enumerate(runtime_entries):
        _closed_keys(row, _RUNTIME_ENTRY_KEYS, f"Agent运行时条目[{index}]")
    if proof is not None:
        _closed_keys(proof, _PROOF_KEYS, "Agent执行结果证明")
        proof_entries = proof.get("entries")
        if not isinstance(proof_entries, list):
            raise TypeError("Agent执行证明条目不是列表")
        for index, row in enumerate(proof_entries):
            _closed_keys(row, _PROOF_ENTRY_KEYS, f"Agent执行证明条目[{index}]")


def _bounded(value: Any, label: str) -> None:
    try:
        raw = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise TypeError(label + "不是有限JSON") from exc
    if len(raw) > MAX_PROOF_BYTES:
        raise ValueError(label + "超过16MiB证明上限")


def _self_fingerprint(value: dict, field: str, label: str) -> None:
    expected = value.get(field)
    if not isinstance(expected, str) or len(expected) != 64:
        raise ValueError(label + "缺少自身指纹")
    unsigned = {key: item for key, item in value.items() if key != field}
    if expected != fingerprint(unsigned):
        raise ValueError(label + "自身指纹不一致")


def _document_effective_entries(document: dict) -> list[dict]:
    """Project the only effective entries authorized by the frozen document.

    The exported effective bundle is intentionally self contained, but it is
    not a second authoring surface.  Rebuilding this small projection here
    prevents an otherwise well-formed payload from being substituted and all
    public SHA fields being recomputed after calculation.
    """

    from .agent_execution_materialization import HANDLER_REGISTRY

    sources = {
        row["source_id"]: row["fingerprint"]
        for row in document["sources"]
    }
    projected = []
    for row in document["entries"]:
        handler = HANDLER_REGISTRY[row["family"]]
        projected.append({
            "entry_id": row["entry_id"],
            "family": row["family"],
            "handler": {
                "handler_id": handler.handler_id,
                "version": row["handler_version"],
            },
            "node_ids": copy.deepcopy(row["node_ids"]),
            "coverage_keys": copy.deepcopy(row["coverage_keys"]),
            "payload": copy.deepcopy(row["payload"]),
            "source_fingerprints": [
                sources[source_id] for source_id in row["source_ids"]
            ],
        })
    return projected


def _validate_document_effective_chain(
        document: dict, materialization: dict, effective: dict) -> None:
    """Bind document, lint receipt and effective bundle without database I/O."""

    from .agent_execution_materialization import EFFECTIVE_AUTHORITY

    expected_coverage: dict[str, dict[str, int]] = {}
    for row in document["entries"]:
        count = len(row["node_ids"])
        bucket = expected_coverage.setdefault(
            row["family"], {"declared": 0, "covered": 0})
        bucket["declared"] += count
        bucket["covered"] += count

    plan_binding = document["plan_binding"]
    topology_binding = document["topology_binding"]
    if (
        materialization.get("plan_input_fingerprint")
        != plan_binding["input_fingerprint"]
        or materialization.get("topology_fingerprint")
        != topology_binding["topology_fingerprint"]
        or materialization.get("handler_set_fingerprint")
        != document["handler_set_fingerprint"]
        or materialization.get("character_semantics") != "agent_declared"
        or materialization.get("database_access") != "none"
        or materialization.get("coverage") != expected_coverage
        or materialization.get("uncovered_supported_node_ids") != []
        or materialization.get("issues") != []
    ):
        raise ValueError("Agent材料化回执不能由导出的强类型文档重建")

    if (
        effective.get("authority") != EFFECTIVE_AUTHORITY
        or effective.get("plan_input_fingerprint")
        != materialization["plan_input_fingerprint"]
        or effective.get("topology_fingerprint")
        != materialization["topology_fingerprint"]
        or effective.get("handler_set_fingerprint")
        != materialization["handler_set_fingerprint"]
        or effective.get("entries") != _document_effective_entries(document)
    ):
        raise ValueError("Agent有效输入不能由导出的强类型文档确定性重建")


def _validate_runtime_projection(effective: dict, runtime: dict) -> None:
    """Require the complete runtime ledger to cover the exact effective input."""

    effective_entries = effective["entries"]
    runtime_entries = runtime["entries"]
    if (
        runtime.get("database_access") != "none"
        or runtime.get("character_identity_dispatch") != "forbidden"
        or [row.get("entry_id") for row in runtime_entries]
        != [row.get("entry_id") for row in effective_entries]
    ):
        raise ValueError("Agent运行时回执不属于当前有效输入")

    installed_keys: list[str] = []
    for entry, installed in zip(effective_entries, runtime_entries, strict=True):
        if (
            installed.get("coverage_keys") != entry.get("coverage_keys")
            or installed.get("status") != "installed"
            or installed.get("issues") != []
        ):
            raise ValueError("Agent运行时条目没有完整安装当前有效输入")
        installed_keys.extend(entry["coverage_keys"])
    if runtime.get("coverage") != {
        "installed_keys": installed_keys,
        "pending_keys": [],
    }:
        raise ValueError("Agent运行时覆盖账不能由当前有效输入重建")


def _entry_projection(effective: dict, runtime: dict) -> list[dict]:
    effective_entries = effective.get("entries")
    runtime_entries = runtime.get("entries")
    if (not isinstance(effective_entries, list)
            or not isinstance(runtime_entries, list)
            or len(effective_entries) > 4096
            or len(runtime_entries) > 4096):
        raise ValueError("Agent执行条目列表缺失或越界")
    effective_by_id = {}
    for row in effective_entries:
        if not isinstance(row, dict) or not isinstance(row.get("entry_id"), str):
            raise TypeError("Agent有效输入条目无效")
        if row["entry_id"] in effective_by_id:
            raise ValueError("Agent有效输入条目身份重复")
        effective_by_id[row["entry_id"]] = row
    runtime_by_id = {}
    for row in runtime_entries:
        if not isinstance(row, dict) or not isinstance(row.get("entry_id"), str):
            raise TypeError("Agent运行时回执条目无效")
        if row["entry_id"] in runtime_by_id:
            raise ValueError("Agent运行时回执条目身份重复")
        _self_fingerprint(row, "adapter_fingerprint", "Agent运行时适配回执")
        runtime_by_id[row["entry_id"]] = row
    if set(effective_by_id) != set(runtime_by_id):
        raise ValueError("Agent有效输入与运行时回执条目集合不一致")

    projected = []
    for entry_id in sorted(effective_by_id):
        entry = effective_by_id[entry_id]
        installed = runtime_by_id[entry_id]
        if (installed.get("family") != entry.get("family")
                or installed.get("status") != "installed"
                or installed.get("adapter_id")
                != "generic-runtime." + entry.get("family", "")
                or installed.get("adapter_version")
                != runtime.get("adapter_version")):
            raise ValueError("Agent执行条目未完整安装或机制族不一致")
        coverage = entry.get("coverage_keys")
        nodes = entry.get("node_ids")
        sources = entry.get("source_fingerprints")
        if (not isinstance(coverage, list) or not coverage
                or not isinstance(nodes, list) or not nodes
                or not isinstance(sources, list) or not sources
                or any(not isinstance(value, str) for value in [*coverage, *nodes, *sources])):
            raise ValueError("Agent执行条目缺少节点、来源或覆盖范围")
        projected.append({
            "entry_id": entry_id,
            "family": entry["family"],
            "handler": copy.deepcopy(entry.get("handler")),
            "node_ids": copy.deepcopy(nodes),
            "coverage_keys": copy.deepcopy(coverage),
            "source_fingerprints": copy.deepcopy(sources),
            "payload_fingerprint": fingerprint(entry.get("payload")),
            "adapter_id": installed.get("adapter_id"),
            "adapter_version": installed.get("adapter_version"),
            "installed_fingerprint": fingerprint(installed.get("installed")),
        })
    return projected


def build_agent_execution_proof(plan: dict, materialization: dict,
                                effective: dict, runtime: dict) -> dict:
    from .agent_execution_materialization import (
        EFFECTIVE_INPUT_CONTRACT_ID,
        RECEIPT_CONTRACT_ID,
    )
    from .agent_execution_materialization import (
        SCHEMA_VERSION as MATERIALIZATION_SCHEMA_VERSION,
    )
    from .agent_execution_runtime import (
        ADAPTER_VERSION,
    )
    from .agent_execution_runtime import (
        CONTRACT_ID as RUNTIME_CONTRACT_ID,
    )
    from .agent_execution_runtime import (
        SCHEMA_VERSION as RUNTIME_SCHEMA_VERSION,
    )

    document = (plan.get("extensions") or {}).get(
        "agent_execution_materialization")
    if not isinstance(document, dict):
        raise TypeError("正式Agent执行证明缺少原始强类型材料化文档")
    _validate_closed_envelopes(materialization, effective, runtime)
    if (materialization.get("contract_id") != RECEIPT_CONTRACT_ID
            or materialization.get("schema_version")
            != MATERIALIZATION_SCHEMA_VERSION
            or materialization.get("status") != "ready"):
        raise ValueError("Agent材料化回执未就绪")
    _self_fingerprint(
        materialization, "receipt_fingerprint", "Agent材料化回执")
    _validate_document_effective_chain(document, materialization, effective)
    if (effective.get("contract_id") != EFFECTIVE_INPUT_CONTRACT_ID
            or effective.get("schema_version")
            != MATERIALIZATION_SCHEMA_VERSION
            or effective.get("materialization_receipt_fingerprint")
            != materialization["receipt_fingerprint"]):
        raise ValueError("Agent有效输入没有绑定材料化回执")
    _self_fingerprint(effective, "bundle_fingerprint", "Agent有效输入")
    if (runtime.get("contract_id") != RUNTIME_CONTRACT_ID
            or runtime.get("schema_version") != RUNTIME_SCHEMA_VERSION
            or runtime.get("adapter_version") != ADAPTER_VERSION
            or runtime.get("status") != "complete"
            or runtime.get("effective_input_fingerprint")
            != effective["bundle_fingerprint"]):
        raise ValueError("Agent运行时回执未完整绑定有效输入")
    _self_fingerprint(runtime, "receipt_fingerprint", "Agent运行时回执")
    _validate_runtime_projection(effective, runtime)
    from .periodic_execution_scope import periodic_execution_scope
    periodic_execution_scope(runtime, effective)
    entries = _entry_projection(effective, runtime)
    proof = {
        "contract_id": CONTRACT_ID,
        "schema_version": SCHEMA_VERSION,
        "materialization_id": effective.get("materialization_id"),
        "materialization_document_fingerprint": fingerprint(document),
        "materialization_receipt_fingerprint": fingerprint(materialization),
        "effective_input_fingerprint": fingerprint(effective),
        "runtime_receipt_fingerprint": fingerprint(runtime),
        "topology_fingerprint": effective.get("topology_fingerprint"),
        "entries": entries,
        "installed_entry_count": len(entries),
        "installed_coverage_keys": [
            key for row in entries for key in row["coverage_keys"]
        ],
        "database_access": "none",
    }
    proof["proof_fingerprint"] = fingerprint(proof)
    _bounded(proof, "Agent执行结果证明")
    return proof


def attach_agent_execution_result(plan: dict, result: dict,
                                  report: dict) -> dict | None:
    materialization = report.get("agent_execution_materialization_receipt")
    effective = report.get("agent_execution_effective_input")
    runtime = report.get("agent_execution_runtime_receipt")
    signals = (materialization, effective, runtime)
    if all(value is None for value in signals):
        return None
    if not all(isinstance(value, dict) for value in signals):
        raise ValueError("Agent执行链缺少材料化、有效输入或运行时回执")
    proof = build_agent_execution_proof(plan, materialization, effective, runtime)
    document = (plan.get("extensions") or {})[
        "agent_execution_materialization"]
    result[DOCUMENT_FIELD] = copy.deepcopy(document)
    result[MATERIALIZATION_FIELD] = copy.deepcopy(materialization)
    result[EFFECTIVE_INPUT_FIELD] = copy.deepcopy(effective)
    result[RUNTIME_RECEIPT_FIELD] = copy.deepcopy(runtime)
    from .periodic_execution_scope import periodic_execution_scope
    periodic_execution_scope(runtime, effective,
        (result.get('damage_report_execution') or {}).get('rotation_model')
        or (result.get('rotation_check') or {}).get('resolved_rotation_model'))
    return proof


def validate_agent_execution_result_receipt(result: dict, receipt: dict) -> None:
    from .agent_execution_materialization import (
        EFFECTIVE_INPUT_CONTRACT_ID,
        RECEIPT_CONTRACT_ID,
    )
    from .agent_execution_materialization import (
        SCHEMA_VERSION as MATERIALIZATION_SCHEMA_VERSION,
    )
    from .agent_execution_runtime import (
        ADAPTER_VERSION,
    )
    from .agent_execution_runtime import (
        CONTRACT_ID as RUNTIME_CONTRACT_ID,
    )
    from .agent_execution_runtime import (
        SCHEMA_VERSION as RUNTIME_SCHEMA_VERSION,
    )

    document = result.get(DOCUMENT_FIELD)
    materialization = result.get(MATERIALIZATION_FIELD)
    effective = result.get(EFFECTIVE_INPUT_FIELD)
    runtime = result.get(RUNTIME_RECEIPT_FIELD)
    proof = receipt.get(PROOF_FIELD)
    signals = (document, materialization, effective, runtime, proof)
    if all(value is None for value in signals):
        return
    if not all(isinstance(value, dict) for value in signals):
        raise TypeError("Agent执行结果缺少完整离线证明链")
    _validate_closed_envelopes(materialization, effective, runtime, proof)
    from .agent_execution_materialization import (
        validate_agent_execution_materialization,
    )

    validate_agent_execution_materialization(document)
    # Rebuild the proof without querying a database.  The exported effective
    # input is the exact frozen execution input; the receipt only adds the
    # original document fingerprint, which is preserved verbatim here.
    if (proof.get("contract_id") != CONTRACT_ID
            or proof.get("schema_version") != SCHEMA_VERSION):
        raise ValueError("Agent执行结果证明合同无效")
    if (materialization.get("contract_id") != RECEIPT_CONTRACT_ID
            or materialization.get("schema_version")
            != MATERIALIZATION_SCHEMA_VERSION
            or materialization.get("status") != "ready"):
        raise ValueError("Agent材料化回执合同或状态无效")
    if (effective.get("contract_id") != EFFECTIVE_INPUT_CONTRACT_ID
            or effective.get("schema_version")
            != MATERIALIZATION_SCHEMA_VERSION):
        raise ValueError("Agent有效输入合同无效")
    if (runtime.get("contract_id") != RUNTIME_CONTRACT_ID
            or runtime.get("schema_version") != RUNTIME_SCHEMA_VERSION
            or runtime.get("adapter_version") != ADAPTER_VERSION
            or runtime.get("status") != "complete"):
        raise ValueError("Agent运行时回执合同或状态无效")
    materialization_ids = {
        document.get("materialization_id"),
        materialization.get("materialization_id"),
        effective.get("materialization_id"),
        proof.get("materialization_id"),
    }
    if None in materialization_ids or len(materialization_ids) != 1:
        raise ValueError("Agent执行证明的材料化身份不一致")
    if proof.get("materialization_document_fingerprint") != fingerprint(document):
        raise ValueError("Agent执行证明不属于导出的强类型材料化文档")
    _self_fingerprint(proof, "proof_fingerprint", "Agent执行结果证明")
    _self_fingerprint(materialization, "receipt_fingerprint", "Agent材料化回执")
    _self_fingerprint(effective, "bundle_fingerprint", "Agent有效输入")
    _self_fingerprint(runtime, "receipt_fingerprint", "Agent运行时回执")
    _validate_document_effective_chain(document, materialization, effective)
    _validate_runtime_projection(effective, runtime)
    from .periodic_execution_scope import periodic_execution_scope
    periodic_execution_scope(runtime, effective,
        (result.get('damage_report_execution') or {}).get('rotation_model')
        or (result.get('rotation_check') or {}).get('resolved_rotation_model'))
    if (effective.get("materialization_receipt_fingerprint")
            != materialization["receipt_fingerprint"]
            or runtime.get("effective_input_fingerprint")
            != effective["bundle_fingerprint"]
            or proof.get("materialization_receipt_fingerprint")
            != fingerprint(materialization)
            or proof.get("effective_input_fingerprint") != fingerprint(effective)
            or proof.get("runtime_receipt_fingerprint") != fingerprint(runtime)):
        raise ValueError("Agent执行结果证明链指纹不一致")
    entries = _entry_projection(effective, runtime)
    if (proof.get("entries") != entries
            or proof.get("installed_entry_count") != len(entries)
            or proof.get("installed_coverage_keys")
            != [key for row in entries for key in row["coverage_keys"]]
            or proof.get("topology_fingerprint")
            != effective.get("topology_fingerprint")
            or proof.get("database_access") != "none"):
        raise ValueError("Agent执行结果证明不能由有效输入和运行时回执重建")
    if receipt.get("result_fingerprint") != fingerprint(result):
        raise ValueError("Agent执行结果证明没有绑定完整计算结果")
    _bounded(materialization, "Agent材料化回执")
    _bounded(document, "Agent强类型材料化文档")
    _bounded(effective, "Agent有效输入")
    _bounded(runtime, "Agent运行时回执")


__all__ = [
    "CONTRACT_ID", "DOCUMENT_FIELD", "EFFECTIVE_INPUT_FIELD", "MATERIALIZATION_FIELD",
    "PROOF_FIELD", "RUNTIME_RECEIPT_FIELD", "attach_agent_execution_result",
    "build_agent_execution_proof", "validate_agent_execution_result_receipt",
]
