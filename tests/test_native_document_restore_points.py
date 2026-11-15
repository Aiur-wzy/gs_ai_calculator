from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

import genshin_app.agent_chat.native_document_restore_points as restore_module
from genshin_app.agent_chat.native_document_restore_points import (
    ARTIFACT_CONTRACT,
    QUEUE_REBIND_CONTRACT,
    STORE_FILE,
    NativeDocumentRestorePointStore,
)
from genshin_app.equipment.files import json_bytes
from genshin_app.equipment.schema import PlanError


class Clock:
    def __init__(self):
        self.index = 0

    def __call__(self):
        self.index += 1
        return f"2026-10-24T01:00:{self.index:02d}Z"


class Ids:
    def __init__(self):
        self.index = 0

    def __call__(self):
        self.index += 1
        return "checkpoint_" + f"{self.index:024d}"


def fingerprints(mark="a"):
    return {
        "database": mark * 64,
        "engine": chr(ord(mark) + 1) * 64,
        "schema": chr(ord(mark) + 2) * 64,
    }


def source():
    return {
        "conversation_id": "conversation-1",
        "turn_id": "turn-1",
        "event_cursor": "event-1",
    }


def guards(**changes):
    value = {
        "active_turn": False,
        "unsettled_write": False,
        "candidate_application_intent": False,
        "app_action": False,
        "outcome_unknown": False,
        "writer_takeover": False,
        "inbox_promoting_or_admitted": False,
    }
    value.update(changes)
    return value


def queue_rows():
    return [
        {
            "queue_item_id": "queue-1",
            "revision": 4,
            "document_lineage_epoch": 0,
            "document_revision": 0,
            "state": "queued",
        },
        {
            "queue_item_id": "queue-2",
            "revision": 8,
            "document_lineage_epoch": 0,
            "document_revision": 1,
            "state": "held_action_required",
        },
    ]


def store(tmp_path):
    result = NativeDocumentRestorePointStore(
        tmp_path,
        document_id="document-1",
        clock=Clock(),
        checkpoint_id_factory=Ids(),
    )
    initialized = result.initialize(
        document_kind="whiteboard",
        document={"team": ["old"], "actions": []},
        application_payload={"calculated": {"total": 1}},
        fingerprints=fingerprints("a"),
    )
    assert initialized["status"] == "initialized"
    return result


def checkpoint_then_edit(tmp_path, *, change_fingerprints=False):
    result = store(tmp_path)
    created = result.create_checkpoint(
        expected_store_revision=0,
        request_id="create-old",
        name="初始方案",
        document={"team": ["old"], "actions": []},
        application_payload={"calculated": {"total": 1}},
        source=source(),
    )
    advanced = result.record_document_revision(
        expected_store_revision=1,
        expected_lineage_epoch=0,
        expected_document_revision=0,
        revision=1,
        document={"team": ["new"], "actions": [{"id": "e"}]},
        application_payload={"calculated": {"total": 2}},
        fingerprints=fingerprints("d" if change_fingerprints else "a"),
    )
    assert advanced["store_revision"] == 2
    return result, created["checkpoint"]


def prepare(tmp_path, *, queue=None):
    result, target = checkpoint_then_edit(tmp_path)
    prepared = result.prepare_restore(
        expected_store_revision=2,
        request_id="restore-old",
        checkpoint_id=target["checkpoint_id"],
        observed_document={"team": ["new"], "actions": [{"id": "e"}]},
        observed_application_payload={"calculated": {"total": 2}},
        queue_revisions=queue or [],
        guard_snapshot=guards(),
        source=source(),
    )
    return result, target, prepared


def test_checkpoint_is_document_owned_content_addressed_and_diff_is_bounded(tmp_path):
    result, target = checkpoint_then_edit(tmp_path, change_fingerprints=True)
    listed = result.list_checkpoints()
    assert listed["type"] == "document_restore_point"
    assert listed["items"][0]["document_id"] == "document-1"
    assert listed["items"][0]["contract"] == "genshin-agent-document-checkpoint-v1"
    assert "domain_checkpoint" not in json.dumps(listed)

    verified = result.verify_checkpoint(target["checkpoint_id"])
    assert verified["status"] == "verified"
    artifact_names = {path.name for path in tmp_path.glob("*-*.json")}
    assert any(verified["document_digest"] in name for name in artifact_names)
    assert any(verified["application_payload_digest"] in name for name in artifact_names)

    summary = result.diff_summary(checkpoint_id=target["checkpoint_id"])
    assert summary["document"]["changed"] is True
    assert summary["application_payload"]["changed"] is True
    assert summary["large_content_inlined"] is False
    assert "old" not in json.dumps(summary)
    assert summary["application_payload_reusable"] is False

    renamed = result.rename_checkpoint(
        checkpoint_id=target["checkpoint_id"],
        name="可回退的旧方案",
        expected_store_revision=2,
    )
    assert renamed["status"] == "renamed"


