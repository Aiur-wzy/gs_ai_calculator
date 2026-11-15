"""Pure typed descriptor -> existing D20 templates; no character dispatch."""
from __future__ import annotations

import copy

from genshin_runtime.mechanism_semantics import (
    LIMITS,
    _rule_template,
    descriptor_payload,
    fingerprint,
    finite_json,
    mechanism_semantics_capabilities,
    target_location,
    verify_mechanism_source,
)

COMPILER = "typed-mechanism-d20-v1"
CAST_COMPILER = "typed-mechanism-d20-v2"
CONTACT_COMPILER = "typed-mechanism-d20-v3"


def mechanism_descriptor_capabilities():
    return {**mechanism_semantics_capabilities(), "compiler": COMPILER, "compiler_version": 1,
            "supported_compilers": [{"compiler": COMPILER, "compiler_version": 1},
                                    {"compiler": CAST_COMPILER, "compiler_version": 2},
                                    {"compiler": CONTACT_COMPILER, "compiler_version": 3}],
            "ready_meaning": "unbound_templates_only", "execution_status": "not_evaluated"}


def _checks(item, qualification=None):
    cast = qualification is not None and qualification["schema_version"] == 2
    result = set(item["requirements"]) | {"fresh_empty_instance", "no_duplicate_source", "fixed_party"}
    if not cast or qualification["target_dependency"]["mode"] != "none":
        result.add("single_target")
    rule = item["rule"]
    if qualification is not None and qualification["schema_version"] == 3:
        result.update({"qualified_contact_origin", "complete_contact_coverage"})
        if qualification["scenario_requirements"].get("no_provider_transformative_damage") is True:
            result.add("no_provider_transformative_damage")
    elif cast:
        result.update({"qualified_cast_origin", "activation_within_cast_window", "no_unmodelled_cancel"})
    elif rule["trigger"]["event"] == "action_start":
        result.update({"qualified_action_origin", "no_unmodelled_cancel", "activation_before_initial_hit"})
    else:
        result.add("qualified_reaction_origin")
    if any(type(e["value"]) is dict for e in rule["effects"]):
        result.add("ordinary_stat_read")
    return sorted(result)


def compile_mechanism_descriptor(descriptor, *, source_proof, facts, parameters):
    out = {"compiler": COMPILER, "compiler_version": 1, "status": "pending", "source_status": "pending",
        "fact_status": "pending", "execution_status": "not_evaluated", "verification_scope": "materialized_consistency_only",
        "whole_source_status": "partial", "evidence_tier": [], "mechanism_id": None, "mechanism_revision": None,
        "execution_qualification": None, "qualification_status": "missing",
        "source_fingerprint": None, "mechanism_fingerprint": None, "parameter_resolutions": [], "fact_resolutions": [],
        "rule_templates": [], "initial_rule_templates": [], "execution_ports": [], "required_execution_checks": [],
        "unsupported_branches": [], "issues": [], "limits": dict(LIMITS)}
    try:
        verified = verify_mechanism_source(descriptor, source_proof=source_proof, facts=facts, parameters=parameters)
        qualification = verified["execution_qualification"]
        cast = qualification is not None and qualification["schema_version"] == 2
        contact = qualification is not None and qualification["schema_version"] == 3
        compiler, version = (CONTACT_COMPILER, 3) if contact else (CAST_COMPILER, 2) if cast else (COMPILER, 1)
        out.update(compiler=compiler, compiler_version=version)
        d = verified["descriptor"]
        ps = {p["ref"]: p for p in verified["parameters"]}
        rules, initials, ports = [], [], []
        checks = {f["claim"]["requirement"] for f in verified["facts"] if f["kind"] == "execution_requirement"}
        for item in d["rule_templates"]:
            materialized = copy.deepcopy(item)
            for binding in item["value_bindings"]:
                container, key = target_location(materialized["rule"], binding["target"])
                container[key] = ps[binding["parameter_ref"]]["value"]
            materialized["value_bindings"] = []
            _rule_template(materialized)  # Validate actual values, not placeholder values.
            rules.append({"key": item["key"], "rule": materialized["rule"]})
            initials.append({"key": item["key"], "cooldown_remaining": 0, "instances": []})
            ports.extend({"rule_key": item["key"], **copy.deepcopy(port)} for port in item["execution_ports"])
            checks.update(_checks(materialized, qualification))
        dependency = {"compiler": compiler, "version": version, "descriptor": descriptor_payload(d),
            "source_fingerprint": verified["source_fingerprint"],
            "parameters": verified["parameters"], "facts": source_proof["fact_fingerprints"],
            "templates": [{"key": r["key"], "rule": {k: v for k, v in r["rule"].items() if k != "name_zh"}} for r in rules],
            "ports": ports, "checks": sorted(checks)}
        if qualification is not None:
            dependency["execution_qualification"] = qualification
        out.update(status="ready", source_status="consistent", fact_status="consistent", evidence_tier=verified["evidence_tier"],
            mechanism_id=d["mechanism_id"], mechanism_revision=d["revision"], source_fingerprint=verified["source_fingerprint"],
            mechanism_fingerprint=fingerprint(dependency), parameter_resolutions=copy.deepcopy(verified["parameters"]),
            execution_qualification=copy.deepcopy(qualification),
            qualification_status="consistent" if qualification is not None else "missing",
            fact_resolutions=[{"ref": f["id"] + "@" + str(f["revision"]), "fingerprint": source_proof["fact_fingerprints"][f["id"] + "@" + str(f["revision"])]} for f in verified["facts"]],
            rule_templates=rules, initial_rule_templates=initials, execution_ports=ports,
            required_execution_checks=sorted(checks), unsupported_branches=copy.deepcopy(verified["coverage"]["excluded_branches"]))
        finite_json(out, LIMITS["output_bytes"])
    except (ValueError, KeyError, TypeError, IndexError, OverflowError, RecursionError, StopIteration) as exc:
        out.update(status="pending", rule_templates=[], initial_rule_templates=[], execution_ports=[],
                   execution_qualification=None, qualification_status="pending",
                   required_execution_checks=[], mechanism_fingerprint=None, parameter_resolutions=[], fact_resolutions=[])
        out["issues"] = [{"code": "mechanism_descriptor_pending", "message": str(exc)[:1024]}]
    return out
