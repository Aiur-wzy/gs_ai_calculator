"""Shared, bounded evidence for one concrete elemental application component."""
from __future__ import annotations

APPLICATION_MODES = {"enum": ["sequence", "no_icd", "no_application", "pending", "unknown"]}
VERIFICATION_FIELDS = {"type": "array", "minItems": 1, "maxItems": 5,
    "items": {"enum": ["application_mode", "counter_tag", "shared_scope", "attempt_gate",
                       "icd_rule"]}}
_SOURCE = {"type": "string", "minLength": 1, "maxLength": 2048}
_AUDIT_TEXT = {"type": "string", "minLength": 1, "maxLength": 2048}
_AUDIT_LIST = {"type": "array", "maxItems": 64, "items": _AUDIT_TEXT}
_SOURCE_CHECK = {"type": "object", "additionalProperties": False,
    "properties": {"locator": _SOURCE, "version": _AUDIT_TEXT,
        "accessed_at": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$", "maxLength": 10},
        "supported_fields": {"type": "array", "minItems": 1, "maxItems": 32,
                             "items": {"type": "string", "minLength": 1, "maxLength": 128}},
        "decision": {"enum": ["adopted", "rejected", "inconclusive"]}, "reason": _AUDIT_TEXT},
    "required": ["locator", "version", "accessed_at", "supported_fields", "decision", "reason"]}
_PATCH_ITEM = {"type": "object", "additionalProperties": False,
    "properties": {"target": _AUDIT_TEXT,
        "fields": {"type": "array", "minItems": 1, "maxItems": 32,
                   "items": {"type": "string", "minLength": 1, "maxLength": 128}},
        "evidence_locators": {"type": "array", "minItems": 1, "maxItems": 32, "items": _SOURCE},
        "status": {"enum": ["proposed", "applied", "deferred"]}, "note": _AUDIT_TEXT},
    "required": ["target", "fields", "evidence_locators", "status", "note"]}
RESEARCH_REPORT = {"type": "object", "additionalProperties": False,
    "properties": {"report_version": {"enum": [1]}, "execution_authority": {"enum": ["none"]},
        "actual_queries": _AUDIT_LIST,
        "sources_checked": {"type": "array", "maxItems": 32, "items": _SOURCE_CHECK},
        "adopted_findings": _AUDIT_LIST, "rejected_findings": _AUDIT_LIST,
        "remaining_unknowns": _AUDIT_LIST,
        "database_patch_plan": {"type": "array", "maxItems": 32, "items": _PATCH_ITEM}},
    "required": ["report_version", "execution_authority", "actual_queries", "sources_checked",
                 "adopted_findings", "rejected_findings", "remaining_unknowns", "database_patch_plan"]}
VERIFICATION = {"type": "object", "additionalProperties": False,
    "properties": {"status": {"enum": ["confirmed"]}, "locator": _SOURCE,
        "version": _SOURCE, "reviewed_at": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$", "maxLength": 10},
        "component_scope": _SOURCE, "confirmed_fields": VERIFICATION_FIELDS},
    "required": ["status", "locator", "version", "reviewed_at", "component_scope", "confirmed_fields"]}
_ADAPTER_ID = {"type": "string", "minLength": 1, "maxLength": 128,
               "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"}
_ADAPTER_HASH = {"type": "string", "pattern": r"^[0-9a-f]{64}$"}
_NULL = {"type": "null"}
ATTEMPT_GATE = {"type": "object", "additionalProperties": False,
    "properties": {
        "mode": {"enum": ["inherit", "none", "every_n_landed_hits"]},
        "period_hits": {"anyOf": [{"type": "integer", "minimum": 1, "maximum": 256}, _NULL]},
        "first_attempt_ordinal": {"anyOf": [
            {"type": "integer", "minimum": 1, "maximum": 256}, _NULL]},
    }, "required": ["mode", "period_hits", "first_attempt_ordinal"]}
ICD_RULE_ADAPTER = {"type": "object", "additionalProperties": False,
    "properties": {
        "mode": {"enum": ["inherit", "timer_counter"]},
        "accepted_base_rule_fingerprint": {"anyOf": [_ADAPTER_HASH, _NULL]},
        "hit_interval": {"anyOf": [
            {"type": "integer", "minimum": 1, "maximum": 64}, _NULL]},
        "reset_seconds": {"anyOf": [
            {"type": "number", "exclusiveMinimum": 0, "maximum": 10**9}, _NULL]},
    }, "required": ["mode", "accepted_base_rule_fingerprint", "hit_interval",
                     "reset_seconds"]}
