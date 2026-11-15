"""S1 actual archived source shape checks; these do not admit App sources."""
import copy
import json
import math
from pathlib import Path

import pytest

from genshin_app.equipment.static_inclusion import compile_static_inclusion
from genshin_db.runtime_catalog import fingerprint

MATERIALS = json.loads((Path(__file__).parent / "fixtures/static_inclusion_materials.json").read_text(encoding="utf8"))


def material():
    return copy.deepcopy(MATERIALS[0])


def rehash(m):
    for key in ("parent", "source"):
        value = m[key]
        value["content_fingerprint"] = fingerprint({k: v for k, v in value.items() if k != "content_fingerprint"})


def parent(m):
    return json.loads(m["parent"]["payload_json"])


def put_parent(m, raw):
    m["parent"]["payload_json"] = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    m["parent"]["definition"]["node_sha256"] = fingerprint(raw)
    rehash(m)


def assert_pending(m, code=None):
    result = compile_static_inclusion(m)
    assert result["status"] == "pending", result
    assert "declaration" not in result
    assert result["verification_scope"] == "materialized_consistency_no_source_authority"
    assert result["requires_fresh_db_admission"] is True
    if code:
        assert result["issues"][0]["code"] == code, result
    return result


@pytest.mark.parametrize("index", range(len(MATERIALS)))
def test_actual_same_snapshot_materials(index):
    m = copy.deepcopy(MATERIALS[index])
    result = compile_static_inclusion(m)
    assert result["status"] == "shape_ready", result
    assert result["requires_fresh_db_admission"] is True
    assert result["verification_scope"] == "materialized_consistency_no_source_authority"
    assert result["source_authority"] == "not_granted"
    assert not any(k in result for k in ("rules", "buffs", "attribute_model", "execution_fingerprint"))
    d = result["declaration"]
    assert d["numeric_application"] is False
    assert d["delivery"] == "already_in_given_panel"
    assert d["nonextra_authority"] == "not_inferred"
    assert d["required_count"] == 2 and d["source_index"] == 0
    assert d["properties"][0]["value"] == m["normalized"]["add_props"][0]["value"]
    assert d["properties"][0]["source_index"] == 0


@pytest.mark.parametrize("value", [None, "", "Anything", False, {}])
def test_even_empty_open_config_is_not_missing(value):
    m = material(); m["source"]["affix"]["record"]["openConfig"] = value; rehash(m)
    assert_pending(m, "raw_affix_shape")


@pytest.mark.parametrize("mutation", ["unknown_raw", "unknown_child", "nonzero_param", "empty_props", "duplicate_prop", "unknown_prop", "bad_param_length", "bad_prop_length"])
def test_unknown_or_non_closed_raw_forms(mutation):
    m = material(); raw = m["source"]["affix"]["record"]
    if mutation == "unknown_raw": raw["condition"] = "on_hit"
    if mutation == "unknown_child": raw["addProps"][0]["condition"] = None
    if mutation == "nonzero_param": raw["paramList"][1] = 1
    if mutation == "empty_props": raw["addProps"] = [{}, {}, {}]
    if mutation == "duplicate_prop": raw["addProps"][1] = copy.deepcopy(raw["addProps"][0])
    if mutation == "unknown_prop": raw["addProps"][0]["propType"] = "FIGHT_PROP_HEAL_ADD"
    if mutation == "bad_param_length": raw["paramList"].append(0)
    if mutation == "bad_prop_length": raw["addProps"].append({})
    rehash(m); assert_pending(m)


@pytest.mark.parametrize("value", [True, None, "0.18", float("nan"), float("inf"), -1, -0.0, 0, 10**400])
def test_numeric_values_are_finite_positive_nonboolean(value):
    m = material(); m["source"]["affix"]["record"]["addProps"][0]["value"] = value
    if not isinstance(value, float) or math.isfinite(value):
        rehash(m)
    assert_pending(m)


