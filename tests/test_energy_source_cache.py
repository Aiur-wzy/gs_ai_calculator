"""Exact request identity, same-transaction reuse and strict cache boundaries."""
from __future__ import annotations

import copy
import gzip
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from genshin_app.calculation_review import review_calculation
from genshin_app.equipment.delivery import validate_delivery_calculated
from genshin_app.equipment.service import result_bytes
from genshin_db import runtime_action_parameter as parameters
from genshin_db import runtime_energy_dispatch as dispatch
from genshin_db.equipment_queries import borrow_read_connection, equipment_read_session
from genshin_db.runtime_attack_metadata import get_attack_metadata_context
from genshin_db.runtime_catalog import RuntimeConfigError, fingerprint
from genshin_db.runtime_queries import query_runtime
from tests.test_pipeline import fake_repo  # noqa: F401
from tests.test_runtime_energy_dispatch import (  # noqa: F401
    identity,
    origin,
    trigger_db,
    trigger_repo,
)

# ruff: noqa: F811
HERE = Path(__file__).resolve().parent


def request(db):
    return {'parent_identity': identity(db), 'origin_reference_id': origin(db)}


def warm(db, ident, mode, snapshot=None):
    selection = ident['selection']
    args = {'action': 'attack_metadata_profile', 'character_id': selection['character_id'],
            'constellation': selection['constellation'], 'talent_level': selection['talent_level']}
    if mode == 'slot':
        args['slot'] = 'skill'
    else:
        args.update(skill_depot_id=selection['skill_depot_id'], skill_id=selection['skill_id'])
    if snapshot is not None:
        args['snapshot_id'] = snapshot
    return query_runtime(db, args)


@pytest.mark.parametrize('history', [[], ['slot'], ['explicit'], ['slot', 'explicit'], ['explicit', 'slot']])
def test_same_request_is_independent_of_public_query_history(trigger_db, history):
    args = request(trigger_db)
    original = copy.deepcopy(args)
    digest = hashlib.sha256(trigger_db.read_bytes()).hexdigest()
    with equipment_read_session(trigger_db):
        cold = dispatch.get_energy_dispatch_context(trigger_db, **args).public_projection
    with equipment_read_session(trigger_db):
        for mode in history:
            warm(trigger_db, args['parent_identity'], mode)
        first = dispatch.get_energy_dispatch_context(trigger_db, **args)
        expected = fingerprint(first.public_projection)
        assert expected == fingerprint(cold)
        first.parameter_context['facts']['records'].clear()
        first.public_projection['source_query']['origin']['operation']['raw'].clear()
        again = dispatch.get_energy_dispatch_context(trigger_db, **args)
        assert fingerprint(again.public_projection) == expected
        assert again.parameter_context['facts']['records']
    assert args == original
    assert hashlib.sha256(trigger_db.read_bytes()).hexdigest() == digest


@pytest.mark.parametrize('mode,expected_reads', [(None, 1), ('explicit', 1), ('slot', 2)])
def test_exact_prewarm_reuses_read_and_slot_needs_own_context(trigger_db, monkeypatch, mode, expected_reads):
    args = request(trigger_db)
    original = parameters.get_action_parameter_context
    calls, connections = [], []

    def counted(db, **selection):
        calls.append(copy.deepcopy(selection))
        borrowed = borrow_read_connection(db)
        assert borrowed is not None
        connections.append(id(borrowed.connection))
        return original(db, **selection)

    monkeypatch.setattr(parameters, 'get_action_parameter_context', counted)
    with equipment_read_session(trigger_db):
        if mode:
            warm(trigger_db, args['parent_identity'], mode)
        first = dispatch.get_energy_dispatch_context(trigger_db, **args)
        second = dispatch.get_energy_dispatch_context(trigger_db, **args)
    assert len(calls) == expected_reads
    assert len(set(connections)) == 1
    assert fingerprint(first.public_projection) == fingerprint(second.public_projection)


def test_no_parameter_cache_survives_read_session(trigger_db, monkeypatch):
    args = request(trigger_db)
    original, calls = parameters.get_action_parameter_context, []

    def counted(*a, **k):
        calls.append(copy.deepcopy(k))
        return original(*a, **k)

    monkeypatch.setattr(parameters, 'get_action_parameter_context', counted)
    responses = []
    for _ in range(2):
        with equipment_read_session(trigger_db):
            responses.append(dispatch.get_energy_dispatch_context(trigger_db, **args).public_projection)
    assert len(calls) == 2
    assert fingerprint(responses[0]) == fingerprint(responses[1])


def test_snapshot_omitted_and_explicit_are_equal_independent_cold_contexts(trigger_db):
    ident = request(trigger_db)['parent_identity']
    with equipment_read_session(trigger_db):
        omitted = get_attack_metadata_context(trigger_db, **ident['selection'])
    snapshot = omitted.parameter_context['facts']['snapshot']['snapshot_id']
    with equipment_read_session(trigger_db):
        explicit = get_attack_metadata_context(trigger_db, snapshot_id=snapshot, **ident['selection'])
    assert fingerprint(omitted.parameter_context) == fingerprint(explicit.parameter_context)
    assert fingerprint(omitted.public_projection) == fingerprint(explicit.public_projection)