APPLICATION_ADAPTER = {"type": "object", "additionalProperties": False,
    "properties": {
        "accepted_base_fingerprint": _ADAPTER_HASH,
        "mode": {"enum": ["inherit", "sequence", "no_icd", "no_application", "pending"]},
        "counter_tag": {"anyOf": [_ADAPTER_ID, _NULL]},
        "attempt_gate": ATTEMPT_GATE,
        "icd_rule": ICD_RULE_ADAPTER,
        "status": {"enum": ["confirmed", "pending"]},
        "source": _SOURCE,
        "verification": {"anyOf": [VERIFICATION, _NULL]},
    }, "required": ["accepted_base_fingerprint", "mode", "counter_tag", "attempt_gate",
                     "status", "source", "verification"]}
DATABASE_ICD_RULE = {"type": "object", "additionalProperties": False,
    "properties": {
        "status": {"enum": ["ready"]},
        "rule_group": _ADAPTER_ID,
        "reset_seconds": {"type": "number", "exclusiveMinimum": 0, "maximum": 10**9},
        "sequence": {"type": "array", "minItems": 1, "maxItems": 64,
                     "items": {"type": "integer", "enum": [0, 1]}},
        "source_fingerprint": _ADAPTER_HASH,
    }, "required": ["status", "rule_group", "reset_seconds", "sequence",
                     "source_fingerprint"]}
EFFECTIVE_ICD_RULE = {"type": "object", "additionalProperties": False,
    "properties": {
        "kind": {"enum": ["sequence", "timer_counter"]},
        "rule_group": _ADAPTER_ID,
        "reset_seconds": {"type": "number", "exclusiveMinimum": 0, "maximum": 10**9},
        "sequence": {"anyOf": [
            {"type": "array", "minItems": 1, "maxItems": 64,
             "items": {"type": "integer", "enum": [0, 1]}}, _NULL]},
        "hit_interval": {"anyOf": [
            {"type": "integer", "minimum": 1, "maximum": 64}, _NULL]},
        "first_hit": {"anyOf": [
            {"type": "integer", "minimum": 1, "maximum": 64}, _NULL]},
    }, "required": ["kind", "rule_group", "reset_seconds", "sequence",
                     "hit_interval", "first_hit"]}
APPLICATION_RULE_GATE = {"type": "object", "additionalProperties": False,
    "properties": {
        "mode": {"enum": ["none", "every_n_landed_hits"]},
        "period_hits": {"anyOf": [{"type": "integer", "minimum": 1, "maximum": 256}, _NULL]},
        "first_attempt_ordinal": {"anyOf": [
            {"type": "integer", "minimum": 1, "maximum": 256}, _NULL]},
    }, "required": ["mode", "period_hits", "first_attempt_ordinal"]}
APPLICATION_RULE = {"type": "object", "additionalProperties": False,
    "properties": {
        "mode": {"enum": ["sequence", "no_icd", "no_application", "pending"]},
        "counter_tag": {"anyOf": [_ADAPTER_ID, _NULL]},
        "attempt_gate": APPLICATION_RULE_GATE,
    }, "required": ["mode", "counter_tag", "attempt_gate"]}
APPLICATION_ADAPTER_AUDIT = {"type": "object", "additionalProperties": False,
    "properties": {
        "contract_id": {"enum": ["application-adapter-v1"]},
        "component_id": _ADAPTER_ID,
        "accepted_base_fingerprint": _ADAPTER_HASH,
        "base_application_fingerprint": _ADAPTER_HASH,
        "adapter_fingerprint": _ADAPTER_HASH,
        "declaration": APPLICATION_ADAPTER,
        "base_rule": APPLICATION_RULE,
        "effective_rule": APPLICATION_RULE,
    }, "required": ["contract_id", "component_id", "accepted_base_fingerprint",
                     "base_application_fingerprint", "adapter_fingerprint", "declaration",
                     "base_rule", "effective_rule"]}
