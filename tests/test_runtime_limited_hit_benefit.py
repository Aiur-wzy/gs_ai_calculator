"""Full local source and bounded projection, using a small isolated raw fixture."""
from __future__ import annotations

import copy
import json

import pytest

from genshin_db.runtime_limited_hit_benefit import get_limited_hit_benefit_context
from genshin_runtime.limited_hit_benefit_identity import (
    LIMITS,
    fingerprint,
    limited_hit_benefit_source_payload,
    recipe,
    verify_limited_hit_benefit_projection,
)
from tests.test_pipeline import fake_repo  # noqa: F401
from tests.test_runtime_object_index import build, write

# ruff: noqa: F811


@pytest.fixture
def benefit_repo(fake_repo):
    r = recipe()
    for entry in r["fixture_records"]:
        write(fake_repo, entry["source_path"], [copy.deepcopy(entry["raw"])])
    write(fake_repo, r["ability"]["source_path"], [{"Default": r["ability"]["new_raw"]}])
    write(fake_repo, r["talent"]["source_path"], {r["talent"]["name"]: r["talent"]["raw"]})
    return fake_repo


@pytest.fixture
def benefit_db(tmp_path, benefit_repo):
    db = tmp_path / "benefit.db"
    build(db, benefit_repo, upstream_commit=recipe()["commit"])
    return db


def query(db, **kwargs):
    return get_limited_hit_benefit_context(db, **({"character_id": 10000063, "slot": "skill",
        "talent_level": 10, "constellation": 0, "param_index": 1} | kwargs))


def rehash(value):
    value["semantic_dependency"] = limited_hit_benefit_source_payload(value)
    value["source_fingerprint"] = fingerprint(value["semantic_dependency"])
    value.pop("content_fingerprint", None)
    value["content_fingerprint"] = fingerprint(value)
    return value


def test_real_parent_projection(benefit_db):
    value = query(benefit_db).public_projection
    assert value["status"] == "ready", value["issues"]
    assert [value["projection"][k]["value"] for k in ("coefficient", "duration_seconds", "capacity")] == [0.8218079805374146, 15, 7]
    assert len(value["source_bundle"]["entries"]) == 13
    assert len(value["source_bundle"]["references"]) == 23
    assert value["whole_source_status"] == "partial" and value["execution_support"] == "not_evaluated"
    assert verify_limited_hit_benefit_projection(value)["parent_bridge"]["variant"] == "hold"


@pytest.mark.parametrize("kwargs", [{"param_index": 0}, {"constellation": 1}, {"talent_level": 9}])
def test_unsupported_source_is_pending(benefit_db, kwargs):
    assert query(benefit_db, **kwargs).public_projection["status"] == "pending"


def test_invalid_request_before_db(tmp_path):
    with pytest.raises(ValueError):
        query(tmp_path / "missing.db", character_id=True)


def test_complete_unknown_talent_and_ability_are_pending(benefit_db, benefit_repo):
    r = recipe()
    operations = copy.deepcopy(r["talent"]["raw"])
    operations.append({"$type": "BIAHEMMGABC", "IIBMNPFLNMJ": r["ability"]["new_raw"]["IIBMNPFLNMJ"],
        "GBPFBNHLOFD": True, "PEOJKHKHAKL": "unknown", "PCABAADALND": "%3"})
    write(benefit_repo, r["talent"]["source_path"], {r["talent"]["name"]: operations})
    build(benefit_db, benefit_repo, upstream_commit=r["commit"])
    assert query(benefit_db).public_projection["status"] == "pending"


@pytest.mark.parametrize("where", ["projection", "raw", "normalization", "bool_control", "writer"])
def test_rehash_does_not_admit_tampered_sources(benefit_db, where):
    value = query(benefit_db).public_projection
    if where == "projection":
        value["projection"]["capacity"]["value"] = 100
    elif where == "normalization":
        value["snapshot"]["normalization"]["mapping_fingerprint"] = "a" * 64
    else:
        entry = next(e for e in value["source_bundle"]["entries"] if e["definition"]["definition_kind"] == "talent_config")
        if where == "raw":
            entry["payload"][0]["unreviewed"] = True
        elif where == "bool_control":
            entry["payload"][0]["GBPFBNHLOFD"] = 1
        else:
            entry["payload"][3]["PCABAADALND"] = "%2"
        entry["node_sha256"] = entry["definition"]["node_sha256"] = fingerprint(entry["payload"])
    with pytest.raises(ValueError):
        verify_limited_hit_benefit_projection(rehash(value))


