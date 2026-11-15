"""Independent EC19 execution and offline-proof boundaries; isolated inputs only."""
from __future__ import annotations

import copy
import io
import json
import xml.etree.ElementTree as ET
import zipfile

import pytest
from test_automatic_energy_review import energy_case
from test_energy_generation_sources import confirm_energy, source_declaration
from test_freeze_runtime import CONTACT
from test_freeze_runtime import model as freeze_model
from test_limited_hit_benefit_sources import benefit_plan, confirm_benefit
from test_mechanics_execution import (
    EVIDENCE,
    ability,
    action,
    add,
    fixture,
    rule,
    second_character,
)
from test_pipeline import fake_repo  # noqa: F401
from test_runtime_energy_dispatch import origin, trigger_db, trigger_repo  # noqa: F401
from test_runtime_limited_hit_benefit import benefit_db, benefit_repo  # noqa: F401

from genshin_app.equipment import run_plan_calc
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import export_resolved_plan, result_bytes
from genshin_engine.mechanics import execute_mechanics

# ruff: noqa: F811
CONTRACT = "verified-empty-damage-v1"
ONCE = {"mode": "once"}


def miss_case(times=(.2, .4)):
    template, rotation, mechanics = fixture(times)
    rotation["actions"][0]["repeat"] = dict(ONCE)
    mechanics["elemental_model"] = freeze_model()
    for desc in mechanics["hit_events"]:
        desc.update(application={"profile_id": "cryo", "target_id": "enemy", "landed": False},
                    contact=copy.deepcopy(CONTACT))
    return template, rotation, mechanics


def require_known_zero(output, count):
    assert output["status"] == "unproven", output["issues"]
    result = output["result"]
    assert output["resolved_template"]["hits"] == result["hits"] == []
    assert result["summary"]["total_damage"] == result["summary"]["dps"] == 0
    assert result["mechanics"]["complete_first_cycle"] is True
    report = result["damage_reporting"]
    assert report["contract"] == CONTRACT
    assert report["status"] == "verified_zero"
    assert report["reason"] == "all_declared_contacts_missed"
    assert report["planned_contacts"] == report["missed_contacts"] == count
    assert report["reported_damage_events"] == 0
    assert output["rotation_check"]["energy_check"]["proven_cycle"] is None
    return result


def require_not_known_zero(output):
    result = output.get("result") or {}
    assert result.get("damage_reporting", {}).get("status") != "verified_zero"
    assert result.get("summary", {}).get("dps") is None


@pytest.mark.parametrize("times", [(.2,), (.2, .2, .4)])
def test_review_complete_all_miss_is_zero_without_fictitious_damage_or_cycle_proof(times):
    args = miss_case(times)
    before = copy.deepcopy(args)
    output = execute_mechanics(*args)
    result = require_known_zero(output, len(times))
    assert args == before
    rows = [r for r in output["rotation_check"]["event_log"] if r["type"] == "mechanics_hit"]
    assert len(rows) == len(times) and all(r["status"] == "missed" for r in rows)
    assert not any(r["type"] in ("hit", "reaction_damage") for r in output["mechanics_trace"])
    assert result["summary"]["by_character"] == {}


def test_review_three_explicit_actions_count_once_inside_the_full_window():
    template, rotation, mechanics = miss_case((.2, 3.2, 6.2))
    rotation["max_cycles"] = 7
    rotation["actions"] = [action(f"cast{i}", start=3*i, end=3*i+.1, repeat=dict(ONCE))
                           for i in range(3)]
    for i, desc in enumerate(mechanics["hit_events"]):
        desc["action_id"] = f"cast{i}"
    output = execute_mechanics(template, rotation, mechanics)
    report = require_known_zero(output, 3)["damage_reporting"]
    assert report["scope"]["origin"] == 0
    assert report["scope"]["end_tick"] == 10_000_000
    assert len({r["run_id"] for r in report["contacts"]}) == 3


