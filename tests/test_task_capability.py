import pytest

from genshin_app.equipment.compose_admission import _trusted_task_capability_resolutions
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import require_ready
from genshin_app.task_capability import (
    blocking_task_issues,
    build_trusted_effective_graph_scope,
    build_trusted_issue_resolution,
    classify_task_issues,
)


def test_legacy_issue_remains_conservatively_blocking():
    receipt = classify_task_issues([
        {"code": "missing_input", "path": "hits.h1", "message": "缺倍率"},
    ])
    assert receipt["status"] == "blocked"
    assert receipt["blocking"][0]["consumer"] == "legacy_effective_graph"
    assert receipt["blocking"][0]["required"] is True
    assert receipt["blocking"][0]["consumed"] is True


def test_unconsumed_or_optional_capability_only_degrades_current_task():
    rows = [
        {"code": "knowledge_missing", "message": "附录未安装",
         "consumer": "optional_hint", "required": False, "consumed": False},
        {"code": "runtime_family_unavailable", "message": "Animator缺失",
         "consumer": "timing_preview", "required": True, "consumed": False},
    ]
    receipt = classify_task_issues(rows)
    assert receipt["status"] == "degraded"
    assert receipt["counts"] == {"total": 2, "blocking": 0, "degraded": 2}
    assert blocking_task_issues(rows) == []


def test_only_required_consumed_issue_blocks():
    rows = [
        {"code": "core_data_missing", "message": "当前角色基础值缺失",
         "consumer": "damage.hit.h1", "required": True, "consumed": True,
         "category": "core_data_missing", "repair_hint": "补齐面板"},
    ]
    blocking = blocking_task_issues(rows)
    assert len(blocking) == 1
    assert blocking[0]["category"] == "core_data_missing"
    assert blocking[0]["repair_hint"] == "补齐面板"


def test_execution_gate_allows_visible_but_unconsumed_capability_gap():
    report = {
        "state": "ready",
        "missing_inputs": [{
            "code": "knowledge_missing",
            "message": "当前轴不使用的附录尚未安装",
            "consumer": "optional_hint",
            "required": False,
            "consumed": False,
        }],
    }

    assert require_ready(report) is None


def test_agent_shaped_resolution_fields_never_clear_a_blocker():
    row = {
        "code": "missing_input",
        "path": "hits.h1.multiplier",
        "message": "缺倍率",
        "resolved": True,
        "resolved_by": "agent_claim",
        "resolution": {"authority": "effective_graph_recomputed"},
    }
    receipt = classify_task_issues([row])
    assert receipt["status"] == "blocked"
    assert receipt["resolved"] == []
    assert "resolution" not in receipt["blocking"][0]


def test_verified_resolution_clears_only_the_exact_bound_issue():
    row = {
        "code": "missing_input",
        "path": "hits.h1.multiplier",
        "message": "缺倍率",
        "consumer": "damage.hit.h1",
        "required": True,
        "consumed": True,
    }
    resolution = build_trusted_issue_resolution(
        row,
        authority="verified_materialization_receipt",
        resolved_by="materialization:hit.h1",
        proof_fingerprint="a" * 64,
    )
    receipt = classify_task_issues([row], trusted_resolutions=[resolution])
    assert receipt["status"] == "ready"
    assert receipt["blocking"] == []
    assert receipt["resolved"][0]["resolution"] == {
        "authority": "verified_materialization_receipt",
        "resolved_by": "materialization:hit.h1",
        "proof_fingerprint": "a" * 64,
        "superseded_by": None,
    }

    changed = {**row, "message": "倍率与元素均缺失"}
    with pytest.raises(ValueError, match="当前缺项不一致"):
        classify_task_issues([changed], trusted_resolutions=[resolution])

    with pytest.raises(ValueError, match="目标不唯一"):
        classify_task_issues([row, dict(row)], trusted_resolutions=[resolution])


