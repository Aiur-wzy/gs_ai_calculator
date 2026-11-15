"""Exact-source ICD projection must not invent attack binding or lose coefficients."""
import hashlib
import json

import pytest

from genshin_db.runtime_catalog import RuntimeConfigError, resolve_pointer
from genshin_db.runtime_queries import query_runtime, runtime_capabilities
from tests import fixture_candidate_builder as fixture_builder
from tests.test_pipeline import fake_repo, stub_fetcher  # noqa: F401

# ruff: noqa: F811


@pytest.fixture
def attenuation_db(tmp_path, fake_repo, stub_fetcher):
    rows = [{"group": "default", "resetCycle": 2.5, "durabilitySequence": [1, 0, 0] * 8,
             "damageSequence": [1, 0.5, 0], "enbreakSequence": [1, 1, 1], "unknown": {"flag": False}},
            {"group": "nonbinary", "resetCycle": 1, "durabilitySequence": [1, 1.5, 0.1]},
            {"group": "float-timer", "resetCycle": 0.10000000149011612, "durabilitySequence": [1, 0]}]
    path = fake_repo / "ExcelBinOutput/AttackAttenuationExcelConfigData.json"
    path.write_text(json.dumps(rows), "utf8")
    fixture_builder.build_database(tmp_path)
    return tmp_path / "genshin.db"


def query(db, group="default", **fields):
    return query_runtime(db, {"action": "attenuation_profile", "group": group, **fields})


def change(fake_repo, db, apply):
    path = fake_repo / "ExcelBinOutput/AttackAttenuationExcelConfigData.json"
    rows = json.loads(path.read_text("utf8"))
    apply(rows)
    path.write_text(json.dumps(rows), "utf8")
    fixture_builder.build_database(db.parent, force=True)


def test_exact_raw_source_and_projection_read_only(attenuation_db, fake_repo):
    before = hashlib.sha256(attenuation_db.read_bytes()).hexdigest()
    result = query(attenuation_db)
    assert result["status"] == result["execution"]["status"] == "ready"
    assert result["execution"]["attack_binding"] == "not_evaluated"
    assert result["projection"]["durability_sequence"] == [1, 0, 0] * 8
    assert result["raw_record"]["damageSequence"] == [1, 0.5, 0]
    assert result["raw_record"]["unknown"] == {"flag": False}
    source = result["source"]
    raw = json.loads((fake_repo / source["source_path"]).read_text("utf8"))
    assert resolve_pointer(raw, source["json_pointer"]) == result["raw_record"]
    assert source["definition"]["exact_key"] == {"group": "default"}
    assert source["node_sha256"] and source["source_sha256"] and source["definition_id"]
    assert hashlib.sha256(attenuation_db.read_bytes()).hexdigest() == before
    assert runtime_capabilities()["attenuation_profile"]["limits"]["max_reset_ticks"] == 10**15


def test_nonbinary_preserved_and_not_coerced_to_executable(attenuation_db):
    result = query(attenuation_db, "nonbinary")
    assert result["status"] == "ready"  # The original source exists and is readable.
    assert result["execution"]["status"] == "pending"
    assert result["raw_record"]["durabilitySequence"] == [1, 1.5, 0.1]
    assert result["projection"]["durability_sequence"] is None


def test_float_timer_rounding_receipt(attenuation_db):
    projection = query(attenuation_db, "float-timer")["projection"]
    assert projection["reset_cycle_seconds_raw"] == 0.10000000149011612
    assert projection["reset_cycle_ticks"] == 100000
    assert projection["reset_cycle_seconds_rounded"] == 0.1
    assert projection["rounding_error_seconds"] == pytest.approx(-1.49011612e-9, abs=1e-18)
    assert abs(projection["rounding_error_seconds"]) <= 0.5e-6


def test_float_binary_coefficients_normalize_only_in_projection(attenuation_db, fake_repo):
    change(fake_repo, attenuation_db, lambda rows: rows[0].update(durabilitySequence=[1.0, 0.0, 1]))
    result = query(attenuation_db)
    assert result["execution"]["status"] == "ready"
    assert type(result["raw_record"]["durabilitySequence"][0]) is float
    assert result["projection"]["durability_sequence"] == [1, 0, 1]
    assert all(type(v) is int for v in result["projection"]["durability_sequence"])


@pytest.mark.parametrize("timer,expected_ticks", [(0.0000015, 2), (0.0000025, 2), (0.0000005, None),
                                                   (0, None), (-1, None), (True, None), (10**9 + 1, None)])
def test_timer_boundary_half_even_and_invalid_not_ready(attenuation_db, fake_repo, timer, expected_ticks):
    change(fake_repo, attenuation_db, lambda rows: rows[0].update(resetCycle=timer))
    result = query(attenuation_db)
    assert result["projection"]["reset_cycle_ticks"] == expected_ticks
    assert result["execution"]["status"] == ("pending" if expected_ticks is None else "ready")


@pytest.mark.parametrize("sequence", [[], [True, False], [1, 0.25], [1] * 65])
def test_sequence_boundary_and_boolean_not_binary(attenuation_db, fake_repo, sequence):
    change(fake_repo, attenuation_db, lambda rows: rows[0].update(durabilitySequence=sequence))
    result = query(attenuation_db)
    assert result["raw_record"]["durabilitySequence"] == sequence
    assert result["execution"]["status"] == "pending"


@pytest.mark.parametrize("group", ["", " ", True, 1, [], "x" * 129])
def test_invalid_selection_rejected_before_read(attenuation_db, group):
    with pytest.raises(ValueError):
        query(attenuation_db, group)


def test_exact_group_missing_duplicate_and_snapshot_identity(attenuation_db, fake_repo):
    original = query(attenuation_db)
    with pytest.raises(RuntimeConfigError) as exc:
        query(attenuation_db, "Default")
    assert exc.value.reason == "not_found"
    with pytest.raises(RuntimeConfigError, match="group重复"):
        change(fake_repo, attenuation_db, lambda rows: rows.append(dict(rows[0])))
    assert query(attenuation_db) == original
    change(fake_repo, attenuation_db, lambda rows: rows.pop())
    change(fake_repo, attenuation_db, lambda rows: rows[0].update(resetCycle=3))
    with pytest.raises(RuntimeConfigError) as exc:
        query(attenuation_db, snapshot_id=original["snapshot"]["snapshot_id"])
    assert exc.value.reason == "snapshot_changed"


def test_projection_fingerprint_ignores_presentation_but_detects_source(attenuation_db, fake_repo, monkeypatch):
    from genshin_db import runtime_attenuation
    original = query(attenuation_db)
    capabilities = runtime_attenuation.attenuation_capabilities
    monkeypatch.setattr(runtime_attenuation, "attenuation_capabilities", lambda: {**capabilities(), "notes": ["new wording"]})
    assert query(attenuation_db)["content_fingerprint"] == original["content_fingerprint"]
    change(fake_repo, attenuation_db, lambda rows: rows[0].update(resetCycle=3))
    assert query(attenuation_db)["content_fingerprint"] != original["content_fingerprint"]


@pytest.mark.parametrize("limit", ["max_bytes", "max_source_rows"])
def test_output_and_scan_limits_do_not_truncate(attenuation_db, monkeypatch, limit):
    from genshin_db.runtime_attenuation import ATTENUATION_LIMITS
    monkeypatch.setitem(ATTENUATION_LIMITS, limit, 1)
    with pytest.raises(RuntimeConfigError) as exc:
        query(attenuation_db)
    assert exc.value.reason == "response_too_large"