def test_review_distinct_actors_are_both_covered_after_real_switch():
    template, rotation, mechanics = miss_case((.2, 2.2))
    second_character(template, rotation)
    template["hits"][1]["character"] = "b"
    template["switches"] = [{"t": 1, "character": "b"}]
    rotation["abilities"].extend([ability("switch", "a", "action.switch"), ability("eb", "b")])
    rotation["actions"].extend([action("switch", "switch", 1, 1.01, repeat=dict(ONCE), switch_to="b"),
                                 action("cast-b", "eb", 2, 2.1, repeat=dict(ONCE))])
    mechanics["hit_events"][1]["action_id"] = "cast-b"
    result = require_known_zero(execute_mechanics(template, rotation, mechanics), 2)
    assert {r["action_id"] for r in result["damage_reporting"]["contacts"]} == {"cast", "cast-b"}


def test_review_right_boundary_contact_is_not_inside_origin_zero_denominator():
    output = execute_mechanics(*miss_case((.2, 10)))
    report = require_known_zero(output, 1)["damage_reporting"]
    assert [r["event_id"] for r in report["contacts"]] == ["h0"]
    assert any(r["id"] == "h1" and r["t"] == 10 for r in output["rotation_check"]["event_log"])


@pytest.mark.parametrize("miss_first", [False, True])
def test_review_real_zero_damage_hit_remains_a_real_calculation_row(miss_first):
    template, rotation, mechanics = miss_case((.2, .4) if miss_first else (.2,))
    template["hits"][-1]["multiplier"] = 0
    mechanics["hit_events"][-1]["application"]["landed"] = True
    output = execute_mechanics(template, rotation, mechanics)
    assert output["status"] == "unproven", output["issues"]
    assert len(output["result"]["hits"]) == 1
    assert output["result"]["hits"][0]["expected"] == 0
    assert output["result"]["summary"]["dps"] == 0
    assert "damage_reporting" not in output["result"]


def test_review_all_miss_keeps_activation_and_automatic_spend_but_no_quota_or_particles():
    template, rotation, mechanics = energy_case(initial=10, capacity=10, cost=5, refund=None)
    mechanics["elemental_model"] = freeze_model()
    mechanics["hit_events"][0].update(
        application={"profile_id": "cryo", "target_id": "enemy", "landed": False},
        contact=copy.deepcopy(CONTACT))
    buff = rule("pool", quota={"capacity": 7, "scope": "per_beneficiary", "refresh": "reset",
                              "consume": {"event": "hit", "phase": "pre"}})
    particles = rule("on-hit", trigger={"event": "hit", "phase": "post"})
    particles["effects"] = []
    particles["resources"] = [{"id": "p", "kind": "particles", "delay": .1,
        "travel_time": .2, "count": 4, "particle_type": "particle", "element": "cryo", "source_part": "p"}]
    add(mechanics, buff, particles)
    mechanics["resource_ownership"] = [{"rule_id": "on-hit", "action_id": "cast",
        "source_part": "p", "replaces_event_ids": []}]
    output = execute_mechanics(template, rotation, mechanics)
    require_known_zero(output, 1)
    assert [r["rule_id"] for r in output["mechanics_trace"] if r["type"] == "activation"] == ["pool"]
    assert not any(r["type"] == "quota" for r in output["mechanics_trace"])
    check = output["rotation_check"]
    assert check["characters"][0]["final_energy"] == 5
    audits = [r for r in check["event_log"] if r.get("delivery") == "automatic_action_start"]
    assert len(audits) == 1 and (audits[0]["before"], audits[0]["after"]) == (10, 5)
    assert not any(r["type"] in ("energy_spawn", "energy_credit") for r in check["event_log"])


@pytest.mark.parametrize("case", ["unknown_profile", "unknown_target", "energy_failed", "cooldown_failed",
                                  "unverified_tail", "hidden_only", "mixed_hidden", "independent_application"])