def test_resolution_and_effective_graph_scope_cannot_be_forged_with_json():
    row = {
        "code": "missing_input",
        "message": "缺倍率",
        "consumer": "damage.hit.h1",
        "required": False,
        "consumed": False,
    }
    with pytest.raises(TypeError, match="App验证回执"):
        classify_task_issues([row], trusted_resolutions=[{
            "issue_id": "x", "authority": "verified_materialization_receipt",
        }])
    with pytest.raises(TypeError, match="App重建"):
        classify_task_issues([row], effective_graph_scope={
            "damage.hit.h1": {"required": False, "consumed": False},
        })


def test_effective_graph_scope_overrides_producer_claims_for_migrated_consumer():
    row = {
        "code": "missing_input",
        "message": "缺倍率",
        "consumer": "damage.hit.h1",
        # A producer or plan-shaped value cannot downgrade the current graph.
        "required": False,
        "consumed": False,
    }
    scope = build_trusted_effective_graph_scope(
        [{"consumer": "damage.hit.h1", "required": True, "consumed": True}],
        graph_fingerprint="b" * 64,
    )
    receipt = classify_task_issues([row], effective_graph_scope=scope)
    assert receipt["status"] == "blocked"
    assert receipt["blocking"][0]["required"] is True
    assert receipt["blocking"][0]["consumed"] is True
    assert receipt["derivation"] == {
        "consumer_authority": "effective_graph",
        "effective_graph_fingerprint": "b" * 64,
        "resolution_authority": "app_verified_receipts_only",
    }


def _execution_graph(template=None, rotation=None, mechanics=None, matches=None):
    return {
        "template": template or {"characters": [], "hits": [], "buffs": []},
        "rotation_model": rotation,
        "mechanics_model": mechanics,
        "mechanics_matches": matches or [],
        "computed_value_overrides": [],
    }


def _runtime_graph(bundle):
    return {
        "template_fingerprint": fingerprint(bundle["template"]),
        "rotation_model_fingerprint": fingerprint(bundle["rotation_model"]),
        "mechanics_model_fingerprint": fingerprint(bundle["mechanics_model"]),
    }


def test_verified_materialization_repairs_only_matching_explicit_consumer(monkeypatch):
    graph = _execution_graph()
    runtime = {"status": "complete", "effective_graph": _runtime_graph(graph)}
    monkeypatch.setattr(
        "genshin_app.equipment.agent_execution_receipt.build_agent_execution_proof",
        lambda *_args: {
            "installed_coverage_keys": ["mechanics.rule.r1"],
            "proof_fingerprint": "1" * 64,
        },
    )
    issues = [
        {
            "code": "effect_pending", "path": "mechanics_model.rules.r1.source_id",
            "message": "机制来源待材料化", "consumer": "mechanics.rule.r1",
            "required": True, "consumed": True,
        },
        {"code": "missing_input", "path": "legacy", "message": "旧生产者缺项"},
    ]
    resolutions = _trusted_task_capability_resolutions(
        issues,
        original_plan={"extensions": {"agent_execution_materialization": {}}},
        materialization_receipt={}, effective_input={}, runtime_receipt=runtime,
        override_execution={"status": "not_declared", "receipt": None,
                            "automatic": graph},
        effective_graph=graph, materialized_graph_fingerprints=_runtime_graph(graph),
    )
    receipt = classify_task_issues(issues, trusted_resolutions=resolutions)
    assert [row["consumer"] for row in receipt["resolved"]] == ["mechanics.rule.r1"]
    assert [row["consumer"] for row in receipt["blocking"]] == ["legacy_effective_graph"]
    resolution = receipt["resolved"][0]["resolution"]
    assert resolution["authority"] == "verified_materialization_receipt"
    assert resolution["proof_fingerprint"] != "1" * 64


