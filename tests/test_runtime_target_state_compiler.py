"""Pure admitted-node target-condition projection, not whole-source execution."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from genshin_engine import runtime_target_state_compiler as compiler
from genshin_engine.mechanics_schema import FIXED_SOURCE
from genshin_engine.rotation import _schema_errors
from genshin_engine.runtime_compiler import _hash, compile_source
from genshin_runtime.normalization import for_commit

FIXTURE = Path(__file__).parent / "fixtures/runtime_target_state_blizzard_current.json"
OLD_FIXTURE = Path(__file__).parent / "fixtures/runtime_target_state_blizzard.json"


def bundle():
    return json.loads(FIXTURE.read_text("utf8"))


def scenario():
    value = compiler.target_state_compiler_caps()["default_scenario"]
    value.update(status="assumed", source="工程核对：固定装备、普通Monster单目标、常驻面板已含而条件暴率未含")
    return value


def compile_case(data=None, scope=None, *, confirm=True, owner="ayaka", source_id="blizzard:upstream"):
    data = bundle() if data is None else data
    scope = scenario() if scope is None else scope
    result = compiler.compile_target_state_source(data, owner=owner, source_id=source_id, scenario=scope)
    if confirm:
        scope = {**scope, **result["required_confirmation"]}
        result = compiler.compile_target_state_source(data, owner=owner, source_id=source_id, scenario=scope)
    return result


def rehash(data):
    data["content_fingerprint"] = _hash({k: v for k, v in data.items() if k != "content_fingerprint"})


@pytest.mark.parametrize("source", [{"opaque": object()}, {"huge": "x" * (compiler.MAX_BYTES + 1)}])
def test_invalid_or_oversized_source_is_not_echoed_in_error_receipt(source):
    data = bundle()
    data["source"] = source
    out = compile_case(data, confirm=False)
    assert out["status"] == "invalid"
    assert out["provenance"]["source"] == {}
    assert len(json.dumps(out, ensure_ascii=False, allow_nan=False).encode()) < 100000


def test_precise_values_predicates_and_persistent_parts():
    out = compile_case()
    assert out["status"] == out["projection_status"] == out["execution_status"] == "ready"
    assert out["whole_source_status"] == "partial"
    assert out["coverage_certificate"]["source_complete"] is False
    assert out["coverage_certificate"]["projection_complete"] is True
    assert out["rules"] == out["initial_rules"] == out["static_parts"] == []
    fixed, = out["fixed_effect_sources"]
    errors = []
    _schema_errors(fixed, FIXED_SOURCE, "fixed", errors)
    assert errors == []
    assert fixed["id"].startswith("upstream.") and fixed["source_id"] == "blizzard:upstream"
    assert fixed["availability"] == "fixed" and fixed["damage_scope"] == "classic_hits_only"
    assert [e["value"] for e in fixed["effects"]] == [0.20000000298023224] * 2
    assert fixed["effects"][0]["target_predicate"] == {"kind": "any", "children": [
        {"kind": "target_aura_in", "elements": ["cryo"]}, {"kind": "target_aura_in", "elements": ["frozen"]}]}
    assert fixed["effects"][1]["target_predicate"] == {"kind": "target_status", "status_id": "element_freeze"}
    assert out["parts"][0]["rule"] == {"kind": "fixed_effect_source", "fixed_source_id": fixed["id"]}
    assert out["parts"][0]["delivery"] == {"kind": "fixed_effect_source"}
    assert out["parts"][0]["dependencies"]["mode"] == "source_only"
    assert out["parts"][0]["conditions"][0]["scope"] == "always"
    assert out["parts"][0]["conditions"][0]["evidence"][0]["profile_id"] == compiler.PROFILE_ID


def test_previous_accepted_pair_is_rejected_without_rewriting_history():
    historical = json.loads(OLD_FIXTURE.read_text("utf8"))
    original = copy.deepcopy(historical)
    out = compile_case(historical)
    assert out["status"] == "pending"
    assert out["parts"] == out["fixed_effect_sources"] == []
    assert historical == original


def test_all_four_raw_branches_and_source_byte_receipts_preserved():
    original = bundle()
    out = compile_case(original)
    assert [b["status"] for b in out["branches"]] == ["projected", "projected", "excluded_by_scope", "excluded_by_scope"]
    assert [b["execution_status"] for b in out["branches"]] == ["ready", "ready", "unsupported", "unsupported"]
    raw = original["abilities"][0]["payload"]["GPPBBDEICKP"]["UNIQUE_Relic_CriticUpAgainstIceAndFrozen_Handler"]["LFIJIMIMBIH"]
    for index, branch in enumerate(out["branches"]):
        assert branch["raw"] == raw[index] and branch["node_sha256"] == _hash(raw[index])
        assert branch["json_pointer"].endswith("/LFIJIMIMBIH/" + str(index))
    files = out["provenance"]["source_byte_evidence"]
    assert files and all(f["workspace_source_sha256"] != f["git_blob_sha256"] for f in files)
    assert all(f["workspace_git_crlf_normalized_equal"] and not f["workspace_git_raw_equal"] for f in files)


def test_pending_confirmation_exposes_no_active_parts():
    out = compile_case(confirm=False)
    assert out["status"] == "pending"
    assert out["fixed_effect_sources"] == out["parts"] == out["unique_keys"] == []
    assert out["required_confirmation"]["accepted_assumptions_fingerprint"]
    pending = compiler.target_state_compiler_caps()["default_scenario"]
    out = compile_case(scope=pending)
    assert out["status"] == "pending"
    assert any(i["code"] == "scenario_evidence_pending" for i in out["issues"])


@pytest.mark.parametrize("change", [
    lambda b: b["source"].update(upstream_commit="0" * 40),
    lambda b: b["snapshot"].update(snapshot_id="mixed"),
    lambda b: b["abilities"][0].update(source_sha256="0" * 64),
    lambda b: b["abilities"][0].update(json_pointer="/21/Default"),
    lambda b: b["abilities"][0].update(variant_key="Changed"),
    lambda b: b["abilities"][0]["payload"].update(unknown_operation=True),
    lambda b: b["affix"]["record"]["paramList"].__setitem__(0, .3),
    lambda b: b["param_list"].__setitem__(0, .3),
    lambda b: b["talent"]["operations"].append({"$type": "UNKNOWN"}),
    lambda b: b["abilities"].append(copy.deepcopy(b["abilities"][0])),
    lambda b: b["references"][0].update(resolution_status="missing"),
    lambda b: b.update(reference_closed=False),
    lambda b: b.update(extra_uninterpreted={"op": "unknown"}),
])
def test_changed_or_incomplete_source_cannot_be_confirmed(change):
    data = bundle()
    change(data)
    rehash(data)
    out = compile_case(data)
    assert out["status"] in ("pending", "invalid")
    assert out["parts"] == out["fixed_effect_sources"] == []
    assert "execution_fingerprint" not in out


def test_unknown_branch_retained_when_whole_node_guard_fails():
    data = bundle()
    mixins = data["abilities"][0]["payload"]["GPPBBDEICKP"]["UNIQUE_Relic_CriticUpAgainstIceAndFrozen_Handler"]["LFIJIMIMBIH"]
    mixins.append({"$type": "NEW_UNKNOWN", "meaning": "not inferred"})
    rehash(data)
    out = compile_case(data)
    assert out["status"] == "pending" and len(out["branches"]) == 5
    assert out["branches"][4]["raw"] == mixins[4]
    assert out["branches"][4]["status"] == "unsupported"


@pytest.mark.parametrize("scope", [None, [], {"source": float("nan")}, {"source": object()}])
def test_malformed_context_returns_invalid_not_exception(scope):
    out = compiler.compile_target_state_source(bundle(), owner="a", source_id="x:upstream", scenario=scope)
    assert out["status"] == "invalid" and out["parts"] == []
    json.dumps(out, allow_nan=False)


@pytest.mark.parametrize("key,value", [("single_player_fixed", False), ("single_target_monster", False),
    ("target_state_profile", "unverified"), ("damage_scope", "all"), ("static_policy", "execute"),
    ("conditional_crit_policy", "included"), ("profile_version", True), ("source", " ")])
def test_unsupported_scope_stays_nonexecuting(key, value):
    scope = scenario()
    scope[key] = value
    out = compile_case(scope=scope)
    assert out["status"] != "ready" and out["fixed_effect_sources"] == []


def test_reference_revision_and_scope_changes_invalidate_old_confirmation(monkeypatch):
    data, scope = bundle(), scenario()
    scope.update(compile_case(data, scope, confirm=False)["required_confirmation"])
    assert compile_case(data, scope, confirm=False)["status"] == "ready"
    changed = {**scope, "source": scope["source"] + "变更"}
    assert compile_case(data, changed, confirm=False)["status"] == "pending"
    monkeypatch.setattr(compiler, "REFERENCE_CONTRACT", {**compiler.REFERENCE_CONTRACT, "contract_fingerprint": "new-reference"})
    after = compile_case(data, scope, confirm=False)
    assert after["status"] == "pending"
    assert after["provenance"]["target_reference_fingerprint"] == "new-reference"


def test_inputs_and_returned_evidence_do_not_mutate_or_touch_global_profile():
    data, scope = bundle(), scenario()
    before = copy.deepcopy((data, scope))
    out = compile_case(data, scope)
    out["provenance"]["source_byte_evidence"][0]["workspace_source_sha256"] = "poison"
    assert (data, scope) == before
    assert compile_case(data, scope)["provenance"]["source_byte_evidence"][0]["workspace_source_sha256"] != "poison"
    base = for_commit(data["source"]["upstream_commit"])
    assert "KFIBKGLJJAD" not in base.types and "GLIBANHDMCM" not in base.fields
    old = compile_source(data, "ayaka", "blizzard:upstream", {}, 20)
    assert old["status"] != "ready" and old["rules"] == []