@pytest.mark.parametrize("mutation", ["parent_condition", "bad_need_type", "need_duplicate", "need_reordered", "bad_metadata", "wrong_set", "wrong_group", "wrong_level", "wrong_threshold", "too_few_pieces", "weapon", "bad_owner", "no_policy", "wrong_mode", "stale_commit", "stale_snapshot", "bad_indexer", "nonempty_refs", "talent", "ability", "not_closed", "pending_source", "normalized_props", "normalized_level", "normalized_ref", "parent_node", "source_content"])
def test_relation_complete_source_and_normalization_guards(mutation):
    m = material(); raw = parent(m)
    if mutation == "parent_condition": raw["unknownCondition"] = False
    if mutation == "bad_need_type": raw["setNeedNum"][0] = True
    if mutation == "need_duplicate": raw["setNeedNum"] = [2, 2]
    if mutation == "need_reordered": raw["setNeedNum"] = [4, 2]
    if mutation == "bad_metadata": raw["containsList"] = [True]
    if mutation.startswith("parent_") or mutation in ("bad_need_type", "need_duplicate", "need_reordered", "bad_metadata"):
        put_parent(m, raw)
    if mutation == "wrong_set": m["selection"]["set_id"] += 1
    if mutation == "wrong_group": m["source"]["affix"]["record"]["id"] += 1
    if mutation == "wrong_level": m["source"]["affix"]["record"]["level"] = 1
    if mutation == "wrong_threshold": m["selection"]["required_count"] = 4
    if mutation == "too_few_pieces": m["selection"]["count"] = 1
    if mutation == "weapon": m["selection"]["kind"] = "weapon"
    if mutation == "bad_owner": m["selection"]["provider"] = " "
    if mutation == "no_policy": m["selection"]["panel_policy"] = "dynamic"
    if mutation == "wrong_mode": m["selection"]["mode"] = "base"
    if mutation == "stale_commit": m["selection"]["data_version"]["upstream_commit"] = "0" * 40
    if mutation == "stale_snapshot": m["source"]["snapshot"]["snapshot_id"] = "0" * 64
    if mutation == "bad_indexer": m["parent"]["snapshot"]["indexer_version"] = "7"
    if mutation == "nonempty_refs": m["source"]["references"] = [{"state": "resolved"}]
    if mutation == "talent": m["source"]["talent"] = {"record": {}}
    if mutation == "ability": m["source"]["abilities"] = [{}]
    if mutation == "not_closed": m["source"]["reference_closed"] = False
    if mutation == "pending_source": m["source"]["status"] = "pending"
    if mutation == "normalized_props": m["normalized"]["add_props"][0]["value"] = 0.5
    if mutation == "normalized_level": m["normalized"]["source_level"] = 1
    if mutation == "normalized_ref": m["normalized"]["source_refs"][0]["key"]["affixId"] += 1
    rehash(m)
    if mutation == "parent_node": m["parent"]["definition"]["node_sha256"] = "0" * 64; rehash(m)
    if mutation == "source_content": m["source"]["content_fingerprint"] = "0" * 64
    assert_pending(m)


def test_ratio_remains_ratio_no_base_or_extra_delivery():
    result = compile_static_inclusion(material())
    p = result["declaration"]["properties"][0]
    assert p["stat"] == "atk" and p["unit"] == "ratio"
    assert p["application"] == "base_fraction_already_in_panel"
    assert "flat_value" not in p and "base_atk" not in result["declaration"]


def test_independent_sources_and_provider_bind_identity_not_stat_dedupe():
    m = material(); r1 = compile_static_inclusion(m)
    m["selection"]["provider"] = "other"
    r2 = compile_static_inclusion(m)
    assert r1["certificate_fingerprint"] != r2["certificate_fingerprint"]
    assert r1["declaration"]["properties"] == r2["declaration"]["properties"]


def test_entire_panel_and_raw_payload_change_certificate_without_certifying_truth():
    m = material(); r1 = compile_static_inclusion(m)
    m["selection"]["panel_fingerprint"] = "0" * 64
    r2 = compile_static_inclusion(m)
    assert r2["status"] == "shape_ready"
    assert r1["certificate_fingerprint"] != r2["certificate_fingerprint"]
    assert r2["declaration"]["basis_authority"] == "caller_given_panel_policy"


