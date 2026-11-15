"""Fresh static-source candidates and final, non-numeric App admission.

Only this in-process assembler grants fresh-database authority. The pure shape
compiler and caller-supplied serialized records never create an admitted batch.
"""
from __future__ import annotations

import copy
import json
import sqlite3

from genshin_db import equipment_queries, runtime_queries
from genshin_db.runtime_catalog import dumps

from .files import context_view, fingerprint, input_view
from .schema import PlanError
from .static_inclusion import (
    PROFILE,
    PROFILE_FINGERPRINT,
    compile_static_inclusion,
    select_static_inclusion_profile,
    static_inclusion_profile_capabilities,
)

COMPILER = 'static-inclusion-app-v1'
MAX_CANDIDATES = 32
MAX_MATERIAL_BYTES = 8 * 1024 * 1024
MAX_GRAPH_BYTES = 16 * 1024 * 1024
MAX_RECORD_BYTES = MAX_MATERIAL_BYTES + MAX_GRAPH_BYTES
PARENT_PATH = 'ExcelBinOutput/ReliquarySetExcelConfigData.json'


def static_inclusion_capabilities():
    return {**static_inclusion_profile_capabilities(), 'compiler': COMPILER, 'profile': copy.deepcopy(PROFILE), 'profile_fingerprint': PROFILE_FINGERPRINT,
        'input': '实际套装达到阈值，panel/static_included给定面板且无对应显式binding时自动检查；不写入原生规则',
        'source_authority': '每次装配同一只读数据库事务重查完整原始父关系、affix与规范条目；不接收调用者证书准入',
        'numeric_application': False, 'basis_authority': 'user_panel_policy', 'nonextra_authority': 'not_inferred',
        'explicit_binding': '同provider/owner的pending、excluded、自定义或resolved决定完整保留',
        'result_field': 'static_inclusion_compilations', 'report_field': 'static_inclusion_compilations',
        'query': {'view': 'static_inclusions', 'filters': ['character', 'source_id']},
        'exports': ['面板已含来源（只读）', '修改配装、面板或政策后须回App重算'],
        'limits': {'max_candidates': MAX_CANDIDATES, 'material_bytes_per_compose': MAX_MATERIAL_BYTES}}


def finite_size(value, limit):
    size = 0
    for chunk in json.JSONEncoder(ensure_ascii=False, allow_nan=False, sort_keys=True).iterencode(value):
        size += len(chunk.encode('utf8'))
        if size > limit:
            raise ValueError('静态来源资料超过本次装配预算')
    return size


def _bounded(value, limit):
    try:
        return finite_size(value, limit)
    except (ValueError, TypeError, RecursionError, OverflowError) as exc:
        raise PlanError('静态来源结构或累计预算无效：' + str(exc)[:1024], 'static_inclusion_budget') from exc


def graph_view(template, model, matches=()):
    # Display assumptions and equipment_resolution are appended after finalization.
    return {'template': {k: v for k, v in template.items() if k != 'meta'},
            'mechanics_model': model, 'mechanics_matches': list(matches)}


def panel_fingerprint(plan, actor):
    matches = [c for c in plan['manual_template']['characters'] if c['id'] == actor]
    if len(matches) != 1:
        raise ValueError('静态来源提供者没有唯一角色面板')
    return fingerprint({'actor': actor, 'stats': matches[0]['stats'],
                        'equipment': plan['equipment_inputs'][actor]})


def _issue_key(row):
    return (row.get('code'), row.get('path'), row.get('binding_id'), row.get('part_id'))


def _expected_issue(binding_id):
    return ('effect_pending', 'effect_bindings.' + binding_id, binding_id, None)


def _candidate(plan, req):
    actor, owner = req['provider_character_id'], req['owner']
    gear = plan['equipment_inputs'].get(actor) or {}
    if owner.get('kind') != 'artifact_set' or gear.get('mode') != 'panel' or gear.get('panel_policy') != 'static_included':
        return False
    if any(b['provider_character_id'] == actor and b['owner'] == owner for b in plan['effect_bindings']):
        return False
    effect = req.get('effect') or {}
    # A cheap candidate screen is not source approval. Unknown raw fields are
    # still inspected by S1 after fresh assembly; dynamic/empty rows stay legacy.
    return (effect.get('open_config') is None and type(effect.get('add_props')) is list
            and any(type(p) is dict and p for p in effect['add_props'])
            and type(effect.get('param_list')) is list and len(effect['param_list']) == 8
            and all(type(v) in (int, float) and v == 0 for v in effect['param_list']))


