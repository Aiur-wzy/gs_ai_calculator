"""Source admission and bounded pure projection tests; no database is opened."""
from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path

import pytest

from genshin_app.equipment.schema import PART
from genshin_engine import runtime_reaction_benefit_compiler as compiler
from genshin_engine.mechanics_schema import RULE
from genshin_engine.rotation import _schema_errors
from genshin_engine.runtime_compiler import _hash
from genshin_runtime.normalization import Profile, for_commit

FIXTURE = Path(__file__).parent / "fixtures/runtime_reaction_benefit_vv_current.json"
OLD_FIXTURE = Path(__file__).parent / "fixtures/runtime_reaction_benefit_vv.json"


def bundle():
    return json.loads(FIXTURE.read_text("utf8"))


def scene():
    scope = compiler.reaction_benefit_compiler_caps()["default_scenario"]
    scope.update(target_id="enemy", origin_target_range="within_source_range", initial_source_effects="none",
                 event_order_policy="declared_atomic_hit_order", status="assumed", source="工程范围核对，非当前客户端完整验证")
    return scope


def project(data=None, scope=None, *, confirm=True, **overrides):
    data, scope = bundle() if data is None else data, scene() if scope is None else scope
    kwargs = {"owner": "arbitrary-owner", "source_id": "equipment:upstream", "period": 20, **overrides}
    out = compiler.compile_reaction_benefit_source(data, scenario=scope, **kwargs)
    if confirm:
        scope = {**scope, **out["required_confirmation"]}
        out = compiler.compile_reaction_benefit_source(data, scenario=scope, **kwargs)
    return out


def rehash(data):
    data["content_fingerprint"] = _hash({k: v for k, v in data.items() if k != "content_fingerprint"})


def empty(out):
    assert out["status"] in ("pending", "invalid")
    assert out["parts"] == out["static_parts"] == out["rules"] == out["initial_rules"] == []
    assert out["unique_keys"] == [] and out["execution_fingerprint"] is None
    assert out["coverage_certificate"]["projection_complete"] is False


def pointer(value, path):
    for key in path.lstrip("/").split("/"):
        key = key.replace("~1", "/").replace("~0", "~")
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def test_ready_projection_reuses_three_valid_parts_and_two_existing_mechanics_rules(monkeypatch):
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("pure compiler opened DB"))
    data, scope = bundle(), scene()
    original = copy.deepcopy((data, scope))
    out = project(data, scope)
    assert (data, scope) == original
    assert out["status"] == out["execution_status"] == "ready", out["issues"]
    assert out["whole_source_status"] == "partial"
    assert out["coverage_certificate"]["gameplay_verified"] is False
    errors = []
    for part in out["parts"]:
        _schema_errors(part, PART, "part", errors)
        assert part["dependencies"]["mode"] == "source_only"
        assert part["conditions"][0]["scope"] == "always"
    for rule in out["rules"]:
        _schema_errors(rule, RULE, "rule", errors)
        assert rule["effects"][0]["value"] == .4000000059604645
        assert rule["timing"]["duration"] == 10
        assert rule["source_id"] in {"equipment:" + p["part_id"] for p in out["parts"]}
    assert not errors
    assert out["static_parts"][0]["rule"]["value"] == .6000000238418579
    assert len({p["part_id"] for p in out["parts"]}) == 3
    assert len({r["source_id"] for r in out["rules"]}) == 2
    assert out["provenance"]["parameter_bindings"]["Minus"]["kind"] == "ability_default"


def test_previous_accepted_pair_is_rejected_without_rewriting_history():
    historical = json.loads(OLD_FIXTURE.read_text("utf8"))
    original = copy.deepcopy(historical)
    out = project(historical)
    empty(out)
    assert historical == original


def test_all_raw_branches_hashes_and_complete_nodes_are_retained_without_source_mutation():
    data = bundle()
    out = project(data)
    entry = data["abilities"][0]
    assert len(out["branches"]) == 16
    for branch in out["branches"]:
        raw = pointer(entry["payload"], branch["json_pointer"][len(entry["json_pointer"]):])
        assert branch["raw"] == raw
        assert branch["node_sha256"] == _hash(raw)
        assert branch["source_sha256"] == entry["source_sha256"]
    assert len(out["raw_sources"]) == 3
    assert sum(b["kind"] == "reaction_listener" for b in out["branches"]) == 4
    assert any(b["kind"] == "landed_damage_path" and b["execution_status"] == "partial" for b in out["branches"])
    out["raw_sources"][0]["payload"]["altered"] = True
    out["provenance"]["source_byte_evidence"].clear()
    assert "altered" not in entry["payload"]
    assert project(data)["provenance"]["source_byte_evidence"]


