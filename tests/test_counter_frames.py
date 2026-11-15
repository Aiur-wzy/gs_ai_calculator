"""Pure candidate contract; all supplied counter states are synthetic facts."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.resource_counter_frames import FrameError, FrameStore
from tests.test_mechanics_execution import rule
from tests.test_resource_counter_views import setup


def fixture(*, raw=300, layers=1, factor=.025, benefit='benefit.dmg_bonus.all', limits=None):
    views, program, state = setup(raw=raw)
    views['schema_version'] = 2
    declaration = rule('gain', domain='self', benefit=benefit,
                       value={'kind': 'counter_view', 'view_id': 'read', 'sample': 'self_frame', 'factor': factor})
    contract = {'rules': [declaration], 'consumers': [{'reference_id': 'cast-output', 'character': 'a',
        'damage_system': 'classic', 'hit_id': 'later'}], 'captures': [], 'matches': []}
    snapshot = {'time': 100000, 'frontline': 'a', 'state': {'gain': {'cooldown': 0, 'instances': [{
        'instance_id': 'A', 'beneficiaries': ['a'], 'expires': [900000]*layers, 'quota': {}}]}}}
    context = {'origin': 0, 'run_id': '0:cast-output', 'event_id': 'cast-output', 'time_us': 100000}
    members = [{'rule_id': 'gain', 'effect_id': 'value', 'instance_id': 'A', 'recipient': 'a'}]
    return FrameStore(views, program, contract, limits=limits), views, contract, state, snapshot, context, members


def capture(data, reserve=None):
    store, _, contract, state, snapshot, context, members = data
    prepared = store.prepare(context, snapshot, state, members, current_contract=contract)
    return store.commit(prepared, reserve or (lambda _: None))


def read(data, *, snapshot=None, layers=None, reserve=None, damage_system='classic'):
    store, _, contract, _, original, _, members = data
    inst = (snapshot or original)['state']['gain']['instances'][0]
    return store.read(snapshot or original, members[0], {'run_id': '0:later', 'event_id': 'later',
        'origin': 0, 'time_us': 500000, 'event': 'hit', 'damage_system': damage_system},
        layers=len(inst['expires']) if layers is None else layers, current_contract=contract,
        reserve=reserve or (lambda _: None))


def test_capture_keeps_bounded_value_and_raw_history_after_caller_reset():
    data = fixture(raw=450)
    capture(data)
    data[3]['values']['count']['total'] = 0
    result = read(data)
    assert result['reading']['raw_atoms'] == 450
    assert result['reading']['numerator'] == 400
    assert result['value'] == 1 and result['combined_value'] == 1
    assert result['authority'] == 'candidate_only'


@pytest.mark.parametrize('old_layers,current_layers', [(2, 1), (1, 2)])
def test_layers_are_frozen_not_current_rule_layers(old_layers, current_layers):
    data = fixture(layers=old_layers)
    capture(data)
    assert read(data)['combined_value'] == .75*old_layers
    with pytest.raises(FrameError):
        read(data, layers=current_layers)


@pytest.mark.parametrize('mutation', ['copied', 'changed_front', 'changed_layer'])
def test_only_actual_unchanged_snapshot_object_is_accepted(mutation):
    data = fixture()
    capture(data)
    snapshot = data[4]
    if mutation == 'copied':
        snapshot = copy.deepcopy(snapshot)
    elif mutation == 'changed_front':
        snapshot['frontline'] = 'b'
    else:
        snapshot['state']['gain']['instances'][0]['expires'][0] += 1
    with pytest.raises(FrameError):
        read(data, snapshot=snapshot)


@pytest.mark.parametrize('field,value', [('owner', 'b'), ('source_id', 'new-source')])
def test_current_complete_reader_contract_cannot_swap_source(field, value):
    data = fixture()
    capture(data)
    data[2]['rules'][0][field] = value
    with pytest.raises(FrameError):
        read(data)


def test_factor_change_same_rule_id_cannot_reuse_frame():
    data = fixture()
    capture(data)
    data[2]['rules'][0]['effects'][0]['value']['factor'] = 999
    with pytest.raises(FrameError):
        read(data)


def test_capture_does_not_reserve_until_all_members_are_valid():
    data = fixture()
    data[-1].append({**data[-1][0], 'effect_id': 'absent'})
    called = []
    with pytest.raises(FrameError):
        capture(data, called.append)
    assert called == [] and data[0].usage()['frames'] == 0


def test_capture_budget_failure_preserves_state_and_allows_same_unclaimed_identity():
    data = fixture()
    before = copy.deepcopy(data[3:])
    def reject(_):
        raise FrameError('budget')
    with pytest.raises(FrameError):
        capture(data, reject)
    assert data[0].usage()['frames'] == 0 and data[3:] == before
    called = []
    capture(data, called.append)
    assert called == [3] and data[0].usage()['frames'] == 1


@pytest.mark.parametrize('limits', [{'frames': 0}, {'members': 0}, {'frame_bytes': 1}])
def test_limits_reject_before_any_reserve(limits):
    data = fixture(limits=limits)
    called = []
    with pytest.raises(FrameError):
        capture(data, called.append)
    assert called == [] and data[0].usage()['frames'] == 0


def test_empty_demand_has_no_capture_charge_or_frame():
    data = fixture()
    data[-1].clear()
    data[4]['state']['gain']['instances'].clear()
    called = []
    assert capture(data, called.append) is None
    assert called == [] and data[0].usage()['frames'] == 0


def test_same_issued_candidate_cannot_be_claimed_twice_or_by_another_store():
    data = fixture()
    store, _, contract, state, snapshot, context, members = data
    candidate = store.prepare(context, snapshot, state, members, current_contract=contract)
    with pytest.raises(FrameError):
        fixture()[0].commit(candidate, lambda _: None)
    store.commit(candidate, lambda _: None)
    with pytest.raises(FrameError):
        store.commit(candidate, lambda _: None)


def test_snapshot_mutation_after_prepare_does_not_publish_or_charge():
    data = fixture()
    store, _, contract, state, snapshot, context, members = data
    candidate = store.prepare(context, snapshot, state, members, current_contract=contract)
    snapshot['state']['gain']['instances'][0]['expires'].append(900000)
    called = []
    with pytest.raises(FrameError):
        store.commit(candidate, called.append)
    assert called == [] and store.usage()['frames'] == 0


@pytest.mark.parametrize('damage_system', ['transformative', 'lunar_charged'])
def test_nonclassic_read_is_rejected_before_charge(damage_system):
    data = fixture()
    capture(data)
    called = []
    with pytest.raises(FrameError):
        read(data, damage_system=damage_system, reserve=called.append)
    assert called == []


@pytest.mark.parametrize('benefit,factor', [('benefit.dmg_bonus.all', 1e308), ('benefit.base_mult.all', 1e200)])
def test_layer_overflow_never_publishes_read(benefit, factor):
    data = fixture(raw=10, layers=2, factor=factor, benefit=benefit)
    capture(data)
    called = []
    with pytest.raises(FrameError):
        read(data, reserve=called.append)
    assert called == [] and data[0].usage()['reads'] == 0


def test_report_is_detached_and_not_reimportable_authority():
    data = fixture()
    receipt = capture(data)
    receipt['frame_id'] = 'forged'
    result = read(data)
    result['reading']['raw_atoms'] = 9
    assert read(data)['reading']['raw_atoms'] == 300
    assert data[0].usage()['reads'] == 1


def test_semantic_signature_keeps_old_values_but_ignores_absolute_run_uid():
    a, b, c = fixture(raw=300), fixture(raw=300), fixture(raw=400)
    b[5]['run_id'] = '0:different-absolute-id'
    for data in (a, b, c):
        capture(data)
    assert a[0].signature(a[4]) == b[0].signature(b[4])
    assert a[0].signature(a[4]) != c[0].signature(c[4])


def test_definition_mutation_after_construction_does_not_alter_private_view():
    data = fixture()
    data[1]['views'][0]['transform']['max_atoms'] = 1
    capture(data)
    assert read(data)['reading']['numerator'] == 300


def test_instance_uid_rename_is_not_part_of_math_boundary_signature():
    a, b = fixture(), fixture()
    b[4]['state']['gain']['instances'][0]['instance_id'] = 'unrelated-runtime-uid'
    b[-1][0]['instance_id'] = 'unrelated-runtime-uid'
    capture(a)
    capture(b)
    assert a[0].signature(a[4]) == b[0].signature(b[4])


def test_new_snapshot_with_same_reference_cannot_replace_old_capture():
    data = fixture()
    capture(data)
    store, _, contract, state, snapshot, context, members = data
    with pytest.raises(FrameError):
        store.prepare(context, copy.deepcopy(snapshot), state, members, current_contract=contract)
    assert store.usage()['frames'] == 1


def test_conservative_demand_keeps_static_mismatch_with_legal_include():
    views, program, state = setup(raw=300)
    views['schema_version'] = 2
    definition = rule('gain', domain='self', value={
        'kind': 'counter_view', 'view_id': 'read', 'sample': 'self_frame', 'factor': .025})
    definition['effects'][0]['filter'] = {'event': 'hit', 'damage_type_ids': ['damage.charged_attack']}
    contract = {'rules': [definition], 'consumers': [{'reference_id': 'cast-output', 'character': 'a',
        'damage_system': 'classic', 'hit_id': 'later', 'damage_type_ids': ['damage.elemental_skill']}],
        'captures': [], 'matches': [{'rule_id': 'gain', 'effect_id': 'value', 'hit_id': 'later', 'operation': 'include'}]}
    base = fixture()
    store = FrameStore(views, program, contract)
    assert store.demand(base[5], base[4]) == base[-1]
    token = store.prepare(base[5], base[4], state, base[-1], current_contract=contract)
    store.commit(token, lambda _: None)


def test_demand_cannot_omit_an_active_member():
    data = fixture()
    data[-1].clear()
    with pytest.raises(FrameError):
        capture(data)


def test_read_budget_failure_does_not_publish_and_can_retry_same_actual_read():
    data = fixture()
    capture(data)
    before = data[0].usage()
    def reject(_):
        raise FrameError('budget')
    with pytest.raises(FrameError):
        read(data, reserve=reject)
    assert data[0].usage() == before
    assert read(data)['value'] == .75


def test_reference_receipt_id_is_not_an_input_token():
    data = fixture()
    receipt = capture(data)
    with pytest.raises(FrameError):
        data[0].commit(receipt, lambda _: None)


@pytest.mark.parametrize('changed', [{'origin': True}, {'time_us': True}, {'run_id': ''}])
def test_strict_occurrence_facts_are_not_coerced(changed):
    data = fixture()
    data[5].update(changed)
    with pytest.raises(FrameError):
        capture(data)


def test_no_data_copy_or_serialization_after_successful_capture_reserve(monkeypatch):
    from genshin_engine import resource_counter_frames as counter_frames
    data = fixture()
    def forbidden(*_a, **_kw):
        raise AssertionError('after-reserve data work')
    def reserve(_):
        monkeypatch.setattr(counter_frames, 'canonical', forbidden)
        monkeypatch.setattr(counter_frames.copy, 'deepcopy', forbidden)
    result = capture(data, reserve)
    assert result['member_count'] == 1 and data[0].usage()['frames'] == 1


def test_no_data_copy_or_serialization_after_successful_read_reserve(monkeypatch):
    from genshin_engine import resource_counter_frames as counter_frames
    data = fixture()
    capture(data)
    def forbidden(*_a, **_kw):
        raise AssertionError('after-reserve data work')
    def reserve(_):
        monkeypatch.setattr(counter_frames, 'canonical', forbidden)
        monkeypatch.setattr(counter_frames.copy, 'deepcopy', forbidden)
    result = read(data, reserve=reserve)
    assert result['value'] == .75 and data[0].usage()['reads'] == 1


def two_member_fixture(*, distinct=False, limits=None):
    data = fixture()
    views, program, state = setup(raw=300)
    views['schema_version'] = 2
    contract = copy.deepcopy(data[2])
    effect = copy.deepcopy(contract['rules'][0]['effects'][0])
    effect['id'] = 'other-effect'
    if distinct:
        views['views'].append({**copy.deepcopy(views['views'][0]), 'id': 'second-read'})
        effect['value']['view_id'] = 'second-read'
    contract['rules'][0]['effects'].append(effect)
    members = [*data[-1], {**data[-1][0], 'effect_id': effect['id']}]
    return FrameStore(views, program, contract, limits=limits), views, contract, state, data[4], data[5], members


def test_same_view_two_members_reads_once_but_accounts_both_members():
    data = two_member_fixture()
    called = []
    capture(data, called.append)
    assert called == [4]  # 1 frame + 1 unique reading + 2 bound effects.
    assert data[0].usage()['members'] == 2


def test_two_views_are_one_capture_reservation_and_do_not_publish_partial_frame():
    data = two_member_fixture(distinct=True)
    before = copy.deepcopy(data[3:])
    called = []
    def reject(work):
        called.append(work)
        raise FrameError('joint failure')
    with pytest.raises(FrameError):
        capture(data, reject)
    assert called == [5] and data[0].usage()['frames'] == data[0].usage()['members'] == 0
    assert data[3:] == before


def test_member_limit_is_joint_for_both_views():
    data = two_member_fixture(distinct=True, limits={'members': 1})
    called = []
    with pytest.raises(FrameError):
        capture(data, called.append)
    assert called == [] and data[0].usage()['members'] == 0


def test_exact_frame_byte_limit_accepts_and_one_less_rejects():
    first = fixture()
    capture(first)
    used = first[0].usage()['frame_bytes']
    exact, short = fixture(limits={'frame_bytes': used}), fixture(limits={'frame_bytes': used-1})
    capture(exact)
    with pytest.raises(FrameError):
        capture(short)
    assert exact[0].usage()['frames'] == 1 and short[0].usage()['frames'] == 0
