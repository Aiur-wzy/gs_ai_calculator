"""Independent EC08 source-closure review using a saved real bundle, never live DB."""
from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from test_target_state_review import direct, execute, inputs

from genshin_engine import runtime_target_state_compiler as compiler

FIXTURE = Path(__file__).parent / "fixtures/runtime_target_state_blizzard_current.json"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def bundle():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def rehash(value):
    value["content_fingerprint"] = digest({k: v for k, v in value.items() if k != "content_fingerprint"})


def scenario():
    out = compiler.target_state_compiler_caps()["default_scenario"]
    out.update(status="assumed", source="独立测试：固定普通目标与装备、条件暴率未含；接受历史证据边界")
    return out


def compile_one(value, scene=None, owner="a", source_id="independent-source:upstream"):
    return compiler.compile_target_state_source(value, owner=owner, source_id=source_id,
                                                scenario=scenario() if scene is None else scene)


def accept(value=None):
    value = bundle() if value is None else value
    scene = scenario()
    preview = compile_one(value, scene)
    assert preview["status"] == "pending", preview["issues"]
    scene.update(preview["required_confirmation"])
    out = compile_one(value, scene)
    assert out["status"] == "ready", out["issues"]
    return value, scene, out


def raw_pointer(value, pointer):
    for token in pointer.lstrip("/").split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        value = value[int(token)] if isinstance(value, list) else value[token]
    return value


def test_review_real_source_preserves_complete_branch_inventory_exact_parameters_and_partial_scope(monkeypatch):
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: pytest.fail("pure projection opened a database"))
    original = bundle()
    value, _scene, out = accept(copy.deepcopy(original))
    assert value == original
    assert out["projection_status"] == "ready" and out["whole_source_status"] == "partial"
    assert out["coverage_certificate"]["source_complete"] is False
    assert out["coverage_certificate"]["gameplay_verified"] is False
    assert out["execution_binding"]["kind"] == "persistent_equipment"
    assert out["rules"] == out["initial_rules"] == out["static_parts"] == []
    assert len(out["branches"]) == 4
    entry = value["abilities"][0]
    for branch in out["branches"]:
        relative = branch["json_pointer"][len(entry["json_pointer"]):]
        expected = raw_pointer(entry["payload"], relative)
        assert branch["raw"] == expected
        assert branch["node_sha256"] == digest(expected)
        assert branch["source_sha256"] == entry["source_sha256"]
    assert all(branch["execution_status"] == "unsupported" for branch in out["branches"][2:])
    assert all(len(branch["attack_tags"]) == 7 for branch in out["branches"][2:])
    assert [effect["value"] for effect in out["fixed_effect_sources"][0]["effects"]] == [
        0.20000000298023224, 0.20000000298023224]


def test_review_real_projected_source_reproduces_independent_before_state_damage():
    _value, _scene, out = accept()
    data = inputs(("cryo", "hydro", "physical", "anemo", "physical"), gauges=(1, 1, 1, 4, 1))
    data[2]["fixed_effect_sources"] = out["fixed_effect_sources"]
    result = execute(data)
    raw = 0.20000000298023224
    assert direct(result) == pytest.approx({"h0": 45, "h1": 45*(1+raw), "h2": 45*(1+2*raw),
                                           "h3": 45*(1+2*raw), "h4": 45}, abs=1e-10)


@pytest.mark.parametrize("change", ["moon_type", "moon_tags", "moon_value", "classic_leaf", "new_sibling",
                                    "new_ability", "param", "source_hash", "source_pointer", "mixed_snapshot"])
