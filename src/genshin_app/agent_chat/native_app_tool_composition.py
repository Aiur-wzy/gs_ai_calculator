"""Production composition of the unified App tool host from a turn scope.

This module is intentionally independent from the service and official turn
port implementations.  It consumes only the narrow attributes already
present on ``NativeTurnScopeV2`` and delegates every domain capability back to
the App-owned conversation binding.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Protocol

from genshin_app.equipment.schema import PlanError

from .native_app_call_ledger import NativeAppCallLedger
from .native_app_terminal_source import NativeAppTerminalSourceV2
from .native_app_tool_host import NativeAppToolHost
from .native_prompt_v2 import RUNTIME_GENERATION
from .native_research_ledger import NativeResearchLedger


class ProductionToolBindingV2(Protocol):
    candidate_session: object

    def domain_checkpoint(self) -> Mapping[str, Any]: ...

    def tool_manifest_hash(self) -> str: ...

    def current_writer_fence(self) -> str: ...

    def domain_write_boundary(self) -> AbstractContextManager[None]: ...

    def live_tool_binding(
        self,
        *,
        runtime_generation: str,
    ) -> Mapping[str, Any]: ...

    def read_domain_tool(
        self,
        name: str,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...

    def execute_domain_tool(
        self,
        name: str,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


class NativeTurnToolScopeV2(Protocol):
    conversation_id: str
    binding: ProductionToolBindingV2
    candidate_session: object
    research_ledger: NativeResearchLedger
    action_controller: object
    app_call_ledger: NativeAppCallLedger


def _fail(message: str, reason: str = "native_app_tool_composition_invalid") -> None:
    raise PlanError(message, reason)


def create_native_app_tool_host(
    scope: NativeTurnToolScopeV2,
    *,
    runtime_generation: str = RUNTIME_GENERATION,
) -> NativeAppToolHost:
    """Build one production Host from the service's conversation scope."""

    return create_native_app_tool_runtime(
        scope,
        runtime_generation=runtime_generation,
    ).host


@dataclass(frozen=True)
class NativeAppToolRuntimeV2:
    """Production pair consumed by the execution bridge and turn port."""

    host: NativeAppToolHost
    terminal_source: NativeAppTerminalSourceV2


def create_native_app_tool_runtime(
    scope: NativeTurnToolScopeV2,
    *,
    runtime_generation: str = RUNTIME_GENERATION,
) -> NativeAppToolRuntimeV2:
    """Build a Host and its synchronous persisted-terminal checkpoint seam."""

    conversation_id = getattr(scope, "conversation_id", None)
    binding = getattr(scope, "binding", None)
    candidate = getattr(scope, "candidate_session", None)
    research_ledger = getattr(scope, "research_ledger", None)
    app_call_ledger = getattr(scope, "app_call_ledger", None)
    action_controller = getattr(scope, "action_controller", None)
    if (
        not isinstance(conversation_id, str)
        or not conversation_id
        or binding is None
        or candidate is None
        or candidate is not getattr(binding, "candidate_session", None)
    ):
        _fail("turn scope与候选绑定不一致")
    for name in (
        "domain_checkpoint",
        "tool_manifest_hash",
        "current_writer_fence",
        "domain_write_boundary",
        "live_tool_binding",
        "read_domain_tool",
        "execute_domain_tool",
    ):
        if not callable(getattr(binding, name, None)):
            _fail("生产绑定缺少统一工具Host所需接口")
    if (
        not isinstance(research_ledger, NativeResearchLedger)
        or research_ledger.conversation_id != conversation_id
        or not isinstance(app_call_ledger, NativeAppCallLedger)
        or app_call_ledger.conversation_id != conversation_id
        or not callable(getattr(action_controller, "call_model_tool", None))
    ):
        _fail("turn scope的工具账或App操作控制器不匹配")

    live = dict(binding.live_tool_binding(runtime_generation=runtime_generation))
    expected = {
        "document_id",
        "database_fingerprint",
        "tool_manifest_hash",
        "runtime_generation",
        "writer_fence",
    }
    if set(live) != expected:
        _fail("生产绑定没有返回完整实时工具身份", "stale_context")

    def ensure_candidate(current: object) -> None:
        if current is not candidate:
            _fail("工具执行器收到其他候选会话", "stale_context")

    def read_executor(
        current: object,
        name: str,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        ensure_candidate(current)
        return binding.read_domain_tool(name, arguments)

    def write_executor(
        current: object,
        name: str,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        ensure_candidate(current)
        return binding.execute_domain_tool(name, arguments)

    host = NativeAppToolHost(
        candidate,
        conversation_id=conversation_id,
        document_id=live["document_id"],
        database_fingerprint=live["database_fingerprint"],
        tool_manifest_hash=live["tool_manifest_hash"],
        runtime_generation=live["runtime_generation"],
        writer_fence=live["writer_fence"],
        ledger=app_call_ledger,
        domain_write_executor=write_executor,
        domain_read_executor=read_executor,
        domain_write_boundary=binding.domain_write_boundary,
        live_binding_reader=lambda: binding.live_tool_binding(
            runtime_generation=runtime_generation,
        ),
        research_ledger=research_ledger,
        app_action_host=action_controller,
        resource_admission=getattr(scope, "resource_admission", None),
        diagnostic_data_dir=getattr(scope, "diagnostic_data_dir", None),
    )
    return NativeAppToolRuntimeV2(
        host=host,
        terminal_source=NativeAppTerminalSourceV2(app_call_ledger),
    )


class NativeAppToolHostFactoryV2:
    """Small callable factory suitable for an official turn-port adapter."""

    def __init__(self, *, runtime_generation: str = RUNTIME_GENERATION) -> None:
        self.runtime_generation = runtime_generation

    def __call__(self, scope: NativeTurnToolScopeV2) -> NativeAppToolHost:
        return create_native_app_tool_host(
            scope,
            runtime_generation=self.runtime_generation,
        )


class NativeAppToolRuntimeFactoryV2:
    """Factory for the complete production execution/checkpoint pair."""

    def __init__(self, *, runtime_generation: str = RUNTIME_GENERATION) -> None:
        self.runtime_generation = runtime_generation

    def __call__(self, scope: NativeTurnToolScopeV2) -> NativeAppToolRuntimeV2:
        return create_native_app_tool_runtime(
            scope,
            runtime_generation=self.runtime_generation,
        )


__all__ = [
    "NativeAppToolHostFactoryV2",
    "NativeAppToolRuntimeFactoryV2",
    "NativeAppToolRuntimeV2",
    "NativeTurnToolScopeV2",
    "ProductionToolBindingV2",
    "create_native_app_tool_host",
    "create_native_app_tool_runtime",
]
