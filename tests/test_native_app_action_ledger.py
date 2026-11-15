from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from genshin_app.agent_chat.native_app_action_ledger import (
    NativeAppActionError,
    NativeAppActionLedger,
    sanitize_suggested_basename,
)

HASH_A = "a" * 64
HASH_B = "b" * 64


def _binding(**changes):
    value = {
        "document_id": "document-1",
        "candidate_revision": 3,
        "stage_revision": 2,
        "receipt_fingerprint": HASH_A,
        "applied_receipt_fingerprint": HASH_A,
    }
    value.update(changes)
    return value


def _workspace(**changes):
    value = {
        "document_kind": "whiteboard",
        "display_name": "测试方案",
        "current_view": "whiteboard",
        "dirty": True,
        "has_path": False,
        "can_save": True,
        "can_open": True,
        "can_export": True,
    }
    value.update(changes)
    return value


def _ledger(tmp_path):
    counter = iter(range(1, 20))
    tick = iter(range(1, 100))
    return NativeAppActionLedger(
        tmp_path,
        conversation_id="conversation-1",
        id_factory=lambda: f"action_{next(counter):032d}",
        clock=lambda: f"2026-10-23T00:00:{next(tick):02d}Z",
    )


def _request(ledger, *, expected_revision=0, action="save_as", **changes):
    values = {
        "expected_revision": expected_revision,
        "action": action,
        "reason": "请用户保存当前候选。",
        "suggested_name": "测试方案.json",
        "binding": _binding(),
        "workspace": _workspace(),
    }
    values.update(changes)
    return ledger.request(**values)


def test_card_is_durable_path_free_and_requires_present_before_completion(tmp_path):
    ledger = _ledger(tmp_path)
    queued = _request(
        ledger,
        suggested_name="C:\\Users\\tester\\危险:方案.json",
    )
    card = queued["card"]

    assert queued["status"] == "queued_for_user"
    assert card["suggested_name"] == "C_Users_tester_危险_方案.json"
    assert "C:\\" not in json.dumps(ledger.read(), ensure_ascii=False)
    with pytest.raises(NativeAppActionError, match="app_action_not_resolvable"):
        ledger.resolve(
            action_id=card["action_id"],
            status="completed",
            result_message="saved",
            current_binding=_binding(),
            agent_turn_active=False,
            ui_receipt="ui_" + "x" * 43,
            dirty_open_guard_passed=False,
        )

    presented = ledger.present(
        action_id=card["action_id"],
        agent_turn_active=False,
        current_binding=_binding(),
        current_workspace=_workspace(),
    )
    assert presented["status"] == "presented_for_user"
    assert presented["card"]["status"] == "presented"
    completed = ledger.resolve(
        action_id=card["action_id"],
        status="completed",
        result_message="用户通过文件界面完成保存。",
        current_binding=_binding(),
        agent_turn_active=False,
        ui_receipt=presented["card"]["ui_receipt"],
        dirty_open_guard_passed=False,
    )
    assert completed["status"] == "resolved"
    assert completed["card"]["status"] == "completed"
    replay = ledger.resolve(
        action_id=card["action_id"],
        status="completed",
        result_message="用户通过文件界面完成保存。",
        current_binding=_binding(),
        agent_turn_active=False,
        ui_receipt=presented["card"]["ui_receipt"],
        dirty_open_guard_passed=False,
    )
    assert replay["status"] == "already_resolved"
    with pytest.raises(NativeAppActionError, match="app_action_settlement_conflict"):
        ledger.resolve(
            action_id=card["action_id"],
            status="completed",
            result_message="duplicate",
            current_binding=_binding(),
            agent_turn_active=False,
            ui_receipt=presented["card"]["ui_receipt"],
            dirty_open_guard_passed=False,
        )


def test_same_pending_intent_is_deduplicated_even_after_ack_loss(tmp_path):
    ledger = _ledger(tmp_path)
    first = _request(ledger)

    replay = _request(ledger, expected_revision=0, reason="不同措辞不产生第二张卡。")

    assert replay["status"] == "already_queued"
    assert replay["card"]["action_id"] == first["card"]["action_id"]
    assert ledger.read()["revision"] == 1
    assert len(ledger.read()["cards"]) == 1


