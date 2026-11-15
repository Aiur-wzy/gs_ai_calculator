"""Bounded acceptance coverage across engine, Agent and user-owned inputs."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

from genshin_app.equipment.provisional_entities import (
    FORMAL_RESULT_FIDELITY,
    PROVISIONAL_RESULT_FIDELITY,
    USAGE_RECEIPT_CONTRACT,
    ProvisionalEntityError,
    declared_provisional_usages,
    validate_usage_receipt,
)
from genshin_app.task_capability import CONTRACT as TASK_CAPABILITY_CONTRACT
from genshin_app.task_capability import blocking_task_issues, normalize_task_issue

CONTRACT = "staged-calculation-coverage-v1"
AUTOMATIC_RECEIPT_CONTRACT = "automatic-calculation-coverage-receipt-v1"
AUTOMATIC_SCOPE = "current_declared_effective_graph_generic_verification_dimensions"
PROVISIONAL_RECEIPT_CONTRACT = USAGE_RECEIPT_CONTRACT
PROVISIONAL_REFERENCE_CONTRACT = "provisional-entity-registry-reference-v1"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_HIGH_IMPACTS = frozenset(("high", "critical"))


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _find(sources: list[dict[str, Any]], names: tuple[str, ...]) -> Any:
    found = [source[name] for source in sources for name in names if name in source]
    if not found:
        return None
    first = found[0]
    if any(item != first for item in found[1:]):
        return {"contract": "conflicting-receipts"}
    return first


def _issue(code: str, message: str, *, blocking: bool = True) -> dict[str, Any]:
    return {"code": code, "message": message, "blocking": blocking}


def _valid_fingerprint(value: Any) -> bool:
    return isinstance(value, str) and _SHA.fullmatch(value) is not None


def _validate_signed_exact(value: Any, keys: set[str], contract: str) -> bool:
    if (not isinstance(value, dict) or set(value) != keys
            or value.get("contract") != contract
            or not _valid_fingerprint(value.get("receipt_fingerprint"))):
        return False
    unsigned = {
        key: copy.deepcopy(item) for key, item in value.items()
        if key != "receipt_fingerprint"
    }
    try:
        return value["receipt_fingerprint"] == _fingerprint(unsigned)
    except (TypeError, ValueError, OverflowError, RecursionError):
        return False


def build_automatic_coverage_receipt(
    *, basis_fingerprint: str, items: list[dict[str, Any]],
    unresolved: list[dict[str, Any]] | None = None,
    source_fingerprints: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build the small receipt an execution adapter can attach to a result.

    The caller remains responsible for deriving the item inventory from the
    exact effective graph; this helper only freezes the bounded transport
    contract used by final acceptance.
    """

    if not _valid_fingerprint(basis_fingerprint):
        raise ValueError("自动覆盖回执缺少有效基线指纹")
    pending = copy.deepcopy(unresolved or [])
    unsigned = {
        "contract": AUTOMATIC_RECEIPT_CONTRACT,
        "scope": AUTOMATIC_SCOPE,
        "status": "partial" if pending else "verified",
        "basis_fingerprint": basis_fingerprint,
        "source_fingerprints": copy.deepcopy(source_fingerprints or {}),
        "items": copy.deepcopy(items),
        "unresolved": pending,
    }
    return {**unsigned, "receipt_fingerprint": _fingerprint(unsigned)}


