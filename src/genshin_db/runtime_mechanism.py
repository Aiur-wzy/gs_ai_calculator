"""Fresh materialization of reviewed mechanism data in one runtime transaction.

The descriptor is data; neither entity IDs nor mechanism IDs dispatch algorithms.
This read proves stored scope and source consistency, not a rotation's eligibility.
"""
from __future__ import annotations

import copy

from genshin_db.runtime_catalog import (
    MAX_SOURCE_BYTES,
    RuntimeConfigError,
    dumps,
    fingerprint,
)
from genshin_db.semantic_store import (
    SemanticStoreError,
    read_catalog,
    read_current_reviewed_record,
)
from genshin_runtime.mechanism_source_binding import LIMITS as BINDING_LIMITS
from genshin_runtime.mechanism_source_binding import (
    resolve_pointer,
    validate_source_binding,
)

LIMITS = {"max_bytes": 2 * 1024 * 1024, "max_catalog_bytes": 256 * 1024,
          "max_nodes": 32, "max_facts": 64, "max_parameters": 32, "max_issues": 64,
          "max_decoded_source_bytes": BINDING_LIMITS["max_decoded_source_bytes"]}
CATALOG_PAGINATION = {"default_page": 1, "default_page_size": 20,
                      "max_page": 2**31 - 1, "max_page_size": 50}


def mechanism_source_capabilities():
    return {"projection_version": 1, "limits": dict(LIMITS), "component": "mechanic_semantics",
            "pagination": dict(CATALOG_PAGINATION),
            "catalog": "按已选character/weapon/artifact_set及target_id发现受审资料；歧义不自动选择",
            "profile": "mechanism_id必填；revision省略只查当前head，不猜最大修订",
            "source_ready_is_execution_ready": False, "execution_support": "not_evaluated",
            "missing_component": "旧库只读返回component_missing；显式语义包导入安装，不自动批准",
            "review": "同事务重新读取事实修订与审核状态；原文approved注释不直接执行",
            "scope": "完整受审源节点及用途绑定；变更返回stale，既有未知分支不默认跳过"}


def _bounded(value, *, catalog=False):
    if len(value.get("issues", [])) > LIMITS["max_issues"]:
        raise RuntimeConfigError("机制资料诊断超过上限", "response_too_large", 413)
    if len(dumps(value).encode("utf8")) > LIMITS["max_catalog_bytes" if catalog else "max_bytes"]:
        raise RuntimeConfigError("机制资料完整回执超限，未截断", "response_too_large", 413)
    return value


def _store(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except SemanticStoreError as exc:
        raise RuntimeConfigError(str(exc), exc.reason, 409 if exc.reason == "semantic_schema_unsupported" else 500) from exc
    except (ValueError, TypeError, KeyError) as exc:
        raise RuntimeConfigError("存储的机制资料无效: " + str(exc), "semantic_dataset_invalid", 500) from exc


def _request_id(value, name):
    import re
    if type(value) is not str or re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,79}", value) is None:
        raise ValueError(name + "必须为1..80位小写标识")


def _current_review(conn, line, kind, ident, expected=None):
    try:
        return _store(read_current_reviewed_record, conn, line, kind, ident, expected)
    except RuntimeConfigError as exc:
        # Corrupt/nonunique heads cannot safely disclose one current revision.
        # Preserve the rejecting domain error and identify the failed request.
        details = {"version_line": line, "record_kind": kind, "record_id": ident,
                   "requested_revision": expected, "current_revision": None,
                   "reason": exc.reason, "review_status": None}
        raise RuntimeConfigError(str(exc), exc.reason, exc.status, details) from exc


def _current_review_issue(conn, line, kind, ident, expected, admission):
    """Describe a failed current read without returning a historical fallback."""
    observed = admission
    if admission["reason"] == "head_changed":
        # The same transaction can safely disclose the new current review. Its
        # record never replaces the explicitly referenced revision.
        observed = _current_review(conn, line, kind, ident)
    return {"code": "mechanism_current_review_pending", "record_kind": kind,
            "record_id": ident, "requested_revision": expected,
            "current_revision": admission.get("current_revision"),
            "reason": admission["reason"], "review_status": observed.get("review_status"),
            "message": "所选机制资料必须与当前已审核头一致；历史修订不能替代当前来源资格"}


def get_mechanism_catalog(db_path, *, target_type=None, target_id=None,
                          page=CATALOG_PAGINATION["default_page"],
                          page_size=CATALOG_PAGINATION["default_page_size"],
                          version_line="release", snapshot_id=None):
    from genshin_db.runtime_queries import _integer, _read
    if (target_type is None) != (target_id is None):
        raise ValueError("target_type与target_id必须成对")
    if target_type is not None:
        if target_type not in ("character", "weapon", "artifact_set"):
            raise ValueError("机制来源类型未支持")
        _integer(target_id, "target_id")
    _integer(page, "page", 1, CATALOG_PAGINATION["max_page"])
    _integer(page_size, "page_size", 1, CATALOG_PAGINATION["max_page_size"])
    with _read(db_path, version_line, snapshot_id) as (conn, state):
        result = _store(read_catalog, conn, version_line, target_type=target_type,
                        target_id=target_id, page=page, page_size=page_size)
        result.update(projection_version=1, snapshot=state, execution_support="not_evaluated")
        result["content_fingerprint"] = fingerprint(result)
        return copy.deepcopy(_bounded(result, catalog=True))


