from genshin_runtime import for_commit
from genshin_engine.special_reaction_capabilities import (
    project_special_reaction_party_capabilities, verified_capability_tags,
)

COMMIT = "6044f5aae7dd79fbbe15c5748ef99b654f6132b7"
PREVIOUS = "f11cf545a7eee32a29999f960ebfba305f896ed3"
CURRENT = "770b88c8baccce9ff8923f5fbc95978671342232"


def test_shared_field_mapping_preserves_exact_receipts():
    assert for_commit(COMMIT).mapping_fingerprint == for_commit(PREVIOUS).mapping_fingerprint
    assert for_commit(COMMIT).receipt(COMMIT) != for_commit(PREVIOUS).receipt(PREVIOUS)
    assert for_commit(COMMIT[:8]) is None
    assert for_commit(CURRENT).mapping_fingerprint == for_commit(COMMIT).mapping_fingerprint
    assert for_commit(CURRENT).receipt(CURRENT) != for_commit(COMMIT).receipt(COMMIT)


def test_danika_projects_only_against_matching_reviewed_catalog():
    equipment = {"character_ref": {"db_character_id": 10000147, "version_line": "release"},
                 "data_version": {"version_line": "release", "upstream_commit": COMMIT}}
    row, = project_special_reaction_party_capabilities({"danika": equipment})
    assert row["catalog_revision"] == 6
    assert verified_capability_tags(row) == ("native_reaction_qualification.stellar_superconduct",)
    assert not verified_capability_tags({**row, "equipment_upstream_commit": PREVIOUS})


def test_legacy_fallback_does_not_admit_new_equipment_without_its_database_catalog():
    from genshin_engine.special_reaction_schema import special_reaction_input_capabilities
    from genshin_engine.special_reaction_capabilities import load_reviewed_special_reaction_capabilities

    current = special_reaction_input_capabilities()["capability_catalog"]
    assert current["source_upstream_commit"] == COMMIT and current["revision"] == 6
    for commit, revision in ((PREVIOUS, 5), (COMMIT, 6)):
        catalog = load_reviewed_special_reaction_capabilities(commit)
        inputs = {str(entry["character_id"]): {
            "character_ref": {"db_character_id": entry["character_id"], "version_line": "release",
                              "skill_depot_id": entry["required_skill_depot_id"]},
            "data_version": {"upstream_commit": commit, "version_line": "release"}}
            for entry in catalog["entries"]}
        rows = project_special_reaction_party_capabilities(inputs)
        assert len(rows) == len(inputs) == 15
        assert all(row["catalog_revision"] == revision and verified_capability_tags(row) for row in rows)
    changed = {actor: {**item, "data_version": {**item["data_version"], "upstream_commit": CURRENT}}
               for actor, item in inputs.items()}
    assert all(row["status"] == "pending" and not verified_capability_tags(row)
               for row in project_special_reaction_party_capabilities(changed))


def test_static_profile_matches_all_reviewed_snapshot_evidence():
    import json
    from pathlib import Path
    from genshin_app.equipment.static_inclusion import select_static_inclusion_profile
    from genshin_app.equipment.static_inclusion_profiles import (
        PROFILE_V5, PROFILE_V5_FINGERPRINT, PROFILE_V6, PROFILE_V6_FINGERPRINT,
    )
    from genshin_db.runtime_catalog import fingerprint

    for profile, digest, commit in (
        (PROFILE_V5, PROFILE_V5_FINGERPRINT, COMMIT),
        (PROFILE_V6, PROFILE_V6_FINGERPRINT, CURRENT),
    ):
        report = json.loads((Path(__file__).resolve().parents[1] / profile["review_evidence"]["reference"]).read_bytes())
        assert fingerprint(report) == profile["review_evidence"]["fingerprint"]
        assert fingerprint(profile) == digest
        assert report["counts"] == {"shape_ready": 36, "pending": 95}
        snapshot = report["snapshot"]
        assert snapshot["upstream_commit"] == commit
        assert select_static_inclusion_profile(snapshot)["profile_fingerprint"] == digest
