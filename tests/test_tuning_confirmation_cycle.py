"""Legacy tuning fingerprint compatibility without executable DB authority."""
import copy
import sys
from pathlib import Path

import pytest

from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.tuning import tuning_source_fingerprint

HERE = Path(__file__).resolve().parent
CANDIDATE = HERE.parent


def test_cold_imports_only_candidate():
    for name, module in list(sys.modules.items()):
        if name.startswith(('genshin_app', 'genshin_db', 'genshin_engine', 'genshin_runtime')) and getattr(module, '__file__', None):
            assert Path(module.__file__).resolve().is_relative_to(CANDIDATE / 'src'), (name, module.__file__)


def materials():
    record = {'id': 'one', 'status': 'ready', 'compiler': 'typed-mechanism-app-v1',
        'source_fingerprint': 'source', 'accepted_fingerprint': 'accepted', 'execution_fingerprint': 'full-input',
        'scope': {'axis': 1}, 'parent_graph': {'parents': [1]}, 'rules': [{'value': 1}], 'initial_rules': [1]}
    row = {'kind': 'mechanism', **{k: record[k] for k in ('id', 'source_fingerprint', 'accepted_fingerprint', 'execution_fingerprint')}}
    context = {'template': {'hits': [1]}}
    return [row, {'kind': 'other', 'nested': {'execution_fingerprint': 'keep'}}], [{'snapshot': 'one'}], [record], context


def test_no_typed_exact_legacy_identity():
    sources = [{'kind': 'ordinary', 'execution_fingerprint': 'semantic'}]
    original = fingerprint(sources)
    assert tuning_source_fingerprint(sources, [], [], None, original) == original


def test_missing_all_components_rejected_but_pending_group_keeps_legacy():
    sources, versions, records, context = materials()
    with pytest.raises(PlanError, match='缺少对应'):
        tuning_source_fingerprint(sources[1:], versions, records, context, 'legacy')
    records[0]['status'] = 'pending'
    assert tuning_source_fingerprint(sources[1:], versions, records, context, 'legacy') == 'legacy'


@pytest.mark.parametrize('field', ['source_fingerprint', 'accepted_fingerprint', 'execution_fingerprint', 'id', 'extra', 'duplicate', 'missing', 'pending'])
def test_exact_component_identity_and_one_to_one_correspondence(field):
    sources, versions, records, context = materials()
    if field == 'duplicate':
        sources.append(copy.deepcopy(sources[0]))
    elif field == 'missing':
        records.clear()
    elif field == 'pending':
        records[0]['status'] = 'pending'
    else:
        sources[0][field] = 'changed'
    with pytest.raises(PlanError):
        tuning_source_fingerprint(sources, versions, records, context, 'legacy')


@pytest.mark.parametrize('field', ['compiler', 'scope', 'parent_graph', 'rules', 'initial_rules', 'template', 'other', 'version', 'source', 'accepted'])
def test_each_compiled_or_source_dependency_remains_bound(field):
    sources, versions, records, context = materials()
    before = copy.deepcopy((sources, versions, records, context))
    expected = tuning_source_fingerprint(*before, 'legacy')
    assert (sources, versions, records, context) == before
    if field == 'compiler':
        records[0]['compiler'] = 'typed-mechanism-app-v2'
    elif field == 'template':
        context['template']['hits'] = [2]
    elif field == 'other':
        sources[1]['nested']['execution_fingerprint'] = 'changed'
    elif field == 'version':
        versions[0]['snapshot'] = 'changed'
    elif field in ('source', 'accepted'):
        key = 'source_fingerprint' if field == 'source' else 'accepted_fingerprint'
        sources[0][key] = records[0][key] = 'changed'
    else:
        records[0][field] = {'changed': True}
    assert tuning_source_fingerprint(sources, versions, records, context, 'legacy') != expected


def test_only_full_execution_audit_change_does_not_reconfirm():
    sources, versions, records, context = materials()
    original = tuning_source_fingerprint(sources, versions, records, context, 'legacy')
    sources[0]['execution_fingerprint'] = records[0]['execution_fingerprint'] = 'acknowledgement-only-audit-change'
    assert tuning_source_fingerprint(sources, versions, records, context, 'other-audit-source') == original
