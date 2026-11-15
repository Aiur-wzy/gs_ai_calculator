"""Requested capacity is not malformed input; true shape errors still win."""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from genshin_app.domain_errors import classify_domain_failure, tool_failure_guidance
from genshin_app.equipment import new_plan
from genshin_app.equipment.attribute_input import validate_author_attribute_model
from genshin_app.equipment.mechanics import enable_mechanics
from genshin_app.equipment.schema import PlanError, validate_plan
from genshin_engine.attribute_budget import HARD_LIMITS, normalize_attribute_budget
from genshin_engine.attribute_ledger import AttributeLedger, AttributeLedgerError
from genshin_engine.attribute_runtime import AttributeRuntime
from genshin_engine.attribute_stat_native import prepare_initials
from genshin_engine.mechanics import execute_mechanics, validate_mechanics_model
from genshin_engine.rotation import _schema_errors
from tests.test_attribute_ledger import attach, contribution, declaration
from tests.test_attribute_ledger_incremental import frame
from tests.test_attribute_stat_runtime import setup


def native_input():
    template, rotation, model = setup()
    plan = enable_mechanics(new_plan(template))
    plan.update(rotation_model=rotation,mechanics_model=model)
    return template,rotation,model,plan


def assert_capacity(issue,dimension,attempted,limit):
    assert issue['code']=='attribute_declaration_limit'
    assert issue['resource']=='attribute_execution'
    assert issue['dimension']==dimension
    assert issue['used']==0 and issue['attempted']==attempted and issue['limit']==limit
    assert issue['severity']=='pending'
    assert issue['adjustment']['path']=='/calculation_options/attribute_limits/'+dimension
    assert issue['adjustment']['minimum_required']==attempted
    assert classify_domain_failure('mechanics_pending',details={'issues':[issue]})['category']=='engine_capability_missing'


def test_v3_low_initials_is_pending_capacity_before_execution():
    template,rotation,model,_=native_input()
    model['execution_options']={'schema_version':1,'attribute_limits':{'initials':1}}
    original=copy.deepcopy((template,rotation,model))
    validation=validate_mechanics_model(model)
    assert len(validation['issues'])==1
    assert_capacity(validation['issues'][0],'initials',4,1)
    output=execute_mechanics(template,rotation,model)
    assert output['status']=='pending' and output['result'] is None
    assert_capacity(output['issues'][0],'initials',4,1)
    assert (template,rotation,model)==original


def test_app_author_shape_reports_actual_adjustable_capacity():
    _,_,model,plan=native_input()
    plan['calculation_options']={'schema_version':1,'attribute_limits':{'initials':1}}
    with pytest.raises(PlanError) as error:
        validate_plan(plan)
    assert error.value.reason=='attribute_declaration_limit'
    assert_capacity(error.value.details[0],'initials',4,1)
    assert classify_domain_failure(error.value.reason,details=error.value.details)['category']=='engine_capability_missing'
    guidance=tool_failure_guidance(error.value.reason,details=error.value.details)
    assert guidance['recovery']['action']=='adjust_execution_budget'
    with pytest.raises(PlanError) as author:
        validate_author_attribute_model(model['attribute_model'],limits=normalize_attribute_budget({'initials':1})['effective'])
    assert author.value.reason=='attribute_declaration_limit'


@pytest.mark.parametrize('malformation',['outside','inside'])
def test_low_budget_does_not_hide_real_schema_failure(malformation):
    _,_,model,plan=native_input()
    if malformation=='outside':model['not_a_field']=True
    else:del model['attribute_model']['initial_basis'][3]['actor']
    plan['calculation_options']={'schema_version':1,'attribute_limits':{'initials':1}}
    with pytest.raises(PlanError) as error:
        validate_plan(plan)
    assert error.value.reason=='invalid_plan'
    assert classify_domain_failure(error.value.reason,details=error.value.details)['category']=='request_error'
    assert any(i['code']=='attribute_declaration_limit' for i in error.value.details)
    assert any(i['code'] in ('schema','unknown_field') for i in error.value.details)
    model['execution_options']=copy.deepcopy(plan['calculation_options'])
    report=validate_mechanics_model(model)
    assert any(i['severity']=='error' for i in report['issues'])


@pytest.mark.parametrize('consumer',['runtime','initials'])
@pytest.mark.parametrize('dimension',['initials','declaration_bytes'])
def test_direct_consumers_report_typed_count_and_byte_capacity(consumer,dimension):
    template,_,model,_=native_input()
    limits={dimension:1}
    if consumer=='runtime':
        model['execution_options']={'schema_version':1,'attribute_limits':limits}
        call=lambda:AttributeRuntime(SimpleNamespace(model=model,template=template))
    else:
        call=lambda:prepare_initials(template,model['attribute_model'],limits=limits)
    with pytest.raises(AttributeLedgerError) as error:
        call()
    attempted=4 if dimension=='initials' else error.value.issue['attempted']
    assert_capacity(error.value.issue,dimension,attempted,1)
    if dimension=='declaration_bytes':assert attempted>1


@pytest.mark.parametrize('dimension',['initials','contributions','frames'])
def test_pure_ledger_declared_array_uses_same_capacity_contract(dimension):
    model=declaration()
    if dimension=='initials':model['initials'].append({**model['initials'][0],'actor':'b'})
    elif dimension=='contributions':attach(model,contribution('first'),contribution('second'))
    else:model['frames'].append(frame('other'))
    with pytest.raises(AttributeLedgerError) as error:
        AttributeLedger(model,limits={dimension:1})
    assert_capacity(error.value.issue,dimension,2,1)


def test_pure_ledger_member_errors_are_not_relabelled_as_capacity():
    model=declaration();model['frames'].append(frame('other'))
    del model['frames'][1]['run_id']
    with pytest.raises(AttributeLedgerError) as error:
        AttributeLedger(model,limits={'frames':1})
    assert error.value.issue['code']=='attribute_schema'
    assert error.value.issue['path'].endswith('[1].run_id')


def test_hard_capacity_boundary_has_no_false_adjustment():
    _,_,model,_=native_input()
    maximum=HARD_LIMITS['initials']
    model['attribute_model']['initial_basis']*=maximum//4+1
    model['execution_options']={'schema_version':1,'attribute_limits':{'initials':maximum}}
    issue=validate_mechanics_model(model)['issues'][0]
    assert issue['dimension']=='initials' and issue['attempted']>maximum
    assert 'adjustment' not in issue


def test_ordinary_array_cap_is_unchanged_even_with_attribute_path_name():
    errors=[]
    _schema_errors([0,1],{'type':'array','maxItems':1,'items':{'type':'number'}},
                   'mechanics_model.attribute_model.initial_basis',errors)
    assert [e['code'] for e in errors]==['limit']
    assert 'dimension' not in errors[0]