def test_packaged_mapping_includes_independent_holdout_nodes_and_no_token_collisions():
    profile = copy.deepcopy(compiler._profile())
    base = for_commit(profile["commit"])
    local = Profile(profile["profile_id"], (profile["commit"],),
                    {**base.fields, **profile["fields"]}, {**base.types, **profile["types"]})
    assert len(profile["mapping_evidence"]["parent_checks"]) == 53
    assert all(c["whole_parent_equals_old"] for c in profile["mapping_evidence"]["parent_checks"])
    pairs = profile["mapping_evidence"]["paired_nodes"]
    assert len(pairs) == 6
    for pair in pairs:
        assert _hash(pair["raw"]) == pair["node_sha256"]
        assert _hash(pair["old_raw"]) == pair["old_node_sha256"]
        kind = "ability" if "/Ability/" in pair["path"] else "talent_config" if "/Talent/" in pair["path"] else "excel_record"
        assert local.normalize(pair["raw"], kind=kind) == pair["old_raw"]
    token, semantic = next(iter(profile["fields"].items()))
    with pytest.raises(ValueError, match="collision"):
        local.normalize({token: [], semantic: []}, kind="ability")


@pytest.mark.parametrize("change", ["reference_removed", "reference_duplicate", "reference_target", "reference_origin",
                                    "source_hash", "talent_flag", "raw_sibling", "new_ability", "params", "normalization"])
def test_recomputed_envelope_does_not_admit_unknown_control_or_forged_closed_graph(change):
    data = bundle()
    if change == "reference_removed":
        data["references"].pop()
    elif change == "reference_duplicate":
        data["references"].append(copy.deepcopy(data["references"][0]))
    elif change == "reference_target":
        data["references"][0]["resolved_definition_id"] = "f" * 64
    elif change == "reference_origin":
        data["references"][0]["from_definition_id"] = "f" * 64
    elif change == "source_hash":
        data["abilities"][0]["source_sha256"] = "f" * 64
    elif change == "talent_flag":
        data["talent"]["operations"][0]["CLGIKOPIKBC"] = False
    elif change == "raw_sibling":
        data["abilities"][0]["payload"]["additionalListener"] = {"unknown": True}
    elif change == "new_ability":
        data["abilities"].append(copy.deepcopy(data["abilities"][0]))
    elif change == "params":
        data["param_list"][0] = .7
    else:
        data["normalization"]["mapping_fingerprint"] = "f" * 64
        data["snapshot"]["normalization"] = copy.deepcopy(data["normalization"])
    rehash(data)
    empty(project(data))


@pytest.mark.parametrize("field,value", [("origin_target_range", "unknown"), ("initial_source_effects", "unknown"),
    ("event_order_policy", "unknown"), ("status", "pending"), ("source", ""), ("target_id", None),
    ("profile_version", True), ("single_player_fixed", 1), ("extra", True)])
def test_confirmation_cannot_upgrade_unknown_or_invalid_scenario(field, value):
    scope = scene()
    scope[field] = value
    empty(project(scope=scope))


@pytest.mark.parametrize("period", [False, 0, -1, .0000001, 1000000000.000001, float("inf"), float("nan")])
def test_period_preserves_existing_positive_exact_microsecond_boundary(period):
    empty(project(confirm=False, period=period))


@pytest.mark.parametrize("fault", ["bytes", "nodes", "depth", "object", "mixed_keys", "nan"])
def test_input_budgets_and_non_json_inputs_return_small_atomic_receipts(fault):
    data = bundle()
    if fault == "bytes":
        data["extra"] = "x" * compiler.MAX_BYTES
    elif fault == "nodes":
        data["extra"] = [0] * 16384
    elif fault == "depth":
        child = 0
        for _ in range(65):
            child = [child]
        data["extra"] = child
    elif fault == "object":
        data["extra"] = object()
    elif fault == "mixed_keys":
        data["extra"] = {1: True, "key": False}
    else:
        data["extra"] = float("nan")
    out = project(data, confirm=False)
    empty(out)
    assert out["status"] == "invalid"
    assert len(json.dumps(out, ensure_ascii=False, allow_nan=False).encode()) < 4096


def test_output_limit_clears_full_materialization_and_keeps_pending_certificate(monkeypatch):
    # A budget between the input and enriched receipt exercises the output guard.
    data, scope = bundle(), scene()
    raw_bytes = len(json.dumps([data, "arbitrary-owner", "equipment:upstream", scope, 20], ensure_ascii=False).encode())
    monkeypatch.setattr(compiler, "MAX_BYTES", raw_bytes + 512)
    out = project(data, scope)
    empty(out)
    assert out["issues"][0]["code"] == "response_too_large"
    assert not out["raw_sources"] and not out["provenance"]


def test_caps_are_independent_and_invalid_scene_codes_are_not_confirmable():
    caps = compiler.reaction_benefit_compiler_caps()
    assert set(caps["confirmable_issue_codes"]) == {"semantic_profile_acceptance_required", "scenario_evidence_pending"}
    assert set(caps["scene_input_issue_codes"]).isdisjoint(caps["confirmable_issue_codes"])
    caps["evidence"].clear()
    caps["default_scenario"]["status"] = "confirmed"
    fresh = compiler.reaction_benefit_compiler_caps()
    assert fresh["evidence"] and fresh["default_scenario"]["status"] == "pending"


def test_old_acceptance_does_not_survive_source_evidence_period_or_owner_change():
    data, scope = bundle(), scene()
    scope.update(project(data, scope, confirm=False)["required_confirmation"])
    assert project(data, scope, confirm=False)["status"] == "ready"
    empty(project(data, scope, confirm=False, owner="another-owner"))
    empty(project(data, scope, confirm=False, period=21))
    scope["source"] += "；场景说明修改"
    empty(project(data, scope, confirm=False))
