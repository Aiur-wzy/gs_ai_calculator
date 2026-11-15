"""EC07a bounded base-resource queries preserve identity, source and missing facts."""
import copy
import hashlib
import json
from contextlib import contextmanager

import pytest

from genshin_db.runtime_action_resource import (
    RESOURCE_LIMITS,
    TEXT_MAP_PATH,
    _resource_projection,
    action_resource_capabilities,
    get_runtime_action_resource_profile,
)
from genshin_db.runtime_catalog import RuntimeConfigError, dumps, fingerprint
from genshin_db.runtime_queries import (
    query_runtime,
    query_runtime_public,
    runtime_capabilities,
)
from tests import fixture_candidate_builder as fixture_builder
from tests.test_pipeline import fake_repo, stub_fetcher  # noqa: F401

# ruff: noqa: F811


@pytest.fixture
def resource_db(tmp_path, fake_repo, stub_fetcher):
    path = fake_repo / "ExcelBinOutput/AvatarSkillExcelConfigData.json"
    rows = json.loads(path.read_text("utf8"))
    rows[1]["maxChargeNum"] = 1
    path.write_text(json.dumps(rows), "utf8")
    fixture_builder.build_database(tmp_path)
    return tmp_path / "genshin.db"


def query(db, **selection):
    return get_runtime_action_resource_profile(db, character_id=90000001, slot="burst", **selection)


def test_values_identity_sources_and_non_execution(resource_db):
    before = hashlib.sha256(resource_db.read_bytes()).hexdigest()
    out = query(resource_db)
    assert out["status"] == "ready"
    assert {k: v["value"] for k, v in out["projection"].items()} == {
        "base_cooldown_seconds": 15, "base_charge_count": 1, "base_energy_cost": 60, "cost_element": "geo"}
    assert set(out["identity_sources"]) == {"avatar", "depot", "skill"}
    cd = out["projection"]["base_cooldown_seconds"]
    assert cd["origin"]["raw_pointer"] == "/1/cdTime"
    assert cd["origin"]["field_node_sha256"] == fingerprint(15)
    assert out["variant_applicability"]["status"] == out["execution_support"] == "not_evaluated"
    assert out["action_dispatch"]["status"] == "pending"
    assert out["descriptions"]["description"]["text"] == "造成岩元素伤害"
    assert out["descriptions"]["description"]["text_origin"]["json_pointer"] == "/221"
    assert hashlib.sha256(resource_db.read_bytes()).hexdigest() == before


def test_slot_and_exact_skill_have_one_resource_fingerprint(resource_db):
    left = query(resource_db)
    right = get_runtime_action_resource_profile(resource_db, character_id=90000001, skill_id=90013)
    assert left["resource_fingerprint"] == right["resource_fingerprint"]
    assert left["content_fingerprint"] != right["content_fingerprint"]


def test_missing_fields_do_not_get_zero_or_one(resource_db):
    out = get_runtime_action_resource_profile(resource_db, character_id=90000001, slot="normal")
    assert out["status"] == "pending"
    for value in out["projection"].values():
        assert value["status"] == "pending" and value["value"] is None
        assert value["origin"]["raw_pointer"] is None
        assert value["origin"]["field_node_sha256"] is None


@pytest.mark.parametrize("name,field,value,reason", [
    ("base_charge_count", "maxChargeNum", True, "invalid_resource_number"),
    ("base_charge_count", "maxChargeNum", 1.0, "invalid_resource_charges"),
    ("base_charge_count", "maxChargeNum", 0, "invalid_resource_charges"),
    ("base_charge_count", "maxChargeNum", 101, "invalid_resource_charges"),
    ("base_cooldown_seconds", "cdTime", "15", "invalid_resource_number"),
    ("base_cooldown_seconds", "cdTime", -1, "resource_value_out_of_range"),
    ("base_cooldown_seconds", "cdTime", 1e9+1, "resource_value_out_of_range"),
    ("base_cooldown_seconds", "cdTime", 0.0000001, "resource_time_precision"),
    ("base_energy_cost", "costElemVal", None, "invalid_resource_number"),
    ("base_energy_cost", "costElemVal", 1e9+1, "resource_value_out_of_range"),
    ("base_energy_cost", "costElemVal", 10**400, "resource_value_out_of_range"),
    ("cost_element", "costElemType", "Mystery", "unsupported_cost_element"),
])
def test_projection_rejects_bad_or_unknown_values(resource_db, name, field, value, reason):
    record = query(resource_db)["identity_sources"]["skill"]
    record["fields"][field] = value
    out = _resource_projection(record, name, field)
    assert out["status"] == "pending" and out["value"] is None
    assert out["issues"][0]["code"] == reason
    assert out["raw_value"] == value


