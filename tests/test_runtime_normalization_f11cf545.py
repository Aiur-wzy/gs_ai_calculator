"""Exact source-backed migration; opaque data does not acquire execution power."""
import copy
import hashlib
import json
import re
from pathlib import Path

import pytest

from genshin_runtime import for_commit

ROOT = Path(__file__).resolve().parents[1]
OLD = "224708edc8056f27e9ffc4eacabe797d75274636"
TARGET = "f11cf545a7eee32a29999f960ebfba305f896ed3"
ENCODED = re.compile(r"[A-Z]{8,16}\Z")


def _known(value, named=False):
    if isinstance(value, dict):
        return {key: _known(child, key in {"modifiers", "abilitySpecials", "properties", "globalValues"})
                for key, child in value.items() if (named or not ENCODED.fullmatch(key))
                and not (key == "$type" and isinstance(child, str) and ENCODED.fullmatch(child))}
    if isinstance(value, list):
        return [_known(child) for child in value]
    return value


def test_exact_target_profile_has_source_evidence_and_retains_old_receipts():
    profile = for_commit(TARGET)
    review = json.loads((ROOT / "docs/pm/audits/2026-11-06-r9-normalization-source-review.json").read_text(encoding="utf-8"))
    payload = {key: value for key, value in review.items() if key != "review_fingerprint"}
    assert review["review_fingerprint"] == hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert profile.mapping_fingerprint == review["mapping_fingerprint"] == "cb52e3904c2c67dddc9f9d0a48301b760e44f032d530a2e82b39cc35c899a648"
    assert len(profile.fields) == 36 and len(profile.types) == 35
    assert not review["failures"] and review["old_profile_objects_unchanged"] is True
    assert for_commit(OLD).mapping_fingerprint == "506862bc6c0bf033d8272b75f193cd2a620fc513320b3223d07afcc6f04356e2"
    assert for_commit(TARGET[:8]) is None
    with pytest.raises(ValueError, match="not covered"):
        profile.receipt(OLD)
    changed = profile.receipt(TARGET)
    changed["mapping_fingerprint"] = for_commit(OLD).mapping_fingerprint
    with pytest.raises(ValueError, match="changed"):
        profile.verify_receipt(changed, commit=TARGET)


def test_role_ugc_nonrole_talent_and_gadget_records_preserve_known_semantics():
    fixtures = json.loads((ROOT / "tests/fixtures/runtime_normalization/f11cf545-source-records.json").read_text(encoding="utf-8"))
    assert len(fixtures) == 5
    old, target = for_commit(OLD), for_commit(TARGET)
    for row in fixtures:
        before = copy.deepcopy(row["target"])
        assert _known(old.normalize(row["old"])) == _known(target.normalize(row["target"])), row["source_path"]
        assert row["target"] == before


def test_dynamic_map_names_and_plain_values_are_not_decoded_as_fields_or_types():
    profile = for_commit(TARGET)
    raw = {"MMMADNCKFLA": "CAEBKCLOLHP", "PGMKHCKJIKM": {
        "MMMADNCKFLA": {"GPJONLFBEGF": 1, "ONACPNFMBFG": "CAEBKCLOLHP", "$type": "CAEBKCLOLHP"}}}
    normalized = profile.normalize(raw)
    assert normalized["abilityName"] == "CAEBKCLOLHP"
    assert normalized["modifiers"]["MMMADNCKFLA"] == {"duration": 1, "value": "CAEBKCLOLHP", "$type": "AttachModifier"}
    assert profile.raw_pointer(raw, "/modifiers/MMMADNCKFLA/duration") == "/PGMKHCKJIKM/MMMADNCKFLA/GPJONLFBEGF"


def test_ambiguous_normalized_keys_are_rejected_and_unknowns_remain_opaque():
    profile = for_commit(TARGET)
    with pytest.raises(ValueError, match="collision"):
        profile.normalize({"abilityName": "one", "MMMADNCKFLA": "other"})
    unknown = {"ZZZZZZZZZZZ": {"$type": "UnknownOpaqueType", "value": 9}}
    assert profile.normalize(unknown) == unknown
    avatar = profile.normalize({"BCDDFEHKKCI": [{"MMMADNCKFLA": "AvatarA"}], "GPJONLFBEGF": 3}, kind="avatar_config")
    assert avatar == {"abilities": [{"abilityName": "AvatarA"}], "GPJONLFBEGF": 3}
