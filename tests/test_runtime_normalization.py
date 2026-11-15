"""Pure mapping tests use injected fixtures, never a production mapping guess."""

import copy
import json

import pytest

from genshin_runtime import Profile, for_commit, normalize, raw_pointer, verify_receipt
from genshin_runtime import normalization as n

COMMIT = "a" * 40
OLD_REVIEWED_COMMIT = "342084292ba4c3c6ec2bc34c045de4de72ae8d53"
CURRENT_REVIEWED_COMMIT = "224708edc8056f27e9ffc4eacabe797d75274636"


@pytest.fixture(autouse=True)
def fresh_registry_cache():
    n._load_profiles.cache_clear()
    yield
    n._load_profiles.cache_clear()


@pytest.fixture
def profile():
    return Profile("fixture-v1", (COMMIT,), {
        "NAME": "abilityName", "MODS": "modifiers", "PARAMS": "abilitySpecials",
        "ADDED": "onAdded", "MODNAME": "modifierName", "PROP": "properties",
        "AMOUNT": "paramDelta", "SPECIAL": "paramSpecial", "TARGET": "target",
        "ABILITIES": "abilities",
    }, {"TYPE_ATTACH": "AttachModifier", "TYPE_MODIFY": "ModifyAbility"})


def test_copy_unknown_and_type_namespace(profile):
    raw = {"NAME": "TYPE_ATTACH", "ADDED": [{"$type": "TYPE_ATTACH", "MODNAME": "NAME",
            "unmapped": ["TYPE_MODIFY", {"$type": "unknown", "unchanged": None}]}]}
    original = copy.deepcopy(raw)
    actual = normalize(raw, profile=profile)
    assert actual["abilityName"] == "TYPE_ATTACH"
    assert actual["onAdded"][0] == {"$type": "AttachModifier", "modifierName": "NAME",
            "unmapped": ["TYPE_MODIFY", {"$type": "unknown", "unchanged": None}]}
    actual["onAdded"][0]["unmapped"].append("new")
    assert raw == original


def test_ability_variants_and_dynamic_names_remain_literal(profile):
    raw = [{"NAME": {"NAME": "NAME", "MODS": {"NAME": {"ADDED": [{
        "$type": "TYPE_ATTACH", "MODNAME": "NAME"}]}},
        "PARAMS": {"NAME": 2, "$type": "TYPE_ATTACH", "abilityName": 3}}}]
    result = profile.normalize(raw, kind="ability")
    body = result[0]["NAME"]
    assert body["abilityName"] == "NAME"
    assert list(body["modifiers"]) == ["NAME"]
    assert body["abilitySpecials"] == {"NAME": 2, "$type": "TYPE_ATTACH", "abilityName": 3}
    assert body["modifiers"]["NAME"]["onAdded"][0]["$type"] == "AttachModifier"
    assert profile.normalize(raw, kind="ability_source") == result


def test_talent_root_and_operation_parameters_are_distinct(profile):
    raw = {"NAME": [{"$type": "TYPE_MODIFY", "NAME": "NAME", "SPECIAL": "AMOUNT",
                     "AMOUNT": 0.5}], "$type": []}
    result = profile.normalize(raw, kind="talent")
    assert list(result) == ["NAME", "$type"]
    assert result["NAME"] == [{"$type": "ModifyAbility", "abilityName": "NAME",
                               "paramSpecial": "AMOUNT", "paramDelta": 0.5}]
    assert profile.normalize(raw, kind="talent_config") == result
    assert profile.normalize(raw["NAME"], kind="talent_config") == result["NAME"]


def test_dynamic_properties_and_global_values(profile):
    raw = {"PROP": {"NAME": 1, "abilityName": 2}, "globalValues": {"NAME": 4}}
    assert profile.normalize(raw, kind="modifier") == {
        "properties": {"NAME": 1, "abilityName": 2}, "globalValues": {"NAME": 4}}


