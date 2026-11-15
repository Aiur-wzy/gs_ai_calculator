"""Character-owned appendix templates stay separate from benchmark evidence."""

import copy
import hashlib
import json
import re
from pathlib import Path

import pytest

from genshin_db.mechanic_facts import render_fact

ROOT = Path(__file__).resolve().parents[1]
SUPPORT_ROOT = ROOT / "benchmarks/followup/character-action-appendices-v1"
MANIFEST_PATH = SUPPORT_ROOT / "manifest.json"
TEMPLATE_PATH = SUPPORT_ROOT / "character-app-fill-templates.json"
FACT_PATH = ROOT / "src/genshin_db/resources/mechanics/character-action-appendices-v1.json"
STABLE_PATH = ROOT / "src/genshin_db/resources/mechanics/stable-facts.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _current_history(data):
    selected = [row for row in data["database_history"] if row["current"]]
    if len(selected) != 1:
        raise ValueError("Exactly one current appendix receipt is required")
    current = selected[0]
    pointer = data["current_database_history"]
    if any(pointer[key] != current[key] for key in ("receipt_id", "receipt_path")):
        raise ValueError("Current appendix receipt pointer mismatch")
    path = ROOT / current["receipt_path"]
    if _sha256(path) != current["receipt_sha256"]:
        raise ValueError("Current appendix receipt SHA mismatch")
    receipt = _json(path)
    published_bundle = FACT_PATH
    publication = data.get("source_publication", {})
    if publication.get("state") == "prepared_awaiting_database_release":
        if publication["prepared_bundle"]["sha256"] != _sha256(FACT_PATH):
            raise ValueError("Prepared appendix bundle SHA mismatch")
        published_bundle = ROOT / publication["published_bundle"]["path"]
        if publication["published_bundle"]["sha256"] != _sha256(published_bundle):
            raise ValueError("Published appendix bundle SHA mismatch")
    if receipt["bundle"]["sha256"] != _sha256(published_bundle):
        raise ValueError("Current appendix bundle SHA mismatch")
    return current, receipt


