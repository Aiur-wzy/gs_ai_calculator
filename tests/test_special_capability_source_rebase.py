"""Source review and qualification projection, never character execution code."""
import hashlib
import json
from pathlib import Path

from genshin_engine.special_reaction_capabilities import (
    load_reviewed_special_reaction_capabilities,
    project_special_reaction_party_capabilities,
    verified_capability_tags,
)

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "f11cf545a7eee32a29999f960ebfba305f896ed3"


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _equipment(catalog, commit=COMMIT):
    return {str(entry["character_id"]): {
        "character_ref": {"db_character_id": entry["character_id"],
                          "skill_depot_id": entry["required_skill_depot_id"], "version_line": "release"},
        "data_version": {"version_line": "release", "upstream_commit": commit},
    } for entry in catalog["entries"]}


def test_catalog_rebase_is_backed_by_all_identity_links_and_bilingual_source_evidence():
    catalog = load_reviewed_special_reaction_capabilities()
    review = json.loads((ROOT / "docs/pm/audits/2026-11-06-r9-special-capability-source-review.json").read_text(encoding="utf-8"))
    assert review["review_fingerprint"] == _hash({key: value for key, value in review.items() if key != "review_fingerprint"})
    assert catalog["source_upstream_commit"] == review["source_upstream_commit"] == COMMIT
    assert catalog["source_target_version"] == review["source_target_version"] == "CNCBWin7.2.50"
    assert catalog["revision"] == review["new_catalog_revision"] == 2
    assert catalog["content_hash"] == review["new_catalog_content_hash"]
    assert set(catalog["source_files"]) < set(review["source_files"])
    for name, digest in catalog["source_files"].items():
        assert review["source_files"][name] == digest
    rows = {row["character_id"]: row for row in review["reviewed_rows"]}
    assert len(rows) == len(catalog["entries"]) == 13
    for entry in catalog["entries"]:
        row = rows[entry["character_id"]]
        assert row["identity_and_passive_link_verified"] is True
        assert row["facts_unchanged_from_previous_review"] is True
        assert entry["required_skill_depot_id"] in {row["avatar"]["skillDepotId"], *row["avatar"]["candSkillDepotIds"]}
        assert row["depot"]["qualification_opening"]["proudSkillGroupId"] == row["proud"]["proud_skill_group_id"]
        assert row["depot"]["qualification_opening"].get("needAvatarPromoteLevel", 0) == 0
        assert row["proud"]["level"] == 1
        assert row["entry_fingerprint"] == entry["entry_fingerprint"]
        assert row["capability_tags"] == entry["capability_tags"]
        assert row["runtime_support_status"] == entry["runtime_support_status"]
        for text in row["texts"].values():
            assert text["sha256"] == hashlib.sha256(text["content"].encode()).hexdigest()
            assert text["pointer"] == "/" + row["proud"]["desc_text_map_hash"]


def test_new_snapshot_qualifies_twelve_and_preserves_one_exception_as_pending():
    catalog = load_reviewed_special_reaction_capabilities()
    projected = project_special_reaction_party_capabilities(_equipment(catalog))
    assert len(projected) == 12
    assert all(row["status"] == "confirmed" for row in projected)
    assert all(verified_capability_tags(row) for row in projected)
    assert all(row["db_character_id"] != 10000136 for row in projected)
    pending = [row for row in catalog["entries"] if row["runtime_support_status"] == "pending"]
    assert [row["character_id"] for row in pending] == [10000136]


def test_old_snapshot_does_not_gain_new_authority_by_catalog_rebase():
    catalog = load_reviewed_special_reaction_capabilities()
    projected = project_special_reaction_party_capabilities(_equipment(catalog, "224708edc8056f27e9ffc4eacabe797d75274636"))
    assert len(projected) == 12
    assert all(row["status"] == "pending" for row in projected)
    assert all(row["projection_reason_code"] == "equipment_upstream_commit_mismatch" for row in projected)
    assert all(not verified_capability_tags(row) for row in projected)


def test_rebase_preserves_explicit_form_and_exact_entry_integrity():
    catalog = load_reviewed_special_reaction_capabilities()
    inputs = _equipment(catalog)
    inputs["10000005"]["character_ref"].pop("skill_depot_id")
    inputs["10000007"]["character_ref"]["skill_depot_id"] = 701
    rows = project_special_reaction_party_capabilities(inputs)
    assert len(rows) == 10
    assert not {10000005, 10000007} & {row["db_character_id"] for row in rows}
    row = rows[0]
    row["catalog_revision"] = 1
    assert not verified_capability_tags(row)
