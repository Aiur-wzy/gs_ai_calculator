"""The same health inputs survive App composition, calculation and saved reopening."""
import json

import pytest
from test_health_mechanics import expression, health_fixture, operation
from test_plan_mechanics import native, review

from genshin_app.equipment import compose_equipment_plan, run_plan_calc


def test_health_native_plan_round_trip_and_actual_output(tmp_path, capsys, monkeypatch):
    db=tmp_path/'absent.db'
    plan=native()
    t,r,m=health_fixture((.2,.4,.6))
    t['characters'][0]['stats']['energy_recharge']=1
    plan.update(manual_template=t,rotation_model=r)
    plan['mechanics_model'].update({k:v for k,v in m.items() if k not in ('rules','initial_state')})
    plan['mechanics_model']['rules'][0]['effects'][0]['value']=expression('hp_change_fraction')
    plan['mechanics_model']['health_model']['events']=[operation('heal1',.15,'heal',1000),
        operation('heal2',.35,'heal',3000),operation('loss',.55,'hp_loss',1000)]
    review(plan,db)
    report=compose_equipment_plan(plan,db)
    assert report['mechanics_model']['health_model']==plan['mechanics_model']['health_model']
    out=run_plan_calc(plan,db)
    assert [h['expected'] for h in out['result']['hits']]==pytest.approx([45,54,58.5])
    assert out['result']['health_ledger']['final_state']['a']['current_hp']==6000
    saved=json.loads(json.dumps(out['plan']))
    reopened=run_plan_calc(saved,db)
    assert reopened['result']==out['result']
    from genshin_app.cli import main
    # Isolate database boot in this transport test; the real bundled database
    # is separately admitted by the frozen package checks.
    monkeypatch.setattr('genshin_app.cli._require_runtime_database', lambda _: {'can_start':True})
    path=tmp_path/'health.plan.json'
    path.write_text(json.dumps(saved),encoding='utf-8')
    assert main(['--data-dir',str(tmp_path),'equipment','calc','--plan',str(path)])==0
    payload=json.loads(capsys.readouterr().out)
    assert payload['data']['result']['health_ledger']==out['result']['health_ledger']
    assert not db.exists()