def test_appendix_support_has_no_execution_authority_and_records_history():
    data = _json(MANIFEST_PATH)
    assert data["execution_authority"] == "none"
    assert data["scope"] == "character_owned_support_with_benchmark_evidence"
    assert data["dispatch_ready"] is False
    assert data["policy"]["facts_and_fixtures_are_separate"] is True
    assert data["policy"]["character_mechanics_are_owned_by_character"] is True
    assert data["policy"]["benchmark_bindings_do_not_own_gameplay_facts"] is True

    serialized = json.dumps(data, ensure_ascii=False).lower()
    assert '"execution_authority": "ready"' not in serialized
    assert '"dispatch_ready": true' not in serialized
    assert '"scope": "production"' not in serialized

    historical = data["database_history"][0]
    assert historical["kind"] == "historical_import"
    assert historical["current"] is False
    assert historical["inserted_pending_facts"] == 23
    assert COMMIT_RE.fullmatch(historical["source_commit"])
    receipt_path = ROOT / historical["receipt_path"]
    assert receipt_path.is_file()
    assert _sha256(receipt_path) == historical["receipt_sha256"]
    assert historical["receipt_sha256"] == "4d44052accd8dad6f981025d65dc7fb642d06b84354c445945c97aa48c89cb4b"

    correction = next(row for row in data["database_history"] if row["kind"] == "character_scope_correction")
    assert correction["current"] is False
    assert correction["updated_pending_facts"] == 9
    assert correction["inserted_pending_facts"] == 9
    assert correction["current_pending_facts"] == 32
    assert correction["final_review_adjusted_facts"] == 3
    assert correction["integrity_check"] == "ok"
    correction_path = ROOT / correction["receipt_path"]
    assert _sha256(correction_path) == correction["receipt_sha256"]
    assert correction["receipt_sha256"] == "92bfa173da9ebd976ec6769fb3c61451e05603e90ec087aa5b497da6898e4c6c"
    correction_receipt = _json(correction_path)
    archived_bundle = SUPPORT_ROOT / "history/fact-bundle-8534057a.json"
    assert correction_receipt["bundle"]["sha256"] == _sha256(archived_bundle)
    assert _sha256(archived_bundle) == "8534057a49ff374e60cba8ea2790be7548ea47c6b2af168abfc9846b69ed226b"
    assert correction_receipt["final_review_adjustments"]["count"] == 3
    assert correction_receipt["post_apply_dry_run"]["counts"] == {
        "would_insert": 0,
        "inserted": 0,
        "unchanged": 32,
        "conflict_preserved": 0,
        "target_missing": 0,
    }

    current, receipt = _current_history(data)
    assert current["kind"] == "database_release_rebase"
    assert current["updated_pending_facts"] == 1
    assert current["inserted_pending_facts"] == 0
    assert current["current_pending_facts"] == 32
    assert current["database_release_id"] == "db-7.2.50-r2"
    assert current["release_sequence"] == 4
    assert receipt["operation"] == "readonly_verification_of_accepted_database_release"
    assert receipt["execution_authority"] == "none"
    assert receipt["historical_bundle"]["sha256"] == _sha256(SUPPORT_ROOT / "history/fact-bundle-f96835a0.json")
    assert receipt["verification"] == {
        "bundle_items": 32, "matched_rows": 32, "pending_rows": 32, "mismatched_rows": 0,
        "changed_annotation_ids": [32], "other_project_rows_preserved": True,
        "integrity_check": "ok", "foreign_key_errors": 0,
        "checks_do_not_repeat_annotation_import_or_mutate_database": True,
    }
    for reference in (receipt["accepted_release_manifest"], receipt["content_approval"],
                      receipt["content_proposal"], receipt["activation_postcheck"],
                      *receipt["archived_support"], *receipt["historical_receipts"]):
        assert _sha256(ROOT / reference["path"]) == reference["sha256"]
    accepted = _json(ROOT / receipt["accepted_release_manifest"]["path"])
    assert receipt["database"]["sha256"] == accepted["database"]["sha256"]
    assert receipt["database"]["upstream_commit"] == accepted["database"]["upstream_commit"]
    approval = _json(ROOT / receipt["content_approval"]["path"])
    assert receipt["content_approval"]["change_set_id"] == approval["change_set_id"]
    assert receipt["content_approval"]["approval_digest"] == approval["approval_digest"]
    rows = {row["fact_id"]: row for row in receipt["fact_rows"]}
    publication = data.get("source_publication", {})
    fact_path = (ROOT / publication["published_bundle"]["path"]
                 if publication.get("state") == "prepared_awaiting_database_release" else FACT_PATH)
    facts = _json(fact_path)["items"]
    assert len(rows) == len(facts) == 32
    for fact in facts:
        row = rows[fact["id"]]
        assert row["status"] == fact["status"] == "pending"
        assert row["target_type"] == fact["target_type"] and row["target_id"] == fact["target_id"]
        assert row["content_sha256"] == hashlib.sha256(render_fact(fact).encode()).hexdigest()


def test_current_appendix_receipt_is_not_selected_by_history_position():
    data = _json(MANIFEST_PATH)
    expected = _current_history(data)
    data["database_history"].reverse()
    assert _current_history(data) == expected


def test_current_appendix_receipt_rejects_unaccepted_bundle_bytes(tmp_path, monkeypatch):
    path = tmp_path / "altered-bundle.json"
    path.write_bytes(FACT_PATH.read_bytes() + b"\n")
    monkeypatch.setitem(globals(), "FACT_PATH", path)
    with pytest.raises(ValueError, match="bundle SHA mismatch"):
        _current_history(_json(MANIFEST_PATH))


