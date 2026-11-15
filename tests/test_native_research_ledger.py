from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from genshin_app.agent_chat.native_research_ledger import NativeResearchLedger
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError

RAW_ARGUMENTS_HASH = "f" * 64


def register(store, **kwargs):
    return store.register(
        epoch_id="epoch-1",
        raw_arguments_hash=RAW_ARGUMENTS_HASH,
        **kwargs,
    )


class Clock:
    def __init__(self):
        self.index = 0

    def __call__(self):
        self.index += 1
        return f"2026-10-23T01:00:{self.index:02d}Z"


class Ids:
    def __init__(self):
        self.index = 0

    def __call__(self):
        self.index += 1
        return "research_" + f"{self.index:032d}"


def ledger(tmp_path):
    return NativeResearchLedger(
        tmp_path,
        conversation_id="conversation-1",
        clock=Clock(),
        id_factory=Ids(),
    )


def usage(research_id, *, disposition="adopted"):
    return {
        "research_id": research_id,
        "disposition": disposition,
        "claim_summary": "用于核对技能持续时间",
        "applied_to": ["finding-1"] if disposition == "adopted" else [],
        "system_gap_class": (
            "database_missing" if disposition == "adopted" else "unused"
        ),
        "reason": "数据库尚未提供该机制" if disposition == "adopted" else "未采用",
    }


def search_meta(*sources, truncated=False, answer=None):
    value = {"sources": list(sources), "truncated": truncated}
    if answer is not None:
        value["answer"] = answer
    return value


def fetch_meta(url="https://example.com/source", *, status_code=200, truncated=False):
    return {"url": url, "statusCode": status_code, "truncated": truncated}


def settle_search(store, research_id, *, content_hash="a" * 64):
    return store.settle(
        research_id=research_id,
        status="completed",
        content_hash=content_hash,
        presentation_meta=search_meta(),
    )


def test_web_attempt_is_durable_before_dispatch_and_body_is_never_accepted(tmp_path):
    store = ledger(tmp_path)
    registered = register(store,
        expected_revision=0,
        turn_id="turn-1",
        workflow_stage="event_topology",
        call_id="call-1",
        tool="web_search",
        arguments={"queries": ["申鹤 冰翎 持续时间", "万叶 风套 持续时间"]},
    )
    assert registered == {
        "status": "registered",
        "research_id": "research_00000000000000000000000000000001",
        "research_ledger_revision": 1,
        "arguments_hash": fingerprint(
            {"queries": ["申鹤 冰翎 持续时间", "万叶 风套 持续时间"]}
        ),
        "raw_arguments_hash": RAW_ARGUMENTS_HASH,
    }
    saved = store.read()
    assert saved["revision"] == 1
    assert saved["entries"][0]["status"] == "pending"
    assert saved["entries"][0]["epoch_id"] == "epoch-1"
    assert saved["entries"][0]["raw_arguments_hash"] == RAW_ARGUMENTS_HASH
    assert "body" not in saved["entries"][0]

    settled = store.settle(
        research_id=registered["research_id"],
        status="completed",
        content_hash="a" * 64,
        presentation_meta=search_meta(
            {
                "url": "https://example.com/mechanic#duration",
                "title": "机制说明",
                "snippet": "有界摘要",
                "publishedAt": "2026-10-20",
            },
            answer="上游生成的摘要不应原文落盘",
        ),
        resource_ids=["web-resource-1"],
    )
    assert settled["status"] == "completed"
    assert settled["sources"][0]["url"] == "https://example.com/mechanic#duration"
    assert settled["sources"][0]["publishedAt"] == "2026-10-20"
    assert settled["presentation_meta"] == {
        "kind": "search",
        "truncated": False,
        "answer_present": True,
    }
    assert "上游生成" not in json.dumps(store.read(), ensure_ascii=False)
    assert store.read()["revision"] == 1


def test_register_requires_exact_revision_unique_call_and_no_topic_filter(tmp_path):
    store = ledger(tmp_path)
    first = register(store,
        expected_revision=0,
        turn_id="turn-1",
        workflow_stage="action_draft",
        call_id="call-1",
        tool="web_search",
        arguments={"queries": ["社区现成拉表答案 卢茜万班"]},
    )
    assert first["research_ledger_revision"] == 1
    with pytest.raises(PlanError) as stale:
        register(store,
            expected_revision=0,
            turn_id="turn-1",
            workflow_stage="action_draft",
            call_id="call-2",
            tool="web_search",
            arguments={"queries": ["任意合法主题"]},
        )
    assert stale.value.reason == "stale_context"
    with pytest.raises(PlanError) as duplicate:
        register(store,
            expected_revision=1,
            turn_id="turn-1",
            workflow_stage="action_draft",
            call_id="call-1",
            tool="web_search",
            arguments={"queries": ["另一个主题"]},
        )
    assert duplicate.value.reason == "call_id_conflict"


