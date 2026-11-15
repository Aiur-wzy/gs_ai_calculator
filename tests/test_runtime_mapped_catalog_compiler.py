"""Raw provenance and execution equivalence across a verified rename profile."""
import copy

import pytest
from test_runtime_compiler import bundle, compile_accepted

from genshin_db.runtime_catalog import (
    extract_definitions,
    extract_parameter_bindings,
    extract_references,
    fingerprint,
    resolve_pointer,
)
from genshin_engine import runtime_compiler
from genshin_runtime.normalization import Profile


@pytest.fixture
def profile():
    return Profile("test-mapping", ("a" * 40,), {
        "AAAAAAAAAAA": "abilityName", "BBBBBBBBBBB": "modifiers",
        "CCCCCCCCCCC": "onAdded", "DDDDDDDDDDD": "modifierName",
        "EEEEEEEEEEE": "paramSpecial", "FFFFFFFFFFF": "paramDelta",
        "GGGGGGGGGGG": "abilitySpecials",
    }, {"HHHHHHHHHHH": "AddAbility", "IIIIIIIIIII": "ModifyAbility",
        "JJJJJJJJJJJ": "ApplyModifier"})


def test_raw_definitions_edges_and_parameter_expressions(profile):
    raw = [{"Default": {
        "AAAAAAAAAAA": "A", "CCCCCCCCCCC": [{"$type": "JJJJJJJJJJJ", "DDDDDDDDDDD": "m/~"}],
        "BBBBBBBBBBB": {"m/~": {"unknown": False}},
    }}]
    definitions = extract_definitions("BinOutput/Ability/Temp/test.json", "ability", raw, "s", normalization=profile)
    ability, modifier = definitions
    assert ability["payload"] is raw[0]["Default"]
    assert modifier["json_pointer"] == "/0/Default/BBBBBBBBBBB/m~1~0"
    assert modifier["node_sha256"] == fingerprint(resolve_pointer(raw, modifier["json_pointer"]))
    ref = extract_references(ability, normalization=profile)[0]
    assert ref["json_pointer"] == "/0/Default/CCCCCCCCCCC/0/DDDDDDDDDDD"
    assert resolve_pointer(raw, ref["json_pointer"]) == "m/~"
    assert ref["field_name"] == "modifierName"
    operations = {"Entry": [{"$type": "IIIIIIIIIII", "AAAAAAAAAAA": "A",
                              "EEEEEEEEEEE": "GGGGGGGGGGG", "FFFFFFFFFFF": ["GGGGGGGGGGG", 0, "ADD"]}]}
    talent = extract_definitions("BinOutput/Talent/test.json", "talent", operations, "s", normalization=profile)[0]
    binding = extract_parameter_bindings(talent, normalization=profile)[0]
    reference = extract_references(talent, normalization=profile)[0]
    assert binding["target_reference_id"] == reference["reference_id"]
    assert binding["parameter_name"] == "GGGGGGGGGGG"
    assert binding["raw_expression_json"] == '["GGGGGGGGGGG",0,"ADD"]'
    assert reference["json_pointer"] == "/Entry/0/AAAAAAAAAAA"


def test_parameter_named_ability_name_is_not_a_reference_in_new_index(profile):
    raw = [{"Default": {"AAAAAAAAAAA": "A", "GGGGGGGGGGG": {"abilityName": 0}}}]
    definition = extract_definitions("a.json", "ability", raw, "s", normalization=profile)[0]
    assert extract_references(definition, normalization=profile) == []
    old = extract_definitions("a.json", "ability", profile.normalize(raw, kind="ability_source"), "s")[0]
    assert len(extract_references(old, indexer_version="2")) == 1


def obfuscate(value, fields, types, dynamic=False):
    if isinstance(value, list):
        return [obfuscate(v, fields, types) for v in value]
    if not isinstance(value, dict):
        return value
    return {(k if dynamic else fields.get(k, k)):
            (types.get(v, v) if k == "$type" and isinstance(v, str) else
             obfuscate(v, fields, types, not dynamic and k in ("modifiers", "abilitySpecials")))
            for k, v in value.items()}