def _derive_automatic_receipt(
    calculated: dict[str, Any], *, topology_receipt: Any,
    topology_binding_receipt: Any, energy_intent: Any, require_auxiliary_topology: bool = True,
) -> dict[str, Any]:
    """Aggregate only generic dimensions proven for this declared graph.

    Character-specific event completeness is intentionally outside this
    scope; it remains the Agent's event-topology audit responsibility.
    """

    result = calculated.get("result") if isinstance(calculated.get("result"), dict) else {}
    rotation = result.get("rotation_check") if isinstance(result.get("rotation_check"), dict) else {}
    interpretation = (rotation.get("damage_interpretation")
                      if isinstance(rotation.get("damage_interpretation"), dict) else {})
    mechanics = result.get("mechanics") if isinstance(result.get("mechanics"), dict) else {}
    evidence = {
        "topology": copy.deepcopy(topology_receipt),
        "topology_binding": copy.deepcopy(topology_binding_receipt),
        "action_check": copy.deepcopy(rotation.get("action_check")),
        "energy_check": copy.deepcopy(rotation.get("energy_check")),
        "rotation_status": rotation.get("status"),
        "damage_interpretation": copy.deepcopy(interpretation),
        "mechanics": {
            "status": result.get("mechanics_status"),
            "execution_status": mechanics.get("execution_status"),
            "complete_first_cycle": mechanics.get("complete_first_cycle"),
        },
        "action_materialization": copy.deepcopy(
            result.get("action_materialization_receipt")),
        "energy_intent": energy_intent,
    }
    source_fingerprints = {
        key: _fingerprint(value) for key, value in evidence.items()
    }
    if result.get('damage_report_window') is not None:
        source_fingerprints['damage_report_window'] = _fingerprint(result['damage_report_window'])
    items: list[dict[str, str]] = []
    unresolved: list[dict[str, str]] = []

    def verified(dimension: str) -> None:
        items.append({
            "coverage_key": f"generic:{dimension}",
            "owner_id": f"generic_verifier:{dimension}",
            "status": "automatic_verified",
        })

    def missing(dimension: str, impact: str = "high") -> None:
        unresolved.append({
            "coverage_key": f"generic:{dimension}",
            "impact": impact,
            "status": "unresolved",
        })

    topology = topology_receipt if isinstance(topology_receipt, dict) else {}
    if not require_auxiliary_topology:
        topology = {}
    kinds = topology.get("active_node_kinds")
    counts = topology.get("active_node_kind_counts")
    if (topology.get("status") == "numeric_ready"
            and isinstance(kinds, list) and isinstance(counts, dict)
            and all(isinstance(kind, str) for kind in kinds)
            and kinds == sorted(set(kinds)) and set(kinds) == set(counts)
            and all(type(value) is int and value > 0 for value in counts.values())):
        verified("declared_topology_shape")
    else:
        if require_auxiliary_topology:
            missing("declared_topology_shape")
        kinds = []
    binding = topology_binding_receipt if isinstance(topology_binding_receipt, dict) else {}
    if require_auxiliary_topology:
        (verified if binding.get("status") == "complete" else missing)("effective_graph_bindings")
    action = rotation.get("action_check")
    from genshin_app.calculation_completion import calculation_completion
    completion = calculation_completion(calculated)
    scoped = result.get('damage_report_window', {}).get('contract') == 'damage-report-window-v1'
    (verified if scoped and completion['required_validation'] == 'complete' or not scoped and isinstance(action, dict) and action.get("status") == "passed" else missing)(
        "generic_action_validation")
    complete_window = completion['complete_input_window'] if scoped else interpretation.get("complete_first_cycle")
    if not scoped and complete_window is not True:
        complete_window = mechanics.get("complete_first_cycle")
    (verified if complete_window is True else missing)("complete_input_window")

    energy = rotation.get("energy_check") if isinstance(rotation.get("energy_check"), dict) else {}
    if energy_intent in {"single_window_legal", "multi_round_legal"}:
        (verified if scoped and completion['required_validation'] == 'complete' or not scoped and energy.get("opening_status") == "passed" else missing)(
            "explicit_window_energy")
    elif energy_intent == "sustainable_loop":
        sustainable = (completion['steady_state'] == 'proven' and energy.get("status") == "passed"
                       and energy.get("proven_cycle") is not None
                       and rotation.get("status") == "passed")
        (verified if sustainable else missing)("sustainable_energy")
    else:
        missing("energy_policy")

    if "reaction_candidate" in kinds:
        reaction = interpretation.get("reaction_verification")
        (verified if isinstance(reaction, dict)
         and reaction.get("status") in {"passed", "verified"} else missing)(
             "elemental_reaction_runtime")
    if "application" in kinds:
        application = interpretation.get("application_verification")
        coverage = application.get("coverage_status", application.get("coverage")) \
            if isinstance(application, dict) else None
        if isinstance(coverage, dict):
            coverage = coverage.get("status")
        passed = (isinstance(application, dict)
                  and application.get("status") in {"passed", "verified"}
                  and coverage in {
                      "complete", "complete_input_window", "covered", "full",
                      "passed", "verified",
                  })
        (verified if passed else missing)("elemental_application_runtime")

    mechanics_status = result.get("mechanics_status")
    execution_status = mechanics.get("execution_status")
    if scoped and completion['required_validation'] == 'complete' or (mechanics_status in {"passed", "ready"}
            and execution_status not in {
                "failed", "pending", "invalid", "rejected", "incomplete",
            }):
        verified('generic_mechanics_execution')
    elif (mechanics_status not in {"failed", "pending", "invalid", "rejected", "incomplete"}
          and execution_status not in {
              "failed", "pending", "invalid", "rejected", "incomplete",
          }):
        # The engine has not asserted failure, but cannot claim verification.
        missing("generic_mechanics_execution", "medium")
    else:
        missing("generic_mechanics_execution")

    materialization = result.get("action_materialization_receipt")
    materialization_status = (materialization.get("status")
                              if isinstance(materialization, dict) else None)
    if materialization_status in {"complete", "not_applicable"}:
        verified("automatic_action_materialization")
    elif materialization_status in {None, "unknown"}:
        # Character dispatch is covered by the Agent-declared topology, so an
        # unknown automatic materializer is visible but not a generic blocker.
        missing("automatic_action_materialization", "medium")
    else:
        missing("automatic_action_materialization")

    return build_automatic_coverage_receipt(
        basis_fingerprint=_fingerprint(evidence),
        source_fingerprints=source_fingerprints,
        items=items,
        unresolved=unresolved,
    )