def test_explicit_zero_is_retained_as_a_fact_not_inferred(resource_db):
    record = query(resource_db)["identity_sources"]["skill"]
    record["fields"]["costElemVal"] = 0
    out = _resource_projection(record, "base_energy_cost", "costElemVal")
    assert out["status"] == "ready" and out["value"] == 0


@pytest.mark.parametrize("selection", [
    {"character_id": True, "slot": "burst"}, {"character_id": 90000001, "slot": []},
    {"character_id": 90000001}, {"character_id": 90000001, "slot": "burst", "skill_id": 90013},
    {"character_id": 90000001, "skill_id": 999},
    {"character_id": 90000001, "slot": "burst", "skill_depot_id": 999},
])
def test_exact_selection_validation(resource_db, selection):
    with pytest.raises(ValueError):
        get_runtime_action_resource_profile(resource_db, **selection)


def test_snapshot_and_empty_slot_are_explicit(resource_db):
    with pytest.raises(RuntimeConfigError) as exc:
        query(resource_db, snapshot_id="wrong")
    assert exc.value.reason == "snapshot_changed"
    with pytest.raises(RuntimeConfigError) as exc:
        get_runtime_action_resource_profile(resource_db, character_id=90000001, slot="skill")
    assert exc.value.reason == "empty_skill_slot"


def test_source_text_changes_fingerprint_without_changing_scalar(resource_db, fake_repo):
    original = query(resource_db)
    path = fake_repo / TEXT_MAP_PATH
    texts = json.loads(path.read_text("utf8"))
    text = "点按冷却时间10秒；长按冷却时间15秒。\n完整原文不能裁剪。"
    texts["221"] = text
    path.write_text(json.dumps(texts, ensure_ascii=False), "utf8")
    fixture_builder.build_database(resource_db.parent, force=True)
    current = query(resource_db)
    assert current["descriptions"]["description"]["text"] == text
    assert current["projection"]["base_cooldown_seconds"]["value"] == 15
    assert current["resource_fingerprint"] != original["resource_fingerprint"]
    assert current["descriptions"]["description"]["text_origin"]["node_sha256"] == fingerprint(text)


def test_missing_text_keeps_numeric_facts(resource_db, fake_repo):
    path = fake_repo / TEXT_MAP_PATH
    texts = json.loads(path.read_text("utf8"))
    del texts["221"]
    path.write_text(json.dumps(texts), "utf8")
    fixture_builder.build_database(resource_db.parent, force=True)
    out = query(resource_db)
    assert out["status"] == "partial"
    assert out["projection"]["base_energy_cost"]["value"] == 60
    assert out["descriptions"]["description"]["status"] == "pending"


def test_multiple_depots_require_choice(resource_db, fake_repo):
    path = fake_repo / "ExcelBinOutput/AvatarExcelConfigData.json"
    records = json.loads(path.read_text("utf8"))
    records[0]["candSkillDepotIds"] = [9001, 9002]
    path.write_text(json.dumps(records), "utf8")
    fixture_builder.build_database(resource_db.parent, force=True)
    with pytest.raises(RuntimeConfigError) as exc:
        query(resource_db)
    assert exc.value.reason == "skill_depot_selection_required"
    assert query(resource_db, skill_depot_id=9001)["status"] == "ready"


def test_one_read_and_no_talent_level_dependency(resource_db, monkeypatch):
    from genshin_db import runtime_action_facts, runtime_queries
    read = runtime_queries._read
    calls = []

    @contextmanager
    def counted(*args, **kwargs):
        calls.append(1)
        with read(*args, **kwargs) as value:
            yield value

    monkeypatch.setattr(runtime_queries, "_read", counted)
    monkeypatch.setattr(runtime_action_facts, "get_runtime_action_facts", lambda *a, **k: pytest.fail("must not load talents"))
    out = query(resource_db)
    assert out["status"] == "ready" and calls == [1]


