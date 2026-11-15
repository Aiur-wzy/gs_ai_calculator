"""Side-effect-free decorator for one internal top-level validation operation."""
from functools import wraps


def verification_scope(function):
    """Public calls own a short-lived session; nested helpers pass it explicitly.

    No ambient state or serialized input selects this object. Every validator
    body still runs even when the session can share an identical engine replay.
    """
    @wraps(function)
    def scoped(*args, _session=None, **kwargs):
        # Delay the equipment facade until public modules finish importing.
        from genshin_app.equipment.execution_verification import (
            ExecutionVerificationError,
            ExecutionVerificationSession,
            annotate_verification_failure,
        )
        if _session is None:
            with ExecutionVerificationSession() as session:
                try:
                    return function(*args, _session=session, **kwargs)
                except ValueError as error:
                    annotate_verification_failure(error)
                    raise
        if type(_session) is not ExecutionVerificationSession:
            raise ExecutionVerificationError('verification_scope', '内部核验对象无效，不能从JSON授予共享权限。')
        _session._check()
        try:
            return function(*args, _session=_session, **kwargs)
        except ValueError as error:
            annotate_verification_failure(error)
            raise
    return scoped
