"""Generic binding between Agent-declared event topology and executable plan input.

The contract proves reference coverage and freshness only.  It never claims that
an Agent-declared character mechanic is true or that a selected runtime source is
the correct gameplay implementation.
"""
from __future__ import annotations

import copy
import re
from collections import Counter

from .event_topology import event_topology_fingerprint, validate_event_topology
from .files import execution_input_view, fingerprint, json_bytes

CONTRACT_ID = "event-topology-execution-bindings-v1"
RECEIPT_ID = "event-topology-execution-coverage-v1"
SCHEMA_VERSION = 1
MAX_BINDINGS = 8192
MAX_TEXT = 4096
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")

TARGET_ROOTS = {
    "hit": ("/manual_template/hits/",),
    "buff": ("/manual_template/buffs/",),
    "rotation_event": ("/rotation_model/events/",),
    "mechanics": ("/mechanics_model/",),
    "action_source": (
        "/action_event_profiles/", "/action_event_bindings/", "/action_event_overrides/",
        "/action_dispatch_sources/", "/attack_metadata_sources/", "/hit_parameter_sources/",
    ),
    "periodic_source": ("/periodic_attack_sources/",),
    "resource_source": ("/action_resource_sources/", "/energy_generation_sources/"),
    "effect_source": (
        "/passive_sources/", "/mechanism_sources/", "/gadget_sources/",
        "/limited_hit_benefit_sources/", "/reaction_benefit_sources/",
        "/snapshot_capture_sources/", "/target_condition_sources/",
    ),
}

NODE_TARGETS = {
    "contact": {"hit", "mechanics", "action_source", "periodic_source"},
    "damage": {"hit", "mechanics", "action_source", "periodic_source"},
    "application": {"hit", "mechanics", "action_source", "periodic_source"},
    "buff_interval": {"buff", "mechanics", "effect_source"},
    "resource": {"rotation_event", "mechanics", "resource_source"},
    "state": {"rotation_event", "mechanics", "effect_source"},
    "reaction_candidate": {"hit", "mechanics", "effect_source"},
    "finite_sequence": {"hit", "mechanics", "action_source", "periodic_source"},
    "periodic_channel": {"hit", "mechanics", "periodic_source"},
    "other": {"mechanics"},
}

_ROW_TARGET_ROOTS = {
    "hit": (("manual_template", "hits"),),
    "buff": (("manual_template", "buffs"),),
    "rotation_event": (("rotation_model", "events"),),
    "action_source": tuple((name,) for name in (
        "action_event_profiles", "action_event_bindings", "action_event_overrides",
        "action_dispatch_sources", "attack_metadata_sources", "hit_parameter_sources")),
    "periodic_source": (("periodic_attack_sources",),),
    "resource_source": (("action_resource_sources",), ("energy_generation_sources",)),
    "effect_source": tuple((name,) for name in (
        "passive_sources", "mechanism_sources", "gadget_sources",
        "limited_hit_benefit_sources", "reaction_benefit_sources",
        "snapshot_capture_sources", "target_condition_sources")),
}

_MECHANICS_ROW_KINDS = {
    "rules": set(NODE_TARGETS),
    "hit_events": {"contact", "damage", "application", "reaction_candidate"},
    "hit_patterns": {"contact", "damage", "application", "finite_sequence", "periodic_channel"},
    "condition_events": {"buff_interval", "state"},
    "resource_ownership": {"resource", "state"},
    "snapshot_captures": {"buff_interval", "state"},
    "fixed_effect_sources": {"buff_interval", "state"},
    "special_reaction_party_capabilities": {"state", "reaction_candidate"},
    "special_reaction_events": {"damage", "state", "reaction_candidate"},
    "elemental_state_events": {"application", "state", "reaction_candidate"},
    "application_events": {"contact", "application", "reaction_candidate"},
}
_MECHANICS_CONTRACT_KINDS = {
    "health_model": {"buff_interval", "state", "resource"},
    "attribute_model": {"buff_interval", "state"},
    "attribute_callbacks": {"buff_interval", "state"},
    "shared_pools": {"buff_interval", "state"},
    "state_runtime": {"buff_interval", "state"},
    "resource_counters": {"resource", "state"},
    "resource_counter_views": {"resource", "state"},
    "resource_reaction_contract": {"resource", "reaction_candidate", "state"},
    "resource_threshold_observers": {"resource", "buff_interval", "state"},
    "resource_threshold_triggers": {"resource", "buff_interval", "state"},
    "elemental_model": {"application", "state", "reaction_candidate"},
    "special_reaction_model": {"damage", "state", "reaction_candidate"},
}