def mapped_bundle(profile, source=None):
    source = bundle() if source is None else copy.deepcopy(source)
    source["source"]["upstream_commit"] = source["snapshot"]["upstream_commit"] = "a" * 40
    fields = {v: k for k, v in profile.fields.items()}
    types = {v: k for k, v in profile.types.items()}
    source["talent"]["operations"] = obfuscate(source["talent"]["operations"], fields, types)
    for ability in source["abilities"]:
        ability["payload"] = obfuscate(ability["payload"], fields, types)
    source["normalization"] = profile.receipt("a" * 40)
    return source


def install_test_profile(monkeypatch, profile):
    monkeypatch.setattr(runtime_compiler, "for_commit", lambda commit: profile if commit == "a" * 40 else None)

    def verify(receipt, *, commit):
        if receipt != profile.receipt(commit):
            raise ValueError("unverified receipt")
        return profile
    monkeypatch.setattr(runtime_compiler, "verify_receipt", verify)


def test_compiler_preserves_raw_bundle_and_locates_raw_fields(profile, monkeypatch):
    install_test_profile(monkeypatch, profile)
    source = mapped_bundle(profile)
    before = copy.deepcopy(source)
    result, _ = compile_accepted(source)
    old, _ = compile_accepted()
    assert result["status"] == "ready", result["issues"]
    for key in ("rules", "initial_rules", "static_parts", "parts", "unique_keys"):
        assert result[key] == old[key]
    assert source == before
    row = next(r for r in result["provenance"]["field_usage"] if r["path"].endswith("/paramDelta"))
    assert row["source_json_pointer"].endswith("/FFFFFFFFFFF")
    assert result["provenance"]["normalization"] == source["normalization"]


@pytest.mark.parametrize("case", ("missing_receipt", "wrong_receipt", "collision", "unknown_field"))
def test_bad_or_incomplete_normalization_never_executes(profile, monkeypatch, case):
    install_test_profile(monkeypatch, profile)
    source = mapped_bundle(profile)
    if case == "missing_receipt":
        del source["normalization"]
    elif case == "wrong_receipt":
        source["normalization"]["fingerprint"] = "tampered"
    elif case == "collision":
        source["abilities"][0]["payload"]["abilityName"] = "shadow"
    else:
        source["abilities"][0]["payload"]["UNPROVEN_FIELD"] = False
    result, _ = compile_accepted(source)
    assert result["status"] in ("invalid", "pending")
    assert result["rules"] == result["parts"] == result["static_parts"] == []


@pytest.mark.parametrize("mapped", (False, True))
def test_dynamic_names_with_pointer_escapes_keep_verifiable_provenance(profile, monkeypatch, mapped):
    install_test_profile(monkeypatch, profile)
    names = {"UNIQUE_Relic_TeamAtkupAfterElementalBurst": "modifier/~", "Rate": "rate/~"}

    def rename(value):
        if isinstance(value, dict):
            return {names.get(k, k): rename(v) for k, v in value.items()}
        if isinstance(value, list):
            return [rename(v) for v in value]
        return names.get(value, value) if isinstance(value, str) else value

    source = rename(bundle())
    if mapped:
        source = mapped_bundle(profile, source)
    result, _ = compile_accepted(source)
    assert result["status"] == "ready", result["issues"]
    rows = result["provenance"]["field_usage"] + result["semantic_assumptions"]
    assert not any(r.get("source_pointer_status") == "not_present" for r in rows)
    entries = [(source["talent"], "operations"), (source["affix"], "record")]
    entries += [(a, "payload") for a in source["abilities"]]
    for row in rows:
        if "source_json_pointer" in row:
            entry, key = next((e, k) for e, k in entries if e["source_path"] == row["source_path"])
            resolve_pointer(entry[key], row["source_json_pointer"][len(entry["json_pointer"]):])
    assert any("modifier~1~0" in r.get("source_json_pointer", "") for r in rows)
    assert any("rate~1~0" in r.get("source_json_pointer", "") for r in rows)