@pytest.mark.parametrize("kind", ["ability", "modifier", "operation"])
def test_key_collision_rejected_without_mutation(profile, kind):
    raw = {"NAME": "one", "abilityName": "two"}
    with pytest.raises(ValueError, match="collision"):
        profile.normalize(raw, kind=kind)
    with pytest.raises(ValueError, match="collision"):
        profile.raw_pointer(raw, "/abilityName", kind=kind)
    assert raw == {"NAME": "one", "abilityName": "two"}


def test_distinct_obfuscated_keys_with_same_target_collide_only_when_present():
    p = Profile("p", (COMMIT,), {"A": "name", "B": "name"}, {})
    assert p.normalize({"A": 1}) == {"name": 1}
    assert p.raw_pointer({"B": 2}, "/name") == "/B"
    with pytest.raises(ValueError, match="collision"):
        p.normalize({"A": 1, "B": 1})


def test_raw_pointer_tracks_variant_dynamic_map_and_typed_value(profile):
    raw = [{"NAME": {"MODS": {"NAME": {"ADDED": [{"$type": "TYPE_ATTACH"}]}}}}]
    ptr = "/0/NAME/modifiers/NAME/onAdded/0/$type"
    assert raw_pointer(raw, ptr, profile=profile) == "/0/NAME/MODS/NAME/ADDED/0/$type"
    assert profile.raw_pointer(raw, "") == ""
    assert profile.raw_pointer({"NAME": [{"AMOUNT": 3}]}, "/NAME/0/paramDelta",
                               kind="talent") == "/NAME/0/AMOUNT"


def test_raw_pointer_escapes_tokens_instead_of_replacing_strings():
    p = Profile("p", (COMMIT,), {"X/Y~": "modifiers", "A~B/C": "onAdded"}, {})
    raw = {"X/Y~": {"modifiers/A~": {"A~B/C": [1]}}}
    assert p.raw_pointer(raw, "/modifiers/modifiers~1A~0/onAdded/0") == (
        "/X~1Y~0/modifiers~1A~0/A~0B~1C/0")
    assert p.raw_pointer({"": 1}, "/") == "/"
    assert p.raw_pointer({"~1": 1}, "/~01") == "/~01"


@pytest.mark.parametrize("pointer", ["name", "#", "/~", "/~2", "/a~x", "/onAdded/01",
                                     "/onAdded/-", "/onAdded/+0", "/onAdded/٠",
                                     "/onAdded/1", "/missing", "/onAdded/0/foo"])
def test_raw_pointer_rejects_invalid_or_missing_paths(profile, pointer):
    with pytest.raises(ValueError):
        profile.raw_pointer({"ADDED": [3]}, pointer)


def test_profile_detaches_and_freezes_mapping_inputs():
    fields, types, commits = {"A": "abilityName"}, {"T": "AttachModifier"}, [COMMIT]
    p = Profile("p", commits, fields, types)
    fp = p.mapping_fingerprint
    fields["A"], types["T"], commits[0] = "other", "Other", "b" * 40
    assert p.normalize({"A": "x", "$type": "T"}) == {"abilityName": "x", "$type": "AttachModifier"}
    assert p.mapping_fingerprint == fp and p.commits == (COMMIT,)
    with pytest.raises(TypeError):
        p.fields["A"] = "other"


def test_fingerprint_is_order_independent_and_covers_both_namespaces():
    a = Profile("p", (COMMIT,), {"A": "a", "B": "b"}, {"T": "t"})
    b = Profile("p", (COMMIT,), {"B": "b", "A": "a"}, {"T": "t"})
    c = Profile("p", (COMMIT,), {"B": "b", "A": "a"}, {"T": "u"})
    assert a.mapping_fingerprint == b.mapping_fingerprint != c.mapping_fingerprint


