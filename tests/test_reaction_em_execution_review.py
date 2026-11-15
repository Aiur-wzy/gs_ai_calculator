"""Independent C3 actual trace join: identities and order, not game evidence."""
import copy

import pytest

from genshin_app.equipment.reaction_em_execution import verify_reaction_em_execution
from genshin_app.equipment.schema import PlanError
from tests.test_reaction_em_execution import executed


@pytest.mark.parametrize('change',['reaction_before_parent','activation_before_reaction','reused_real_run','refresh_borrows_first'])
def test_occurrence_joins_need_real_trace_order_and_unique_current_run(change):
    out,comp=executed();trace=out['mechanics_trace']
    reactions=[r for r in trace if r.get('type')=='elemental_reaction' and r.get('cycle')==0]
    parents=[r for r in trace if r.get('type')=='elemental_application' and r.get('cycle')==0]
    acts=[r for r in trace if r.get('type')=='activation' and r.get('cycle')==0]
    if change=='reaction_before_parent':
        row=reactions[0];trace.remove(row);trace.insert(trace.index(parents[0]),row)
    elif change=='activation_before_reaction':
        row=acts[0];trace.remove(row);trace.insert(trace.index(reactions[0]),row)
    elif change=='reused_real_run':
        reactions[1]['run_id']=reactions[0]['run_id']
        parents[1]['reaction']['run_id']=reactions[0]['run_id']
    else:
        acts[1]['event_id']=acts[0]['event_id']
    before=copy.deepcopy(out)
    with pytest.raises(PlanError) as error:verify_reaction_em_execution(out,comp)
    assert error.value.reason=='reaction_em_pending' and error.value.details['result'] is None
    assert out==before