class _Reader:
    def __init__(self, conn, state):
        from genshin_db.runtime_queries import _Nodes
        self.conn, self.state = conn, state
        self.nodes = _Nodes(conn, state["version_line"])
        self.sources = {}

    def read(self, binding):
        line, path = self.state["version_line"], binding["source_path"]
        if path not in self.sources:
            row = self.conn.execute("SELECT source_size_bytes FROM runtime_source WHERE version_line=? AND source_path=?",
                                    (line, path)).fetchone()
            if row is None:
                return None, "missing"
            size = row[0]
            if type(size) is not int or not 0 <= size <= MAX_SOURCE_BYTES:
                raise RuntimeConfigError("运行原文大小无效", "dataset_invalid", 500)
            if sum(self.sources.values()) + size > LIMITS["max_decoded_source_bytes"]:
                raise RuntimeConfigError("机制资料累计解压预算不足", "response_too_large", 413)
            self.sources[path] = size
        rows = self.conn.execute("SELECT * FROM runtime_definition WHERE version_line=? AND source_path=? "
            "AND json_pointer=? AND definition_kind=? LIMIT 2",
            (line, path, binding["json_pointer"], binding["definition_kind"])).fetchall()
        if not rows:
            return None, "missing"
        if len(rows) != 1:
            return None, "ambiguous"
        row = dict(rows[0])
        expected_id = fingerprint([self.state["snapshot_id"], path, binding["json_pointer"], binding["definition_kind"]])
        if row["snapshot_id"] != self.state["snapshot_id"] or row["definition_id"] != expected_id:
            raise RuntimeConfigError("机制节点索引身份不属于当前快照", "dataset_invalid", 500)
        raw, origin = self.nodes.node(row)
        node = {"id": binding["id"], **origin, "node_sha256": row["node_sha256"], "payload": raw}
        status = "ready" if all(node[k] == binding[k] for k in ("node_sha256", "source_sha256")) else "stale"
        return node, status


