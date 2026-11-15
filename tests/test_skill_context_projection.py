from __future__ import annotations

import hashlib

from genshin_app.agent_chat import tools
from genshin_app.agent_chat.native_tool_receipt import DIRECT_RESULT_MAX_BYTES
from genshin_app.agent_chat.skill_context import FOCUS_RESOURCES, current_focus_card
from genshin_app.equipment.files import json_bytes


def test_current_card_is_the_same_page_as_tool_without_loading_all_cards(monkeypatch):
    original = tools._skill
    reads = []
    def record(session, args):
        reads.append(args["resource"])
        return original(session, args)
    monkeypatch.setattr(tools, "_skill", record)
    one = current_focus_card({"workflow": "artifact", "workflow_stage": "loadout_draft"})
    assert reads == [FOCUS_RESOURCES["loadout_draft"]]
    expected = original(None, {"resource": one["resource"]})
    assert one["text"] == expected["text"]
    assert one["content_hash"] == expected["content_hash"]
    assert one["page_hash"] == hashlib.sha256(one["text"].encode()).hexdigest()
    assert one["focus_is_authorization"] is False
    reads.clear()
    two = current_focus_card({"workflow": "artifact", "workflow_stage": "event_topology"})
    assert reads == [FOCUS_RESOURCES["event_topology"]]
    assert one["resource"] != two["resource"]
    assert current_focus_card({"workflow": "calculate"}) is None


def test_artifact_production_shape_uses_advisory_focus_without_changing_authority():
    value = current_focus_card({"workflow": "artifact", "workflow_stage": "artifact",
                               "stage": {"focus": "calculation_closure"}})
    assert value["resource"] == FOCUS_RESOURCES["calculation_closure"]
    assert value["focus_is_authorization"] is False
    assert current_focus_card({"workflow": "artifact", "workflow_stage": "loadout_draft",
                              "stage": {"focus": "unknown"}}) is None


def test_reader_has_no_file_or_fixed_character_quota_and_pages_exact_bytes(monkeypatch, tmp_path):
    path = tmp_path / "SKILL.md"
    value = '冰🎵"\n' * 90000
    path.write_text(value, encoding="utf-8")
    assert path.stat().st_size > 256 * 1024
    monkeypatch.setattr(tools, "skill_dir", lambda: tmp_path)
    offset, chunks, digest = 0, [], hashlib.sha256(value.encode()).hexdigest()
    while True:
        page = tools._skill(None, {"resource": "SKILL.md", "offset": offset, "max_chars": 1_000_000})
        assert page["content_hash"] == digest
        assert len(json_bytes({"code": 0, "data": page})) <= min(tools.MAX_RESPONSE, DIRECT_RESULT_MAX_BYTES)
        assert page["delivered_chars"] > 0
        chunks.append(page["text"])
        if page["next_offset"] is None:
            break
        assert page["truncation_reason"] == "response_bytes"
        assert page["next_offset"] > offset
        offset = page["next_offset"]
    assert "".join(chunks) == value
    requested = tools._skill(None, {"resource": "SKILL.md", "max_chars": 21})
    assert requested["delivered_chars"] == 21
    assert requested["truncation_reason"] == "max_chars"


def test_reader_default_is_available_page_not_old_16000_cap(monkeypatch, tmp_path):
    (tmp_path / "SKILL.md").write_text("x" * 20000, encoding="utf-8")
    monkeypatch.setattr(tools, "skill_dir", lambda: tmp_path)
    page = tools._skill(None, {"resource": "SKILL.md"})
    assert page["delivered_chars"] == 20000
    assert page["next_offset"] is None
