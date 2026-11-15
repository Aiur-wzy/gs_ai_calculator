"""Compare numerical replay without promoting optional display retention to a rule.

Stored typed proof hashes still cover their original ledgers. This only projects
the independent replay comparison; final result/package readers separately
validate the complete explanation family. No damage or binding is inferred.
"""
from __future__ import annotations

import copy

OBSERVATION_FIELDS = ("action_id", "ability_id", "run_id")
RUNTIME_OBSERVATION_FIELDS = ("execution_ref", "in_input_window", "report_disposition")
LEGACY_PRODUCERS = frozenset({"0.42.0", "0.44.0"})


def legacy_source_vocabulary(result, receipt):
    if receipt.get("engine_version") not in LEGACY_PRODUCERS:
        return False
    saved_hits, trace = result.get("hits"), result.get("mechanics_trace")
    if (not isinstance(saved_hits, list) or any(not isinstance(hit, dict) for hit in saved_hits)
            or not isinstance(trace, list) or any(not isinstance(row, dict) for row in trace)):
        return False
    if (any(key in result for key in ("damage_explanations", "damage_explanation_fingerprint",
                                      "damage_explanation_contract", "calculation_producer_contract",
                                      "attribute_execution_budget"))
            or "damage_explanations" in receipt
            or any("explanation_ref" in hit for hit in result.get("hits", []))):
        return False
    hits = [row for row in result.get("mechanics_trace", [])
            if row.get("type") in ("hit", "reaction_damage")]
    if not hits:
        return False
    for row in hits:
        if any(field in row for field in RUNTIME_OBSERVATION_FIELDS):
            return False
        if row["type"] == "reaction_damage" and "reaction_run_id" in row:
            if "ability_id" in row:
                return False
        elif any(field in row for field in OBSERVATION_FIELDS):
            return False
    return True


def replay_output_template(template, result, receipt):
    """Compare a trusted replay with the original legacy output vocabulary.

    The stored template and its fingerprint remain untouched. Only known old
    producer shapes predate the runtime reference attached to each resolved
    hit; all numerical fields and other extensions remain in the comparison.
    Current results must retain and verify their references exactly.
    """
    if not legacy_source_vocabulary(result, receipt):
        return template
    projected = copy.deepcopy(template)
    for hit in projected.get("hits", []):
        extensions = hit.get("extensions")
        if isinstance(extensions, dict) and "execution_ref" in extensions:
            del extensions["execution_ref"]
            if not extensions:
                del hit["extensions"]
    return projected


def _numeric_hits(ledger):
    if not isinstance(ledger.get("hits"), list):
        return {**ledger}
    return {**ledger, "hits": [{key: value for key, value in row.items() if key != "explanation_ref"}
                              if isinstance(row, dict) else row
                              for row in ledger["hits"]]}


def numeric_budget_result(result):
    """Budget numerical proof material; optional observations have their own cap.

    Hashes/receipts continue to bind the original result, not this accounting
    view. This only accepts a known result/ledger root supplied by its consumer.
    It does not recurse into engine declarations or omit arbitrary fields.
    """
    if not isinstance(result, dict) or "hits" not in result or "summary" not in result:
        return result
    return _numeric_hits({key: value for key, value in result.items() if key not in (
        "damage_explanations", "damage_explanation_fingerprint", "damage_explanation_contract")})


def numeric_budget_hits(hits):
    return _numeric_hits({"hits": hits}).get("hits")


def _legacy_scan_usage(expected, actual, original_trace, projected_trace):
    audit, saved = expected.get("resource_reactions"), actual.get("resource_reactions")
    if (not isinstance(audit, dict) or not isinstance(saved, dict)
            or audit.get("contract") != "ordinary-reaction-resource-v1"
            or saved.get("contract") != "ordinary-reaction-resource-v1"):
        return
    from genshin_engine.resource_reaction_program import _bounded
    def work(rows):
        charges = []
        _bounded(rows, charge_work=charges.append)
        return sum(charges)
    # The unchanged v1 report scanner charges the actual mechanics trace.
    # Re-meter both exact vocabularies, rather than ignoring all work or using
    # a fixture-specific 32/96 constant. Current engineering usage stays real.
    delta = work(original_trace) - work(projected_trace)
    usage, saved_usage = audit.get("usage"), saved.get("usage")
    if (delta >= 0 and isinstance(usage, dict) and isinstance(saved_usage, dict)
            and type(usage.get("work_consumed")) is int and type(saved_usage.get("work_consumed")) is int
            and usage["work_consumed"] - delta == saved_usage["work_consumed"]):
        expected["resource_reactions"] = {
            **audit, "usage": {**usage, "work_consumed": usage["work_consumed"] - delta}}


def replay_numeric_ledgers(expected, actual, result, receipt):
    # During fresh construction the final external/observation proof has not
    # yet been attached. When a final proof exists, validate it independently;
    # typed replay alone is never advertised as a complete package verifier.
    if "damage_explanations" in receipt:
        from genshin_app.damage_explanation_reporting import (
            validate_damage_explanations,
        )
        validate_damage_explanations(result, receipt)
    expected, actual = _numeric_hits(expected), _numeric_hits(actual)
    if not legacy_source_vocabulary(result, receipt):
        return expected, actual
    left, right = expected.get("mechanics_trace"), actual.get("mechanics_trace")
    if (not isinstance(left, list) or not isinstance(right, list) or len(left) != len(right)
            or any(any(a.get(key) != b.get(key) for key in ("type", "event_id", "cycle", "t"))
                   for a, b in zip(left, right, strict=True))):
        return expected, actual  # A changed event/order/time remains a real mismatch.
    rows = []
    for replayed, saved in zip(left, right, strict=True):
        if saved.get("type") in ("hit", "reaction_damage"):
            replayed = {key: value for key, value in replayed.items()
                        if key not in (*OBSERVATION_FIELDS, *RUNTIME_OBSERVATION_FIELDS) or key in saved}
        rows.append(replayed)
    expected["mechanics_trace"] = rows
    _legacy_scan_usage(expected, actual, left, rows)
    return expected, actual
