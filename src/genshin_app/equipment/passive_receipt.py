"""Offline consistency checks for adopted inherent-talent calculations."""
from __future__ import annotations

from genshin_runtime.passive_source_identity import (
    passive_source_fingerprint,
    passive_unlock_fingerprint,
)

from .files import fingerprint, input_view

COMPILER = "database-inherent-swirl-bonus-v1"
PROFILE = "inherent-ordinary-swirl-bonus-v1"
ATTRIBUTE_COMPILER = 'database-inherent-swirl-attribute-v2'
ATTRIBUTE_PROFILE = 'inherent-ordinary-swirl-attribute-v2'
COLLECTION_COMPILER = 'passive-source-collection-v2'
IDENTITY_COVERAGE = "explicit_passive_sources_and_provider_anemo_parents"
PART_IDS = {"passive.dmg_bonus.hydro", "passive.dmg_bonus.cryo"}
MESSAGE = "固有天赋来源、动作资格、实际规则或面板口径与回执不一致，请回App重新计算。"


def make_passive_receipt(plan, report):
    compiler = COLLECTION_COMPILER if any(r.get('compiler')==ATTRIBUTE_COMPILER for r in report.get('passive_compilations', [])) else COMPILER
    return {"compiler": compiler, "identity_coverage": IDENTITY_COVERAGE,
        "compilations_fingerprint": fingerprint(report.get("passive_compilations", [])),
        "compiled_mechanics_model_fingerprint": fingerprint(report["mechanics_model"]),
        "mechanics_input_template_fingerprint": fingerprint(report["mechanics_input_template"]),
        "input_sources_fingerprint": fingerprint(plan.get("passive_sources", []))}


def validate_passive_result_receipt(result, receipt):
    """Snapshot exports have frozen results, not a database or editable model."""
    records, stored = result.get("passive_compilations") or [], receipt.get("passives")
    if not records and not stored:
        return
    try:
        expected_compiler = COLLECTION_COMPILER if any(r.get('compiler')==ATTRIBUTE_COMPILER for r in records) else COMPILER
        if (not records or not isinstance(stored, dict) or stored.get("compiler") != expected_compiler
                or stored.get("identity_coverage") != IDENTITY_COVERAGE
                or stored.get("compilations_fingerprint") != fingerprint(records)
                or receipt.get("result_fingerprint") != fingerprint(result)
                or len({r["id"] for r in records}) != len(records)):
            raise ValueError(MESSAGE)
        parent = receipt.get("action_events")
        if (not isinstance(parent, dict) or not result.get("action_event_compilations")
                or parent.get("compilations_fingerprint") != fingerprint(result["action_event_compilations"])):
            raise ValueError(MESSAGE)
        for key in ("compiled_mechanics_model_fingerprint", "mechanics_input_template_fingerprint"):
            if not stored.get(key) or stored[key] != parent.get(key):
                raise ValueError(MESSAGE)
        for row in records:
            dynamic = row.get('compiler') == ATTRIBUTE_COMPILER
            source, scope = row["source"], row["scope"]
            certificate = row.get("coverage_certificate") or {}
            accepted = row["required_confirmation"]["accepted_fingerprint"]
            if (row["status"] != "ready" or row.get("profile_id") != (ATTRIBUTE_PROFILE if dynamic else PROFILE) or row.get("compiler") != (ATTRIBUTE_COMPILER if dynamic else COMPILER)
                    or row.get("whole_source_status") != "partial" or row.get("projection_status") != "ready"
                    or certificate.get("kind") != ('ordinary_swirl_attribute_projection_certificate' if dynamic else "ordinary_swirl_passive_projection_certificate")
                    or certificate.get("source_complete") is not False or certificate.get("projection_complete") is not True
                    or certificate.get("gameplay_verified") is not False
                    or not accepted or row["accepted_fingerprint"] != accepted
                    or row["scope_fingerprint"] != fingerprint(scope) or scope.get("status") != "ready"
                    or not scope.get("provider_parent_events") or scope.get("issues")
                    or scope.get("identity_coverage") != IDENTITY_COVERAGE
                    or row["source_fingerprint"] != source["source_fingerprint"]
                    or source["source_fingerprint"] != passive_source_fingerprint(source)
                    or row["unlock_fingerprint"] != source["unlock_fingerprint"]
                    or source["unlock_fingerprint"] != passive_unlock_fingerprint(source)
                    or source["content_fingerprint"] != fingerprint({k: v for k, v in source.items() if k != "content_fingerprint"})
                    or row["unlock"] != source["unlock"]
                    or row["unlock"].get("status") != "eligible_under_declared_progression"
                    or row["stat_basis"].get("gameplay_interpretation") != "declared_not_verified"
                    or len(row["parts"]) != 2 or {p["part_id"] for p in row["parts"]} != PART_IDS
                    or len(row["rules"]) != 2 or len(row["initial_rules"]) != 2
                    or len(set(row["rule_ids"])) != 2 or set(row["rule_ids"]) != {r["id"] for r in row["rules"]}):
                raise ValueError(MESSAGE)
            if dynamic:
                from genshin_engine.runtime_passive_attribute_compiler import (
                    compile_passive_attribute_source,
                )
                replay = compile_passive_attribute_source(source,owner=row['actor'],source_id=row['id'],scenario=row['scenario'])
                if replay.get('status') != 'ready' or any(row.get(k) != replay.get(k) for k in (
                        'attribute_evidence','rules','parts','initial_rules','execution_binding','execution_fingerprint')):
                    raise ValueError(MESSAGE)
                final = row.get('final_attribute_sampling_qualification') or {}
                if (final.get('provider') != row['actor'] or final.get('status') != 'qualified_constant_nonextra_writers'
                        or final.get('issues') or row.get('final_attribute_sampling_fingerprint') != fingerprint(final)):
                    raise ValueError(MESSAGE)
    except (KeyError, TypeError, AttributeError, RecursionError) as exc:
        raise ValueError(MESSAGE) from exc