def test_restore_rejects_stale_application_payload_across_fingerprints(tmp_path):
    result, target = checkpoint_then_edit(tmp_path, change_fingerprints=True)

    with pytest.raises(PlanError) as stale:
        result.prepare_restore(
            expected_store_revision=2,
            request_id="restore-stale-application-payload",
            checkpoint_id=target["checkpoint_id"],
            observed_document={"team": ["new"], "actions": [{"id": "e"}]},
            observed_application_payload={"calculated": {"total": 2}},
            queue_revisions=[],
            guard_snapshot=guards(),
            source=source(),
        )

    assert stale.value.reason == "document_restore_application_payload_stale"
    assert stale.value.details == {
        "fingerprint_match": {
            "database": False,
            "engine": False,
            "schema": False,
        },
        "application_payload_reusable": False,
        "compatibility": "version_incompatible",
        "recovery_action": "agent_guided_current_file_compatibility",
    }
    state = result.read()
    assert state["store_revision"] == 2
    assert state["restore_intents"] == []


def test_checkpoint_create_ack_loss_is_idempotent_and_conflicts_fail_closed(tmp_path):
    result = store(tmp_path)
    arguments = {
        "expected_store_revision": 0,
        "request_id": "create-one",
        "name": "恢复点一",
        "document": {"team": ["old"], "actions": []},
        "application_payload": {"calculated": {"total": 1}},
        "source": source(),
    }
    first = result.create_checkpoint(**arguments)
    replay = result.create_checkpoint(**arguments)
    assert replay["status"] == "already_created"
    assert replay["checkpoint"]["checkpoint_id"] == first["checkpoint"]["checkpoint_id"]
    with pytest.raises(PlanError) as conflict:
        result.create_checkpoint(**{**arguments, "name": "另一个内容"})
    assert conflict.value.reason == "request_id_conflict"


def test_restore_request_id_binds_the_audit_source_on_exact_replay(tmp_path):
    result, target, prepared = prepare(tmp_path)
    exact = result.prepare_restore(
        expected_store_revision=2,
        request_id="restore-old",
        checkpoint_id=target["checkpoint_id"],
        observed_document={"team": ["new"], "actions": [{"id": "e"}]},
        observed_application_payload={"calculated": {"total": 2}},
        queue_revisions=[],
        guard_snapshot=guards(),
        source=source(),
    )
    assert exact["status"] == "already_prepared"
    assert exact["intent"]["source"] == source()
    assert exact["intent"]["intent_id"] == prepared["intent"]["intent_id"]

    with pytest.raises(PlanError) as conflict:
        result.prepare_restore(
            expected_store_revision=2,
            request_id="restore-old",
            checkpoint_id=target["checkpoint_id"],
            observed_document={"team": ["new"], "actions": [{"id": "e"}]},
            observed_application_payload={"calculated": {"total": 2}},
            queue_revisions=[],
            guard_snapshot=guards(),
            source={
                "conversation_id": "conversation-other",
                "turn_id": "turn-other",
                "event_cursor": "event-other",
            },
        )
    assert conflict.value.reason == "request_id_conflict"


def test_restore_two_phase_state_machine_advances_lineage_and_invalidates_old_auth(tmp_path):
    queue = queue_rows()
    result, target, prepared = prepare(tmp_path, queue=queue)
    intent = prepared["intent"]
    assert intent["state"] == "prepared"
    assert intent["before_restore_checkpoint_id"] != target["checkpoint_id"]
    assert intent["pre_document_digest"] != intent["target_document_digest"]
    assert intent["target_lineage_epoch"] == 0
    assert intent["result_lineage_epoch"] == 1
    assert intent["result_revision"] == 2

    renderer = result.mark_renderer_applied(
        intent_id=intent["intent_id"],
        expected_store_revision=3,
        receipt_id="renderer-ack",
        observed_document={"team": ["old"], "actions": []},
        observed_application_payload={"calculated": {"total": 1}},
    )
    assert renderer["status"] == "renderer_applied"
    replay = result.mark_renderer_applied(
        intent_id=intent["intent_id"],
        expected_store_revision=3,
        receipt_id="renderer-ack",
        observed_document={"team": ["old"], "actions": []},
        observed_application_payload={"calculated": {"total": 1}},
    )
    assert replay["status"] == "already_renderer_applied"

    baseline = result.mark_baseline_advanced(
        intent_id=intent["intent_id"],
        expected_store_revision=4,
        receipt_id="baseline-ack",
        observed_document={"team": ["old"], "actions": []},
        observed_application_payload={"calculated": {"total": 1}},
    )
    assert baseline["status"] == "baseline_advanced"
    queue_digest = intent["queue_revision_digest"]
    queue_receipt = {
        "contract": QUEUE_REBIND_CONTRACT,
        "queue_revision_digest": queue_digest,
        "result_lineage_epoch": 1,
        "result_revision": 2,
        "rebound": [
            {
                "queue_item_id": row["queue_item_id"],
                "from_revision": row["revision"],
                "to_revision": row["revision"] + 1,
                "state": "needs_rebind",
            }
            for row in queue
        ],
    }
    committed = result.commit_restore(
        intent_id=intent["intent_id"],
        expected_store_revision=5,
        receipt_id="commit-ack",
        observed_document={"team": ["old"], "actions": []},
        observed_application_payload={"calculated": {"total": 1}},
        observed_queue_revisions=queue,
        queue_rebind_receipt=queue_receipt,
    )
    assert committed["status"] == "committed"
    assert committed["current"]["document_lineage_epoch"] == 1
    assert committed["current"]["revision"] == 2
    invalidation = committed["authorization_invalidation"]
    assert invalidation["old_lineage_authorizations_valid"] is False
    assert invalidation["invalidated_lineage_epoch"] == 0
    assert invalidation["queue_items_needing_rebind"] == ["queue-1", "queue-2"]
    assert committed["chat_deleted"] is False
    assert committed["external_side_effects_reverted"] is False
    assert committed["provider_request_sent"] is False

    replay = result.commit_restore(
        intent_id=intent["intent_id"],
        expected_store_revision=5,
        receipt_id="commit-ack",
        observed_document={"team": ["old"], "actions": []},
        observed_application_payload={"calculated": {"total": 1}},
        observed_queue_revisions=queue,
        queue_rebind_receipt=queue_receipt,
    )
    assert replay["status"] == "already_committed"
    with pytest.raises(PlanError) as old_lineage:
        result.record_document_revision(
            expected_store_revision=6,
            expected_lineage_epoch=0,
            expected_document_revision=1,
            revision=2,
            document={"team": []},
            application_payload={},
            fingerprints=fingerprints("a"),
        )
    assert old_lineage.value.reason == "stale_context"


