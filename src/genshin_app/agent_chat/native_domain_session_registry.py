"""Lifecycle-neutral registry for App-owned conversation resources.

The registry stores references to existing domain owners.  It does not copy
candidate, action, document, Session or Turn facts.  A Service-provided access
validator is evaluated while the shared authority lock is held, so a command
cannot race a conversation switch or a newly admitted Turn.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .native_app_action_controller import NativeAppActionController
from .native_app_call_ledger import NativeAppCallLedger
from .native_research_ledger import NativeResearchLedger
from .native_session_store import NativeSessionStore

_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


class NativeDomainSessionRegistryError(RuntimeError):
    """Stable, path-free failure from the domain-handle boundary."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _conversation_id(value: object) -> str:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        raise NativeDomainSessionRegistryError(
            "native_conversation_id_invalid"
        )
    return value


@dataclass(frozen=True, slots=True)
class NativeDomainSessionHandle:
    """References to the existing App owners for one active conversation."""

    conversation_id: str
    session_root: Path
    binding: object
    research_ledger: NativeResearchLedger
    action_controller: NativeAppActionController
    app_call_ledger: NativeAppCallLedger
    # The retired V2 bridge needs its domain companion. New DSH workspaces
    # already have candidate/binding ledgers and do not create that old record.
    session_store: NativeSessionStore | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "conversation_id",
            _conversation_id(self.conversation_id),
        )
        if not isinstance(self.session_root, Path) or not self.session_root.is_absolute():
            raise NativeDomainSessionRegistryError(
                "native_domain_session_root_invalid"
            )
        if self.session_root.name != self.conversation_id:
            raise NativeDomainSessionRegistryError(
                "native_domain_session_root_invalid"
            )
        if self.binding is None:
            raise NativeDomainSessionRegistryError(
                "native_app_binding_invalid"
            )
        if not isinstance(self.research_ledger, NativeResearchLedger):
            raise NativeDomainSessionRegistryError(
                "native_research_ledger_invalid"
            )
        if not isinstance(self.action_controller, NativeAppActionController):
            raise NativeDomainSessionRegistryError(
                "native_app_action_controller_invalid"
            )
        if not isinstance(self.app_call_ledger, NativeAppCallLedger):
            raise NativeDomainSessionRegistryError(
                "native_app_call_ledger_invalid"
            )
        if self.session_store is not None and not isinstance(self.session_store, NativeSessionStore):
            raise NativeDomainSessionRegistryError(
                "native_session_store_invalid"
            )


class NativeDomainSessionRegistry:
    """Resolve active domain owners under the Service authority lock.

    ``access_validator`` may inspect Service lifecycle state but must not
    execute domain commands.  It is called with the exact shared lock held.
    """

    def __init__(
        self,
        *,
        authority_lock: threading.RLock,
        access_validator: Callable[[str], None],
        read_access_validator: Callable[[str], None] | None = None,
        single_binding_compatibility: bool = True,
        candidate_lock_for_actions: bool = False,
    ) -> None:
        if not callable(access_validator):
            raise TypeError("native_domain_access_validator_invalid")
        self._lock = authority_lock
        self._access_validator = access_validator
        self._read_access_validator = read_access_validator or access_validator
        self._single_binding_compatibility = single_binding_compatibility
        self._candidate_lock_for_actions = candidate_lock_for_actions
        self._handles: dict[str, NativeDomainSessionHandle] = {}

    def register(self, handle: NativeDomainSessionHandle) -> None:
        if not isinstance(handle, NativeDomainSessionHandle):
            raise TypeError("native_domain_session_handle_invalid")
        with self._lock:
            if handle.conversation_id in self._handles or (self._single_binding_compatibility and self._handles):
                raise NativeDomainSessionRegistryError(
                    "native_domain_session_already_active"
                    if handle.conversation_id in self._handles
                    else "native_domain_session_registry_busy"
                )
            self._handles[handle.conversation_id] = handle

    def unregister(
        self,
        conversation_id: str,
        *,
        expected_handle: NativeDomainSessionHandle,
    ) -> None:
        conversation_id = _conversation_id(conversation_id)
        if not isinstance(expected_handle, NativeDomainSessionHandle):
            raise TypeError("native_domain_session_handle_invalid")
        with self._lock:
            current = self._handles.get(conversation_id)
            if current is None:
                raise NativeDomainSessionRegistryError(
                    "native_domain_session_not_active"
                )
            if current is not expected_handle:
                raise NativeDomainSessionRegistryError(
                    "native_domain_session_changed"
                )
            del self._handles[conversation_id]

    @contextmanager
    def action_scope(
        self,
        conversation_id: str,
    ) -> Iterator[NativeDomainSessionHandle]:
        """Hold the authority lock across one App action controller call."""

        conversation_id = _conversation_id(conversation_id)
        with self._lock:
            self._access_validator(conversation_id)
            handle = self._handles.get(conversation_id)
            if handle is None:
                raise NativeDomainSessionRegistryError(
                    "native_domain_session_not_active"
                )
            if self._candidate_lock_for_actions:
                with handle.binding.candidate_session._lock:
                    yield handle
            else:
                yield handle

    def snapshot_ids(self) -> tuple[str, ...]:
        """Return only identities for diagnostics; never copy domain facts."""

        with self._lock:
            return tuple(sorted(self._handles))

    @contextmanager
    def read_scope(self, conversation_id: str) -> Iterator[NativeDomainSessionHandle]:
        """Borrow an exact owner without holding Service lock across its lock.

        A running Agent may write while the GUI is idle. The candidate lock
        gives one coherent page; the final identity check rejects a concurrent
        close/switch. No document writer or tool execution authority is lent.
        """
        conversation_id = _conversation_id(conversation_id)
        with self._lock:
            self._read_access_validator(conversation_id)
            handle = self._handles.get(conversation_id)
            if handle is None:
                raise NativeDomainSessionRegistryError("native_domain_session_not_active")
        yield handle
        with self._lock:
            self._read_access_validator(conversation_id)
            if self._handles.get(conversation_id) is not handle:
                raise NativeDomainSessionRegistryError("native_domain_session_changed")


__all__ = [
    "NativeDomainSessionHandle",
    "NativeDomainSessionRegistry",
    "NativeDomainSessionRegistryError",
]
