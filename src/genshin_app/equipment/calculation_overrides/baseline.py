"""Automatic baseline bundle and stable target adapters for calculation overrides."""

from __future__ import annotations

import copy
import hashlib
import sqlite3
from pathlib import Path
from typing import Any

from genshin_db import queries

from .identity import stable_target_identity
from .schema import FIELD, OverrideDeclarationError, fingerprint

ABSENT_TARGET = {"state": "absent"}

_COLLECTIONS = {
    "manual_hit": ("template", "hits", "id", {"manual_template.hits", "template.hits"}),
    "manual_buff": ("template", "buffs", "id", {"manual_template.buffs", "template.buffs"}),
    "rotation_action": ("rotation_model", "actions", "id", {"rotation_model.actions"}),
    "rotation_event": ("rotation_model", "events", "id", {"rotation_model.events"}),
    "mechanics_rule": ("mechanics_model", "rules", "id", {"mechanics_model.rules"}),
    "mechanics_hit_event": (
        "mechanics_model", "hit_events", "hit_id", {"mechanics_model.hit_events"},
    ),
    "mechanics_hit_pattern": (
        "mechanics_model", "hit_patterns", "id", {"mechanics_model.hit_patterns"},
    ),
    "mechanics_application_event": (
        "mechanics_model", "application_events", "id",
        {"mechanics_model.application_events"},
    ),
    "mechanics_condition_event": (
        "mechanics_model", "condition_events", "id", {"mechanics_model.condition_events"},
    ),
}

# A state transition is an execution role, not a character-specific type.  The
# stable scope selects one of the generic engine inputs that can carry state.
# Replacing the complete member makes the ordinary validators and executor
# replay every downstream effect instead of patching a result trace.
_STATE_TRANSITION_COLLECTIONS = {
    "template.buffs": ("template", ("buffs",), "id"),
    "manual_template.buffs": ("template", ("buffs",), "id"),
    "rotation_model.events": ("rotation_model", ("events",), "id"),
    "mechanics_model.application_events": (
        "mechanics_model", ("application_events",), "id",
    ),
    "mechanics_model.condition_events": (
        "mechanics_model", ("condition_events",), "id",
    ),
    "mechanics_model.state_runtime.lifecycles": (
        "mechanics_model", ("state_runtime", "lifecycles"), "id",
    ),
    "mechanics_model.state_runtime.timers": (
        "mechanics_model", ("state_runtime", "timers"), "id",
    ),
}

# A computed-value override is bound to an existing damage event.  It does not
# replace the hit input; application.py records a post-formula event overlay
# and service.py re-derives summaries from the resulting per-event ledger.
_COMPUTED_EVENT_COLLECTIONS = {
    "result.hits": ("template", ("hits",), "id"),
}

# ``replace_input`` is intentionally narrower than whole-event replacement.
# Every admitted key is a non-identity execution input owned by the selected
# stable entity.  IDs and relationship keys (notably action_id/hit_id) must be
# changed through a reviewed whole-event replacement instead.
_PARAMETER_FIELDS = {
    "manual_hit": {
        "t", "multiplier", "count", "scaling_stat", "damage_system", "element",
        "cast_t", "landed",
    },
    "manual_buff": {"start", "end", "scope"},
    "rotation_action": {
        "start", "order", "occupation_end", "switch_allowed_at",
    },
    "rotation_event": {
        "t", "credit_at", "order", "count", "lost_count",
        "required", "amount", "operation", "particle_type", "element", "source_kind",
    },
    # Rule graphs and elemental application/contact payloads contain nested
    # identities and relationship bindings.  They must be replaced as whole,
    # reviewed events until a future typed leaf-path contract exists.
    "mechanics_rule": set(),
    "mechanics_hit_event": {"order", "report"},
    "mechanics_hit_pattern": {"report"},
    "mechanics_application_event": {"t", "order"},
    "mechanics_condition_event": {"t", "order", "value"},
}


def _plan_input_without_overrides(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(value)
        for key, value in plan.items()
        if key not in {
            # Optimistic-concurrency revision is not a calculation input.
            # Keeping it here would make an accepted override stale merely
            # because the unchanged draft was committed.  ``plan_id`` remains
            # in the basis so acceptance cannot be replayed onto another plan.
            FIELD,
            "revision",
            "resolution",
            "resolved_template",
            "external_damage_supplements",
        }
    }