def test_deep_copy_and_order_stability():
    m = material(); before = copy.deepcopy(m); r1 = compile_static_inclusion(m)
    r2 = compile_static_inclusion(json.loads(json.dumps(m, sort_keys=True)))
    assert r1 == r2 and m == before
    r1["declaration"]["properties"][0]["value"] = 99
    assert compile_static_inclusion(m) == r2 and m == before


@pytest.mark.parametrize("value", [None, [], {}, "x", {"contract": "static-inclusion-material-v1"}])
def test_malformed_input_is_controlled(value):
    assert_pending(value)


def test_duplicate_json_keys_bounded_depth_and_material_size():
    m = material(); m["parent"]["payload_json"] = '{"setId":1,"setId":2}'; rehash(m)
    assert_pending(m)
    m = material(); m["selection"]["provider"] = "x" * (1024 * 1024)
    assert_pending(m)
    m = material(); x = m
    for _ in range(100): x["nested"] = {}; x = x["nested"]
    assert_pending(m)


@pytest.mark.parametrize("container,key", [("relation", "source_index"), ("relation", "required_count"),
                                         ("selection", "set_id"), ("selection", "required_count"),
                                         ("selection", "count"), ("normalized", "source_level")])
def test_identity_never_uses_float_as_integer(container, key):
    m = material(); m[container][key] = float(m[container][key]); rehash(m)
    assert_pending(m)


def test_negative_zero_parameters_and_bool_level_rejected():
    m = material(); m["source"]["affix"]["record"]["paramList"][0] = -0.0; rehash(m)
    assert_pending(m)
    m = material(); m["source"]["affix"]["record"]["level"] = False; rehash(m)
    assert_pending(m)


def test_full_local_rehash_can_only_show_shape_not_authenticate_game_source():
    m = material()
    m["source"]["affix"]["record"]["addProps"][0]["value"] = 0.19
    m["normalized"]["add_props"][0]["value"] = 0.19
    rehash(m)
    result = compile_static_inclusion(m)
    assert result["status"] == "shape_ready"
    assert result["source_authority"] == "not_granted" and result["requires_fresh_db_admission"] is True
    assert result["material_fingerprint"] != compile_static_inclusion(material())["material_fingerprint"]
    assert "raw_source_complete" not in result


def test_profile_and_live_mapping_cannot_drift_under_old_fingerprint(monkeypatch):
    from genshin_app.equipment import static_inclusion as compiler
    monkeypatch.setitem(compiler.PROFILE, "version", 2)
    assert_pending(material(), "profile_changed")


@pytest.mark.parametrize("key", ["parent", "source", "relation", "normalized", "selection"])
def test_missing_required_material_is_controlled(key):
    m = material(); del m[key]; assert_pending(m)


def test_wide_material_and_utf8_budget_reject_before_canonical_serialization(monkeypatch):
    from genshin_app.equipment import static_inclusion as compiler
    from genshin_db import runtime_catalog

    actual = compiler.dumps
    # Fixed registry integrity is checked before material validation. A second
    # profile compares its trusted constants; no caller input may be serialized.
    constants = [item for profile, _ in compiler._REGISTERED_PROFILES
                 for item in (profile, *profile.values())]
    calls = []
    def guarded(value):
        assert any(value is item for item in constants), "untrusted material serialized before its budget check"
        calls.append(value)
        return actual(value)
    monkeypatch.setattr(compiler, "dumps", guarded)
    monkeypatch.setattr(runtime_catalog, "dumps", guarded)
    assert_pending({str(i): 0 for i in range(compiler.MAX_NODES)}, "material_limit")
    assert all(any(value is item for item in constants) for value in calls)
    assert_pending(["冰" * 32000] * 3, "material_limit")
    assert all(any(value is item for item in constants) for value in calls)
