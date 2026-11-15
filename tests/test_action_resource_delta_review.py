"""Independent pure-capacity checks. No fixture grants production source authority."""
import copy

import pytest

from genshin_db.runtime_capacity_delta import (
    CapacityError,
    project_source_delta,
    source_fact_claim,
)
from tests.action_resource_fixtures import (
    action_resource_synthetic_db_only,  # noqa: F401
    load_material,
)

pytestmark = pytest.mark.usefixtures("action_resource_synthetic_db_only")


def material(index=0):
    inv = load_material("action-capacity__real-source-inventory.json")
    row = inv['candidates'][index]
    dec = row['declarations'][0]
    proposal = load_material("capacity-fact-review__fresh-source-proposals.json")
    facts = [p['fact'] for p in proposal['proposals']]
    fact = copy.deepcopy(next(f for f in facts if f['claim']['operation_pointer'] == row['operation_pointer']
                              and f['claim']['source_path'] == row['source_path']))
    return inv, row, dec, fact


@pytest.mark.parametrize('index', range(11))
def test_all_fresh_materials_keep_pending_then_only_source_delta(index):
    inv, row, dec, fact = material(index)
    rank = dec['constellation_rank']
    pending = project_source_delta(inv, row, dec, fact, constellation=rank)
    assert pending['status'] == 'pending' and pending['amount'] is None
    # This flag is an engineering fixture only. Fresh DB approval is still required.
    fact['status'] = 'reviewed'
    active = project_source_delta(inv, row, dec, fact, constellation=rank)
    inactive = project_source_delta(inv, row, dec, fact, constellation=rank - 1)
    assert active['status'] == 'known_delta' and type(active['amount']) is int and active['amount'] == 1
    assert inactive['status'] == 'inactive' and inactive['amount'] == 0
    for value in (pending, active, inactive):
        assert value['capacity'] is None
        assert value['action_dispatch'] == 'pending'
        assert value['requires_fresh_db_admission'] is True


def test_same_amount_other_skill_fact_cannot_authorize_selected_source():
    inv, row, dec, _ = material(0)
    other = material(1)[3]
    other['status'] = 'reviewed'
    value = project_source_delta(inv, row, dec, other, constellation=dec['constellation_rank'])
    assert value['status'] == 'pending' and value['amount'] is None


def test_revoking_fact_after_previous_projection_prevents_reuse():
    inv, row, dec, fact = material()
    fact['status'] = 'reviewed'
    assert project_source_delta(inv, row, dec, fact, constellation=6)['amount'] == 1
    fact['status'] = 'revoked'
    value = project_source_delta(inv, row, dec, fact, constellation=6)
    assert value['status'] == 'pending' and value['amount'] is None


@pytest.mark.parametrize('field,value', [('json_pointer', 1), ('operation_pointer', None),
                                        ('json_pointer', '/bad~escape'), ('operation_pointer', '/x/01')])
def test_malformed_pointer_returns_domain_error(field, value):
    inv, row, dec, _ = material()
    row[field] = value
    with pytest.raises(CapacityError):
        source_fact_claim(inv, row, dec)
