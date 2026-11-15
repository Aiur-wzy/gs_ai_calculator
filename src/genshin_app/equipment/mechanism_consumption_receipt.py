"""Check consumed benefits against the real queue, including derived reactions."""
from __future__ import annotations

from genshin_app.rotation_check import resolve_rotation_model

from .files import fingerprint
from .mechanism_contact_receipt import _matches
from .mechanism_execution_scope import need
from .snapshot_capture_receipt import _ledger
from .verification_scope import verification_scope

_PRE_CURRENT_GUARD_ENGINE_VERSION = "0.42.0"
_PRE_CURRENT_GUARD_APPLICATION_FIELDS = (
    "profile_status",
    "area_id",
    "atomic_parent_id",
    "application_packet_size",
    "direct_hit",
    "counts_toward_stellar_superconduct",
    "application_attempted",
    "application_allowed",
    "reaction_occurred",
    "icd_audit",
)
_PRE_CURRENT_GUARD_PROOF_FIELDS = {
    "compiler",
    "input_sources_fingerprint",
    "compilations_fingerprint",
    "replay_context_fingerprint",
    "compiled_mechanics_model_fingerprint",
    "mechanics_input_template_fingerprint",
    "execution_rotation_model_fingerprint",
    "ledger_fingerprint",
    "summary_fingerprint",
}


def _pre_current_guard_shape(result, receipt):
    proof = receipt.get("mechanisms")
    if (receipt.get("engine_version") != _PRE_CURRENT_GUARD_ENGINE_VERSION
            or type(proof) is not dict or set(proof) != _PRE_CURRENT_GUARD_PROOF_FIELDS):
        return False
    hits = result.get("hits")
    if type(hits) is not list or not hits or any(type(row) is not dict or "result_weight" in row for row in hits):
        return False
    application_rows = [row for rows in (result.get("mechanics_trace"), result.get("elemental", {}).get("trace"))
                        if type(rows) is list for row in rows
                        if type(row) is dict and row.get("type") == "elemental_application"]
    if (not application_rows or any(any(field in row for field in _PRE_CURRENT_GUARD_APPLICATION_FIELDS)
                                    for row in application_rows)):
        return False
    events = (result.get("rotation_check") or {}).get("event_log")
    deadlines = ([row for row in events if type(row) is dict and row.get("type") == "elemental_state_deadline"]
                 if type(events) is list else [])
    return bool(deadlines) and all(row.get("status") == "passed" for row in deadlines)


def _project_pre_current_guard_ledger(expected, actual, legacy):
    """Project a current replay onto the exact 0.42 saved-result vocabulary.

    Version 0.42 predates result weights and the application audit envelope.  It
    also recorded an executed elemental-state deadline as ``passed``.  Only the
    current replay is projected; the saved ledger remains intact and continues
    to be pinned by both its mechanism-ledger and whole-result fingerprints.
    """
    if not legacy:
        return expected

    expected_hits, actual_hits = expected.get("hits"), actual.get("hits")
    if type(expected_hits) is list and type(actual_hits) is list and len(expected_hits) == len(actual_hits):
        for replayed, saved in zip(expected_hits, actual_hits, strict=True):
            if (type(replayed) is dict and type(saved) is dict
                    and "result_weight" not in saved and replayed.get("result_weight") == 1.0):
                replayed.pop("result_weight")

    expected_trace, actual_trace = expected.get("mechanics_trace"), actual.get("mechanics_trace")
    if type(expected_trace) is list and type(actual_trace) is list and len(expected_trace) == len(actual_trace):
        for replayed, saved in zip(expected_trace, actual_trace, strict=True):
            if (type(replayed) is not dict or type(saved) is not dict
                    or replayed.get("type") != "elemental_application"
                    or saved.get("type") != "elemental_application"):
                continue
            for field in _PRE_CURRENT_GUARD_APPLICATION_FIELDS:
                if field not in saved:
                    replayed.pop(field, None)

    expected_check, actual_check = expected.get("rotation_check"), actual.get("rotation_check")
    expected_events = expected_check.get("event_log") if type(expected_check) is dict else None
    actual_events = actual_check.get("event_log") if type(actual_check) is dict else None
    if type(expected_events) is list and type(actual_events) is list and len(expected_events) == len(actual_events):
        for replayed, saved in zip(expected_events, actual_events, strict=True):
            if (type(replayed) is dict and type(saved) is dict
                    and replayed.get("type") == saved.get("type") == "elemental_state_deadline"
                    and replayed.get("id") == saved.get("id")
                    and replayed.get("run_id") == saved.get("run_id")
                    and replayed.get("status") == "executed" and saved.get("status") == "passed"):
                replayed["status"] = "passed"
    return expected


