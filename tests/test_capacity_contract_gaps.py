# ruff: noqa: F811 - pytest fixture injection uses imported names.
import copy

import pytest
from test_capacity_inputs import input_plan
from test_capacity_model import (  # noqa: F401
    action_resource_synthetic_db_only,
    fake_repo,
    query,
    ready,
    source_db,
)

from genshin_app.equipment.action_capacities import (
    compile_action_capacities,
    verify_final_pools,
)
from genshin_app.equipment.action_capacity_inputs import actual_selection
from genshin_app.equipment.schema import PlanError
from genshin_runtime.action_capacity_model import verify_material

pytestmark = pytest.mark.usefixtures("action_resource_synthetic_db_only")


def test_deleted_text_reference_cannot_be_hidden_by_deleting_saved_text(ready):
    profile = query(ready)
    claim = copy.deepcopy(profile['model_record']['payload']['claim'])
    material = copy.deepcopy(profile['model_material'])
    claim['text_inventory'].pop()
    material['texts'].pop()
    with pytest.raises(ValueError):
        verify_material(claim, material)


@pytest.mark.parametrize('field,value', [('duration', 1), ('start', 'occupation_end'), ('recovery', 'parallel')])
def test_final_pool_must_keep_confirmed_cooldown_semantics(ready, field, value):
    plan = input_plan()
    _, records, _ = compile_action_capacities(plan, ready[0], lambda *a: None)
    plan['action_capacity_sources'][0].update(records[0]['required_confirmation'])
    rotation, records, _ = compile_action_capacities(plan, ready[0], lambda *a: None)
    rotation['abilities'][0]['cooldown'][field] = value
    with pytest.raises(PlanError):
        verify_final_pools(plan, rotation, records)


def test_normal_hit_level_is_not_compared_to_skill_level():
    plan = input_plan()
    plan['equipment_inputs']['a']['progression']['talent_base_levels']['normal'] = 8
    plan['hit_parameter_sources'] = [{'hit_id': 'h0', 'version_line': 'release', 'selection': {
        'character_id': 90000001, 'skill_depot_id': 9001, 'skill_id': 90011,
        'slot': 'normal', 'base_level': 8, 'constellation': 1}}]
    actual_selection(plan, plan['action_capacity_sources'][0])
