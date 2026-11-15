"""Independent EC07a counterexamples: resources are facts, never execution rules."""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from genshin_db import runtime_queries
from genshin_db.equipment_queries import equipment_read_session
from genshin_db.runtime_action_resource import (
    _resource_projection,
    get_runtime_action_resource_profile,
)
from genshin_db.runtime_catalog import RuntimeConfigError
from tests import fixture_candidate_builder as fixture_builder
from tests.test_pipeline import fake_repo, stub_fetcher  # noqa: F401

# ruff: noqa: F811


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf8")


@pytest.fixture
def review_db(tmp_path, fake_repo, stub_fetcher):
    skill_file = fake_repo / "ExcelBinOutput/AvatarSkillExcelConfigData.json"
    skills = json.loads(skill_file.read_text("utf8"))
    skills[1]["maxChargeNum"] = 1
    skills += [
        {"id": 90012, "nameTextMapHash": 220, "descTextMapHash": 221,
         "cdTime": 10, "maxChargeNum": 1},
        {"id": 90014, "nameTextMapHash": 220, "descTextMapHash": 221,
         "cdTime": 15, "maxChargeNum": 1, "costElemVal": 0, "costElemType": "Rock"},
        {"id": 80013, "nameTextMapHash": 220, "descTextMapHash": 221,
         "cdTime": 15, "maxChargeNum": 1, "costElemVal": 60, "costElemType": "Rock"},
    ]
    write_json(skill_file, skills)
    depot_file = fake_repo / "ExcelBinOutput/AvatarSkillDepotExcelConfigData.json"
    depots = json.loads(depot_file.read_text("utf8"))
    depots[0]["skills"][1] = 90012
    depots[0]["subSkills"] = [90014]
    # An unrelated, numerically identical Q must not pass the membership check.
    depots.append({"id": 8001, "skills": [90011], "energySkill": 80013, "talents": []})
    write_json(depot_file, depots)
    # The fixture has a normal-attack ProudSkill level 1 only, and no Q ProudSkill
    # group/level. Preserve unrelated rows required by the global DB validator.
    fixture_builder.build_database(tmp_path)
    return tmp_path / "genshin.db"


def profile(db, **changes):
    return get_runtime_action_resource_profile(db, **{"character_id": 90000001, "slot": "burst", **changes})


@pytest.mark.parametrize("selection", [
    {"character_id": True, "slot": "burst"},
    {"character_id": 90000001, "slot": []},
    {"character_id": 90000001},
    {"character_id": 90000001, "slot": "burst", "skill_id": 90013},
])
def test_review_invalid_selection_is_rejected_before_any_database_read(monkeypatch, selection):
    def forbidden(*args, **kwargs):
        pytest.fail("an invalid resource selection must not open or validate a database")

    monkeypatch.setattr(runtime_queries, "_read", forbidden)
    with pytest.raises(ValueError):
        get_runtime_action_resource_profile("not-used.db", **selection)


def test_review_identity_cannot_be_inferred_from_equal_numbers(review_db):
    for selection in ({"skill_id": 80013}, {"slot": "burst", "skill_depot_id": 8001}):
        with pytest.raises(ValueError, match="不属于"):
            get_runtime_action_resource_profile(review_db, character_id=90000001, **selection)
    sub = get_runtime_action_resource_profile(review_db, character_id=90000001, skill_id=90014)
    assert sub["selection"]["matching_slots"] == []
    assert sub["projection"]["base_energy_cost"]["value"] == 0
    assert sub["variant_applicability"]["status"] == sub["execution_support"] == "not_evaluated"