def database_snapshot_fingerprint(db_path: str | Path) -> str:
    """Fingerprint the actual database identity used by one override review.

    Release databases expose immutable release-core and mutable-layer hashes.
    Legacy development fixtures fall back to the complete SQLite file hash;
    compiler-output hashes are never presented as database identity.
    """

    path = Path(db_path)
    if not path.is_file():
        return fingerprint({"state": "database_absent"})
    try:
        from genshin_db.release_profile import read_database_identity

        identity = read_database_identity(path, include_file_hash=False)
        return fingerprint({"kind": "release_identity", "identity": identity})
    except (OSError, sqlite3.Error, TypeError, ValueError):
        # A valid legacy DB may not carry the release manifest.  Hashing the
        # whole file is slower, but overrides are exceptional and this keeps
        # the freshness promise exact instead of trusting mutable metadata.
        try:
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            # Also prove that this is a readable project database, rather than
            # silently accepting an arbitrary file with a stable hash.
            queries.get_meta(path)
            return fingerprint({
                "kind": "legacy_full_file", "sha256": digest.hexdigest(),
                "size_bytes": path.stat().st_size,
            })
        except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
            raise OverrideDeclarationError(
                "override_database_identity_unavailable",
                f"无法读取覆盖声明所需的数据库快照身份：{exc}",
            ) from exc


def automatic_bundle(
    plan: dict[str, Any],
    template: dict[str, Any],
    rotation_model: dict[str, Any] | None,
    mechanics_model: dict[str, Any] | None,
    mechanics_matches: Any = None,
    *,
    database_snapshot_fingerprint: str,
    engine_contract_fingerprint: str,
) -> dict[str, Any]:
    """Freeze the automatic output before any Agent override is applied."""

    bundle = {
        "template": copy.deepcopy(template),
        "rotation_model": copy.deepcopy(rotation_model),
        "mechanics_model": copy.deepcopy(mechanics_model),
        "mechanics_matches": copy.deepcopy(mechanics_matches),
        "computed_value_overrides": [],
    }
    extensions = plan.get("extensions") if isinstance(plan.get("extensions"), dict) else {}
    topology = extensions.get("event_topology")
    bindings = extensions.get("event_topology_bindings")
    basis = {
        "plan_input_fingerprint": fingerprint(_plan_input_without_overrides(plan)),
        "automatic_baseline_fingerprint": fingerprint(bundle),
        "database_snapshot_fingerprint": database_snapshot_fingerprint,
        "engine_contract_fingerprint": engine_contract_fingerprint,
        "topology_fingerprint": fingerprint(topology) if topology is not None else None,
        "execution_bindings_fingerprint": fingerprint(bindings) if bindings is not None else None,
    }
    return {"bundle": bundle, "basis": basis}


def _scoped_collection(
    bundle: dict[str, Any], target: dict[str, Any],
    specs: dict[str, tuple[str, tuple[str, ...], str]],
):
    spec = specs.get(target["scope_id"])
    if spec is None:
        raise OverrideDeclarationError(
            "override_target_scope_mismatch", "目标scope不在通用执行范围内"
        )
    root, path, id_key = spec
    parent = bundle.get(root)
    if parent is None:
        raise OverrideDeclarationError(
            "override_target_domain_unavailable", "当前自动基线没有该执行模型"
        )
    for key in path[:-1]:
        if not isinstance(parent, dict) or parent.get(key) is None:
            raise OverrideDeclarationError(
                "override_target_domain_unavailable", "当前自动基线没有该状态容器"
            )
        parent = parent[key]
    if not isinstance(parent, dict):
        raise OverrideDeclarationError(
            "override_target_invalid", "目标状态容器不是对象"
        )
    rows = parent.get(path[-1])
    if not isinstance(rows, list):
        raise OverrideDeclarationError(
            "override_target_invalid", "目标状态集合不是列表"
        )
    return rows, id_key