def test_abort_only_before_renderer_and_same_target_is_noop_without_regression(tmp_path):
    result, _target, prepared = prepare(tmp_path)
    intent = prepared["intent"]
    renderer = result.mark_renderer_applied(
        intent_id=intent["intent_id"],
        expected_store_revision=3,
        receipt_id="renderer-ack",
        observed_document={"team": ["old"], "actions": []},
        observed_application_payload={"calculated": {"total": 1}},
    )
    with pytest.raises(PlanError) as forbidden:
        result.abort_restore(
            intent_id=intent["intent_id"],
            expected_store_revision=renderer["store_revision"],
            receipt_id="abort-ack",
            observed_document={"team": ["old"], "actions": []},
            observed_application_payload={"calculated": {"total": 1}},
        )
    assert forbidden.value.reason == "restore_abort_forbidden"

    other = store(tmp_path / "noop")
    same = other.create_checkpoint(
        expected_store_revision=0,
        request_id="same",
        name="当前状态",
        document={"team": ["old"], "actions": []},
        application_payload={"calculated": {"total": 1}},
    )
    no_op = other.prepare_restore(
        expected_store_revision=1,
        request_id="noop-restore",
        checkpoint_id=same["checkpoint"]["checkpoint_id"],
        observed_document={"team": ["old"], "actions": []},
        observed_application_payload={"calculated": {"total": 1}},
        queue_revisions=[],
        guard_snapshot=guards(),
    )
    assert no_op == {
        "status": "no_op",
        "store_revision": 1,
        "document_lineage_epoch": 0,
        "document_revision": 0,
        "old_lineage_authorizations_invalidated": False,
    }
    assert len(other.read()["restore_intents"]) == 0


def test_prepared_can_abort_only_when_full_pre_state_is_proven(tmp_path):
    result, _target, prepared = prepare(tmp_path)
    aborted = result.abort_restore(
        intent_id=prepared["intent"]["intent_id"],
        expected_store_revision=3,
        receipt_id="abort-ack",
        observed_document={"team": ["new"], "actions": [{"id": "e"}]},
        observed_application_payload={"calculated": {"total": 2}},
    )
    assert aborted["status"] == "aborted"
    replay = result.abort_restore(
        intent_id=prepared["intent"]["intent_id"],
        expected_store_revision=3,
        receipt_id="abort-ack",
        observed_document={"team": ["new"], "actions": [{"id": "e"}]},
        observed_application_payload={"calculated": {"total": 2}},
    )
    assert replay["status"] == "already_aborted"


