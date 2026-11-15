"""Typed command port for App-owned user interface actions.

The existing ``NativeAppActionController`` remains the only action state
owner.  This port supplies lifecycle-safe access, document-restore guards and
domain timeline correlation without persisting a second action projection.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any, Protocol, runtime_checkable

from .native_domain_session_registry import (
    NativeDomainSessionRegistry,
    NativeDomainSessionRegistryError,
)


class NativeAppActionRestoreGuard(Protocol):
    def __call__(self, conversation_id: str, *, operation: str) -> None: ...


class NativeAppActionTimelineSink(Protocol):
    def __call__(
        self,
        conversation_id: str,
        *,
        kind: str,
        category: str,
        label: str,
        state: str,
        operation_id: str,
        identity: tuple[str, ...],
    ) -> None: ...


@runtime_checkable
class NativeAppActionCommandPort(Protocol):
    def list_actions(
        self,
        conversation_id: str,
        *,
        include_terminal: bool = True,
    ) -> list[dict[str, Any]]: ...

    def present(self, conversation_id: str, *, action_id: str) -> dict[str, Any]: ...

    def claim(
        self,
        conversation_id: str,
        *,
        action_id: str,
        ui_receipt: str,
    ) -> dict[str, Any]: ...

    def settle(
        self,
        conversation_id: str,
        *,
        action_id: str,
        ui_receipt: str,
        outcome: Mapping[str, Any],
    ) -> dict[str, Any]: ...

    def recover_without_replay(
        self,
        conversation_id: str,
        *,
        action_id: str,
        ui_receipt: str,
    ) -> dict[str, Any]: ...


class NativeAppActionPort(NativeAppActionCommandPort):
    """Execute typed commands against the controller in one domain handle."""

    def __init__(
        self,
        registry: NativeDomainSessionRegistry,
        *,
        restore_guard: NativeAppActionRestoreGuard,
        timeline_sink: NativeAppActionTimelineSink,
        error_factory: Callable[[str], BaseException] | None = None,
    ) -> None:
        if not isinstance(registry, NativeDomainSessionRegistry):
            raise TypeError("native_domain_session_registry_invalid")
        if not callable(restore_guard) or not callable(timeline_sink):
            raise TypeError("native_app_action_port_dependency_invalid")
        self._registry = registry
        self._restore_guard = restore_guard
        self._timeline_sink = timeline_sink
        self._error_factory = error_factory

    @contextmanager
    def _action_scope(self, conversation_id: str) -> Iterator[Any]:
        """Project only Registry-owned failures at the typed port boundary."""

        try:
            with self._registry.action_scope(conversation_id) as handle:
                yield handle
        except NativeDomainSessionRegistryError as error:
            if self._error_factory is None:
                raise
            projected = self._error_factory(error.code)
            if not isinstance(projected, BaseException):
                raise TypeError("native_app_action_error_factory_invalid")
            raise projected from None

    def list_actions(
        self,
        conversation_id: str,
        *,
        include_terminal: bool = True,
    ) -> list[dict[str, Any]]:
        with self._action_scope(conversation_id) as handle:
            return copy.deepcopy(
                handle.action_controller.list_cards(
                    include_terminal=include_terminal
                )
            )

    def present(
        self,
        conversation_id: str,
        *,
        action_id: str,
    ) -> dict[str, Any]:
        self._restore_guard(conversation_id, operation="app_action_present")
        with self._action_scope(conversation_id) as handle:
            result = copy.deepcopy(
                handle.action_controller.present_action(action_id)
            )
            self._timeline_sink(
                conversation_id,
                kind="app_action",
                category="app_action",
                label="App 操作已呈现",
                state="presented",
                operation_id=action_id,
                identity=("app-action-present", action_id),
            )
        return result

    def claim(
        self,
        conversation_id: str,
        *,
        action_id: str,
        ui_receipt: str,
    ) -> dict[str, Any]:
        self._restore_guard(conversation_id, operation="app_action_claim")
        with self._action_scope(conversation_id) as handle:
            result = copy.deepcopy(
                handle.action_controller.claim_presented_action(
                    action_id=action_id,
                    ui_receipt=ui_receipt,
                )
            )
            self._timeline_sink(
                conversation_id,
                kind="app_action",
                category="app_action",
                label="App 操作已认领",
                state="claimed",
                operation_id=action_id,
                identity=("app-action-claim", action_id, ui_receipt),
            )
        return result

    def settle(
        self,
        conversation_id: str,
        *,
        action_id: str,
        ui_receipt: str,
        outcome: Mapping[str, Any],
    ) -> dict[str, Any]:
        self._restore_guard(conversation_id, operation="app_action_settle")
        with self._action_scope(conversation_id) as handle:
            result = copy.deepcopy(
                handle.action_controller.settle_claimed_action(
                    action_id=action_id,
                    ui_receipt=ui_receipt,
                    outcome=outcome,
                )
            )
            raw_status = result.get("status")
            status = raw_status if isinstance(raw_status, str) else "completed"
            self._timeline_sink(
                conversation_id,
                kind="app_action",
                category="app_action",
                label="App 操作已结算",
                state=status,
                operation_id=action_id,
                identity=("app-action-settle", action_id, ui_receipt, status),
            )
        return result

    def recover_without_replay(
        self,
        conversation_id: str,
        *,
        action_id: str,
        ui_receipt: str,
    ) -> dict[str, Any]:
        self._restore_guard(conversation_id, operation="app_action_recover")
        with self._action_scope(conversation_id) as handle:
            result = copy.deepcopy(
                handle.action_controller.recover_claimed_action_without_replay(
                    action_id=action_id,
                    ui_receipt=ui_receipt,
                )
            )
            self._timeline_sink(
                conversation_id,
                kind="app_action",
                category="app_action",
                label="App 操作已安全恢复",
                state="recovered",
                operation_id=action_id,
                identity=("app-action-recover", action_id, ui_receipt),
            )
        return result


__all__ = [
    "NativeAppActionCommandPort",
    "NativeAppActionPort",
    "NativeAppActionRestoreGuard",
    "NativeAppActionTimelineSink",
]