def test_review_empty_projection_never_waives_missing_knowledge_or_unreported_work(case):
    template, rotation, mechanics = miss_case()
    if case == "unknown_profile":
        mechanics["elemental_model"]["profiles"][1]["icd"] = {"kind": "unknown"}
    elif case == "unknown_target":
        mechanics["elemental_model"]["target"]["knowledge"] = "unknown"
    elif case == "energy_failed":
        rotation["abilities"][0].update(action_type_id="action.elemental_burst",
            energy={"required": 10, "amount": 10, "delivery": "action_start"})
        rotation["characters"][0]["initial_energy"] = 0
    elif case in ("cooldown_failed", "unverified_tail"):
        rotation["abilities"][0]["cooldown"] = {
            "group": "skill", "duration": 10, "charges": 1, "start": "action_start", "recovery": "parallel"}
        if case == "cooldown_failed":
            rotation["initial_cooldowns"] = [{"character": "a", "group": "skill", "ready_in": [1]}]
        else:
            rotation["initial_cooldowns"] = [{"character": "a", "group": "skill", "ready_in": []}]
            rotation["actions"].append(action("failed", start=1, end=1.1, repeat=dict(ONCE)))
            template["hits"][1]["t"] = 1.2
            mechanics["hit_events"][1]["action_id"] = "failed"
    elif case in ("hidden_only", "mixed_hidden"):
        for desc in mechanics["hit_events"]:
            desc["report"] = False if case == "hidden_only" else desc["hit_id"] == "h0"
        if case == "mixed_hidden":
            mechanics["hit_events"][1]["application"]["landed"] = True
    else:
        mechanics["application_events"] = [{"id": "outside", "action_id": "cast", "t": .6, "order": 30,
            "application": {"profile_id": "cryo", "target_id": "enemy", "landed": False},
            "contact": copy.deepcopy(CONTACT)}]
    require_not_known_zero(execute_mechanics(template, rotation, mechanics))


def test_review_empty_application_without_damage_remains_unknown_not_zero():
    template, rotation, mechanics = miss_case()
    template["hits"] = []
    mechanics["hit_events"] = []
    mechanics["application_events"] = [{"id": "wind", "action_id": "cast", "t": .2, "order": 10,
        "application": {"profile_id": "anemo", "target_id": "enemy", "landed": True},
        "contact": copy.deepcopy(CONTACT)}]
    output = execute_mechanics(template, rotation, mechanics)
    assert output["status"] == "pending"
    assert output["result"] is output["resolved_template"] is None


def test_review_existing_event_budget_failure_cannot_be_relabelled_known_zero(monkeypatch):
    import genshin_engine.mechanics as module

    monkeypatch.setattr(module, "MAX_MECHANICS_EVENTS", 1)
    template, rotation, mechanics = miss_case()
    delayed = rule("delayed")
    delayed["timing"]["delay"] = .1
    add(mechanics, delayed)
    output = execute_mechanics(template, rotation, mechanics)
    assert output["status"] in ("pending", "invalid")
    require_not_known_zero(output)


@pytest.mark.parametrize("budget", ["contacts", "bytes"])
def test_review_zero_proof_budget_is_controlled_and_never_publishes_truncated_zero(monkeypatch, budget):
    import genshin_engine.damage_reporting as module

    setting = "MAX_DAMAGE_REPORTING_CONTACTS" if budget == "contacts" else "MAX_DAMAGE_REPORTING_BYTES"
    monkeypatch.setattr(module, setting, 0)
    output = execute_mechanics(*miss_case())
    assert output["status"] == "pending"
    assert output["result"] is output["resolved_template"] is None
    assert any(i["code"] == "damage_reporting_limit" for i in output["issues"])


