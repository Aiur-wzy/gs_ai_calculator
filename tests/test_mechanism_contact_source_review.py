"""Independent EC23 source review: real raw inputs, fixture-only approval.

No engine/App execution authority and no live database are used here.
"""
import copy
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from genshin_db.runtime_queries import query_runtime
from genshin_db.semantic_store import import_bundle, read_record, review_record
from genshin_engine.mechanism_descriptor_compiler import compile_mechanism_descriptor
from genshin_runtime.mechanism_semantics import (
    descriptor_fingerprint,
    fact_fingerprint,
    fingerprint,
    parameter_fingerprint,
    validate_descriptor,
)
from genshin_runtime.mechanism_source_binding import resolve_pointer
from tests.mechanism_contact_source_fixtures import (  # noqa: F401
    DEEPWOOD,
    IRON,
    approved_contact_db,
    contact_db,
    contact_repo,
    contact_seed,
    fake_repo,
    fixture_contact_catalog,
)

# ruff: noqa: F811


def query(db, ident=DEEPWOOD):
    return query_runtime(db, {"action": "mechanism_profile", "mechanism_id": ident})


def qualification(out):
    return next(f["claim"] for f in out["facts"] if f["kind"] == "execution_qualification")


def compile_again(out):
    p = out["source_proof"]
    p["descriptor_fingerprint"] = descriptor_fingerprint(out["descriptor"])
    p["fact_fingerprints"] = {f["id"] + "@1": fact_fingerprint(f) for f in out["facts"]}
    return compile_mechanism_descriptor(out["descriptor"], source_proof=p,
                                       facts=out["facts"], parameters=out["resolved_parameters"])


def assert_atomic(out):
    assert out["status"] == "pending", out
    for key in ("rule_templates", "initial_rule_templates", "execution_ports",
                "required_execution_checks", "parameter_resolutions", "fact_resolutions"):
        assert out[key] == [], key
    assert out["execution_qualification"] is None
    assert out["mechanism_fingerprint"] is None


def replace_raw(out, node_id, mutate):
    """Model coherent transport rehash, not a new review or database approval."""
    p = out["source_proof"]
    node = next(n for n in p["nodes"] if n["id"] == node_id)
    mutate(node["payload"])
    node["node_sha256"] = fingerprint(node["payload"])
    next(e for e in p["evidence"] if e["id"] == node_id)["sha256"] = node["node_sha256"]