def _collection(bundle: dict[str, Any], target: dict[str, Any]):
    domain = target["domain"]
    if domain == "state_transition":
        return _scoped_collection(
            bundle, target, _STATE_TRANSITION_COLLECTIONS
        )
    if domain == "computed_event":
        return _scoped_collection(bundle, target, _COMPUTED_EVENT_COLLECTIONS)
    lookup_domain = domain
    if domain == "parameter_input":
        scope = target["scope_id"]
        match = next((name for name, row in _COLLECTIONS.items() if scope in row[3]), None)
        if match is None:
            raise OverrideDeclarationError(
                "override_target_domain_unsupported",
                "输入替换的scope不在首批强类型目标范围内",
            )
        lookup_domain = match
        component = target.get("component_id")
        if component not in _PARAMETER_FIELDS[lookup_domain]:
            raise OverrideDeclarationError(
                "override_parameter_unsupported",
                "输入替换只能修改目标域已声明的非身份参数",
            )
    if lookup_domain not in _COLLECTIONS:
        raise OverrideDeclarationError(
            "override_target_domain_unsupported", "目标域尚未接入有效执行图"
        )
    root, name, id_key, scopes = _COLLECTIONS[lookup_domain]
    if target["scope_id"] not in scopes:
        raise OverrideDeclarationError(
            "override_target_scope_mismatch", "目标scope与目标域不一致"
        )
    parent = bundle.get(root)
    if parent is None:
        if root in {"rotation_model", "mechanics_model"}:
            raise OverrideDeclarationError(
                "override_target_domain_unavailable", "当前自动基线没有该执行模型"
            )
        raise OverrideDeclarationError("override_target_missing", "目标集合不存在")
    rows = parent.setdefault(name, [])
    if type(rows) is not list:
        raise OverrideDeclarationError("override_target_invalid", "目标集合不是列表")
    return rows, id_key


def _matches(row: dict[str, Any], target: dict[str, Any], id_key: str) -> bool:
    if row.get(id_key) != target["entity_id"]:
        return False
    if (
        target["scope_id"] == "mechanics_model.hit_events"
        and target.get("action_id") is not None
    ):
        return row.get("action_id") == target["action_id"]
    return True


def locate_target(bundle: dict[str, Any], target: dict[str, Any], *, allow_absent: bool = False):
    rows, id_key = _collection(bundle, target)
    matches = [(index, row) for index, row in enumerate(rows)
               if isinstance(row, dict) and _matches(row, target, id_key)]
    if len(matches) > 1:
        raise OverrideDeclarationError("override_target_ambiguous", "稳定目标身份匹配多个对象")
    if not matches:
        if allow_absent:
            return rows, id_key, None, copy.deepcopy(ABSENT_TARGET)
        raise OverrideDeclarationError("override_target_missing", "稳定目标在自动基线中不存在")
    index, row = matches[0]
    if target["domain"] == "parameter_input":
        field = target.get("component_id")
        if not isinstance(field, str) or not field or field not in row:
            raise OverrideDeclarationError(
                "override_parameter_missing", "输入替换需要component_id指向既有字段"
            )
        return rows, id_key, index, copy.deepcopy(row[field])
    return rows, id_key, index, copy.deepcopy(row)


def _direct_action_anchor(nodes: dict[str, dict[str, Any]], node_id: str) -> str | None:
    """Resolve a finite node-anchor chain to its declared action identity."""

    seen: set[str] = set()
    current = node_id
    while current not in seen:
        seen.add(current)
        node = nodes.get(current)
        if node is None:
            return None
        schedule = node.get("schedule")
        anchor = schedule.get("anchor") if isinstance(schedule, dict) else None
        if not isinstance(anchor, dict):
            return None
        if anchor.get("kind") == "action":
            return anchor.get("ref_id")
        if anchor.get("kind") != "node" or not isinstance(anchor.get("ref_id"), str):
            return None
        current = anchor["ref_id"]
    return None


def _row_by_id(
    rows: Any, key: str, ident: str
) -> tuple[int, dict[str, Any]] | None:
    matches = [
        (index, row) for index, row in enumerate(rows or [])
        if isinstance(row, dict) and row.get(key) == ident
    ]
    if len(matches) > 1:
        raise OverrideDeclarationError(
            "override_target_ambiguous", "关系合同对应多个稳定目标"
        )
    if not matches:
        return None
    index, row = matches[0]
    return index, copy.deepcopy(row)