def get_mechanism_profile(db_path, *, mechanism_id, revision=None, version_line="release", snapshot_id=None):
    from genshin_db.runtime_queries import _integer, _read
    from genshin_engine.mechanism_descriptor_compiler import (
        compile_mechanism_descriptor,
    )
    from genshin_runtime.mechanism_semantics import (
        descriptor_fingerprint,
        fact_fingerprint,
        parameter_fingerprint,
    )
    _request_id(mechanism_id, "mechanism_id")
    if revision is not None:
        _integer(revision, "revision", 1, 2**31 - 1)
    with _read(db_path, version_line, snapshot_id) as (conn, state):
        result = {"projection_version": 1, "snapshot": state,
            "selection": {"mechanism_id": mechanism_id, "revision": revision},
            "status": "pending", "source_status": "pending", "review_status": "pending",
            "materialization_status": "pending", "execution_support": "not_evaluated",
            "descriptor": None, "source_binding": None, "facts": [], "resolved_parameters": [],
            "source_proof": None, "compiler_result": None, "issues": [], "limits": dict(LIMITS)}
        current = _current_review(conn, version_line, "descriptor", mechanism_id, revision)
        if current["status"] != "ready":
            if current["status"] == "missing":
                result.update(source_status="missing")
            result["issues"].append(_current_review_issue(conn, version_line, "descriptor", mechanism_id, revision, current))
            result["content_fingerprint"] = fingerprint(result)
            return copy.deepcopy(_bounded(result))
        record = current["record"]
        descriptor = record["payload"]["descriptor"]
        current_facts = {}
        for ref in descriptor["evidence_fact_ids"]:
            ident, rev = ref.rsplit("@", 1)
            current = _current_review(conn, version_line, "fact", ident, int(rev))
            if current["status"] != "ready":
                result["issues"].append(_current_review_issue(conn, version_line, "fact", ident, int(rev), current))
            else:
                current_facts[ref] = current["record"]
        if result["issues"]:
            result["content_fingerprint"] = fingerprint(result)
            return copy.deepcopy(_bounded(result))
        binding = validate_source_binding(record["payload"]["source_binding"])
        result.update(descriptor=copy.deepcopy(descriptor), source_binding=copy.deepcopy(binding),
                      selection={"mechanism_id": mechanism_id, "revision": record["revision"]})
        issues, source_status = result["issues"], "ready"

        def issue(code, **details):
            if len(issues) >= LIMITS["max_issues"]:
                raise RuntimeConfigError("机制诊断超过预算", "response_too_large", 413)
            issues.append({"code": code, **details})

        applicability = {"version_line": version_line, "upstream_commit": state["upstream_commit"],
                         "normalization_fingerprint": fingerprint(state.get("normalization"))}
        if record["applicability"] != applicability or binding["upstream_commit"] != state["upstream_commit"] or binding["normalization_fingerprint"] != applicability["normalization_fingerprint"]:
            source_status = "stale"
            issue("mechanism_source_stale", scope="snapshot")
        reader, nodes = _Reader(conn, state), []
        for selected in binding["nodes"]:
            node, status = reader.read(selected)
            if node is not None:
                nodes.append(node)
                _bounded({"nodes": nodes})
            if status != "ready":
                source_status = status if source_status == "ready" else source_status
                issue("mechanism_source_" + status, node_id=selected["id"])
        facts, fact_reviews, authorizations = [], {}, {}
        if len(descriptor["evidence_fact_ids"]) > LIMITS["max_facts"]:
            raise RuntimeConfigError("机制事实数量超限", "response_too_large", 413)
        evidence = {row["id"]: row for row in binding["evidence"]}
        for ref in descriptor["evidence_fact_ids"]:
            fact = current_facts[ref]
            facts.append(fact["payload"])
            review = fact["review"]["status"]
            fact_reviews[ref] = "pending" if review == "draft" else review
            authorizations[ref] = fact["review_fingerprint"]
            if fact["subject"] != binding["subject"] or fact["applicability"] != applicability:
                fact_reviews[ref] = "stale"
                issue("mechanism_fact_scope_mismatch", fact_ref=ref)
            if any(evidence.get(row["id"]) != row for row in fact["evidence"]):
                fact_reviews[ref] = "conflict"
                issue("mechanism_fact_evidence_mismatch", fact_ref=ref)
        parameters, node_map = [], {node["id"]: node for node in nodes}
        for selected in binding["parameters"]:
            try:
                value = resolve_pointer(node_map[selected["node_id"]]["payload"], selected["pointer"])
            except (ValueError, KeyError, IndexError, TypeError):
                issue("mechanism_parameter_source_missing", parameter_ref=selected["ref"])
                continue
            parameters.append({"ref": selected["ref"], "value_type": selected["value_type"], "unit": selected["unit"],
                "value": value, "status": "ready", "origin_kind": "upstream_field", "derivation": "identity",
                "evidence_ref": {"node_id": selected["node_id"], "pointer": selected["pointer"]}})
        descriptor_status = record["review"]["status"]
        if descriptor_status == "draft":
            descriptor_status = "pending"
        review_status = descriptor_status if descriptor_status != "approved" else (
            "approved" if all(status == "approved" for status in fact_reviews.values()) else "pending")
        parameter_fingerprints = {}
        for parameter in parameters:
            try:
                parameter_fingerprints[parameter["ref"]] = parameter_fingerprint(parameter)
            except (ValueError, TypeError, OverflowError):
                # Retain the actual source value and diagnose it; never supply a default.
                issue("mechanism_parameter_invalid", parameter_ref=parameter["ref"])
        proof = {"schema_version": 1,
            "snapshot": {key: state.get(key) for key in ("version_line", "snapshot_id", "upstream_commit", "indexer_version", "normalization")},
            "subject": binding["subject"], "nodes": nodes, "evidence": binding["evidence"],
            "review": {"descriptor_status": descriptor_status if source_status == "ready" else "stale", "fact_statuses": fact_reviews},
            "coverage": binding["coverage"], "descriptor_fingerprint": descriptor_fingerprint(descriptor),
            "fact_fingerprints": {f["id"] + "@" + str(f["revision"]): fact_fingerprint(f) for f in facts},
            "parameter_fingerprints": parameter_fingerprints}
        compiled = compile_mechanism_descriptor(descriptor, source_proof=proof, facts=facts, parameters=parameters)
        qualification = compiled.get("execution_qualification")
        if qualification and qualification["schema_version"] == 3:
            provider = qualification["provider"]
            expected_selection = {"refinement": provider["refinement"]} if provider["relation"] == "equipped_weapon" else {}
            if binding["selection"] != expected_selection:
                issue("mechanism_contact_source_selection_mismatch")
                compiled.update(status="pending", execution_qualification=None, qualification_status="pending",
                                rule_templates=[], initial_rule_templates=[], execution_ports=[],
                                mechanism_fingerprint=None, required_execution_checks=[])
        materialized = source_status == "ready" and review_status == "approved" and compiled["status"] == "ready" and not issues
        result.update(status="ready" if materialized else "pending", source_status=source_status,
            review_status=review_status, materialization_status="ready" if materialized else "pending",
            facts=facts, resolved_parameters=parameters, source_proof=proof, compiler_result=compiled,
            semantic_fingerprint=compiled.get("mechanism_fingerprint"),
            authorization_fingerprint=fingerprint({"descriptor": record["review_fingerprint"], "facts": authorizations}),
            record_payload_fingerprint=record["payload_fingerprint"])
        result["content_fingerprint"] = fingerprint(result)
        return copy.deepcopy(_bounded(result))