def _automatic_receipt(
    value: Any, expected: dict[str, Any]
) -> tuple[int, int, bool, list[dict[str, Any]], dict[str, Any]]:
    """Validate or materialize the generic-dimension coverage receipt."""

    if value is None:
        value = expected
    keys = {
        "contract", "scope", "status", "basis_fingerprint",
        "source_fingerprints", "items", "unresolved", "receipt_fingerprint",
    }
    if (not _validate_signed_exact(value, keys, AUTOMATIC_RECEIPT_CONTRACT)
            or value.get("scope") != AUTOMATIC_SCOPE
            or value.get("status") not in {"verified", "partial"}
            or not isinstance(value.get("source_fingerprints"), dict)
            or not isinstance(value.get("items"), list)
            or not isinstance(value.get("unresolved"), list)):
        return 0, 1, False, [_issue(
            "automatic_coverage_receipt_invalid",
            "自动覆盖回执合同、字段或指纹无效",
        )], copy.deepcopy(value) if isinstance(value, dict) else {}
    if (value.get("basis_fingerprint") != expected.get("basis_fingerprint")
            or value.get("source_fingerprints") != expected.get("source_fingerprints")
            or value != expected):
        return 0, 1, False, [_issue(
            "automatic_coverage_receipt_stale",
            "自动覆盖回执未绑定当前声明图及本次通用验证来源",
        )], copy.deepcopy(value)

    issues: list[dict[str, Any]] = []
    coverage_keys: set[str] = set()
    verified = 0
    for row in value["items"]:
        if (not isinstance(row, dict)
                or set(row) != {"coverage_key", "owner_id", "status"}
                or row.get("status") != "automatic_verified"
                or not all(isinstance(row.get(key), str) and row[key]
                           for key in ("coverage_key", "owner_id"))):
            issues.append(_issue(
                "automatic_coverage_item_invalid",
                "自动覆盖成员缺少稳定范围、唯一所有者或已验证状态",
            ))
            continue
        if row["coverage_key"] in coverage_keys:
            issues.append(_issue(
                "automatic_coverage_owner_conflict",
                "同一自动覆盖范围出现多个所有权声明",
            ))
            continue
        coverage_keys.add(row["coverage_key"])
        verified += 1

    unresolved = 0
    for row in value["unresolved"]:
        if (not isinstance(row, dict)
                or set(row) != {"coverage_key", "impact", "status"}
                or row.get("status") != "unresolved"
                or row.get("impact") not in {
                    "informational", "low", "medium", "high", "critical",
                }
                or not isinstance(row.get("coverage_key"), str)
                or not row["coverage_key"]):
            issues.append(_issue(
                "automatic_coverage_unresolved_invalid",
                "自动覆盖未解决成员格式无效",
            ))
            unresolved += 1
            continue
        unresolved += 1
        high = row["impact"] in _HIGH_IMPACTS
        issues.append(_issue(
            "automatic_coverage_high_impact_unresolved"
            if high else "automatic_coverage_unresolved",
            "自动计算仍有未解决的高影响范围"
            if high else "自动计算仍有未解决范围，未计为已验证",
            blocking=high,
        ))
    if value["status"] == "verified" and unresolved:
        issues.append(_issue(
            "automatic_coverage_status_conflict",
            "自动覆盖回执声称完整，但同时保留未解决范围",
        ))
    complete = value["status"] == "verified" and not unresolved and not issues
    return verified, unresolved, complete, issues, copy.deepcopy(value)