@pytest.mark.parametrize("kind", ["avatar_config", "gadget_config"])
def test_limited_config_scope_preserves_other_fields_types_and_names(profile, kind):
    body = {"ABILITIES": [{"NAME": "TYPE_ATTACH", "$type": "TYPE_ATTACH", "TARGET": "Self"}],
            "ADDED": [{"NAME": "outside-list-reference", "$type": "TYPE_MODIFY"}],
            "onAdded": [], "MODS": {"NAME": {"NAME": "inner-reference"}},
            "PARAMS": {"NAME": 1, "abilityName": 2}, "PROP": {"NAME": 3},
            "globalValues": {"NAME": 4}, "$type": "TYPE_ATTACH"}
    raw = {"NAME": body} if kind == "gadget_config" else body
    before = copy.deepcopy(raw)
    value = profile.normalize(raw, kind=kind)
    result = value["NAME"] if kind == "gadget_config" else value
    assert result["abilities"] == [{"abilityName": "TYPE_ATTACH", "$type": "TYPE_ATTACH", "TARGET": "Self"}]
    assert result["ADDED"] == [{"abilityName": "outside-list-reference", "$type": "TYPE_MODIFY"}]
    assert result["onAdded"] == []  # Unapproved ADDED is not renamed or collided.
    assert result["MODS"] == {"NAME": {"abilityName": "inner-reference"}}
    assert result["PARAMS"] == {"NAME": 1, "abilityName": 2}
    assert result["PROP"] == {"NAME": 3} and result["globalValues"] == {"NAME": 4}
    assert result["$type"] == "TYPE_ATTACH"
    prefix = "/NAME" if kind == "gadget_config" else ""
    assert profile.raw_pointer(raw, prefix + "/abilities/0/abilityName", kind=kind) == (
        prefix + "/ABILITIES/0/NAME")
    assert profile.raw_pointer(raw, prefix + "/PARAMS/NAME", kind=kind) == prefix + "/PARAMS/NAME"
    assert raw == before


@pytest.mark.parametrize("kind", ["avatar_config", "gadget_config"])
def test_limited_config_approved_key_collision_fails(profile, kind):
    body = {"ABILITIES": [], "abilities": []}
    raw = {"Device": body} if kind == "gadget_config" else body
    with pytest.raises(ValueError, match="collision"):
        profile.normalize(raw, kind=kind)
    with pytest.raises(ValueError, match="collision"):
        profile.raw_pointer(raw, "/Device/abilities" if kind == "gadget_config" else "/abilities", kind=kind)


def test_limited_config_pointer_keeps_gadget_root_identity_and_escaping():
    p = Profile("p", (COMMIT,), {"A/B": "abilities", "N~x": "abilityName"}, {})
    raw = {"A/B": {"A/B": [{"N~x": "device"}]}}
    assert p.normalize(raw, kind="gadget_config") == {"A/B": {"abilities": [{"abilityName": "device"}]}}
    assert p.raw_pointer(raw, "/A~1B/abilities/0/abilityName", kind="gadget_config") == "/A~1B/A~1B/0/N~0x"


def test_mapping_fingerprint_covers_kind_field_scopes(profile, monkeypatch):
    scopes = dict(n.KIND_FIELD_SCOPES)
    scopes["avatar_config"] = ("abilities",)
    monkeypatch.setattr(n, "KIND_FIELD_SCOPES", scopes)
    changed = Profile(profile.id, profile.commits, profile.fields, profile.types)
    assert changed.mapping_fingerprint != profile.mapping_fingerprint
    with pytest.raises(ValueError):
        changed.verify_receipt(profile.receipt(COMMIT), commit=COMMIT)


def test_mapping_fingerprint_covers_kind_type_scopes(profile, monkeypatch):
    monkeypatch.setattr(n, "KIND_TYPE_SCOPES", n.KIND_TYPE_SCOPES - {"operation"})
    changed = Profile(profile.id, profile.commits, profile.fields, profile.types)
    assert changed.mapping_fingerprint != profile.mapping_fingerprint