class EventTopologyBindingError(ValueError):
    def __init__(self, path, message, code="invalid_event_topology_bindings"):
        self.path, self.message, self.code = path, message, code
        super().__init__(f"{path}: {message}")


def _fail(path, message, code="invalid_event_topology_bindings"):
    raise EventTopologyBindingError(path, message, code)


def _object(value, path, required):
    if not isinstance(value, dict) or set(value) != set(required):
        _fail(path, "must be a closed object with fields: " + ", ".join(sorted(required)))
    return value


def _stable_id(value, path):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        _fail(path, "must be a bounded stable identifier")
    return value


def _pointer_tokens(pointer):
    if not isinstance(pointer, str) or not pointer.startswith("/") or len(pointer) > 2048:
        _fail("binding.pointer", "must be a bounded JSON Pointer")
    result = []
    for raw in pointer[1:].split("/"):
        if re.search(r"~(?:[^01]|$)", raw):
            _fail("binding.pointer", "contains an invalid JSON Pointer escape")
        result.append(raw.replace("~1", "/").replace("~0", "~"))
    return result


def _resolve(plan, pointer):
    value = plan
    for token in _pointer_tokens(pointer):
        if isinstance(value, dict) and token in value:
            value = value[token]
        elif isinstance(value, list) and token.isdigit() and str(int(token)) == token \
                and int(token) < len(value):
            value = value[int(token)]
        else:
            _fail("binding.pointer", "does not resolve in the current executable input",
                  "event_topology_binding_target_missing")
    if value is None:
        _fail("binding.pointer", "cannot target a null input",
              "event_topology_binding_target_missing")
    return value


def _safe_fingerprint(value):
    try:
        return fingerprint(value)
    except (TypeError, ValueError, RecursionError):
        return None


def _target_shape_allowed(target_kind, node_kind, pointer, value):
    """Require bindings to select one executable consumer object, not a label leaf."""
    tokens = _pointer_tokens(pointer)
    if target_kind == "mechanics":
        if len(tokens) == 3 and tokens[0] == "mechanics_model" and tokens[2].isdigit():
            return (node_kind in _MECHANICS_ROW_KINDS.get(tokens[1], set())
                    and isinstance(value, dict))
        if (len(tokens) == 4 and tokens[:3] == ["mechanics_model", "elemental_model", "profiles"]
                and tokens[3].isdigit()):
            return node_kind in {"contact", "application", "state", "reaction_candidate"} \
                and isinstance(value, dict)
        return (len(tokens) == 2 and tokens[0] == "mechanics_model"
                and node_kind in _MECHANICS_CONTRACT_KINDS.get(tokens[1], set())
                and isinstance(value, (dict, list)))
    for root in _ROW_TARGET_ROOTS.get(target_kind, ()):
        if (len(tokens) == len(root) + 1 and tuple(tokens[:-1]) == root
                and tokens[-1].isdigit() and isinstance(value, dict)):
            return True
    return False


def validate_event_topology_bindings(document):
    try:
        size = len(json_bytes(document))
    except (AttributeError, TypeError, ValueError, RecursionError) as exc:
        raise EventTopologyBindingError("bindings", "must be bounded JSON") from exc
    if size > MAX_DOCUMENT_BYTES:
        _fail("bindings", "exceeds the 2 MiB execution-binding limit",
              "event_topology_bindings_too_large")
    root = _object(document, "bindings", {
        "contract_id", "schema_version", "topology_id", "topology_fingerprint", "bindings"})
    if root["contract_id"] != CONTRACT_ID or root["schema_version"] != SCHEMA_VERSION:
        _fail("bindings", "unsupported binding contract/version")
    _stable_id(root["topology_id"], "bindings.topology_id")
    if not isinstance(root["topology_fingerprint"], str) or not _HASH.fullmatch(
            root["topology_fingerprint"]):
        _fail("bindings.topology_fingerprint", "must be a lowercase sha256 digest")
    if not isinstance(root["bindings"], list) or len(root["bindings"]) > MAX_BINDINGS:
        _fail("bindings.bindings", f"must contain at most {MAX_BINDINGS} rows")
    seen = set()
    for index, item in enumerate(root["bindings"]):
        path = f"bindings.bindings[{index}]"
        row = _object(item, path, {
            "binding_id", "node_id", "target_kind", "pointer", "target_fingerprint", "note"})
        binding_id = _stable_id(row["binding_id"], path + ".binding_id")
        _stable_id(row["node_id"], path + ".node_id")
        if binding_id in seen:
            _fail(path + ".binding_id", "binding identity is duplicated")
        seen.add(binding_id)
        if row["target_kind"] not in TARGET_ROOTS:
            _fail(path + ".target_kind", "unsupported target kind")
        _pointer_tokens(row["pointer"])
        if (not isinstance(row["target_fingerprint"], str)
                or not _HASH.fullmatch(row["target_fingerprint"])):
            _fail(path + ".target_fingerprint", "must be a lowercase sha256 digest")
        if not isinstance(row["note"], str) or len(row["note"]) > MAX_TEXT:
            _fail(path + ".note", "must be bounded text")
    return document