def test_review_recomputed_envelope_cannot_launder_changed_source_or_excluded_branch(change):
    value, scene, _ = accept()
    node = value["abilities"][0]["payload"]
    mixins = node["GPPBBDEICKP"]["UNIQUE_Relic_CriticUpAgainstIceAndFrozen_Handler"]["LFIJIMIMBIH"]
    if change == "moon_type":
        mixins[2]["$type"] = "UnknownNewWriter"
    elif change == "moon_tags":
        mixins[2]["BFDEHDNLNJH"].append("Elemental_Burst")
    elif change == "moon_value":
        mixins[2]["GLIBANHDMCM"] = 1
    elif change == "classic_leaf":
        mixins[0]["IJDGNIIOHLM"][1]["IJDGNIIOHLM"].pop()
    elif change == "new_sibling":
        node["unreviewedOnHit"] = {"write": "Rate1"}
    elif change == "new_ability":
        value["abilities"].append(copy.deepcopy(value["abilities"][0]))
    elif change == "param":
        value["param_list"][0] = .3
        value["affix"]["record"]["paramList"][0] = .3
    elif change == "source_hash":
        value["abilities"][0]["source_sha256"] = "0"*64
    elif change == "source_pointer":
        value["abilities"][0]["json_pointer"] = "/21/Default"
    else:
        value["snapshot"]["snapshot_id"] = "different-source-snapshot"
    rehash(value)
    out = compile_one(value, scene)
    assert out["status"] in ("invalid", "pending"), out
    assert out["fixed_effect_sources"] == out["parts"] == []
    assert out["required_confirmation"] != {"accepted_assumptions_fingerprint": scene["accepted_assumptions_fingerprint"]}
    scene.update(out["required_confirmation"])
    retried = compile_one(value, scene)
    assert retried["status"] != "ready", "user confirmation cannot repair changed raw-source admission"


@pytest.mark.parametrize("change", ["owner", "source_id", "policy_evidence", "status"])
def test_review_old_confirmation_does_not_cross_provider_binding_or_purpose_change(change):
    value, scene, _ = accept()
    kwargs = {}
    if change in ("owner", "source_id"):
        kwargs[change] = "b" if change == "owner" else "other-source:upstream"
    else:
        scene["source" if change == "policy_evidence" else "status"] = "new purpose evidence" if change == "policy_evidence" else "confirmed"
    out = compile_one(value, scene, **kwargs)
    assert out["status"] == "pending", out["issues"]
    assert out["fixed_effect_sources"] == []


def test_review_reference_contract_change_invalidates_target_projection_confirmation(monkeypatch):
    value, scene, before = accept()
    from genshin_engine.elemental_schema import REFERENCE_CONTRACT
    monkeypatch.setitem(REFERENCE_CONTRACT, "contract_fingerprint", "f"*64)
    after = compile_one(value, scene)
    assert after["status"] == "pending", after["issues"]
    assert before["assumptions_fingerprint"] != after["assumptions_fingerprint"]


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), {"not": "a string"}, None])
def test_review_invalid_scenario_values_return_controlled_failure_not_uncaught_hash_error(bad):
    value = bundle()
    scene = scenario()
    scene["source"] = bad
    out = compile_one(value, scene)
    assert out["status"] in ("invalid", "pending") and out["fixed_effect_sources"] == []


def test_review_returned_receipt_and_caps_cannot_poison_cached_profile_or_future_projection():
    value, scene, out = accept()
    expected = copy.deepcopy(out)
    out["branches"][0]["raw"]["$type"] = "poisoned"
    out["fixed_effect_sources"][0]["effects"][0]["value"] = 1
    caps = compiler.target_state_compiler_caps()
    caps["evidence"]["assumptions"].append("poisoned")
    again = compile_one(value, scene)
    assert again == expected


def test_review_known_lf_source_bytes_require_fresh_confirmation_without_changing_numeric_projection():
    value, scene, before = accept()
    profile_path = Path(__file__).parents[1] / "src/genshin_runtime/resources/target_state_profiles.json"
    admitted = {row["source_path"]: row["git_blob_sha256"] for row in json.loads(profile_path.read_text(encoding="utf-8"))["files"]}
    for entry in [value["affix"], value["talent"], *value["abilities"]]:
        entry["source_sha256"] = admitted[entry["source_path"]]
    rehash(value)
    pending = compile_one(value, scene)
    assert pending["status"] == "pending"
    scene.update(pending["required_confirmation"])
    after = compile_one(value, scene)
    assert after["status"] == "ready", after["issues"]
    assert before["fixed_effect_sources"] == after["fixed_effect_sources"]
    assert before["execution_fingerprint"] != after["execution_fingerprint"]