def test_response_cap_and_capability_copy(resource_db, monkeypatch):
    first = action_resource_capabilities()
    first["limits"]["max_bytes"] = 1
    assert action_resource_capabilities()["limits"]["max_bytes"] == 65536
    before = copy.deepcopy(RESOURCE_LIMITS)
    monkeypatch.setitem(RESOURCE_LIMITS, "max_bytes", 100)
    with pytest.raises(RuntimeConfigError) as exc:
        query(resource_db)
    assert exc.value.reason == "response_too_large"
    assert before["max_bytes"] == 65536


def test_public_query_and_capabilities(resource_db):
    request = {"action": "action_resource_profile", "character_id": 90000001, "slot": "burst"}
    public = query_runtime_public(resource_db, request)
    publication = public.pop("owner_publication")
    assert public == query(resource_db) == query_runtime(resource_db, request)
    assert len(publication) == 1
    assert publication[0]["entity"] == {"type": "character", "id": 90000001}
    assert publication[0]["entity_status"] == "unknown"
    assert publication[0]["requires_usage_confirmation"] is True
    caps = runtime_capabilities()
    assert set(caps["actions"][request["action"]]) == {"character_id", "skill_depot_id", "slot", "skill_id"}
    assert caps["required"][request["action"]] == ["character_id"]
    assert caps[request["action"]]["limits"]["max_bytes"] == 65536
    assert request["action"] in caps["batch"]["actions"]
    assert caps["batch"]["limits"]["max_items"] == 8


@pytest.mark.parametrize("selection", [
    {}, {"character_id": True, "slot": "burst"}, {"character_id": 90000001, "slot": []},
    {"character_id": 90000001, "slot": "burst", "skill_id": 90013},
    {"character_id": 90000001, "slot": "burst", "skill_depot_id": False},
    {"character_id": 90000001, "slot": "burst", "constellation": 0},
    {"character_id": 90000001, "slot": "burst", "talent_level": 10},
    {"character_id": 90000001, "slot": "burst", "skill_id": None},
])
def test_invalid_public_selection_never_reads_database(monkeypatch, selection):
    from genshin_db import runtime_queries
    monkeypatch.setattr(runtime_queries, "_read", lambda *a, **k: pytest.fail("malformed selection read database"))
    with pytest.raises(ValueError):
        query_runtime("absent.db", {"action": "action_resource_profile", **selection})


def test_eight_item_batch_uses_one_snapshot_and_validation(resource_db, monkeypatch):
    from genshin_db import runtime_queries
    selection = {"action": "action_resource_profile", "character_id": 90000001, "slot": "burst"}
    single = query_runtime_public(resource_db, selection)
    original, calls = runtime_queries.validate_runtime, []

    def validate(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime_queries, "validate_runtime", validate)
    request = {"action": "batch", "snapshot_id": single["snapshot"]["snapshot_id"], "items": [
        {"id": f"resource-{i}", "request": dict(selection)}
        for i in range(8)]}
    unchanged = copy.deepcopy(request)
    out = query_runtime_public(resource_db, request)
    assert calls == [1] and request == unchanged
    assert out["status"] == "complete" and out["counts"] == {"requested": 8, "ok": 8, "error": 0, "skipped": 0}
    assert all(item["data"] == single and item["data"]["snapshot"] == out["snapshot"] for item in out["items"])
    assert len(dumps(out).encode("utf8")) <= out["limits"]["max_bytes"]
    request["items"].append({"id": "ninth", "request": copy.deepcopy(request["items"][0]["request"])})
    with pytest.raises(ValueError, match="1..8"):
        query_runtime(resource_db, request)


def test_batch_preserves_pending_and_continues_invalid_selection(resource_db):
    out = query_runtime(resource_db, {"action": "batch", "items": [
        {"id": "wrong", "request": {"action": "action_resource_profile", "character_id": True, "slot": "burst"}},
        {"id": "missing", "request": {"action": "action_resource_profile", "character_id": 90000001, "slot": "normal"}},
        {"id": "q", "request": {"action": "action_resource_profile", "character_id": 90000001, "slot": "burst"}},
    ]})
    assert out["status"] == "partial"
    assert [item["status"] for item in out["items"]] == ["error", "ok", "ok"]
    assert out["items"][0]["error"]["reason"] == "invalid_parameter"
    assert out["items"][0]["error"]["details"]["path"] == "/character_id"
    assert out["items"][0]["error"]["details"]["contract_ref"] == {
        "resource": "runtime_contracts", "pointer": "/actions/action_resource_profile"}
    assert out["items"][1]["data"]["status"] == "pending"
    assert out["items"][2]["data"]["variant_applicability"]["status"] == "not_evaluated"
