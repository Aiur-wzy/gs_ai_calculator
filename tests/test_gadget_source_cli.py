"""Short CLI receipts retain the distinction between mounting and dispatch."""
import copy
import json

import pytest

from genshin_app.cli import _runtime_output_summary, main


def payload():
    return {"status": "pending", "graph_scope": "gadget_identity_v1", "selection": {"gadget_id": 101},
            "snapshot": {"snapshot_id": "fixed", "indexer_version": "5"},
            "source_fingerprint": "source", "content_fingerprint": "content", "execution_support": "not_evaluated",
            "execution": {"status": "not_evaluated", "creation": "not_evaluated", "controller": "not_evaluated"},
            "origin": {"status": "verified_relationship", "parent": {"payload": "完整原父" * 10000}},
            "record": {"record": "完整配置行" * 10000},
            "config": {"status": "ready", "candidates": [{"payload": "完整配置" * 10000}]},
            "mounted_abilities": [{"status": "pending", "candidates": [], "selected": None}],
            "mount_list_status": "pending", "mount_list_field_status": "present", "issues": [{"code": "missing"}]}


def test_summary_does_not_echo_raw_and_output_remains_complete(
    tmp_path, monkeypatch, capsys, isolated_cli_database_admission,
):
    from genshin_db import runtime_queries

    value = payload()
    before = copy.deepcopy(value)
    monkeypatch.setattr(runtime_queries, "query_runtime_public", lambda *_: value)
    request, output = tmp_path / "request.json", tmp_path / "source.json"
    request.write_text(json.dumps({"action": "gadget_source_profile", "gadget_id": 101}), "utf8")
    assert main(["--data-dir", str(tmp_path), "db", "runtime", "--input", str(request),
                 "--output", str(output), "--summary"]) == 0
    captured = capsys.readouterr()
    summary = json.loads(captured.out)["data"]
    assert not captured.err and len(captured.out.encode("utf8")) < 3000 and "完整" not in captured.out
    assert summary["selection"] == {"gadget_id": 101}
    assert summary["origin_status"] == "verified_relationship" and summary["mount_list_field_status"] == "present"
    assert summary["mount_count"] == 1 and summary["ready_mount_count"] == 0
    assert summary["status"] == "pending" and summary["execution_support"] == "not_evaluated"
    assert value == before == json.loads(output.read_text("utf8"))


@pytest.mark.parametrize("status", ["ready", "pending"])
def test_batch_summary_does_not_promote_mounting_to_execution(status):
    value = payload()
    value["status"] = status
    batch = {"status": "complete", "snapshot": value["snapshot"], "counts": {"requested": 1, "ok": 1},
             "content_fingerprint": "batch", "items": [{"id": "gadget", "status": "ok",
             "request": {"action": "gadget_source_profile", "gadget_id": 101}, "data": value}]}
    summary = _runtime_output_summary(batch, {"action": "batch"})["items"][0]["result"]
    assert summary["status"] == status and summary["execution_support"] == "not_evaluated"
    assert summary["graph_scope"] == "gadget_identity_v1"
