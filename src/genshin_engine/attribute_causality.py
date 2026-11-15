"""Bounded partial-order consistency, without rule execution or source authority.

Footprints and edge evidence are caller-supplied materialized claims. Only an
independent runtime/source adapter can certify their completeness and provenance.
No game-event list is reordered, no attribute is evaluated, and no frame is made.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import defaultdict

from genshin_engine.rotation import _schema_errors
from genshin_engine.rotation_schema import arr, obj

CONTRACT = "attribute-callback-order-v1"
LIMITS = {"nodes": 256, "edges": 2048, "footprints": 4096, "dependency_depth": 32,
          "input_bytes": 1024 * 1024, "input_depth": 24, "execution_work": 250000,
          "receipt_bytes": 8 * 1024 * 1024}
ID = {"type": "string", "minLength": 1, "maxLength": 128}


def _closed(properties):
    return obj(properties, list(properties))


DEPENDENCY = _closed({"actor": ID, "stat": {"const": "em"}, "basis": {"enum": ["base", "total", "nonextra"]}})
READ = _closed({"id": ID, **DEPENDENCY["properties"], "current_dependencies": arr(DEPENDENCY)})
COMPONENT = _closed({key: ID for key in ("source_fingerprint", "provider", "part_id", "effect_id", "recipient")})
WRITE = _closed({"id": ID, "actor": ID, "stat": {"const": "em"}, "component": COMPONENT,
                 "nonextra_inclusion": {"enum": ["included", "excluded", "unknown"]}})
CALLBACK = _closed({"kind": {"enum": ["hit_post", "reaction_post"]}, "origin": {"type": "integer", "minimum": 0},
                   "actual_run_id": ID, "parent_actual_run_id": ID, "event_id": ID})
NODE = _closed({"id": ID, "rule_id": ID, "callback": CALLBACK, "reads": arr(READ), "writes": arr(WRITE),
               "activation_keys": arr(ID)})
EDGE = _closed({"before": ID, "after": ID, "evidence_ref": ID})
EVIDENCE = _closed({"id": ID, "status": {"enum": ["approved", "assumed", "pending", "revoked", "stale"]},
                   "fingerprint": ID, "note": {"type": "string", "minLength": 1, "maxLength": 4096}})
SCHEMA = _closed({"contract": {"const": CONTRACT},
    "parent": _closed({"origin": {"type": "integer", "minimum": 0, "maximum": 1000000},
        "actual_run_id": ID, "event_id": ID, "t_us": {"type": "integer", "minimum": 0, "maximum": 10**15},
        "ruleset": {"const": "gauge-swirl-dynamic-v3"}}),
    "nodes": arr(NODE), "edges": arr(EDGE), "evidence": arr(EVIDENCE)})
MULTISTAT_CONTRACT = 'attribute-callback-order-v2'
MULTISTAT_SCHEMA = copy.deepcopy(SCHEMA)
MULTISTAT_SCHEMA['properties']['contract'] = {'const': MULTISTAT_CONTRACT}
MULTISTAT_SCHEMA['properties']['parent']['properties']['ruleset'] = {'enum': ['gauge-swirl-dynamic-v3', 'gauge-multi-aura-v4', 'none']}


def _multistat_fields(node):
    if isinstance(node, dict):
        if node.get('properties', {}).get('stat') == {'const': 'em'}:
            from genshin_engine.attribute_stats import STATS
            node['properties']['stat'] = {'enum': list(STATS)}
        for value in node.values():
            _multistat_fields(value)
    elif isinstance(node, list):
        for value in node:
            _multistat_fields(value)


_multistat_fields(MULTISTAT_SCHEMA)
MULTISTAT_SCHEMA['properties']['nodes']['items']['properties']['reads']['items']['properties'].update(
    frame_id=ID, sample_scope={'enum': ['current', 'historical']})


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf8")


class _Stop(ValueError):
    def __init__(self, code, message, path="attribute_causality", *, status="invalid"):
        self.issue = {"code": code, "message": message, "path": path, "severity": status}
        super().__init__(message)


def _fail(code, message, path="attribute_causality", *, status="invalid"):
    raise _Stop(code, message, path, status=status)


def _dep(item):
    return item["actor"], item["stat"], item["basis"]


def _component(item):
    return tuple(item[key] for key in ("source_fingerprint", "provider", "part_id", "effect_id", "recipient"))


def attribute_causality_capabilities():
    return {"contract": CONTRACT, "schema": copy.deepcopy(SCHEMA), "limits": dict(LIMITS),
        "runtime_integration": "not_connected_in_c1", "execution": "none",
        "verification_scope": "materialized_causal_consistency_no_source_authority",
        "footprint_authority": "caller_supplied_not_verified",
        "output_budget": "ready_and_nonterminal_diagnostics_plus_one_terminal_envelope_at_most_2048_bytes",
        "exhaustion": "terminal_receipt_then_AttributeCausalityStopped_on_further_calls",
        "state_conflicts": "shared_activation_or_component_identity_pending_in_c1"}


class AttributeCausalityStopped(RuntimeError):
    """The execution has exhausted its budget; the caller must stop dispatch."""


class AttributeCallbackPlanner:
    """One execution owns one planner, with cumulative work/output accounting."""
    def __init__(self, *, limits=None, multi_stat=False):
        self.multi_stat = multi_stat
        self.contract = MULTISTAT_CONTRACT if multi_stat else CONTRACT
        self._limits, self._work, self._output_bytes = dict(LIMITS), 0, 0
        self._stopped, self._terminal_bytes = False, 0
        for key, value in (limits or {}).items():
            if key not in LIMITS or type(value) is not int or not 0 < value <= LIMITS[key]:
                raise ValueError("因果规划限制只能是已知上限以内的正整数")
            self._limits[key] = value

    def usage(self):
        return {"execution_work": self._work, "receipt_bytes": self._output_bytes,
                "terminal_diagnostic_bytes": self._terminal_bytes, "stopped": self._stopped, "limits": dict(self._limits)}

    def _charge(self, count=1):
        if self._work + count > self._limits["execution_work"]:
            self._work = self._limits["execution_work"]
            _fail("causal_work_limit", "因果规划超过本次执行累计工作量", status="pending")
        self._work += count

    def _bounded(self, value, path="attribute_causality", depth=0):
        self._charge()
        if depth > self._limits["input_depth"]:
            _fail("causal_input_limit", "因果声明嵌套过深", path)
        if isinstance(value, dict):
            for key, child in value.items():
                if not isinstance(key, str) or not 0 < len(key) <= 128:
                    _fail("causal_schema", "因果对象键须为有界文本", path)
                self._bounded(child, path + "." + key, depth + 1)
        elif isinstance(value, list):
            if len(value) > self._limits["footprints"]:
                _fail("causal_input_limit", "单个因果集合超过限制", path)
            for child in value:
                self._bounded(child, path, depth + 1)
        elif isinstance(value, str):
            if not value.strip() or len(value) > (4096 if path.endswith(".note") else 128):
                _fail("causal_schema", "因果文本为空或过长", path)
        elif type(value) not in (int, float, bool, type(None)):
            _fail("causal_schema", "因果声明不是JSON值", path)

    def _normalize(self, data):
        self._bounded(data)
        try:
            encoded = _canonical(data)
        except (ValueError, TypeError, ArithmeticError) as exc:
            raise _Stop("causal_schema", "因果声明必须是有限JSON") from exc
        if len(encoded) > self._limits["input_bytes"]:
            _fail("causal_input_limit", "因果声明字节超过限制")
        errors = []
        _schema_errors(data, MULTISTAT_SCHEMA if self.multi_stat else SCHEMA, "attribute_causality", errors)
        if errors:
            _fail("causal_schema", errors[0]["message"], errors[0]["path"])
        if (len(data["nodes"]) > self._limits["nodes"] or len(data["edges"]) > self._limits["edges"]
                or len(data["evidence"]) > self._limits["edges"]):
            _fail("causal_input_limit", "因果节点或边数量超过限制")
        data = copy.deepcopy(data)
        for key in ("nodes", "evidence"):
            if len({row["id"] for row in data[key]}) != len(data[key]):
                _fail("causal_duplicate_identity", "因果标识重复", key)
            data[key].sort(key=lambda row: row["id"])
        data["edges"].sort(key=lambda row: (row["before"], row["after"], row["evidence_ref"]))
        count, reaction_identity = 0, None
        for node in data["nodes"]:
            callback = node["callback"]
            if (callback["origin"] != data["parent"]["origin"]
                    or callback["parent_actual_run_id"] != data["parent"]["actual_run_id"]):
                _fail("causal_parent_identity", "回调不属于同一实际父事件", node["id"])
            if callback["kind"] == "hit_post" and (callback["actual_run_id"] != data["parent"]["actual_run_id"]
                    or callback["event_id"] != data["parent"]["event_id"]):
                _fail("causal_parent_identity", "命中回调身份与实际父命中不符", node["id"])
            if callback["kind"] == "reaction_post" and (callback["actual_run_id"] == data["parent"]["actual_run_id"]
                    or callback["event_id"] == data["parent"]["event_id"]):
                _fail("causal_parent_identity", "反应回调须保留独立于父命中的实际反应身份", node["id"])
            if callback["kind"] == "reaction_post":
                identity = (callback["actual_run_id"], callback["event_id"])
                if not self.multi_stat and reaction_identity is not None and identity != reaction_identity:
                    _fail("causal_reaction_scope", "首片不混合同一父命中的多次反应回调发生", node["id"])
                reaction_identity = identity
            for key in ("reads", "writes"):
                if len({row["id"] for row in node[key]}) != len(node[key]):
                    _fail("causal_duplicate_identity", "单回调读写标识重复", node["id"])
                node[key].sort(key=lambda row: row["id"])
                count += len(node[key])
            if len(node["activation_keys"]) != len(set(node["activation_keys"])):
                _fail("causal_duplicate_identity", "单回调激活状态标识重复", node["id"])
            node["activation_keys"].sort()
            count += len(node["activation_keys"])
            for read in node["reads"]:
                dependencies = read["current_dependencies"]
                count += len(dependencies)
                keys = {_dep(item) for item in dependencies}
                if len(keys) != len(dependencies):
                    _fail("causal_duplicate_identity", "当前读取闭包含重复项", node["id"])
                historical = self.multi_stat and read.get('sample_scope') == 'historical'
                if historical and dependencies:
                    _fail('causal_dependency_historical', '历史快照不能伪装成当前写入依赖', node['id'])
                if read["basis"] != "base" and not historical and _dep(read) not in keys:
                    _fail("causal_dependency_root_missing", "当前取样读取不能省略自身属性依赖", node["id"])
                if read["basis"] == "base" and dependencies:
                    _fail("causal_dependency_base", "BASE读取不消费动态属性闭包", node["id"])
                dependencies.sort(key=_dep)
            for write in node["writes"]:
                if write["component"]["recipient"] != write["actor"]:
                    _fail("causal_component_recipient", "写入组件受益者与属性角色不符", node["id"])
        if count > self._limits["footprints"]:
            _fail("causal_input_limit", "因果读写足迹总数超过限制")
        return data

    def _plan(self, data):
        nodes = {item["id"]: item for item in data["nodes"]}
        evidence = {item["id"]: item for item in data["evidence"]}
        for item in evidence.values():
            if not re.fullmatch(r"[0-9a-f]{64}", item["fingerprint"]):
                _fail("causal_evidence_fingerprint", "证据指纹须为SHA256文本", item["id"])
        outgoing, incoming, used = {key: set() for key in nodes}, {key: set() for key in nodes}, set()
        for edge in data["edges"]:
            self._charge()
            a, b, ref = edge["before"], edge["after"], edge["evidence_ref"]
            if a not in nodes or b not in nodes or ref not in evidence:
                _fail("causal_reference", "因果边引用缺少实际节点或证据")
            if a == b:
                _fail("causal_cycle", "同一回调不能先于自身", a)
            if b in outgoing[a]:
                _fail("causal_duplicate_edge", "同一因果关系被重复声明", a)
            if evidence[ref]["status"] not in ("approved", "assumed"):
                _fail("causal_evidence_unusable", "尚未获准或已失效的证据不能确定顺序", ref, status="pending")
            outgoing[a].add(b)
            incoming[b].add(a)
            used.add(ref)
        if used != set(evidence):
            _fail("causal_unused_evidence", "因果证据没有被本批实际关系使用")

        batches, depth, emitted = [], {}, set()
        remaining = {key: len(value) for key, value in incoming.items()}
        frontier = sorted(key for key, value in remaining.items() if value == 0)
        while frontier:
            batches.append(frontier)
            next_frontier = []
            for key in frontier:
                self._charge(1 + len(outgoing[key]))
                emitted.add(key)
                depth[key] = 1 + max((depth[parent] for parent in incoming[key]), default=0)
                if depth[key] > self._limits["dependency_depth"]:
                    _fail("causal_depth_limit", "因果最长依赖链超过限制", key, status="pending")
                for target in outgoing[key]:
                    remaining[target] -= 1
                    if remaining[target] == 0:
                        next_frontier.append(target)
            frontier = sorted(next_frontier)
        if len(emitted) != len(nodes):
            _fail("causal_cycle", "因果关系存在循环")
        reach = {key: set() for key in nodes}
        for batch in reversed(batches):
            for key in batch:
                for child in sorted(outgoing[key]):
                    self._charge(1 + len(reach[child]))
                    reach[key].add(child)
                    reach[key].update(reach[child])

        mutations, writes = {}, defaultdict(list)
        for key, node in nodes.items():
            identities = [("rule", node["rule_id"])]
            identities.extend(("activation", item) for item in node["activation_keys"])
            identities.extend(("component", _component(item["component"])) for item in node["writes"])
            for identity in identities:
                self._charge()
                if identity in mutations:
                    previous = mutations[identity]
                    ordered_occurrence = (self.multi_stat and nodes[previous]['rule_id'] == node['rule_id']
                        and nodes[previous]['callback']['actual_run_id'] != node['callback']['actual_run_id']
                        and (key in reach[previous] or previous in reach[key]))
                    if not ordered_occurrence:
                        _fail("causal_mutation_conflict", "共享激活状态或重复组件没有同源有序发生证明", key, status="pending")
                mutations[identity] = key
            for write in node["writes"]:
                writes[write["actor"], write["stat"]].append((key, write))

        relations = []
        for reader_id, node in nodes.items():
            for read in node["reads"]:
                for dependency in read["current_dependencies"]:
                    self._charge()
                    if dependency["basis"] == "base":
                        continue
                    for writer_id, write in writes.get((dependency["actor"], dependency["stat"]), []):
                        self._charge()
                        if writer_id == reader_id:
                            relation = "own_activation_prestate"
                        elif dependency["basis"] == "nonextra" and write["nonextra_inclusion"] == "excluded":
                            relation = "excluded_from_nonextra"
                        elif reader_id in reach[writer_id]:
                            relation = "write_before_read"
                        elif writer_id in reach[reader_id]:
                            relation = "read_before_write"
                        else:
                            _fail("causal_order_missing", "相关属性写入与取样之间没有已声明先后", reader_id, status="pending")
                        relations.append({"reader": reader_id, "read_id": read["id"], "writer": writer_id,
                            "write_id": write["id"], "dependency": copy.deepcopy(dependency), "relation": relation})
        return {"batches": batches, "relations": relations,
                "fingerprint": hashlib.sha256(_canonical(data)).hexdigest(), "materialized_input": data}

    def plan(self, batch):
        if self._stopped:
            raise AttributeCausalityStopped("因果执行已因预算终止；不得继续规划或复用本服务")
        start = self._work
        result = {"contract": self.contract, "verification_scope": "materialized_causal_consistency_no_source_authority",
                  "footprint_authority": "caller_supplied_not_verified", "status": "ready", "issues": []}
        try:
            normalized = self._normalize(batch)
            result.update(self._plan(normalized))
            result["work"] = {"this_plan": self._work - start, "execution_total": self._work}
            size = len(_canonical(result))
            if self._output_bytes + size > self._limits["receipt_bytes"]:
                _fail("causal_output_limit", "因果回执超过累计输出预算", status="pending")
            self._output_bytes += size
            return result
        except _Stop as exc:
            # Diagnostic envelope is intentionally bounded. No partial batches,
            # executable nodes, or a ready fingerprint escape a failed plan.
            diagnostic = {key: value for key, value in result.items() if key in
                    ("contract", "verification_scope", "footprint_authority")} | {
                "status": exc.issue["severity"], "issues": [exc.issue],
                "work": {"this_plan": self._work - start, "execution_total": self._work}}
            terminal = exc.issue["code"] in ("causal_work_limit", "causal_output_limit")
            size = len(_canonical(diagnostic))
            if not terminal and self._output_bytes + size > self._limits["receipt_bytes"]:
                terminal = True
                diagnostic["status"] = "pending"
                diagnostic["issues"] = [{"code": "causal_output_limit", "message": "因果诊断超过累计输出预算",
                    "path": "attribute_causality", "severity": "pending"}]
            if terminal:
                # Exactly one terminal envelope is outside the normal receipt
                # budget; subsequent calls are rejected before input traversal.
                diagnostic["terminal"] = True
                diagnostic["issues"][0]["path"] = "attribute_causality"
                self._stopped = True
                self._terminal_bytes = len(_canonical(diagnostic))
                assert self._terminal_bytes <= 2048
            else:
                self._output_bytes += size
            return diagnostic
