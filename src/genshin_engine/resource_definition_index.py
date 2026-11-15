"""Private research candidate: bounded JSON identity checks, no game authority.

The caller supplies roots/spine/scopes. This module proves their identity only;
it does not prove semantic completeness of the spine, lifecycle completion,
source approval, or that a caller occurrence is a genuine reaction. Nothing
returned here authorizes a ledger operation. Actual RQ2 integration is separate.
"""
from __future__ import annotations

import hashlib
import json
import math


class IndexError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def reject(code, message):
    raise IndexError(code, message)


class Budget:
    """Monotone prepaid accounting; actual structural visits reported separately."""

    def __init__(self, *, work=2_000_000, storage=64 * 1024 * 1024,
                 encoding=64 * 1024 * 1024, graph_bytes=8 * 1024 * 1024,
                 scope_references=100_000, containers=30_000, edges=200_000,
                 charge=None, consume=None):
        values = (work, storage, encoding, graph_bytes, scope_references, containers, edges)
        if any(type(value) is not int or value <= 0 for value in values):
            reject("index_limit", "Limits must be positive exact integers")
        self.work_limit, self.storage_limit, self.encoding_limit = work, storage, encoding
        self.graph_bytes_limit = graph_bytes
        self.scope_reference_limit, self.container_limit, self.edge_limit = scope_references, containers, edges
        self.work = self.reserved_work = self.storage = self.encoding = 0
        self.rejected_reservations = 0
        self._external_charge, self._external_consume = charge, consume

    def pay(self, work=0, storage=0, encoding=0):
        if (self.work + self.reserved_work + work > self.work_limit
                or self.storage + storage > self.storage_limit
                or self.encoding + encoding > self.encoding_limit):
            self.rejected_reservations += 1
            reject("index_limit", "Bounded work/storage/encoding exhausted before allocation or access")
        if self._external_charge is not None:
            self._external_charge(work)
        self.work += work
        self.storage += storage
        self.encoding += encoding

    def reserve(self, work):
        if self.work + self.reserved_work + work > self.work_limit:
            self.rejected_reservations += 1
            reject("index_limit", "Cannot reserve future verification work")
        if self._external_charge is not None:
            self._external_charge(0, tickets=work)
        self.reserved_work += work

    def consume(self, work):
        if type(work) is not int or not 0 <= work <= self.reserved_work:
            reject("index_internal", "Invalid owned work reservation")
        if self._external_consume is not None:
            self._external_consume(work)
        self.reserved_work -= work
        self.work += work

    def report(self):
        return {"work_consumed": self.work, "work_reserved": self.reserved_work,
                "storage_reserved_bytes": self.storage, "encoding_upper_bytes": self.encoding,
                "rejected_reservations": self.rejected_reservations}


class _Ticket:
    __slots__ = ()