def test_recovery_requires_complete_evidence_and_unknown_freezes_writes(tmp_path):
    result, _target, prepared = prepare(tmp_path)
    intent_id = prepared["intent"]["intent_id"]
    recovery = result.mark_recovery_required(
        intent_id=intent_id,
        expected_store_revision=3,
        reason="host_restart",
    )
    assert recovery["status"] == "recovery_required"
    unknown = result.reconcile_restore(
        intent_id=intent_id,
        expected_store_revision=4,
        evidence_id="probe-1",
        observed_document=None,
        observed_application_payload=None,
        observed_lineage_epoch=None,
        observed_revision=None,
    )
    assert unknown["status"] == "outcome_unknown"
    assert unknown["writes_frozen"] is True
    with pytest.raises(PlanError) as missing_review:
        result.mark_recovery_required(
            intent_id=intent_id,
            expected_store_revision=5,
            reason="manual_evidence_available",
        )
    assert missing_review.value.reason == "restore_evidence_required"
    with pytest.raises(PlanError) as blocked:
        result.record_document_revision(
            expected_store_revision=5,
            expected_lineage_epoch=0,
            expected_document_revision=1,
            revision=2,
            document={"team": []},
            application_payload={},
            fingerprints=fingerprints("a"),
        )
    assert blocked.value.reason == "restore_blocked"

    reopened = result.mark_recovery_required(
        intent_id=intent_id,
        expected_store_revision=5,
        reason="manual_evidence_available",
        manual_verification_id="manual-review-1",
    )
    recovered = result.reconcile_restore(
        intent_id=intent_id,
        expected_store_revision=reopened["store_revision"],
        evidence_id="renderer-proof-1",
        observed_document={"team": ["old"], "actions": []},
        observed_application_payload={"calculated": {"total": 1}},
        observed_lineage_epoch=0,
        observed_revision=1,
        renderer_applied_proof=True,
    )
    assert recovered["status"] == "renderer_applied"
    assert recovered["writes_frozen"] is True
    assert recovered["queue_promotion_frozen"] is True
    replay = result.reconcile_restore(
        intent_id=intent_id,
        expected_store_revision=reopened["store_revision"],
        evidence_id="renderer-proof-1",
        observed_document={"team": ["old"], "actions": []},
        observed_application_payload={"calculated": {"total": 1}},
        observed_lineage_epoch=0,
        observed_revision=1,
        renderer_applied_proof=True,
    )
    assert replay["status"] == "already_renderer_applied"
    assert replay["writes_frozen"] is True
    assert replay["queue_promotion_frozen"] is True


def test_guards_queue_cas_and_queue_receipt_fail_closed(tmp_path):
    result, target = checkpoint_then_edit(tmp_path)
    with pytest.raises(PlanError) as blocked:
        result.prepare_restore(
            expected_store_revision=2,
            request_id="blocked",
            checkpoint_id=target["checkpoint_id"],
            observed_document={"team": ["new"], "actions": [{"id": "e"}]},
            observed_application_payload={"calculated": {"total": 2}},
            queue_revisions=[],
            guard_snapshot=guards(active_turn=True),
        )
    assert blocked.value.reason == "restore_blocked"

    queue = queue_rows()
    result, _target, prepared = prepare(tmp_path / "queue", queue=queue)
    intent_id = prepared["intent"]["intent_id"]
    result.mark_renderer_applied(
        intent_id=intent_id,
        expected_store_revision=3,
        receipt_id="renderer",
        observed_document={"team": ["old"], "actions": []},
        observed_application_payload={"calculated": {"total": 1}},
    )
    result.mark_baseline_advanced(
        intent_id=intent_id,
        expected_store_revision=4,
        receipt_id="baseline",
        observed_document={"team": ["old"], "actions": []},
        observed_application_payload={"calculated": {"total": 1}},
    )
    with pytest.raises(PlanError) as missing_receipt:
        result.commit_restore(
            intent_id=intent_id,
            expected_store_revision=5,
            receipt_id="commit",
            observed_document={"team": ["old"], "actions": []},
            observed_application_payload={"calculated": {"total": 1}},
            observed_queue_revisions=queue,
        )
    assert missing_receipt.value.reason == "restore_queue_transaction_required"
    changed = [dict(row) for row in queue]
    changed[0]["revision"] += 1
    with pytest.raises(PlanError) as queue_race:
        result.commit_restore(
            intent_id=intent_id,
            expected_store_revision=5,
            receipt_id="commit",
            observed_document={"team": ["old"], "actions": []},
            observed_application_payload={"calculated": {"total": 1}},
            observed_queue_revisions=changed,
        )
    assert queue_race.value.reason == "restore_queue_conflict"


def test_strict_json_artifact_tamper_and_sensitive_content_are_rejected(tmp_path):
    result = store(tmp_path)
    created = result.create_checkpoint(
        expected_store_revision=0,
        request_id="create-one",
        name="恢复点",
        document={"team": ["old"], "actions": []},
        application_payload={"calculated": {"total": 1}},
    )
    ref = created["checkpoint"]["document_artifact"]
    artifact = tmp_path / f"document-body-{ref['sha256']}.json"
    envelope = json.loads(artifact.read_text(encoding="utf-8"))
    assert envelope["contract"] == ARTIFACT_CONTRACT
    envelope["value"]["team"] = ["tampered"]
    artifact.write_text(json.dumps(envelope), encoding="utf-8")
    with pytest.raises(PlanError) as tampered:
        result.verify_checkpoint(created["checkpoint"]["checkpoint_id"])
    assert tampered.value.reason == "document_restore_integrity"

    sensitive = store(tmp_path / "sensitive")
    with pytest.raises(PlanError) as secret:
        sensitive.record_document_revision(
            expected_store_revision=0,
            expected_lineage_epoch=0,
            expected_document_revision=0,
            revision=1,
            document={"api_key": "sk-1234567890abcdef"},
            application_payload={},
            fingerprints=fingerprints("a"),
        )
    assert secret.value.reason == "document_restore_sensitive"
    with pytest.raises(PlanError) as path:
        sensitive.record_document_revision(
            expected_store_revision=0,
            expected_lineage_epoch=0,
            expected_document_revision=0,
            revision=1,
            document={"note": r"C:\Users\me\secret.json"},
            application_payload={},
            fingerprints=fingerprints("a"),
        )
    assert path.value.reason == "document_restore_sensitive"

    duplicate_root = tmp_path / "duplicate"
    duplicate_root.mkdir()
    duplicate = NativeDocumentRestorePointStore(
        duplicate_root, document_id="document-1"
    )
    (duplicate_root / STORE_FILE).write_text(
        '{"contract":"a","contract":"b"}', encoding="utf-8"
    )
    with pytest.raises(PlanError) as duplicate_key:
        duplicate.read()
    assert duplicate_key.value.reason == "document_restore_integrity"


