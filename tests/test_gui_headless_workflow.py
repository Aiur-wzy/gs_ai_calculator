"""Current document transport guard retained after legacy native runner retirement."""
from __future__ import annotations

import json

import pytest

from genshin_app.gui.headless_document_client import (
    GuiDocumentCallError,
    GuiDocumentClient,
)


class UncertainApi:
    def document_json_call(self, method, args_text):
        return json.dumps(
            {
                "code": 1,
                "message": "响应序列化失败",
                "reason": "document_transport",
                "operation_may_have_completed": True,
            }
        )


def test_document_client_surfaces_uncertain_completion_without_fallback():
    client = GuiDocumentClient(UncertainApi())
    with pytest.raises(GuiDocumentCallError) as caught:
        client.call("agent_start", {"message": "x"})
    assert caught.value.operation_may_have_completed is True
    assert caught.value.reason == "document_transport"
