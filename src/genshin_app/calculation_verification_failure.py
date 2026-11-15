"""Unsealed capacity diagnostics; never a calculated result or source approval."""
from __future__ import annotations

from .equipment.execution_verification import verification_diagnostic
from .equipment.files import fingerprint


def verification_failure_artifact(error, plan, *, source_path=None, source_file_sha=None):
    diagnostic = verification_diagnostic(error)
    if diagnostic is None:
        return None
    source = {'input_fingerprint': fingerprint(plan)}
    if source_path is not None:
        source['path'] = str(source_path)
    if source_file_sha is not None:
        source['file_sha'] = source_file_sha
    return {'kind': 'genshin-calculation-diagnostic', 'schema_version': 1,
            'sealed': False, 'accepted_result': False, 'source': source,
            'diagnostic': diagnostic}


def verification_failure_response(error, artifact, *, files=None):
    """Keep the real domain reason while returning only bounded failure facts."""
    issue = artifact['diagnostic']['issues'][0]
    details = {'diagnostic': artifact}
    if files is not None:
        details['files'] = files
    return {'code': 1, 'message': str(error),
            'reason': getattr(error, 'reason', None) or issue['code'],
            'path': getattr(error, 'path', None) or issue['path'],
            'details': details}


def verification_failure_view(value):
    """Read saved diagnostics as unverified facts, never validate their authority."""
    from .equipment.schema import PlanError
    artifact, prefix = value, ''
    if value.get('code') == 1 and isinstance(value.get('details'), dict):
        artifact, prefix = value['details'].get('diagnostic'), '/details/diagnostic'
    if (not isinstance(artifact, dict)
            or artifact.get('kind') != 'genshin-calculation-diagnostic'
            or not isinstance(artifact.get('diagnostic'), dict)
            or artifact['diagnostic'].get('contract') != 'execution-verification-diagnostic-v1'):
        return None
    diagnostic = artifact['diagnostic']
    if (artifact.get('sealed') is not False or artifact.get('accepted_result') is not False
            or diagnostic.get('sealed') is not False or diagnostic.get('accepted_result') is not False
            or diagnostic.get('status') != 'verification_incomplete'
            or any(key in obj for obj in (value, artifact, diagnostic)
                   for key in ('result', 'result_receipt', 'calculated', 'summary'))):
        raise PlanError('核验容量诊断不能混入接纳回执或伤害结果。', 'invalid_diagnostic_input')
    return ({'issues': diagnostic.get('issues'), 'status': diagnostic['status'], 'mechanics_trace': []},
            prefix + '/diagnostic/issues', 'unverified_saved_verification_failure')