def test_96_mib_document_and_192_mib_payload_boundaries_stay_independent(
    tmp_path, monkeypatch
):
    assert restore_module.MAX_DOCUMENT_BYTES == 96 * 1024 * 1024
    assert restore_module.MAX_APPLICATION_PAYLOAD_BYTES == 192 * 1024 * 1024
    assert restore_module.MAX_STRUCTURE_NODES == 8_000_000
    assert restore_module.MAX_STRUCTURE_DEPTH == 64
    monkeypatch.setattr(restore_module, "MAX_DOCUMENT_BYTES", 64)
    monkeypatch.setattr(restore_module, "MAX_APPLICATION_PAYLOAD_BYTES", 80)
    monkeypatch.setattr(restore_module, "MAX_ARTIFACT_OVERHEAD_BYTES", 512)
    result = NativeDocumentRestorePointStore(
        tmp_path,
        document_id="document-1",
        clock=Clock(),
        checkpoint_id_factory=Ids(),
    )
    fitting_document = {"v": "x" * 56}
    assert len(json_bytes(fitting_document)) == 64
    fitting_payload = {"v": "y" * 72}
    assert len(json_bytes(fitting_payload)) == 80
    result.initialize(
        document_kind="native_plan",
        document=fitting_document,
        application_payload=fitting_payload,
    )
    assert (tmp_path / STORE_FILE).stat().st_size < 4096

    too_large = NativeDocumentRestorePointStore(
        tmp_path / "large", document_id="document-2"
    )
    with pytest.raises(PlanError) as limit:
        too_large.initialize(
            document_kind="native_plan",
            document={"v": "x" * 57},
            application_payload={},
        )
    assert limit.value.reason == "agent_size_limit"


def test_restore_structure_node_budget_is_independent_and_bounded(monkeypatch):
    monkeypatch.setattr(restore_module, "MAX_STRUCTURE_NODES", 5)
    restore_module._scan_sensitive({"rows": [1, 2, 3]})
    with pytest.raises(PlanError) as limit:
        restore_module._scan_sensitive({"rows": [1, 2, 3, 4]})
    assert limit.value.reason == "agent_size_limit"


def test_conflicting_reinitialize_does_not_persist_unreferenced_artifacts(tmp_path):
    result = store(tmp_path)
    before = sorted(path.name for path in tmp_path.glob("*.json"))

    with pytest.raises(PlanError) as conflict:
        result.initialize(
            document_kind="whiteboard",
            document={"team": ["unrelated"], "actions": [{"id": "new"}]},
            application_payload={"calculated": {"total": 999}},
            fingerprints=fingerprints("a"),
        )

    assert conflict.value.reason == "document_restore_conflict"
    assert sorted(path.name for path in tmp_path.glob("*.json")) == before


def test_concurrent_compare_and_swap_allows_only_one_document_advance(tmp_path):
    result = store(tmp_path)

    def advance(index):
        try:
            return result.record_document_revision(
                expected_store_revision=0,
                expected_lineage_epoch=0,
                expected_document_revision=0,
                revision=index + 1,
                document={"winner": index},
                application_payload={"calculated": index},
                fingerprints=fingerprints("a"),
            )["store_revision"]
        except PlanError as exc:
            return exc.reason

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(advance, (0, 1)))
    assert outcomes.count(1) == 1
    assert outcomes.count("stale_context") == 1


@pytest.mark.skipif(not hasattr(os, "link"), reason="hard links unavailable")
def test_late_hard_link_on_registry_is_rejected(tmp_path):
    result = store(tmp_path)
    try:
        os.link(result.path, tmp_path / "registry-hardlink.json")
    except OSError:
        pytest.skip("当前Windows策略禁止建立测试硬链接")
    with pytest.raises(PlanError) as linked:
        result.read()
    assert linked.value.reason == "agent_path_rejected"