@pytest.mark.parametrize(
    "tool,arguments",
    [
        ("web_search", {"queries": ["api_key: sk-1234567890abcdef"]}),
        ("web_search", {"queries": [r"读取 C:\\Users\\me\\secret.txt"]}),
        ("web_fetch", {"url": "file:///C:/secret.txt"}),
        ("web_fetch", {"url": "https://user:pass@example.com/private"}),
        (
            "web_fetch",
            {"url": "https://example.com/?source=C%3A%5CUsers%5Cme%5Csecret.txt"},
        ),
    ],
)
def test_secret_or_local_path_cannot_enter_outbound_audit(tool, arguments, tmp_path):
    store = ledger(tmp_path)
    with pytest.raises(PlanError) as caught:
        register(store,
            expected_revision=0,
            turn_id="turn-1",
            workflow_stage="event_topology",
            call_id="call-1",
            tool=tool,
            arguments=arguments,
        )
    assert caught.value.reason in {"research_sensitive_argument", "agent_checkpoint_invalid"}
    assert store.read()["entries"] == []


def test_report_requires_exact_closed_set_and_all_calls_settled(tmp_path):
    store = ledger(tmp_path)
    one = register(store,
        expected_revision=0,
        turn_id="turn-1",
        workflow_stage="event_topology",
        call_id="call-1",
        tool="web_search",
        arguments={"queries": ["查询一"]},
    )
    two = register(store,
        expected_revision=1,
        turn_id="turn-1",
        workflow_stage="event_topology",
        call_id="call-2",
        tool="web_fetch",
        arguments={"url": "https://example.com/source"},
    )
    settle_search(store, one["research_id"])
    incomplete = store.report_usage(
        turn_id="turn-1",
        research_ledger_revision=2,
        entries=[usage(one["research_id"])],
        complete_for_turn=True,
    )
    assert incomplete == {
        "status": "incomplete_research_report",
        "research_ledger_revision": 2,
        "missing_ids": [two["research_id"]],
        "duplicate_ids": [],
        "unknown_ids": [],
        "unsettled_ids": [two["research_id"]],
        "complete_for_turn": True,
    }

    store.settle(research_id=two["research_id"], status="failed")
    complete = store.report_usage(
        turn_id="turn-1",
        research_ledger_revision=2,
        entries=[usage(one["research_id"]), usage(two["research_id"], disposition="rejected")],
        complete_for_turn=True,
    )
    assert complete == {
        "status": "complete",
        "research_ledger_revision": 2,
        "reported_count": 2,
    }
    saved = store.read()
    assert saved["entries"][0]["usage"]["disposition"] == "adopted"
    assert saved["entries"][1]["usage"]["disposition"] == "rejected"
    # A lost acknowledgement can replay the same report without manufacturing
    # a second timestamp or conflicting on entry order.
    replay = store.report_usage(
        turn_id="turn-1",
        research_ledger_revision=2,
        entries=[usage(two["research_id"], disposition="rejected"), usage(one["research_id"])],
        complete_for_turn=True,
    )
    assert replay == complete


def test_new_web_call_makes_old_report_revision_stale(tmp_path):
    store = ledger(tmp_path)
    one = register(store,
        expected_revision=0,
        turn_id="turn-1",
        workflow_stage="event_topology",
        call_id="call-1",
        tool="web_search",
        arguments={"queries": ["查询一"]},
    )
    settle_search(store, one["research_id"])
    register(store,
        expected_revision=1,
        turn_id="turn-1",
        workflow_stage="event_topology",
        call_id="call-2",
        tool="web_search",
        arguments={"queries": ["查询二"]},
    )
    stale = store.report_usage(
        turn_id="turn-1",
        research_ledger_revision=1,
        entries=[usage(one["research_id"])],
        complete_for_turn=True,
    )
    assert stale == {
        "status": "stale_research_report",
        "expected_revision": 2,
        "reported_revision": 1,
    }