@pytest.mark.parametrize("delta,allowed", [(0, True), (-1, False)])
def test_review_zero_proof_budget_counts_exact_canonical_utf8_bytes(monkeypatch, delta, allowed):
    import genshin_engine.damage_reporting as module

    baseline = execute_mechanics(*miss_case((.2,)))
    report = require_known_zero(baseline, 1)["damage_reporting"]
    size = len(json.dumps(report, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8"))
    monkeypatch.setattr(module, "MAX_DAMAGE_REPORTING_BYTES", size + delta)
    output = execute_mechanics(*miss_case((.2,)))
    if allowed:
        require_known_zero(output, 1)
    else:
        assert output["status"] == "pending" and output["result"] is None


def test_review_zero_proof_budget_does_not_change_old_nonempty_results(monkeypatch):
    import genshin_engine.damage_reporting as module

    monkeypatch.setattr(module, "MAX_DAMAGE_REPORTING_CONTACTS", 0)
    monkeypatch.setattr(module, "MAX_DAMAGE_REPORTING_BYTES", 0)
    template, rotation, mechanics = miss_case((.2,))
    mechanics["hit_events"][0]["application"]["landed"] = True
    output = execute_mechanics(template, rotation, mechanics)
    assert output["result"]["summary"]["total_damage"] == 45
    assert "damage_reporting" not in output["result"]


@pytest.fixture
def zero_calculated(benefit_db):
    plan = benefit_plan(benefit_db)
    plan["action_event_overrides"] = [{"action_id": "cast", "landed": False, "order": 20, **EVIDENCE}]
    return run_plan_calc(confirm_benefit(plan, benefit_db), benefit_db, capture_display=True)


def test_review_real_source_all_miss_keeps_activation_and_offline_delivery(zero_calculated, monkeypatch):
    import sqlite3

    from genshin_app.equipment.delivery import validate_delivery_calculated
    from genshin_app.formula_workbook import validate_formula_calculation

    def forbidden(*args, **kwargs):
        raise AssertionError("zero-result export must not reopen the database")

    monkeypatch.setattr(sqlite3, "connect", forbidden)
    result = zero_calculated["result"]
    assert result["summary"]["total_damage"] == result["summary"]["dps"] == 0
    assert result["limited_hit_benefit_summary"]["activations"] == 1
    assert result["limited_hit_benefit_summary"]["consumed_hits"] == 0
    assert result["sampling_trace"] == []
    validate_delivery_calculated(zero_calculated)
    validate_formula_calculation(zero_calculated)
    loaded = json.loads(result_bytes(result, zero_calculated["result_receipt"], "json"))
    assert loaded["result"]["damage_reporting"]["missed_contacts"] == 1
    assert result_bytes(result, zero_calculated["result_receipt"], "xlsx")


def test_review_real_sources_miss_has_neither_particle_batch_nor_quill_consumption(trigger_db):
    plan = benefit_plan(trigger_db)
    plan["rotation_model"]["characters"][0].update(initial_energy=0, energy_capacity=100)
    source = source_declaration()
    source["origin_reference_id"] = origin(trigger_db, "hold", True)
    plan["energy_generation_sources"] = [source]
    plan["action_event_overrides"] = [{"action_id": "cast", "landed": False, "order": 20, **EVIDENCE}]
    out = run_plan_calc(confirm_benefit(confirm_energy(plan, trigger_db), trigger_db), trigger_db)
    result = out["result"]
    assert result["energy_generation_summary"]["missed_parent_hits"] == 1
    assert result["energy_generation_summary"]["spawned_particles"] == 0
    assert result["energy_generation_summary"]["credited_particles"] == 0
    assert result["limited_hit_benefit_summary"]["activations"] == 1
    assert result["limited_hit_benefit_summary"]["consumed_hits"] == 0
    assert result_bytes(result, out["result_receipt"], "json")


@pytest.mark.parametrize("mutation", ["remove_proofs", "missing_contact", "duplicate_contact", "wrong_count",
                                      "wrong_total", "unknown_parent", "unverified_parent"])
def test_review_outer_rehash_cannot_hide_missing_or_contradictory_zero_coverage(zero_calculated, mutation):
    from genshin_app.formula_workbook import validate_formula_calculation

    result, receipt = zero_calculated["result"], zero_calculated["result_receipt"]
    if mutation == "remove_proofs":
        result.pop("damage_reporting")
        receipt.pop("damage_reporting")
        zero_calculated["report"].pop("damage_reporting", None)
    elif mutation == "missing_contact":
        result["damage_reporting"]["contacts"] = []
    elif mutation == "duplicate_contact":
        result["damage_reporting"]["contacts"] *= 2
    elif mutation == "wrong_count":
        result["damage_reporting"]["missed_contacts"] = 0
    elif mutation == "wrong_total":
        result["summary"]["total_damage"] = 1
    else:
        rows = result["rotation_check"]["event_log"]
        parent = next(row for row in rows if row["type"] == "mechanics_hit")
        if mutation == "unknown_parent":
            rows.remove(parent)
        else:
            parent["status"] = "not_verified"
    receipt["result_fingerprint"] = fingerprint(result)
    with pytest.raises((ValueError, PlanError)):
        result_bytes(result, receipt, "json")
    with pytest.raises((ValueError, PlanError)):
        validate_formula_calculation(zero_calculated)


def test_review_empty_csv_is_rejected_instead_of_losing_all_receipts(zero_calculated):
    with pytest.raises(PlanError):
        result_bytes(zero_calculated["result"], zero_calculated["result_receipt"], "csv")


def test_review_four_artifacts_contain_actual_zero_cells_and_no_fabricated_damage_rows(zero_calculated):
    from genshin_app.equipment import validate_plan
    from genshin_app.equipment.delivery import build_calculation_delivery
    from genshin_app.equipment.files import input_view

    built = build_calculation_delivery(zero_calculated, input_file_sha="a" * 64)
    assert built["diagnostic"] is False
    assert built["summary"]["total_damage"] == built["summary"]["dps"] == 0
    assert built["summary"]["reported_hits"] == 0
    native = json.loads(built["artifacts"]["native_plan"])
    validate_plan(native)
    assert native["resolution"] == {"state": "pending"} and native["resolved_template"] is None
    assert fingerprint(input_view(native)) == zero_calculated["result_receipt"]["input_fingerprint"]
    package = json.loads(built["artifacts"]["calculated"])
    assert package["calculated"]["result"]["damage_reporting"]["missed_contacts"] == 1
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rel_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    for role, detail_name, summary_labels in (
        ("result_workbook", "逐事件明细", ("总伤害", "每秒伤害")),
        ("formula_workbook", "逐事件", ("总期望伤害", "纸面 DPS")),
    ):
        with zipfile.ZipFile(io.BytesIO(built["artifacts"][role])) as archive:
            book = ET.fromstring(archive.read("xl/workbook.xml"))
            rels = {r.attrib["Id"]: r.attrib["Target"] for r in
                    ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))}
            sheets = {s.attrib["name"]: ET.fromstring(archive.read(
                "xl/" + rels[s.attrib[f"{{{rel_ns}}}id"]])) for s in book.findall("s:sheets/s:sheet", ns)}
        cells = {c.attrib["r"]: c for c in sheets["汇总"].findall(".//s:c", ns)}
        for label in summary_labels:
            found = next(address for address, cell in cells.items()
                         if "".join(cell.itertext()) == label)
            numeric = cells["B" + found[1:]]
            assert float(numeric.find("s:v", ns).text) == 0
            assert numeric.get("t") != "e"
        assert not sheets[detail_name].findall(".//s:c[@t='e']", ns)
        # Neither artifact may introduce a numerical damage cell merely to carry metadata.
        damage_column = "G" if role == "result_workbook" else "V"
        assert not [c for c in sheets[detail_name].findall(".//s:c", ns)
                    if c.attrib["r"].startswith(damage_column)
                    and c.attrib["r"][len(damage_column):].isdigit() and c.find("s:v", ns) is not None]


def test_review_empty_resolved_template_is_not_exported_as_a_legacy_calculable_template(benefit_db, tmp_path):
    plan = benefit_plan(benefit_db)
    plan["action_event_overrides"] = [{"action_id": "cast", "landed": False, "order": 20, **EVIDENCE}]
    confirm_benefit(plan, benefit_db)
    target = tmp_path / "misleading-resolved.json"
    with pytest.raises(PlanError):
        export_resolved_plan(plan, benefit_db, target)
    assert not target.exists()