def test_formal_save_depends_on_document_not_unrelated_candidate_or_focus(tmp_path):
    for change in (
        {"document_id": "document-2"},
        {"candidate_revision": 4},
        {"stage_revision": 3},
        {"receipt_fingerprint": HASH_B},
        {"applied_receipt_fingerprint": HASH_B},
    ):
        case = tmp_path / next(iter(change))
        ledger = _ledger(case)
        card = _request(ledger)["card"]

        expired = ledger.refresh_binding(current_binding=_binding(**change))

        changed_document = "document_id" in change
        assert [item["action_id"] for item in expired] == ([card["action_id"]] if changed_document else [])
        replay = ledger.present(
            action_id=card["action_id"],
            agent_turn_active=False,
            current_binding=_binding(**change),
            current_workspace=_workspace(),
        )
        assert replay["status"] == ("already_settled" if changed_document else "presented_for_user")
        assert replay["card"]["status"] == ("expired" if changed_document else "presented")


def test_result_export_keeps_exact_applied_calculation_binding(tmp_path):
    ledger = _ledger(tmp_path)
    card = _request(ledger, action="export_result")["card"]
    assert ledger.refresh_binding(current_binding=_binding(candidate_revision=99,stage_revision=8,
                                                           receipt_fingerprint=HASH_B)) == []
    expired = ledger.refresh_binding(current_binding=_binding(applied_receipt_fingerprint=HASH_B))
    assert [item["action_id"] for item in expired] == [card["action_id"]]


def test_active_agent_turn_cannot_open_file_ui(tmp_path):
    ledger = _ledger(tmp_path)
    card = _request(ledger)["card"]

    with pytest.raises(NativeAppActionError, match="app_action_turn_active"):
        ledger.present(
            action_id=card["action_id"],
            agent_turn_active=True,
            current_binding=_binding(),
            current_workspace=_workspace(),
        )

    assert ledger.read()["cards"][0]["status"] == "queued"


def test_draft_save_is_independent_but_result_export_requires_acceptance(tmp_path):
    ledger = _ledger(tmp_path)
    draft = _request(
        ledger,
        binding=_binding(
            receipt_fingerprint=None,
            applied_receipt_fingerprint=None,
        ),
    )
    assert draft["card"]["action"] == "save_as"

    ledger = _ledger(tmp_path / "export")
    with pytest.raises(NativeAppActionError, match="app_action_candidate_not_accepted"):
        _request(
            ledger,
            action="export_result",
            binding=_binding(
                receipt_fingerprint=None,
                applied_receipt_fingerprint=None,
            ),
        )
    with pytest.raises(NativeAppActionError, match="app_action_candidate_not_accepted"):
        _request(ledger, action="export_result",
                 binding=_binding(applied_receipt_fingerprint=None))
    with pytest.raises(
        NativeAppActionError, match="app_action_workspace_capability_unavailable",
    ):
        _request(ledger, workspace=_workspace(can_save=False))

    open_card = _request(
        ledger,
        action="open_document",
        binding=_binding(
            receipt_fingerprint=None,
            applied_receipt_fingerprint=None,
        ),
    )
    assert open_card["card"]["action"] == "open_document"


def test_save_current_without_a_path_becomes_user_mediated_save_as(tmp_path):
    ledger = _ledger(tmp_path)

    result = _request(
        ledger,
        action="save_current",
        workspace=_workspace(has_path=False),
    )

    assert result["status"] == "queued_for_user"
    assert result["notice_code"] == "save_as_required"
    assert result["card"]["action"] == "save_as"
    assert result["card"]["workspace"]["has_path"] is False


def test_card_never_persists_workspace_or_status_paths(tmp_path):
    ledger = _ledger(tmp_path)
    with pytest.raises(NativeAppActionError, match="app_action_workspace_invalid"):
        _request(
            ledger,
            workspace=_workspace(display_name="C:\\Users\\tester\\plan.json"),
        )

    queued = _request(
        ledger,
        reason="请保存到 C:\\Users\\tester\\plan.json",
    )
    assert "C:\\" not in queued["card"]["reason"]
    presented = ledger.present(
        action_id=queued["card"]["action_id"],
        agent_turn_active=False,
        current_binding=_binding(),
        current_workspace=_workspace(),
    )
    completed = ledger.resolve(
        action_id=queued["card"]["action_id"],
        status="completed",
        result_message="已保存到 C:\\Users\\tester\\plan.json",
        current_binding=_binding(),
        agent_turn_active=False,
        ui_receipt=presented["card"]["ui_receipt"],
        dirty_open_guard_passed=False,
    )
    assert "C:\\" not in completed["card"]["result_message"]
    assert "C:\\" not in ledger.path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("CON", "_CON"),
        ("LPT9.txt", "_LPT9.txt"),
        ("CON .txt", "_CON .txt"),
        ("../目录/方案?.json", ".._目录_方案_.json"),
        ("api_key=sk-1234567890.json", "[已隐藏凭据]"),
        ("   ", "方案"),
        (None, None),
    ],
)
def test_suggested_basename_is_sanitized(value, expected):
    assert sanitize_suggested_basename(value) == expected