def test_duplicate_unknown_and_explicit_incomplete_are_reported(tmp_path):
    store = ledger(tmp_path)
    one = register(store,
        expected_revision=0,
        turn_id="turn-1",
        workflow_stage="event_topology",
        call_id="call-1",
        tool="web_search",
        arguments={"queries": ["查询一"]},
    )
    store.settle(research_id=one["research_id"], status="cancelled")
    unknown = "research_99999999999999999999999999999999"
    result = store.report_usage(
        turn_id="turn-1",
        research_ledger_revision=1,
        entries=[usage(one["research_id"]), usage(one["research_id"]), usage(unknown)],
        complete_for_turn=False,
    )
    assert result["status"] == "incomplete_research_report"
    assert result["duplicate_ids"] == [one["research_id"]]
    assert result["unknown_ids"] == [unknown]
    assert result["complete_for_turn"] is False


def test_list_calls_is_bounded_and_turn_scoped(tmp_path):
    store = ledger(tmp_path)
    one = register(store,
        expected_revision=0,
        turn_id="turn-1",
        workflow_stage="event_topology",
        call_id="call-1",
        tool="web_search",
        arguments={"queries": ["查询一"]},
    )
    register(store,
        expected_revision=1,
        turn_id="turn-2",
        workflow_stage="event_topology",
        call_id="call-2",
        tool="web_search",
        arguments={"queries": ["查询二"]},
    )
    page = store.list_calls(turn_id="turn-1")
    assert page["total"] == 1
    assert page["items"][0]["research_id"] == one["research_id"]
    assert "content" not in json.dumps(page)


def test_terminal_settlement_is_idempotent_but_conflict_is_rejected(tmp_path):
    store = ledger(tmp_path)
    row = register(store,
        expected_revision=0,
        turn_id="turn-1",
        workflow_stage="event_topology",
        call_id="call-1",
        tool="web_fetch",
        arguments={"url": "https://example.com/source"},
    )
    first = store.settle(
        research_id=row["research_id"],
        status="completed",
        content_hash="a" * 64,
        presentation_meta=fetch_meta(),
    )
    replay = store.settle(
        research_id=row["research_id"],
        status="completed",
        content_hash="a" * 64,
        presentation_meta=fetch_meta(),
    )
    assert replay == first
    with pytest.raises(PlanError) as conflict:
        store.settle(
            research_id=row["research_id"],
            status="completed",
            content_hash="b" * 64,
            presentation_meta=fetch_meta(),
        )
    assert conflict.value.reason == "settlement_conflict"


