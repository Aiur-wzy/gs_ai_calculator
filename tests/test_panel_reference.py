from __future__ import annotations

import copy
import json
import sqlite3
from contextlib import contextmanager, nullcontext

import pytest

from genshin_app.equipment import panel_reference as module
from genshin_app.equipment.panel_calculation import calculate_static_panel
from genshin_app.equipment.schema import PlanError
from genshin_db import equipment_queries


@pytest.fixture
def source(monkeypatch, tmp_path):
    path = tmp_path / "reference.db"
    path.write_bytes(b"READ_ONLY_SENTINEL")
    character = {"atk_90": 300, "hp_90": 12000, "def_90": 800,
                 "atk_100": 350, "hp_100": 14000, "def_100": 850,
                 "crit_rate": .05, "crit_dmg": .5, "charge_efficiency": 1,
                 "ascension_prop": "FIGHT_PROP_CRITICAL_HURT", "ascension_value": .384}
    @contextmanager
    def scope(_path):
        yield
    monkeypatch.setattr(module.queries, "equipment_read_session", scope)
    monkeypatch.setattr(module.queries, "get_meta", lambda _: {"upstream_commit:release": "a" * 40})
    monkeypatch.setattr(module.queries, "get_character_detail", lambda *a, **kw: {"character": copy.deepcopy(character)})
    monkeypatch.setattr(module.queries, "get_weapon_stats", lambda *a: {"properties": [
        {"prop_type": "FIGHT_PROP_BASE_ATTACK", "value": 674, "source_refs": []},
        {"prop_type": "FIGHT_PROP_CRITICAL_HURT", "value": .44, "source_refs": []}]})
    values = {"FIGHT_PROP_HP": 4780, "FIGHT_PROP_ATTACK": 311, "FIGHT_PROP_ATTACK_PERCENT": .466,
              "FIGHT_PROP_FIRE_ADD_HURT": .466, "FIGHT_PROP_CRITICAL": .311}
    monkeypatch.setattr(module.equipment_queries, "get_artifact_main_stat_for_slot", lambda p, rarity, slot, level, prop, line: {
        "state": "ready", "value": values[prop], "source_refs": [], "selector": {"prop_type": prop}, "candidate_values": [values[prop]]})
    monkeypatch.setattr(module.queries, "get_artifact_set_detail", lambda *a: {"effects": [
        {"required_count": 2, "effect": {"open_config": "", "param_list": [0] * 8,
         "add_props": [{"propType": "FIGHT_PROP_ATTACK_PERCENT", "value": .18}], "source_refs": []}},
        {"required_count": 4, "effect": {"open_config": "CONDITIONAL", "param_list": [0] * 8,
         "add_props": [{"propType": "FIGHT_PROP_ATTACK_PERCENT", "value": 99}], "source_refs": []}}]})
    return path, character


def request():
    return {"db_character_id": 1, "level": 90, "weapon": {"weapon_id": 1, "level": 90, "ascension": 6},
            "artifacts": {"rarity": 5, "level": 20, "sands": "FIGHT_PROP_ATTACK_PERCENT",
                          "goblet": "FIGHT_PROP_FIRE_ADD_HURT", "circlet": "FIGHT_PROP_CRITICAL"},
            "artifact_sets": [{"set_id": 15001, "count": 4}],
            "substats": [{"prop_type": "FIGHT_PROP_ATTACK_PERCENT", "count": 5, "value_per_roll": .0495, "source": "用户平均档口径"},
                         {"prop_type": "FIGHT_PROP_CRITICAL", "count": 10.5, "value_per_roll": .033, "source": "题面"}]}


def test_aggregate_reference_includes_feather_once_and_never_multiplies_flat(source):
    path, _ = source
    before = path.read_bytes()
    req = request()
    req["current_panel"] = {"atk": 1500}
    unchanged = copy.deepcopy(req)
    result = module.reference_panel(path, req)
    panel = result["reference_panel"]
    assert panel["atk"] == pytest.approx((300 + 674) * (1 + .466 + .2475 + .18) + 311)
    assert panel["hp"] == 16780
    assert panel["crit_rate"] == pytest.approx(.05 + .311 + 10.5 * .033)
    assert panel["crit_dmg"] == pytest.approx(.5 + .384 + .44)
    assert panel["dmg_bonus"]["pyro"] == .466
    assert result["differences"][0]["stat"] == "atk"
    assert any(w["code"] == "non_static_set_effect_excluded" for w in result["warnings"])
    assert result["authority"] == "advisory_only" and result["candidate_effect"] == "none"
    assert req == unchanged and path.read_bytes() == before


def test_budget_is_soft_and_continuous_count_is_preserved(source):
    req = request()
    req["budget"] = {"total": 10, "crit_total": 10}
    result = module.reference_panel(source[0], req)
    assert result["budget"]["used"] == 15.5
    assert result["budget"]["exceeded"] is True
    assert result["budget"]["comparisons"]["total"] == {"used": 15.5, "limit": 10, "status": "exceeded"}
    assert result["reference_panel"]["atk"] is not None
    assert result["acceptance_effect"] == "none"


