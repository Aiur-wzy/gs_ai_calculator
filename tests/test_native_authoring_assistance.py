from __future__ import annotations

import copy
import uuid

from genshin_app.agent_chat.artifact_candidate import ArtifactCandidateSession
from genshin_app.agent_chat.native_app_tool_host import (
    _derived_view,
    _durable_candidate_view,
)
from genshin_app.agent_chat.tools import definitions_for_session
from genshin_app.authoring_assistance import (
    authoring_assistance,
    authoring_request_schema,
)
from genshin_app.whiteboard import new_whiteboard
from tests.test_calculation_delivery import delivery_plan


def make(tmp_path, board=False):
    return ArtifactCandidateSession(str(uuid.uuid4()), tmp_path / "candidate", tmp_path / "absent.db",
                                    "whiteboard" if board else "native", new_whiteboard() if board else delivery_plan())


def test_native_and_helper_share_catalog_without_mutation(tmp_path):
    current = make(tmp_path)
    request = {"kind": "catalog", "version": 1, "request_id": "catalog", "expected_revision": 0}
    before = copy.deepcopy((_derived_view(current), _durable_candidate_view(current)))
    response = current.call_tool("gs_validate_agent_materialization", {"expected_revision": 0, "request": request})
    assert response["code"] == 0, response
    assert response["data"] == authoring_assistance(request, plan=current.candidate)
    assert before == (_derived_view(current), _durable_candidate_view(current))
    schema = next(row for row in definitions_for_session(current) if row["name"] == "gs_validate_agent_materialization")["inputSchema"]
    assert schema["properties"]["request"] == authoring_request_schema()


def test_initial_whiteboard_can_discover_authoring_without_converting(tmp_path):
    current = make(tmp_path, board=True)
    request = {"kind": "catalog", "version": 1, "request_id": "catalog", "expected_revision": 0}
    response = current.call_tool("gs_validate_agent_materialization", {"expected_revision": 0, "request": request})
    assert response["code"] == 0, response
    assert current._is_board() and current.revision == 0
    assert response["data"]["plan_modified"] is False
    assert current.call_tool("gs_validate_agent_materialization", {"expected_revision": 0})["reason"] == "agent_native_artifact_required"


def test_nested_expected_revision_cannot_create_a_stale_draft(tmp_path):
    current = make(tmp_path)
    request = {"kind": "catalog", "version": 1, "request_id": "catalog", "expected_revision": 1}
    result = current.call_tool("gs_validate_agent_materialization", {"expected_revision": 0, "request": request})
    assert result["reason"] == "agent_stale_revision"
    assert result["details"] == {"path": "/request/expected_revision", "actual": 1, "expected": 0}
    assert current.revision == 0