def _read_material(plan, req, db_path):
    """One exact read transaction; there is no public material-provider override."""
    actor, owner = req['provider_character_id'], req['owner']
    gear = plan['equipment_inputs'][actor]
    version = gear.get('data_version') or {}
    line = version.get('version_line', 'release')
    conn = equipment_queries.borrow_read_connection(db_path)
    if conn is None:
        raise ValueError('静态来源需要同一只读数据库事务')
    definitions = conn.execute(
        'SELECT definition_id FROM runtime_definition WHERE version_line=? AND source_path=? AND exact_key_json=?',
        (line, PARENT_PATH, dumps({'setId': owner['set_id']}))).fetchall()
    if len(definitions) != 1:
        raise ValueError('实际套装原始父关系不存在或不唯一')
    parent = runtime_queries.get_runtime_definition(db_path, definitions[0][0], version_line=line,
        sections=('summary', 'references', 'payload'))
    source = runtime_queries.assemble_runtime_source(db_path, owner['affix_id'], line,
        snapshot_id=parent['snapshot']['snapshot_id'])
    detail = equipment_queries.get_artifact_set_detail(db_path, owner['set_id'], line)
    relations = [r for r in detail['effects'] if r['required_count'] == owner['required_count']
                 and (r.get('effect') or {}).get('affix_id') == owner['affix_id']]
    if len(relations) != 1 or fingerprint(relations[0]['effect']) != fingerprint(req['effect']):
        raise ValueError('实际阈值与原文规范条目不存在唯一完整关联')
    if detail['data_version'] != version:
        raise ValueError('静态来源与当前选择的数据库版本不同')
    selected = [r for r in gear['artifact_sets'] if r['set_id'] == owner['set_id']]
    if len(selected) != 1:
        raise ValueError('静态来源的实际套装选择不存在或不唯一')
    relation = relations[0]
    return {'contract': 'static-inclusion-material-v1', 'parent': parent, 'source': source,
            'relation': {k: relation[k] for k in ('source_index', 'required_count', 'relation_status')},
            'normalized': relation['effect'],
            'selection': {'kind': 'artifact_set', 'provider': actor, 'set_id': owner['set_id'],
                          'count': selected[0]['count'], 'required_count': owner['required_count'],
                          'mode': gear['mode'], 'panel_policy': gear['panel_policy'],
                          'panel_fingerprint': panel_fingerprint(plan, actor), 'data_version': version}}


class _Batch:
    __slots__ = ('_components', '_context', '_entries', '_seal')

    def __init__(self, entries, context):
        self._entries = copy.deepcopy(entries)
        self._context = context
        self._components = [copy.deepcopy(r['source_component']) for r in entries]
        self._seal = fingerprint([self._entries, self._context, self._components])

    @property
    def source_components(self):
        return copy.deepcopy(self._components)