def test_nonzero_parameters_do_not_hide_explicit_unconditional_property(source, monkeypatch):
    monkeypatch.setattr(module.queries, "get_artifact_set_detail", lambda *a: {"effects": [
        {"required_count": 2, "effect": {"open_config": "", "param_list": [.18],
         "add_props": [{"propType": "FIGHT_PROP_ATTACK_PERCENT", "value": .18}], "source_refs": []}},
        {"required_count": 4, "effect": {"open_config": "Conditional", "param_list": [.50],
         "add_props": [{"propType": "FIGHT_PROP_ATTACK_PERCENT", "value": .50}], "source_refs": []}}]})
    result = module.reference_panel(source[0], request())
    assert result["reference_panel"]["atk"] == pytest.approx(974 * (1 + .466 + .2475 + .18) + 311)
    assert len([r for r in result["contributions"] if r["contribution_id"].startswith("artifact.set.")]) == 1
    assert result["acceptance_effect"] == "none"


def test_omitted_feather_is_reported_as_311_difference_without_rewriting_panel(source):
    req = request()
    # Independent arithmetic: character + weapon, multiplied only by percent
    # attack. The submitted final panel deliberately omits the flat feather.
    req["current_panel"] = {"atk": 974 * (1 + .466 + .2475 + .18)}
    before = copy.deepcopy(req)
    result = module.reference_panel(source[0], req)
    assert result["differences"][0]["difference"] == pytest.approx(-311)
    assert req == before and result["acceptance_effect"] == "none"


def test_budget_describes_unsupplied_and_partial_limits(source):
    req = request()
    result = module.reference_panel(source[0], req)["budget"]
    assert all(row["status"] == "not_compared" for row in result["comparisons"].values())
    req["budget"] = {"total": 20}
    result = module.reference_panel(source[0], req)["budget"]
    assert result["comparisons"]["total"]["status"] == "within_limit"
    assert result["comparisons"]["crit_total"]["status"] == "not_compared"


def test_unknown_contribution_does_not_zero_or_hide_other_attributes(source):
    req = request()
    req["static_contributions"] = [{"contribution_id": "unknown.atk", "stat": "atk", "operation": "percent", "value": None, "source": "待核来源"}]
    result = module.reference_panel(source[0], req)
    assert result["reference_panel"]["atk"] is None
    assert result["reference_panel"]["hp"] == 16780
    assert result["completeness"] == "partial"


def test_unspecified_weapon_does_not_fake_known_secondaries(source):
    req = request()
    del req["weapon"]
    result = module.reference_panel(source[0], req)
    assert result["reference_panel"]["atk"] is None
    assert result["reference_panel"]["hp"] is None
    assert result["basis"]["character_base_hp"] == 12000


def test_duplicate_source_and_percentage_on_crit_are_request_errors(source):
    for stat, operation, ident in [("atk", "flat", "artifact.main.feather"), ("crit_rate", "percent", "wrong")]:
        req = request()
        req["static_contributions"] = [{"contribution_id": ident, "stat": stat, "operation": operation, "value": .1, "source": "manual"}]
        with pytest.raises(PlanError, match="不合法|重复"):
            module.reference_panel(source[0], req)


def test_unknown_support_in_same_arithmetic_core():
    base = {"atk": None, "hp": 1000, "def": 300, "crit_rate": .05, "crit_dmg": .5,
            "em": 0, "energy_recharge": 1, "dmg_bonus": {}}
    result = calculate_static_panel(base, [{"stat": "hp", "operation": "flat", "value": 100}], allow_unknown=True)
    assert result["atk"] is None and result["hp"] == 1100


def test_abstract_main_stat_checks_all_sources_without_arbitrary_piece_id(monkeypatch):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("CREATE TABLE artifact (main_prop_depot_id INTEGER,max_source_level INTEGER,rarity INTEGER,equip_type TEXT,version_line TEXT);"
                      "CREATE TABLE artifact_main_prop (depot_id INTEGER,prop_type TEXT,version_line TEXT);"
                      "CREATE TABLE artifact_level (source_rank INTEGER,source_level INTEGER,version_line TEXT,add_props_json TEXT,source_refs_json TEXT);")
    con.execute("INSERT INTO artifact VALUES (9,21,5,'EQUIP_NECKLACE','release')")
    con.execute("INSERT INTO artifact_main_prop VALUES (9,'FIGHT_PROP_ATTACK','release')")
    def growth(value):
        con.execute("INSERT INTO artifact_level VALUES (5,21,'release',?,'[]')", (json.dumps([{"propType": "FIGHT_PROP_ATTACK", "value": value}]),))
    growth(311)
    @contextmanager
    def scope(*_):
        yield con, {"upstream_commit": "a" * 40}
    monkeypatch.setattr(equipment_queries, "_read", scope)
    read = lambda: equipment_queries.get_artifact_main_stat_for_slot("unused", 5, "EQUIP_NECKLACE", 20, "FIGHT_PROP_ATTACK")
    assert read()["value"] == 311
    growth(333)
    assert read()["state"] == "ambiguous" and read()["value"] is None
    assert read()["candidate_values"] == [311, 333]
    con.close()


