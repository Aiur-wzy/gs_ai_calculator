"""Public App/CLI delivery preserves custom outcomes and their actual effects."""
import copy
import json

import pytest

from genshin_app.cli import main
from genshin_app.equipment import run_plan_calc
from genshin_app.equipment.delivery import validate_delivery_calculated
from genshin_app.gui.api import GuiApi
from tests.test_shared_pool_app_delivery import authorize, pool_plan
from tests.test_shared_pool_probability import choice, effect
from tests.test_shared_pools import config


@pytest.mark.parametrize('success,damage',[(True,[45,90,90]),(False,[45,90,45]),('expected',[45,90,45])])
def test_extra_stack_app_saved_cli_gui_recalculation(tmp_path,monkeypatch,capsys,success,damage):
    plan=pool_plan()
    plan['rotation_model']['actions'][0]['repeat']={'mode':'once'}
    pools=plan['mechanics_model']['shared_pools']
    pools['schema_version']=2
    pools['pools'][0].update(initial={'team':0},unit_scale=1)
    op=copy.deepcopy(config()['operations'][0])
    op.update(pool_id='pool',source_id='binding:part',amount=1,
              trigger={'event':'hit','phase':'post','event_ids':['h0']},
              random_effect=effect('extra_grant',[choice(success,run_id='0:h0')]))
    pools['operations']=[op]
    expected = success == 'expected'
    if expected:
        pools['pools'][0].update(unit_scale=100,capacity=1000)
        pools['consumers'][0]['amount']=100
        op.update(amount=100,random_effect={'kind':'extra_grant','extra_amount':100,'chance':.5})
    db=tmp_path/'absent.db'
    authorize(plan,db)
    before=copy.deepcopy(plan)
    out=run_plan_calc(plan,db,capture_display=True)
    assert [h['expected'] for h in out['result']['hits']]==damage
    report=out['result']['shared_pools']
    assert report['probability_mode']==('expected' if expected else 'custom')
    receipt=next(row for row in report['trace'] if row['type']=='operation')
    assert receipt['probability']['outcome'] is (None if expected else success)
    assert receipt['accepted']==(150 if expected else 1+int(success))
    validate_delivery_calculated(out)
    path=tmp_path/'probability.plan.json'
    path.write_text(json.dumps(out['plan']),encoding='utf8')
    monkeypatch.setattr('genshin_app.cli._require_runtime_database',lambda _: {'can_start':True})
    assert main(['--data-dir',str(tmp_path),'equipment','calc','--plan',str(path)])==0
    replay=json.loads(capsys.readouterr().out)['data']
    assert replay['result']==out['result']
    gui=GuiApi(tmp_path).run_plan_calc(json.loads(path.read_text(encoding='utf8')))
    assert gui['code']==0,gui
    assert gui['data']['result']==out['result']
    assert plan==before