def _project_pre_current_guard_elemental(expected, actual, legacy):
    if not legacy or type(expected) is not dict or type(actual) is not dict:
        return expected
    expected_trace, actual_trace = expected.get("trace"), actual.get("trace")
    if type(expected_trace) is list and type(actual_trace) is list and len(expected_trace) == len(actual_trace):
        for replayed, saved in zip(expected_trace, actual_trace, strict=True):
            if (type(replayed) is not dict or type(saved) is not dict
                    or replayed.get("type") != "elemental_application"
                    or saved.get("type") != "elemental_application"):
                continue
            for field in _PRE_CURRENT_GUARD_APPLICATION_FIELDS:
                if field not in saved:
                    replayed.pop(field, None)
    return expected


@verification_scope
def verify_mechanism_consumption(context, execution, result, receipt, *, _session=None):
    # V1/V2 saved only template/model. Rebuild the declared selections with the
    # same fixed-first/rule-second ordering as prepare_mechanics. Template buff
    # selections live in the saved input template, not the executor matches.
    rotation, _ = resolve_rotation_model(execution['template'], context['plan']['rotation_model'])
    need(fingerprint(rotation) == fingerprint(result['rotation_check']['resolved_rotation_model']),
         '通用机制的实际执行轴与保存的输入不一致')
    inputs = {'template': execution['template'], 'rotation_model': rotation,
              'mechanics_model': execution['model'], 'matches': _matches(context['plan'], execution['model'])}
    need(fingerprint(inputs) == result['rotation_check']['input_fingerprint'],
         '通用机制的完整求值输入或收益关联不一致')
    replay = _session.replay(inputs)
    expected = replay.get('result')
    need(expected is not None and replay.get('resolved_template') is not None,
         '通用机制的保存输入无法重建实际求值')
    # Older result-only receipts have no outer output pin. Their full hit,
    # transition and sampling ledgers are still checked below; full deliveries
    # additionally bind the exact resolved template through this existing pin.
    if 'output_fingerprint' in receipt:
        from .replay_numeric_projection import replay_output_template
        need(fingerprint(replay_output_template(replay['resolved_template'], result, receipt))
             == receipt['output_fingerprint'],
             '通用机制的实际输出模板不一致')
    if not expected.get('mechanics', {}).get('complete_first_cycle', False):
        expected['summary']['dps'] = None
    expected.update(rotation_check=replay['rotation_check'], mechanics_trace=replay['mechanics_trace'],
                    sampling_trace=replay.get('sampling_trace', []))
    # These descriptive fields were optional in the original saved-result API.
    # When present they must match, without promoting pending/unproven status.
    if 'mechanics_status' in result:
        expected['mechanics_status'] = replay['status']
    if 'mechanics_issues' in result:
        expected['mechanics_issues'] = replay.get('issues', [])
    # Exact lists retain duplicate, missing, wrong-type and unused references.
    # Formula dependencies (even zero/capped values) come from core execution,
    # not from effect_applied or incomplete result-hit metadata.
    legacy = _pre_current_guard_shape(result, receipt)
    expected_ledger = _ledger(expected)
    actual_ledger = _ledger(result)
    _project_pre_current_guard_ledger(expected_ledger, actual_ledger, legacy)
    from .replay_numeric_projection import replay_numeric_ledgers
    expected_ledger, actual_ledger = replay_numeric_ledgers(expected_ledger, actual_ledger, result, receipt)
    need(fingerprint(expected_ledger) == fingerprint(actual_ledger),
         '通用机制的实际公式消费、取样或执行流水不一致')
    for key in ('damage_reporting', 'fixed_effect_sources', 'target_condition_trace'):
        need(expected.get(key) == result.get(key), '通用机制的命中投影或报表范围不一致')
    actual_elemental = result.get('elemental', {})
    expected_elemental = _project_pre_current_guard_elemental(expected.get('elemental', {}), actual_elemental, legacy)
    for key, value in expected_elemental.items():
        need(value == actual_elemental.get(key), '通用机制的实际反应或附着流水不一致')