def test_stale_snapshot_rejected_before_warm_cache_can_satisfy_request(trigger_db, monkeypatch):
    args = request(trigger_db)
    original, calls = parameters.get_action_parameter_context, []

    def counted(*a, **k):
        calls.append(copy.deepcopy(k))
        return original(*a, **k)

    monkeypatch.setattr(parameters, 'get_action_parameter_context', counted)
    with equipment_read_session(trigger_db):
        good = dispatch.get_energy_dispatch_context(trigger_db, **args)
        size = len(calls)
        with pytest.raises(RuntimeConfigError):
            get_attack_metadata_context(trigger_db, snapshot_id='f' * 64, **args['parent_identity']['selection'])
        stale = copy.deepcopy(args)
        stale['parent_identity']['snapshot_id'] = 'f' * 64
        with pytest.raises(RuntimeConfigError):
            dispatch.get_energy_dispatch_context(trigger_db, **stale)
        assert len(calls) == size
        assert fingerprint(dispatch.get_energy_dispatch_context(trigger_db, **args).public_projection) == fingerprint(good.public_projection)


def sample_identity():
    return {'selection': {'character_id': 10000047, 'skill_depot_id': 4701, 'skill_id': 10472,
                          'constellation': 0, 'talent_level': 10}, 'version_line': 'release'}


def context_for(ident, state):
    return {'facts': {'snapshot': state, 'selection': {**ident['selection'], 'slot': None},
                      'level': {'effective_level': ident['selection']['talent_level']}}, 'descriptions': {'test': []}}


def install_cache(monkeypatch, cache):
    from genshin_db import equipment_queries

    monkeypatch.setattr(equipment_queries, 'borrow_read_cache', lambda db, namespace: cache)


@pytest.mark.parametrize('field', ['character_id', 'skill_depot_id', 'skill_id', 'constellation', 'talent_level', 'version_line', 'snapshot_id'])
def test_each_full_request_identity_component_requires_its_own_read(monkeypatch, field):
    identity, state, cache, calls = sample_identity(), {'snapshot_id': 'a' * 64}, {}, []
    install_cache(monkeypatch, cache)

    def fresh(db, **args):
        calls.append(copy.deepcopy(args))
        return {'request': args, 'facts': context_for(identity, state)['facts']}

    monkeypatch.setattr(parameters, 'get_action_parameter_context', fresh)
    dispatch._parameters(None, identity, state)
    altered, changed_state = copy.deepcopy(identity), copy.deepcopy(state)
    if field == 'version_line':
        altered[field] = 'beta'
    elif field == 'snapshot_id':
        changed_state[field] = 'b' * 64
    else:
        altered['selection'][field] += 1
    result = dispatch._parameters(None, altered, changed_state)
    assert len(calls) == 2 and len(cache) == 2
    assert result['request'] == calls[-1]


@pytest.mark.parametrize('initial_size', [31, 32])
def test_parameter_cache_bound_precedes_read_and_insertion(monkeypatch, initial_size):
    identity, state = sample_identity(), {'snapshot_id': 'a' * 64}
    old = context_for(identity, state)
    # The existing bug used these matching response facts to invent an alias,
    # regardless of their distinct request keys, even at the full 32-entry cap.
    cache = {('parameters', f'{i:064x}'): copy.deepcopy(old) for i in range(initial_size)}
    install_cache(monkeypatch, cache)
    calls = []

    def fresh(db, **args):
        calls.append(args)
        return copy.deepcopy(old)

    monkeypatch.setattr(parameters, 'get_action_parameter_context', fresh)
    if initial_size == 32:
        before = copy.deepcopy(cache)
        with pytest.raises(RuntimeConfigError) as caught:
            dispatch._parameters(None, identity, state)
        assert caught.value.reason == 'response_too_large'
        assert not calls and cache == before
    else:
        first = dispatch._parameters(None, identity, state)
        assert len(calls) == 1 and len(cache) == 32
        first['facts']['selection']['slot'] = 'mutated'
        again = dispatch._parameters(None, identity, state)
        assert len(calls) == 1 and len(cache) == 32
        assert again['facts']['selection']['slot'] is None
        changed = copy.deepcopy(identity)
        changed['selection']['skill_id'] += 1
        with pytest.raises(RuntimeConfigError):
            dispatch._parameters(None, changed, state)
        assert len(calls) == 1 and len(cache) == 32


@pytest.mark.parametrize('history', ['cold', 'slot'])
def test_old_complete_energy_packages_stay_valid_offline(monkeypatch, history):
    directory = HERE / 'fixtures/energy_cache'
    manifest = json.loads((directory / 'manifest.json').read_text('utf8'))
    row, = [r for r in manifest['cases'] if r['history'] == history]
    path = directory / (row['package']['path'] + '.gz')
    raw = gzip.decompress(path.read_bytes())
    assert hashlib.sha256(raw).hexdigest() == row['package']['sha256']
    package = json.loads(raw)

    def no_db(*args, **kwargs):
        pytest.fail('Historical validation/export must stay offline')

    monkeypatch.setattr(sqlite3, 'connect', no_db)
    calculated = package['calculated']
    validate_delivery_calculated(calculated)
    review = review_calculation(package, {'view': 'summary'})
    assert review['validation'] == 'full_calculation_package'
    assert calculated['result']['energy_generation_summary']['credited_particles'] == 9
    assert calculated['result']['energy_generation_compilations'][0]['source_bundle']['selection']['slot'] == (None if history == 'cold' else 'skill')
    assert json.loads(result_bytes(calculated['result'], calculated['result_receipt'], 'json'))['result'] == calculated['result']
    assert hashlib.sha256(gzip.decompress(path.read_bytes())).hexdigest() == row['package']['sha256']
