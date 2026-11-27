"""Saved result binding, shared by result-only and full-calculation readers.

This checks saved consistency, never game truth or current database authority.
It does not repair a receipt or select a weaker scope when verification fails.
"""
from __future__ import annotations

import re

from genshin_app.verification_scope import verification_scope

_SHA = re.compile(r"[0-9a-f]{64}\Z")
_FORMAL_FIELDS = frozenset({
    "calculation_ownership_proof", "agent_execution_materialization_document", "agent_execution_proof",
    "agent_execution_materialization_receipt", "agent_execution_effective_input",
    "agent_execution_runtime_receipt", "attribute_execution_budget",
    "calculation_producer_contract",
    "damage_explanation_contract",
    "damage_report_window", "damage_report_execution",
    "special_damage_echoes", "special_damage_echo_execution",
    "special_capability_source",
})


@verification_scope
def validate_result_binding(result, receipt=None, *, formal_required=False, _report=None, _session=None):
    """Validate the original result, not an envelope that embeds its receipt.

    Legacy bare numeric snapshots remain exportable. A supplied binding must
    always match, including empty/None/incorrectly typed fingerprints. Native
    identity or formal proof signals make that binding mandatory.
    """
    # export is also imported by the document/GuiApi factory. Importing the
    # equipment package here, rather than at module initialization, prevents
    # its service from re-entering the still-initializing export module.
    from genshin_app.equipment.files import fingerprint
    if not isinstance(result, dict) or receipt is not None and not isinstance(receipt, dict):
        raise ValueError("计算结果或结果回执必须是对象。")
    bound = receipt or {}
    formal = (formal_required or bool(_FORMAL_FIELDS.intersection(result))
              or bool(_FORMAL_FIELDS.intersection(bound))
              or any(key in bound for key in ("plan_id", "input_fingerprint", "output_fingerprint")))
    # fingerprint performs the same finite, exact JSON encoding as producers;
    # notably 1 and 1.0 are not silently canonicalized into one representation.
    try:
        actual = fingerprint(result)
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ValueError("计算结果不是可核对的有限JSON。") from exc
    if formal or "result_fingerprint" in bound:
        saved = bound.get("result_fingerprint")
        if not isinstance(saved, str) or _SHA.fullmatch(saved) is None or saved != actual:
            raise ValueError("计算结果与结果回执指纹不一致，请重新计算。")
    from genshin_app.equipment.special_capability_authority import bind_saved_capability_catalog
    bind_saved_capability_catalog(result, bound, _session, result_fingerprint=actual, report=_report)
    from genshin_app.equipment.attribute_budget_binding import (
        validate_attribute_budget_binding,
    )
    validate_attribute_budget_binding(result, receipt)
    from genshin_app.equipment.energy_policy import validate_energy_policy_binding
    check = result.get('rotation_check')
    rotation = check.get('resolved_rotation_model', ...) if isinstance(check, dict) else ...
    validate_energy_policy_binding(result, receipt, rotation=rotation,
        require_current=isinstance(rotation, dict) and rotation.get('direct_energy_policy') == 'energy-direct-base-er-v1')
    from genshin_app.damage_explanation_reporting import validate_damage_explanations
    validate_damage_explanations(result, receipt, report=_report)
    from genshin_app.equipment.damage_report_window_receipt import (
        validate_window_result,
    )
    validate_window_result(result, bound, _session=_session)
    from genshin_app.equipment.special_damage_echo_receipt import validate_echo_result
    validate_echo_result(result, bound, _session=_session)
    from genshin_app.equipment.calculation_overrides.computed_values import (
        validate_computed_value_result_receipt,
    )
    validate_computed_value_result_receipt(result, bound, _session=_session)
    return actual


def validate_calculated_attribute_budget(plan, result, report, receipt):
    """Require the budget family promised by the actual producer revision.

    A missing new proof is not a legacy receipt. Versioned full-calculation
    context lets us distinguish that case even if every copy of the budget
    family and the compiled options was removed from the saved object.
    """
    from genshin_app.equipment.attribute_budget_binding import (
        validate_attribute_budget_binding,
    )
    authored = plan.get("mechanics_model") or {}
    compiled = report.get("mechanics_model") or {}
    declares = any(isinstance(model, dict) and model.get("attribute_model") is not None
                   for model in (authored, compiled))
    version = report.get("composer_version", "")
    pieces = version.split(".") if isinstance(version, str) else []
    current = (len(pieces) == 3 and all(piece.isascii() and piece.isdigit() for piece in pieces)
               and tuple(map(int, pieces)) >= (1, 32, 0))
    required = current or declares and isinstance(compiled, dict) and "execution_options" in compiled
    return validate_attribute_budget_binding(result, receipt, plan, require_current=required,
        execution_model=report.get('mechanics_model'))


def result_transport_parts(value, *, _session=None):
    """Decode the two published result-only JSON shapes without fallback.

    Full calculated packages must be recognized/validated by their full reader
    before reaching this helper. A root export attaches exactly one receipt to
    the original result; do not recursively strip similarly named fields.
    """
    if not isinstance(value, dict):
        raise ValueError("结果输送对象必须是对象。")  # noqa: TRY004 - invalid saved value, not Python call shape
    if any(key in value for key in ("calculated", "package_schema", "plan", "report", "display_context", "presentation_issues")):
        raise ValueError("完整计算包必须经过完整计算包读取器。")
    if "result" in value:
        if "summary" in value or "hits" in value:
            raise ValueError("结果输送同时包含嵌套与根结果，形状有歧义。")
        result, receipt = value.get("result"), value.get("result_receipt")
    elif "result_receipt" in value and "summary" in value and "hits" in value:
        result = {key: item for key, item in value.items() if key != "result_receipt"}
        receipt = value["result_receipt"]
    else:
        raise ValueError("不是带回执的受支持结果输送格式。")
    validate_result_binding(result, receipt, formal_required=True, _session=_session)
    return result, receipt
