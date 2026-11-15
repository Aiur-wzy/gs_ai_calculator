"""Lifecycle and concurrency tests use a provider double; no inference billing."""
import copy
import json
import threading
import time
from typing import ClassVar

import pytest

from genshin_app.agent_chat.manager import AgentManager, quota_summary
from genshin_app.equipment.schema import PlanError


class Candidate:
    storage: ClassVar[dict] = {}

    def __init__(self, sid, root, db, kind, document, **kwargs):
        self.sid, self.root, self.document, self.kind = sid, root, copy.deepcopy(document), kind
        self.revision = 0
        self.calls = []
        self.calculated = False
        self.storage[sid] = self

    @classmethod
    def load(cls, sid, *args, **kwargs):
        return cls.storage[sid]

    def tool_definitions(self):
        return []

    def call_tool(self, name, args):
        self.calls.append((name, args))
        self.calculated = True
        return {"code": 0, "data": {"done": True}}

    def summary(self):
        return {"revision": self.revision, "ready": self.calculated, "receipt_fingerprint": "receipt"}

    def accept(self, kind, document, revision, receipt):
        if document != self.document or revision != self.revision or receipt != "receipt":
            raise PlanError("stale", "stale_candidate")
        return {"kind": self.kind, "plan": document}

    def save(self):
        pass

    def close(self):
        pass


class Transport:
    instances: ClassVar[list] = []
    mode = "complete"
    quota = 30

    def __init__(self, cli, cwd, on_event):
        self.emit = on_event
        self.closed = False
        self.turns = 0
        self.replies = []
        self.rejections = []
        self.interrupted = False
        self.instances.append(self)

    def connect(self):
        return {"state": "ready", "models": [{"model": "test-model", "supportedReasoningEfforts": [{"reasoningEffort": "high"}]}],
                "account": {"type": "chatgpt"}, "limits": self.request("account/rateLimits/read", {})}

    def request(self, method, params):
        assert method == "account/rateLimits/read"
        return {"rateLimits": {"primary": {"usedPercent": self.quota}}}

    def start_thread(self, tools, instructions, model):
        assert "gs_read_skill" in instructions
        return "thread"

    def start_turn(self, thread, message, model, effort):
        self.turns += 1
        tid = f"turn-{self.turns}"
        if self.mode == "complete":
            self.emit({"method": "item/tool/call", "id": 20, "params": {"threadId": thread, "turnId": tid, "callId": tid + ".call", "tool": "gs_calculate", "arguments": {}}})
            self.emit({"method": "item/agentMessage/delta", "params": {"threadId": thread, "turnId": tid, "itemId": tid + ".m", "delta": "已完成"}})
            self.emit({"method": "item/completed", "params": {"threadId": thread, "turnId": tid, "item": {"type": "agentMessage", "id": tid + ".m", "text": "已完成"}}})
            self.emit({"method": "turn/completed", "params": {"threadId": thread, "turn": {"id": tid, "status": "completed"}}})
        elif self.mode == "permission":
            self.emit({"method": "item/commandExecution/requestApproval", "id": 99, "params": {"threadId": thread, "turnId": tid}})
        elif self.mode == "quota":
            self.emit({"method": "account/rateLimits/updated", "params": {"rateLimits": {"primary": {"usedPercent": 96}}}})
            self.emit({"method": "turn/completed", "params": {"threadId": thread, "turn": {"id": tid, "status": "completed"}}})
        return tid

    def reply_tool(self, request_id, result):
        self.replies.append((request_id, result))

    def reject_request(self, request_id):
        self.rejections.append(request_id)

    def interrupt(self, *args):
        self.interrupted = True

    def close(self):
        self.closed = True


@pytest.fixture
def manager(tmp_path):
    Transport.mode, Transport.quota = "complete", 30
    value = AgentManager(tmp_path, transport_factory=Transport, candidate_factory=Candidate)
    value.detect = lambda: {"candidates": [{"id": "cli", "supported": True}]}
    value.connect("cli")
    yield value
    value.shutdown()


def request(**changes):
    return {"kind": "native", "document": {"manual_template": {"meta": {"name": "测试方案"}}},
            "message": "补全计算", "model": "test-model", "effort": "high", "client_request_id": "request-0001", **changes}


def finish(manager):
    manager._worker.join(3)
    assert not manager._worker.is_alive()


@pytest.mark.parametrize("value,known,remaining", [({}, False, None),
    ({"rateLimits": {"primary": {"usedPercent": 94}}}, True, 6),
    ({"rateLimits": {"primary": {"usedPercent": 95}}}, True, 5),
    ({"rateLimits": {"primary": {"usedPercent": float("nan")}}}, False, None),
    ({"rateLimits": {"primary": {"usedPercent": 10}, "secondary": {"usedPercent": 99}}}, True, 1),
    ({"rateLimitsByLimitId": {"a": {"primary": {"usedPercent": 10}}, "b": {}}}, False, 90),
    ({"rateLimits": {"credits": {"balance": 999999}}}, False, None)])
def test_quota_is_private_read_only_information(value, known, remaining):
    result = quota_summary(value)
    assert result["allowed"] and result["managed_by_user"]
    assert result["known"] is known and result["remaining_percent"] == remaining
    assert "999999" not in str(result)


def test_run_multiround_and_apply_specific_reviewed_candidate(manager):
    result = manager.start(request())
    sid = result["session_id"]
    finish(manager)
    view = manager.poll(sid)
    assert view["state"] == "completed" and view["candidate"]["ready"]
    assert [m["message"] for m in view["messages"]] == ["补全计算", "已完成"]
    assert manager._candidates[sid].calls == [("gs_calculate", {})]
    manager.send(sid, "检查Buff", "request-0002")
    finish(manager)
    assert len(manager._candidates[sid].calls) == 2
    assert manager.accept(sid, "native", request()["document"], 0, "receipt")["kind"] == "native"
    with pytest.raises(PlanError, match="stale"):
        manager.accept(sid, "native", request()["document"], 1, "receipt")