def test_tampered_duplicate_json_key_fails_closed(tmp_path):
    ledger = _ledger(tmp_path)
    _request(ledger)
    raw = ledger.path.read_text(encoding="utf-8")
    ledger.path.write_text(raw.replace('"revision":1', '"revision":1,"revision":1'), encoding="utf-8")

    with pytest.raises(NativeAppActionError, match="app_action_storage_invalid"):
        ledger.read()


def test_present_ack_loss_is_idempotent_and_never_reopens_ui(tmp_path):
    ledger = _ledger(tmp_path)
    card = _request(ledger)["card"]

    first = ledger.present(
        action_id=card["action_id"],
        agent_turn_active=False,
        current_binding=_binding(),
        current_workspace=_workspace(),
    )
    replay = ledger.present(
        action_id=card["action_id"],
        agent_turn_active=False,
        current_binding=_binding(),
        current_workspace=_workspace(),
    )

    assert first["status"] == "presented_for_user"
    assert replay["status"] == "already_presented"
    assert replay["ledger_revision"] == first["ledger_revision"]
    request_replay = _request(ledger, expected_revision=0)
    assert request_replay["status"] == "already_presented"
    assert request_replay["card"]["action_id"] == card["action_id"]


def test_resolve_rejects_active_turn_but_allows_completed_open_post_binding(tmp_path):
    ledger = _ledger(tmp_path)
    card = _request(
        ledger,
        action="open_document",
        binding=_binding(
            receipt_fingerprint=None,
            applied_receipt_fingerprint=None,
        ),
    )["card"]
    presented = ledger.present(
        action_id=card["action_id"],
        agent_turn_active=False,
        current_binding=_binding(
            receipt_fingerprint=None,
            applied_receipt_fingerprint=None,
        ),
        current_workspace=_workspace(),
    )

    with pytest.raises(NativeAppActionError, match="app_action_turn_active"):
        ledger.resolve(
            action_id=card["action_id"],
            status="completed",
            result_message="打开完成。",
            current_binding=_binding(document_id="document-2"),
            agent_turn_active=True,
            ui_receipt=presented["card"]["ui_receipt"],
            dirty_open_guard_passed=True,
        )
    result = ledger.resolve(
        action_id=card["action_id"],
        status="completed",
        result_message="打开完成。",
        current_binding=_binding(document_id="document-2"),
        agent_turn_active=False,
        ui_receipt=presented["card"]["ui_receipt"],
        dirty_open_guard_passed=True,
    )
    assert result["card"]["status"] == "completed"


def test_dirty_open_requires_the_presented_ui_receipt_and_guard(tmp_path):
    ledger = _ledger(tmp_path)
    binding = _binding(
        receipt_fingerprint=None,
        applied_receipt_fingerprint=None,
    )
    card = _request(ledger, action="open_document", binding=binding)["card"]
    presented = ledger.present(
        action_id=card["action_id"],
        agent_turn_active=False,
        current_binding=binding,
        current_workspace=_workspace(dirty=True),
    )

    with pytest.raises(NativeAppActionError, match="app_action_ui_receipt_mismatch"):
        ledger.resolve(
            action_id=card["action_id"],
            status="completed",
            result_message="打开完成。",
            current_binding=_binding(document_id="document-2"),
            agent_turn_active=False,
            ui_receipt="ui_" + "x" * 43,
            dirty_open_guard_passed=True,
        )
    with pytest.raises(NativeAppActionError, match="app_action_dirty_guard_required"):
        ledger.resolve(
            action_id=card["action_id"],
            status="completed",
            result_message="打开完成。",
            current_binding=_binding(document_id="document-2"),
            agent_turn_active=False,
            ui_receipt=presented["card"]["ui_receipt"],
            dirty_open_guard_passed=False,
        )
    assert ledger.read()["cards"][0]["status"] == "presented"


def test_present_rechecks_live_workspace_capability(tmp_path):
    ledger = _ledger(tmp_path)
    card = _request(ledger)["card"]

    with pytest.raises(NativeAppActionError, match="app_action_expired"):
        ledger.present(
            action_id=card["action_id"],
            agent_turn_active=False,
            current_binding=_binding(),
            current_workspace=_workspace(can_save=False),
        )
    assert ledger.read()["cards"][0]["status"] == "expired"


