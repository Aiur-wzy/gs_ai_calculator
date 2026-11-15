"""Offline periodic-source and private-dispatch consistency, without DB reads."""
from __future__ import annotations

import copy
import json

from genshin_runtime.periodic_attack_identity import (
    periodic_source_fingerprint,
    periodic_source_payload,
)

from .files import fingerprint, input_view
from .finite_action_schema import COMPILER as FINITE_APPLICATION_COMPILER
from .finite_action_schema import KIND as FINITE_APPLICATION_KIND
from .periodic_events import (
    _refresh_declared_target,
    compile_periodic_events,
    patterns_for_record,
)
from .periodic_schema import COMPILER, MAX_RECEIPT_BYTES, MULTI_KINDS

SOURCE_EVENT_KINDS = (*MULTI_KINDS, FINITE_APPLICATION_KIND)

MESSAGE = "周期来源、成对通道、校准生命周期或实际派发与回执不一致，请回App重新计算。"

_OPTIONAL_CANDIDATE_ISSUES = {
    "sequence": set(),
    "no_icd": {"periodic_attenuation_pending", "periodic_attenuation_unavailable",
               "periodic_attenuation_not_admitted", "periodic_attenuation_tag_invalid"},
    "no_application": {"periodic_gauge_unsupported", "periodic_attenuation_pending",
                       "periodic_attenuation_unavailable", "periodic_attenuation_not_admitted",
                       "periodic_attenuation_tag_invalid"},
}


def make_periodic_receipt(plan, report):
    from .action_dispatch_sources import action_dispatch_execution_plan
    execution_plan = action_dispatch_execution_plan(plan, report)
    materialization = report.get("action_dispatch_materialization") or {}
    result = {"compiler": COMPILER, "dispatch_mode": "calibrated", "whole_source_status": "partial",
        "action_creation_association": "unproven", "dispatch_semantics_status": "not_proven",
        "compilations_fingerprint": fingerprint(report.get("periodic_attack_compilations", [])),
        "input_sources_fingerprint": fingerprint(execution_plan.get("periodic_attack_sources", [])),
        "action_dispatch_materialization_fingerprint": materialization.get("content_fingerprint"),
        "compiled_mechanics_model_fingerprint": fingerprint(report["mechanics_model"]),
        "mechanics_input_template_fingerprint": fingerprint(report["mechanics_input_template"]),
        "dispatch_compilations_fingerprint": fingerprint(report["action_event_compilations"])}
    refreshes = execution_plan.get("periodic_refresh_triggers", [])
    if refreshes:
        result["input_refresh_triggers_fingerprint"] = fingerprint(refreshes)
    return result


