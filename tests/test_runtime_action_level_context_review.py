"""Independent source-material checks; no engine dispatch authority."""

# ruff: noqa: F811

import copy

import pytest

from genshin_db import runtime_action_facts as facts_module
from genshin_db import runtime_action_level as candidate
from genshin_db.runtime_catalog import RuntimeConfigError, fingerprint
from tests.test_pipeline import fake_repo, stub_fetcher  # noqa: F401
from tests.test_runtime_talent_level import level_db  # noqa: F401

# ruff: noqa: F811


def read(db):
    return candidate.get_runtime_action_level_context(db, character_id=90000001,
                                                     slot='skill', constellation=3)


@pytest.mark.parametrize('field', ['parameter_record_origin', 'source_sha256', 'references'])
def test_full_talent_parent_or_reference_change_is_rejected(level_db, monkeypatch, field):
    original = facts_module.get_runtime_action_facts

    def changed(*args, **kwargs):
        output = original(*args, **kwargs)
        row = next(t for t in output['talents'] if t['role'] == 'constellation')
        if field == 'parameter_record_origin':
            row[field]['json_pointer'] = '/changed-parent'
        elif field == 'source_sha256':
            row[field] = 'a' * 64
        else:
            row[field] = []
        return output

    monkeypatch.setattr(facts_module, 'get_runtime_action_facts', changed)
    with pytest.raises(RuntimeConfigError):
        read(level_db)


def test_all_public_material_and_parameter_projection_are_deeply_isolated(level_db):
    context = candidate.get_level_action_context(level_db, character_id=90000001,
                                                slot='skill', constellation=3)
    original = candidate.project_level_action_context(context)
    edited = copy.deepcopy(original)
    edited['target_material'][0]['raw'].clear()
    edited['footprints'][0]['references'].clear()
    edited['level_profile']['records']['talents'].clear()
    projected = candidate.project_level_action_parameter(context, 0)
    projected['scalar_projection']['value'] = -999
    assert candidate.project_level_action_context(context) == original
    assert candidate.project_level_action_parameter(context, 0)['scalar_projection']['value'] == 3.3
    assert original['source_authority'] == 'not_granted'
    assert original['action_dispatch'] == 'pending'
    check = copy.deepcopy(original)
    asserted = check.pop('content_fingerprint')
    assert fingerprint(check) == asserted


def test_nested_queries_borrow_the_same_live_connection(level_db, monkeypatch):
    from genshin_db import runtime_queries

    original = runtime_queries.borrow_read_connection
    connections = []

    def observed(*args, **kwargs):
        connection = original(*args, **kwargs)
        connections.append(connection.connection)
        return connection

    monkeypatch.setattr(runtime_queries, 'borrow_read_connection', observed)
    output = read(level_db)
    assert output['scope'] == 'same_read_source_material_only'
    assert len(connections) >= 3
    assert len({id(c) for c in connections}) == 1


@pytest.mark.parametrize('value', ['\ud800', {'value': '\udfff'}])
def test_unencodable_material_fails_as_structured_dataset_error(value):
    with pytest.raises(RuntimeConfigError) as failure:
        candidate._bounded(value)
    assert failure.value.reason == 'dataset_invalid'


def test_bounded_traversal_rejects_cycle_before_json_serialization(monkeypatch):
    cycle = {}
    cycle['same'] = cycle
    calls = []
    original = candidate.dumps

    def encoded(value):
        calls.append(value)
        return original(value)

    monkeypatch.setattr(candidate, 'dumps', encoded)
    with pytest.raises(RuntimeConfigError):
        candidate._bounded(cycle)
    assert not calls