def test_normalization_version_two_rejects_version_one_receipt(profile):
    assert n.NORMALIZATION_VERSION == 2
    receipt = profile.receipt(COMMIT)
    receipt["normalization_version"] = 1
    with pytest.raises(ValueError):
        profile.verify_receipt(receipt, commit=COMMIT)


@pytest.mark.parametrize("kind,prefix,field", [
    ("gadget_config", "", "abilityName"),
    ("ability_source", "/0", "abilityName"),
    ("animator_config", "", "NAME"),
])
def test_large_literal_map_pointer_does_not_scan_siblings(profile, kind, prefix, field):
    class CountedMap(dict):
        item_scans = 0

        def items(self):
            self.item_scans += 1
            return super().items()

    named = CountedMap({f"device-{i}": {"NAME": f"ability-{i}"} for i in range(5000)})
    named["NAME/~"] = {"NAME": "literal-map-identity"}
    raw = [named] if kind == "ability_source" else named
    profile.normalize(raw, kind=kind)
    assert named.item_scans == 1
    named.item_scans = 0
    before = profile.receipt(COMMIT)
    assert profile.raw_pointer(raw, f"{prefix}/NAME~1~0/{field}", kind=kind) == f"{prefix}/NAME~1~0/NAME"
    with pytest.raises(ValueError, match="does not exist"):
        profile.raw_pointer(raw, f"{prefix}/absent", kind=kind)
    assert named.item_scans == 0
    assert profile.receipt(COMMIT) == before


class _MemoryResource:
    payload = None
    reads = 0

    def joinpath(self, *parts):
        assert parts == ("resources", "profiles.json")
        return self

    def read_text(self, *, encoding):
        assert encoding == "utf-8"
        self.reads += 1
        if self.payload is None:
            raise FileNotFoundError("no registry fixture")
        return self.payload


@pytest.fixture
def registry(monkeypatch):
    value = _MemoryResource()
    monkeypatch.setattr(n.resources, "files", lambda package: value)
    return value


def _registry(registry, monkeypatch, profiles):
    n._load_profiles.cache_clear()
    registry.payload = json.dumps({"schema_version": 1, "profiles": profiles})
    monkeypatch.setattr(n.resources, "files", lambda package: registry)


def _entry(profile):
    return {"id": profile.id, "commits": list(profile.commits),
            "fields": dict(profile.fields), "types": dict(profile.types)}


def test_registry_lookup_and_receipt_are_exact(registry, monkeypatch, profile):
    _registry(registry, monkeypatch, [_entry(profile)])
    installed = for_commit(COMMIT)
    assert installed.id == profile.id
    assert for_commit(COMMIT[:12]) is None
    assert for_commit(COMMIT.upper()) is None
    assert for_commit("b" * 40) is None
    receipt = installed.receipt(COMMIT)
    assert verify_receipt(receipt, commit=COMMIT).mapping_fingerprint == profile.mapping_fingerprint
    with pytest.raises(ValueError, match="unknown"):
        verify_receipt(receipt, commit="b" * 40)


def test_current_upstream_has_separate_exact_reviewed_profile():
    old = for_commit(OLD_REVIEWED_COMMIT)
    current = for_commit(CURRENT_REVIEWED_COMMIT)
    assert old is not None and current is not None
    assert old.id == "upstream-34208429-reviewed-schema-v1"
    assert current.id == "upstream-224708ed-reviewed-schema-v1"
    assert current.commits == (CURRENT_REVIEWED_COMMIT,)
    assert old.id != current.id
    assert old.mapping_fingerprint == current.mapping_fingerprint
    assert for_commit(CURRENT_REVIEWED_COMMIT[:12]) is None
    with pytest.raises(ValueError):
        current.verify_receipt(old.receipt(OLD_REVIEWED_COMMIT), commit=CURRENT_REVIEWED_COMMIT)
    with pytest.raises(ValueError):
        old.verify_receipt(current.receipt(CURRENT_REVIEWED_COMMIT), commit=OLD_REVIEWED_COMMIT)


