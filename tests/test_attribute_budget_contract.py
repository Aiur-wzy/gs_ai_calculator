"""Instance capacity, author/execution separation and precise union diagnostics."""
import copy
from threading import Event

import pytest

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.attribute_budget_binding import (
    validate_attribute_budget_binding,
)
from genshin_app.equipment.attributes import prepare_attribute_review
from genshin_app.equipment.schema import PlanError, validate_plan
from genshin_engine.attribute_budget import (
    DEFAULT_LIMITS,
    HARD_LIMITS,
    normalize_attribute_budget,
    validate_calculation_options,
)
from genshin_engine.attribute_ledger import AttributeLedger, AttributeLedgerError
from genshin_engine.execution_control import ExecutionCancelled, execution_control_scope
from genshin_engine.mechanics_validation import validate_mechanics_model
from genshin_engine.rotation import _schema_errors
from tests.test_attribute_app_sources import attribute_plan, known_classification
from tests.test_attribute_ledger import declaration, read
from tests.test_attribute_ledger_incremental import frame
from tests.test_plan_mechanics import native, review


@pytest.mark.parametrize('value', [True, None, [], {'schema_version': True}, {'schema_version': 1, 'x':1},
                                 {'schema_version':1,'attribute_limits':None},
                                 {'schema_version':1,'attribute_limits':{'frames':True}},
                                 {'schema_version':1,'attribute_limits':{'frames':0}},
                                 {'schema_version':1,'attribute_limits':{'frames':HARD_LIMITS['frames']+1}}])
def test_options_reject_unpublished_or_noninteger_capacity(value):
    with pytest.raises(ValueError):
        validate_calculation_options(value)


def test_instances_never_share_budget_and_small_limit_is_real():
    data=declaration(); data['frames']=[]
    small=AttributeLedger(data,limits={'frames':1})
    large=AttributeLedger(data,limits={'frames':2})
    for ledger in (small,large):ledger.adopt_frame(frame())
    before=copy.deepcopy(small.usage())
    with pytest.raises(AttributeLedgerError) as error:small.adopt_frame(frame('later'))
    assert error.value.issue['dimension']=='frames'
    assert error.value.issue['used']==1 and error.value.issue['attempted']==2
    assert error.value.issue['adjustment']['path']=='/calculation_options/attribute_limits/frames'
    large.adopt_frame(frame('later'))
    assert small.usage()['frames']==before['frames']
    assert small.usage()['contributions']==before['contributions']
    assert small.usage()['registration_work']==before['registration_work']+2
    assert small.usage()['limits']['frames']==1 and large.usage()['limits']['frames']==2
    assert DEFAULT_LIMITS['frames']==16384


def test_bulk_and_incremental_frames_cross_generic_10000_guard():
    data=declaration();data['frames']=[frame(f'f{i}') for i in range(10001)]
    # Stable IDs/run identities remain distinct; no coalescing by timestamp.
    bulk=AttributeLedger(data)
    empty=copy.deepcopy(data);empty['frames']=[]
    incremental=AttributeLedger(empty)
    for row in data['frames']:incremental.adopt_frame(row)
    assert bulk.usage()['frames']==incremental.usage()['frames']==10001
    assert read(bulk,frame='f10000')['value']==read(incremental,frame='f10000')['value']


def test_cancelled_ledger_cannot_be_reused_after_scope_exits():
    ledger = AttributeLedger(declaration())
    read(ledger)  # A cache hit must not bypass cancellation.
    event = Event()
    with execution_control_scope(event.is_set):
        event.set()
        with pytest.raises(ExecutionCancelled) as error:
            read(ledger)
    assert error.value.issue['code'] == 'calculation_cancelled'
    with pytest.raises(ExecutionCancelled):
        ledger.adopt_frame(frame('after'))
    assert read(AttributeLedger(declaration()))['value'] == 300


def test_schema_walk_observes_scope_cancellation_without_app_mutation():
    event = Event()
    count = 0
    def cancellation():
        nonlocal count
        count += 1
        if count == 20:
            event.set()
        return event.is_set()
    original = declaration()
    before = copy.deepcopy(original)
    with execution_control_scope(cancellation), pytest.raises(ExecutionCancelled):
        AttributeLedger(original)
    assert original == before


@pytest.mark.parametrize('field',['contract','initials','frames','contributions'])
def test_pure_ledger_missing_top_fields_is_structured(field):
    data=declaration();data.pop(field)
    with pytest.raises(AttributeLedgerError) as error:AttributeLedger(data)
    assert error.value.issue['code']=='attribute_schema'


@pytest.mark.parametrize('field',['effect_ref','nonextra_inclusion','status','source'])
def test_author_classification_missing_facts_never_reaches_keyerror(field,tmp_path):
    plan=attribute_plan(); row=known_classification(plan);row.pop(field)
    plan['mechanics_model']['attribute_model']['effect_classifications']=[row]
    with pytest.raises(PlanError):validate_plan(plan)
    with pytest.raises(PlanError):prepare_attribute_review(plan,'Cannot approve incomplete shape',tmp_path/'absent.db')


def test_component_can_be_previewed_but_never_executed_before_adoption(tmp_path):
    plan=attribute_plan();row=known_classification(plan);row.pop('component')
    row.update(nonextra_inclusion='unknown',status='pending')
    plan['mechanics_model']['attribute_model']['effect_classifications']=[row]
    db=tmp_path/'absent.db';review(plan,db)
    original=copy.deepcopy(plan)
    validate_plan(plan)
    assert validate_mechanics_model(plan['mechanics_model'])['status']=='invalid'
    preview=compose_equipment_plan(plan,db)
    assert preview['state']=='pending' and plan==original
    assert any(item['code']=='attribute_source_stale' for item in preview['missing_inputs'])
    adopted=prepare_attribute_review(plan,'Verified exact source identity; classification stays unknown',db)
    actual=adopted['plan']['mechanics_model']['attribute_model']['effect_classifications'][0]
    assert actual['nonextra_inclusion']=='unknown' and actual['status']=='pending'
    assert len(actual['component']['source_fingerprint'])==64 and plan==original
    assert adopted['state']=='ready'


