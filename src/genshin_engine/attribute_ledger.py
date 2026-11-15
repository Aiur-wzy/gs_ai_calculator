"""Immutable, bounded EM reads from caller-qualified constant contributions.

This service does not run mechanics, approve sources, or manufacture frames.
Only the dependency subset requested by a read is numerically evaluated.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math

from genshin_engine.attribute_budget import (
    budget_failure_details,
    first_attribute_validation_issue,
    limits_for_schema,
    normalize_attribute_budget,
    only_attribute_capacity_issues,
)
from genshin_engine.attribute_schema import (
    ATTRIBUTE_LEDGER_SCHEMA,
    BASIS,
    CONTRACT,
    CONTRIBUTION,
    FRAME,
    LIMITS,
)
from genshin_engine.execution_control import (
    ExecutionCancelled,
    check_execution_cancelled,
)
from genshin_engine.rotation import _schema_errors
from genshin_engine.rotation_schema import arr, obj


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf8")


def _issue(code, message, path, *, status="invalid"):
    return {"code": code, "message": message, "path": path, "severity": status}


class AttributeLedgerError(ValueError):
    def __init__(self, code, message, path="attribute_ledger", *, status="invalid", **details):
        self.issue = _issue(code, message, path, status=status)
        self.issue.update(details)
        super().__init__(message)


def attribute_schema_error(issues):
    first = first_attribute_validation_issue(issues)
    capacity = only_attribute_capacity_issues(issues)
    return AttributeLedgerError('attribute_declaration_limit' if capacity else 'attribute_schema',
        first['message'], first['path'], status='pending' if capacity else 'invalid',
        **{k:v for k,v in first.items() if k not in ('code','message','path','severity')})


def _component_key(component):
    return tuple(component[key] for key in
                 ("source_fingerprint", "provider", "part_id", "effect_id", "recipient"))


def _bounded_text(value, *, path="attribute_ledger", opaque=False, depth=0, max_depth=32):
    """The existing schema walker does not implement maxLength."""
    check_execution_cancelled()
    if depth > max_depth:
        raise AttributeLedgerError("attribute_declaration_limit", "属性声明嵌套深度超过限制", path,
            **budget_failure_details('declaration_depth', depth-1, depth, max_depth))
    if isinstance(value, dict):
        for key, child in value.items():
            _bounded_text(child, path=f"{path}.{key}", opaque=opaque or key == "value", depth=depth+1, max_depth=max_depth)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _bounded_text(child, path=f"{path}[{index}]", opaque=opaque, depth=depth+1, max_depth=max_depth)
    elif isinstance(value, str):
        limit = 4096 if path.endswith(".source") or opaque else 128
        if not value.strip() or len(value) > limit:
            raise AttributeLedgerError("attribute_text_limit", "属性账标识或来源文本为空或超限", path)


class AttributeLedger:
    contract = CONTRACT
    declaration_schema = ATTRIBUTE_LEDGER_SCHEMA
    contribution_schema = CONTRIBUTION
    frame_schema = FRAME

    def _checkpoint(self):
        if self._cancelled:
            raise ExecutionCancelled()
        try:
            check_execution_cancelled()
        except ExecutionCancelled:
            self._cancelled = True
            raise

    def reserve_capture_work(self, work):
        """Charge the runtime's fact capture to this execution's shared budget."""
        self._checkpoint()
        if type(work) is not int or work < 0:
            raise AttributeLedgerError("attribute_work_invalid", "属性捕获工作量必须为非负整数")
        if self._execution_work + work > self._limits["execution_work"]:
            raise AttributeLedgerError("attribute_work_limit", "属性捕获超过累计执行工作量限制", status="pending",
                **budget_failure_details('execution_work', self._execution_work, self._execution_work+work, self._limits['execution_work']))
        self._execution_work += work
        self._capture_work += work

    def usage(self):
        return {"capture_work": self._capture_work, "query_work": self._work, "registration_work": self._registration_work,
                "execution_work": self._execution_work, "declaration_bytes": self._declaration_bytes,
                "receipt_bytes": self._receipt_bytes, "frames": len(self._frames),
                "contributions": len(self._nodes), "limits": dict(self._limits)}

    def __init__(self, declaration, *, limits=None):
        self._cancelled = False
        self._checkpoint()
        try:
            effective = normalize_attribute_budget(limits)['effective']
        except ValueError as exc:
            raise AttributeLedgerError('attribute_budget_invalid', str(exc), 'attribute_limits') from exc
        self._limits = {key: effective[key] for key in LIMITS}
        self._registration_schema = limits_for_schema(
            obj({"frame": self.frame_schema, "contributions": arr(self.contribution_schema)},
                ['frame', 'contributions']),
            self._limits,
        )
        try:
            encoded = _canonical(declaration)
            if len(encoded) > self._limits["declaration_bytes"]:
                raise AttributeLedgerError("attribute_declaration_limit", "属性账声明字节超过限制",
                    **budget_failure_details('declaration_bytes', 0, len(encoded), self._limits['declaration_bytes']))
            issues = []
            _schema_errors(declaration, limits_for_schema(self.declaration_schema, effective), "attribute_ledger", issues)
            if issues:
                raise attribute_schema_error(issues)
            _bounded_text(declaration, max_depth=self._limits["declaration_depth"])
        except ExecutionCancelled:
            self._cancelled = True
            raise
        except AttributeLedgerError:
            raise
        except (ValueError, TypeError, ArithmeticError, RecursionError) as exc:
            raise AttributeLedgerError("attribute_schema", "属性账必须是有限、可序列化且深度有界的JSON") from exc
        for collection in ("initials", "contributions", "frames"):
            if len(declaration[collection]) > self._limits[collection]:
                raise AttributeLedgerError("attribute_declaration_limit", "属性账项目数量超过限制", collection, status='pending',
                    **budget_failure_details(collection,0,len(declaration[collection]),self._limits[collection]))
        model = copy.deepcopy(declaration)
        self._initials, self._nodes, self._frames, self._active = {}, {}, {}, {}
        self._cache, self._work, self._receipt_bytes = {}, 0, 0
        self._published = {}
        self._registration_work, self._execution_work = 0, 0
        self._capture_work = 0
        self._declaration_bytes = len(encoded)
        for seed in model["initials"]:
            self._checkpoint()
            identity = (seed["actor"], seed["stat"])
            if identity in self._initials:
                raise AttributeLedgerError("attribute_duplicate_basis", "同一角色属性的初始基底重复", str(identity))
            self._initials[identity] = seed
            identities = set()
            for item in seed["included_components"]:
                key = _component_key(item["component"])
                if item["component"]["recipient"] != seed["actor"] or key in identities:
                    raise AttributeLedgerError("attribute_duplicate_delivery", "已含面板组件重复或受益者不一致", str(identity))
                identities.add(key)
        for node in model["contributions"]:
            self._checkpoint()
            self._validate_node(node)
            if node["id"] in self._nodes:
                raise AttributeLedgerError("attribute_duplicate_id", "属性贡献行ID重复", node["id"])
            if (node["component"]["recipient"], node["stat"]) not in self._initials:
                raise AttributeLedgerError("attribute_basis_reference", "属性贡献受益者没有初始属性声明", node["id"])
            if len(node["constraints"]) != len(set(node["constraints"])):
                raise AttributeLedgerError("attribute_duplicate_constraint", "属性上下文约束重复", node["id"])
            node["constraints"].sort()
            self._nodes[node["id"]] = node
        frame_keys = set()
        self._frame_keys = frame_keys
        for frame in model["frames"]:
            self._checkpoint()
            self._validate_frame(frame)
            key = tuple(frame[field] for field in ("origin", "run_id", "phase", "state_epoch"))
            if frame["id"] in self._frames or key in frame_keys:
                raise AttributeLedgerError("attribute_frame_alias", "取样帧ID重复或同一帧被不同ID冒名", frame["id"])
            if not frame["run_id"].startswith(f"{frame['origin']}:") or frame["run_id"].endswith(":"):
                raise AttributeLedgerError("attribute_frame_origin", "取样帧run_id必须带相同origin前缀", frame["id"])
            refs = frame["contribution_ids"]
            if len(refs) != len(set(refs)) or not set(refs) <= self._nodes.keys():
                raise AttributeLedgerError("attribute_frame_reference", "帧引用不存在或重复的属性贡献", frame["id"])
            frame["contribution_ids"].sort()
            frame_keys.add(key)
            self._frames[frame["id"]] = frame
            for ident in refs:
                node = self._nodes[ident]
                group = (frame["id"], node["component"]["recipient"], node["stat"])
                self._active.setdefault(group, []).append(node)
        for nodes in self._active.values():
            nodes.sort(key=lambda row: (_component_key(row["component"]), row["instance_id"], row["layer_id"], row["id"]))

    def adopt_frame(self, frame, *, contributions=()):
        """Atomically register caller-supplied facts without replacing old ones.

        A semantic replay is idempotent for storage, but every attempt consumes
        bounded registration work. Validation scans only the incoming rows.
        """
        self._checkpoint()
        nodes = contributions
        refs = frame.get("contribution_ids", []) if isinstance(frame, dict) else []
        work = 2 + (len(nodes) if isinstance(nodes, (list, tuple)) else 0) + (len(refs) if isinstance(refs, list) else 0)
        for dimension,used in (('registration_work',self._registration_work),('execution_work',self._execution_work)):
            if used + work > self._limits[dimension]:
                raise AttributeLedgerError("attribute_registration_limit", "属性帧登记超过累计工作量限制", status="pending",
                    **budget_failure_details(dimension,used,used+work,self._limits[dimension]))
        self._registration_work += work
        self._execution_work += work
        if isinstance(nodes, tuple):
            nodes = list(nodes)
        payload = {"frame": frame, "contributions": nodes}
        try:
            encoded = _canonical(payload)
            if len(encoded) > self._limits["declaration_bytes"]:
                raise AttributeLedgerError("attribute_declaration_limit", "属性帧增量声明字节超过限制", status='pending',
                    **budget_failure_details('declaration_bytes',0,len(encoded),self._limits['declaration_bytes']))
            issues = []
            _schema_errors(payload, self._registration_schema, "registration", issues)
            if issues:
                raise attribute_schema_error(issues)
            _bounded_text(payload, max_depth=self._limits["declaration_depth"])
        except ExecutionCancelled:
            self._cancelled = True
            raise
        except AttributeLedgerError:
            raise
        except (ValueError, TypeError, ArithmeticError, RecursionError) as exc:
            raise AttributeLedgerError("attribute_schema", "属性帧增量必须是有限、可序列化的JSON") from exc
        payload = copy.deepcopy(payload)
        frame, candidates = payload["frame"], {}
        self._validate_frame(frame)
        for node in payload["contributions"]:
            self._checkpoint()
            self._validate_node(node)
            ident = node["id"]
            if ident in candidates:
                raise AttributeLedgerError("attribute_duplicate_id", "本次登记的属性贡献行ID重复", ident)
            if (node["component"]["recipient"], node["stat"]) not in self._initials:
                raise AttributeLedgerError("attribute_basis_reference", "属性贡献受益者没有初始属性声明", ident)
            if len(node["constraints"]) != len(set(node["constraints"])):
                raise AttributeLedgerError("attribute_duplicate_constraint", "属性上下文约束重复", ident)
            node["constraints"].sort()
            if ident in self._nodes and _canonical(node) != _canonical(self._nodes[ident]):
                raise AttributeLedgerError("attribute_node_replacement", "不能替换已登记的属性贡献事实", ident)
            candidates[ident] = node
        if not frame["run_id"].startswith(f"{frame['origin']}:") or frame["run_id"].endswith(":"):
            raise AttributeLedgerError("attribute_frame_origin", "取样帧run_id必须带相同origin前缀", frame["id"])
        refs = frame["contribution_ids"]
        if len(refs) != len(set(refs)) or any(ident not in candidates and ident not in self._nodes for ident in refs):
            raise AttributeLedgerError("attribute_frame_reference", "帧引用不存在或重复的属性贡献", frame["id"])
        refs.sort()
        added = {ident: node for ident, node in candidates.items() if ident not in self._nodes}
        if frame["id"] in self._frames:
            if frame != self._frames[frame["id"]] or added:
                raise AttributeLedgerError("attribute_frame_replacement", "已有帧只允许相同事实重读，不能覆盖或借重读加入新节点", frame["id"])
            return frame["id"]
        frame_key = tuple(frame[field] for field in ("origin", "run_id", "phase", "state_epoch"))
        if frame_key in self._frame_keys:
            raise AttributeLedgerError("attribute_frame_alias", "同一取样帧不能换ID重复登记", frame["id"])
        for dimension, used, attempted in (('frames',len(self._frames),len(self._frames)+1),
                                           ('contributions',len(self._nodes),len(self._nodes)+len(added))):
            if attempted > self._limits[dimension]:
                raise AttributeLedgerError("attribute_declaration_limit", "属性帧或贡献累计数量超过限制", status="pending",
                    **budget_failure_details(dimension,used,attempted,self._limits[dimension]))
        # Exact incremental size of the canonical full declaration: only new
        # array elements and separating commas change. Never serialize history.
        added_bytes = sum(len(_canonical(node)) for node in added.values())
        added_bytes += len(added) if self._nodes else max(0, len(added) - 1)
        added_bytes += len(_canonical(frame)) + bool(self._frames)
        if self._declaration_bytes + added_bytes > self._limits["declaration_bytes"]:
            raise AttributeLedgerError("attribute_declaration_limit", "属性帧声明累计字节超过限制", status="pending",
                **budget_failure_details('declaration_bytes',self._declaration_bytes,self._declaration_bytes+added_bytes,self._limits['declaration_bytes']))
        # All checks precede publication; failure only spends attempted work.
        self._checkpoint()
        self._nodes.update(added)
        self._frames[frame["id"]] = frame
        self._frame_keys.add(frame_key)
        self._declaration_bytes += added_bytes
        groups = {}
        for ident in refs:
            node = self._nodes[ident]
            group = (frame["id"], node["component"]["recipient"], node["stat"])
            groups.setdefault(group, []).append(node)
        for group, rows in groups.items():
            self._active[group] = sorted(rows, key=lambda row: (_component_key(row["component"]), row["instance_id"], row["layer_id"], row["id"]))
        return frame["id"]

    def _receipt(self, query, status, rows, issues=(), *, value=None, baseline=None):
        issues = copy.deepcopy(list(issues))
        for issue in issues:
            if issue.get('code') == 'attribute_work_limit' and getattr(self, '_last_budget_failure', None):
                issue.update(self._last_budget_failure)
        result = {"contract": self.contract, "qualification_scope": "caller_supplied_frame", "status": status,
                  "query": copy.deepcopy(query), "contributions": copy.deepcopy(rows), "issues": list(issues)}
        if status == "known":
            result.update(value=value, baseline=baseline)
        result["receipt_id"] = hashlib.sha256(_canonical(result)).hexdigest()
        return result

    def read_stat(self, actor, stat, basis, frame_id, *, purpose="ordinary"):
        self._checkpoint()
        raw = {"actor": actor, "stat": stat, "basis": basis, "frame_id": frame_id, "purpose": purpose}
        if any(not isinstance(value, str) or not value.strip() or len(value) > 128 for value in raw.values()):
            safe = {key: value if isinstance(value, str) and len(value) <= 128 else f"<{type(value).__name__}>"
                    for key, value in raw.items()}
            return self._receipt(safe, "invalid", [], [_issue("attribute_query", "属性查询标识必须是有界文本", "query")])
        frame = self._frames.get(frame_id)
        query = {"actor": actor, "stat": stat, "basis": basis, "purpose": purpose,
                 "frame": {key: value for key, value in frame.items() if key != "contribution_ids"} if frame else {"id": frame_id}}
        identity = tuple(frame[field] for field in ("origin", "run_id", "phase", "state_epoch")) if frame else None
        key = (actor, stat, basis, frame_id, identity, purpose)
        if key in self._cache:
            return copy.deepcopy(self._cache[key])
        result = self._read(query, frame)
        return self._publish(key, result)

    def _cacheable(self, result):
        return True

    def _validate_node(self, node):
        """Additional contract-specific checks, before an atomic commit."""

    def _validate_frame(self, frame):
        """Additional closed-frame facts checked before publication."""

    def _context_issue(self, query, frame):
        if query["purpose"] != "ordinary":
            return _issue("attribute_context_unsupported", "L1不支持无类型上下文的命中或其他用途属性查询", "query.purpose", status="pending")

    def _node_qualification_issue(self, node, query):
        """Contract-specific membership proof, before evaluating any value."""

    def _query_support_issue(self, query):
        if query['stat'] != 'em' or query['basis'] not in BASIS:
            return _issue('attribute_query_unsupported', 'L1只支持em与明确的total/nonextra/base查询', 'query', status='pending')

    def _publish(self, key, result):
        size = 0 if result["receipt_id"] in self._published else len(_canonical(result))
        if self._receipt_bytes + size > self._limits["receipt_bytes"]:
            issue = _issue("attribute_receipt_limit", "属性账累计回执字节超过限制", "receipts", status="pending")
            issue.update(budget_failure_details('receipt_bytes',self._receipt_bytes,self._receipt_bytes+size,self._limits['receipt_bytes']))
            return self._receipt(result["query"], "pending", [], [issue])
        self._receipt_bytes += size
        self._published[result["receipt_id"]] = result
        if self._cacheable(result):
            self._cache[key] = result
        return copy.deepcopy(result)

    def _node_value(self, node, query):
        if isinstance(node["value"], dict):
            return {"issue": _issue("attribute_expression_unsupported", "L1不执行属性表达式或转换依赖；不能按转换前值代替", node["id"], status="pending")}
        return {"value": node["value"], "metadata": {}}

    def _charge_query(self, amount):
        self._checkpoint()
        for dimension,used in (('query_work',self._work),('execution_work',self._execution_work)):
            if used + amount > self._limits[dimension]:
                self._last_budget_failure = budget_failure_details(dimension,used,used+amount,self._limits[dimension])
                return False
        self._work += amount
        self._execution_work += amount
        return True

    def _qualification_walk(self, query, frame):
        """Yield one qualified row at a time, preserving arithmetic interleaving."""
        actor, stat, basis = query["actor"], query["stat"], query["basis"]

        def fail(code, message, path, status="pending"):
            return {"kind": "issue", "issue": _issue(code, message, path, status=status)}

        if not self._charge_query(1):
            yield fail("attribute_work_limit", "属性查询累计工作量超过限制", "query")
            return
        if frame is None:
            yield fail("attribute_frame_missing", "请求的取样帧尚未提供，不能回退当前帧", query["frame"]["id"])
            return
        support_issue = self._query_support_issue(query)
        if support_issue:
            yield {'kind': 'issue', 'issue': support_issue}
            return
        context_issue = self._context_issue(query, frame)
        if context_issue:
            yield {"kind": "issue", "issue": context_issue}
            return
        seed = self._initials.get((actor, stat))
        if seed is None or basis not in seed:
            yield fail("attribute_basis_missing", "缺少该属性的显式基底，不能用total替代", f"initials.{actor}.{stat}.{basis}")
            return
        nodes = self._active.get((frame["id"], actor, stat), []) if basis != "base" else []
        included = seed["included_components"] if basis != "base" else []
        if not self._charge_query(len(nodes) + len(included)):
            yield fail("attribute_work_limit", "属性查询累计工作量超过限制", "query")
            return
        yield {"kind": "seed", "value": seed[basis]}
        static_keys = {_component_key(item["component"]) for item in included}
        for item in sorted(included, key=lambda row: _component_key(row["component"])):
            self._checkpoint()
            yield {"kind": "static", "row": {**copy.deepcopy(item), "decision": "already_in_baseline"}}
        identities = set()
        for node in nodes:
            self._checkpoint()
            component_key = _component_key(node["component"])
            identity = (component_key, node["instance_id"], node["layer_id"])
            row = copy.deepcopy(node)
            row["decision"] = "pending"
            if component_key in static_keys or identity in identities:
                yield {**fail("attribute_duplicate_delivery", "同一已含组件或同一实例层被重复交付", node["id"], "invalid"), "row": row}
                return
            identities.add(identity)
            qualification_issue = self._node_qualification_issue(node, query)
            if qualification_issue:
                yield {"kind": "issue", "row": row, "issue": qualification_issue}
                return
            if basis == "nonextra":
                if node["nonextra_inclusion"] == "unknown":
                    yield {**fail("attribute_classification_unknown", "贡献的NONEXTRA纳入类别尚未明确", node["id"]), "row": row}
                    return
                if node["nonextra_inclusion"] == "excluded":
                    row["decision"] = "excluded_from_nonextra"
                    yield {"kind": "excluded", "row": row}
                    continue
            if node["visibility"] != "attribute" or node["constraints"]:
                yield {**fail("attribute_context_unsupported", "受筛选、限次或共享资源约束的属性需要已执行上下文许可", node["id"]), "row": row}
                return
            yield {"kind": "contribution", "row": row, "node": node}

    def _read(self, query, frame):
        rows, terms = [], []
        for item in self._qualification_walk(query, frame):
            if "row" in item:
                rows.append(item["row"])
            if item["kind"] == "issue":
                issue = item["issue"]
                return self._receipt(query, issue["severity"], rows, [issue])
            if item["kind"] == "seed":
                terms.append(item["value"])
            if item["kind"] != "contribution":
                continue
            node, row = item["node"], item["row"]
            evaluated = self._node_value(node, query)
            if "issue" in evaluated:
                issue = evaluated["issue"]
                return self._receipt(query, issue["severity"], rows, [issue])
            row["decision"] = "included"
            row.update(evaluated["metadata"])
            terms.append(evaluated["value"])
        try:
            value = math.fsum(terms)
        except ArithmeticError:
            return self._receipt(query, "invalid", rows, [_issue("attribute_value_invalid", "属性稳定求和溢出或不是有限值", "query")])
        if not math.isfinite(value) or value < 0:
            return self._receipt(query, "invalid", rows, [_issue("attribute_value_invalid", "有效属性必须有限且非负", "query")])
        return self._receipt(query, "known", rows, value=value, baseline=terms[0])
