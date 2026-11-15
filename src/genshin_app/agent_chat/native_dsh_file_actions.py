"""App file/navigation cards over the existing one-shot operation ledger.

No path is accepted from the model. The Renderer supplies its current document
facts, the user opens the normal file UI, and a claim is settled exactly once.
This is an App side-effect boundary, not another DSH task/session controller.
"""
from __future__ import annotations

import copy

from .native_app_action_ledger import normalize_action_workspace
from .native_dsh_mux_client import NativeDshMuxError


def workspace_state(request):
    if (type(request) is not dict or not {"current_document_id", "workspace"} <= set(request)
            or set(request) - {"current_document_id", "workspace", "current_payload"}):
        raise NativeDshMuxError("dsh_workspace_state_invalid")
    document_id = request["current_document_id"]
    if document_id is not None and (type(document_id) is not str or not document_id or len(document_id) > 128):
        raise NativeDshMuxError("dsh_workspace_state_invalid")
    workspace = normalize_action_workspace(request["workspace"])
    result_payload_digest = None
    if workspace["can_export"] and request.get("current_payload") is not None:
        from .native_candidate_transaction_contracts import (
            _application_document_id,
            _application_payload_digest,
        )

        payload = request["current_payload"]
        kind = workspace["document_kind"]
        field = "board" if kind == "whiteboard" else "plan"
        try:
            if type(payload) is not dict or type(payload.get(field)) is not dict:
                raise ValueError("invalid_current_payload")
            document = payload[field]
            if _application_document_id(kind, document) != document_id:
                raise ValueError("current_payload_document_mismatch")
            result_payload_digest = _application_payload_digest(kind, document, payload)
        except Exception as error:
            raise NativeDshMuxError("dsh_workspace_result_payload_invalid") from error
    if document_id is None:
        workspace.update(can_save=False, can_export=False, has_path=False)
    # Keep only the digest. The large result never becomes chat context or a
    # second stored result; an existing applied proof remains the authority.
    return {"current_document_id": document_id, "workspace": copy.deepcopy(workspace),
            "result_payload_digest": result_payload_digest}


def dispatch_action(controller, command, arguments):
    if type(arguments) is not dict:
        raise NativeDshMuxError("dsh_app_action_request_invalid")
    if command == "present" and set(arguments) == {"action_id"}:
        return controller.present_action(**arguments)
    if command == "claim" and set(arguments) == {"action_id", "ui_receipt"}:
        return controller.claim_presented_action(**arguments)
    if command == "settle" and set(arguments) == {"action_id", "ui_receipt", "outcome"}:
        return controller.settle_claimed_action(**arguments)
    if command == "recover" and set(arguments) == {"action_id", "ui_receipt"}:
        return controller.recover_claimed_action_without_replay(**arguments)
    raise NativeDshMuxError("dsh_app_action_request_invalid")