def test_manual_checkpoint_deletion_is_idempotent_and_artifact_reference_safe(
    tmp_path,
):
    result = store(tmp_path)
    first = result.create_checkpoint(
        expected_store_revision=0,
        request_id="manual-first",
        name="手工一",
        document={"team": ["old"], "actions": []},
        application_payload={"calculated": {"total": 1}},
    )["checkpoint"]
    second = result.create_checkpoint(
        expected_store_revision=1,
        request_id="manual-second",
        name="手工二",
        document={"team": ["old"], "actions": []},
        application_payload={"calculated": {"total": 1}},
    )["checkpoint"]
    result.record_document_revision(
        expected_store_revision=2,
        expected_lineage_epoch=0,
        expected_document_revision=0,
        revision=1,
        document={"team": ["new"]},
        application_payload={"calculated": {"total": 2}},
        fingerprints=fingerprints("d"),
    )
    old_artifacts = {
        tmp_path / f"document-body-{first['document_artifact']['sha256']}.json",
        tmp_path
        / (
            "application-payload-"
            f"{first['application_payload_artifact']['sha256']}.json"
        ),
    }
    assert all(path.exists() for path in old_artifacts)

    deleted_first = result.delete_checkpoint(
        checkpoint_id=first["checkpoint_id"], expected_store_revision=3
    )
    assert deleted_first["status"] == "deleted"
    assert all(path.exists() for path in old_artifacts)

    deleted_second = result.delete_checkpoint(
        checkpoint_id=second["checkpoint_id"], expected_store_revision=4
    )
    assert deleted_second["status"] == "deleted"
    assert not any(path.exists() for path in old_artifacts)
    replay = result.delete_checkpoint(
        checkpoint_id=second["checkpoint_id"], expected_store_revision=0
    )
    assert replay["status"] == "already_deleted"


def test_checkpoint_referenced_by_active_or_terminal_intent_cannot_be_deleted(
    tmp_path,
):
    result, target, prepared = prepare(tmp_path)
    before_id = prepared["intent"]["before_restore_checkpoint_id"]
    for checkpoint_id in (target["checkpoint_id"], before_id):
        with pytest.raises(PlanError) as in_use:
            result.delete_checkpoint(
                checkpoint_id=checkpoint_id,
                expected_store_revision=prepared["store_revision"],
            )
        assert in_use.value.reason == "document_restore_in_use"

    aborted = result.abort_restore(
        intent_id=prepared["intent"]["intent_id"],
        expected_store_revision=prepared["store_revision"],
        receipt_id="abort-protected",
        observed_document={"team": ["new"], "actions": [{"id": "e"}]},
        observed_application_payload={"calculated": {"total": 2}},
    )
    with pytest.raises(PlanError) as terminal_reference:
        result.delete_checkpoint(
            checkpoint_id=before_id,
            expected_store_revision=aborted["store_revision"],
        )
    assert terminal_reference.value.reason == "document_restore_in_use"


def test_active_restore_freezes_unrelated_checkpoint_rename_and_delete(tmp_path):
    result, target = checkpoint_then_edit(tmp_path)
    unrelated = result.create_checkpoint(
        expected_store_revision=2,
        request_id="unrelated-manual",
        name="无关手工点",
        document={"team": ["new"], "actions": [{"id": "e"}]},
        application_payload={"calculated": {"total": 2}},
        source={
            "conversation_id": "conversation-one",
            "turn_id": "turn-two",
            "event_cursor": "2",
        },
    )["checkpoint"]
    prepared = result.prepare_restore(
        expected_store_revision=3,
        request_id="restore-with-unrelated",
        checkpoint_id=target["checkpoint_id"],
        observed_document={"team": ["new"], "actions": [{"id": "e"}]},
        observed_application_payload={"calculated": {"total": 2}},
        queue_revisions=[],
        guard_snapshot=guards(),
    )
    with pytest.raises(PlanError) as rename_blocked:
        result.rename_checkpoint(
            checkpoint_id=unrelated["checkpoint_id"],
            name="不能改名",
            expected_store_revision=prepared["store_revision"],
        )
    assert rename_blocked.value.reason == "restore_blocked"
    with pytest.raises(PlanError) as delete_blocked:
        result.delete_checkpoint(
            checkpoint_id=unrelated["checkpoint_id"],
            expected_store_revision=prepared["store_revision"],
        )
    assert delete_blocked.value.reason == "restore_blocked"
    gate = result.write_gate()
    assert gate == {
        "writes_frozen": True,
        "store_revision": prepared["store_revision"],
        "intent_id": prepared["intent"]["intent_id"],
        "state": "prepared",
    }