def test_review_no_proud_talent_or_dispatch_dependency_and_exact_source_hashes(review_db, fake_repo, monkeypatch):
    source = runtime_queries._Nodes.source
    visits = []

    def observed(self, path):
        assert "ProudSkill" not in path and "Talent" not in path and not path.startswith("BinOutput/")
        visits.append(path)
        return source(self, path)

    monkeypatch.setattr(runtime_queries._Nodes, "source", observed)
    out = profile(review_db)
    assert out["projection"]["base_energy_cost"]["value"] == 60
    assert len(visits) == 4
    for name, record in out["identity_sources"].items():
        original = (fake_repo / record["source_path"]).read_bytes()
        original_node = json.loads(original)[int(record["json_pointer"].lstrip("/"))]
        canonical = json.dumps(original_node, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        assert record["source_sha256"] == hashlib.sha256(original).hexdigest(), name
        assert record["node_sha256"] == hashlib.sha256(canonical.encode("utf8")).hexdigest(), name


def test_review_missing_is_not_null_or_zero_and_query_does_not_supply_defaults(review_db):
    missing = profile(review_db, slot="skill")["projection"]["base_energy_cost"]
    zero = get_runtime_action_resource_profile(review_db, character_id=90000001, skill_id=90014)["projection"]["base_energy_cost"]
    assert missing["status"] == "pending" and missing["value"] is None
    assert missing["origin"]["present"] is False and missing["origin"]["raw_pointer"] is None
    assert missing["origin"]["requested_pointer"] == "/2/costElemVal"
    assert zero["status"] == "ready" and zero["value"] == 0 and zero["origin"]["present"] is True
    record = profile(review_db)["identity_sources"]["skill"]
    record["fields"]["costElemVal"] = None
    explicit_null = _resource_projection(record, "base_energy_cost", "costElemVal")
    assert explicit_null["status"] == "pending" and explicit_null["origin"]["present"] is True
    assert explicit_null["issues"] != missing["issues"]


@pytest.mark.parametrize("output,field", [
    ("base_cooldown_seconds", "cdTime"),
    ("base_energy_cost", "costElemVal"),
    ("base_charge_count", "maxChargeNum"),
])
def test_review_valid_json_integer_outside_float_range_stays_bounded_pending(output, field):
    record = {"source_path": "fixture", "source_sha256": "a" * 64, "node_sha256": "b" * 64,
              "json_pointer": "/0", "fields": {field: 10**400}, "field_pointers": {field: "/0/" + field}}
    out = _resource_projection(record, output, field)
    assert out["status"] == "pending" and out["value"] is None
    assert out["raw_value"] == 10**400 and out["issues"]


def test_review_returned_receipt_cannot_mutate_same_session_validation_cache(review_db):
    with equipment_read_session(review_db):
        baseline = copy.deepcopy(profile(review_db))
        returned = profile(review_db)
        returned["snapshot"]["counts"]["runtime_definition"] = -1
        returned["identity_sources"]["skill"]["fields"]["costElemVal"] = -2
        returned["descriptions"]["description"]["text"] = "恶意修改"
        returned["projection"]["base_energy_cost"]["value"] = -3
        current = profile(review_db)
    assert current == baseline


def test_review_unknown_cost_enum_retains_raw_but_never_infers_from_actor(review_db):
    record = profile(review_db)["identity_sources"]["skill"]
    record["fields"]["costElemType"] = "WindOrRock"
    out = _resource_projection(record, "cost_element", "costElemType")
    assert out["value"] is None and out["status"] == "pending" and out["raw_value"] == "WindOrRock"
    assert out["origin"]["raw_pointer"] == "/1/costElemType"


def test_review_precise_microseconds_and_energy_have_different_numeric_constraints(review_db):
    record = profile(review_db)["identity_sources"]["skill"]
    for number, ready in ((0, True), (0.000001, True), (1_000_000_000, True), (0.0000001, False), (1_000_000_001, False)):
        record["fields"]["cdTime"] = number
        out = _resource_projection(record, "base_cooldown_seconds", "cdTime")
        assert (out["status"] == "ready") is ready
    record["fields"]["costElemVal"] = 0.0000001
    assert _resource_projection(record, "base_energy_cost", "costElemVal")["value"] == 0.0000001


def test_review_chinese_original_survives_markup_and_never_resolves_level_parameters(review_db, fake_repo):
    text_path = fake_repo / "TextMap/TextMap_MediumCHS.json"
    texts = json.loads(text_path.read_text("utf8"))
    expected = "<color=#99FFFFFF>点按</color>与长按\n伤害：{param1:P}。$[D]原始说明。"
    texts["221"] = expected
    write_json(text_path, texts)
    fixture_builder.build_database(review_db.parent, force=True)
    out = profile(review_db)
    assert out["descriptions"]["description"]["text"] == expected
    assert "level" not in out and "constellation" not in out["selection"]
    assert out["execution_support"] == "not_evaluated"


def test_review_identity_change_invalidates_fingerprint_without_scalar_change(review_db, fake_repo):
    before = profile(review_db)
    path = fake_repo / "ExcelBinOutput/AvatarSkillDepotExcelConfigData.json"
    rows = json.loads(path.read_text("utf8"))
    rows[0]["subSkills"] = []
    write_json(path, rows)
    fixture_builder.build_database(review_db.parent, force=True)
    after = profile(review_db)
    assert after["projection"] == before["projection"]
    for field in ("fields", "node_sha256", "source_sha256", "json_pointer"):
        assert after["identity_sources"]["skill"][field] == before["identity_sources"]["skill"][field]
    assert after["resource_fingerprint"] != before["resource_fingerprint"]


def test_review_shared_session_validates_once_and_keeps_snapshot_identity(review_db, monkeypatch):
    validate = runtime_queries.validate_runtime
    validations = []

    def counted(conn, line):
        validations.append((id(conn), line))
        return validate(conn, line)

    monkeypatch.setattr(runtime_queries, "validate_runtime", counted)
    with equipment_read_session(review_db):
        first = profile(review_db)
        second = profile(review_db, slot="skill", snapshot_id=first["snapshot"]["snapshot_id"])
        with pytest.raises(RuntimeConfigError) as exc:
            profile(review_db, snapshot_id="wrong")
    assert exc.value.reason == "snapshot_changed"
    assert first["snapshot"] == second["snapshot"]
    assert len(validations) == 1


def test_review_source_response_fingerprint_is_full_canonical_payload(review_db):
    out = profile(review_db)
    payload = copy.deepcopy(out)
    signature = payload.pop("content_fingerprint")
    expected = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf8")).hexdigest()
    assert signature == expected
    resource_payload = {key: out[key] for key in ("projection_version", "snapshot", "identity_sources", "projection", "descriptions")}
    resource_payload["selection"] = {key: value for key, value in out["selection"].items() if key != "slot"}
    resource_signature = hashlib.sha256(json.dumps(resource_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf8")).hexdigest()
    assert out["resource_fingerprint"] == resource_signature


@pytest.mark.parametrize("extra", [
    {"talent_level": 10}, {"constellation": 0}, {"variant": "hold"},
    {"fields": ["cdTime"]}, {"slot": None}, {"skill_depot_id": None},
])
def test_review_public_api_rejects_unowned_semantics_and_explicit_null_before_db_read(monkeypatch, extra):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid public request must not acquire a database read")

    monkeypatch.setattr(runtime_queries, "_read", forbidden)
    with pytest.raises(ValueError):
        runtime_queries.query_runtime("not-used.db", {"action": "action_resource_profile", "character_id": 90000001,
                                                      "slot": "burst", **extra})


def test_review_batch_keeps_partial_resource_facts_and_item_error_without_losing_later_work(review_db, monkeypatch):
    validate = runtime_queries.validate_runtime
    validations = []

    def counted(conn, line):
        validations.append(id(conn))
        return validate(conn, line)

    monkeypatch.setattr(runtime_queries, "validate_runtime", counted)
    requests = [
        {"id": "e", "request": {"action": "action_resource_profile", "character_id": 90000001, "slot": "skill"}},
        {"id": "foreign", "request": {"action": "action_resource_profile", "character_id": 90000001, "skill_id": 80013}},
        {"id": "q", "request": {"action": "action_resource_profile", "character_id": 90000001, "slot": "burst"}},
    ]
    result = runtime_queries.query_runtime(review_db, {"action": "batch", "items": requests})
    assert result["status"] == "partial" and len(validations) == 1
    assert [row["id"] for row in result["items"]] == ["e", "foreign", "q"]
    assert [row["status"] for row in result["items"]] == ["ok", "error", "ok"]
    first, last = result["items"][0]["data"], result["items"][2]["data"]
    assert first["status"] == "partial" and first["projection"]["base_energy_cost"]["value"] is None
    assert last["status"] == "ready" and last["projection"]["base_energy_cost"]["value"] == 60
    assert first["snapshot"] == last["snapshot"] == result["snapshot"]
    assert first["variant_applicability"]["status"] == last["execution_support"] == "not_evaluated"