def test_duplicate_json_keys_fail_closed(tmp_path):
    store = ledger(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    store.path.write_text(
        '{"contract":"genshin-native-research-ledger-v1",'
        '"conversation_id":"conversation-1","revision":0,"revision":1,'
        '"entries":[],"reports":[]}',
        encoding="utf-8",
    )
    with pytest.raises(PlanError) as caught:
        store.read()
    assert caught.value.reason == "agent_checkpoint_invalid"


def test_official_arguments_are_exact_and_search_has_no_content_filter(tmp_path):
    store = ledger(tmp_path)
    queries = [
        "  社区现成答案 KQM / HoYoLab  ",
        "  社区现成答案 KQM / HoYoLab  ",
        "DeepSeek API key: configuration guide",
    ]
    first = register(store,
        expected_revision=0,
        turn_id="turn-1",
        workflow_stage="loadout_draft",
        call_id="call-1",
        tool="web_search",
        arguments={"queries": queries},
    )
    second = register(store,
        expected_revision=1,
        turn_id="turn-1",
        workflow_stage="loadout_draft",
        call_id="call-2",
        tool="web_fetch",
        arguments={"url": "https://example.com"},
    )
    third = register(store,
        expected_revision=2,
        turn_id="turn-1",
        workflow_stage="loadout_draft",
        call_id="call-3",
        tool="web_fetch",
        arguments={"url": "https://example.com/home/user/guide"},
    )
    saved = store.read()
    assert saved["entries"][0]["arguments"] == {"queries": queries}
    assert saved["entries"][0]["arguments_hash"] == fingerprint({"queries": queries})
    assert saved["entries"][1]["arguments"] == {"url": "https://example.com"}
    assert saved["entries"][2]["arguments"] == {
        "url": "https://example.com/home/user/guide"
    }
    assert first["research_ledger_revision"] == 1
    assert second["research_ledger_revision"] == 2
    assert third["research_ledger_revision"] == 3


def test_exact_register_replay_recovers_identity_but_is_not_dispatchable(tmp_path):
    store = ledger(tmp_path)
    arguments = {"queries": ["元素附着规则"]}
    first = register(store,
        expected_revision=0,
        turn_id="turn-1",
        workflow_stage="event_topology",
        call_id="call-1",
        tool="web_search",
        arguments=arguments,
    )
    replay = register(store,
        expected_revision=0,
        turn_id="turn-1",
        workflow_stage="event_topology",
        call_id="call-1",
        tool="web_search",
        arguments=arguments,
    )
    assert replay == {
        **first,
        "status": "already_registered",
    }
    assert len(store.read()["entries"]) == 1


def test_concurrent_registration_uses_one_revision_cas(tmp_path):
    stores = [
        NativeResearchLedger(tmp_path, conversation_id="conversation-1")
        for _ in range(2)
    ]

    def attempt(index):
        try:
            return register(stores[index],
                expected_revision=0,
                turn_id="turn-1",
                workflow_stage="event_topology",
                call_id=f"call-{index}",
                tool="web_search",
                arguments={"queries": [f"查询{index}"]},
            )["status"]
        except PlanError as exc:
            return exc.reason

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert sorted(results) == ["registered", "stale_context"]
    assert stores[0].read()["revision"] == 1


def test_failed_terminal_write_leaves_durable_pending_attempt(tmp_path, monkeypatch):
    store = ledger(tmp_path)
    row = register(store,
        expected_revision=0,
        turn_id="turn-1",
        workflow_stage="event_topology",
        call_id="call-1",
        tool="web_search",
        arguments={"queries": ["查询"]},
    )
    before = store.path.read_bytes()

    def fail_write(_ledger):
        raise PlanError("模拟发布失败", "agent_storage")

    monkeypatch.setattr(store, "_write_unlocked", fail_write)
    with pytest.raises(PlanError) as caught:
        settle_search(store, row["research_id"])
    assert caught.value.reason == "agent_storage"
    assert store.path.read_bytes() == before
    assert store.read()["entries"][0]["status"] == "pending"


@pytest.mark.parametrize(
    "tool,arguments,presentation",
    [
        (
            "web_search",
            {"queries": ["查询"]},
            {"sources": [], "truncated": False, "body": "禁止保存正文"},
        ),
        (
            "web_fetch",
            {"url": "https://example.com"},
            {"url": "https://example.com", "statusCode": 99, "truncated": False},
        ),
    ],
)
def test_malformed_dsh_presentation_fails_before_terminal_write(
    tmp_path, tool, arguments, presentation
):
    store = ledger(tmp_path)
    row = register(store,
        expected_revision=0,
        turn_id="turn-1",
        workflow_stage="event_topology",
        call_id="call-1",
        tool=tool,
        arguments=arguments,
    )
    with pytest.raises(PlanError) as caught:
        store.settle(
            research_id=row["research_id"],
            status="completed",
            content_hash="a" * 64,
            presentation_meta=presentation,
        )
    assert caught.value.reason == "invalid_tool_result"
    assert store.read()["entries"][0]["status"] == "pending"


def test_conversation_owner_and_persisted_usage_binding_fail_closed(tmp_path):
    store = ledger(tmp_path)
    row = register(store,
        expected_revision=0,
        turn_id="turn-1",
        workflow_stage="event_topology",
        call_id="call-1",
        tool="web_search",
        arguments={"queries": ["查询"]},
    )
    settle_search(store, row["research_id"])
    store.report_usage(
        turn_id="turn-1",
        research_ledger_revision=1,
        entries=[usage(row["research_id"])],
        complete_for_turn=True,
    )
    with pytest.raises(PlanError) as wrong_owner:
        NativeResearchLedger(
            tmp_path, conversation_id="conversation-2"
        ).read()
    assert wrong_owner.value.reason == "stale_context"

    tampered = json.loads(store.path.read_text(encoding="utf-8"))
    tampered["entries"][0]["usage"]["research_id"] = (
        "research_99999999999999999999999999999999"
    )
    store.path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(PlanError):
        store.read()


def test_report_input_limit_is_checked_before_per_entry_validation(tmp_path):
    store = ledger(tmp_path)
    oversized = [usage("not-even-a-valid-id")] * 2049
    with pytest.raises(PlanError) as caught:
        store.report_usage(
            turn_id="turn-1",
            research_ledger_revision=0,
            entries=oversized,
            complete_for_turn=True,
        )
    assert caught.value.reason == "agent_tool_input"
