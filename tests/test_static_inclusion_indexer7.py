"""Real-source saved negatives without opening the database."""
import copy
import gzip
import json
from pathlib import Path

import pytest

from genshin_app.equipment.delivery import validate_delivery_calculated
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.static_inclusion import (
    PROFILE_V2,
    PROFILE_V2_FINGERPRINT,
    select_static_inclusion_profile,
    static_inclusion_profile_capabilities,
)

HERE = Path(__file__).resolve().parent


def calculated():
    return json.loads(gzip.decompress((HERE / "fixtures/static_inclusion_profiles/indexer7-10001-calculated.json.gz").read_bytes()))["calculated"]


@pytest.mark.parametrize("field,value", [
    ("version_line", "beta"), ("upstream_commit", "f" * 40),
    ("schema_version", True), ("schema_version", 1.0), ("indexer_version", 7),
    ("snapshot_id", "f" * 64), ("manifest_fingerprint", "f" * 64),
    ("normalization.normalization_version", True), ("normalization.normalization_version", 2.0),
    ("normalization.mapping_fingerprint", "f" * 64),
    ("normalization.profile_id", "unknown"), ("normalization.upstream_commit", "f" * 40),
])
def test_actual_fixed_scope_never_selects_changed_or_mistyped_identity(field, value):
    snapshot = calculated()["result"]["static_inclusion_compilations"][0]["source_material"]["parent"]["snapshot"]
    assert select_static_inclusion_profile(snapshot)["profile_fingerprint"] == PROFILE_V2_FINGERPRINT
    target = snapshot
    keys = field.split(".")
    for key in keys[:-1]:
        target = target[key]
    target[keys[-1]] = value
    assert select_static_inclusion_profile(snapshot)["status"] == "pending"


@pytest.mark.parametrize("change", ["panel", "set", "count", "policy", "profile"])
def test_real_saved_proof_invalidated_by_panel_selection_and_profile_changes(change):
    value = calculated()
    validate_delivery_calculated(copy.deepcopy(value))
    if change == "panel":
        value["plan"]["manual_template"]["characters"][0]["stats"]["atk"] += 1
    elif change == "set":
        value["plan"]["equipment_inputs"]["char1"]["artifact_sets"][0]["set_id"] = 10007
    elif change == "count":
        value["plan"]["equipment_inputs"]["char1"]["artifact_sets"][0]["count"] = 4
    elif change == "policy":
        value["plan"]["equipment_inputs"]["char1"]["panel_policy"] = "unknown"
    else:
        value["result"]["static_inclusion_compilations"][0]["shape_certificate"]["profile_fingerprint"] = "f" * 64
        value["result_receipt"]["result_fingerprint"] = fingerprint(value["result"])
    with pytest.raises((ValueError, KeyError)):
        validate_delivery_calculated(value)


def test_registered_v2_expected_hash_and_capabilities_are_isolated():
    assert fingerprint(PROFILE_V2) == PROFILE_V2_FINGERPRINT == "e07844a863e6eea9a973e9213811446b0337f22bf5ed0800f6e1b683acbf7d49"
    caps = static_inclusion_profile_capabilities()
    profiles = {row["profile"]["id"]: row["profile"] for row in caps["supported_profiles"]}
    assert len(caps["supported_profiles"]) == len(profiles) == 5
    assert set(profiles) == {"equipment-static-included-v1", "equipment-static-included-v2",
                             "equipment-static-included-v3", "equipment-static-included-v4",
                             "equipment-static-included-v5"}
    profiles["equipment-static-included-v2"]["snapshot_scope"]["snapshot_id"] = "f" * 64
    assert fingerprint(PROFILE_V2) == PROFILE_V2_FINGERPRINT