def test_source_parameter_change_is_derived_not_literal(benefit_db, benefit_repo):
    r = recipe()
    item = next(e for e in r["fixture_records"] if "ProudSkill" in e["source_path"])
    row = copy.deepcopy(item["raw"])
    row["paramList"][2], row["paramList"][4], row["paramList"][6] = 0.5, 12, 4
    write(benefit_repo, item["source_path"], [row])
    build(benefit_db, benefit_repo, upstream_commit=r["commit"])
    value = query(benefit_db).public_projection
    assert value["status"] == "ready", value["issues"]
    assert [value["projection"][k]["value"] for k in ("coefficient", "duration_seconds", "capacity")] == [0.5, 12, 4]


def test_same_session_return_is_detached(benefit_db):
    from genshin_db.equipment_queries import equipment_read_session
    with equipment_read_session(benefit_db):
        first = query(benefit_db)
        first.public_projection["source_bundle"]["entries"].clear()
        first.parameter_context["facts"]["records"].clear()
        second = query(benefit_db)
    assert second.public_projection["status"] == "ready"
    assert second.parameter_context["facts"]["records"]


def test_node_budget_before_children_decompression(benefit_db, monkeypatch):
    from genshin_db.runtime_catalog import RuntimeConfigError
    monkeypatch.setitem(LIMITS, "max_nodes", 12)
    with pytest.raises(RuntimeConfigError) as error:
        query(benefit_db)
    assert error.value.reason == "response_too_large"


def test_fixture_is_json_safe(benefit_db):
    value = query(benefit_db).public_projection
    assert verify_limited_hit_benefit_projection(json.loads(json.dumps(value)))["projection"] == value["projection"]


def test_public_route_batch_and_capabilities(benefit_db):
    from genshin_db.runtime_queries import query_runtime, runtime_capabilities
    request = {"action": "limited_hit_benefit_profile", "character_id": 10000063, "slot": "skill", "param_index": 1, "talent_level": 10}
    single = query_runtime(benefit_db, request)
    batch = query_runtime(benefit_db, {"action": "batch", "items": [{"id": "benefit", "request": request}]})
    assert batch["items"][0]["data"] == single
    caps = runtime_capabilities()
    assert caps["required"][request["action"]] == ["character_id", "param_index"]
    assert request["action"] in caps["batch"]["actions"]


def test_cli_summary_omits_raw_but_preserves_partial(benefit_db):
    from genshin_app.cli import _runtime_output_summary
    value = query(benefit_db).public_projection
    summary = _runtime_output_summary(value, {"action": "limited_hit_benefit_profile"})
    assert summary["whole_source_status"] == "partial" and summary["execution_support"] == "not_evaluated"
    assert summary["projection_summary"]["capacity"] == {"status": "ready", "value": 7}
    encoded = json.dumps(summary)
    assert "ECFFDLHMCLK" not in encoded and "source_bundle" not in encoded and len(encoded) < 5000


def test_reference_budget_is_checked_before_raw_validation(benefit_db, monkeypatch):
    from genshin_db import runtime_queries
    from genshin_db.runtime_catalog import RuntimeConfigError
    monkeypatch.setitem(LIMITS, "max_edges", 7)
    monkeypatch.setattr(runtime_queries, "_references", lambda *a: pytest.fail("must reject before validating 8 Talent edges"))
    with pytest.raises(RuntimeConfigError) as error:
        query(benefit_db)
    assert error.value.reason == "response_too_large"


def test_exact_snapshot_required(benefit_db):
    from genshin_db.runtime_catalog import RuntimeConfigError
    with pytest.raises(RuntimeConfigError) as error:
        query(benefit_db, snapshot_id="f" * 64)
    assert error.value.reason == "snapshot_changed"


def test_owning_skill_not_only_same_parameter_value(benefit_db):
    value = query(benefit_db).public_projection
    value["selection"]["skill_id"] = 19999
    for e in value["source_bundle"]["entries"]:
        if e["definition"]["source_path"].endswith("AvatarSkillExcelConfigData.json"):
            e["payload"]["id"] = 19999
            e["definition"]["exact_key"]["id"] = 19999
        if e["definition"]["source_path"].endswith("AvatarSkillDepotExcelConfigData.json"):
            e["payload"]["skills"][1] = 19999
        e["node_sha256"] = e["definition"]["node_sha256"] = fingerprint(e["payload"])
    with pytest.raises(ValueError, match="Ability"):
        verify_limited_hit_benefit_projection(rehash(value))
