"""Persist one execution request across native and whiteboard representations."""
from __future__ import annotations

import copy

from genshin_app.equipment.schema import PlanError
from genshin_engine.attribute_budget import validate_calculation_options

_ABSENT = object()


def option_request(document):
    if 'calculation_options' not in document:
        return {}
    try:
        return validate_calculation_options(document['calculation_options'])['requested']
    except ValueError as exc:
        raise PlanError(str(exc), 'invalid_calculation_options', {'path':'calculation_options'}) from exc


def synchronize_option_request(previous, candidate):
    """Mirror a single edited view; explicitly conflicting paired edits reject.

    This does not change game inputs, authorize an edit, or refresh a result.
    The caller owns candidate revision and the normal handoff identity update.
    """
    handoff = candidate.get('extensions', {}).get('whiteboard_handoff')
    old_handoff = previous.get('extensions', {}).get('whiteboard_handoff')
    if not isinstance(handoff, dict) or not isinstance(handoff.get('board'), dict):
        return False
    board = handoff['board']
    old_board = old_handoff.get('board', {}) if isinstance(old_handoff, dict) else {}
    old_native = previous.get('calculation_options', _ABSENT)
    old_saved = old_board.get('calculation_options', _ABSENT)
    new_native = candidate.get('calculation_options', _ABSENT)
    new_saved = board.get('calculation_options', _ABSENT)
    native_changed, board_changed = new_native != old_native, new_saved != old_saved
    if native_changed and not board_changed:
        if new_native is _ABSENT:
            board.pop('calculation_options', None)
        else:
            board['calculation_options'] = copy.deepcopy(new_native)
    elif board_changed and not native_changed:
        if new_saved is _ABSENT:
            candidate.pop('calculation_options', None)
        else:
            candidate['calculation_options'] = copy.deepcopy(new_saved)
    if option_request(candidate) != option_request(board):
        raise PlanError('白板与原生计算预算请求不一致；不能静默选择其中一个',
                        'whiteboard_intent_mismatch', {'path':'calculation_options'})
    return native_changed or board_changed