def validate_passive_receipt(plan, result, report, receipt):
    """Cross-check sources, declarations, report and exact private runtime model."""
    bindings, records = plan.get("passive_sources", []), result.get("passive_compilations") or []
    stored = receipt.get("passives")
    if not (bindings or records or stored or report.get("passive_compilations")):
        return
    try:
        validate_passive_result_receipt(result, receipt)
        if (not bindings or not records or stored != make_passive_receipt(plan, report)
                or records != report.get("passive_compilations")
                or receipt.get("input_fingerprint") != fingerprint(input_view(plan))
                or len({b["id"] for b in bindings}) != len(bindings)
                or {b["id"] for b in bindings} != {r["id"] for r in records}):
            raise ValueError(MESSAGE)
        rules = {r["id"]: r for r in report["mechanics_model"]["rules"]}
        initial = {r["rule_id"]: r for r in report["mechanics_model"]["initial_state"]["rules"]}
        if len(rules) != len(report["mechanics_model"]["rules"]) or len(initial) != len(report["mechanics_model"]["initial_state"]["rules"]):
            raise ValueError(MESSAGE)
        for record in records:
            binding = next(b for b in bindings if b["id"] == record["id"])
            if (binding["accepted_fingerprint"] != record["accepted_fingerprint"] or binding["actor"] != record["actor"]
                    or binding["proud_skill_id"] != record["proud_skill_id"]
                    or record["source"]["selection"]["proud_skill_id"] != binding["proud_skill_id"]
                    or record["scope"]["progression"] != plan["equipment_inputs"][binding["actor"]].get("progression")
                    or record["stat_basis"]["mode"] != binding["scenario"]["stat_basis"]
                    or record["stat_basis"]["source"] != binding["scenario"]["source"]
                    or record["stat_basis"]["status"] != binding["scenario"]["status"]):
                raise ValueError(MESSAGE)
            if record.get('compiler') == ATTRIBUTE_COMPILER:
                from .passive_attribute_scope import inspect_nonextra_sampling
                final = inspect_nonextra_sampling({**plan,'mechanics_model':report['mechanics_model']},
                                                  report['mechanics_input_template'],record['actor'])
                if (record.get('scenario') != binding['scenario']
                        or record.get('final_attribute_sampling_qualification') != final):
                    raise ValueError(MESSAGE)
            for rule in record["rules"]:
                if (rules.get(rule["id"]) != rule or rule["owner"] != binding["actor"]
                        or rule["source_id"] not in {binding["id"] + ":" + part for part in PART_IDS}):
                    raise ValueError(MESSAGE)
            for state in record["initial_rules"]:
                if initial.get(state["rule_id"]) != state or state["instances"] or state["cooldown_remaining"] != 0:
                    raise ValueError(MESSAGE)
            for part in record["parts"]:
                rule = rules.get(part["rule"]["rule_id"], {})
                if rule.get("source_id") != binding["id"] + ":" + part["part_id"] or part["delivery"]["kind"] != "mechanics":
                    raise ValueError(MESSAGE)
    except (KeyError, TypeError, AttributeError, StopIteration, RecursionError) as exc:
        raise ValueError(MESSAGE) from exc
