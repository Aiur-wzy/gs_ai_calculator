"""GUI credential scrubbing remains valid independently of retired native owners."""
from __future__ import annotations

import json

from genshin_app.equipment.schema import PlanError
from genshin_app.gui.api import GuiApi

NATIVE_CONNECTION_ID = "deepseek-official"


class _StableFailureManager:
    def __init__(self):
        self.request = None

    def connect(self, request):
        self.request = request
        raise PlanError("连接失败", "native_test_failure")


def test_gui_api_scrubs_mutable_connection_request_after_failure(tmp_path):
    manager = _StableFailureManager()
    api = GuiApi(tmp_path, agent_manager_factory=lambda _root: manager)
    request = {
        "id": NATIVE_CONNECTION_ID,
        "kind": "native_api",
        "api_key": "TEST_ONLY_GUI_BOUNDARY_SECRET",
        "remember_credential": True,
    }

    result = api.agent_connect(request)

    assert result["reason"] == "native_test_failure"
    assert request["api_key"] == ""
    assert manager.request["api_key"] == ""
    assert "TEST_ONLY_GUI_BOUNDARY_SECRET" not in json.dumps(result)
