"""Independent saved-attribute query checks; no recalculation or DB certification."""
import copy

import pytest

from genshin_app.equipment.schema import PlanError
from tests.test_attribute_stat_query import candidate, resign_synthetic, saved


def resign(value):
    resign_synthetic(value)


def test_saved_fingerprint_cannot_be_ignored_for_new_attribute_view():
    value=saved();value['result']['attribute_ledger']['receipts'][0]['value']=999
    with pytest.raises(PlanError):candidate.review_calculation(value,{'view':'attributes'})


def test_actual_frame_and_receipt_identity_do_not_alias_each_other():
    value=saved();row=value['result']['attribute_ledger']['receipts'][0]
    row['query']['frame']['event_id']='actual-capture'
    resign(value)
    filters={'receipt_id':'receipt-0','frame_id':'frame-0','event_id':'actual-capture','basis':'total','stat':'em'}
    out=candidate.review_calculation(value,{'view':'attributes','filters':filters})
    assert len(out['items'])==1 and out['items'][0]['event_id']=='actual-capture'
    filters['event_id']='receipt-0'
    assert candidate.review_calculation(value,{'view':'attributes','filters':filters})['items']==[]


@pytest.mark.parametrize('field,bad',[('query',[]),('frame',None),('contributions',{}),('issues','unknown')])
def test_malformed_selected_saved_receipt_returns_planerror(field,bad):
    value=saved();row=value['result']['attribute_ledger']['receipts'][0]
    if field=='frame':row['query']['frame']=bad
    else:row[field]=bad
    resign(value)
    with pytest.raises(PlanError) as error:candidate.review_calculation(value,{'view':'attributes'})
    assert error.value.reason=='invalid_review_input'


def test_bounded_projection_has_raw_pointer_and_does_not_modify_or_recompute_receipt():
    value=saved();row=value['result']['attribute_ledger']['receipts'][0]
    row['contributions']=[{'stat':'em','source_id':'真实来源'*200,'evaluated_value':i} for i in range(40)]
    resign(value);before=copy.deepcopy(value)
    out=candidate.review_calculation(value,{'view':'attributes','limit':1})
    item=out['items'][0]
    assert item['contribution_count']==40 and len(item['contributions'])==16
    assert item['json_pointer']=='/result/attribute_ledger/receipts/0'
    assert item['value']==120 and out['engine_summary']==value['result']['summary']
    item['contributions'][0]['evaluated_value']=999
    assert value==before


def test_contribution_source_is_not_inferred_as_receipt_source_authority():
    value=saved();value['result']['attribute_ledger']['receipts'][0]['contributions'][0]['source_id']='writer'
    resign(value)
    assert candidate.review_calculation(value,{'view':'attributes','filters':{'source_id':'writer'}})['items']==[]
