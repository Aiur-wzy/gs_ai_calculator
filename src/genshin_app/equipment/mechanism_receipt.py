"""Offline binding checks and same-operation replay of actual formula consumption."""
from __future__ import annotations

import copy
import json
import math

from genshin_app.equipment.verification_scope import verification_scope

from .files import fingerprint, input_view
from .mechanism_source_schema import CAST_COMPILER, CONTACT_COMPILER

COMPILER = "typed-mechanism-app-v1"
PREFIX = "auto.mechanism."
CONSUMPTION_LEDGER_CONTRACT = "mechanism-consumption-ledger-v2"
MAX_RECEIPT_BYTES = 8 * 1024 * 1024
MESSAGE = "通用机制的完整来源、资格、父动作、规则或实际流水不一致，请重新计算。"
CONTEXT_KEYS = {"plan", "template", "action_event_compilations", "attack_metadata_compilations", "parameter_compilations"}
CAST_CONTEXT_KEYS = CONTEXT_KEYS | {'schema_version', 'action_resource_compilations', 'equipment_evidence', 'equipment_requirements'}
CONTACT_CONTEXT_KEYS = CAST_CONTEXT_KEYS | {'periodic_compilations', 'elemental_compilations', 'reaction_compilations'}
ADMISSION_CAST_CONTEXT_KEYS = CAST_CONTEXT_KEYS | {'action_admission_compilations'}
ADMISSION_CONTACT_CONTEXT_KEYS = CONTACT_CONTEXT_KEYS | {'action_admission_compilations'}


def _compiler(records):
    if any(r.get('compiler') == CONTACT_COMPILER for r in records):
        return CONTACT_COMPILER
    return CAST_COMPILER if any(r.get('compiler') == CAST_COMPILER for r in records) else COMPILER


def _requires_action_admission(records):
    from .mechanism_sources import requires_action_admission
    return requires_action_admission(records)


def _same(a, b):
    return fingerprint(a) == fingerprint(b)


def _own(row):
    return str(row.get("rule_id", "")).startswith(PREFIX)


def _residue(result):
    return (any(_own(r) for key in ("mechanics_trace", "sampling_trace") for r in result.get(key, []))
        or any(str(ref.get("effect_id", "")).startswith(PREFIX) for h in result.get("hits", []) for ref in h.get("matched_effect_refs", []))
        or any(PREFIX in str(r.get("id", "")) or _own(r) for r in (result.get("rotation_check") or {}).get("event_log", [])))


