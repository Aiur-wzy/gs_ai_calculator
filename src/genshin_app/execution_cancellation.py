"""Project an explicit computation cancellation, separately from program faults."""
from __future__ import annotations

import re
from collections.abc import Mapping

from genshin_engine.execution_control import ExecutionCancelled

_OPERATION = re.compile(r'[A-Za-z][A-Za-z0-9_.:-]{0,127}\Z')
_FIELDS = frozenset({'code', 'reason', 'message', 'failure_kind', 'operation',
                     'retryable', 'details', 'recovery'})


def validate_cancellation_response(value):
    """Validate only the existing explicit execution-cancellation envelope.

    Cancellation is neither a program fault nor permission to accept a result
    or commit an interrupted write. Unknown kinds and mixed fault fields must
    not gain a generic non-system bypass at the portable response boundary.
    """
    if (not isinstance(value, Mapping) or set(value) != _FIELDS
            or type(value.get('code')) is not int or value['code'] != 1
            or value.get('reason') != 'calculation_cancelled'
            or value.get('failure_kind') != 'cancelled' or value.get('retryable') is not True
            or not isinstance(value.get('message'), str) or not 1 <= len(value['message']) <= 4096
            or not isinstance(value.get('operation'), str) or not _OPERATION.fullmatch(value['operation'])):
        raise ValueError('invalid_cancellation_response')
    details = value['details']
    if (not isinstance(details, Mapping) or set(details) != {'code', 'message', 'path', 'severity'}
            or details['code'] != 'calculation_cancelled' or details['path'] != 'execution'
            or details['severity'] != 'cancelled' or details['message'] != value['message']):
        raise ValueError('invalid_cancellation_response')
    recovery = value['recovery']
    if (not isinstance(recovery, Mapping) or set(recovery) != {'action', 'retry_same_request'}
            or recovery['action'] != 'recalculate_fresh' or recovery['retry_same_request'] is not False):
        raise ValueError('invalid_cancellation_response')


def cancellation_response(error, *, operation):
    if not isinstance(error, ExecutionCancelled):
        raise TypeError('only an explicit execution cancellation may use this response')
    response = {'code': 1, 'reason': 'calculation_cancelled', 'message': str(error),
            'failure_kind': 'cancelled', 'operation': operation,
            'retryable': True, 'details': dict(error.issue),
            'recovery': {'action': 'recalculate_fresh', 'retry_same_request': False}}
    validate_cancellation_response(response)
    return response