def test_start_idempotency_does_not_charge_twice(manager):
    one = manager.start(request())
    finish(manager)
    two = manager.start(request())
    assert one["session_id"] == two["session_id"]
    assert manager._transport.turns == 1
    with pytest.raises(PlanError, match="同一请求"):
        manager.start(request(message="另一个请求"))


def test_provider_escape_stops_owned_process(manager):
    Transport.mode = "permission"
    transport = manager._transport
    sid = manager.start(request())["session_id"]
    finish(manager)
    view = manager.poll(sid)
    assert view["state"] == "failed" and transport.closed and transport.interrupted
    assert any(e.get("reason") == "agent_permission" for e in view["events"])


def test_low_quota_does_not_block_or_stop_turn(manager):
    Transport.quota = 96
    transport = manager._transport
    sid = manager.start(request())["session_id"]
    finish(manager)
    assert transport.turns == 1
    assert manager.poll(sid)["state"] == "completed"


def test_rate_limit_update_is_display_only_during_turn(manager):
    Transport.mode = "quota"
    transport = manager._transport
    sid = manager.start(request())["session_id"]
    finish(manager)
    assert manager.poll(sid)["state"] == "completed"
    assert not transport.closed and not transport.interrupted
    assert manager._connection["limits"]["remaining_percent"] == 4


def test_unknown_quota_endpoint_does_not_block_a_turn(tmp_path):
    class UnknownQuotaTransport(Transport):
        def connect(self):
            return {"state": "ready", "models": [{"model": "test-model", "supportedReasoningEfforts": [
                {"reasoningEffort": "high"}]}], "account": {"type": "chatgpt"},
                    "limits": {"known": False, "remaining_percent": None, "can_start": True}}

        def request(self, method, params):
            raise RuntimeError("quota display unavailable")

    value = AgentManager(tmp_path, transport_factory=UnknownQuotaTransport, candidate_factory=Candidate)
    value.detect = lambda: {"candidates": [{"id": "cli", "supported": True}]}
    value.connect("cli")
    try:
        sid = value.start(request())["session_id"]
        finish(value)
        assert value.poll(sid)["state"] == "completed"
    finally:
        value.shutdown()


def test_stop_and_busy_guards(manager):
    Transport.mode = "wait"
    sid = manager.start(request())["session_id"]
    for _ in range(100):
        if manager.poll(sid)["state"] == "running":
            break
        time.sleep(.01)
    with pytest.raises(PlanError, match="运行"):
        manager.accept(sid, "native", request()["document"], 0, "receipt")
    with pytest.raises(PlanError, match="活动任务"):
        manager.forget(sid)
    manager.stop(sid)
    finish(manager)
    assert manager.poll(sid)["state"] == "stopped"


def test_history_recovery_never_restarts_and_cursor_reset_preserves_messages(manager):
    sid = manager.start(request())["session_id"]
    finish(manager)
    session = manager._sessions[sid]
    for _ in range(1200):
        manager._event(session, "tool", message="记录")
    manager._save(session)
    result = manager.poll(sid, 1)
    assert result["reset_required"] and result["messages"][-1]["message"] == "已完成"
    session["state"] = "running"
    manager._save(session)
    reloaded = AgentManager(manager.data_dir, candidate_factory=Candidate)
    assert reloaded.sessions()["sessions"][0]["state"] == "stopped"
    assert reloaded._worker is None and reloaded._transport is None
    assert reloaded.poll(sid)["candidate"]["ready"]


def test_unknown_models_effort_and_traversal_are_rejected(manager):
    with pytest.raises(PlanError, match="模型"):
        manager.start(request(model="invented"))
    with pytest.raises(PlanError, match="档位"):
        manager.start(request(effort="ultra"))
    with pytest.raises(PlanError, match="标识"):
        manager.forget("../plans")


def test_advertised_ultra_rejected_before_candidate_or_turn(manager):
    manager._connection["models"][0]["supportedReasoningEfforts"].append({"reasoningEffort": "ultra"})
    with pytest.raises(PlanError, match="受控模式"):
        manager.start(request(effort="ultra"))
    assert manager._transport.turns == 0
    assert manager._candidates == {} and manager._worker is None


def test_forget_only_deletes_selected_session(manager):
    sid = manager.start(request())["session_id"]
    finish(manager)
    delivered = manager.data_dir / "agent_deliveries"
    delivered.mkdir()
    (delivered / "calculated.json").write_text("{}")
    manager.forget(sid)
    assert not (manager.root / sid).exists()
    assert (delivered / "calculated.json").exists()


def test_record_does_not_persist_transport_or_account(manager):
    sid = manager.start(request())["session_id"]
    finish(manager)
    saved = json.loads((manager.root / sid / "chat.json").read_text(encoding="utf-8"))
    assert not {"account", "limits", "token", "config"} & saved.keys()


def test_close_waits_for_owned_worker_and_is_idempotent(manager):
    Transport.mode = "wait"
    sid = manager.start(request())["session_id"]
    assert manager.shutdown()["closed"]
    assert manager.poll(sid)["state"] == "stopped"
    assert manager.shutdown()["closed"]


def test_two_threads_cannot_start_two_turns(manager):
    Transport.mode = "wait"
    results = []

    def start(index):
        try:
            results.append(manager.start(request(client_request_id=f"request-{index:04d}")))
        except PlanError as exc:
            results.append(exc)

    threads = [threading.Thread(target=start, args=(n,)) for n in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(2)
    assert sum(isinstance(x, dict) for x in results) == 1
