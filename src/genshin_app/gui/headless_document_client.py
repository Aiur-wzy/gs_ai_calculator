"""Headless caller for the exact GUI document transport boundary.

The workflow CLI is a projection of the desktop Renderer, not a second App
client. Every document-bearing operation therefore crosses
``GuiApi.document_json_call`` with the same JSON argument array used by the
WebView. This module deliberately does not fall back to calling ``GuiApi``
methods directly when the transport reports an uncertain completion.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from typing import Any

from genshin_app.gui.document_transport import DOCUMENT_METHODS


class GuiDocumentCallError(RuntimeError):
    """A reviewed GUI document call failed or returned an invalid envelope."""

    def __init__(
        self,
        message: str,
        *,
        reason: str,
        details: object | None = None,
        operation_may_have_completed: bool = False,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.details = copy.deepcopy(details)
        self.operation_may_have_completed = operation_may_have_completed


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate object key: {key}")
        result[key] = value
    return result


class GuiDocumentClient:
    """Call reviewed document methods through ``document_json_call`` only."""

    def __init__(self, api: object) -> None:
        bridge = getattr(api, "document_json_call", None)
        if not callable(bridge):
            raise TypeError("gui_document_transport_unavailable")
        self._bridge = bridge

    def call(self, method: str, *args: object) -> Any:
        if method not in DOCUMENT_METHODS:
            raise GuiDocumentCallError(
                "该方法没有进入GUI文档传输白名单",
                reason="gui_document_method_not_reviewed",
                details={"method": method},
            )
        try:
            args_text = json.dumps(
                list(args),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
        except (TypeError, ValueError, OverflowError, RecursionError) as error:
            raise GuiDocumentCallError(
                "GUI文档调用参数不是有限JSON",
                reason="gui_document_arguments_invalid",
                details={"method": method},
            ) from error

        raw = self._bridge(method, args_text)
        if not isinstance(raw, str):
            raise GuiDocumentCallError(
                "GUI文档传输没有返回JSON文本",
                reason="gui_document_response_invalid",
                details={"method": method},
            )
        try:
            envelope = json.loads(raw, object_pairs_hook=_strict_object)
        except (TypeError, ValueError, RecursionError) as error:
            raise GuiDocumentCallError(
                "GUI文档传输返回了无效JSON",
                reason="gui_document_response_invalid",
                details={"method": method},
            ) from error
        if not isinstance(envelope, Mapping) or type(envelope.get("code")) is not int:
            raise GuiDocumentCallError(
                "GUI文档传输返回了无效包络",
                reason="gui_document_response_invalid",
                details={"method": method},
            )
        if envelope["code"] != 0:
            raw_details = envelope.get("details")
            if isinstance(raw_details, Mapping):
                checked_details: object = {
                    "method": method,
                    **copy.deepcopy(dict(raw_details)),
                }
            elif raw_details is None:
                checked_details = {"method": method}
            else:
                checked_details = {
                    "method": method,
                    "host_details": copy.deepcopy(raw_details),
                }
            raise GuiDocumentCallError(
                str(envelope.get("message") or "GUI文档操作失败"),
                reason=str(envelope.get("reason") or "document_transport"),
                details=checked_details,
                operation_may_have_completed=(
                    envelope.get("operation_may_have_completed") is True
                ),
            )
        if "data" not in envelope:
            raise GuiDocumentCallError(
                "GUI文档传输成功包络缺少data",
                reason="gui_document_response_invalid",
                details={"method": method},
            )
        return copy.deepcopy(envelope["data"])
