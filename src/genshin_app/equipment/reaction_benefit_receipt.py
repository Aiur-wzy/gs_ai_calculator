"""Materialized reaction-benefit evidence shared by calculation and exports."""
from __future__ import annotations

from .files import fingerprint, input_view

PROFILE_ID = "equipment-ordinary-swirl-benefits-v1"
COMPILER = "database-ordinary-swirl-benefits-v1"
UPSTREAM_RECEIPT_FIELDS = ("binding_id", "source_identity", "compilation_fingerprint", "provenance",
    "gameplay_semantics_status", "semantic_assumptions", "projection_status", "whole_source_status",
    "coverage_certificate", "branches")


def reaction_benefit_bindings(plan):
    return [row for row in plan.get("effect_bindings", [])
            if row.get("upstream_recipe", {}).get("scenario", {}).get("profile_id") == PROFILE_ID]


def reaction_benefit_records(report):
    return [row for row in report.get("upstream_compilations", []) if row.get("profile_id") == PROFILE_ID]


def recipe_inputs(plan):
    return [{key: row[key] for key in ("binding_id", "provider_character_id", "owner", "upstream_recipe")}
            for row in reaction_benefit_bindings(plan)]


def make_reaction_benefit_receipt(plan, report):
    return {"compiler": COMPILER,
            "compilations_fingerprint": fingerprint(reaction_benefit_records(report)),
            "compiled_mechanics_model_fingerprint": fingerprint(report["mechanics_model"]),
            "mechanics_input_template_fingerprint": fingerprint(report["mechanics_input_template"]),
            "input_recipes_fingerprint": fingerprint(recipe_inputs(plan))}


def validate_reaction_benefit_shared_receipt(records, receipt):
    """A second shared receipt must not contradict the complete new source records."""
    if not records:
        return
    message = "反应收益的共享来源回执与完整记录不一致，请重新计算。"
    try:
        ids = {row["binding_id"] for row in records}
        shared = [row for row in receipt.get("upstream_sources", []) if row.get("binding_id") in ids]
        expected = [{key: row.get(key) for key in UPSTREAM_RECEIPT_FIELDS} for row in records]
        if len(ids) != len(records) or sorted(shared, key=lambda row: row["binding_id"]) != sorted(expected, key=lambda row: row["binding_id"]):
            raise ValueError(message)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(message) from exc


def validate_reaction_benefit_receipt(plan, result, report, receipt):
    """Cross-check frozen inputs, actual execution model, generated parts and source."""
    bindings = reaction_benefit_bindings(plan)
    records = result.get("reaction_benefit_compilations") or []
    recorded = receipt.get("reaction_benefits")
    if not (bindings or records or recorded or reaction_benefit_records(report)):
        return
    message = "反应收益来源、实际规则或场景与计算回执不一致，请回App重新计算。"
    try:
        by_binding = {row["binding_id"]: row for row in bindings}
        present = {row["binding_id"] for row in records}
        requirements = {row["binding_id"] for row in report.get("effect_requirements", [])}
        all_compiled = report.get("upstream_compilations", [])
        for identity in by_binding.keys() - present:
            inactive = [row for row in all_compiled if row.get("binding_id") == identity]
            if (len(inactive) != 1 or inactive[0].get("status") != "inactive" or identity in requirements
                    or any(inactive[0].get(key) for key in ("parts", "static_parts", "rules", "initial_rules"))):
                raise ValueError(message)
        if not records:
            if recorded or reaction_benefit_records(report):
                raise ValueError(message)
            return
        validate_reaction_benefit_shared_receipt(records, receipt)
        expected = make_reaction_benefit_receipt(plan, report)
        if (not bindings or not records or recorded != expected
                or records != reaction_benefit_records(report)
                or fingerprint(records) != recorded["compilations_fingerprint"]
                or receipt.get("input_fingerprint") != fingerprint(input_view(plan))
                or receipt.get("result_fingerprint") != fingerprint(result)):
            raise ValueError(message)
        if len(records) != len(present) or not present <= by_binding.keys():
            raise ValueError(message)
        rules = {row["id"]: row for row in report["mechanics_model"]["rules"]}
        initial = {row["rule_id"]: row for row in report["mechanics_model"]["initial_state"]["rules"]}
        buffs = report["mechanics_input_template"].get("buffs", [])
        for record in records:
            binding = by_binding[record["binding_id"]]
            scenario = binding["upstream_recipe"]["scenario"]
            certificate = record.get("coverage_certificate") or {}
            if (record["status"] != "ready" or record.get("projection_status") != "ready"
                    or record.get("whole_source_status") != "partial"
                    or record.get("execution_binding", {}).get("kind") != "elemental_reaction"
                    or certificate.get("kind") != "ordinary_swirl_benefit_projection_certificate"
                    or certificate.get("source_complete") is not False
                    or certificate.get("projection_complete") is not True
                    or record["required_confirmation"]["accepted_assumptions_fingerprint"] != scenario["accepted_assumptions_fingerprint"]
                    or record["assumptions_fingerprint"] != scenario["accepted_assumptions_fingerprint"]):
                raise ValueError(message)
            expected_parts = {"upstream.swirl_bonus", "upstream.shred.hydro", "upstream.shred.cryo"}
            if (len(record["parts"]) != 3 or {row["part_id"] for row in record["parts"]} != expected_parts
                    or len(record["rules"]) != 2 or len(record["initial_rules"]) != 2):
                raise ValueError(message)
            for rule in record["rules"]:
                if rules.get(rule["id"]) != rule or rule["owner"] != binding["provider_character_id"]:
                    raise ValueError(message)
            for state in record["initial_rules"]:
                if initial.get(state["rule_id"]) != state:
                    raise ValueError(message)
            for part in record["parts"]:
                source_id = record["binding_id"] + ":" + part["part_id"]
                if part["delivery"]["kind"] == "mechanics":
                    rule = rules.get(part["rule"]["rule_id"], {})
                    if rule.get("source_id") != source_id:
                        raise ValueError(message)
                    continue
                matching = [buff for buff in buffs if buff.get("extensions", {}).get("source_id") == source_id]
                if len(matching) != 1 or len(matching[0]["effects"]) != 1:
                    raise ValueError(message)
                actual, delivery = matching[0], part["delivery"]
                if (any(actual.get(key) != delivery.get(key) for key in ("start", "end", "scope", "target"))
                        or actual["extensions"].get("owner") != binding["provider_character_id"]
                        or actual["effects"][0] != {**delivery["effect"], "value": part["rule"]["value"]}):
                    raise ValueError(message)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(message) from exc
