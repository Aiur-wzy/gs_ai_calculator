"""Bounded retries for Windows transient locks, retaining os.replace atomicity.

Callers still own path validation, transaction locks, fsync and temp cleanup.
Never remove the destination, change permissions, or fall back to copying.
"""
from __future__ import annotations

import os
import sys
import time

_WINDOWS = sys.platform == "win32"
_RETRY_DELAYS = (0.02, 0.05, 0.1, 0.2, 0.4, 0.8)
_RETRYABLE_WINDOWS_ERRORS = frozenset({5, 32, 33})


def atomic_replace(source, destination) -> None:
    """Retry only access/sharing/lock failures; raise the last error if persistent."""
    for attempt in range(len(_RETRY_DELAYS) + 1):
        try:
            os.replace(source, destination)
            return
        except OSError as error:
            if (not _WINDOWS or getattr(error, "winerror", None) not in _RETRYABLE_WINDOWS_ERRORS
                    or attempt == len(_RETRY_DELAYS)):
                raise
            time.sleep(_RETRY_DELAYS[attempt])