def _compact_issue(code, path, message, **identity):
    return {"code": code, "path": path, "message": message, **identity}


def lint_event_topology_bindings(plan, topology, document):
    """Return bounded coverage against the current candidate input."""
    try:
        validate_event_topology(topology)
        validate_event_topology_bindings(document)
    except (ValueError, TypeError, KeyError) as exc:
        issue = _compact_issue(getattr(exc, "code", "invalid_event_topology_bindings"),
            getattr(exc, "path", "bindings"), getattr(exc, "message", str(exc))[:512])
        receipt = {"contract_id": RECEIPT_ID, "schema_version": SCHEMA_VERSION,
            "status": "invalid", "topology_fingerprint": None,
            "bindings_fingerprint": _safe_fingerprint(document),
            "target_projection_fingerprint": None, "counts": {}, "issues": [issue],
            "issues_total": 1, "issues_truncated": False,
            "character_semantics": "agent_declared_unverified"}
        receipt["receipt_fingerprint"] = fingerprint(receipt)
        return receipt

    topology_fp = event_topology_fingerprint(topology)
    issues = []
    if document["topology_id"] != topology["topology_id"]:
        issues.append(_compact_issue("event_topology_binding_identity_stale", "bindings.topology_id",
            "绑定合同不属于当前事件拓扑。"))
    if document["topology_fingerprint"] != topology_fp:
        issues.append(_compact_issue("event_topology_binding_fingerprint_stale",
            "bindings.topology_fingerprint", "绑定合同引用的事件拓扑已经变化。"))

    node_by_id = {row["node_id"]: row for row in topology["nodes"]}
    expected = {node_id for node_id, node in node_by_id.items()
                if node["status"] != "excluded" and node["kind"] != "action_anchor"}
    covered = set()
    targets = []
    try:
        executable = execution_input_view(plan)
    except (AttributeError, TypeError, ValueError, RecursionError) as exc:
        issue = _compact_issue("event_topology_binding_input_invalid", "plan",
                               str(exc)[:512] or "当前可执行输入不合法")
        receipt = {
            "contract_id": RECEIPT_ID, "schema_version": SCHEMA_VERSION,
            "status": "invalid", "topology_fingerprint": topology_fp,
            "bindings_fingerprint": fingerprint(document),
            "target_projection_fingerprint": None, "counts": {}, "issues": [issue],
            "issues_total": 1, "issues_truncated": False,
            "character_semantics": "agent_declared_unverified",
        }
        receipt["receipt_fingerprint"] = fingerprint(receipt)
        return receipt
    explicit_covered = set()
    for index, row in enumerate(document["bindings"]):
        path = f"bindings.bindings[{index}]"
        node = node_by_id.get(row["node_id"])
        if node is None or node["status"] == "excluded" or node["kind"] == "action_anchor":
            issues.append(_compact_issue("event_topology_binding_node_invalid", path + ".node_id",
                "绑定必须引用当前活跃且需要执行投影的事件节点。", node_id=row["node_id"]))
            continue
        if row["target_kind"] not in NODE_TARGETS[node["kind"]]:
            issues.append(_compact_issue("event_topology_binding_kind_mismatch", path + ".target_kind",
                "目标种类与事件节点种类不兼容。", node_id=row["node_id"]))
            continue
        pointer = row["pointer"]
        if (pointer == "/extensions" or pointer.startswith((
                "/extensions/event_topology", "/resolution", "/resolved_template", "/result"))):
            issues.append(_compact_issue("event_topology_binding_target_forbidden", path + ".pointer",
                "绑定不能指向拓扑、自身合同、缓存或结果字段。", node_id=row["node_id"]))
            continue
        prefixes = TARGET_ROOTS[row["target_kind"]]
        if prefixes and not any(pointer.startswith(prefix) for prefix in prefixes):
            issues.append(_compact_issue("event_topology_binding_root_mismatch", path + ".pointer",
                "目标路径不属于声明的通用输入种类。", node_id=row["node_id"]))
            continue
        try:
            value = _resolve(executable, pointer)
        except EventTopologyBindingError as exc:
            issues.append(_compact_issue(exc.code, path + ".pointer", exc.message,
                                         node_id=row["node_id"]))
            continue
        if not _target_shape_allowed(row["target_kind"], node["kind"], pointer, value):
            issues.append(_compact_issue("event_topology_binding_target_shape_invalid",
                path + ".pointer",
                "绑定必须指向与节点种类匹配的完整执行对象；说明字段、容器标签和other_input不授予覆盖。",
                node_id=row["node_id"]))
            continue
        target_fp = _safe_fingerprint(value)
        if target_fp is None:
            issues.append(_compact_issue("event_topology_binding_target_invalid",
                path + ".pointer", "目标对象不是可指纹化的JSON输入。", node_id=row["node_id"]))
            continue
        targets.append({"binding_id": row["binding_id"], "node_id": row["node_id"],
                        "target_kind": row["target_kind"], "pointer": pointer,
                        "target_fingerprint": target_fp})
        if row["target_fingerprint"] != target_fp:
            issues.append(_compact_issue("event_topology_binding_target_stale",
                path + ".target_fingerprint",
                "绑定目标完整对象已经变化；请显式核对当前对象并更新目标指纹。",
                node_id=row["node_id"],
                expected_target_fingerprint=row["target_fingerprint"],
                current_target_fingerprint=target_fp))
            continue
        covered.add(row["node_id"])
        explicit_covered.add(row["node_id"])

    # A validated Agent materialization is itself the execution projection for
    # newly-created generic objects.  Requiring a pointer to an object that
    # does not exist until the adapter runs would otherwise make the staged
    # workflow impossible to close.  This is deliberately a receipt-only
    # projection: it does not add a permissive pointer kind to the authored
    # binding document and it never covers automatic nodes unless the Agent
    # explicitly claims them in the bound materialization document.
    materialized_covered = set()
    extensions = plan.get("extensions") if isinstance(plan.get("extensions"), dict) else {}
    materialization = extensions.get("agent_execution_materialization")
    if isinstance(materialization, dict):
        from .agent_execution_materialization import (
            lint_agent_execution_materialization,
        )
        from .agent_execution_runtime import runtime_entry_capability

        materialization_receipt = lint_agent_execution_materialization(
            plan, topology, materialization)
        if materialization_receipt["status"] != "ready":
            issues.append(_compact_issue(
                "event_topology_materialization_binding_invalid",
                "extensions.agent_execution_materialization",
                "材料化输入尚未通过当前方案与拓扑校验，不能授予执行绑定覆盖。",
                materialization_status=materialization_receipt["status"],
            ))
        else:
            for index, entry in enumerate(materialization["entries"]):
                runtime_capability = runtime_entry_capability(entry)
                if runtime_capability["status"] == "unsupported":
                    issues.append(_compact_issue(
                        "event_topology_materialization_runtime_unsupported",
                        f"extensions.agent_execution_materialization.entries[{index}]",
                        "该材料化处理器版本尚无可执行运行时适配器，不能替代执行对象绑定。",
                        entry_id=entry["entry_id"], family=entry["family"],
                        handler_version=entry["handler_version"],
                        runtime_capability=runtime_capability["capability"],
                    ))
                    continue
                entry_projection = {
                    "entry_id": entry["entry_id"],
                    "family": entry["family"],
                    "handler_version": entry["handler_version"],
                    "node_ids": copy.deepcopy(entry["node_ids"]),
                    "source_ids": copy.deepcopy(entry["source_ids"]),
                    "coverage_keys": copy.deepcopy(entry["coverage_keys"]),
                    "payload": copy.deepcopy(entry["payload"]),
                }
                target_fp = fingerprint(entry_projection)
                for node_id in entry["node_ids"]:
                    if node_id in explicit_covered:
                        issues.append(_compact_issue(
                            "event_topology_execution_owner_conflict",
                            f"extensions.agent_execution_materialization.entries[{index}].node_ids",
                            "同一事件节点不能同时由既有执行对象和Agent材料化条目拥有。",
                            node_id=node_id, entry_id=entry["entry_id"],
                        ))
                        continue
                    materialized_covered.add(node_id)
                    covered.add(node_id)
                    targets.append({
                        "binding_id": "materialization:" + entry["entry_id"],
                        "node_id": node_id,
                        "target_kind": "agent_materialization",
                        "pointer": "materialization:" + entry["entry_id"],
                        "target_fingerprint": target_fp,
                    })

    missing = sorted(expected - covered)
    for node_id in missing[:20]:
        issues.append(_compact_issue("event_topology_execution_binding_missing", "bindings.bindings",
            "活跃事件节点尚未绑定当前可执行输入。", node_id=node_id))
    total_issues = len(issues) + max(0, len(missing) - 20)
    status = "complete" if not issues and not missing else "incomplete"
    shared_targets = []
    grouped_targets = {}
    for row in targets:
        key = (row["target_kind"], row["pointer"])
        grouped_targets.setdefault(key, set()).add(row["node_id"])
    for (target_kind, pointer), node_ids in sorted(grouped_targets.items()):
        if len(node_ids) > 1:
            shared_targets.append({"target_kind": target_kind, "pointer": pointer,
                                   "node_ids": sorted(node_ids)[:20],
                                   "node_ids_truncated": len(node_ids) > 20})
    receipt = {
        "contract_id": RECEIPT_ID, "schema_version": SCHEMA_VERSION, "status": status,
        "topology_fingerprint": topology_fp,
        "bindings_fingerprint": fingerprint(document),
        "target_projection_fingerprint": fingerprint(targets),
        "counts": {"expected_nodes": len(expected), "bound_nodes": len(covered),
                   "bindings": len(document["bindings"]), "resolved_bindings": len(targets)},
        "coverage": {"missing_node_ids": missing[:20],
                     "missing_node_ids_truncated": len(missing) > 20,
                     "materialized_node_ids": sorted(materialized_covered)[:20],
                     "materialized_node_ids_truncated": len(materialized_covered) > 20,
                     "target_kinds": dict(Counter(row["target_kind"] for row in targets)),
                     "shared_targets": shared_targets[:20],
                     "shared_targets_truncated": len(shared_targets) > 20},
        "issues": issues[:20], "issues_total": total_issues,
        "issues_truncated": total_issues > 20,
        "character_semantics": "agent_declared_unverified",
        "limitations": [
            "回执只证明Agent声明节点已绑定当前输入，不核实角色特有游戏事实。",
            "目标存在与指纹一致不代表所选目标具有正确游戏语义。",
        ],
    }
    receipt["receipt_fingerprint"] = fingerprint(receipt)
    return receipt


