"""Human candidate inspection is not the model's staged context filter."""
from __future__ import annotations

import copy
import threading

import pytest

from genshin_app.agent_chat.staged_candidate import PHASES, StagedCandidateSession
from genshin_app.equipment.schema import PlanError


@pytest.mark.parametrize("phase", PHASES)
def test_complete_candidate_is_readable_in_every_model_focus(phase):
    candidate = object.__new__(StagedCandidateSession)
    candidate._lock = threading.RLock()
    candidate.closed = False
    candidate.stage_state = {"phase": phase}
    candidate.candidate = {"team": [{"id": "a"}], "actions": [{"id": "e"}],
                           "extensions": {"event_topology": {"nodes": [{"id": "hit"}]}}}
    candidate.baseline = {"team": [], "actions": []}
    candidate.calculated = None
    before = copy.deepcopy(candidate.candidate)
    candidate.call_tool = lambda *_: pytest.fail("GUI view must not call model-context tools")
    view = candidate.read_candidate_view("candidate", "/extensions/event_topology/nodes")
    assert view["items"] == [{"key": 0, "value": {"id": "hit"}}]
    view["items"][0]["value"]["id"] = "tampered"
    assert candidate.candidate == before
    assert candidate.read_candidate_view("baseline", "/team")["total"] == 0
    assert candidate.read_candidate_view("calculated")["value"] is None
    candidate.calculated = {"result": {"hits": [{"id": "derived", "damage": 42}]}}
    derived = candidate.read_candidate_view("calculated", "/result/hits")
    assert derived["items"][0]["value"]["damage"] == 42
    derived["items"][0]["value"]["damage"] = 0
    assert candidate.calculated["result"]["hits"][0]["damage"] == 42
    candidate.closed = True
    with pytest.raises(PlanError, match="已关闭"):
        candidate.read_candidate_view("candidate")