@pytest.mark.parametrize("change", ["duplicate_current", "bad_pointer", "bad_receipt_sha"])
def test_current_appendix_receipt_rejects_ambiguous_or_forged_selection(change):
    data = copy.deepcopy(_json(MANIFEST_PATH))
    if change == "duplicate_current":
        data["database_history"][0]["current"] = True
    elif change == "bad_pointer":
        data["current_database_history"]["receipt_id"] = "historical-import"
    else:
        next(row for row in data["database_history"] if row["current"])["receipt_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        _current_history(data)


def test_character_catalog_is_unique_complete_and_party_independent():
    manifest = _json(MANIFEST_PATH)
    catalog = _json(TEMPLATE_PATH)
    facts = _json(FACT_PATH)
    stable = _json(STABLE_PATH)

    assert catalog["execution_authority"] == "none"
    assert catalog["dispatch_ready"] is False
    assert catalog["policy"]["party_independent"] is True
    assert catalog["source_fact_bundle_sha256"] == _sha256(FACT_PATH)
    assert catalog["maintenance_revision"] == 3
    assert catalog["source_database_release_id"] == "db-7.2.50-r2"
    assert manifest["character_catalog"]["fact_bundle_sha256"] == _sha256(FACT_PATH)
    assert manifest["character_catalog"]["app_fill_template_sha256"] == _sha256(TEMPLATE_PATH)
    assert manifest["character_catalog"]["template_count"] == 24
    archived = _json(SUPPORT_ROOT / "history/character-app-fill-templates-pre-r9.json")
    # Only the character-owned burst hint changes, never the other characters
    # or their independent benchmark configuration/evidence.
    previous_characters = {row["character_id"]: row for row in archived["characters"]}
    for character in catalog["characters"]:
        previous = previous_characters[character["character_id"]]
        if character["character_id"] != 10000148:
            assert character == previous
        else:
            for template in character["templates"]:
                old = next(row for row in previous["templates"] if row["template_id"] == template["template_id"])
                if template["template_id"] != "character.aroha.burst.v1":
                    assert template == old
    previous_bindings = _json(SUPPORT_ROOT / "history/manifest-pre-r9.json")["benchmark_bindings"]
    for binding, previous in zip(manifest["benchmark_bindings"], previous_bindings, strict=True):
        if "character.aroha.burst.v1" not in binding["character_template_refs"]:
            assert binding == previous
        else:
            normalized = copy.deepcopy(binding)
            normalized.pop("materialization_status")
            normalized["character_template_revisions"]["character.aroha.burst.v1"] = 1
            normalized["evidence_gaps"] = previous["evidence_gaps"]
            assert normalized == previous

    characters = catalog["characters"]
    character_ids = [row["character_id"] for row in characters]
    assert len(character_ids) == len(set(character_ids)) == 11
    assert manifest["character_catalog"]["unique_character_count"] == 11
    assert manifest["character_catalog"]["benchmark_character_slot_count"] == 12

    pending_ids = {row["id"] for row in facts["items"]}
    approved_ids = {row["id"] for row in stable["items"]}
    catalog_fact_ids = {ref for character in characters for ref in character["fact_refs"]}
    assert pending_ids <= catalog_fact_ids
    assert catalog_fact_ids <= pending_ids | approved_ids

    template_ids = []
    for character in characters:
        assert character["fact_refs"]
        consumed_fact_refs = set()
        character_overlay_ids = set()
        for overlay in character.get("character_overlays", []):
            character_overlay_ids.add(overlay["overlay_id"])
            assert set(overlay["fact_refs"]) <= set(character["fact_refs"])
            assert overlay["validation_checks"]
            consumed_fact_refs.update(overlay["fact_refs"])
        for template in character["templates"]:
            template_ids.append(template["template_id"])
            assert template["revision"] == (2 if template["template_id"] == "character.aroha.burst.v1" else 1)
            assert template["character_id"] == character["character_id"]
            assert template["version_line"] == "release"
            assert template["skill_level"] == {
                "mode": "scene_input",
                "default_level": 10,
                "matching_active_plus_three_constellation_level": 13,
                "apply_plus_three_only_to_matching_talent": True,
            }
            assert template["constellation_condition"]["minimum"] == 0
            assert template["constellation_condition"]["selection"] == "scene_input"
            assert set(template["fact_refs"]) <= set(character["fact_refs"])
            consumed_fact_refs.update(template["fact_refs"])
            for overlay in template.get("optional_overlays", []):
                consumed_fact_refs.update(overlay["fact_refs"])
            assert set(template.get("character_overlay_refs", [])) <= character_overlay_ids
            assert template["fill_sections"] and template["validation_checks"]
        assert consumed_fact_refs == set(character["fact_refs"])
    assert len(template_ids) == len(set(template_ids))

    serialized = json.dumps(catalog, ensure_ascii=False)
    for forbidden in ("神鹤万心", "卢茜万班", "星超导队", '"team_id"', '"benchmark_case_id"'):
        assert forbidden not in serialized


def test_published_aroha_knowledge_is_exactly_the_single_proposed_update():
    manifest = _json(MANIFEST_PATH)
    catalog = _json(TEMPLATE_PATH)
    publication = manifest["source_publication"]
    assert publication["state"] == "published_accepted_database_release"
    assert publication == catalog["source_publication"]
    assert publication["execution_authority"] == "none"
    proposal = _json(ROOT / publication["content_proposal"]["path"])
    assert _sha256(ROOT / publication["content_proposal"]["path"]) == publication["content_proposal"]["sha256"]
    before = _json(ROOT / publication["previous_published_bundle"]["path"])
    after = _json(FACT_PATH)
    expected = copy.deepcopy(before)
    index = next(i for i, row in enumerate(expected["items"]) if row["id"] == proposal["stable_fact_id"])
    assert expected["items"][index] == proposal["source_item_before"]
    expected["items"][index] = proposal["source_item_after"]
    assert after == expected
    assert after["items"][index]["status"] == "pending"

    aroha = next(row for row in catalog["characters"] if row["character_id"] == 10000148)
    burst = next(row for row in aroha["templates"] if row["template_id"] == "character.aroha.burst.v1")
    hint = burst["application_rule_hint"]
    assert hint["execution_authority"] == "none"
    assert hint["source_fact_ref"] == proposal["stable_fact_id"]
    assert hint["icd"] == {
        "kind": "sequence", "counter_tag": "Elemental_Burst",
        "rule_group": "Alyosha_ElementalBurst", "reset_seconds": 1.6, "sequence": [1, 0],
    }
    assert "phase_offset" not in hint and "first_hit_at" not in hint


def test_kazuha_template_keeps_absorption_c2_and_a4_character_scoped():
    catalog = _json(TEMPLATE_PATH)
    kazuha = next(row for row in catalog["characters"] if row["character_id"] == 10000047)
    skill = next(
        row for row in kazuha["templates"]
        if row["template_id"] == "character.kazuha.skill-plunge.v1"
    )
    burst = next(row for row in kazuha["templates"] if row["template_id"] == "character.kazuha.burst.v1")
    inputs = {row["key"]: row for row in burst["scene_inputs"]}
    assert set(inputs["absorbed_element"]["allowed_values"]) == {
        "pyro", "hydro", "electro", "cryo", None,
    }
    assert "swirled_elements" not in inputs
    overlays = {row["overlay_id"]: row for row in burst["optional_overlays"]}
    c2 = overlays["character.kazuha.c2.em-field.v1"]
    assert c2["fact_refs"] == ["appendix.kazuha.c2-em-field.v1"]
    assert c2["effect"]["value"] == 200
    assert c2["effect"]["beneficiaries"] == ["枫原万叶", "领域内当前前台角色"]
    assert c2["effect"]["stacking"] == "unique"
    assert "不叠成+400" in c2["effect"]["recipient_semantics"]
    assert "character.kazuha.a4.elemental-bonus.v1" not in overlays

    character_overlays = {row["overlay_id"]: row for row in kazuha["character_overlays"]}
    a4 = character_overlays["character.kazuha.a4.elemental-bonus.v1"]
    assert a4["fact_refs"] == ["appendix.kazuha.a4-elemental-bonus.v1"]
    assert a4["scope"] == "character_owned_reaction_event"
    assert a4["enable_when"]["reaction_family"] == "swirl"
    assert a4["event_inputs"][0]["key"] == "reaction_events"
    assert "swirled_element" in a4["event_inputs"][0]["fields"]
    assert skill["character_overlay_refs"] == [a4["overlay_id"]]
    assert burst["character_overlay_refs"] == [a4["overlay_id"]]


def test_benchmark_bindings_only_reference_character_templates_and_evidence():
    data = _json(MANIFEST_PATH)
    catalog = _json(TEMPLATE_PATH)
    template_ids = {
        template["template_id"]
        for character in catalog["characters"]
        for template in character["templates"]
    }
    bindings = data["benchmark_bindings"]
    assert {row["scenario_id"] for row in bindings} == {
        "ayaka-shenhe-kazuha-kokomi",
        "diluc-citlali-kazuha-bennett",
        "sandrone-odetta-sucrose-aroha",
    }
    for binding in bindings:
        assert set(binding["character_template_refs"]) <= template_ids
        assert binding["character_template_revisions"] == {
            template_id: (2 if template_id == "character.aroha.burst.v1" else 1)
            for template_id in binding["character_template_refs"]
        }
        contract_path = ROOT / binding["benchmark_contract_path"]
        assert contract_path.is_file()
        assert "annotation_fact_ids" not in binding
        assert "known_unknowns" not in binding
        for scene_input in binding["scene_input_bindings"]:
            assert scene_input["template_id"] in template_ids
            assert scene_input["scope"] == "this_scenario_only"


def test_all_fixture_and_snapshot_evidence_paths_exist_and_match_sha256():
    for binding in _json(MANIFEST_PATH)["benchmark_bindings"]:
        assert binding["evidence_gaps"]
        for fixture in binding["fixtures"]:
            path = ROOT / fixture["path"]
            assert path.is_file(), fixture["path"]
            assert SHA256_RE.fullmatch(fixture["sha256"])
            assert _sha256(path) == fixture["sha256"]
            assert COMMIT_RE.fullmatch(fixture["source_commit"])
            if fixture["role"] == "positive_local_fixture":
                assert fixture["allowed_claims"] and fixture["forbidden_claims"]
                assert "可执行 dispatch profile" in fixture["forbidden_claims"]
            else:
                assert fixture["role"] == "negative_regression"
                assert fixture["failure_mode"]

        for snapshot in binding["database_snapshots"]:
            assert COMMIT_RE.fullmatch(snapshot["repository_commit"])
            if snapshot["status"] == "captured":
                assert SHA256_RE.fullmatch(snapshot["database_sha256"])
                assert COMMIT_RE.fullmatch(snapshot["upstream_commit"])
                evidence = snapshot["evidence"]
                path = ROOT / evidence["path"]
                assert path.is_file(), evidence["path"]
                assert _sha256(path) == evidence["sha256"]
            else:
                assert snapshot["status"] == "not_captured_in_committed_case"
                assert snapshot["database_sha256"] is None
                assert snapshot["upstream_commit"] is None
                assert snapshot["evidence"] is None


def test_known_bad_results_stay_negative():
    fixtures = [
        fixture
        for binding in _json(MANIFEST_PATH)["benchmark_bindings"]
        for fixture in binding["fixtures"]
    ]
    by_id = {row["fixture_id"]: row for row in fixtures}
    assert by_id["ayaka-opt-03-known-wrong-native-plan"]["role"] == "negative_regression"
    assert by_id["diluc-rev6-revoked-result"]["role"] == "negative_regression"
