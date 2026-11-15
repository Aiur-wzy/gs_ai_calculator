import copy
import json

import pytest

from genshin_app.attribute_stat_reporting import attribute_stat_rows
from genshin_app.equipment.schema import PlanError
from genshin_engine.attribute_stat_graph import AttributeStatGraphLedger
from tests.test_attribute_stat_graph import expr, model, node, put, read


def actual_pure_receipts(*, missing_base=False):
    data = model(); put(data, node('pct', expr(factor=.002, max=.3), percent=True))
    if missing_base:
        del data['initials'][0]['base']
    service = AttributeStatGraphLedger(data); read(service); read(service, stat='em')
    return {'attribute_ledger': {'contract': 'attribute-ledger-v3', 'receipts': service.receipt_catalog(),
                                'qualification_scope': 'caller_supplied_frame'}}


def test_receipt_values_and_full_raw_without_recompute():
    result = actual_pure_receipts(); before = copy.deepcopy(result)
    rows = attribute_stat_rows(result)
    assert rows[1][2] == '属性'
    assert {'攻击力', '元素精通'} <= {r[2] for r in rows if len(r) == 11}
    header = next(i for i, r in enumerate(rows) if r and r[0] == '总量回执')
    pct = rows[header+1]
    assert pct[7:10] == [.3, 50, 15]
    assert pct[10] and pct[11] and pct[10] != pct[11]
    ids = {r['receipt_id'] for r in result['attribute_ledger']['receipts']}
    assert {pct[0], pct[10], pct[11]} <= ids
    assert json.loads(''.join(r[1] for r in rows if r and str(r[0]).startswith('原文块 '))) == result['attribute_ledger']
    assert result == before


def test_pending_base_does_not_invent_zero_or_multiply_anything():
    result = actual_pure_receipts(missing_base=True)
    rows = attribute_stat_rows(result)
    pending = [r for r in rows if len(r) == 11 and r[7] == '待核实']
    assert pending and all(r[9] is None for r in pending)
    header = next(i for i, r in enumerate(rows) if r and r[0] == '总量回执')
    assert rows[header+1][7:10] == [None, None, None]


@pytest.mark.parametrize('value', [True, -1, 10**400, '100'])
def test_time_failure_is_controlled(value):
    result = actual_pure_receipts(); result['attribute_ledger']['receipts'][0]['query']['frame']['t_us'] = value
    with pytest.raises(PlanError):
        attribute_stat_rows(result)


@pytest.mark.parametrize('contract', ['attribute-ledger-v1', 'attribute-ledger-v2', None])
def test_legacy_not_routed_through_new_layout(contract):
    assert attribute_stat_rows({'attribute_ledger': {'contract': contract}}) is None