def test_total_set_count_cannot_exceed_five_but_three_plus_two_is_valid(source):
    req = request()
    req["artifact_sets"] = [{"set_id": 1, "count": 4}, {"set_id": 2, "count": 4}]
    with pytest.raises(PlanError, match="五件") as error:
        module.reference_panel(source[0], req)
    assert error.value.reason == "invalid_panel_reference"
    req["artifact_sets"] = [{"set_id": 1, "count": 3}, {"set_id": 2, "count": 2}]
    assert module.reference_panel(source[0], req)["authority"] == "advisory_only"


@pytest.mark.parametrize("panel", [{"atk": "1200"}, {"atk": True}, {"dmg_bonus": "wrong"},
                                   {"dmg_bonus": {"pyro": []}}, {"typo_atk": 1200}])
def test_malformed_comparison_is_not_silently_ignored(source, panel):
    req = request()
    req["current_panel"] = panel
    with pytest.raises(PlanError) as error:
        module.reference_panel(source[0], req)
    assert error.value.reason == "invalid_panel_reference"


def test_partial_comparison_and_unknown_values_remain_legal(source):
    req = request()
    req["current_panel"] = {"atk": None, "dmg_bonus": {"pyro": None}, "crit_rate": .4}
    result = module.reference_panel(source[0], req)
    assert [row["stat"] for row in result["differences"]] == ["crit_rate"]


@pytest.mark.parametrize("case", ["nested_bonus", "roll_total", "comparison_difference"])
def test_aggregate_overflows_are_typed_request_errors(source, case):
    req = request()
    if case == "nested_bonus":
        req["static_contributions"] = [{"contribution_id": "big." + str(i), "stat": "dmg_bonus:pyro",
            "operation": "flat", "value": 1e308, "source": "overflow probe"} for i in range(2)]
    elif case == "roll_total":
        req["substats"] = [{"prop_type": "FIGHT_PROP_CRITICAL", "count": 1e308,
                            "value_per_roll": 0, "source": "overflow probe"} for _ in range(2)]
    else:
        req["static_contributions"] = [{"contribution_id": "negative", "stat": "em",
                                        "operation": "flat", "value": -1e308, "source": "overflow probe"}]
        req["current_panel"] = {"em": 1e308}
    with pytest.raises(PlanError) as error:
        module.reference_panel(source[0], req)
    assert error.value.reason == "invalid_panel_reference"


def test_abstract_main_stat_keeps_null_growth_unknown(monkeypatch):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("CREATE TABLE artifact (main_prop_depot_id INTEGER,max_source_level INTEGER,rarity INTEGER,equip_type TEXT,version_line TEXT);"
                      "CREATE TABLE artifact_main_prop (depot_id INTEGER,prop_type TEXT,version_line TEXT);"
                      "CREATE TABLE artifact_level (source_rank INTEGER,source_level INTEGER,version_line TEXT,add_props_json TEXT,source_refs_json TEXT);")
    con.execute("INSERT INTO artifact VALUES (9,21,5,'EQUIP_NECKLACE','release')")
    con.execute("INSERT INTO artifact_main_prop VALUES (9,'FIGHT_PROP_ATTACK','release')")
    for values in ([{"propType": "FIGHT_PROP_ATTACK", "value": 311}],
                   [{"propType": "FIGHT_PROP_ATTACK", "value": None}]):
        con.execute("INSERT INTO artifact_level VALUES (5,21,'release',?,'[]')", (json.dumps(values),))
    @contextmanager
    def scope(*_):
        yield con, {"upstream_commit": "a" * 40}
    monkeypatch.setattr(equipment_queries, "_read", scope)
    result = equipment_queries.get_artifact_main_stat_for_slot("unused", 5, "EQUIP_NECKLACE", 20, "FIGHT_PROP_ATTACK")
    assert result["state"] == "source_missing"
    assert result["value"] is None and result["candidate_values"] == [311]
    con.close()


def test_real_agent_tool_and_host_reference_panel_are_pure_read(source, tmp_path):
    from genshin_app.agent_chat import tools
    from tests.test_native_app_tool_host import Session, call, host

    session = Session()
    session.db_path = source[0]
    session.candidate["version_line"] = "release"
    session._read_db = nullcontext
    before_candidate = copy.deepcopy(session.candidate)
    before_resources = copy.deepcopy(session.resources)
    current = host(tmp_path, session, tools.call_tool)
    before_snapshot = current.snapshot
    result = call(current, tool="gs_reference_panel", raw=json.dumps({"request": request()}))
    assert result["terminal_state"] == "settled_success"
    response = result["result"]
    assert response["code"] == 0
    assert response["data"]["reference_panel"]["atk"] > 0
    assert response["data"]["comparison_binding"]["candidate_revision"] == session.revision
    assert response["data"]["candidate_effect"] == response["data"]["acceptance_effect"] == "none"
    assert current.snapshot == before_snapshot
    assert session.candidate == before_candidate and session.resources == before_resources
    assert session.calculated is None and session.report is None
    assert source[0].read_bytes() == b"READ_ONLY_SENTINEL"