def _override_receipt(
    value: Any, declarations: Any, report: dict[str, Any]
) -> tuple[int, list[dict[str, Any]]]:
    items = declarations.get("items") if isinstance(declarations, dict) else None
    if declarations is None:
        if value is not None:
            return 0, [_issue(
                "unexpected_calculation_override_receipt",
                "结果包含未由当前方案声明的覆盖回执",
            )]
        return 0, []
    if not isinstance(items, list) or not items:
        return 0, [_issue(
            "calculation_override_declaration_invalid",
            "当前方案的覆盖声明没有可核验成员",
        )]
    try:
        from genshin_app.equipment.calculation_overrides.receipt import (
            validate_execution_receipt,
        )

        validate_execution_receipt(value)
        if value.get("status") != "applied" or value.get("issues"):
            raise ValueError("override not fully applied")

        basis = report.get("calculation_override_basis")
        effective = report.get("effective_execution_fingerprint")
        if (not isinstance(basis, dict)
                or value.get("basis_fingerprint") != _fingerprint(basis)
                or not _valid_fingerprint(effective)
                or value.get("effective_execution_fingerprint") != effective):
            raise ValueError("override receipt is stale")

        declaration_ids = [row.get("override_id") for row in items]
        declaration_coverage = [row.get("coverage_key") for row in items]
        if (any(not isinstance(item, str) or not item for item in declaration_ids)
                or any(not isinstance(item, str) or not item
                       for item in declaration_coverage)
                or len(set(declaration_ids)) != len(declaration_ids)
                or len(set(declaration_coverage)) != len(declaration_coverage)):
            raise ValueError("override declaration identity invalid")
        expected_by_id = {
            row["override_id"]: (row["coverage_key"], row.get("mode"))
            for row in items
        }

        coverage_rows = value.get("coverage")
        applied_rows = value.get("applied")
        if not isinstance(coverage_rows, list) or not isinstance(applied_rows, list):
            raise TypeError("override coverage missing")
        coverage_by_id: dict[str, tuple[Any, Any]] = {}
        for row in coverage_rows:
            if (not isinstance(row, dict)
                    or row.get("status") != "declaration_ready"
                    or not isinstance(row.get("override_id"), str)
                    or row["override_id"] in coverage_by_id):
                raise ValueError("override coverage pending or duplicate")
            coverage_by_id[row["override_id"]] = (
                row.get("coverage_key"), row.get("mode"),
            )
        applied_by_id: dict[str, tuple[Any, Any]] = {}
        for row in applied_rows:
            if (not isinstance(row, dict)
                    or not isinstance(row.get("override_id"), str)
                    or row["override_id"] in applied_by_id):
                raise ValueError("override application duplicate")
            applied_by_id[row["override_id"]] = (
                row.get("coverage_key"), row.get("mode"),
            )
        if coverage_by_id != expected_by_id or applied_by_id != expected_by_id:
            raise ValueError("override coverage mismatch")

        installed = value.get("installed_owner_ids")
        released = value.get("released_owner_ids")
        expected_owners = {f"override:{item}" for item in declaration_ids}
        if (not isinstance(installed, list) or set(installed) != expected_owners
                or len(installed) != len(set(installed))
                or not isinstance(released, list)
                or len(released) != len(set(released))):
            raise ValueError("override ownership is not unique")
        return len(applied_by_id), []
    except (TypeError, ValueError, KeyError):
        return 0, [_issue(
            "calculation_override_unresolved",
            "覆盖声明没有新鲜、唯一所有权且已完整应用的有效执行回执",
        )]


def _ownership_proof(
    result: dict[str, Any], receipt: dict[str, Any], report: dict[str, Any],
) -> list[dict[str, Any]]:
    """Require the App-generated cross-lane one-owner proof."""

    try:
        from genshin_app.equipment.calculation_overrides.ownership_receipt import (
            validate_ownership_result_proof,
        )

        proof = validate_ownership_result_proof(result, receipt, report)
        if proof is None:
            raise ValueError("ownership proof missing")
        return []
    except (TypeError, ValueError, KeyError):
        return [_issue(
            "calculation_ownership_proof_invalid",
            "材料化、外部补充与覆盖通道缺少当前有效图的一对一所有权证明",
        )]


def _provisional_receipt(
    value: Any, trusted_value: Any, plan: dict[str, Any], *,
    candidate_revision: Any, stage_revision: Any,
    database_snapshot_fingerprint: Any,
) -> tuple[dict[str, int], list[dict[str, Any]], dict[str, Any] | None]:
    empty = {"total": 0, "database_missing": 0, "public_data_patch": 0}
    usages = set(declared_provisional_usages(plan))
    if not usages:
        if value is not None or trusted_value is not None:
            return empty, [_issue(
                "unexpected_provisional_entity_receipt",
                "结果包含当前有效图未消费的临时实体回执",
            )], None
        return empty, [], None

    # The JSON carried by the calculated document is an audit projection, not
    # an authority.  Formal acceptance requires the exact out-of-band receipt
    # retained by the App execution/session boundary.
    if not isinstance(value, dict) or not isinstance(trusted_value, Mapping):
        return empty, [_issue(
            "provisional_entity_usage_receipt_untrusted",
            "当前有效图使用临时公共实体，但没有可核验的App实际使用回执",
        )], None
    if dict(trusted_value) != value:
        return empty, [_issue(
            "provisional_entity_usage_receipt_untrusted",
            "结果中的临时实体回执与App保留的受信回执不一致",
        )], None
    if (type(candidate_revision) is not int or candidate_revision < 0
            or type(stage_revision) is not int or stage_revision < 0
            or not _valid_fingerprint(database_snapshot_fingerprint)):
        return empty, [_issue(
            "provisional_entity_usage_context_untrusted",
            "当前计算没有提供可核验的候选修订、阶段修订和数据库快照上下文",
        )], None
    try:
        checked = validate_usage_receipt(
            value, plan,
            candidate_revision=candidate_revision,
            stage_revision=stage_revision,
            database_snapshot_fingerprint=database_snapshot_fingerprint,
        )
    except (ProvisionalEntityError, TypeError, ValueError, KeyError) as exc:
        code = (exc.reason if isinstance(exc, ProvisionalEntityError)
                else "provisional_entity_usage_receipt_invalid")
        return empty, [_issue(code, str(exc) or "临时实体使用回执无效")], None
    counts = {
        "total": len(checked["entities"]),
        "database_missing": sum(
            row["authority"] == "agent_reported_database_missing"
            for row in checked["entities"]
        ),
        "public_data_patch": sum(
            row["authority"] == "user_confirmed_public_data_patch"
            for row in checked["entities"]
        ),
    }
    return counts, [], checked