def prepare_static_inclusions(plan, requirements, issues, db_path, *, context_plan=None):
    native = context_plan or plan
    context = fingerprint(context_view(input_view(native)))
    candidates = [r for r in requirements if _candidate(native, r)]
    entries, used = [], 0
    if not candidates:
        return _Batch([], context)
    if len(candidates) > MAX_CANDIDATES:
        raise PlanError('静态来源候选超过单次装配上限', 'static_inclusion_budget')
    with equipment_queries.equipment_read_session(db_path):
        for req in candidates:
            actor, owner, bid = req['provider_character_id'], req['owner'], req['binding_id']
            rid = 'static.' + fingerprint([actor, owner])[:24]
            record = {'id': rid, 'compiler': COMPILER, 'binding_id': bid, 'provider_character_id': actor,
                      'owner': copy.deepcopy(owner), 'status': 'pending', 'source_authority': 'not_granted',
                      'context_fingerprint': context, 'source_material': None, 'shape_certificate': None,
                      'declaration': None, 'source_display': None, 'issues': [],
                      'requirement_fingerprint': fingerprint(req),
                      'issue_fingerprints': [fingerprint(i) for i in issues if _issue_key(i) == _expected_issue(bid)]}
            try:
                if used >= MAX_MATERIAL_BYTES:
                    raise ValueError('静态来源资料已耗尽本次装配预算')
                material = _read_material(native, req, db_path)
                try:
                    used += finite_size(material, MAX_MATERIAL_BYTES - used)
                except ValueError:
                    # A failed response does not reset the shared material budget.
                    used = MAX_MATERIAL_BYTES
                    raise
                record['source_material'] = material
                selection = select_static_inclusion_profile(material['parent']['snapshot'])
                if selection['status'] != 'selected':
                    raise ValueError('当前快照没有唯一已审核的静态来源合同：' + selection['issues'][0]['code'])
                shape = compile_static_inclusion(material, **{key: selection[key] for key in
                    ('profile_id', 'profile_version', 'profile_fingerprint')})
                record['shape_certificate'] = shape
                normal = material['normalized']
                record['source_display'] = {'name': normal.get('name'), 'description': normal.get('desc_template'),
                    'name_source': copy.deepcopy(normal.get('name_source')),
                    'description_source': copy.deepcopy(normal.get('desc_source'))}
                if shape['status'] != 'shape_ready':
                    raise ValueError('原始静态来源结构尚不支持：' + shape['issues'][0]['code'])
                record['declaration'] = {**copy.deepcopy(shape['declaration']), 'basis_authority': 'user_panel_policy'}
            except (ValueError, KeyError, TypeError, OSError, sqlite3.Error, OverflowError, RecursionError) as exc:
                record['issues'].append({'code': 'static_inclusion_pending', 'message': str(exc)[:1024]})
            material, shape = record['source_material'], record['shape_certificate']
            component = {'kind': 'static_inclusion_candidate', 'id': rid, 'binding_id': bid,
                'provider_character_id': actor, 'owner': copy.deepcopy(owner), 'context_fingerprint': context,
                'profile_fingerprint': shape['profile_fingerprint'] if shape is not None else None,
                'panel_fingerprint': panel_fingerprint(native, actor),
                'requirement_fingerprint': record['requirement_fingerprint'],
                'material_fingerprint': fingerprint(material) if material is not None else None,
                'shape_fingerprint': fingerprint(shape) if shape is not None else None,
                'shape_status': shape['status'] if shape is not None else 'unavailable',
                'authority': 'fresh_material_candidate_not_admitted'}
            record['source_component'] = component
            entries.append(record)
    _bounded(entries, MAX_MATERIAL_BYTES + 1024 * 1024)
    return _Batch(entries, context)


def _ref_affix(ref):
    """Explicit typed identity only; numbers in prose and source_id prefixes are not IDs."""
    if not isinstance(ref, dict):
        return None
    if ref.get('table') == 'EquipAffixExcelConfigData' and isinstance(ref.get('key'), dict):
        value = ref['key'].get('affixId')
    elif ref.get('kind') in ('equip_affix', 'equipment_affix'):
        value = ref.get('affix_id')
    elif ref.get('kind') == 'database' and isinstance(ref.get('owner'), dict):
        return _owner_affix(ref['owner'])
    else:
        return None
    return value if type(value) is int and value > 0 else None


def _owner_affix(owner):
    if owner.get('kind') in ('artifact_set', 'weapon'):
        value = owner.get('affix_id')
        return value if type(value) is int and value > 0 else None
    return None


def _binding_claims(binding):
    values = {_owner_affix(binding['owner'])}
    values.update(_ref_affix(r) for r in binding.get('source_refs', []))
    return values - {None}


