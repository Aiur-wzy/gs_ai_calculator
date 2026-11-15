"""The real queue's logical switch follows action phases and precedes children."""

from genshin_engine.mechanics import execute_mechanics
from tests.test_mechanics_execution import ability, action, second_character
from tests.test_resource_threshold_lifecycle_execution import life_case


def test_clock2_logical_frontline_switch_and_original_action_are_monotone():
    args = life_case()
    second_character(args[0], args[1])
    args[1]["abilities"].append(ability("switch", "a", "action.switch"))
    args[1]["actions"].append(action("switch-b", "switch", .21, .22, order=25, switch_to="b", repeat={"mode": "once"}))
    out = execute_mechanics(*args)
    assert out["result"] is not None, out["issues"]
    assert [r["expected"] for r in out["result"]["hits"]] == [45, 90, 45, 90, 45]


def test_zero_duration_switch_and_its_end_own_distinct_positions():
    args = life_case()
    second_character(args[0], args[1])
    args[1]['abilities'].append(ability('switch', 'a', 'action.switch'))
    args[1]['actions'].append(action('switch-b', 'switch', .21, .21, order=25,
        switch_to='b', repeat={'mode': 'once'}))
    out = execute_mechanics(*args)
    assert out['result'] is not None, out['issues']
    assert not any(i['code'] == 'state_position' for i in out['issues'])