def _relation_candidates(
    bundle: dict[str, Any], target: dict[str, Any], mode: str
) -> list[tuple[str, str, dict[str, Any], str]]:
    """Return typed objects capable of proving a binding-target relation."""

    domain = target["domain"]
    entity_id = target["entity_id"]
    exact: dict[str, tuple[str, str, str, str]] = {
        "manual_hit": ("hit", "/manual_template/hits/", "template", "hits"),
        "manual_buff": ("buff", "/manual_template/buffs/", "template", "buffs"),
        "rotation_event": (
            "rotation_event", "/rotation_model/events/", "rotation_model", "events",
        ),
        "mechanics_rule": (
            "mechanics", "/mechanics_model/rules/", "mechanics_model", "rules",
        ),
        "mechanics_hit_event": (
            "mechanics", "/mechanics_model/hit_events/",
            "mechanics_model", "hit_events",
        ),
        "mechanics_hit_pattern": (
            "mechanics", "/mechanics_model/hit_patterns/",
            "mechanics_model", "hit_patterns",
        ),
        "mechanics_application_event": (
            "mechanics", "/mechanics_model/application_events/",
            "mechanics_model", "application_events",
        ),
        "mechanics_condition_event": (
            "mechanics", "/mechanics_model/condition_events/",
            "mechanics_model", "condition_events",
        ),
    }
    candidates: list[tuple[str, str, dict[str, Any], str]] = []
    if domain == "computed_event" and target["scope_id"] == "result.hits":
        found = _row_by_id(
            (bundle.get("template") or {}).get("hits"), "id", entity_id
        )
        if found is not None:
            index, row = found
            candidates.append((
                "hit", f"/manual_template/hits/{index}", row,
                "computed_damage_event",
            ))
    if domain == "state_transition":
        state_specs = {
            "template.buffs": ("buff", "/manual_template/buffs/", "template", "buffs"),
            "manual_template.buffs": ("buff", "/manual_template/buffs/", "template", "buffs"),
            "rotation_model.events": (
                "rotation_event", "/rotation_model/events/", "rotation_model", "events",
            ),
            "mechanics_model.application_events": (
                "mechanics", "/mechanics_model/application_events/",
                "mechanics_model", "application_events",
            ),
            "mechanics_model.condition_events": (
                "mechanics", "/mechanics_model/condition_events/",
                "mechanics_model", "condition_events",
            ),
        }
        state_spec = state_specs.get(target["scope_id"])
        if state_spec is not None:
            kind, prefix, root, collection = state_spec
            found = _row_by_id((bundle.get(root) or {}).get(collection), "id", entity_id)
            if found is not None:
                index, row = found
                candidates.append((kind, f"{prefix}{index}", row, "exact"))
        elif target["scope_id"].startswith("mechanics_model.state_runtime."):
            runtime = (bundle.get("mechanics_model") or {}).get("state_runtime")
            collection = target["scope_id"].rsplit(".", 1)[-1]
            found = _row_by_id((runtime or {}).get(collection), "id", entity_id)
            if found is not None and isinstance(runtime, dict):
                candidates.append((
                    "mechanics", "/mechanics_model/state_runtime", runtime,
                    "state_runtime_contract_member",
                ))
    spec = exact.get(domain)
    if spec is not None:
        kind, pointer_prefix, root, collection = spec
        parent = bundle.get(root) or {}
        key = "hit_id" if domain == "mechanics_hit_event" else "id"
        found = _row_by_id(parent.get(collection), key, entity_id)
        if found is not None and (
            domain != "mechanics_hit_event"
            or target.get("action_id") is None
            or found[1].get("action_id") == target["action_id"]
        ):
            index, row = found
            candidates.append((kind, f"{pointer_prefix}{index}", row, "exact"))
    # A mechanics hit dispatch that does not yet exist can still be proven by
    # the topology node's exact action anchor plus its bound manual hit.  This
    # is the only currently supported add-event projection; other absent event
    # domains remain fail-closed until their binding contracts can prove them.
    if domain == "mechanics_hit_event" and mode == "add_event":
        found = _row_by_id(
            (bundle.get("template") or {}).get("hits"), "id", entity_id
        )
        if found is not None:
            index, hit = found
            candidates.append((
                "hit", f"/manual_template/hits/{index}", hit,
                "manual_hit_dispatch_projection",
            ))
    return candidates


