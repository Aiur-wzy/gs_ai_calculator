"""Offline source reconstruction and arithmetic checks, not a second simulator."""
from __future__ import annotations

import copy
import json
import math

from .files import fingerprint, input_view
from .limited_hit_benefit_schema import COMPILER, LIMITS, PREFIX

MESSAGE = "有限次数收益的来源、激活、次数、攻击力取样或伤害回执不一致，请重新计算。"
MAX_LEDGER_BYTES = LIMITS["max_ledger_bytes"]


def _own(row):
    return str(row.get("rule_id", "")).startswith(PREFIX)


def _residue(result):
    if any(_own(row) for field in ("mechanics_trace", "sampling_trace") for row in result.get(field, [])):
        return True
    if any(str(ref.get("effect_id", "")).startswith(PREFIX)
           for hit in result.get("hits", []) for ref in hit.get("matched_effect_refs", [])):
        return True
    return any(PREFIX in str(row.get("id", "")) or _own(row)
               for row in (result.get("rotation_check") or {}).get("event_log", []))


def _ledger_view(result):
    return {"mechanics": [row for row in result.get("mechanics_trace", []) if _own(row)],
            "sampling": [row for row in result.get("sampling_trace", []) if _own(row)],
            "hits": result.get("hits", [])}


def limited_hit_benefit_ledger_fingerprint(result):
    view = _ledger_view(result)
    if len(json.dumps(view, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf8")) > MAX_LEDGER_BYTES:
        raise ValueError("有限次数收益完整流水超过8MiB；未截断交付，请拆分输入窗口。")
    return fingerprint(view)


def limited_hit_benefit_summary(result):
    rows = [r for r in result.get("mechanics_trace", []) if _own(r)]
    return {"activations": sum(r["type"] == "activation" for r in rows),
            "consumed_hits": sum(r["type"] == "quota" and r["consumed"] for r in rows),
            "exhausted_hits": sum(r["type"] == "quota" and not r["consumed"] for r in rows),
            "applied_benefits": sum(r["type"] == "effect_applied" for r in rows),
            "sampled_hits": sum(_own(r) for r in result.get("sampling_trace", [])),
            "consumed_by_character": {cid: sum(r["type"] == "quota" and r.get("pool") == cid and r["consumed"] for r in rows)
                for cid in sorted({r["pool"] for r in rows if r["type"] == "quota"})},
            "whole_source_status": "partial", "identity_coverage": "explicit_typed_sources_and_declared_no_external_changes"}


def make_limited_hit_benefit_receipt(plan, report):
    return {"compiler": COMPILER,
        "input_sources_fingerprint": fingerprint(plan.get("limited_hit_benefit_sources", [])),
        "compilations_fingerprint": fingerprint(report.get("limited_hit_benefit_compilations", [])),
        "compiled_mechanics_model_fingerprint": fingerprint(report["mechanics_model"]),
        "execution_rotation_model_fingerprint": fingerprint(report["action_event_execution_rotation_model"]),
        "summary_fingerprint": fingerprint(report["limited_hit_benefit_summary"]),
        "ledger_fingerprint": report["limited_hit_benefit_ledger_fingerprint"]}


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(MESSAGE)
    return value


def _equal(a, b):
    if not math.isclose(_number(a), _number(b), rel_tol=1e-12, abs_tol=1e-8):
        raise ValueError(MESSAGE)


def _record(record):
    from genshin_engine.runtime_limited_hit_benefit_compiler import (
        compile_limited_hit_benefit_source,
    )

    from .limited_hit_benefit_sources import _delivery
    if record.get("status") != "ready" or record.get("compiler") != COMPILER or not record.get("parent_links"):
        raise ValueError(MESSAGE)
    projection = compile_limited_hit_benefit_source(record["source_bundle"], parent_semantics=record["parent_semantics"],
        scenario=record["scenario"], calibration=record["calibration"])
    if projection["status"] != "ready" or record["projection"] != projection:
        raise ValueError(MESSAGE)
    for key in ("source_fingerprint", "source_status", "benefit_status", "benefit_fingerprint", "benefit_certificate", "semantic_dependency"):
        if record.get(key) != projection.get(key):
            raise ValueError(MESSAGE)
    identity = record["parent_identity"]
    if (record["source_bundle"]["snapshot"]["snapshot_id"] != identity["snapshot_id"]
            or record["source_bundle"]["snapshot"]["version_line"] != identity["version_line"]
            or identity["selection"] != record["parent_semantics"]["selection"]
            or identity["param_index"] != record["parent_semantics"]["param_index"]
            or any(record["scope"][key] != record[key] for key in ("actor", "parent_identity", "parent_links"))):
        raise ValueError(MESSAGE)
    acceptance = fingerprint({"compiler": COMPILER, "benefit_fingerprint": projection["benefit_fingerprint"]})
    if (record["accepted_fingerprint"] != acceptance or record["acceptance_fingerprint"] != acceptance
            or record["required_confirmation"] != {"accepted_fingerprint": acceptance}):
        raise ValueError(MESSAGE)
    stage = _delivery({"id": record["id"]}, record["actor"], record["parent_links"], projection)
    if (any(record.get(key) != value for key, value in stage.items())
            or record["execution_fingerprint"] != fingerprint({"scope": record["scope"], "delivery": stage})):
        raise ValueError(MESSAGE)


def _consumers(record):
    rows = {}
    for consumer in record["scope"]["consumers"]:
        descriptor = consumer["descriptor"]
        ids = ([consumer["hit_id"]] if consumer["kind"] == "hit_events" else
               [descriptor["id"] + ":" + str(i + 1) for i in range(len(descriptor["offsets"]))])
        for eid in ids:
            if eid in rows:
                raise ValueError(MESSAGE)
            rows[eid] = consumer
    return rows


def _arithmetic(result, record):
    """Check recorded transitions and multiplications; do not dispatch gameplay."""
    from .limited_hit_benefit_sources import _ticks
    rid = record["rules"][0]["id"]
    certificate = record["benefit_certificate"]
    capacity, duration = certificate["capacity"], _ticks(certificate["duration_seconds"])
    consumers = _consumers(record)
    check = result["rotation_check"]
    events = {row["run_id"]: row for row in check["event_log"]}
    if len(events) != len(check["event_log"]):
        raise ValueError(MESSAGE)
    characters = {row["id"] for row in check["resolved_rotation_model"]["characters"]}
    links = {link["action_id"]: link for link in record["parent_links"]}
    remaining, expires, instance = {}, -1, None
    quotas, applied, activations = {}, {}, set()
    for row in result.get("mechanics_trace", []):
        own = row.get("rule_id") == rid
        kind = row["type"]
        key = (row.get("cycle", 0), row.get("event_id"))
        tick = _ticks(row["t"])
        if own and kind == "activation":
            link = links[row["event_id"]]
            action = events.get("0:" + link["action_id"])
            if (row["cycle"] != 0 or row["event_id"] in activations or not action or action["status"] != "passed"
                    or _ticks(action["t"]) != link["action_tick"] or tick != link["activation_tick"]
                    or set(row["beneficiaries"]) != characters or len(row["beneficiaries"]) != len(characters)
                    or row["quota"] != dict.fromkeys(characters, capacity) or row["layers"] != 1
                    or row["expires_at"] != [(tick + duration) / 1000000]):
                raise ValueError(MESSAGE)
            activations.add(row["event_id"])
            remaining, expires, instance = dict(row["quota"]), tick + duration, row["instance_id"]
        elif own and kind == "expiry":
            if row["instance_id"] != instance or tick != expires or row["layers_before"] != 1 or row["layers_after"] != 0:
                raise ValueError(MESSAGE)
            remaining, instance = {}, None
        elif own and kind == "quota":
            consumer = consumers[row["event_id"]]
            parent = events.get(f"{row['cycle']}:{row['event_id']}")
            pool = row["pool"]
            if (key in quotas or not parent or parent["status"] != "passed" or row["instance_id"] != instance
                    or tick >= expires or pool != consumer["character"] or pool not in remaining
                    or type(row["consumed"]) is not bool or row["consumed"] != (remaining[pool] > 0)
                    or type(row["before"]) is not int or row["before"] != remaining[pool]
                    or type(row["after"]) is not int or row["after"] != max(0, remaining[pool] - 1)
                    or row["reported_damage"] != consumer["descriptor"].get("report", True)):
                raise ValueError(MESSAGE)
            _equal(row["t"], parent["t"])
            remaining[pool] = row["after"]
            quotas[key] = row
        elif own and kind == "effect_applied":
            quota = quotas.get(key)
            if (key in applied or not quota or not quota["consumed"] or row["instance_id"] != instance
                    or row["layers"] != 1 or row["domain"] != "provider"
                    or row["source_id"] != record["rules"][0]["source_id"]
                    or row["effect_id"] != record["rules"][0]["effects"][0]["id"]):
                raise ValueError(MESSAGE)
            _equal(row["sample_t"], row["t"])
            applied[key] = row
        elif kind == "hit" and row["event_id"] in consumers:
            # A real hit is logged after its quota/effect decisions, including
            # report=false. Expired or missed hits must have no consumption.
            eligible = bool(remaining) and tick < expires
            if eligible != (key in quotas):
                raise ValueError(MESSAGE)
    for aid, link in links.items():
        action = events.get("0:" + aid)
        if action and action["status"] == "passed" and aid not in activations:
            delayed = events.get("0:" + aid + "~" + rid + ":activate")
            if link["activation_tick"] == link["action_tick"] or delayed and delayed["status"] == "passed":
                raise ValueError(MESSAGE)
    samples = [row for row in result.get("sampling_trace", []) if row.get("rule_id") == rid]
    if len(samples) != len(applied):
        raise ValueError(MESSAGE)
    seen = set()
    for sample in samples:
        key = (sample["cycle"], sample["event_id"])
        effect = applied[key]
        if (key in seen or sample["expression"] != record["rules"][0]["effects"][0]["value"]
                or sample["character"] != record["actor"] or sample["stat"] != "atk" or sample["domain"] != "provider"
                or sample["projected_effect_id"] != effect["projected_effect_id"]):
            raise ValueError(MESSAGE)
        seen.add(key)
        basis = record["scope"]["provider_stats"]
        _equal(sample["base_value"], basis["atk"])
        current = _number(basis["atk"])
        for dependency in sample["dependencies"]:
            # Other ordinary stats may share the panel dependency trace.
            if dependency["stat"] == "atk" and dependency["zone"] in ("stat_add", "stat_pct"):
                value = _number(dependency["value"])
                current += value if dependency["zone"] == "stat_add" else _number(basis["base_atk"]) * value
        _equal(sample["source_value"], current)
        _equal(sample["value"], current * certificate["coefficient"])
        _equal(sample["value"], effect["value"])
        _equal(sample["sample_t"], effect["t"])
        _equal(sample["t"], effect["t"])
    for hit in result["hits"]:
        key = (0, hit["id"])
        refs = [ref for ref in hit.get("matched_effect_refs", []) if str(ref["effect_id"]).startswith(rid + ":")]
        effect = applied.get(key)
        if bool(refs) != (effect is not None):
            raise ValueError(MESSAGE)
        if effect and (len(refs) != 1 or refs[0]["effect_id"] != effect["projected_effect_id"]
                       or refs[0]["benefit_type_id"] != "benefit.base_add.all"):
            raise ValueError(MESSAGE)


def validate_limited_hit_benefit_result_receipt(result, receipt):
    records, stored = result.get("limited_hit_benefit_compilations") or [], receipt.get("limited_hit_benefit")
    if not (records or stored or result.get("limited_hit_benefit_summary") or _residue(result)):
        return
    try:
        if (not records or len(records) > LIMITS["max_sources"] or not isinstance(stored, dict)
                or stored.get("compiler") != COMPILER or stored["compilations_fingerprint"] != fingerprint(records)
                or receipt.get("result_fingerprint") != fingerprint(result)
                or len(json.dumps(records, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf8")) > LIMITS["max_receipt_bytes"]
                or stored["ledger_fingerprint"] != limited_hit_benefit_ledger_fingerprint(result)):
            raise ValueError(MESSAGE)
        for key in ("compiled_mechanics_model_fingerprint", "execution_rotation_model_fingerprint"):
            if stored[key] != receipt["action_events"][key]:
                raise ValueError(MESSAGE)
        if stored["execution_rotation_model_fingerprint"] != fingerprint(result["rotation_check"]["resolved_rotation_model"]):
            raise ValueError(MESSAGE)
        for record in records:
            _record(record)
            parent = [row for row in result.get("action_event_compilations", []) if row["profile_id"] == record["action_event_profile_id"]]
            if (len(parent) != 1 or parent[0]["status"] != "ready"
                    or any(parent[0][key] != record["parent_semantics"][key] for key in ("profile_fingerprint", "source_dependency_fingerprint"))
                    or sorted(parent[0]["generated"], key=lambda row: row["hit_id"]) != sorted([
                        {k: v for k, v in link.items() if k not in ("action_tick", "activation_tick", "absolute_tick", "action_order", "source_cycle", "pattern_fingerprint")}
                        for link in record["parent_links"]], key=lambda row: row["hit_id"])):
                raise ValueError(MESSAGE)
            _arithmetic(result, record)
        summary = limited_hit_benefit_summary(result)
        if summary != result["limited_hit_benefit_summary"] or stored["summary_fingerprint"] != fingerprint(summary):
            raise ValueError(MESSAGE)
    except (KeyError, TypeError, AttributeError, IndexError, ArithmeticError, RecursionError) as exc:
        raise ValueError(MESSAGE) from exc


def validate_limited_hit_benefit_receipt(plan, result, report, receipt):
    records = report.get("limited_hit_benefit_compilations") or []
    model = report.get("mechanics_model") or {}
    if not (plan.get("limited_hit_benefit_sources") or records or receipt.get("limited_hit_benefit")
            or result.get("limited_hit_benefit_compilations") or _residue(result)
            or any(r["id"].startswith(PREFIX) for r in model.get("rules", []))):
        return
    try:
        validate_limited_hit_benefit_result_receipt(result, receipt)
        if (not records or records != result.get("limited_hit_benefit_compilations")
                or receipt.get("input_fingerprint") != fingerprint(input_view(plan))
                or receipt.get("limited_hit_benefit") != make_limited_hit_benefit_receipt(plan, report)):
            raise ValueError(MESSAGE)
        replay = copy.deepcopy(plan)
        replay["mechanics_model"] = copy.deepcopy(model)
        replay["rotation_model"] = copy.deepcopy(report["action_event_execution_rotation_model"])
        replay["mechanics_model"]["rules"] = [r for r in model["rules"] if not r["id"].startswith(PREFIX)]
        replay["mechanics_model"]["initial_state"]["rules"] = [r for r in model["initial_state"]["rules"] if not r["rule_id"].startswith(PREFIX)]
        originals = {r["id"]: r for r in records}
        problems = []
        from .limited_hit_benefit_sources import compile_limited_hit_benefit_sources
        rebuilt, rows, _ = compile_limited_hit_benefit_sources(replay, report["mechanics_input_template"], None,
            action_event_compilations=report.get("action_event_compilations", []),
            attack_metadata_compilations=report.get("attack_metadata_compilations", []),
            parameter_compilations=report.get("hit_parameter_compilations", []),
            missing=lambda *args: problems.append(args), source_provider=lambda binding, identity: originals[binding["id"]]["source_bundle"])
        if problems or rows != records or rebuilt != model:
            raise ValueError(MESSAGE)
        # Bind each sampled projection to the actual numerical input exported by
        # D20. This does not rerun its event/quota state machine.
        buffs = {buff["id"]: buff for buff in report["plan"]["resolved_template"].get("buffs", [])}
        for row in result.get("mechanics_trace", []):
            if not _own(row) or row["type"] != "effect_applied" or row["cycle"] != 0:
                continue
            if not any(h["id"] == row["event_id"] for h in result["hits"]):
                continue
            buff = buffs["mechanics:" + row["event_id"]]
            effects = [e for e in buff["effects"] if e["id"] == row["projected_effect_id"]]
            if len(effects) != 1 or effects[0]["benefit_type_id"] != "benefit.base_add.all":
                raise ValueError(MESSAGE)
            _equal(effects[0]["value"], row["value"])
    except (KeyError, TypeError, AttributeError, IndexError, RecursionError) as exc:
        raise ValueError(MESSAGE) from exc