def _result_fidelity(
    sources: list[dict[str, Any]], *, provisional_total: int,
) -> list[dict[str, Any]]:
    """Verify that the published result class matches actual D2 consumption.

    Legacy calculations that do not consume provisional public entities may
    omit this newer projection. Once any publisher emits it, all three formal
    projections must agree. A result that actually consumes D2 input is never
    allowed to omit or downgrade the approximate label.
    """

    expected = (
        PROVISIONAL_RESULT_FIDELITY
        if provisional_total else FORMAL_RESULT_FIDELITY
    )
    values = [source.get("result_fidelity") for source in sources]
    present = [value is not None for value in values]
    if not provisional_total and not any(present):
        return []
    if not all(present) or any(value != expected for value in values):
        return [_issue(
            "calculation_result_fidelity_invalid",
            (
                "临时公共资料已进入当前有效图，但结果、报告与回执没有一致标注为近似"
                if provisional_total else
                "结果、报告与回执发布的结果性质不一致"
            ),
        )]
    return []


def _external_fixed_value(
    plan: dict[str, Any], result: dict[str, Any], report: dict[str, Any],
    receipt: dict[str, Any],
) -> tuple[int, list[dict[str, Any]]]:
    del report  # result/receipt proof already binds the accepted source basis.
    external = result.get("external_damage")
    declarations = plan.get("external_damage_supplements")
    if external is None and not declarations:
        return 0, []
    try:
        from genshin_app.equipment.external_damage import (
            validate_external_damage_result_receipt,
        )

        validate_external_damage_result_receipt(result, receipt)
        items = external.get("items") if isinstance(external, dict) else None
        if not isinstance(items, list):
            raise TypeError("external damage items missing")
        if isinstance(declarations, list) and len(items) != len(declarations):
            raise ValueError("external damage coverage mismatch")
        return len(items), []
    except (TypeError, ValueError, KeyError):
        return 0, [_issue(
            "external_fixed_value_receipt_invalid",
            "外部固定值缺少与当前结果一致的受审补充回执",
        )]


def _agent_execution_materialization(
    plan: dict[str, Any], result: dict[str, Any], receipt: dict[str, Any],
) -> tuple[int, list[dict[str, Any]]]:
    from genshin_app.equipment.agent_execution_receipt import (
        DOCUMENT_FIELD,
        EFFECTIVE_INPUT_FIELD,
        MATERIALIZATION_FIELD,
        PROOF_FIELD,
        RUNTIME_RECEIPT_FIELD,
        validate_agent_execution_result_receipt,
    )

    document = (plan.get("extensions") or {}).get(
        "agent_execution_materialization")
    signals = (
        document,
        result.get(DOCUMENT_FIELD),
        result.get(MATERIALIZATION_FIELD),
        result.get(EFFECTIVE_INPUT_FIELD),
        result.get(RUNTIME_RECEIPT_FIELD),
        receipt.get(PROOF_FIELD),
    )
    if all(value is None for value in signals):
        return 0, []
    try:
        if not isinstance(document, dict):
            raise TypeError("正式结果缺少对应的Agent材料化文档")
        validate_agent_execution_result_receipt(result, receipt)
        proof = receipt[PROOF_FIELD]
        if proof.get("materialization_document_fingerprint") != _fingerprint(document):
            raise ValueError("Agent执行证明不属于当前方案材料化文档")
        count = proof.get("installed_entry_count")
        if type(count) is not int or count < 1:
            raise ValueError("Agent执行证明没有已安装条目")
        return count, []
    except (TypeError, ValueError, KeyError):
        return 0, [_issue(
            "agent_execution_proof_invalid",
            "Agent材料化执行缺少与当前方案、有效输入及运行时安装一致的正式证明",
        )]


