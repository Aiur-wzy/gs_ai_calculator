"""New-source scope is separately reviewed; prior source identities remain valid."""
import copy
import gzip
import json
from collections import Counter
from pathlib import Path

import pytest

from genshin_app.equipment import static_inclusion as compiler
from genshin_app.equipment.static_inclusion_profiles import (
    PROFILE_V3,
    PROFILE_V3_FINGERPRINT,
    PROFILE_V4,
    PROFILE_V4_FINGERPRINT,
)
from genshin_db.runtime_catalog import fingerprint

ROOT = Path(__file__).resolve().parents[1]
MATERIALS = json.loads(gzip.decompress((ROOT / "tests/fixtures/static_inclusion_profiles/current-f11cf545-materials.json.gz").read_bytes()))
SELECTION = {"profile_id": PROFILE_V4["id"], "profile_version": 4, "profile_fingerprint": PROFILE_V4_FINGERPRINT}


def test_new_review_has_independent_source_units_and_is_not_numeric_authority():
    review = json.loads((ROOT / PROFILE_V4["review_evidence"]["reference"]).read_text(encoding="utf-8"))
    assert fingerprint(review) == PROFILE_V4["review_evidence"]["fingerprint"]
    assert fingerprint(PROFILE_V4) == PROFILE_V4_FINGERPRINT
    assert fingerprint(MATERIALS) == review["materials_fingerprint"]
    assert review["database_unchanged"] and review["database_sha256_before"] == review["database_sha256_after"]
    assert review["counts"] == {"shape_ready": 36, "pending": 95}
    results = [compiler.compile_static_inclusion(material, **SELECTION) for material in MATERIALS]
    assert Counter(row["status"] for row in results) == {"shape_ready": 36, "pending": 93}
    assert all(row["source_authority"] == "not_granted" for row in results)
    assert all(row["declaration"]["numeric_application"] is False for row in results if row["status"] == "shape_ready")
    assert PROFILE_V3["upstream_commit"] == "224708edc8056f27e9ffc4eacabe797d75274636"
    assert fingerprint(PROFILE_V3) == PROFILE_V3_FINGERPRINT == "916a73d64fba532f13cbc52d05a1ba14072017ec3fe618796f5d37dda6f120f4"


@pytest.mark.parametrize("field", ["upstream_commit", "snapshot_id", "manifest_fingerprint", "normalization", "source_capabilities"])
def test_snapshot_identity_change_cannot_borrow_new_source_review(field):
    snapshot = copy.deepcopy(MATERIALS[0]["parent"]["snapshot"])
    assert compiler.select_static_inclusion_profile(snapshot) == {"status": "selected", **SELECTION}
    snapshot[field] = {} if isinstance(snapshot[field], dict) else "f" * 64
    assert compiler.select_static_inclusion_profile(snapshot)["status"] == "pending"