@pytest.mark.parametrize(
    ("mutator", "error"),
    [
        (
            lambda value: value["cards"][0].__setitem__(
                "conversation_id", "conversation-2",
            ),
            "app_action_ledger_identity_mismatch",
        ),
        (
            lambda value: value["cards"][0]["workspace"].__setitem__(
                "can_save", False,
            ),
            "app_action_card_invalid",
        ),
        (
            lambda value: value["cards"][0]["binding"].__setitem__(
                "receipt_fingerprint", "not-a-fingerprint",
            ),
            "app_action_receipt_fingerprint_invalid",
        ),
        (
            lambda value: value["cards"][0].__setitem__(
                "settled_at", "2026-10-22T00:00:00Z",
            ),
            "app_action_card_invalid",
        ),
    ],
)
def test_semantically_tampered_cards_fail_closed(tmp_path, mutator, error):
    ledger = _ledger(tmp_path)
    _request(ledger)
    value = json.loads(ledger.path.read_text(encoding="utf-8"))
    mutator(value)
    ledger.path.write_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    with pytest.raises(NativeAppActionError, match=error):
        ledger.read()


def test_terminal_timestamp_cannot_precede_presentation(tmp_path):
    ledger = _ledger(tmp_path)
    card = _request(ledger)["card"]
    presented = ledger.present(
        action_id=card["action_id"],
        agent_turn_active=False,
        current_binding=_binding(),
        current_workspace=_workspace(),
    )
    ledger.resolve(
        action_id=card["action_id"],
        status="completed",
        result_message="保存完成。",
        current_binding=_binding(),
        agent_turn_active=False,
        ui_receipt=presented["card"]["ui_receipt"],
        dirty_open_guard_passed=False,
    )
    value = json.loads(ledger.path.read_text(encoding="utf-8"))
    value["cards"][0]["settled_at"] = "2026-10-22T00:00:00Z"
    ledger.path.write_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    with pytest.raises(NativeAppActionError, match="app_action_card_invalid"):
        ledger.read()


def test_persisted_timestamps_require_canonical_utc(tmp_path):
    ledger = _ledger(tmp_path)
    _request(ledger)
    value = json.loads(ledger.path.read_text(encoding="utf-8"))
    value["cards"][0]["created_at"] = "2026-10-23T08:00:01+08:00"
    ledger.path.write_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    with pytest.raises(NativeAppActionError, match="app_action_created_at_invalid"):
        ledger.read()


def test_hardlinked_ledger_or_lock_file_fails_closed(tmp_path):
    ledger = _ledger(tmp_path)
    _request(ledger)
    ledger_alias = tmp_path / "ledger-alias.json"
    try:
        os.link(ledger.path, ledger_alias)
    except OSError as error:
        pytest.skip(f"hard links unavailable: {error}")

    with pytest.raises(NativeAppActionError, match="app_action_storage_invalid"):
        ledger.read()

    ledger_alias.unlink()
    ledger.read()
    lock_alias = tmp_path / "lock-alias"
    os.link(ledger.lock_path, lock_alias)
    with pytest.raises(NativeAppActionError, match="app_action_storage_invalid"):
        ledger.read()


def test_suggested_basename_obeys_utf16_limit():
    value = "😀" * 128
    result = sanitize_suggested_basename(value)

    assert result is not None
    assert len(result.encode("utf-16-le")) // 2 <= 128


def test_concurrent_same_intent_and_lifecycle_are_single_transition(tmp_path):
    ledger = NativeAppActionLedger(tmp_path, conversation_id="conversation-1")

    def request_once():
        return _request(ledger)

    with ThreadPoolExecutor(max_workers=2) as pool:
        requested = list(pool.map(lambda _index: request_once(), range(2)))
    assert {row["status"] for row in requested} == {
        "queued_for_user",
        "already_queued",
    }
    action_id = requested[0]["card"]["action_id"]

    def present_once():
        return ledger.present(
            action_id=action_id,
            agent_turn_active=False,
            current_binding=_binding(),
            current_workspace=_workspace(),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        presented = list(pool.map(lambda _index: present_once(), range(2)))
    assert {row["status"] for row in presented} == {
        "presented_for_user",
        "already_presented",
    }
    ui_receipt = presented[0]["card"]["ui_receipt"]

    def resolve_once():
        return ledger.resolve(
            action_id=action_id,
            status="completed",
            result_message="保存完成。",
            current_binding=_binding(),
            agent_turn_active=False,
            ui_receipt=ui_receipt,
            dirty_open_guard_passed=False,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        resolved = list(pool.map(lambda _index: resolve_once(), range(2)))
    assert {row["status"] for row in resolved} == {
        "resolved",
        "already_resolved",
    }
    assert ledger.read()["revision"] == 3
