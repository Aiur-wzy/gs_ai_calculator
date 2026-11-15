"""Compatibility facade for the side-effect-free App-level scope decorator."""
from genshin_app.verification_scope import verification_scope

from .execution_verification import (
    ExecutionVerificationError,
    ExecutionVerificationSession,
)

__all__ = ["ExecutionVerificationError", "ExecutionVerificationSession", "verification_scope"]