APPLICATION_RULE_AUDIT = {"type": "object", "additionalProperties": False,
    "properties": {
        "contract_id": {"enum": ["application-rule-audit-v1"]},
        "component_id": _ADAPTER_ID,
        "emission_kind": {"enum": ["fixed", "periodic"]},
        "base_application_fingerprint": _ADAPTER_HASH,
        "base_rule": APPLICATION_RULE,
        "effective_rule": APPLICATION_RULE,
        "adapter_fingerprint": {"anyOf": [_ADAPTER_HASH, _NULL]},
        "database_identity": {"type": "object", "additionalProperties": False,
            "properties": {
                "status": {"anyOf": [{"enum": ["ready", "pending"]}, _NULL]},
                "counter_tag": {"anyOf": [_ADAPTER_ID, _NULL]},
            }, "required": ["status", "counter_tag"]},
        "identity_divergence": {"type": "boolean"},
        "base_icd_rule_fingerprint": _ADAPTER_HASH,
        "base_icd_rule": DATABASE_ICD_RULE,
        "effective_icd_rule": EFFECTIVE_ICD_RULE,
    }, "required": ["contract_id", "component_id", "emission_kind",
                     "base_application_fingerprint", "base_rule", "effective_rule",
                     "adapter_fingerprint", "database_identity", "identity_divergence"]}


def has_verified_application_evidence(binding, mode, *, require_binding_status=False):
    """Free text is never enough to create executable per-component semantics."""
    verification = binding.get("verification") or {}
    confirmed = set(verification.get("confirmed_fields") or [])
    required = {"application_mode", "shared_scope"}
    if mode == "sequence":
        required.add("counter_tag")
    return ((not require_binding_status or binding.get("status") == "confirmed")
            and verification.get("status") == "confirmed"
            and all(isinstance(verification.get(key), str) and verification[key].strip()
                    for key in ("locator", "version", "reviewed_at", "component_scope"))
            and required <= confirmed)


def research_report_template():
    """Return a blank *persistable* audit log with no execution authority.

    Work orders expose this value as the exact object an Agent may fill and
    write back to a source binding.  Keep it identical to ``RESEARCH_REPORT``:
    putting explanatory schema fields in the template would make the template
    itself fail the closed saved-plan schema.
    """
    return {"report_version": 1, "execution_authority": "none",
        "actual_queries": [], "sources_checked": [], "adopted_findings": [],
        "rejected_findings": [], "remaining_unknowns": [],
        "database_patch_plan": []}


def verification_guidance(mode, missing_fields, *, component="该具体攻击分量"):
    """Return mode-specific research prompts without re-questioning proven fields."""
    missing = set(missing_fields)
    questions = []
    if mode == "pending":
        questions.append(f"确认{component}属于sequence、no_icd、no_application中的哪一态；若证据仍不足则保持pending。")
        acceptance = "先取得逐攻击分量的正向证据以裁定附着四态；不得用动作类别或同技能其他段推断"
    elif mode == "sequence":
        if missing & {"element", "gauge"}:
            questions.append(f"核实{component}尝试附着的元素与原始GU，并记录适用目标和触发条件。")
        if missing & {"counter_tag", "structured_verification"}:
            questions.append(f"核实{component}的attenuationTag及与其他攻击分量的实际共享范围。")
        if "rule_group" in missing:
            questions.append(f"核实{component}引用的attenuationGroup、重置时间与序列模板。")
        acceptance = "逐攻击分量的sequence、counter_tag与共享范围正向证据；规则组须来自已确认数据库投影；不得用动作类别推断"
    elif mode == "no_icd":
        if missing & {"element", "gauge"}:
            questions.append(f"核实{component}每次尝试附着的元素与原始GU。")
        if "structured_verification" in missing:
            questions.append(f"取得{component}在列明条件下逐次均可附着、没有共享ICD计数器的正向证据。")
        acceptance = "逐攻击分量在明确版本、目标与触发条件下无ICD的正向证据"
    elif mode == "no_application":
        if "element" in missing:
            questions.append(f"核实{component}的伤害元素；伤害元素不能单独证明会附着。")
        if "structured_verification" in missing:
            questions.append(f"取得{component}造成元素伤害但不尝试元素附着的正向证据。")
        acceptance = "逐攻击分量在明确版本、目标与触发条件下不尝试附着的正向证据"
    else:
        questions.append(f"核实{component}的canonical application_mode。")
        acceptance = "采用canonical四态之一并提供逐攻击分量证据"
    return {"targeted_research_questions": questions, "acceptance_required": acceptance}