def _validate_event_relation(
    plan: dict[str, Any], bundle: dict[str, Any], target: dict[str, Any], mode: str
) -> dict[str, Any]:
    from ..event_topology import event_topology_fingerprint, validate_event_topology
    from ..event_topology_binding import (
        NODE_TARGETS,
        validate_event_topology_bindings,
    )

    node_id = target.get("topology_node_id")
    binding_id = target.get("execution_binding_id")
    if not isinstance(node_id, str) or not isinstance(binding_id, str):
        raise OverrideDeclarationError(
            "override_event_relation_required",
            "事件覆盖必须同时声明拓扑节点与执行绑定身份",
        )
    if (
        target["domain"] == "mechanics_hit_event"
        and not isinstance(target.get("action_id"), str)
    ):
        raise OverrideDeclarationError(
            "override_event_action_required",
            "机制命中覆盖必须声明并绑定稳定动作身份",
        )
    extensions = plan.get("extensions") if isinstance(plan.get("extensions"), dict) else {}
    topology = extensions.get("event_topology")
    bindings = extensions.get("event_topology_bindings")
    if not isinstance(topology, dict):
        raise OverrideDeclarationError(
            "override_topology_unavailable",
            "事件覆盖声明了拓扑身份但当前拓扑不可用",
        )
    if not isinstance(bindings, dict) or not bindings.get("bindings"):
        raise OverrideDeclarationError(
            "override_execution_bindings_unavailable",
            "事件覆盖声明了执行绑定身份但当前绑定文档为空或不可用",
        )
    try:
        validate_event_topology(topology)
        validate_event_topology_bindings(bindings)
    except (TypeError, ValueError, KeyError) as exc:
        raise OverrideDeclarationError(
            "override_event_relation_invalid", "事件覆盖引用的拓扑或绑定合同无效"
        ) from exc
    if (
        bindings["topology_id"] != topology["topology_id"]
        or bindings["topology_fingerprint"] != event_topology_fingerprint(topology)
    ):
        raise OverrideDeclarationError(
            "override_event_relation_stale", "执行绑定不属于当前拓扑快照"
        )
    node_rows = [row for row in topology["nodes"] if row["node_id"] == node_id]
    binding_rows = [row for row in bindings["bindings"] if row["binding_id"] == binding_id]
    if len(node_rows) != 1 or len(binding_rows) != 1:
        raise OverrideDeclarationError(
            "override_event_relation_missing", "事件覆盖的拓扑节点或执行绑定不存在"
        )
    node = node_rows[0]
    binding = binding_rows[0]
    if node["status"] != "bound" or binding["node_id"] != node_id:
        raise OverrideDeclarationError(
            "override_event_relation_mismatch", "执行绑定没有关联指定的已绑定拓扑节点"
        )
    if binding["target_kind"] not in NODE_TARGETS[node["kind"]]:
        raise OverrideDeclarationError(
            "override_event_relation_mismatch", "执行绑定类型与拓扑节点类型不兼容"
        )
    if target.get("action_id") is not None and (
        _direct_action_anchor(
            {row["node_id"]: row for row in topology["nodes"]}, node_id
        )
        != target["action_id"]
    ):
        raise OverrideDeclarationError(
            "override_event_relation_mismatch", "拓扑节点没有锚定覆盖声明的动作"
        )
    candidates = _relation_candidates(bundle, target, mode)
    selected = next((
        (value, projection)
        for kind, pointer, value, projection in candidates
        if binding["target_kind"] == kind
        and binding["pointer"] == pointer
        and binding["target_fingerprint"] == fingerprint(value)
    ), None)
    if selected is None:
        raise OverrideDeclarationError(
            "override_event_target_unproven",
            "执行绑定不能证明当前覆盖目标域、实体与自动值的关系",
        )
    _, projection = selected
    return {
        "topology_node_id": node_id,
        "execution_binding_id": binding_id,
        "binding_target_kind": binding["target_kind"],
        "binding_pointer": binding["pointer"],
        "binding_target_fingerprint": binding["target_fingerprint"],
        "projection": projection,
    }


def _validate_graph_identity(
    plan: dict[str, Any], bundle: dict[str, Any], item: dict[str, Any]
) -> dict[str, Any] | None:
    target = item["target"]
    if item["mode"] in {
        "add_event", "replace_event", "suppress_event",
        "replace_state_transition", "replace_computed_value",
    }:
        return _validate_event_relation(plan, bundle, target, item["mode"])
    extensions = plan.get("extensions") if isinstance(plan.get("extensions"), dict) else {}
    topology = extensions.get("event_topology")
    if target.get("topology_node_id") is not None:
        if not isinstance(topology, dict) or not isinstance(topology.get("nodes"), list):
            raise OverrideDeclarationError(
                "override_topology_unavailable", "覆盖声明了拓扑身份但当前拓扑不可用"
            )
        node_ids = {
            row.get("node_id") for row in topology["nodes"] if isinstance(row, dict)
        }
        if target["topology_node_id"] not in node_ids:
            raise OverrideDeclarationError(
                "override_topology_target_missing", "覆盖引用的拓扑节点不存在"
            )
    bindings = extensions.get("event_topology_bindings")
    if target.get("execution_binding_id") is not None:
        if not isinstance(bindings, dict) or not isinstance(bindings.get("bindings"), list):
            raise OverrideDeclarationError(
                "override_execution_bindings_unavailable",
                "覆盖声明了执行绑定身份但当前绑定文档不可用",
            )
        candidates = bindings["bindings"]
        binding_ids = {row.get("binding_id") for row in candidates if isinstance(row, dict)}
        if target["execution_binding_id"] not in binding_ids:
            raise OverrideDeclarationError(
                "override_execution_binding_missing", "覆盖引用的执行绑定不存在"
            )
    return None