def test_materialization_resolution_rejects_stale_runtime_graph(monkeypatch):
    graph = _execution_graph()
    runtime = {"status": "complete", "effective_graph": {
        **_runtime_graph(graph), "template_fingerprint": "0" * 64,
    }}
    monkeypatch.setattr(
        "genshin_app.equipment.agent_execution_receipt.build_agent_execution_proof",
        lambda *_args: {
            "installed_coverage_keys": ["mechanics.rule.r1"],
            "proof_fingerprint": "1" * 64,
        },
    )
    with pytest.raises(PlanError, match="材料化运行时回执.*当前有效图"):
        _trusted_task_capability_resolutions(
            [{"code": "effect_pending", "message": "待修复",
              "consumer": "mechanics.rule.r1"}],
            original_plan={"extensions": {"agent_execution_materialization": {}}},
            materialization_receipt={}, effective_input={}, runtime_receipt=runtime,
            override_execution={"status": "not_declared", "receipt": None,
                                "automatic": graph},
            effective_graph=graph, materialized_graph_fingerprints=_runtime_graph(graph),
        )


def test_verified_override_repairs_matching_consumer_and_owner_collision_fails(monkeypatch):
    graph = _execution_graph()
    validated = []
    monkeypatch.setattr(
        "genshin_app.equipment.calculation_overrides.validate_execution_bundle_receipt",
        lambda receipt, bundle: validated.append((receipt, bundle)),
    )
    override = {
        "status": "applied", "automatic": graph, "effective": graph,
        "receipt": {
            "receipt_fingerprint": "2" * 64,
            "coverage": [{"override_id": "o1", "coverage_key": "damage.hit.h1"}],
        },
    }
    issues = [{
        "code": "missing_input", "path": "hits.h1.multiplier", "message": "缺倍率",
        "consumer": "damage.hit.h1", "required": True, "consumed": True,
    }]
    resolutions = _trusted_task_capability_resolutions(
        issues, original_plan={}, materialization_receipt=None,
        effective_input=None, runtime_receipt=None,
        override_execution=override, effective_graph=graph,
    )
    assert validated == [(override["receipt"], graph)]
    assert classify_task_issues(
        issues, trusted_resolutions=resolutions)["status"] == "ready"

    runtime = {"status": "complete", "effective_graph": _runtime_graph(graph)}
    monkeypatch.setattr(
        "genshin_app.equipment.agent_execution_receipt.build_agent_execution_proof",
        lambda *_args: {
            "installed_coverage_keys": ["damage.hit.h1"],
            "proof_fingerprint": "3" * 64,
        },
    )
    with pytest.raises(PlanError, match="多个修复所有者"):
        _trusted_task_capability_resolutions(
            issues,
            original_plan={"extensions": {"agent_execution_materialization": {}}},
            materialization_receipt={}, effective_input={}, runtime_receipt=runtime,
            override_execution=override, effective_graph=graph,
            materialized_graph_fingerprints=_runtime_graph(graph),
        )


def test_require_ready_uses_fresh_composer_classification_for_resolved_issue():
    graph = _execution_graph()
    issue = {
        "code": "missing_input", "path": "hits.h1.multiplier", "message": "缺倍率",
        "consumer": "damage.hit.h1", "required": True, "consumed": True,
    }
    resolution = build_trusted_issue_resolution(
        issue, authority="verified_override_receipt", resolved_by="override:o1",
        proof_fingerprint="4" * 64,
    )
    capability = classify_task_issues([issue], trusted_resolutions=[resolution])
    capability["derivation"]["effective_graph_fingerprint"] = fingerprint({
        "template": graph["template"], "rotation_model": graph["rotation_model"],
        "mechanics_model": graph["mechanics_model"],
        "mechanics_matches": graph["mechanics_matches"],
    })
    report = {
        "state": "ready", "missing_inputs": [issue], "task_capability": capability,
        "plan": {"resolved_template": graph["template"],
                 "rotation_model": graph["rotation_model"]},
        "mechanics_model": graph["mechanics_model"],
        "mechanics_matches": graph["mechanics_matches"],
    }
    assert require_ready(report) is None

    report["mechanics_matches"] = [{"id": "changed"}]
    with pytest.raises(PlanError, match="任务能力回执.*过期"):
        require_ready(report)