def test_budget_identity_is_bound_to_real_execution_and_preserved_in_receipt(tmp_path):
    plan=attribute_plan();plan['calculation_options']={'schema_version':1,'attribute_limits':{'frames':2000}}
    db=tmp_path/'absent.db';review(plan,db)
    plan=prepare_attribute_review(plan,'Initial basis reviewed, dynamic unknown retained',db)['plan']
    calculated=run_plan_calc(plan,db)
    result,receipt=calculated['result'],calculated['result_receipt']
    binding=validate_attribute_budget_binding(result,receipt,plan)
    assert binding['policy']['effective']['frames']==2000
    changed=copy.deepcopy(result);changed.pop('attribute_execution_budget')
    with pytest.raises(ValueError):validate_attribute_budget_binding(changed,receipt,plan)
    different=copy.deepcopy(plan);different['calculation_options']['attribute_limits']['frames']=2001
    with pytest.raises(ValueError):validate_attribute_budget_binding(result,receipt,different)
    old=copy.deepcopy(result);old.pop('attribute_execution_budget');old.pop('calculation_producer_contract')
    assert validate_attribute_budget_binding(old)==None


def test_union_error_uses_present_contract_stat_and_shared_missing_field():
    schema={'oneOf':[{'type':'object','properties':{'contract':{'const':'v1'},'em':{'type':'number'}},'required':['contract','em'],'additionalProperties':False},
                     {'type':'object','properties':{'contract':{'const':'v3'},'atk':{'type':'number'}},'required':['contract','atk'],'additionalProperties':False}]}
    issues=[];_schema_errors({'contract':'v3','atk':2,'limits':{}},schema,'model',issues)
    assert len(issues)==1 and issues[0]['path']=='model.limits'
    missing=[];_schema_errors({},schema,'model',missing)
    assert missing[0]['path']=='model.contract'


def test_policy_fingerprint_owns_a_copy_of_the_request():
    requested={'frames':1234};policy=normalize_attribute_budget(requested)
    requested['frames']=999
    assert policy['requested']['frames']==policy['effective']['frames']==1234


@pytest.mark.parametrize('dimension', list(HARD_LIMITS))
def test_each_published_hard_limit_is_inclusive_but_not_extendable(dimension):
    maximum = HARD_LIMITS[dimension]
    assert normalize_attribute_budget({dimension:maximum})['effective'][dimension] == maximum
    with pytest.raises(ValueError):
        normalize_attribute_budget({dimension:maximum+1})


def test_evidence_limit_equal_and_over_are_capacity_facts():
    from genshin_app.equipment.attribute_receipt import MAX_EVIDENCE_BYTES, _bounded
    value = {'x':'a'*(MAX_EVIDENCE_BYTES-9)}
    _bounded(value)
    value['x'] += 'a'
    with pytest.raises(PlanError) as error:
        _bounded(value)
    assert error.value.reason == 'attribute_evidence_limit'
    assert error.value.details['actual_bytes'] == MAX_EVIDENCE_BYTES+1
    assert error.value.details['recovery']['action'] == 'report_capacity_boundary'


def test_options_do_not_reopen_generic_array_guard():
    issues=[]
    _schema_errors([0]*10001, {'type':'array','items':{'type':'number'}}, 'ordinary',issues)
    assert issues[0]['code']=='limit'


def test_new_nonconsumer_result_has_bound_not_applicable_receipt(tmp_path):
    from genshin_app.calculation_integrity import (
        validate_calculated_attribute_budget,
        validate_result_binding,
    )
    from genshin_app.equipment.files import fingerprint
    plan=native();db=tmp_path/'absent.db';review(plan,db)
    calculated=run_plan_calc(plan,db)
    result,receipt=calculated['result'],calculated['result_receipt']
    binding=validate_calculated_attribute_budget(calculated['plan'],result,calculated['report'],receipt)
    assert binding['state']=='not_applicable' and 'attribute_ledger' not in result
    assert receipt['attribute_execution_budget']==binding
    validate_result_binding(result,receipt)
    altered=copy.deepcopy(calculated)
    for obj in (altered['result'],altered['result_receipt']):
        obj.pop('calculation_producer_contract');obj.pop('attribute_execution_budget')
    altered['result_receipt']['result_fingerprint']=fingerprint(altered['result'])
    with pytest.raises(ValueError):
        validate_calculated_attribute_budget(altered['plan'],altered['result'],altered['report'],altered['result_receipt'])
    wrong_model=copy.deepcopy(calculated['report']);wrong_model['mechanics_model']=None
    with pytest.raises(ValueError):
        validate_calculated_attribute_budget(calculated['plan'],result,wrong_model,receipt)


def test_nonconsumer_status_cannot_replace_an_attribute_runtime(tmp_path):
    from genshin_app.equipment.attribute_budget_binding import attach_nonconsumer_budget
    with pytest.raises(ValueError):
        attach_nonconsumer_budget({}, {'attribute_model':{'contract':'attribute-ledger-v2'}})
    result={};attach_nonconsumer_budget(result,None)
    with pytest.raises(ValueError):
        validate_attribute_budget_binding(result,execution_model={'attribute_model':{}})