def _bounded(records, context, execution=None):
    raw = json.dumps([records, context, execution], ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf8")
    if len(raw) > MAX_RECEIPT_BYTES:
        raise ValueError("通用机制完整离线回执超过8MiB；未截断，请缩减输入窗口或来源。")


def mechanism_ledger_fingerprint(result):
    return fingerprint({"mechanics": [r for r in result.get("mechanics_trace", []) if _own(r)],
        "sampling": [r for r in result.get("sampling_trace", []) if _own(r)],
        "hits": result.get("hits", []), "rotation_check": result.get("rotation_check")})


def mechanism_summary(result):
    rows = [r for r in result.get("mechanics_trace", []) if _own(r)]
    return {"activations": sum(r.get("type") == "activation" for r in rows),
        "consumed_hits": sum(r.get("type") == "quota" and r.get("consumed") is True for r in rows),
        "applied_benefits": sum(r.get("type") == "effect_applied" for r in rows),
        "sampled_events": sum(_own(r) for r in result.get("sampling_trace", [])),
        "whole_source_status": "partial", "state_machine_replay_in_workbook": False}


def make_mechanism_receipt(plan, report, result):
    _bounded(result["mechanism_compilations"], result["mechanism_replay_context"], result["mechanism_execution"])
    proof = {"compiler": _compiler(result['mechanism_compilations']),
        "consumption_ledger_contract": CONSUMPTION_LEDGER_CONTRACT,
        "input_sources_fingerprint": fingerprint(plan.get("mechanism_sources", [])),
        "compilations_fingerprint": fingerprint(result["mechanism_compilations"]),
        "replay_context_fingerprint": fingerprint(result["mechanism_replay_context"]),
        "compiled_mechanics_model_fingerprint": fingerprint(report["mechanics_model"]),
        "mechanics_input_template_fingerprint": fingerprint(report["mechanics_input_template"]),
        "execution_rotation_model_fingerprint": fingerprint(result["rotation_check"]["resolved_rotation_model"]),
        "ledger_fingerprint": mechanism_ledger_fingerprint(result),
        "summary_fingerprint": fingerprint(result["mechanism_summary"])}
    if _requires_action_admission(result['mechanism_compilations']):
        proof['action_admission_execution_fingerprint'] = fingerprint(
            result['rotation_check']['action_admission_execution'])
    return proof


def _replay(records, context, *, pre_current_guard=False):
    contact = _compiler(records) == CONTACT_COMPILER
    cast = contact or _compiler(records) == CAST_COMPILER
    admission = _requires_action_admission(records)
    expected_keys = (ADMISSION_CONTACT_CONTEXT_KEYS if contact and admission else CONTACT_CONTEXT_KEYS if contact
                     else ADMISSION_CAST_CONTEXT_KEYS if cast and admission else CAST_CONTEXT_KEYS if cast
                     else CONTEXT_KEYS)
    expected_version = 5 if contact and admission else 3 if contact else 4 if admission else 2
    if (type(context) is not dict or set(context) != expected_keys or not 1 <= len(records) <= 8
            or cast and (type(context.get('schema_version')) is not int
                         or context['schema_version'] != expected_version)):
        raise ValueError(MESSAGE)
    _bounded(records, context)
    by_id = {r["id"]: r for r in records}
    if len(by_id) != len(records) or any(r.get("compiler") not in (COMPILER, CAST_COMPILER, CONTACT_COMPILER) or r.get("status") != "ready" for r in records):
        raise ValueError(MESSAGE)
    seen = set()

    def source_provider(binding):
        ident = binding["id"]
        if ident not in by_id or ident in seen:
            raise ValueError(MESSAGE)
        seen.add(ident)
        return copy.deepcopy(by_id[ident]["source_material"])

    missing = []
    extra = {}
    if cast:
        identities = {}
        for r in records:
            if r['compiler'] not in (CAST_COMPILER, CONTACT_COMPILER):
                continue
            parents = (r['scope']['identity']['contact_identity'] if r['compiler'] == CONTACT_COMPILER
                       else r['parent_graph']['dependencies'])
            for parent in parents:
                proof = parent['identity_proof']
                key = fingerprint({'query': proof['query'], 'snapshot': proof['snapshot']})
                if key in identities and not _same(identities[key], proof):
                    raise ValueError(MESSAGE)
                identities[key] = proof

        def identity_provider(query, snapshot):
            key = fingerprint({'query': query, 'snapshot': snapshot})
            if key not in identities:
                raise ValueError(MESSAGE)
            return copy.deepcopy(identities[key])

        extra = {k: copy.deepcopy(context[k]) for k in (
            'action_resource_compilations', 'equipment_evidence', 'equipment_requirements')}
        if admission:
            extra['action_admission_compilations'] = copy.deepcopy(
                context['action_admission_compilations'])
        extra['cast_identity_provider'] = identity_provider
    if contact:
        extra.update({k: copy.deepcopy(context[k]) for k in
                      ('periodic_compilations', 'elemental_compilations', 'reaction_compilations')})
    from .mechanism_sources import _compile_historical_mechanism_replay
    model, rebuilt, _ = _compile_historical_mechanism_replay(copy.deepcopy(context["plan"]), copy.deepcopy(context["template"]), None,
        action_event_compilations=copy.deepcopy(context["action_event_compilations"]),
        attack_metadata_compilations=copy.deepcopy(context["attack_metadata_compilations"]),
        parameter_compilations=copy.deepcopy(context["parameter_compilations"]),
        missing=lambda *args: missing.append(args), source_provider=source_provider, **extra)
    compared = rebuilt
    if pre_current_guard:
        # 0.42.0 predates the D1 governance projection stored on each
        # compiler row.  Its already-frozen receipt is still verified through
        # the private replay authority, but only these four known descriptive
        # fields may be projected away.  This does not restore a production
        # knowledge-to-execution path.
        added = {
            "governance_profile", "execution_authority",
            "candidate_fingerprint", "required_materialization",
        }
        compared = copy.deepcopy(rebuilt)
        if len(compared) != len(records):
            raise ValueError(MESSAGE)
        for current, saved in zip(compared, records, strict=True):
            if (
                set(current) - set(saved) != added
                or set(saved) - set(current)
                or current.get("execution_authority") != "legacy_app_compiler"
                or current.get("required_materialization") is not None
            ):
                raise ValueError(MESSAGE)
            for field in added:
                current.pop(field)
    if missing or model is None or seen != set(by_id) or not _same(compared, records):
        raise ValueError(MESSAGE)
    return model


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(MESSAGE)
    return value


def _equal(a, b):
    if not math.isclose(_number(a), _number(b), rel_tol=1e-12, abs_tol=1e-8):
        raise ValueError(MESSAGE)


def _arithmetic(result, execution, records):
    """Verify recorded transitions/expressions; do not redispatch game events."""
    from genshin_engine.registry import BENEFIT_TYPES
    rules = {r["id"]: r for record in records for r in record["rules"]}
    if any("eligibility" in r["beneficiaries"] for r in rules.values()):
        raise ValueError("自动机制来源回执尚未批准受益者资格契约")
    characters = {c["id"]: c["stats"] for c in execution["template"]["characters"]}
    all_rules = {r["id"]: r for r in execution["model"]["rules"]}
    buffs = {b["id"]: b for b in execution["template"].get("buffs", [])}
    remaining, activations, quotas, effects = {}, {}, {}, {}
    for row in result.get("mechanics_trace", []):
        rid = row.get("rule_id")
        if rid not in rules:
            continue
        rule, kind = rules[rid], row["type"]
        if kind == "activation":
            beneficiaries = [rule["owner"]] if rule["beneficiaries"]["mode"] == "self" else list(characters)
            if len(row["beneficiaries"]) != len(beneficiaries) or set(row["beneficiaries"]) != set(beneficiaries) or type(row["layers"]) is not int or row["layers"] != 1:
                raise ValueError(MESSAGE)
            if len(row["expires_at"]) != 1:
                raise ValueError(MESSAGE)
            _equal(row["expires_at"][0], _number(row["t"]) + rule["timing"]["duration"])
            q = rule.get("quota")
            expected = {} if not q else dict.fromkeys(beneficiaries if q["scope"] == "per_beneficiary" else ["team"], q["capacity"])
            if not _same(row["quota"], expected):
                raise ValueError(MESSAGE)
            remaining[(rid, row["instance_id"])] = expected
            activations.setdefault((rid, row["instance_id"]), []).append(row)
        elif kind == "expiry":
            remaining.pop((rid, row["instance_id"]), None)
        elif kind == "quota":
            key = (rid, row["cycle"], row["event_id"], row["instance_id"])
            pool = remaining.get((rid, row["instance_id"]), {})
            before = pool.get(row["pool"])
            if (key in quotas or type(before) is not int or type(row["before"]) is not int
                    or row["before"] != before or type(row["after"]) is not int
                    or row["after"] != max(0, before - 1) or row["consumed"] is not (before > 0)):
                raise ValueError(MESSAGE)
            pool[row["pool"]] = row["after"]
            quotas[key] = row
        elif kind == "effect_applied":
            key = (rid, row["cycle"], row["event_id"], row["projected_effect_id"])
            effect = next(e for e in rule["effects"] if e["id"] == row["effect_id"])
            if (key in effects or row["source_id"] != rule["source_id"] or row["domain"] != effect["domain"]
                    or type(row["layers"]) is not int or row["layers"] != 1
                    or (rid, row["instance_id"]) not in activations):
                raise ValueError(MESSAGE)
            if rule.get("quota") and not quotas.get((rid, row["cycle"], row["event_id"], row["instance_id"]), {}).get("consumed"):
                raise ValueError(MESSAGE)
            if type(effect["value"]) in (int, float):
                _equal(row["value"], effect["value"])
            effects[key] = (row, effect)
    sampled = set()
    for sample in result.get("sampling_trace", []):
        rid = sample.get("rule_id")
        if rid not in rules:
            continue
        key = (rid, sample["cycle"], sample["event_id"], sample["projected_effect_id"])
        row, effect = effects[key]
        expr = effect["value"]
        if (key in sampled or type(expr) is not dict or not _same(sample["expression"], expr)
                or sample["character"] != rules[rid]["owner"] or sample["stat"] != expr["stat"]
                or sample["domain"] != effect["domain"] or sample["effect_id"] != effect["id"]):
            raise ValueError(MESSAGE)
        sampled.add(key)
        panel = characters[sample["character"]]
        stat = expr["stat"]
        _equal(sample["base_value"], panel[stat])
        value = _number(panel[stat])
        for dep in sample["dependencies"]:
            if "rule_id" in dep:
                drule = all_rules[dep["rule_id"]]
                deffect = next(e for e in drule["effects"] if e["id"] == dep["effect_id"])
                info = BENEFIT_TYPES[deffect["benefit_type_id"]]
                if type(dep["layers"]) is not int or not 1 <= dep["layers"] <= drule["stacking"]["max"]:
                    raise ValueError(MESSAGE)
                _equal(dep["value"], _number(deffect["value"]) * dep["layers"])
                if dep["zone"] != info["zone"] or dep["stat"] != info["stat"]:
                    raise ValueError(MESSAGE)
            elif "buff_id" in dep:
                deffect = next(e for e in buffs[dep["buff_id"]]["effects"] if e["id"] == dep["effect_id"])
                _equal(dep["value"], deffect["value"])
                if dep["zone"] != deffect["zone"] or dep["stat"] != deffect["stat"]:
                    raise ValueError(MESSAGE)
            elif "energy_window_id" not in dep:
                raise ValueError(MESSAGE)
            if dep["zone"] == "stat_add" and dep["stat"] == stat or dep["zone"] == "em" and stat == "em":
                value += _number(dep["value"])
            elif dep["zone"] == "stat_pct" and dep["stat"] == stat:
                value += _number(panel["base_" + stat]) * _number(dep["value"])
        _equal(sample["source_value"], value)
        calculated = value * expr["factor"] + expr.get("add", 0)
        calculated = min(expr.get("max", float("inf")), max(expr.get("min", float("-inf")), calculated))
        _equal(sample["value"], calculated)
        _equal(sample["value"], row["value"])
        _equal(sample["t"], row["t"])
        _equal(sample["sample_t"], row["sample_t"])
    if sampled != {key for key, (_, effect) in effects.items() if type(effect["value"]) is dict}:
        raise ValueError(MESSAGE)


@verification_scope
def validate_mechanism_result_receipt(result, receipt, *, _session=None):
    records = result.get("mechanism_compilations") or []
    proof = receipt.get("mechanisms")
    context = result.get("mechanism_replay_context")
    execution = result.get("mechanism_execution")
    if not (records or proof or context or execution or result.get("mechanism_summary") or _residue(result)):
        return
    try:
        if not records or type(proof) is not dict or proof.get("compiler") != _compiler(records):
            raise ValueError(MESSAGE)
        from .mechanism_consumption_receipt import _pre_current_guard_shape
        legacy_consumption = _pre_current_guard_shape(result, receipt)
        model = _replay(
            records, context, pre_current_guard=legacy_consumption,
        )
        _bounded(records, context, execution)
        contact = _compiler(records) == CONTACT_COMPILER
        execution_keys = {"model", "template", "rotation_model", "matches"} if contact else {"model", "template"}
        if type(execution) is not dict or set(execution) != execution_keys:
            raise ValueError(MESSAGE)
        generated = [rule for record in records for rule in record["rules"]]
        initials = [rule for record in records for rule in record["initial_rules"]]
        for actual_model in (model, execution["model"]):
            actual_rules = [r for r in actual_model["rules"] if str(r["id"]).startswith(PREFIX)]
            actual_initials = [r for r in actual_model["initial_state"]["rules"] if _own(r)]
            if not _same(sorted(actual_rules, key=lambda r: r["id"]), sorted(generated, key=lambda r: r["id"])) or not _same(
                    sorted(actual_initials, key=lambda r: r["rule_id"]), sorted(initials, key=lambda r: r["rule_id"])):
                raise ValueError(MESSAGE)
        expected = {"input_sources_fingerprint": fingerprint(context["plan"].get("mechanism_sources", [])),
            "compilations_fingerprint": fingerprint(records), "replay_context_fingerprint": fingerprint(context),
            "compiled_mechanics_model_fingerprint": fingerprint(execution["model"]),
            "mechanics_input_template_fingerprint": fingerprint(execution["template"]),
            "execution_rotation_model_fingerprint": fingerprint(result["rotation_check"]["resolved_rotation_model"]),
            "ledger_fingerprint": mechanism_ledger_fingerprint(result), "summary_fingerprint": fingerprint(mechanism_summary(result))}
        if not legacy_consumption:
            expected["consumption_ledger_contract"] = CONSUMPTION_LEDGER_CONTRACT
        admission = _requires_action_admission(records)
        if admission:
            expected['action_admission_execution_fingerprint'] = fingerprint(
                result['rotation_check']['action_admission_execution'])
        elif 'action_admission_execution_fingerprint' in proof:
            raise ValueError(MESSAGE)
        if (any(proof.get(k) != v for k, v in expected.items()) or not _same(result["mechanism_summary"], mechanism_summary(result))
                or receipt.get("result_fingerprint") != fingerprint(result)):
            raise ValueError(MESSAGE)
        parent = receipt.get("action_events") or {}
        if _compiler(records) == CAST_COMPILER or admission:
            from .mechanism_cast_receipt import verify_cast_execution
            verify_cast_execution(context, model, execution, result, records=records)
        if (parent or any(r['compiler'] == COMPILER for r in records)) and any(parent.get(k) != proof[k] for k in (
                "compiled_mechanics_model_fingerprint", "execution_rotation_model_fingerprint", "mechanics_input_template_fingerprint")):
            raise ValueError(MESSAGE)
        for key, result_key in (("action_event_compilations", "action_event_compilations"),
                                ("attack_metadata_compilations", "attack_metadata_compilations"),
                                ("parameter_compilations", "hit_parameter_sources")):
            if not _same(context[key], result.get(result_key, [])):
                raise ValueError(MESSAGE)
        if _compiler(records) in (CAST_COMPILER, CONTACT_COMPILER) and not _same(context['action_resource_compilations'], result.get('action_resource_compilations', [])):
            raise ValueError(MESSAGE)
        if admission and not _same(context['action_admission_compilations'],
                                   result.get('action_admission_compilations', [])):
            raise ValueError(MESSAGE)
        if contact:
            if not _same(context['periodic_compilations'], result.get('periodic_attack_compilations', [])):
                raise ValueError(MESSAGE)
            for key, result_key in (('elemental_compilations', 'source_compilations'),
                                    ('reaction_compilations', 'reaction_damage_source_compilations')):
                if not _same(context[key], result.get('elemental', {}).get(result_key, [])):
                    raise ValueError(MESSAGE)
            from .mechanism_contact_receipt import verify_contact_execution
            verify_contact_execution(context, model, execution, result, receipt, records, _session=_session)
        else:
            _arithmetic(result, execution, records)
            from .mechanism_consumption_receipt import verify_mechanism_consumption
            verify_mechanism_consumption(context, execution, result, receipt, _session=_session)
    except (KeyError, TypeError, AttributeError, IndexError, RecursionError, OverflowError, StopIteration) as exc:
        raise ValueError(MESSAGE) from exc


@verification_scope
def validate_mechanism_receipt(plan, result, report, receipt, *, _session=None):
    if not (plan.get("mechanism_sources") or report.get("mechanism_compilations") or report.get("mechanism_replay_context")
            or result.get("mechanism_compilations") or result.get("mechanism_execution") or receipt.get("mechanisms") or _residue(result)):
        return
    validate_mechanism_result_receipt(result, receipt, _session=_session)
    try:
        proof = receipt["mechanisms"]
        if (not _same(report["mechanism_compilations"], result["mechanism_compilations"])
                or not _same(report["mechanism_replay_context"], result["mechanism_replay_context"])
                or fingerprint(plan.get("mechanism_sources", [])) != proof["input_sources_fingerprint"]
                or fingerprint(input_view(plan)) != receipt["input_fingerprint"]
                or fingerprint(report["mechanics_model"]) != proof["compiled_mechanics_model_fingerprint"]
                or fingerprint(report["mechanics_input_template"]) != proof["mechanics_input_template_fingerprint"]
                or fingerprint(report["plan"]["resolved_template"]) != receipt["output_fingerprint"]):
            raise ValueError(MESSAGE)
        if _compiler(result['mechanism_compilations']) == CONTACT_COMPILER and not _same(
                report['mechanics_matches'], result['mechanism_execution']['matches']):
            raise ValueError(MESSAGE)
        if _compiler(result['mechanism_compilations']) != CONTACT_COMPILER:
            from .mechanism_contact_receipt import _matches
            if not _same(report['mechanics_matches'], _matches(
                    result['mechanism_replay_context']['plan'], result['mechanism_execution']['model'])):
                raise ValueError(MESSAGE)
    except (KeyError, TypeError, AttributeError, IndexError) as exc:
        raise ValueError(MESSAGE) from exc