class DefinitionIndex:
    """One bind, prepaid original-reference edges, one-use attempt/final tickets.

    `spine_nodes` are shallow whole-container checks. Include routing descendants
    explicitly. A scope checks its seed descendants and all ancestor edges from
    roots; ancestor traversal does not make unrelated descendants relevant.
    Object equality is never used for source values or dictionary-key identity.
    """

    def __init__(self, roots, *, spine_nodes, scopes, budget=None):
        self.budget = budget if budget is not None else Budget()
        if type(self.budget) is not Budget:
            reject("index_schema", "Only the owned candidate Budget is accepted")
        self._closed = False
        self._records = {}
        self._parents = {}
        self._root_edges = ()
        self._roots_by_name = {}
        self._scopes = {}
        self._scope_costs = {}
        self._spine = ()
        self._seen_attempts = {}
        self._tickets = {}
        self._edge_count = self._key_references = self._scope_references = 0
        self._root_visits = self._container_visits = self._edge_visits = 0
        self._validate_roots(roots)
        # Full expanded JSON preflight: aliases count each serialized occurrence;
        # identity index records below deduplicate actual containers only.
        self._preflight(roots, set(), 0)
        upper = self.budget.encoding
        self.budget.pay(storage=upper * 5 + 512)
        try:
            encoded = json.dumps(roots, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":"), allow_nan=False).encode("utf-8")
        except (UnicodeError, ValueError, OverflowError) as exc:
            raise IndexError('index_schema', 'Declaration is not canonical finite UTF-8 JSON') from exc
        if len(encoded) > self.budget.graph_bytes_limit:
            reject("index_limit", "Complete canonical graph exceeds byte bound")
        self.canonical_bytes = len(encoded)
        self._fingerprint = hashlib.sha256(encoded).hexdigest()
        self.budget.pay(work=1 + len(roots), storage=256 + 96 * len(roots))
        self._root_edges = tuple(roots.items())
        self._roots_by_name = dict(self._root_edges)
        for _, root in self._root_edges:
            self._bind(root, set(), 0)
        self._compile_spine(spine_nodes)
        self._compile_scopes(scopes)
        self.budget.pay(work=len(self._records))
        self._final_structural_cost = self._root_cost() + sum(self._record_cost(rid) for rid in self._records)
        self._final_cost = self._final_structural_cost + 1 + len(self._scopes)
        self.budget.pay(storage=1024 + 128 * len(self._scopes))
        # If this fails the constructor cannot issue an attempt.
        self.budget.reserve(self._final_cost)

    def _validate_roots(self, roots):
        if type(roots) is not dict or not 0 < len(roots) <= 128:
            reject("index_schema", "Expected 1..128 named roots")
        self.budget.pay(work=1 + len(roots))
        for key in roots:
            if type(key) is not str or not 0 < len(key) <= 128:
                reject("index_schema", "Root names must be bounded exact strings")

    def _preflight(self, value, active, depth):
        self.budget.pay(work=1)
        if depth > 64:
            reject("index_depth", "Declaration depth exceeds 64")
        kind = type(value)
        if kind in (dict, list):
            if id(value) in active:
                reject("index_cycle", "Cyclic declarations are not JSON")
            self.budget.pay(work=len(value), storage=160, encoding=2 + len(value) * 2)
            active.add(id(value))
            if kind is dict:
                for key, child in value.items():
                    if type(key) is not str:
                        reject("index_schema", "Dictionary keys must be exact strings")
                    self._preflight(key, active, depth + 1)
                    self._preflight(child, active, depth + 1)
            else:
                for child in value:
                    self._preflight(child, active, depth + 1)
            active.remove(id(value))
        elif kind is str:
            # No encoding/materialization before paying control-character-safe bound.
            if len(value) > self.budget.graph_bytes_limit:
                reject("index_limit", "One string alone exceeds the whole canonical graph bound")
            self.budget.pay(encoding=6 * len(value) + 2)
        elif kind is int:
            if value.bit_length() > 256:
                reject("index_schema", "Integer exceeds bounded JSON declaration range")
            self.budget.pay(encoding=80)
        elif kind is float:
            if not math.isfinite(value):
                reject("index_schema", "Nonfinite declarations are not accepted")
            self.budget.pay(encoding=32)
        elif kind in (bool, type(None)):
            self.budget.pay(encoding=5)
        else:
            reject("index_schema", "Only exact built-in finite JSON is accepted")

    def _bind(self, node, active, depth):
        self.budget.pay(work=1)
        if type(node) not in (dict, list):
            return
        rid = id(node)
        if rid in active:
            reject("index_cycle", "Cyclic declaration during binding")
        if rid in self._records:
            return
        size = len(node)
        if len(self._records) >= self.budget.container_limit or self._edge_count + size > self.budget.edge_limit:
            reject("index_limit", "Container/edge bound exceeded")
        # Includes original edge tuples, integer-key lookup, reverse refs, and
        # temporary construction/visited storage; conservative, not sys.getsizeof.
        self.budget.pay(work=1 + size, storage=768 + 320 * size)
        edges = tuple(node.items()) if type(node) is dict else tuple(enumerate(node))
        keyed = {id(key): (key, child) for key, child in edges} if type(node) is dict else None
        self._records[rid] = (node, edges, keyed)
        self._parents.setdefault(rid, set())
        self._edge_count += size
        self._key_references += size if type(node) is dict else 0
        active.add(rid)
        for _, child in edges:
            self._bind(child, active, depth + 1)
            if type(child) in (dict, list):
                self._parents[id(child)].add(rid)
        active.remove(rid)

    def _seeds(self, values):
        if type(values) not in (tuple, list):
            reject("index_schema", "Scope/spine seeds must be an exact sequence")
        self.budget.pay(work=1 + len(values), storage=256 + 160 * len(values))
        result = set()
        for value in values:
            rid = id(value)
            if type(value) not in (dict, list) or rid not in self._records or self._records[rid][0] is not value:
                reject("index_scope", "Scope seed must be an original root-reachable container")
            result.add(rid)
        return result

    def _closure(self, ids, *, descendants):
        self.budget.pay(storage=256 + 128 * len(ids), work=len(ids))
        result, pending = set(ids), list(ids)
        while pending:
            rid = pending.pop()
            size = len(self._records[rid][1]) if descendants else len(self._parents[rid])
            self.budget.pay(work=1 + size, storage=64 + 32 * size)
            children = (tuple(id(child) for _, child in self._records[rid][1]
                              if type(child) in (dict, list)) if descendants else self._parents[rid])
            for child in children:
                if child not in result:
                    self.budget.pay(storage=160)
                    result.add(child)
                    pending.append(child)
        return result

    def _vector(self, ids):
        if self._scope_references + len(ids) > self.budget.scope_reference_limit:
            reject("index_limit", "Aggregate scope/spine reference bound exceeded")
        self.budget.pay(work=len(ids), storage=64 + 32 * len(ids))
        self._scope_references += len(ids)
        return tuple(sorted(ids))

    def _compile_spine(self, nodes):
        seeds = self._seeds(nodes)
        self._spine = self._vector(self._closure(seeds, descendants=False))

    def _compile_scopes(self, scopes):
        if type(scopes) is not dict or not 0 < len(scopes) <= 4096:
            reject("index_schema", "Expected bounded nonempty scope directory")
        self.budget.pay(work=1 + len(scopes), storage=256 + 160 * len(scopes))
        for key, seeds in scopes.items():
            if type(key) is not str or not 0 < len(key) <= 512:
                reject("index_schema", "Scope IDs must be bounded exact strings")
            descendants = self._closure(self._seeds(seeds), descendants=True)
            ancestors = self._closure(descendants, descendants=False)
            self.budget.pay(work=len(ancestors) + len(self._spine), storage=256 + 160 * (len(ancestors) + len(self._spine)))
            self._scopes[key] = self._vector(ancestors.union(self._spine))
            self.budget.pay(work=len(self._scopes[key]), storage=96)
            self._scope_costs[key] = self._root_cost() + sum(self._record_cost(rid) for rid in self._scopes[key])

    def _root_cost(self):
        return 1 + len(self._root_edges)

    def _record_cost(self, rid):
        return 1 + len(self._records[rid][1])

    def _check_roots(self, current):
        self._root_visits += 1
        if type(current) is not dict or len(current) != len(self._root_edges):
            reject("index_changed", "Current root directory differs")
        # Compare bounded exact strings after type validation, no custom equality.
        expected = self._roots_by_name
        for key, child in current.items():
            self._edge_visits += 1
            if type(key) is not str or key not in expected or child is not expected[key]:
                reject("index_changed", "Current root edge is detached")

    def _check_record(self, rid):
        node, edges, keyed = self._records[rid]
        self._container_visits += 1
        if len(node) != len(edges):
            reject("index_changed", "Declaration keys/length changed")
        if type(node) is dict:
            for key, child in node.items():
                self._edge_visits += 1
                # id(key) lookup avoids attacker __hash__/__eq__; strong original
                # refs prevent id reuse. Even equal newly allocated keys can fail.
                expected = keyed.get(id(key)) if type(key) is str else None
                if expected is None or key is not expected[0] or child is not expected[1]:
                    reject("index_changed", "Declaration dictionary edge changed")
        else:
            for (_, expected), child in zip(edges, node, strict=True):
                self._edge_visits += 1
                if child is not expected:
                    reject("index_changed", "Declaration array edge changed")

    def _check(self, current, records):
        self._check_roots(current)
        for rid in records:
            self._check_record(rid)

    def prepare(self, current_roots, scope_id, occurrence):
        if self._closed:
            reject("index_closed", "The binding is finalized")
        if type(scope_id) is not str or scope_id not in self._scopes:
            reject("index_scope", "Unknown scope")
        # This object is a caller fact in the pure API, never reaction authority.
        if type(occurrence) is not object:
            reject("index_occurrence", "Expected an opaque caller occurrence object")
        identity = (id(occurrence), scope_id)
        self.budget.pay(work=1, storage=256)
        if identity in self._seen_attempts:
            reject("index_replay", "An occurrence/scope attempt cannot be repeated")
        self._seen_attempts[identity] = occurrence
        records = self._scopes[scope_id]
        cost = self._scope_costs[scope_id]
        self.budget.reserve(cost * 2)
        self.budget.consume(cost)
        # If checking fails admission is impossible. Its prepaid work remains
        # reserved, not performed, and unavailable to future attempts.
        self._check(current_roots, records)
        self.budget.pay(work=1, storage=384)
        ticket = _Ticket()
        self._tickets[id(ticket)] = (ticket, records, cost)
        return ticket

    def admit(self, ticket, current_roots):
        if self._closed:
            reject("index_closed", "The binding is finalized")
        self.budget.pay(work=1)
        owned = self._tickets.pop(id(ticket), None)
        if owned is None or owned[0] is not ticket:
            reject("index_ticket", "Only an unconsumed original attempt ticket is accepted")
        self.budget.consume(owned[2])
        self._check(current_roots, owned[1])
        return True

    def finish(self, current_roots):
        if self._closed:
            reject("index_closed", "Final verification is one-use")
        self._closed = True
        self.budget.consume(self._final_cost)
        self._check(current_roots, self._records)
        return {"status": "candidate_identity_checked_no_runtime_authority",
                "fingerprint": self._fingerprint, **self._report()}

    def report(self):
        self.budget.pay(work=1 + len(self._scopes), storage=1024 + 128 * len(self._scopes))
        return self._report()

    def _report(self):
        return {"canonical_bytes": self.canonical_bytes,
                "unique_containers": len(self._records), "edges": self._edge_count,
                "dictionary_key_references": self._key_references,
                "spine_records": len(self._spine), "aggregate_scope_references": self._scope_references,
                "scope_check_costs": dict(self._scope_costs),
                "final_check_cost": self._final_structural_cost,
                "final_reserved_check_and_report_cost": self._final_cost,
                "actual_root_visits": self._root_visits, "actual_container_visits": self._container_visits,
                "actual_edge_visits": self._edge_visits,
                "work_accounting": "conservative prepaid nonrefundable work; not an exact instruction count",
                "closed": self._closed,
                "pending_tickets": len(self._tickets), **self.budget.report()}
