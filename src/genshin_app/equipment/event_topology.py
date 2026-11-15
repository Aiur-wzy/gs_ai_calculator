"""Agent-declared, non-executable event topology.

``event-topology-v1`` is the review boundary between an editable action axis
and executable mechanics.  It records what the agent believes an action or a
persistent source causes, without granting execution authority and without
carrying calculation results.

The engine deliberately does *not* infer or verify character-specific game
facts here.  Validation is limited to a closed/bounded JSON contract, stable
identity, references, scheduling shape, binding structure, and declared
parameter-slot completeness.  A later compiler may consume a separately
reviewed projection; this module never creates one.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections import Counter, defaultdict

CONTRACT_ID = "event-topology-v1"
RECEIPT_CONTRACT_ID = "event-topology-coverage-receipt-v1"
SCHEMA_VERSION = 1
AUTHORITY = "agent_declared_non_executable"
RECEIPT_AUTHORITY = "diagnostic_only"

MAX_BYTES = 2 * 1024 * 1024
MAX_ACTIONS = 2048
MAX_DEPENDENCIES = 256
MAX_SOURCES = 1024
MAX_SOURCE_REFS = 64
MAX_RECEIPT_ISSUES = 512
MAX_NODES = 4096
MAX_EDGES = 8192
MAX_PARAMETER_SLOTS = 128
MAX_OCCURRENCES = 2048
MAX_TEXT = 4096
MAX_SECONDS = 3600
MAX_REPORTED_CYCLES = 3

NODE_KINDS = frozenset((
    "action_anchor", "contact", "damage", "application", "buff_interval",
    "resource", "state", "reaction_candidate", "finite_sequence",
    "periodic_channel", "other",
))
NODE_STATUSES = frozenset(("identified", "bound", "pending", "excluded"))
EDGE_KINDS = frozenset((
    "causes", "triggers", "refreshes", "replaces", "samples", "consumes",
    "applies_to", "contains", "binds",
))
EDGE_STATUSES = frozenset(("bound", "pending", "excluded"))
SOURCE_CATEGORIES = frozenset((
    "character", "talent", "constellation", "weapon", "artifact", "enemy",
    "scenario", "database", "other",
))
SOURCE_DISPOSITIONS = frozenset(("modeled", "not_applicable", "pending"))
SOURCE_REF_KINDS = frozenset((
    "database", "database_appendix", "agent_research", "user_input",
    "derived", "other",
))
SOURCE_REF_STATUSES = frozenset(("verified", "declared", "pending"))
DEPENDENCY_KINDS = frozenset((
    "database_snapshot", "equipment", "scenario", "source_inventory", "other",
))
PARAMETER_VALUE_TYPES = frozenset(("number", "integer", "string", "boolean", "reference"))
PARAMETER_REQUIRED_FOR = frozenset(("topology", "calculation"))
PARAMETER_STATUSES = frozenset(("bound", "pending", "not_applicable"))
REFRESH_POLICIES = frozenset(("none", "replace", "extend", "restart", "ignore", "pending"))
OFF_FIELD_POLICIES = frozenset(("continue", "stop", "pending"))

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_KEYS = frozenset((
    "result", "results", "result_receipt", "dps", "total_damage",
    "calculated_damage", "damage_value", "resolved_template", "mechanics_model",
    "runtime_events", "execution_authority", "can_execute", "can_accept",
))
_FORBIDDEN_PARAMETER_SEMANTICS = frozenset((
    "result", "results", "dps", "total_damage", "calculated_damage", "damage_value",
))
_SOURCE_REF_PATH = re.compile(r"topology\.sources\[\d+\]\.source_refs\[\d+\]\Z")
_GRAPH_EDGE_KINDS = frozenset(("causes", "triggers", "contains", "binds", "consumes"))


class EventTopologyError(ValueError):
    """The non-executable topology violates its closed contract."""

    def __init__(self, path: str, message: str, code: str = "invalid_event_topology"):
        super().__init__(f"{path}: {message}")
        self.path = path
        self.message = message
        self.code = code


def _fail(path: str, message: str, code: str = "invalid_event_topology") -> None:
    raise EventTopologyError(path, message, code)


def _bounded_json(value) -> bytes:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise EventTopologyError("topology", "must be finite JSON") from exc
    if len(encoded) > MAX_BYTES:
        _fail("topology", "exceeds the 2 MiB event-topology limit", "event_topology_too_large")
    return encoded


def _fingerprint(value) -> str:
    return hashlib.sha256(_bounded_json(value)).hexdigest()


def _object(value, path: str, required: set[str], optional: set[str] = frozenset()) -> dict:
    if not isinstance(value, dict):
        _fail(path, "must be an object")
    missing = required - value.keys()
    extra = value.keys() - required - optional
    if missing:
        _fail(path, "missing fields: " + ", ".join(sorted(missing)))
    if extra:
        _fail(path, "unknown fields: " + ", ".join(sorted(extra)))
    return value


def _list(value, path: str, maximum: int, *, minimum: int = 0) -> list:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        _fail(path, f"must be a list with {minimum}..{maximum} items")
    return value


def _text(value, path: str, *, nullable: bool = False, empty: bool = False):
    if value is None and nullable:
        return None
    if not isinstance(value, str) or len(value) > MAX_TEXT or (not empty and not value.strip()):
        _fail(path, "must be bounded text" + (" or null" if nullable else ""))
    return value


def _id(value, path: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        _fail(path, "must be a bounded stable identifier")
    return value


def _hash(value, path: str, *, nullable: bool = False):
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        _fail(path, "must be a lowercase sha256 digest")
    return value


def _integer(value, path: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        _fail(path, f"must be an integer in [{low}, {high}]")
    return value


def _time(value, path: str, *, nullable: bool = False, positive: bool = False):
    if value is None and nullable:
        return None
    if type(value) not in (int, float) or not math.isfinite(value):
        _fail(path, "must be a finite number" + (" or null" if nullable else ""))
    if value < 0 or (positive and value == 0) or value > MAX_SECONDS:
        _fail(path, f"must be {'positive' if positive else 'non-negative'} and at most {MAX_SECONDS}")
    if round(value * 1_000_000) != value * 1_000_000:
        _fail(path, "must be exactly representable in microseconds")
    return value


def _unique(values: list[str], path: str) -> None:
    duplicate = next((value for value, count in Counter(values).items() if count > 1), None)
    if duplicate is not None:
        _fail(path, f"contains duplicate stable identifier {duplicate}")


def _reject_result_payloads(value, path: str = "topology") -> None:
    if isinstance(value, dict):
        forbidden = value.keys() & _FORBIDDEN_KEYS
        if value.get("execution_authority") == "none" and _SOURCE_REF_PATH.fullmatch(path):
            forbidden.discard("execution_authority")
        if forbidden:
            _fail(path, "non-executable topology cannot carry: " + ", ".join(sorted(forbidden)),
                  "event_topology_execution_payload")
        for key, item in value.items():
            _reject_result_payloads(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_result_payloads(item, f"{path}[{index}]")


def _validate_anchor(value, path: str) -> None:
    row = _object(value, path, {"kind", "ref_id", "phase"})
    if row["kind"] not in ("action", "node"):
        _fail(path + ".kind", "must be action or node")
    _id(row["ref_id"], path + ".ref_id")
    allowed = ("start", "end") if row["kind"] == "action" else ("point", "start", "end")
    if row["phase"] not in allowed:
        _fail(path + ".phase", "unsupported anchor phase")


def _validate_schedule(value, path: str) -> None:
    if not isinstance(value, dict):
        _fail(path, "must be an object")
    kind = value.get("kind")
    if kind == "point":
        row = _object(value, path, {"kind", "anchor", "offset_seconds", "order"})
        _validate_anchor(row["anchor"], path + ".anchor")
        _time(row["offset_seconds"], path + ".offset_seconds", nullable=True)
        _integer(row["order"], path + ".order", -10**9, 10**9)
    elif kind == "periodic":
        row = _object(value, path, {"kind", "anchor", "first_offset_seconds",
            "interval_seconds", "count", "duration_seconds", "refresh_policy",
            "off_field_policy", "order"})
        _validate_anchor(row["anchor"], path + ".anchor")
        _time(row["first_offset_seconds"], path + ".first_offset_seconds", nullable=True)
        _time(row["interval_seconds"], path + ".interval_seconds", nullable=True, positive=True)
        if row["count"] is not None:
            _integer(row["count"], path + ".count", 1, MAX_OCCURRENCES)
        _time(row["duration_seconds"], path + ".duration_seconds", nullable=True, positive=True)
        if row["refresh_policy"] not in REFRESH_POLICIES:
            _fail(path + ".refresh_policy", "unsupported refresh policy")
        if row["off_field_policy"] not in OFF_FIELD_POLICIES:
            _fail(path + ".off_field_policy", "unsupported off-field policy")
        _integer(row["order"], path + ".order", -10**9, 10**9)
    elif kind == "explicit_offsets":
        row = _object(value, path, {"kind", "anchor", "occurrences"})
        _validate_anchor(row["anchor"], path + ".anchor")
        occurrences = _list(row["occurrences"], path + ".occurrences", MAX_OCCURRENCES, minimum=1)
        ids, positions = [], []
        for index, occurrence in enumerate(occurrences):
            opath = f"{path}.occurrences[{index}]"
            item = _object(occurrence, opath, {"occurrence_id", "offset_seconds", "order"})
            ids.append(_id(item["occurrence_id"], opath + ".occurrence_id"))
            _time(item["offset_seconds"], opath + ".offset_seconds", nullable=True)
            _integer(item["order"], opath + ".order", -10**9, 10**9)
            if item["offset_seconds"] is not None:
                positions.append((round(item["offset_seconds"] * 1_000_000), item["order"]))
        _unique(ids, path + ".occurrences")
        if len(positions) != len(set(positions)):
            _fail(path + ".occurrences", "known occurrences need distinct microsecond/order positions")
    elif kind == "pending":
        row = _object(value, path, {"kind", "anchor", "reason"})
        if row["anchor"] is not None:
            _validate_anchor(row["anchor"], path + ".anchor")
        _text(row["reason"], path + ".reason")
    else:
        _fail(path + ".kind", "unsupported schedule kind")


def _validate_source_ref(value, path: str) -> None:
    row = _object(value, path, {"kind", "reference", "fingerprint", "status"},
                  {"integrity_status", "knowledge_review_status", "execution_authority"})
    if row["kind"] not in SOURCE_REF_KINDS:
        _fail(path + ".kind", "unsupported source-reference kind")
    _text(row["reference"], path + ".reference")
    _hash(row["fingerprint"], path + ".fingerprint", nullable=True)
    if row["status"] not in SOURCE_REF_STATUSES:
        _fail(path + ".status", "unsupported source-reference status")
    if row["status"] == "verified" and row["fingerprint"] is None:
        _fail(path + ".fingerprint", "verified source reference requires a fingerprint")
    if row["status"] == "pending" and row["fingerprint"] is not None:
        _fail(path + ".fingerprint", "pending source reference cannot claim a fingerprint")
    if "integrity_status" in row and (row["integrity_status"] != "verified" or row["status"] != "verified"):
        _fail(path + ".integrity_status", "display integrity must match a verified reference")
    if "knowledge_review_status" in row and (
            row["kind"] != "database_appendix"
            or row["knowledge_review_status"] not in ("approved", "pending", "mixed_or_unspecified")):
        _fail(path + ".knowledge_review_status", "unsupported appendix review status")


def _validate_parameter_slot(value, path: str) -> None:
    row = _object(value, path, {"slot_id", "semantic", "value_type", "required_for",
        "status", "value", "unit", "source_ids", "note"})
    _id(row["slot_id"], path + ".slot_id")
    semantic = _id(row["semantic"], path + ".semantic")
    semantic_parts = set(semantic.lower().replace(":", ".").split("."))
    if semantic.lower() in _FORBIDDEN_PARAMETER_SEMANTICS or semantic_parts & {"result", "results", "dps"}:
        _fail(path + ".semantic", "parameter slots describe inputs, not calculated results",
              "event_topology_execution_payload")
    if row["value_type"] not in PARAMETER_VALUE_TYPES:
        _fail(path + ".value_type", "unsupported value type")
    if row["required_for"] not in PARAMETER_REQUIRED_FOR:
        _fail(path + ".required_for", "must be topology or calculation")
    if row["status"] not in PARAMETER_STATUSES:
        _fail(path + ".status", "unsupported parameter status")
    _text(row["unit"], path + ".unit", nullable=True)
    _text(row["note"], path + ".note", empty=True)
    source_ids = _list(row["source_ids"], path + ".source_ids", MAX_SOURCES)
    for index, source_id in enumerate(source_ids):
        _id(source_id, f"{path}.source_ids[{index}]")
    _unique(source_ids, path + ".source_ids")
    if row["status"] == "bound":
        if not source_ids:
            _fail(path + ".source_ids", "bound parameter requires declared provenance")
        expected = row["value_type"]
        actual = row["value"]
        if expected == "number" and (type(actual) not in (int, float) or not math.isfinite(actual)):
            _fail(path + ".value", "must be a finite number")
        elif expected == "integer" and type(actual) is not int:
            _fail(path + ".value", "must be an integer")
        elif expected in ("string", "reference"):
            _text(actual, path + ".value")
            if expected == "reference":
                _id(actual, path + ".value")
        elif expected == "boolean" and type(actual) is not bool:
            _fail(path + ".value", "must be a boolean")
    elif row["value"] is not None:
        _fail(path + ".value", "pending/not-applicable parameter cannot carry a value")
    if row["status"] == "not_applicable" and not row["note"].strip():
        _fail(path + ".note", "not-applicable parameter requires a reason")


def _anchor_reference(schedule: dict):
    if schedule["kind"] == "pending":
        return schedule["anchor"]
    return schedule["anchor"]


def validate_event_topology(topology: dict) -> dict:
    """Validate the closed non-executable contract and all direct references.

    Successful validation does not assert game truth, topology completeness, or
    calculation readiness.  Call :func:`lint_event_topology` for those declared
    coverage states.
    """
    _bounded_json(topology)
    _reject_result_payloads(topology)
    root = _object(topology, "topology", {"contract_id", "schema_version", "topology_id",
        "authority", "declaration", "action_checkpoint", "dependencies", "sources",
        "nodes", "edges"})
    if root["contract_id"] != CONTRACT_ID or root["schema_version"] != SCHEMA_VERSION:
        _fail("topology", "unsupported event-topology contract/version")
    if root["authority"] != AUTHORITY:
        _fail("topology.authority", "must remain agent_declared_non_executable",
              "event_topology_execution_authority")
    _id(root["topology_id"], "topology.topology_id")

    declaration = _object(root["declaration"], "topology.declaration",
        {"character_semantics", "completeness", "note"})
    if declaration["character_semantics"] != "agent_declared":
        _fail("topology.declaration.character_semantics",
              "engine cannot claim character-specific verification")
    if declaration["completeness"] not in ("draft", "declared_complete"):
        _fail("topology.declaration.completeness", "must be draft or declared_complete")
    _text(declaration["note"], "topology.declaration.note", empty=True)

    checkpoint = _object(root["action_checkpoint"], "topology.action_checkpoint",
        {"revision", "fingerprint", "action_ids"})
    _integer(checkpoint["revision"], "topology.action_checkpoint.revision", 0, 2**53 - 1)
    _hash(checkpoint["fingerprint"], "topology.action_checkpoint.fingerprint")
    action_ids = _list(checkpoint["action_ids"], "topology.action_checkpoint.action_ids",
        MAX_ACTIONS, minimum=1)
    for index, action_id in enumerate(action_ids):
        _id(action_id, f"topology.action_checkpoint.action_ids[{index}]")
    _unique(action_ids, "topology.action_checkpoint.action_ids")

    dependencies = _list(root["dependencies"], "topology.dependencies", MAX_DEPENDENCIES)
    dependency_ids = []
    for index, dependency in enumerate(dependencies):
        path = f"topology.dependencies[{index}]"
        row = _object(dependency, path, {"dependency_id", "kind", "fingerprint", "required"})
        dependency_ids.append(_id(row["dependency_id"], path + ".dependency_id"))
        if row["kind"] not in DEPENDENCY_KINDS:
            _fail(path + ".kind", "unsupported dependency kind")
        _hash(row["fingerprint"], path + ".fingerprint")
        if type(row["required"]) is not bool:
            _fail(path + ".required", "must be boolean")
    _unique(dependency_ids, "topology.dependencies")

    sources = _list(root["sources"], "topology.sources", MAX_SOURCES)
    source_ids = []
    for index, source in enumerate(sources):
        path = f"topology.sources[{index}]"
        row = _object(source, path, {"source_id", "category", "owner_ref", "description",
            "disposition", "node_ids", "reason", "source_refs"})
        source_ids.append(_id(row["source_id"], path + ".source_id"))
        if row["category"] not in SOURCE_CATEGORIES:
            _fail(path + ".category", "unsupported source category")
        _text(row["owner_ref"], path + ".owner_ref", nullable=True)
        _text(row["description"], path + ".description")
        if row["disposition"] not in SOURCE_DISPOSITIONS:
            _fail(path + ".disposition", "unsupported source disposition")
        node_ids = _list(row["node_ids"], path + ".node_ids", MAX_NODES)
        for item_index, node_id in enumerate(node_ids):
            _id(node_id, f"{path}.node_ids[{item_index}]")
        _unique(node_ids, path + ".node_ids")
        _text(row["reason"], path + ".reason", nullable=True)
        refs = _list(row["source_refs"], path + ".source_refs", MAX_SOURCE_REFS)
        for ref_index, ref in enumerate(refs):
            _validate_source_ref(ref, f"{path}.source_refs[{ref_index}]")
        if row["disposition"] == "modeled" and not node_ids:
            _fail(path + ".node_ids", "modeled source must identify at least one node")
        if row["disposition"] == "not_applicable" and (node_ids or not row["reason"]):
            _fail(path, "not-applicable source needs a reason and no modeled nodes")
        if row["disposition"] == "pending" and not row["reason"]:
            _fail(path + ".reason", "pending source needs an explicit follow-up reason")
    _unique(source_ids, "topology.sources")
    known_sources = set(source_ids)

    nodes = _list(root["nodes"], "topology.nodes", MAX_NODES)
    node_ids = []
    for index, node in enumerate(nodes):
        path = f"topology.nodes[{index}]"
        row = _object(node, path, {"node_id", "kind", "status", "schedule", "source_ids",
            "parameter_slots", "note"})
        node_ids.append(_id(row["node_id"], path + ".node_id"))
        if row["kind"] not in NODE_KINDS:
            _fail(path + ".kind", "unsupported node kind")
        if row["status"] not in NODE_STATUSES:
            _fail(path + ".status", "unsupported node status")
        _validate_schedule(row["schedule"], path + ".schedule")
        declared_sources = _list(row["source_ids"], path + ".source_ids", MAX_SOURCES)
        for item_index, source_id in enumerate(declared_sources):
            _id(source_id, f"{path}.source_ids[{item_index}]")
            if source_id not in known_sources:
                _fail(f"{path}.source_ids[{item_index}]", "references an unknown source")
        _unique(declared_sources, path + ".source_ids")
        slots = _list(row["parameter_slots"], path + ".parameter_slots", MAX_PARAMETER_SLOTS)
        slot_ids = []
        for slot_index, slot in enumerate(slots):
            slot_path = f"{path}.parameter_slots[{slot_index}]"
            _validate_parameter_slot(slot, slot_path)
            slot_ids.append(slot["slot_id"])
            for source_id in slot["source_ids"]:
                if source_id not in known_sources:
                    _fail(slot_path + ".source_ids", "references an unknown source")
        _unique(slot_ids, path + ".parameter_slots")
        _text(row["note"], path + ".note", empty=True)
        if row["status"] == "excluded" and not row["note"].strip():
            _fail(path + ".note", "excluded node requires a reason")
        if row["kind"] == "action_anchor":
            anchor = _anchor_reference(row["schedule"])
            if anchor is None or anchor["kind"] != "action":
                _fail(path + ".schedule", "action-anchor node must reference an action")
    _unique(node_ids, "topology.nodes")
    known_nodes = set(node_ids)
    known_actions = set(action_ids)

    # Cross-reference schedules only after all node identities are known.
    for index, node in enumerate(nodes):
        anchor = _anchor_reference(node["schedule"])
        if anchor is None:
            continue
        if anchor["kind"] == "action" and anchor["ref_id"] not in known_actions:
            _fail(f"topology.nodes[{index}].schedule.anchor.ref_id", "references an unknown action")
        if anchor["kind"] == "node" and anchor["ref_id"] not in known_nodes:
            _fail(f"topology.nodes[{index}].schedule.anchor.ref_id", "references an unknown node")
        if anchor["kind"] == "node" and anchor["ref_id"] == node["node_id"]:
            _fail(f"topology.nodes[{index}].schedule.anchor.ref_id", "cannot anchor a node to itself")

    # Source coverage is closed in both directions: an inventory row cannot
    # claim an absent node, and every claimed node must name the source back.
    source_by_id = {row["source_id"]: row for row in sources}
    node_source_ids = {row["node_id"]: set(row["source_ids"]) for row in nodes}
    source_node_ids = {row["source_id"]: set(row["node_ids"]) for row in sources}
    for index, source in enumerate(sources):
        for node_id in source["node_ids"]:
            if node_id not in known_nodes:
                _fail(f"topology.sources[{index}].node_ids", "references an unknown node")
            if source["source_id"] not in node_source_ids[node_id]:
                _fail(f"topology.sources[{index}].node_ids",
                      "source/node ownership must be bidirectional")
    for index, node in enumerate(nodes):
        for source_id in node["source_ids"]:
            # ``source_id`` was checked against ``known_sources`` above.  Use
            # the pre-built maps here: repeated linear scans over up to 1024
            # sources and list membership over up to 4096 node ids otherwise
            # turn this closed-reference check quadratic on legal documents.
            source = source_by_id[source_id]
            if node["node_id"] not in source_node_ids[source["source_id"]]:
                _fail(f"topology.nodes[{index}].source_ids",
                      "node/source ownership must be bidirectional")

    edges = _list(root["edges"], "topology.edges", MAX_EDGES)
    edge_ids = []
    for index, edge in enumerate(edges):
        path = f"topology.edges[{index}]"
        row = _object(edge, path, {"edge_id", "kind", "from_node_id", "to_node_id",
            "status", "source_ids", "note"})
        edge_ids.append(_id(row["edge_id"], path + ".edge_id"))
        if row["kind"] not in EDGE_KINDS:
            _fail(path + ".kind", "unsupported edge kind")
        _id(row["from_node_id"], path + ".from_node_id")
        _id(row["to_node_id"], path + ".to_node_id")
        if row["from_node_id"] not in known_nodes or row["to_node_id"] not in known_nodes:
            _fail(path, "edge endpoint references an unknown node")
        if row["from_node_id"] == row["to_node_id"]:
            _fail(path, "self edges are not valid event bindings")
        if row["status"] not in EDGE_STATUSES:
            _fail(path + ".status", "unsupported edge status")
        declared_sources = _list(row["source_ids"], path + ".source_ids", MAX_SOURCES)
        for item_index, source_id in enumerate(declared_sources):
            _id(source_id, f"{path}.source_ids[{item_index}]")
            if source_id not in known_sources:
                _fail(f"{path}.source_ids[{item_index}]", "references an unknown source")
        _unique(declared_sources, path + ".source_ids")
        _text(row["note"], path + ".note", empty=True)
        if row["status"] == "excluded" and not row["note"].strip():
            _fail(path + ".note", "excluded edge requires a reason")
    _unique(edge_ids, "topology.edges")
    return topology


def _issue(code: str, stage: str, severity: str, path: str, message: str, **identity) -> dict:
    return {"code": code, "stage": stage, "severity": severity, "path": path,
            "message": message, **identity}


def _schedule_pending_fields(schedule: dict) -> list[str]:
    kind = schedule["kind"]
    if kind == "pending":
        return ["schedule"]
    if kind == "point":
        return ["offset_seconds"] if schedule["offset_seconds"] is None else []
    if kind == "explicit_offsets":
        return [f"occurrences[{index}].offset_seconds" for index, row in enumerate(
            schedule["occurrences"]) if row["offset_seconds"] is None]
    fields = [key for key in ("first_offset_seconds", "interval_seconds")
              if schedule[key] is None]
    if schedule["count"] is None and schedule["duration_seconds"] is None:
        fields.append("count_or_duration_seconds")
    if schedule["refresh_policy"] == "pending":
        fields.append("refresh_policy")
    if schedule["off_field_policy"] == "pending":
        fields.append("off_field_policy")
    return fields


def _graph_cycles(nodes: list[dict], edges: list[dict]) -> list[list[str]]:
    active = {row["node_id"] for row in nodes if row["status"] != "excluded"}
    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        if (edge["status"] == "bound" and edge["kind"] in _GRAPH_EDGE_KINDS
                and edge["from_node_id"] in active and edge["to_node_id"] in active):
            adjacency[edge["from_node_id"]].add(edge["to_node_id"])
    for node in nodes:
        if node["status"] == "excluded":
            continue
        anchor = _anchor_reference(node["schedule"])
        if anchor is not None and anchor["kind"] == "node" and anchor["ref_id"] in active:
            adjacency[anchor["ref_id"]].add(node["node_id"])

    # Iterative depth-first traversal avoids Python's recursion ceiling on a
    # legal 4096-node chain.  ``path_index`` also makes a back-edge slice O(1)
    # to locate instead of repeatedly scanning the active path.
    ordered_adjacency = {node_id: tuple(sorted(targets))
                         for node_id, targets in adjacency.items()}
    color: dict[str, int] = {}
    path: list[str] = []
    path_index: dict[str, int] = {}
    cycles: list[list[str]] = []
    unique_cycles: set[tuple[str, ...]] = set()

    for root in sorted(active):
        if color.get(root, 0) != 0:
            continue
        color[root] = 1
        path_index[root] = 0
        path.append(root)
        frames: list[tuple[str, object]] = [
            (root, iter(ordered_adjacency.get(root, ())))]
        while frames:
            node_id, targets = frames[-1]
            try:
                target = next(targets)
            except StopIteration:
                frames.pop()
                path.pop()
                path_index.pop(node_id, None)
                color[node_id] = 2
                continue

            state = color.get(target, 0)
            if state == 0:
                color[target] = 1
                path_index[target] = len(path)
                path.append(target)
                frames.append((target, iter(ordered_adjacency.get(target, ()))))
            elif state == 1:
                start = path_index[target]
                cycle = path[start:] + [target]
                key = tuple(sorted(set(cycle)))
                if key not in unique_cycles:
                    unique_cycles.add(key)
                    cycles.append(cycle)
                    # One cycle blocks the topology; a small bound keeps the
                    # stage-safe receipt below its own 2 MiB JSON ceiling even
                    # for adversarial graphs with long identifiers.
                    if len(cycles) >= MAX_REPORTED_CYCLES:
                        return cycles
    return cycles


def lint_event_topology(topology: dict) -> dict:
    """Return a stage-safe coverage receipt; never return source text or values."""
    try:
        validate_event_topology(topology)
    except EventTopologyError as exc:
        topology_id = topology.get("topology_id") if isinstance(topology, dict) else None
        try:
            topology_fp = _fingerprint(topology)
        except EventTopologyError:
            topology_fp = None
        receipt = {
            "contract_id": RECEIPT_CONTRACT_ID,
            "schema_version": SCHEMA_VERSION,
            "topology_id": topology_id if isinstance(topology_id, str) else None,
            "topology_fingerprint": topology_fp,
            "action_checkpoint_fingerprint": None,
            "dependencies_fingerprint": None,
            "authority": RECEIPT_AUTHORITY,
            "character_semantics": "agent_declared_unverified",
            "status": "invalid",
            "stages": {
                "topology": {"status": "blocked", "blocking_issue_count": 1},
                "numeric_binding": {"status": "blocked", "blocking_issue_count": 1},
            },
            "counts": {},
            "active_node_kinds": [],
            "active_node_kind_counts": {},
            "coverage": {},
            "issues": [_issue(exc.code, "topology", "error", exc.path, exc.message)],
            "issues_total": 1,
            "issues_truncated": False,
            "limitations": [
                "角色特有事实由Agent声明，本回执不核实游戏真实性。",
                "回执仅用于诊断，不授予执行、计算结果或接纳权限。",
            ],
        }
        receipt["receipt_fingerprint"] = _fingerprint(receipt)
        return receipt

    declared_complete = topology["declaration"]["completeness"] == "declared_complete"
    issues = []
    nodes, edges, sources = topology["nodes"], topology["edges"], topology["sources"]
    active_nodes = [row for row in nodes if row["status"] != "excluded"]
    active_ids = {row["node_id"] for row in active_nodes}
    active_kind_counts = Counter(row["kind"] for row in active_nodes)

    anchors: dict[str, list[str]] = defaultdict(list)
    for index, node in enumerate(nodes):
        if node["status"] == "excluded":
            continue
        path = f"topology.nodes[{index}]"
        if node["kind"] == "action_anchor":
            anchor = _anchor_reference(node["schedule"])
            anchors[anchor["ref_id"]].append(node["node_id"])
        if not node["source_ids"]:
            issues.append(_issue("event_node_missing_source", "topology", "error", path + ".source_ids",
                "活跃事件节点必须绑定来源盘点项。", node_id=node["node_id"]))
        if node["status"] in ("pending", "identified"):
            severity = "error" if declared_complete else "warning"
            issues.append(_issue("event_node_not_bound", "topology", severity, path + ".status",
                "事件已识别但尚未完成拓扑绑定。", node_id=node["node_id"]))
        for field in _schedule_pending_fields(node["schedule"]):
            severity = "error" if declared_complete else "warning"
            issues.append(_issue("event_schedule_pending", "topology", severity,
                path + ".schedule." + field, "事件调度仍有待确定字段。", node_id=node["node_id"]))
        for slot_index, slot in enumerate(node["parameter_slots"]):
            if slot["status"] != "pending":
                continue
            stage = slot["required_for"]
            severity = "error" if (stage == "topology" and declared_complete) else (
                "error" if stage == "calculation" else "warning")
            issues.append(_issue("event_parameter_pending", stage, severity,
                f"{path}.parameter_slots[{slot_index}]",
                "声明为必需的参数槽尚未绑定。", node_id=node["node_id"], slot_id=slot["slot_id"]))

    expected_actions = topology["action_checkpoint"]["action_ids"]
    missing_actions = [action_id for action_id in expected_actions if not anchors.get(action_id)]
    duplicate_actions = [action_id for action_id in expected_actions if len(anchors.get(action_id, ())) > 1]
    for action_id in missing_actions:
        issues.append(_issue("action_anchor_missing", "topology",
            "error" if declared_complete else "warning", "topology.nodes",
            "动作检查点尚无事件拓扑锚点。", action_id=action_id))
    for action_id in duplicate_actions:
        issues.append(_issue("action_anchor_duplicate", "topology", "error", "topology.nodes",
            "同一动作只能有一个活跃事件拓扑锚点。", action_id=action_id))

    edge_shapes = Counter((row["kind"], row["from_node_id"], row["to_node_id"])
                          for row in edges if row["status"] != "excluded")
    for index, edge in enumerate(edges):
        if edge["status"] == "excluded":
            continue
        path = f"topology.edges[{index}]"
        if edge["status"] == "pending":
            issues.append(_issue("event_edge_pending", "topology",
                "error" if declared_complete else "warning", path,
                "事件关系已识别但尚未完成绑定。", edge_id=edge["edge_id"]))
        if not edge["source_ids"]:
            issues.append(_issue("event_edge_missing_source", "topology", "error",
                path + ".source_ids", "活跃事件关系必须绑定来源盘点项。",
                edge_id=edge["edge_id"]))
        if edge["from_node_id"] not in active_ids or edge["to_node_id"] not in active_ids:
            issues.append(_issue("event_edge_targets_excluded_node", "topology", "error", path,
                "活跃关系不能连接已排除节点。", edge_id=edge["edge_id"]))
        shape = (edge["kind"], edge["from_node_id"], edge["to_node_id"])
        if edge_shapes[shape] > 1:
            issues.append(_issue("event_edge_duplicate_binding", "topology", "error", path,
                "相同类型和端点的事件关系重复。", edge_id=edge["edge_id"]))

    for index, source in enumerate(sources):
        path = f"topology.sources[{index}]"
        if source["disposition"] == "pending":
            issues.append(_issue("event_source_pending", "topology",
                "error" if declared_complete else "warning", path,
                "来源盘点项尚未决定计入或排除。", source_id=source["source_id"]))
        if source["disposition"] == "modeled" and not source["source_refs"]:
            issues.append(_issue("event_source_evidence_missing", "topology",
                "error" if declared_complete else "warning", path + ".source_refs",
                "已建模来源尚未声明资料依据。", source_id=source["source_id"]))
        if source["disposition"] == "modeled" and any(
                row["status"] == "pending" for row in source["source_refs"]):
            issues.append(_issue("event_source_evidence_pending", "topology",
                "error" if declared_complete else "warning", path + ".source_refs",
                "来源依据仍处于待查询或待核实状态。", source_id=source["source_id"]))
        if source["disposition"] == "modeled" and any(
                node_id not in active_ids for node_id in source["node_ids"]):
            issues.append(_issue("event_source_only_excluded_nodes", "topology", "error", path,
                "已建模来源不能只指向排除节点。", source_id=source["source_id"]))

    for cycle in _graph_cycles(nodes, edges):
        issues.append(_issue("event_topology_cycle", "topology", "error", "topology.edges",
            "因果/调度绑定形成循环，无法得到有限事件顺序。", node_ids=cycle))

    if not declared_complete:
        issues.append(_issue("event_topology_declaration_draft", "topology", "warning",
            "topology.declaration.completeness", "Agent尚未声明事件盘点完成。"))

    topology_blockers = [row for row in issues
                         if row["stage"] == "topology" and row["severity"] == "error"]
    numeric_blockers = [row for row in issues
                        if row["stage"] == "calculation" and row["severity"] == "error"]
    topology_passed = declared_complete and not topology_blockers
    numeric_passed = topology_passed and not numeric_blockers
    status = "numeric_ready" if numeric_passed else (
        "topology_ready" if topology_passed else "draft")

    slots = [slot for node in nodes if node["status"] != "excluded"
             for slot in node["parameter_slots"]]
    dependency_projection = [{"dependency_id": row["dependency_id"], "kind": row["kind"],
        "fingerprint": row["fingerprint"], "required": row["required"]}
        for row in topology["dependencies"]]
    receipt = {
        "contract_id": RECEIPT_CONTRACT_ID,
        "schema_version": SCHEMA_VERSION,
        "topology_id": topology["topology_id"],
        "topology_fingerprint": _fingerprint(topology),
        "action_checkpoint_fingerprint": topology["action_checkpoint"]["fingerprint"],
        "dependencies_fingerprint": _fingerprint(dependency_projection),
        "authority": RECEIPT_AUTHORITY,
        "character_semantics": "agent_declared_unverified",
        "status": status,
        "stages": {
            "topology": {"status": "passed" if topology_passed else "blocked",
                         "blocking_issue_count": len(topology_blockers)},
            "numeric_binding": {"status": "passed" if numeric_passed else "blocked",
                                "blocking_issue_count": len(numeric_blockers) + (
                                    0 if topology_passed else len(topology_blockers))},
        },
        "counts": {
            "actions": len(expected_actions),
            "sources": len(sources),
            "nodes": len(nodes),
            "active_nodes": len(active_nodes),
            "edges": len(edges),
            "parameter_slots": len(slots),
        },
        # Both fields are bounded by the closed ``NODE_KINDS`` enumeration,
        # regardless of the number of event nodes.  Calculation acceptance can
        # therefore decide which generic runtime receipts are mandatory without
        # receiving node identities, source text, or parameter values.
        "active_node_kinds": sorted(active_kind_counts),
        "active_node_kind_counts": {
            kind: active_kind_counts[kind] for kind in sorted(active_kind_counts)
        },
        "coverage": {
            "actions": {"expected": len(expected_actions),
                        "anchored": len(expected_actions) - len(missing_actions),
                        "missing_action_ids": missing_actions,
                        "duplicate_action_ids": duplicate_actions},
            "sources": dict(Counter(row["disposition"] for row in sources)),
            "nodes": dict(Counter(row["status"] for row in nodes)),
            "parameter_slots": dict(Counter(row["status"] for row in slots)),
        },
        # A legal graph near the topology byte limit can expand into many more
        # repeated diagnostics than the input itself. Keep authoritative
        # blocker counts above, but bound the persisted/model-facing receipt.
        "issues": issues[:MAX_RECEIPT_ISSUES],
        "issues_total": len(issues),
        "issues_truncated": len(issues) > MAX_RECEIPT_ISSUES,
        "limitations": [
            "角色特有事实由Agent声明，本回执不核实技能段数、天赋、命座或专属触发真实性。",
            "引擎仅校验通用结构、关系绑定、调度形状和Agent声明的参数槽完整性。",
            "本合同不携带伤害结果、DPS、运行时事件或执行权限。",
        ],
    }
    receipt["receipt_fingerprint"] = _fingerprint(receipt)
    return receipt


def new_event_topology(topology_id: str, *, action_checkpoint: dict,
                       dependencies: list[dict] | None = None, note: str = "") -> dict:
    """Create an empty draft bound to one immutable action-checkpoint identity."""
    topology = {
        "contract_id": CONTRACT_ID,
        "schema_version": SCHEMA_VERSION,
        "topology_id": topology_id,
        "authority": AUTHORITY,
        "declaration": {"character_semantics": "agent_declared",
                        "completeness": "draft", "note": note},
        "action_checkpoint": copy.deepcopy(action_checkpoint),
        "dependencies": copy.deepcopy(dependencies or []),
        "sources": [],
        "nodes": [],
        "edges": [],
    }
    validate_event_topology(topology)
    return topology


def event_topology_fingerprint(topology: dict) -> str:
    """Fingerprint only a valid non-executable topology document."""
    validate_event_topology(topology)
    return _fingerprint(topology)


def _event_topology_component_schema() -> dict:
    """Return the compact JSON-Schema bundle advertised to Agent clients.

    Components use local references so schedule and slot rules are disclosed
    once instead of being duplicated inside every node schema.  Every object is
    closed and every variable-length string/array is bounded by the same limits
    as :func:`validate_event_topology`.
    """
    object_base = {"type": "object", "additionalProperties": False}
    identifier = {"type": "string", "pattern": _ID.pattern, "maxLength": 128}
    bounded_text = {"type": "string", "minLength": 1, "maxLength": MAX_TEXT}
    empty_text = {"type": "string", "maxLength": MAX_TEXT}
    nullable_text = {"anyOf": [bounded_text, {"type": "null"}]}
    sha256 = {"type": "string", "pattern": _HASH.pattern, "minLength": 64,
              "maxLength": 64}
    nullable_sha256 = {"anyOf": [sha256, {"type": "null"}]}
    nonnegative_time = {"type": "number", "minimum": 0, "maximum": MAX_SECONDS,
                        "multipleOf": 0.000001}
    positive_time = {"type": "number", "exclusiveMinimum": 0,
                     "maximum": MAX_SECONDS, "multipleOf": 0.000001}
    nullable_nonnegative_time = {"anyOf": [nonnegative_time, {"type": "null"}]}
    nullable_positive_time = {"anyOf": [positive_time, {"type": "null"}]}
    order = {"type": "integer", "minimum": -(10**9), "maximum": 10**9}

    action_anchor = {**object_base,
        "required": ["kind", "ref_id", "phase"],
        "properties": {
            "kind": {"const": "action"},
            "ref_id": identifier,
            "phase": {"enum": ["start", "end"]},
        }}
    node_anchor = {**object_base,
        "required": ["kind", "ref_id", "phase"],
        "properties": {
            "kind": {"const": "node"},
            "ref_id": identifier,
            "phase": {"enum": ["point", "start", "end"]},
        }}
    anchor = {"oneOf": [action_anchor, node_anchor]}

    occurrence = {**object_base,
        "required": ["occurrence_id", "offset_seconds", "order"],
        "properties": {
            "occurrence_id": identifier,
            "offset_seconds": nullable_nonnegative_time,
            "order": order,
        }}
    point_schedule = {**object_base,
        "required": ["kind", "anchor", "offset_seconds", "order"],
        "properties": {
            "kind": {"const": "point"},
            "anchor": {"$ref": "#/$defs/anchor"},
            "offset_seconds": nullable_nonnegative_time,
            "order": order,
        }}
    periodic_schedule = {**object_base,
        "required": ["kind", "anchor", "first_offset_seconds", "interval_seconds",
                     "count", "duration_seconds", "refresh_policy", "off_field_policy",
                     "order"],
        "properties": {
            "kind": {"const": "periodic"},
            "anchor": {"$ref": "#/$defs/anchor"},
            "first_offset_seconds": nullable_nonnegative_time,
            "interval_seconds": nullable_positive_time,
            "count": {"anyOf": [{"type": "integer", "minimum": 1,
                                    "maximum": MAX_OCCURRENCES}, {"type": "null"}]},
            "duration_seconds": nullable_positive_time,
            "refresh_policy": {"enum": sorted(REFRESH_POLICIES)},
            "off_field_policy": {"enum": sorted(OFF_FIELD_POLICIES)},
            "order": order,
        }}
    explicit_schedule = {**object_base,
        "required": ["kind", "anchor", "occurrences"],
        "properties": {
            "kind": {"const": "explicit_offsets"},
            "anchor": {"$ref": "#/$defs/anchor"},
            "occurrences": {"type": "array", "minItems": 1,
                            "maxItems": MAX_OCCURRENCES,
                            "items": {"$ref": "#/$defs/occurrence"}},
        }}
    pending_schedule = {**object_base,
        "required": ["kind", "anchor", "reason"],
        "properties": {
            "kind": {"const": "pending"},
            "anchor": {"anyOf": [{"$ref": "#/$defs/anchor"}, {"type": "null"}]},
            "reason": bounded_text,
        }}
    schedule = {"oneOf": [
        {"$ref": "#/$defs/point_schedule"},
        {"$ref": "#/$defs/periodic_schedule"},
        {"$ref": "#/$defs/explicit_offsets_schedule"},
        {"$ref": "#/$defs/pending_schedule"},
    ]}

    source_ref = {**object_base,
        "required": ["kind", "reference", "fingerprint", "status"],
        "properties": {
            "kind": {"enum": sorted(SOURCE_REF_KINDS)},
            "reference": bounded_text,
            "fingerprint": nullable_sha256,
            "status": {"enum": sorted(SOURCE_REF_STATUSES)},
            "integrity_status": {"const": "verified"},
            "knowledge_review_status": {"enum": ["approved", "pending", "mixed_or_unspecified"]},
            "execution_authority": {"const": "none"},
        },
        "allOf": [
            {"if": {"properties": {"status": {"const": "verified"}}},
             "then": {"properties": {"fingerprint": sha256}}},
            {"if": {"properties": {"status": {"const": "pending"}}},
             "then": {"properties": {"fingerprint": {"type": "null"}}}},
            {"if": {"required": ["integrity_status"]},
             "then": {"properties": {"status": {"const": "verified"}}}},
            {"if": {"required": ["knowledge_review_status"]},
             "then": {"properties": {"kind": {"const": "database_appendix"}}}},
        ]}
    source = {**object_base,
        "required": ["source_id", "category", "owner_ref", "description", "disposition",
                     "node_ids", "reason", "source_refs"],
        "properties": {
            "source_id": identifier,
            "category": {"enum": sorted(SOURCE_CATEGORIES)},
            "owner_ref": nullable_text,
            "description": bounded_text,
            "disposition": {"enum": sorted(SOURCE_DISPOSITIONS)},
            "node_ids": {"type": "array", "maxItems": MAX_NODES, "uniqueItems": True,
                         "items": identifier},
            "reason": nullable_text,
            "source_refs": {"type": "array", "maxItems": MAX_SOURCE_REFS,
                            "items": {"$ref": "#/$defs/source_ref"}},
        },
        "allOf": [
            {"if": {"properties": {"disposition": {"const": "modeled"}}},
             "then": {"properties": {"node_ids": {"minItems": 1}}}},
            {"if": {"properties": {"disposition": {"const": "not_applicable"}}},
             "then": {"properties": {"node_ids": {"maxItems": 0},
                                      "reason": bounded_text}}},
            {"if": {"properties": {"disposition": {"const": "pending"}}},
             "then": {"properties": {"reason": bounded_text}}},
        ]}

    parameter_value = {"anyOf": [
        {"type": "number"}, {"type": "string", "maxLength": MAX_TEXT},
        {"type": "boolean"}, {"type": "null"},
    ]}
    parameter_slot = {**object_base,
        "required": ["slot_id", "semantic", "value_type", "required_for", "status",
                     "value", "unit", "source_ids", "note"],
        "properties": {
            "slot_id": identifier,
            "semantic": identifier,
            "value_type": {"enum": sorted(PARAMETER_VALUE_TYPES)},
            "required_for": {"enum": sorted(PARAMETER_REQUIRED_FOR)},
            "status": {"enum": sorted(PARAMETER_STATUSES)},
            "value": parameter_value,
            "unit": nullable_text,
            "source_ids": {"type": "array", "maxItems": MAX_SOURCES,
                           "uniqueItems": True, "items": identifier},
            "note": empty_text,
        },
        "allOf": [
            {"if": {"properties": {"status": {"const": "bound"}}},
             "then": {"properties": {"source_ids": {"minItems": 1}}}},
            {"if": {"properties": {"status": {"enum": ["pending", "not_applicable"]}}},
             "then": {"properties": {"value": {"type": "null"}}}},
            {"if": {"properties": {"status": {"const": "not_applicable"}}},
             "then": {"properties": {"note": bounded_text}}},
        ]}
    bound_value_schemas = {
        "number": {"type": "number"},
        "integer": {"type": "integer"},
        "string": bounded_text,
        "boolean": {"type": "boolean"},
        "reference": identifier,
    }
    for value_type, value_schema in bound_value_schemas.items():
        parameter_slot["allOf"].append({
            "if": {"properties": {"status": {"const": "bound"},
                                   "value_type": {"const": value_type}},
                   "required": ["status", "value_type"]},
            "then": {"properties": {"value": value_schema}},
        })

    node = {**object_base,
        "required": ["node_id", "kind", "status", "schedule", "source_ids",
                     "parameter_slots", "note"],
        "properties": {
            "node_id": identifier,
            "kind": {"enum": sorted(NODE_KINDS)},
            "status": {"enum": sorted(NODE_STATUSES)},
            "schedule": {"$ref": "#/$defs/schedule"},
            "source_ids": {"type": "array", "maxItems": MAX_SOURCES,
                           "uniqueItems": True, "items": identifier},
            "parameter_slots": {"type": "array", "maxItems": MAX_PARAMETER_SLOTS,
                                "items": {"$ref": "#/$defs/parameter_slot"}},
            "note": empty_text,
        },
        "allOf": [
            {"if": {"properties": {"status": {"const": "excluded"}}},
             "then": {"properties": {"note": bounded_text}}},
            {"if": {"properties": {"kind": {"const": "action_anchor"}}},
             "then": {"properties": {"schedule": {"properties": {
                 "anchor": {"$ref": "#/$defs/action_anchor"}}}}}},
        ]}
    edge = {**object_base,
        "required": ["edge_id", "kind", "from_node_id", "to_node_id", "status",
                     "source_ids", "note"],
        "properties": {
            "edge_id": identifier,
            "kind": {"enum": sorted(EDGE_KINDS)},
            "from_node_id": identifier,
            "to_node_id": identifier,
            "status": {"enum": sorted(EDGE_STATUSES)},
            "source_ids": {"type": "array", "maxItems": MAX_SOURCES,
                           "uniqueItems": True, "items": identifier},
            "note": empty_text,
        },
        "allOf": [
            {"if": {"properties": {"status": {"const": "excluded"}}},
             "then": {"properties": {"note": bounded_text}}},
        ]}

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:genshin:event-topology-v1:components",
        "$defs": {
            "identifier": identifier,
            "action_anchor": action_anchor,
            "node_anchor": node_anchor,
            "anchor": anchor,
            "occurrence": occurrence,
            "point_schedule": point_schedule,
            "periodic_schedule": periodic_schedule,
            "explicit_offsets_schedule": explicit_schedule,
            "pending_schedule": pending_schedule,
            "schedule": schedule,
            "source_ref": source_ref,
            "source": source,
            "parameter_slot": parameter_slot,
            "node": node,
            "edge": edge,
        },
    }


def _event_topology_component_templates() -> dict:
    pending_schedule = {"kind": "pending", "anchor": None,
                        "reason": "待Agent确定调度锚点。"}
    return {
        "source": {
            "source_id": "source.pending", "category": "other", "owner_ref": None,
            "description": "待盘点来源。", "disposition": "pending", "node_ids": [],
            "reason": "待Agent查询并决定建模或排除。", "source_refs": [],
        },
        "node": {
            "node_id": "node.pending", "kind": "other", "status": "identified",
            "schedule": copy.deepcopy(pending_schedule), "source_ids": [],
            "parameter_slots": [], "note": "",
        },
        "schedule": copy.deepcopy(pending_schedule),
        "parameter_slot": {
            "slot_id": "slot.pending", "semantic": "mechanism.parameter",
            "value_type": "number", "required_for": "calculation", "status": "pending",
            "value": None, "unit": None, "source_ids": [], "note": "",
        },
        "edge": {
            "edge_id": "edge.pending", "kind": "causes",
            "from_node_id": "node.from", "to_node_id": "node.to", "status": "pending",
            "source_ids": [], "note": "",
        },
    }


def event_topology_capabilities() -> dict:
    """Machine-readable integration boundary and closed component contracts."""
    templates = _event_topology_component_templates()
    schedule_variants = {
        "point": {"kind": "point",
                  "anchor": {"kind": "action", "ref_id": "action.pending", "phase": "start"},
                  "offset_seconds": None, "order": 0},
        "periodic": {"kind": "periodic",
                     "anchor": {"kind": "action", "ref_id": "action.pending", "phase": "start"},
                     "first_offset_seconds": None, "interval_seconds": None, "count": None,
                     "duration_seconds": None, "refresh_policy": "pending",
                     "off_field_policy": "pending", "order": 0},
        "explicit_offsets": {"kind": "explicit_offsets",
                             "anchor": {"kind": "action", "ref_id": "action.pending",
                                        "phase": "start"},
                             "occurrences": [{"occurrence_id": "occurrence.1",
                                              "offset_seconds": None, "order": 0}]},
        "pending": copy.deepcopy(templates["schedule"]),
    }
    return {
        "contract_id": CONTRACT_ID,
        "schema_version": SCHEMA_VERSION,
        "authority": AUTHORITY,
        "receipt_contract_id": RECEIPT_CONTRACT_ID,
        "node_kinds": sorted(NODE_KINDS),
        "edge_kinds": sorted(EDGE_KINDS),
        "node_statuses": sorted(NODE_STATUSES),
        "parameter_statuses": sorted(PARAMETER_STATUSES),
        "schedule_kinds": ["point", "periodic", "explicit_offsets", "pending"],
        "validation_scope": ["bounded_json", "stable_identity", "closed_references",
                             "schedule_shape", "binding_structure", "declared_parameter_slots"],
        "excluded_scope": ["character_specific_game_truth", "damage_calculation",
                           "runtime_event_generation", "execution_authority", "result_acceptance"],
        "stage_disclosure": "coverage receipt omits parameter values and source-reference text",
        "limits": {"bytes": MAX_BYTES, "actions": MAX_ACTIONS, "sources": MAX_SOURCES,
                   "nodes": MAX_NODES, "edges": MAX_EDGES,
                   "parameter_slots_per_node": MAX_PARAMETER_SLOTS},
        "component_json_schema": _event_topology_component_schema(),
        "component_contracts": {
            name: {"schema_ref": f"#/$defs/{name}", "minimal_legal_template": template,
                   **({"variant_templates": schedule_variants} if name == "schedule" else {})}
            for name, template in templates.items()
        },
    }