def _inventory(plan, template, model, contribution_report, applicable, automatic):
    """Known source occupancy, including pending claims; never infers untyped prose."""
    claims, unresolved = [], []
    applicable_keys = {(b['binding_id'], p['part_id']) for b, p in applicable}
    source_parts = {}
    for binding in plan['effect_bindings']:
        affixes = _binding_claims(binding)
        actor, bid = binding['provider_character_id'], binding['binding_id']
        parts = binding['parts']
        state = ('excluded' if parts and all(p['state'] == 'excluded' for p in parts) else
                 'active' if any((bid, p['part_id']) in applicable_keys for p in parts) else 'pending')
        for affix in sorted(affixes):
            claims.append({'provider': actor, 'affix_id': affix, 'state': state, 'path': 'effect_bindings.' + bid})
        for part in parts:
            source_parts[bid + ':' + part['part_id']] = (actor, affixes)
    for collection, rows in (('buffs', template.get('buffs', [])),
                             ('rules', (model or {}).get('rules', [])),
                             ('fixed_effect_sources', (model or {}).get('fixed_effect_sources', []))):
        for row in rows:
            ext = row.get('extensions') or {}
            actor = row.get('owner', ext.get('owner'))
            sid = row.get('source_id', ext.get('source_id'))
            refs = [*ext.get('source_refs', []), *(r for e in row.get('effects', []) for r in (e.get('extensions') or {}).get('source_refs', []))]
            affixes = {_ref_affix(r) for r in refs} - {None}
            if sid in source_parts:
                linked_actor, linked = source_parts[sid]
                if actor is not None and actor != linked_actor:
                    raise PlanError('实际收益提供者与唯一来源部件不一致', 'static_inclusion_identity')
                actor = linked_actor; affixes |= linked
            if not affixes:
                unresolved.append({'path': collection + '.' + row['id'], 'provider': actor})
            for affix in sorted(affixes):
                claims.append({'provider': actor, 'affix_id': affix, 'state': 'active', 'path': collection + '.' + row['id']})
    for actor, character in contribution_report.get('characters', {}).items():
        for row in character.get('contributions', []):
            for affix in sorted({_ref_affix(r) for r in row.get('source_refs', [])} - {None}):
                claims.append({'provider': row.get('provider_character_id'), 'affix_id': affix,
                               'state': 'active', 'path': 'panel.' + actor + '.' + row['contribution_id']})
    for record in automatic:
        state = record.get('status')
        if state in ('inactive', 'excluded'):
            continue
        equipment = (((record.get('scope') or {}).get('identity') or {}).get('provider_proof') or {}).get('equipment')
        registered = [*(record.get('equipment_coverage') or []), *([equipment] if equipment else [])]
        # A mechanism may fail its parent/scene gate before producing scope or
        # coverage. Its typed provider claim still occupies the source; source
        # pending must not be mistaken for an explicit exclusion.
        material = record.get('source_material') or {}
        projection = record.get('projection') or material.get('compiler_result') or {}
        provider = (projection.get('execution_qualification') or {}).get('provider') or {}
        subject = (material.get('source_binding') or {}).get('subject') or {}
        if (provider.get('relation') in ('equipped_artifact_set', 'equipped_weapon')
                and subject.get('target_type') in ('artifact_set', 'weapon')):
            registered.append({'provider': record.get('actor'), 'owner': {
                'kind': subject['target_type'], 'affix_id': provider.get('affix_id')}})
        for row in registered:
            affix = _owner_affix(row.get('owner') or {})
            if affix is not None:
                claims.append({'provider': row.get('provider'), 'affix_id': affix,
                    'state': 'active' if state == 'ready' else 'pending', 'path': 'automatic.' + record['id']})
    return {'identity_coverage': 'explicit_typed_sources_only', 'claims': claims, 'unresolved_sources': unresolved}