@pytest.mark.parametrize("ident,amount,duration,cooldown", [
    (DEEPWOOD, .30000001192092896, 8, 0),
    (IRON[0], .05999999865889549, 6, 1),
    (IRON[1], .07500000298023224, 6, 1),
    (IRON[2], .09000000357627869, 6, 1),
    (IRON[3], .10499999672174454, 6, 1),
    (IRON[4], .11999999731779099, 6, 1),
])
def test_review_six_raw_writer_chains_and_narrow_reference(approved_contact_db, ident, amount, duration, cooldown):
    out = query(approved_contact_db, ident)
    assert out["status"] == "ready", out["issues"]
    c = out["compiler_result"]
    assert c["execution_status"] == out["execution_support"] == "not_evaluated"
    assert c["verification_scope"] == "materialized_consistency_only"
    assert c["whole_source_status"] == "partial"
    assert c["evidence_tier"] == ["experimental_reference"]
    nodes = {n["id"]: n["payload"] for n in out["source_proof"]["nodes"]}
    originals = json.loads((Path(__file__).parent / "fixtures/mechanism_contact_nodes.json").read_text("utf8"))
    original_raw = {n["node_sha256"]: n["raw"] for n in originals["nodes"]}
    for n in out["source_proof"]["nodes"]:
        assert n["payload"] == original_raw[n["node_sha256"]]
    # Identity -> openConfig -> complete add/modify operations -> exact Ability.
    config = nodes["affix"]["openConfig"]
    assert config == nodes["ability"]["IIBMNPFLNMJ"]
    assert all(op["IIBMNPFLNMJ"] == config and op["GBPFBNHLOFD"] is True for op in nodes["talent"])
    assert nodes["talent"][0]["$type"] == "JPCAAJGEELO"
    symbols = [op["PEOJKHKHAKL"] for op in nodes["talent"][1:]]
    assert symbols == (["ResistDown", "Dura"] if ident == DEEPWOOD else ["Rate", "Inter_CD", "Time"])
    assert [op["PCABAADALND"] for op in nodes["talent"][1:]] == ["%" + str(i + 1) for i in range(len(symbols))]
    assert all(op["$type"] == "BIAHEMMGABC" for op in nodes["talent"][1:])
    for p in out["resolved_parameters"]:
        e = p["evidence_ref"]
        assert p["value"] == resolve_pointer(nodes[e["node_id"]], e["pointer"])
    rule = c["rule_templates"][0]["rule"]
    assert rule["effects"][0]["value"] == amount
    assert rule["timing"] == {"delay": 0, "duration": duration, "cooldown": cooldown, "cooldown_anchor": "success"}
    q = qualification(out)
    b = q["parent"]["callback_bridge"]
    callback = resolve_pointer(nodes["ability"], b["callback_pointer"])
    assert len(callback) == 1
    if ident == DEEPWOOD:
        assert callback[0]["MJKPCGCAPLK"][2]["BFDEHDNLNJH"] == [
            "Elemental_Art", "Elemental_Art_Hold", "Elemental_Art_Extra", "Elemental_Burst"]
        assert b["hit_filter"] == {"damage_type_ids": ["damage.elemental_skill", "damage.elemental_burst"]}
    else:
        assert [p["JOFNJODFEDG"] for p in callback[0]["MJKPCGCAPLK"][1]["IJDGNIIOHLM"]] == [
            "Wind", "Fire", "Water", "Ice", "Electric", "Rock", "Grass"]
        assert set(b["hit_filter"]["elements"]) == {"anemo", "pyro", "hydro", "cryo", "electro", "geo", "dendro"}
    assert callback[0]["LJFIEGHHLOB"] is (ident == DEEPWOOD)
    assert all(resolve_pointer(nodes["ability"], p)["LJFIEGHHLOB"] is (ident == DEEPWOOD) for p in b["apply_pointers"])
    assert rule["trigger"]["owner_presence"] == ("any" if ident == DEEPWOOD else "frontline")
    assert rule["trigger"]["phase"] == "post"
    assert "qualified_contact_origin" in c["required_execution_checks"]
    assert "complete_contact_coverage" in c["required_execution_checks"]
    if ident != DEEPWOOD:
        assert q["scenario_requirements"]["no_provider_transformative_damage"] is True
        assert "no_provider_transformative_damage" in c["required_execution_checks"]
        assert rule["stacking"] == {"max": 2, "add": 1, "policy": "add_and_refresh"}
        assert nodes["weapon"]["skillAffix"].count(nodes["affix"]["id"]) == 1
        assert nodes["affix"].get("level", 0) == out["source_binding"]["selection"]["refinement"] - 1


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("new_surface", ["event_port", "stack_policy"])
def test_review_legacy_descriptors_cannot_borrow_contact_surface(version, new_surface):
    from tests.test_mechanism_cast_qualification import cast_case
    from tests.test_mechanism_descriptor_compiler import case
    d, a = cast_case(True) if version == 2 else case("swirl")
    before = compile_mechanism_descriptor(d, **a)
    assert before["status"] == "ready" and before["compiler_version"] == version
    row = d["rule_templates"][0]
    if new_surface == "event_port":
        row["execution_ports"].append({"target": "trigger.event_ids", "role": "qualifying_hits"})
    else:
        row["rule"]["stacking"] = {"max": 2, "add": 1, "policy": "add_and_refresh"}
    with pytest.raises(ValueError):
        validate_descriptor(d)
    assert_atomic(compile_mechanism_descriptor(d, **a))


@pytest.mark.parametrize("version", [1, 2])
def test_review_structurally_valid_hit_template_still_needs_v3_qualification(version):
    from tests.test_mechanism_cast_qualification import cast_case
    from tests.test_mechanism_qualification import qualified_case, refresh
    d, a = cast_case(True) if version == 2 else qualified_case("swirl")
    assert compile_mechanism_descriptor(d, **a)["status"] == "ready"
    row = d["rule_templates"][0]
    row["rule"]["trigger"] = {"event": "hit", "phase": "post", "elements": ["cryo"]}
    row["rule"]["timing"]["delay"] = 0
    row["execution_ports"] = [{"target": "owner", "role": "provider"},
                              {"target": "trigger.event_ids", "role": "qualifying_hits"}]
    if version == 1:
        row["rule"]["effects"][0].pop("value")
        next(v for v in row["value_bindings"] if v["parameter_ref"] == "factor")["target"] = "effects.value.value"
        next(f for f in a["facts"] if f["id"] == "factor")["claim"]["targets"][0]["target"] = "effects.value.value"
    next(f for f in a["facts"] if f["kind"] == "rule_template")["claim"] = copy.deepcopy(row)
    validate_descriptor(d)  # It is a valid *new* template, not merely malformed JSON.
    refresh(d, a)
    assert_atomic(compile_mechanism_descriptor(d, **a))


