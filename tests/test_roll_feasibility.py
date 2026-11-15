import copy

from genshin_app.equipment.roll_feasibility import aggregate_roll_feasibility


def construction(main="FIGHT_PROP_ELEMENT_MASTERY", level=20):
    return {"rarity": 5, "level": level, "sands": main, "goblet": main, "circlet": main}


def roll(prop, count):
    return {"prop_type": prop, "count": count, "value_per_roll": 1, "source": "explicit test"}


def test_three_em_mains_cannot_have_35_em_subrolls_without_changing_inputs():
    mains, rolls = construction(), [roll("FIGHT_PROP_ELEMENT_MASTERY", 35)]
    before = copy.deepcopy((mains, rolls))
    out = aggregate_roll_feasibility(mains, rolls)
    assert out["status"] == "infeasible"
    assert out["issues"][0]["maximum"] == 12
    assert out["issues"][0]["available_slots"] == ["flower", "feather"]
    assert (mains, rolls) == before


def test_duplicate_rows_share_capacity_not_independent_roll_limits():
    out = aggregate_roll_feasibility(construction(), [roll("FIGHT_PROP_ELEMENT_MASTERY", 7)] * 2)
    assert out["status"] == "infeasible"
    assert out["issues"][0]["requested"] == 14


def test_fractional_crit_balancing_remains_a_bound_not_item_proof():
    out = aggregate_roll_feasibility(construction(), [roll("FIGHT_PROP_CRITICAL", 13.3),
                                                     roll("FIGHT_PROP_CRITICAL_HURT", 16.7)])
    assert out["status"] == "bounds_passed"
    assert out["proof_scope"] == "necessary_aggregate_bounds_not_item_allocation"


def test_early_artifact_levels_and_non_substat_properties_are_explained():
    out = aggregate_roll_feasibility(construction(level=0), [roll("FIGHT_PROP_CRITICAL", 6)])
    assert out["issues"][0]["maximum"] == 5
    out = aggregate_roll_feasibility(construction(), [roll("FIGHT_PROP_FIRE_ADD_HURT", 1)])
    assert out["status"] == "infeasible"
    assert out["issues"][0]["maximum"] == 0


def test_missing_template_and_unreviewed_rarity_are_unknown_not_illegal():
    assert aggregate_roll_feasibility(None, [])['status'] == "unknown"
    other = {**construction(), "rarity": 4}
    assert aggregate_roll_feasibility(other, [])['status'] == "unknown"


def test_two_crit_types_cannot_count_unrelated_initial_lines_as_upgrades():
    mains = {**construction(), "circlet": "FIGHT_PROP_CRITICAL"}
    out = aggregate_roll_feasibility(mains, [roll("FIGHT_PROP_CRITICAL", 17), roll("FIGHT_PROP_CRITICAL_HURT", 18)])
    assert out["status"] == "infeasible"
    assert out["requested_type_slot_upper_bounds"] == [7, 7, 7, 7, 6]
    assert out["issues"][0]["maximum_allocatable_upper_bound"] == 34
