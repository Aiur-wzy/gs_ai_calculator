"""Independent read-only row builder boundaries, using actual pure receipts."""
import copy
import json

import pytest

from genshin_app.attribute_stat_reporting import attribute_stat_rows
from genshin_app.equipment.schema import PlanError
from tests.test_attribute_stat_reporting import actual_pure_receipts


@pytest.mark.parametrize('field,value',[('receipts',{}),('receipt',None),('query',[]),('contributions',None)])
def test_malformed_saved_receipt_is_controlled(field,value):
    result=actual_pure_receipts()
    if field=='receipts':result['attribute_ledger'][field]=value
    elif field=='receipt':result['attribute_ledger']['receipts'][0]=value
    else:result['attribute_ledger']['receipts'][0][field]=value
    with pytest.raises(PlanError):attribute_stat_rows(result)


@pytest.mark.parametrize('field',['baseline','value'])
def test_nonfinite_raw_values_are_not_written_as_valid_json_or_cells(field):
    result=actual_pure_receipts();result['attribute_ledger']['receipts'][0][field]=float('nan')
    with pytest.raises(PlanError):attribute_stat_rows(result)


def test_zero_values_and_zero_window_are_kept_separate_from_missing():
    result=actual_pure_receipts();receipt=result['attribute_ledger']['receipts'][0]
    receipt['baseline']=receipt['value']=0;receipt['query']['frame']['origin']=0
    receipt['query']['frame'].pop('t_us',None)
    row=attribute_stat_rows(result)[2]
    assert row[4] is None and row[5]==0 and row[8:10]==[0,0]


def test_ratio_flat_and_raw_order_are_copied_without_double_application():
    result=actual_pure_receipts();result['attribute_ledger']['note']='原文四百八十字符分块'*100
    before=copy.deepcopy(result);rows=attribute_stat_rows(result)
    blocks=[r[1] for r in rows if r and str(r[0]).startswith('原文块 ')]
    assert len(blocks)>1 and json.loads(''.join(blocks))==result['attribute_ledger']
    assert result==before
    header=next(i for i,r in enumerate(rows) if r and r[0]=='总量回执')
    assert rows[header+1][7:10]==[.3,50,15]

@pytest.mark.parametrize('location,value',[('stat',[]),('raw_evaluated_value',[]),('application_base_value',10**400)])
def test_display_fields_are_controlled_scalar_or_finite_numbers(location,value):
    result=actual_pure_receipts()
    if location=='stat':result['attribute_ledger']['receipts'][0]['query']['stat']=value
    else:
        receipt=next(r for r in result['attribute_ledger']['receipts'] if any(c.get('application',{}).get('kind')=='recipient_base_fraction' for c in r.get('contributions',[])))
        component=next(c for c in receipt['contributions'] if c.get('application',{}).get('kind')=='recipient_base_fraction')
        component[location]=value
    with pytest.raises(PlanError):attribute_stat_rows(result)