@pytest.mark.parametrize("missing", ["entire_branch", "predicate_sibling", "apply_sibling"])
def test_review_complete_callback_cannot_hide_new_raw_branch(approved_contact_db, missing):
    out = query(approved_contact_db, IRON[0])
    bridge = qualification(out)["parent"]["callback_bridge"]

    def mutate(raw):
        callback = resolve_pointer(raw, bridge["callback_pointer"])
        if missing == "entire_branch":
            callback.append(copy.deepcopy(callback[0]))
        elif missing == "predicate_sibling":
            callback[0]["MJKPCGCAPLK"].append({"$type": "UnknownExtraPredicate"})
        else:
            callback[0]["MLJNNELMEFL"].append({"$type": "UnknownExtraApply"})
    replace_raw(out, "ability", mutate)
    assert_atomic(compile_again(out))


@pytest.mark.parametrize("field,value", [("level", None), ("level", False), ("id", 111408)])
def test_review_explicit_raw_level_or_group_cannot_use_omission_default(approved_contact_db, field, value):
    out = query(approved_contact_db, IRON[0])
    replace_raw(out, "affix", lambda raw: raw.update({field: value}))
    assert_atomic(compile_again(out))


def test_review_wrong_rank_affix_with_same_duration_cd_cannot_supply_r1(approved_contact_db):
    one, five = query(approved_contact_db, IRON[0]), query(approved_contact_db, IRON[4])
    new = next(n["payload"] for n in five["source_proof"]["nodes"] if n["id"] == "affix")
    replace_raw(one, "affix", lambda raw: (raw.clear(), raw.update(new)))
    factor = next(p for p in one["resolved_parameters"] if p["ref"] == "factor")
    factor["value"] = new["paramList"][0]
    one["source_proof"]["parameter_fingerprints"]["factor"] = parameter_fingerprint(factor)
    assert_atomic(compile_again(one))


@pytest.mark.parametrize("change", ["owner_presence", "effect_domain", "reaction_guard"])
def test_review_transport_rehash_does_not_rewrite_reviewed_rule_claim(approved_contact_db, change):
    out = query(approved_contact_db, IRON[0])
    row = out["descriptor"]["rule_templates"][0]
    if change == "owner_presence":
        row["rule"]["trigger"]["owner_presence"] = "any"
    elif change == "effect_domain":
        row["rule"]["effects"][0]["domain"] = "provider"
    else:
        row["requirements"].remove("no_provider_transformative_damage")
    assert_atomic(compile_again(out))


def test_review_parameter_role_revocation_withdraws_entire_source(approved_contact_db):
    before = query(approved_contact_db, IRON[0])
    ident = next(f["id"] for f in before["facts"] if f["kind"] == "parameter_role" and f["claim"]["ref"] == "duration")
    with closing(sqlite3.connect(approved_contact_db)) as conn:
        old = read_record(conn, "release", "fact", ident)
    review_record(approved_contact_db, version_line="release", record_kind="fact", record_id=ident, revision=1,
                  status="revoked", reviewer="independent-fixture-only", reviewed_at="2026-10-03",
                  expected_review_fingerprint=old["review_fingerprint"])
    after = query(approved_contact_db, IRON[0])
    assert after["status"] == "pending"
    assert after["compiler_result"] is None and after["source_proof"] is None
    assert after.get("authorization_fingerprint") is None
    assert any(item["record_id"] == ident and item["review_status"] == "revoked" for item in after["issues"])


def test_review_rehashed_parameter_unit_cannot_override_reviewed_role(approved_contact_db):
    out = query(approved_contact_db, IRON[0])
    p = next(p for p in out["resolved_parameters"] if p["ref"] == "factor")
    p["unit"] = "flat"
    out["source_proof"]["parameter_fingerprints"]["factor"] = parameter_fingerprint(p)
    assert_atomic(compile_again(out))


def test_review_pending_seed_cannot_borrow_only_descriptor_approval(contact_db):
    seed = fixture_contact_catalog(contact_db, approve=True)
    for f in seed["facts"]:
        f["review"] = {"status": "pending", "reviewer": None, "reviewed_at": None}
    import_bundle(contact_db, seed, dry_run=False)
    out = query(contact_db)
    assert out["status"] == "pending"
    assert out["compiler_result"] is None and out["source_proof"] is None
    assert out.get("authorization_fingerprint") is None
    assert all(item["record_kind"] == "fact" and item["review_status"] == "pending" for item in out["issues"])
    # The shipped catalog remains only a candidate; fixture approval never leaks.
    assert all(r["review"]["status"] == "pending" for r in contact_seed()["facts"] + contact_seed()["descriptors"])