def base_ownership_claims(
    plan: dict[str, Any],
    *,
    additional_ownership_claims: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return non-override owners for the current effective graph.

    This inventory must be built even when no override declaration exists.
    External supplements and Agent materialization are independent execution
    lanes, but they still may not own the same coverage range.
    """

    # External supplements are a separate additive execution lane, but their
    # coverage identities still participate in the same one-owner registry.
    # An override cannot silently reuse one of those ranges.
    claims: list[dict[str, Any]] = [
        {
            "owner_id": f"external:{item['id']}",
            "owner_kind": "external",
            "coverage_key": item["coverage_key"],
            "target_identity": (
                f"external_damage|event_id={item['event_id']}|id={item['id']}"
            ),
            "source_fingerprint": fingerprint(item),
        }
        for item in plan.get("external_damage_supplements", [])
        if isinstance(item, dict)
        and all(isinstance(item.get(key), str) and item[key]
                for key in ("id", "event_id", "coverage_key"))
    ]
    # Upstream automatic compilers and Agent materialization may install
    # additional exact owners before this downstream override transaction.
    # Callers may pass only claims derived from the freshly verified effective
    # graph; authored plan JSON is never treated as ownership authority here.
    claims.extend(copy.deepcopy(additional_ownership_claims or []))
    return claims


def target_rows(
    plan: dict[str, Any], bundle: dict[str, Any], declarations: dict[str, Any],
    *, additional_ownership_claims: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return review targets and all ownership claims for this declaration."""

    rows: list[dict[str, Any]] = []
    claims = base_ownership_claims(
        plan, additional_ownership_claims=additional_ownership_claims,
    )
    relation_proofs: list[dict[str, Any]] = []
    identities: set[str] = set()
    for item in declarations["items"]:
        target = item["target"]
        relation = _validate_graph_identity(plan, bundle, item)
        if relation is not None:
            relation_proofs.append({"override_id": item["override_id"], **relation})
        identity = stable_target_identity(target)
        if identity in identities:
            raise OverrideDeclarationError(
                "override_target_duplicate", "同一稳定目标不能在一个覆盖集中重复接管"
            )
        identities.add(identity)
        _, _, index, value = locate_target(
            bundle, target, allow_absent=item["mode"] == "add_event"
        )
        if item["mode"] == "add_event" and index is not None:
            raise OverrideDeclarationError(
                "override_add_target_exists", "新增事件的稳定身份已被自动基线占用"
            )
        rows.append({"target": copy.deepcopy(target), "value": value})
        if index is not None:
            owner_id, owner_kind = baseline_owner(target)
            claims.append({
                "owner_id": owner_id,
                "owner_kind": owner_kind,
                "coverage_key": item["coverage_key"],
                "target_identity": identity,
                "source_fingerprint": fingerprint(value),
            })
    return rows, claims, relation_proofs


def baseline_owner(target: dict[str, Any]) -> tuple[str, str]:
    """Return the source owner for an already materialized baseline member."""

    domain = target.get("domain")
    scope = target.get("scope_id")
    manual = (
        domain in {"manual_hit", "manual_buff"}
        or domain == "computed_event" and scope == "result.hits"
        or domain == "state_transition"
        and scope in {"template.buffs", "manual_template.buffs"}
        or domain == "parameter_input"
        and scope in {
            "template.hits", "manual_template.hits",
            "template.buffs", "manual_template.buffs",
        }
    )
    kind = "manual" if manual else "automatic"
    return (
        f"{kind}:" + fingerprint(stable_target_identity(target))[:24],
        kind,
    )


def automatic_owner_id(target: dict[str, Any]) -> str:
    """Compatibility name for the current baseline owner identity."""

    return baseline_owner(target)[0]