def _task_capability_blocking(
    report: dict[str, Any], result: dict[str, Any], receipt: dict[str, Any],
    *, override_receipt: Any, override_valid: bool,
    materialization_valid: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Consume the Composer's fresh task-scoped capability decision.

    ``missing_inputs`` intentionally remains an immutable diagnostic ledger.
    A verified materialization or override can resolve one exact consumer
    without deleting that historical row.  Final qualification therefore
    validates the Composer projection and its source proof instead of
    reclassifying the pre-repair diagnostics from scratch.
    """

    raw_issues = report.get("missing_inputs", [])
    capability = report.get("task_capability")
    if capability is None:
        return blocking_task_issues(raw_issues), []
    try:
        if not isinstance(raw_issues, list) or not isinstance(capability, dict):
            raise TypeError("任务能力回执或缺项列表不是对象")
        expected_keys = {
            "contract", "status", "issues", "unresolved", "resolved",
            "blocking", "degraded", "derivation", "counts",
        }
        if (
            set(capability) != expected_keys
            or capability.get("contract") != TASK_CAPABILITY_CONTRACT
        ):
            raise ValueError("任务能力回执字段或合同无效")
        for key in ("issues", "unresolved", "resolved", "blocking", "degraded"):
            if not isinstance(capability.get(key), list):
                raise TypeError("任务能力回执列表字段无效")

        canonical = [normalize_task_issue(row) for row in raw_issues]
        published = capability["issues"]
        if len(published) != len(canonical):
            raise ValueError("任务能力回执没有覆盖当前全部缺项")
        immutable_keys = (
            "issue_id", "code", "path", "message", "category", "consumer",
            "blocking_scope", "retryable", "repair_hint",
        )
        for actual, expected in zip(published, canonical, strict=True):
            if not isinstance(actual, dict) or any(
                actual.get(key) != expected.get(key) for key in immutable_keys
            ):
                raise ValueError("任务能力回执与当前缺项身份不一致")
            if (
                type(actual.get("required")) is not bool
                or type(actual.get("consumed")) is not bool
                or actual.get("impact")
                not in {"informational", "low", "medium", "high", "critical"}
            ):
                raise ValueError("任务能力消费状态无效")

        derivation = capability.get("derivation")
        if (
            not isinstance(derivation, dict)
            or set(derivation) != {
                "consumer_authority", "effective_graph_fingerprint",
                "resolution_authority",
            }
            or derivation.get("consumer_authority")
            not in {"effective_graph", "producer_or_legacy"}
            or derivation.get("resolution_authority")
            != "app_verified_receipts_only"
        ):
            raise ValueError("任务能力派生来源无效")
        plan = report.get("plan") if isinstance(report.get("plan"), dict) else {}
        # Composer classifies task capability against the effective input graph
        # before the mechanics executor mutates ``resolved_template`` into its
        # post-execution projection.  The service deliberately preserves that
        # exact pre-execution template as ``mechanics_input_template``.  Using
        # the later projection here makes a valid App-issued capability receipt
        # look stale as soon as a materialized mechanics rule executes.
        graph_fingerprint = _fingerprint({
            "template": report.get(
                "mechanics_input_template", plan.get("resolved_template")
            ),
            "rotation_model": report.get(
                "compiled_rotation_model", plan.get("rotation_model")
            ),
            "mechanics_model": report.get("mechanics_model"),
            "mechanics_matches": report.get("mechanics_matches"),
        })
        published_graph = derivation.get("effective_graph_fingerprint")
        if published_graph is not None and published_graph != graph_fingerprint:
            raise ValueError("任务能力回执对应的有效图已经过期")

        published_by_id: dict[str, dict[str, Any]] = {}
        for row in published:
            issue_id = row["issue_id"]
            if issue_id in published_by_id:
                raise ValueError("任务能力缺项身份重复")
            published_by_id[issue_id] = row

        resolved_base: list[dict[str, Any]] = []
        resolved_ids: set[str] = set()
        materialization_proof = receipt.get("agent_execution_proof")
        materialization_keys: set[str] = set()
        materialization_source = None
        if materialization_valid and isinstance(materialization_proof, dict):
            materialization_keys = set(
                materialization_proof.get("installed_coverage_keys", [])
            )
            materialization_source = materialization_proof.get("proof_fingerprint")
        override_sources: dict[tuple[str, str], str] = {}
        if override_valid and isinstance(override_receipt, dict):
            source = override_receipt.get("receipt_fingerprint")
            for row in override_receipt.get("coverage", []):
                if isinstance(row, dict):
                    override_sources[(
                        str(row.get("coverage_key")),
                        "override:" + str(row.get("override_id")),
                    )] = source

        for row in capability["resolved"]:
            if not isinstance(row, dict) or not isinstance(row.get("resolution"), dict):
                raise TypeError("任务能力已解决成员缺少受信回执")
            issue_id = row.get("issue_id")
            if issue_id in resolved_ids or issue_id not in published_by_id:
                raise ValueError("任务能力已解决成员身份无效")
            resolved_ids.add(issue_id)
            base = copy.deepcopy(row)
            resolution = base.pop("resolution")
            if base != published_by_id[issue_id]:
                raise ValueError("任务能力已解决成员与当前缺项不一致")
            if set(resolution) != {
                "authority", "resolved_by", "proof_fingerprint", "superseded_by",
            }:
                raise ValueError("任务能力修复证明字段无效")
            authority = resolution.get("authority")
            resolved_by = resolution.get("resolved_by")
            source_proof = None
            if authority == "verified_materialization_receipt":
                if (
                    base["consumer"] not in materialization_keys
                    or resolved_by != "materialization:" + base["consumer"]
                ):
                    raise ValueError("材料化修复没有拥有当前缺项范围")
                source_proof = materialization_source
            elif authority == "verified_override_receipt":
                source_proof = override_sources.get((base["consumer"], resolved_by))
            else:
                raise ValueError("当前资格层不支持该任务能力修复权威")
            if not _valid_fingerprint(source_proof):
                raise ValueError("任务能力修复缺少已验证来源证明")
            expected_proof = _fingerprint({
                "authority": authority,
                "coverage_key": base["consumer"],
                "source_proof_fingerprint": source_proof,
                "effective_graph_fingerprint": graph_fingerprint,
            })
            if resolution.get("proof_fingerprint") != expected_proof:
                raise ValueError("任务能力修复证明与当前有效图不一致")
            if resolution.get("superseded_by") is not None and (
                not isinstance(resolution["superseded_by"], str)
                or not resolution["superseded_by"]
            ):
                raise ValueError("任务能力替代消费者无效")
            resolved_base.append(base)

        unresolved = [row for row in published if row["issue_id"] not in resolved_ids]
        blocking = [row for row in unresolved if row["required"] and row["consumed"]]
        degraded = [row for row in unresolved if row not in blocking]
        if (
            capability["unresolved"] != unresolved
            or capability["blocking"] != blocking
            or capability["degraded"] != degraded
            or [
                {key: value for key, value in row.items() if key != "resolution"}
                for row in capability["resolved"]
            ] != resolved_base
        ):
            raise ValueError("任务能力分组与当前缺项不一致")
        expected_status = "blocked" if blocking else (
            "degraded" if degraded else "ready"
        )
        if (
            capability.get("status") != expected_status
            or capability.get("counts") != {
                "total": len(published),
                "blocking": len(blocking),
                "degraded": len(degraded),
            }
        ):
            raise ValueError("任务能力状态或计数不一致")
        return copy.deepcopy(blocking), []
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError):
        return [], [_issue(
            "task_capability_receipt_invalid",
            "当前缺项的任务能力回执无效、过期或无法由正式执行证明重建",
        )]


def qualify_calculation_coverage(
    calculated: Any, *, topology_receipt: Any = None,
    topology_binding_receipt: Any = None, energy_intent: Any = None,
    trusted_provisional_usage_receipt: Any = None,
    candidate_revision: Any = None, stage_revision: Any = None,
    database_snapshot_fingerprint: Any = None, require_auxiliary_topology: bool = True,
    _readonly_provisional_context: Any = None,
) -> dict[str, Any]:
    if not isinstance(calculated, dict):
        issue = _issue("coverage_calculation_missing", "没有可核验的计算覆盖范围")
        return {
            "contract": CONTRACT, "scope": AUTOMATIC_SCOPE,
            "status": "unresolved", "complete": False,
            "automatic_verified": 0, "agent_materialized": 0,
            "agent_overridden": 0,
            "user_confirmed_provisional": 0,
            "agent_reported_missing_entity": 0,
            "user_confirmed_public_data_patch": 0,
            "provisional_entity_total": 0,
            "provisional_entity_usage_receipt": None,
            "result_fidelity": "not_checked",
            "external_fixed_value": 0,
            "unresolved": 1, "automatic_coverage_receipt": None,
            "issues": [issue], "blocking_issues": [issue],
        }
    plan = calculated.get("plan") if isinstance(calculated.get("plan"), dict) else {}
    result = calculated.get("result") if isinstance(calculated.get("result"), dict) else {}
    report = calculated.get("report") if isinstance(calculated.get("report"), dict) else {}
    receipt = calculated.get("result_receipt") if isinstance(calculated.get("result_receipt"), dict) else {}
    sources = [result, report, receipt]
    issues: list[dict[str, Any]] = []
    from genshin_app.calculation_completion import (
        advisory_execution_completion,
        calculation_completion,
    )
    readonly_evidence = None
    if _readonly_provisional_context is not None:
        from genshin_app.equipment.provisional_readonly import (
            ReadOnlyProvisionalProjection,
        )
        if type(_readonly_provisional_context) is not ReadOnlyProvisionalProjection:
            from genshin_app.equipment.schema import PlanError
            raise PlanError("只读执行核验缺少App真实上下文", "energy_trial_result_untrusted")
        readonly_evidence = _readonly_provisional_context.validate_fresh_result(calculated)
        completion = advisory_execution_completion(calculated)
    else:
        completion = calculation_completion(calculated)
    if completion.get('execution_admission', completion['task_admission']) != 'accepted':
        issues.append(_issue('calculation_incomplete', '当前数值或实际执行未完成；真实诊断前缀不能代替完整执行。'))
    issues.extend(_ownership_proof(result, receipt, report))

    expected_automatic = _derive_automatic_receipt(
        calculated,
        topology_receipt=topology_receipt,
        topology_binding_receipt=topology_binding_receipt,
        energy_intent=energy_intent, require_auxiliary_topology=require_auxiliary_topology,
    )
    (automatic_verified, automatic_unresolved, automatic_complete, rows,
     automatic_receipt) = _automatic_receipt(
        _find(sources, ("automatic_coverage_receipt",)), expected_automatic,
    )
    issues.extend(rows)
    override_receipt = _find(sources, (
        "calculation_override_receipt", "calculation_event_overrides_receipt",
    ))
    agent_overridden, rows = _override_receipt(
        override_receipt,
        plan.get("calculation_event_overrides"), report,
    )
    override_valid = agent_overridden > 0 and not rows
    issues.extend(rows)
    if readonly_evidence is not None:
        uses = readonly_evidence["uses"]
        provisional_counts = {"total": len(uses),
            "database_missing": sum(row["authority"] == "agent_reported_database_missing" for row in uses),
            "public_data_patch": sum(row["authority"] == "user_confirmed_public_data_patch" for row in uses)}
        rows, provisional_receipt = [], None
    else:
        provisional_counts, rows, provisional_receipt = _provisional_receipt(
            _find(sources, ("provisional_entity_usage_receipt",)),
            trusted_provisional_usage_receipt, plan,
            candidate_revision=candidate_revision, stage_revision=stage_revision,
            database_snapshot_fingerprint=database_snapshot_fingerprint,
        )
    issues.extend(rows)
    issues.extend(_result_fidelity(
        sources, provisional_total=provisional_counts["total"],
    ))
    external_count, rows = _external_fixed_value(plan, result, report, receipt)
    issues.extend(rows)
    agent_materialized, rows = _agent_execution_materialization(
        plan, result, receipt)
    materialization_valid = agent_materialized > 0 and not rows
    issues.extend(rows)

    blocking, rows = _task_capability_blocking(
        report, result, receipt,
        override_receipt=override_receipt,
        override_valid=override_valid,
        materialization_valid=materialization_valid,
    )
    issues.extend(rows)
    for row in blocking:
        issues.append(_issue(row["code"], row["message"]))
    blocking_issues = [row for row in issues if row["blocking"]]
    unresolved = automatic_unresolved + sum(
        not row["code"].startswith("automatic_coverage_")
        for row in blocking_issues
    )
    status = "unresolved" if blocking_issues else (
        "complete" if automatic_complete else "partial"
    )
    return {
        "contract": CONTRACT,
        "scope": AUTOMATIC_SCOPE,
        "status": status,
        "complete": status == "complete",
        "automatic_verified": automatic_verified,
        "agent_materialized": agent_materialized,
        "agent_overridden": agent_overridden,
        # Retain the legacy field as the count that truly required user
        # confirmation. Database-missing Agent fallbacks are reported
        # separately and never mislabeled as user-approved.
        "user_confirmed_provisional": provisional_counts["public_data_patch"],
        "agent_reported_missing_entity": provisional_counts["database_missing"],
        "user_confirmed_public_data_patch": provisional_counts["public_data_patch"],
        "provisional_entity_total": provisional_counts["total"],
        "provisional_entity_usage_receipt": provisional_receipt,
        **({"readonly_public_fact_evidence": readonly_evidence, "formal_result_authority": False}
           if readonly_evidence is not None else {}),
        "result_fidelity": (
            "approximate_with_provisional_entities"
            if provisional_counts["total"] else "formal_without_provisional_entities"
        ),
        "external_fixed_value": external_count,
        "unresolved": unresolved,
        "automatic_coverage_receipt": automatic_receipt,
        "issues": copy.deepcopy(issues),
        "blocking_issues": copy.deepcopy(blocking_issues),
    }
