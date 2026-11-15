"""Bounded resource summaries keep numeric availability distinct from applicability."""
from __future__ import annotations

import copy
import json

import pytest

from genshin_app.cli import _runtime_output_summary, main


def payload():
    return {"status": "partial", "scope": "base_skill_record", "snapshot": {"snapshot_id": "fixed"},
            "selection": {"character_id": 10000063, "skill_id": 10632, "slot": "skill"},
            "execution_support": "not_evaluated", "variant_applicability": {"status": "not_evaluated"},
            "gameplay_interpretation": "not_evaluated", "action_dispatch": {"status": "pending"},
            "resource_fingerprint": "resource", "content_fingerprint": "content",
            "identity_sources": {"raw": "identity-details" * 10000},
            "projection": {
                "base_cooldown_seconds": {"status": "ready", "value": 10, "source_field": "cdTime", "issues": []},
                "base_charge_count": {"status": "ready", "value": 1, "source_field": "maxChargeNum", "issues": []},
                "base_energy_cost": {"status": "pending", "value": None, "source_field": "costElemVal",
                                     "issues": [{"code": "missing_resource_field"}]},
                "cost_element": {"status": "pending", "value": None, "source_field": "costElemType", "issues": []}},
            "descriptions": {"status": "ready", "name": {"status": "ready", "text": "仰灵威召将役咒"},
                "description": {"status": "ready", "text": "长按原文不可进入摘要" * 10000}}}


def test_single_summary_retains_missing_values_without_echoing_raw_text(tmp_path, capsys, monkeypatch):
    from genshin_db import runtime_queries

    data = payload()
    frozen = copy.deepcopy(data)
    # This unit covers the bounded CLI projection, not database-release
    # admission.  The runtime query is already isolated below, so admit the
    # synthetic data directory explicitly instead of weakening production's
    # fail-closed startup gate.
    monkeypatch.setattr("genshin_app.cli._require_runtime_database", lambda _data_dir: {})
    monkeypatch.setattr(runtime_queries, "query_runtime_public", lambda *_: data)
    request, output = tmp_path / "query.json", tmp_path / "resource.json"
    request.write_text(json.dumps({"action": "action_resource_profile", "character_id": 10000063, "slot": "skill"}), "utf8")
    assert main(["--data-dir", str(tmp_path), "db", "runtime", "--input", str(request),
                 "--output", str(output), "--summary"]) == 0
    captured = capsys.readouterr()
    summary = json.loads(captured.out)["data"]
    assert not captured.err and len(captured.out.encode("utf8")) < 3500
    assert "长按原文" not in captured.out and "identity-details" not in captured.out
    assert summary["projection_summary"]["base_cooldown_seconds"]["value"] == 10
    assert summary["projection_summary"]["base_energy_cost"] == {
        "status": "pending", "value": None, "source_field": "costElemVal", "issue_count": 1}
    assert summary["variant_applicability"]["status"] == "not_evaluated"
    assert summary["action_dispatch"]["status"] == "pending"
    assert summary["resource_fingerprint"] == "resource"
    assert json.loads(output.read_text("utf8")) == frozen and data == frozen


@pytest.mark.parametrize("status", ["ready", "partial", "pending"])
def test_batch_summary_never_promotes_resource_or_variant_status(status):
    data = payload()
    data["status"] = status
    request = {"action": "action_resource_profile", "character_id": 10000063, "slot": "skill"}
    batch = {"status": "complete", "snapshot": {"snapshot_id": "fixed"},
             "counts": {"requested": 1, "ok": 1, "error": 0, "skipped": 0},
             "content_fingerprint": "batch", "items": [{"id": "resource", "status": "ok", "request": request, "data": data}]}
    before = copy.deepcopy(batch)
    summary = _runtime_output_summary(batch, {"action": "batch"})
    result = summary["items"][0]["result"]
    assert summary["status"] == "complete" and result["status"] == status
    assert result["projection_summary"]["base_energy_cost"]["value"] is None
    assert result["gameplay_interpretation"] == result["execution_support"] == "not_evaluated"
    assert result["variant_applicability"] == {"status": "not_evaluated"}
    assert batch == before