def _sources(records, *, finite_profile_ids=frozenset()):
    if (not isinstance(finite_profile_ids, (set, frozenset))
            or any(not isinstance(profile_id, str) for profile_id in finite_profile_ids)):
        raise ValueError(MESSAGE)
    if (not records or len(records) > 32 or len({r["action_event_profile_id"] for r in records}) != len(records)
            or len(json.dumps(records, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf8")) > MAX_RECEIPT_BYTES):
        raise ValueError(MESSAGE)
    if not finite_profile_ids.issubset({row["action_event_profile_id"] for row in records}):
        raise ValueError(MESSAGE)
    for row in records:
        query = row["query"]
        semantic = periodic_source_payload(query)
        if (row["status"] != "ready" or row["compiler"] != COMPILER or row["whole_source_status"] != "partial"
                or row["action_creation_association"] != "unproven" or row["dispatch_semantics_status"] != "not_proven"
                or row["dispatch_mode"] != "calibrated" or row["gameplay_interpretation"] != "declared_not_verified"
                or query["status"] not in ("ready", "pending") or query["mode"] != "selected"
                or query["semantic_dependency"] != semantic or query["source_fingerprint"] != periodic_source_fingerprint(query)
                or query["content_fingerprint"] != fingerprint({k: v for k, v in query.items() if k != "content_fingerprint"})
                or row["snapshot"] != query["snapshot"] or row["source_fingerprint"] != query["source_fingerprint"]
                or row["semantic_dependency"]["database"] != semantic
                or row["periodic_dependency_fingerprint"] != fingerprint(row["semantic_dependency"])):
            raise ValueError(MESSAGE)
        selected = {c["candidate_id"]: c for c in query["selected_candidates"]}
        channels = row["channels"]
        if (not channels or len(channels) != len(selected) or {c["candidate_id"] for c in channels} != set(selected)
                or len({c["channel_id"] for c in channels}) != len(channels)):
            raise ValueError(MESSAGE)
        pairs = []
        for channel in channels:
            candidate = selected[channel["candidate_id"]]
            projection = candidate["projection"]
            fields = channel["compiled_fields"]
            mode = fields.get("application_mode")
            if mode not in _OPTIONAL_CANDIDATE_ISSUES:
                raise ValueError(MESSAGE)
            expected = {"element": projection["element"]["value"], "multiplier": projection["parameter"]["value"],
                "param_index": projection["parameter"]["param_index"], "rule_group": None,
                "counter_tag": None, "application_mode": mode,
                "application_identity_status": channel["application_identity"]["status"]}
            required = ("parameter", "element", "classification")
            if mode in ("sequence", "no_icd"):
                required += ("gauge",)
                expected.update(gauge_units=projection["gauge"]["value"], tax_applied=False)
            if mode == "sequence":
                required += ("attenuation",)
                expected.update(rule_group=projection["attenuation"]["group"],
                                counter_tag=fields["counter_tag"])
            optional_issues = set(_OPTIONAL_CANDIDATE_ISSUES[mode])
            # A reviewed finite topology owns occurrence timing in its steps.
            # Only that exactly paired profile may accept an inexact source
            # callback timer; ordinary periodic and explicit-offset profiles
            # still require their callback timer evidence to be ready.
            if row["action_event_profile_id"] in finite_profile_ids:
                optional_issues.add("periodic_timer_not_exact")
            blocking = [issue for issue in candidate.get("issues", [])
                        if issue.get("code") not in optional_issues]
            rule_audit = channel.get("application_rule_audit")
            effective_icd_rule = (rule_audit or {}).get("effective_icd_rule")
            if (effective_icd_rule or {}).get("kind") == "timer_counter":
                expected.update(rule_group=effective_icd_rule["rule_group"],
                                database_rule_group=projection["attenuation"]["group"])
                attenuation = projection["attenuation"]
                profile = attenuation["profile"]
                projected = profile["projection"]
                database_rule = {"status": "ready", "rule_group": attenuation["group"],
                    "reset_seconds": projected["reset_cycle_seconds_rounded"],
                    "sequence": projected["durability_sequence"],
                    "source_fingerprint": profile["content_fingerprint"]}
                adapter = channel.get("application_adapter") or {}
                icd_rule = ((adapter.get("declaration") or {}).get("icd_rule") or {})
                base_fingerprint = fingerprint(database_rule)
                expected_reset = (database_rule["reset_seconds"]
                                  if icd_rule.get("reset_seconds") is None
                                  else icd_rule.get("reset_seconds"))
                expected_group = "adapter.icd." + fingerprint({
                    "base_rule_fingerprint": base_fingerprint,
                    "kind": "timer_counter", "reset_seconds": expected_reset,
                    "hit_interval": icd_rule.get("hit_interval"),
                })[:48]
                if (rule_audit.get("base_icd_rule") != database_rule
                        or rule_audit.get("base_icd_rule_fingerprint") != base_fingerprint
                        or icd_rule.get("mode") != "timer_counter"
                        or icd_rule.get("accepted_base_rule_fingerprint") != base_fingerprint
                        or effective_icd_rule.get("reset_seconds") != expected_reset
                        or effective_icd_rule.get("hit_interval") != icd_rule.get("hit_interval")
                        or effective_icd_rule.get("rule_group") != expected_group
                        or effective_icd_rule.get("sequence") is not None
                        or effective_icd_rule.get("first_hit") != 1):
                    raise ValueError(MESSAGE)
            expected_divergence = (
                (projection.get("application_identity") or {}).get("status") == "ready"
                and (mode != "sequence" or fields.get("counter_tag")
                     != (projection.get("application_identity") or {}).get("tag")))
            identity_audit_invalid = (
                (rule_audit is not None and (
                    channel.get("database_application_identity")
                    != (projection.get("application_identity") or {})
                    or channel.get("application_identity_divergence") != expected_divergence))
                or (rule_audit is None and (
                    "application_adapter" in channel
                    or "database_application_identity" in channel
                    or "application_identity_divergence" in channel)))
            if (channel["channel_kind"] != candidate["channel_kind"]
                    or channel["projection"] != projection or fields != expected or blocking
                    or identity_audit_invalid
                    or any((projection.get(k) or {}).get("status") != "ready" for k in required)
                    or mode in ("sequence", "no_icd") and projection["gauge"].get("tax_applied") is not False):
                raise ValueError(MESSAGE)
            pairs.append({"candidate_id": channel["candidate_id"], "application_mode": mode,
                "counter_tag": fields.get("counter_tag"), "verification": copy.deepcopy(channel.get("verification")),
                **({"application_adapter": copy.deepcopy(channel["application_adapter"])}
                   if "application_adapter" in channel else {}),
                **({"application_rule_audit": copy.deepcopy(channel["application_rule_audit"])}
                   if "application_rule_audit" in channel else {}),
                **({"identity_divergence": channel["application_identity_divergence"]}
                   if rule_audit is not None else {}),
                **({"deprecated_counter_tag_field": "shared_group"}
                   if "shared_group" in channel.get("deprecated_input_fields", []) else {})})
        if (row["semantic_dependency"]["channels"] != sorted(pairs, key=lambda c: c["candidate_id"])
                or row["compiled_fields"] != {c["channel_id"]: c["compiled_fields"] for c in channels}):
            raise ValueError(MESSAGE)


def validate_periodic_result_receipt(result, receipt):
    """Result-only exports validate their frozen materialized evidence offline."""
    records, stored = result.get("periodic_attack_compilations") or [], receipt.get("periodic_attacks")
    periodic_events = [r for r in result.get("action_event_compilations", [])
                       if r.get("kind") in SOURCE_EVENT_KINDS]
    if not records and not stored and not periodic_events:
        return
    try:
        if (len(periodic_events) != len(records)
                or {event["profile_id"] for event in periodic_events}
                != {row["action_event_profile_id"] for row in records}):
            raise ValueError(MESSAGE)
        finite_events = [event for event in periodic_events
                         if event["kind"] == FINITE_APPLICATION_KIND]
        if any(event.get("compiler") != FINITE_APPLICATION_COMPILER
               or (event.get("scope") or {}).get("kind") != FINITE_APPLICATION_KIND
               for event in finite_events):
            raise ValueError(MESSAGE)
        finite_profile_ids = {event["profile_id"] for event in finite_events}
        _sources(records, finite_profile_ids=finite_profile_ids)
        parent = receipt["action_events"]
        if (not isinstance(stored, dict) or stored["compiler"] != COMPILER
                or stored["compilations_fingerprint"] != fingerprint(records)
                or receipt["result_fingerprint"] != fingerprint(result)
                or stored["dispatch_compilations_fingerprint"] != fingerprint(result["action_event_compilations"])
                or stored["dispatch_compilations_fingerprint"] != parent["compilations_fingerprint"]
                or any(stored[k] != parent[k] for k in ("compiled_mechanics_model_fingerprint", "mechanics_input_template_fingerprint"))):
            raise ValueError(MESSAGE)
        events = periodic_events
        for event in events:
            source = next(r for r in records if r["action_event_profile_id"] == event["profile_id"])
            generated = event["generated"]
            cancelled = sum(row["cancelled_hits"] for row in event["instance_payload"].get("replacements", []))
            stats = {"matched_actions": len(event["instance_payload"]["actions"]),
                "planned_hits": len(generated) + cancelled,
                "generated_hits": len(generated), "landed_hits": sum(g["landed"] for g in generated), "missed_hits": sum(not g["landed"] for g in generated)}
            if event.get("replacement_policy"):
                stats["cancelled_hits"] = cancelled
            if any(channel.get("application_cadence") is not None for channel in event["channels"]):
                cancelled_attempts = sum(
                    row.get("cancelled_application_attempts", 0)
                    for row in event["instance_payload"].get("replacements", []))
                generated_attempts = sum(
                    row.get("application_attempted", row["landed"]) for row in generated)
                stats.update(
                    planned_application_attempts=generated_attempts + cancelled_attempts,
                    generated_application_attempts=generated_attempts,
                    cancelled_application_attempts=cancelled_attempts,
                    application_gated_hits=sum(
                        row["landed"] and row.get("application_attempted") is False for row in generated),
                )
            refreshes = event.get("refresh_triggers", [])
            if refreshes:
                declarations = event["instance_payload"].get("refresh_declarations", [])
                declarations_by_id = {row.get("id"): row for row in declarations}
                if (len(refreshes) != len(declarations)
                        or len(declarations_by_id) != len(declarations)
                        or {row.get("trigger_id") for row in refreshes} != set(declarations_by_id)
                        or any(row.get("declared_target")
                               != _refresh_declared_target(declarations_by_id[row["trigger_id"]])
                               or row.get("resolved_profile_id") != event["profile_id"]
                               or row.get("profile_id") != event["profile_id"]
                               for row in refreshes)
                        or any(row.get("source_event_fingerprint") != fingerprint(row.get("source_event"))
                               for row in refreshes)
                        or event["instance_payload"].get("refresh_triggers") != refreshes
                        or event["instance_payload"].get("refresh_contract") != event.get("refresh_contract")):
                    raise ValueError(MESSAGE)
                applied = sum(row.get("outcome") == "replaced_active" for row in refreshes)
                inactive = sum(row.get("outcome") == "inactive_noop" for row in refreshes)
                if applied + inactive != len(refreshes):
                    raise ValueError(MESSAGE)
                stats.update(refresh_triggers=len(declarations), refreshes_applied=applied,
                    refreshes_inactive_noop=inactive,
                    generations_created=len(event["instance_payload"]["actions"]) + applied,
                    instances_replaced=len(event["instance_payload"].get("replacements", [])))
            if (event["status"] != "ready" or event["profile_fingerprint"] != fingerprint(event["scope"])
                    or event["accepted_profile_fingerprint"] != event["profile_fingerprint"]
                    or event["required_confirmation"] != {"accepted_profile_fingerprint": event["profile_fingerprint"]}
                    or event["scope"]["dependencies"] != source["semantic_dependency"]
                    or event["source_dependency_fingerprint"] != source["periodic_dependency_fingerprint"]
                    or event["instance_fingerprint"] != fingerprint(event["instance_payload"])
                    or event["instance_payload"]["generated"] != event["generated"]
                    or event["instance_payload"]["channels"] != event["channels"] or event["stats"] != stats):
                raise ValueError(MESSAGE)
    except (KeyError, TypeError, AttributeError, StopIteration, RecursionError) as exc:
        raise ValueError(MESSAGE) from exc


def validate_periodic_receipt(plan, result, report, receipt):
    """Rebuild source-backed dispatch from native declarations, never from a DB."""
    records = report.get("periodic_attack_compilations") or []
    if not (plan.get("periodic_attack_sources") or records or result.get("periodic_attack_compilations") or receipt.get("periodic_attacks")):
        return
    try:
        from .action_dispatch_sources import action_dispatch_execution_plan
        work = action_dispatch_execution_plan(plan, report)
        periodic_sources = work.get("periodic_attack_sources", [])
        validate_periodic_result_receipt(result, receipt)
        if (receipt.get("input_fingerprint") != fingerprint(input_view(plan))
                or receipt.get("periodic_attacks") != make_periodic_receipt(plan, report)
                or records != result.get("periodic_attack_compilations")
                or {r["action_event_profile_id"] for r in records}
                != {s["action_event_profile_id"] for s in periodic_sources}):
            raise ValueError(MESSAGE)
        work["mechanics_model"] = copy.deepcopy(report["mechanics_model"])
        work["mechanics_model"]["hit_patterns"] = [p for p in work["mechanics_model"]["hit_patterns"] if not p["id"].startswith("auto.action.")]
        work["rotation_model"] = copy.deepcopy(report.get("compiled_rotation_model", plan["rotation_model"]))
        for source in records:
            binding = next(b for b in periodic_sources
                           if b["action_event_profile_id"] == source["action_event_profile_id"])
            if (source["selection"] != binding["selection"] or source["version_line"] != binding["version_line"]
                    or source["origin_reference_id"] != binding["origin_reference_id"]
                    or source["semantic_dependency"]["interpretation"] != {k: binding[k] for k in ("purpose", "status", "source")}):
                raise ValueError(MESSAGE)
            for channel in source["channels"]:
                mapping = next(c for c in binding["channels"] if c["channel_id"] == channel["channel_id"])
                mapping_tag = mapping.get("counter_tag") or mapping.get("shared_group")
                if (mapping["candidate_id"] != channel["candidate_id"]
                        or ("pending" if mapping.get("application_mode") in (None, "unknown")
                            else mapping.get("application_mode")) != channel["compiled_fields"]["application_mode"]
                        or mapping_tag != channel["compiled_fields"]["counter_tag"]
                        or mapping.get("application_adapter") != channel.get("application_adapter")
                        or mapping.get("application_rule_audit")
                        != channel.get("application_rule_audit")):
                    raise ValueError(MESSAGE)
                hit = next(h for h in work["manual_template"]["hits"] if h["id"] == channel["prototype_hit_id"])
                if "element" in hit or "multiplier" in hit:
                    raise ValueError(MESSAGE)
                hit["element"] = channel["compiled_fields"]["element"]
        issues = []
        _, periodic_expected, _ = compile_periodic_events(work, report["mechanics_input_template"],
            parameter_compilations=report.get("hit_parameter_compilations", []),
            elemental_compilations=report.get("elemental_compilations", []),
            reaction_compilations=report.get("reaction_damage_compilations", []), periodic_compilations=records,
            resource_compilations=report.get("action_resource_compilations", []), missing=lambda *args: issues.append(args))
        from .finite_action_events import compile_finite_action_events
        _, finite_expected, _ = compile_finite_action_events(work,
            report["mechanics_input_template"],
            parameter_compilations=report.get("hit_parameter_compilations", []),
            elemental_compilations=report.get("elemental_compilations", []),
            reaction_compilations=report.get("reaction_damage_compilations", []),
            periodic_compilations=records, missing=lambda *args: issues.append(args))
        expected = periodic_expected + finite_expected
        actual = [r for r in report["action_event_compilations"]
                  if r["kind"] in SOURCE_EVENT_KINDS]
        if issues or expected != actual or actual != [r for r in result["action_event_compilations"]
                                                       if r["kind"] in SOURCE_EVENT_KINDS]:
            raise ValueError(MESSAGE)
        patterns = report["mechanics_model"]["hit_patterns"]
        for event in actual:
            for pattern in patterns_for_record(event):
                if [p for p in patterns if p["id"] == pattern["id"]] != [pattern]:
                    raise ValueError(MESSAGE)
    except (KeyError, TypeError, AttributeError, StopIteration, RecursionError) as exc:
        raise ValueError(MESSAGE) from exc