def finalize_static_inclusions(batch, plan, template, mechanics_model, requirements, issues, source_fingerprint,
        *, contribution_report, applicable=(), automatic_compilations=(), mechanics_matches=(), context_plan=None):
    if (type(batch) is not _Batch or batch._seal != fingerprint([batch._entries, batch._context, batch._components])):
        raise PlanError('静态来源候选必须来自本次新鲜数据库装配且不可变', 'static_inclusion_identity')
    if not batch._entries:
        return {'records': [], 'adoptions': [], 'remove_issue_fingerprints': [], 'issues': []}
    native = context_plan or plan
    if fingerprint(context_view(input_view(native))) != batch._context:
        raise PlanError('静态来源原生上下文在本次取证后发生变化', 'static_inclusion_identity')
    graph = graph_view(template, mechanics_model, mechanics_matches); _bounded(graph, MAX_GRAPH_BYTES)
    graph_fp = fingerprint(graph)
    inventory = _inventory(plan, template, mechanics_model, contribution_report, applicable, automatic_compilations)
    inventory_bytes = _bounded(inventory, MAX_MATERIAL_BYTES)
    # Every record retains the same occupancy proof. Reserve its repeated bytes
    # before deep-copying; a failed compose cannot publish a partial adoption.
    if sum(_bounded(r, MAX_RECORD_BYTES) + inventory_bytes + 4096 for r in batch._entries) > MAX_RECORD_BYTES:
        raise PlanError('静态来源最终记录超过本次装配预算', 'static_inclusion_budget')
    result = {'records': [], 'adoptions': [], 'remove_issue_fingerprints': [], 'issues': []}
    for saved in batch._entries:
        record = copy.deepcopy(saved); bid = record['binding_id']; actor = record['provider_character_id']
        record.update(source_fingerprint=source_fingerprint, final_graph_fingerprint=graph_fp,
                      source_occupancy=copy.deepcopy(inventory))
        reqs = [r for r in requirements if r['binding_id'] == bid]
        own = [r for r in issues if _issue_key(r) == _expected_issue(bid)]
        if len(reqs) != 1 or fingerprint(reqs[0]) != record['requirement_fingerprint']:
            record['issues'].append({'code': 'static_inclusion_identity', 'message': '最终装备要求发生变化或已被其它来源认领'})
        if panel_fingerprint(plan, actor) != record['source_component']['panel_fingerprint']:
            record['issues'].append({'code': 'static_inclusion_identity', 'message': '原始给定面板或装备选择在本次来源取证后发生变化'})
        actual = [r for r in template['characters'] if r['id'] == actor]
        expected = [r for r in native['manual_template']['characters'] if r['id'] == actor]
        if (len(actual) != 1 or len(expected) != 1
                or fingerprint(actual[0]['stats']) != fingerprint(expected[0]['stats'])
                or actual[0]['level'] != expected[0]['level']):
            record['issues'].append({'code': 'static_inclusion_identity', 'message': '最终计算角色面板与本次原生给定面板不一致'})
        if len(own) != 1 or [fingerprint(r) for r in own] != record['issue_fingerprints']:
            record['issues'].append({'code': 'static_inclusion_identity', 'message': '待解除问题不是本次唯一原始装备缺项'})
        conflicts = [r for r in inventory['claims'] if r['state'] != 'excluded'
                     and r['affix_id'] == record['owner']['affix_id'] and r['provider'] in (None, actor)]
        if conflicts:
            record['issues'].append({'code': 'static_inclusion_duplicate', 'message': '同一真实来源存在实际交付或未决来源，不能同时声明面板已含',
                                     'conflicting_paths': sorted({r['path'] for r in conflicts})})
        if not record['issues']:
            record.update(status='ready', source_authority='fresh_database_admission')
        record['compilation_fingerprint'] = fingerprint(record)
        result['records'].append(record)
        if record['status'] == 'ready':
            result['adoptions'].append({'binding_id': bid, 'resolved_by': {'kind': 'static_inclusion', 'id': record['id'],
                                         'compilation_fingerprint': record['compilation_fingerprint']}})
            result['remove_issue_fingerprints'].extend(record['issue_fingerprints'])
        else:
            result['issues'].append({'code': 'static_inclusion_pending', 'path': 'static_inclusions.' + record['id'],
                'binding_id': bid, 'part_id': None, 'message': record['issues'][0]['message'], 'action': 'review_effect'})
    _bounded(result, MAX_RECORD_BYTES)
    return result


def apply_static_inclusion_adoptions(finalized, requirements, issues):
    """Only composer's private report collections are changed here."""
    remove = finalized['remove_issue_fingerprints']
    if len(remove) != len(set(remove)) or any(sum(fingerprint(i) == fp for i in issues) != 1 for fp in remove):
        raise PlanError('静态来源缺项认领不唯一', 'static_inclusion_identity')
    targets = []
    for adoption in finalized['adoptions']:
        matches = [r for r in requirements if r['binding_id'] == adoption['binding_id']]
        if len(matches) != 1 or 'resolved_by' in matches[0]:
            raise PlanError('静态来源装备要求认领冲突', 'static_inclusion_identity')
        targets.append((matches[0], adoption['resolved_by']))
    for req, resolved_by in targets:
        req['resolved_by'] = copy.deepcopy(resolved_by)
    issues[:] = [r for r in issues if fingerprint(r) not in remove]
    issues.extend(copy.deepcopy(finalized['issues']))
