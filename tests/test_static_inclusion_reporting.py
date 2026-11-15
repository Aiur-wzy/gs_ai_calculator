import copy
import json

from genshin_app import static_inclusion_reporting as reporting


def record():
    return {'id': 'source-a', 'binding_id': 'binding-a', 'provider_character_id': 'a', 'status': 'ready',
            'declaration': {'set_id': 10007, 'required_count': 2, 'numeric_application': False,
                            'properties': [{'stat': 'atk', 'value': .18, 'unit': 'ratio', 'source_index': 0}]},
            'source_display': {'name': '来源名称', 'description': '中文原文' * 400}}


def test_ratio_and_complete_evidence_survive_rows_without_numeric_application():
    result = {'static_inclusion_compilations': [record()]}
    before = copy.deepcopy(result)
    rows = reporting.static_inclusion_rows(result)
    header = next(i for i, row in enumerate(rows) if row and row[0] == '提供者')
    assert rows[header + 1][4:6] == [.18, '比例（1为100%）']
    start = rows.index(['完整静态来源记录（按序拼接JSON）']) + 1
    assert all(len(row[0]) <= 480 for row in rows[start:])
    assert json.loads(''.join(row[0] for row in rows[start:])) == result['static_inclusion_compilations']
    assert result == before


def test_same_property_different_providers_is_not_deduplicated():
    a, b = record(), record()
    b.update(id='source-b', binding_id='binding-b', provider_character_id='b')
    rows = reporting.static_inclusion_rows({'static_inclusion_compilations': [a, b]})
    assert len([row for row in rows if len(row) == 11 and row[0] in ('a', 'b')]) == 2
    assert reporting.static_inclusion_projection(a)['source_id'] != reporting.static_inclusion_projection(b)['source_id']


def test_projection_has_no_invented_event_time_or_damage():
    assert set(reporting.static_inclusion_projection(record())) == {'id', 'source_id', 'character', 'status', 'kind', 'name_zh'}
    assert reporting.static_inclusion_rows({}) == []


def test_partial_proof_removal_keeps_validation_required():
    assert not reporting.has_static_inclusion_signal({}, {}, plan={}, report={})
    assert reporting.has_static_inclusion_signal({'static_inclusion_adoption': {}}, {})
    assert reporting.has_static_inclusion_signal({}, {'static_inclusions': {}})
    requirement = {'resolved_by': {'kind': 'static_inclusion', 'id': 'old'}}
    assert reporting.has_static_inclusion_signal({}, {}, report={'effect_requirements': [requirement]})
    assert reporting.has_static_inclusion_signal({}, {}, plan={'resolution': {'effect_requirements': [requirement]}})