@pytest.mark.parametrize("field,value", [
    ("profile_id", "unknown"), ("mapping_fingerprint", "0" * 64),
    ("upstream_commit", "b" * 40), ("normalization_version", 1),
    ("normalization_version", True), ("normalization_version", 1.0), ("extra", 1),
])
def test_changed_receipt_rejected(registry, monkeypatch, profile, field, value):
    _registry(registry, monkeypatch, [_entry(profile)])
    receipt = profile.receipt(COMMIT)
    receipt[field] = value
    with pytest.raises(ValueError):
        verify_receipt(receipt, commit=COMMIT)


def test_receipt_rejected_after_installed_mapping_changes(registry, monkeypatch, profile):
    receipt = profile.receipt(COMMIT)
    entry = _entry(profile)
    entry["fields"]["NAME"] = "changedName"
    _registry(registry, monkeypatch, [entry])
    with pytest.raises(ValueError):
        verify_receipt(receipt, commit=COMMIT)


def test_registry_absent_and_duplicate_failures(registry, monkeypatch, profile):
    monkeypatch.setattr(n.resources, "files", lambda package: registry)
    assert for_commit(COMMIT) is None
    _registry(registry, monkeypatch, [_entry(profile), _entry(profile)])
    with pytest.raises(ValueError, match="duplicate"):
        for_commit(COMMIT)
    entry = _entry(profile)
    entry["id"] = "another"
    _registry(registry, monkeypatch, [_entry(profile), entry])
    with pytest.raises(ValueError, match="duplicate"):
        for_commit(COMMIT)


def test_unknown_kind_preserves_whole_copy_and_pointer(profile):
    raw = {"NAME": [{"$type": "TYPE_ATTACH", "MODS": {"NAME": {"ADDED": []}}}]}
    result = profile.normalize(raw, kind="animator_config")
    assert result == raw
    result["NAME"][0]["MODS"]["NAME"]["ADDED"].append(1)
    assert raw["NAME"][0]["MODS"]["NAME"]["ADDED"] == []
    pointer = "/NAME/0/MODS/NAME/ADDED"
    assert profile.raw_pointer(raw, pointer, kind="animator_config") == pointer


def test_registry_is_cached_and_explicit_refresh_changes_receipt(registry, monkeypatch, profile):
    _registry(registry, monkeypatch, [_entry(profile)])
    original = for_commit(COMMIT)
    entry = _entry(profile)
    entry["types"]["TYPE_ATTACH"] = "Changed"
    registry.payload = json.dumps({"schema_version": 1, "profiles": [entry]})
    assert for_commit(COMMIT) is original
    assert registry.reads == 1
    n._load_profiles.cache_clear()
    assert for_commit(COMMIT).mapping_fingerprint != original.mapping_fingerprint
    with pytest.raises(ValueError):
        verify_receipt(original.receipt(COMMIT), commit=COMMIT)


@pytest.mark.parametrize("body", [
    '{"schema_version":1,"profiles":[],"profiles":[]}',
    '{"schema_version":1,"profiles":[{"id":"p","commits":["' + COMMIT +
    '"],"fields":{"A":"a","A":"b"},"types":{}}]}',
])
def test_registry_duplicate_json_keys_fail(registry, monkeypatch, body):
    registry.payload = body
    monkeypatch.setattr(n.resources, "files", lambda package: registry)
    with pytest.raises(ValueError, match="duplicate JSON key"):
        for_commit(COMMIT)


@pytest.mark.parametrize("overrides", [
    {"commits": ["short"]}, {"commits": [COMMIT, COMMIT]}, {"commits": None},
    {"fields": {"A": "$type"}}, {"fields": {"$type": "A"}}, {"types": {"T": 1}},
])
def test_invalid_profile_rejected(overrides):
    args = {"id": "p", "commits": (COMMIT,), "fields": {}, "types": {}}
    args.update(overrides)
    with pytest.raises(ValueError):
        Profile(**args)