def new_event_topology_bindings(topology):
    validate_event_topology(topology)
    return {"contract_id": CONTRACT_ID, "schema_version": SCHEMA_VERSION,
            "topology_id": topology["topology_id"],
            "topology_fingerprint": event_topology_fingerprint(topology), "bindings": []}


def event_topology_binding_capabilities():
    return {"contract_id": CONTRACT_ID, "receipt_contract_id": RECEIPT_ID,
        "schema_version": SCHEMA_VERSION, "target_roots": copy.deepcopy(TARGET_ROOTS),
        "node_target_kinds": {key: sorted(value) for key, value in NODE_TARGETS.items()},
        "materialization_coverage": {
            "contract": "agent-execution-materialization-v1",
            "mode": "validated_new_execution_object",
            "requirements": [
                "材料化回执属于当前方案和拓扑且状态为ready",
                "条目机制族已有App运行时适配器",
                "节点没有同时绑定既有执行对象",
            ],
            "authority": "只计入Agent明确声明的node_ids；不会接管未声明的自动节点",
        },
        "template": {"binding_id": "binding.damage.1", "node_id": "node.damage.1",
                     "target_kind": "hit", "pointer": "/manual_template/hits/0",
                     "target_fingerprint": "0" * 64,
                     "note": "绑定当前方案中的具体命中输入"},
        "scope": "绑定行须保存完整目标对象的当前sha256；仅校验输入引用、覆盖和新鲜度，角色语义仍由Agent声明",
        "limits": {"document_bytes": MAX_DOCUMENT_BYTES, "bindings": MAX_BINDINGS,
                   "pointer_chars": 2048, "note_chars": MAX_TEXT}}


__all__ = ["CONTRACT_ID", "EventTopologyBindingError", "event_topology_binding_capabilities",
           "lint_event_topology_bindings", "new_event_topology_bindings",
           "validate_event_topology_bindings"]