def test_automatic_checkpoint_and_terminal_history_are_bounded_with_ack_replay(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(restore_module, "MAX_TERMINAL_RESTORE_INTENTS", 1)
    monkeypatch.setattr(restore_module, "MAX_AUTOMATIC_CHECKPOINTS", 1)
    monkeypatch.setattr(restore_module, "MAX_RESTORE_RECEIPTS", 2)
    result, target = checkpoint_then_edit(tmp_path)
    first = result.prepare_restore(
        expected_store_revision=2,
        request_id="restore-first",
        checkpoint_id=target["checkpoint_id"],
        observed_document={"team": ["new"], "actions": [{"id": "e"}]},
        observed_application_payload={"calculated": {"total": 2}},
        queue_revisions=[],
        guard_snapshot=guards(),
    )
    result.abort_restore(
        intent_id=first["intent"]["intent_id"],
        expected_store_revision=3,
        receipt_id="abort-first",
        observed_document={"team": ["new"], "actions": [{"id": "e"}]},
        observed_application_payload={"calculated": {"total": 2}},
    )
    second = result.prepare_restore(
        expected_store_revision=4,
        request_id="restore-second",
        checkpoint_id=target["checkpoint_id"],
        observed_document={"team": ["new"], "actions": [{"id": "e"}]},
        observed_application_payload={"calculated": {"total": 2}},
        queue_revisions=[],
        guard_snapshot=guards(),
    )
    state = result.read()
    assert [item["checkpoint_id"] for item in state["checkpoints"] if not item["automatic"]] == [
        target["checkpoint_id"]
    ]
    assert sum(item["automatic"] for item in state["checkpoints"]) == 1
    assert len(state["restore_intents"]) == 1
    assert len(state["restore_receipts"]) == 1
    assert first["intent"]["intent_id"] in second["retention"]["retired_intent_ids"]

    ack_replay = result.abort_restore(
        intent_id=first["intent"]["intent_id"],
        expected_store_revision=0,
        receipt_id="abort-first",
        observed_document={"team": ["new"], "actions": [{"id": "e"}]},
        observed_application_payload={"calculated": {"total": 2}},
    )
    assert ack_replay["status"] == "already_aborted"
    request_replay = result.prepare_restore(
        expected_store_revision=0,
        request_id="restore-first",
        checkpoint_id=target["checkpoint_id"],
        observed_document={"team": ["new"], "actions": [{"id": "e"}]},
        observed_application_payload={"calculated": {"total": 2}},
        queue_revisions=[],
        guard_snapshot=guards(active_turn=True),
    )
    assert request_replay["status"] == "already_aborted"
    with pytest.raises(PlanError) as conflicting_replay:
        result.abort_restore(
            intent_id=first["intent"]["intent_id"],
            expected_store_revision=0,
            receipt_id="different-abort",
            observed_document={"team": ["new"], "actions": [{"id": "e"}]},
            observed_application_payload={"calculated": {"total": 2}},
        )
    assert conflicting_replay.value.reason == "restore_ack_conflict"


def test_automatic_checkpoint_space_limit_fails_closed_without_partial_metadata(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(restore_module, "MAX_AUTOMATIC_ARTIFACT_BYTES", 1)
    result, target = checkpoint_then_edit(tmp_path)
    with pytest.raises(PlanError) as capacity:
        result.prepare_restore(
            expected_store_revision=2,
            request_id="restore-too-large",
            checkpoint_id=target["checkpoint_id"],
            observed_document={"team": ["new"], "actions": [{"id": "e"}]},
            observed_application_payload={"calculated": {"total": 2}},
            queue_revisions=[],
            guard_snapshot=guards(),
        )
    assert capacity.value.reason == "document_restore_capacity"
    state = result.read()
    assert state["store_revision"] == 2
    assert len(state["checkpoints"]) == 1
    assert state["restore_intents"] == []


def test_retired_committed_intent_keeps_renderer_baseline_and_commit_ack_replay(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(restore_module, "MAX_TERMINAL_RESTORE_INTENTS", 1)
    result, _target, prepared = prepare(tmp_path)
    intent = prepared["intent"]
    result.mark_renderer_applied(
        intent_id=intent["intent_id"],
        expected_store_revision=3,
        receipt_id="renderer-proof",
        observed_document={"team": ["old"], "actions": []},
        observed_application_payload={"calculated": {"total": 1}},
    )
    result.mark_baseline_advanced(
        intent_id=intent["intent_id"],
        expected_store_revision=4,
        receipt_id="baseline-proof",
        observed_document={"team": ["old"], "actions": []},
        observed_application_payload={"calculated": {"total": 1}},
    )
    result.commit_restore(
        intent_id=intent["intent_id"],
        expected_store_revision=5,
        receipt_id="commit-proof",
        observed_document={"team": ["old"], "actions": []},
        observed_application_payload={"calculated": {"total": 1}},
        observed_queue_revisions=[],
    )
    result.prepare_restore(
        expected_store_revision=6,
        request_id="restore-back-to-new",
        checkpoint_id=intent["before_restore_checkpoint_id"],
        observed_document={"team": ["old"], "actions": []},
        observed_application_payload={"calculated": {"total": 1}},
        queue_revisions=[],
        guard_snapshot=guards(),
    )
    assert result.mark_renderer_applied(
        intent_id=intent["intent_id"],
        expected_store_revision=0,
        receipt_id="renderer-proof",
        observed_document={"team": ["old"], "actions": []},
        observed_application_payload={"calculated": {"total": 1}},
    )["status"] == "already_renderer_applied"
    assert result.mark_baseline_advanced(
        intent_id=intent["intent_id"],
        expected_store_revision=0,
        receipt_id="baseline-proof",
        observed_document={"team": ["old"], "actions": []},
        observed_application_payload={"calculated": {"total": 1}},
    )["status"] == "already_baseline_advanced"
    assert result.commit_restore(
        intent_id=intent["intent_id"],
        expected_store_revision=0,
        receipt_id="commit-proof",
        observed_document={"team": ["old"], "actions": []},
        observed_application_payload={"calculated": {"total": 1}},
        observed_queue_revisions=[],
    )["status"] == "already_committed"


def test_expired_compact_receipt_fails_closed_instead_of_reexecuting_request(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(restore_module, "MAX_TERMINAL_RESTORE_INTENTS", 1)
    monkeypatch.setattr(restore_module, "MAX_RESTORE_RECEIPTS", 1)
    result, target = checkpoint_then_edit(tmp_path)

    def prepare_and_abort(request_id, expected_revision):
        prepared = result.prepare_restore(
            expected_store_revision=expected_revision,
            request_id=request_id,
            checkpoint_id=target["checkpoint_id"],
            observed_document={"team": ["new"], "actions": [{"id": "e"}]},
            observed_application_payload={"calculated": {"total": 2}},
            queue_revisions=[],
            guard_snapshot=guards(),
        )
        aborted = result.abort_restore(
            intent_id=prepared["intent"]["intent_id"],
            expected_store_revision=prepared["store_revision"],
            receipt_id="abort-" + request_id,
            observed_document={"team": ["new"], "actions": [{"id": "e"}]},
            observed_application_payload={"calculated": {"total": 2}},
        )
        return prepared, aborted["store_revision"]

    first, revision = prepare_and_abort("first-request", 2)
    _second, revision = prepare_and_abort("second-request", revision)
    result.prepare_restore(
        expected_store_revision=revision,
        request_id="third-request",
        checkpoint_id=target["checkpoint_id"],
        observed_document={"team": ["new"], "actions": [{"id": "e"}]},
        observed_application_payload={"calculated": {"total": 2}},
        queue_revisions=[],
        guard_snapshot=guards(),
    )

    with pytest.raises(PlanError) as request_expired:
        result.prepare_restore(
            expected_store_revision=0,
            request_id="first-request",
            checkpoint_id=target["checkpoint_id"],
            observed_document={"team": ["new"], "actions": [{"id": "e"}]},
            observed_application_payload={"calculated": {"total": 2}},
            queue_revisions=[],
            guard_snapshot=guards(),
        )
    assert request_expired.value.reason == "restore_request_expired"
    with pytest.raises(PlanError) as ack_expired:
        result.abort_restore(
            intent_id=first["intent"]["intent_id"],
            expected_store_revision=0,
            receipt_id="abort-first-request",
            observed_document={"team": ["new"], "actions": [{"id": "e"}]},
            observed_application_payload={"calculated": {"total": 2}},
        )
    assert ack_expired.value.reason == "restore_ack_expired"


def test_garbage_collection_only_unlinks_safe_unreferenced_artifact_names(tmp_path):
    result = store(tmp_path)
    referenced = {
        path.name
        for path in tmp_path.glob("*-*.json")
        if path.name != STORE_FILE
    }
    orphan = tmp_path / ("document-body-" + "f" * 64 + ".json")
    orphan.write_text("{}", encoding="utf-8")
    unrelated = tmp_path / "user-notes.json"
    unrelated.write_text("keep", encoding="utf-8")
    hard_link = tmp_path / ("application-payload-" + "e" * 64 + ".json")
    hard_link_created = False
    if hasattr(os, "link"):
        source_file = tmp_path / "foreign.bin"
        source_file.write_bytes(b"foreign")
        try:
            os.link(source_file, hard_link)
            hard_link_created = True
        except OSError:
            pass

    collected = result.collect_garbage(expected_store_revision=0)
    assert collected["status"] == "collected"
    assert not orphan.exists()
    assert unrelated.exists()
    assert all((tmp_path / name).exists() for name in referenced)
    if hard_link_created:
        assert hard_link.exists()
        assert hard_link.name in collected["skipped"]
    with pytest.raises(PlanError) as stale:
        result.collect_garbage(expected_store_revision=1)
    assert stale.value.reason == "stale_context"


def test_storage_usage_reports_deduplicated_manual_and_current_references(tmp_path):
    result = store(tmp_path)
    result.create_checkpoint(
        expected_store_revision=0,
        request_id="usage-manual",
        name="保留",
        document={"team": ["old"], "actions": []},
        application_payload={"calculated": {"total": 1}},
    )
    usage = result.storage_usage()
    assert usage["manual_checkpoint_count"] == 1
    assert usage["automatic_checkpoint_count"] == 0
    assert usage["referenced_artifact_count"] == 2
    assert usage["artifact_file_count"] == 2
    assert usage["unreferenced_artifact_file_count"] == 0
