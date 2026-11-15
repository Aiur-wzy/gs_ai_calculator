"""Fresh reviewed data -> qualified D20 delivery, with atomic group admission."""
from __future__ import annotations

import copy
import json
import sqlite3

from genshin_engine.mechanism_descriptor_compiler import compile_mechanism_descriptor
from genshin_runtime.mechanism_semantics import finite_json, target_location

from .files import execution_input_view, fingerprint
from .mechanism_execution_scope import (
    consumer_scope,
    duplicate_scope,
    need,
    qualify_activation,
    qualify_identity,
    qualify_scene,
    stat_scope,
)
from .mechanism_parent_graph import build_mechanism_parent_graph
from .mechanism_source_schema import (
    CAST_COMPILER,
    COMPILER,
    CONTACT_COMPILER,
    PREFIX,
    SUPERVISED_PROFILE,
    mechanism_governance_profile,
    mechanism_rule_id,
)

MAX_BYTES = 8 * 1024 * 1024
ACTION_ADMISSION_CONTRACT = 'action-admission-execution-v1'
_HISTORICAL_REPLAY_AUTHORITY = object()


def requires_action_admission(records):
    return any((record.get('projection') or {}).get('execution_qualification', {}).get('parent', {}).get(
        'profile_kind') == 'successful_action_v2' for record in records)


def _query(db_path, binding):
    from genshin_db.runtime_queries import query_runtime
    request = {'action': 'mechanism_profile', 'version_line': binding['version_line'],
               'mechanism_id': binding['mechanism_id']}
    if binding['revision'] is not None:
        request['revision'] = binding['revision']
    return query_runtime(db_path, request)


def _delivery(binding, projection, graph, activation):
    parts, rules, initials = [], [], []
    actions = list(dict.fromkeys(p['action_id'] for p in graph['parents']))
    values = {'provider': binding['actor'], 'provider_characters': [binding['actor']],
              'qualifying_actions': actions, 'activation_delay': activation['delay']}
    if projection['execution_qualification'].get('schema_version') == 3:
        values['qualifying_hits'] = [p['hit_id'] for p in graph['parents']]
    for item in projection['rule_templates']:
        key, rule = item['key'], copy.deepcopy(item['rule'])
        part_id, rule_id = 'mechanism.' + key, mechanism_rule_id(binding['id'], key)
        for port in projection['execution_ports']:
            if port['rule_key'] == key:
                container, field = target_location(rule, port['target'])
                container[field] = copy.deepcopy(values[port['role']])
        rule.update(id=rule_id, source_id=binding['id'] + ':' + part_id,
                    status=binding['scenario']['status'], source=binding['scenario']['source'])
        qualification = projection.get('execution_qualification') or {}
        parent = qualification.get('parent') or {}
        if parent.get('profile_kind') == 'successful_action_v2':
            need(rule['trigger']['event'] == 'action_start', '动作接纳资格只能交付动作开始规则')
            rule['trigger']['action_admission'] = {'contract': ACTION_ADMISSION_CONTRACT}
        initial = next(copy.deepcopy(r) for r in projection['initial_rule_templates'] if r['key'] == key)
        initial.pop('key')
        initial['rule_id'] = rule_id
        need(initial['instances'] == [] and initial['cooldown_remaining'] == 0, '受审机制必须从空状态开始')
        parts.append({'part_id': part_id, 'name_zh': rule['name_zh'],
                      'rule': {'kind': 'mechanics', 'rule_id': rule_id}, 'delivery': {'kind': 'mechanics'}})
        rules.append(rule)
        initials.append(initial)
    return {'parts': parts, 'rules': rules, 'initial_rules': initials}


def _record(binding, governance_profile):
    return {'id': binding['id'], 'actor': binding['actor'], 'compiler': COMPILER, 'status': 'pending',
            'governance_profile': governance_profile,
            'execution_authority': ('none' if governance_profile == SUPERVISED_PROFILE
                                    else 'legacy_app_compiler'),
            'whole_source_status': 'partial', 'source_material': None, 'source_fingerprint': None,
            'authorization_fingerprint': None, 'projection': None, 'parent_graph': None, 'scope': None,
            'acceptance_fingerprint': None, 'execution_fingerprint': None,
            'candidate_fingerprint': None, 'required_materialization': None,
            'accepted_fingerprint': binding['accepted_fingerprint'], 'required_confirmation': None,
            'parts': [], 'rules': [], 'initial_rules': [], 'issues': []}


def compile_mechanism_sources(plan, template, db_path, *, action_event_compilations=(),
        attack_metadata_compilations=(), parameter_compilations=(), action_resource_compilations=(),
        action_admission_compilations=(),
        periodic_compilations=(), elemental_compilations=(), reaction_compilations=(),
        equipment_evidence=None, equipment_requirements=(), missing, source_provider=None,
        cast_identity_provider=None, _historical_replay_authority=None):
    """Private source_provider enables saved-material replay; never native input."""
    bindings = plan.get('mechanism_sources', [])
    if not bindings:
        return None, [], []
    if (_historical_replay_authority is not None
            and _historical_replay_authority is not _HISTORICAL_REPLAY_AUTHORITY):
        from .schema import PlanError
        raise PlanError('旧机制重放授权无效', 'legacy_mechanism_replay_forbidden')
    governance_profile = mechanism_governance_profile(
        plan,
        historical_receipt_replay=(
            _historical_replay_authority is _HISTORICAL_REPLAY_AUTHORITY
        ),
    )
    source_provider = source_provider or (lambda binding: _query(db_path, binding))
    if cast_identity_provider is None:
        from .mechanism_cast_identity import identity_provider
        cast_identity_provider = identity_provider(db_path)
    records = [_record(b, governance_profile) for b in bindings]

    def issue(record, message, code='mechanism_pending', action='review_mechanism_source'):
        row = {'code': code, 'path': 'mechanism_sources.' + record['id'], 'message': str(message)[:2048]}
        record['issues'].append(row)
        missing(code, row['path'], row['message'], action)

    for binding, record in zip(bindings, records, strict=True):
        try:
            need(len(bindings) <= 8, '通用机制来源最多8项')
            need(len({b['id'] for b in bindings}) == len(bindings), '机制来源本地ID重复')
            material = copy.deepcopy(source_provider(binding))
            finite_json(material, 2 * 1024 * 1024)
            record['source_material'] = material
            need(material.get('status') == 'ready' and material.get('source_status') == 'ready'
                 and material.get('review_status') == 'approved' and material.get('materialization_status') == 'ready',
                 '受审机制资料尚未就绪或已撤销；请重新核对当前来源')
            descriptor = material['descriptor']
            need(material['snapshot']['version_line'] == binding['version_line']
                 and descriptor['mechanism_id'] == binding['mechanism_id']
                 and (binding['revision'] is None or descriptor['revision'] == binding['revision']),
                 '所选资料线、机制或修订与实际返回不一致')
            projection = compile_mechanism_descriptor(descriptor, source_proof=material['source_proof'],
                facts=material['facts'], parameters=material['resolved_parameters'])
            record['projection'] = projection
            need(projection['status'] == 'ready' and projection['qualification_status'] == 'consistent',
                 '机制规则或执行资格无法从完整资料重新构建')
            # Do not use the stored compiler result as an execution oracle.
            checked_material = {**material, 'compiler_result': projection}
            qualification = projection['execution_qualification']
            cast = qualification.get('schema_version') == 2
            contact = qualification.get('schema_version') == 3
            if contact:
                from genshin_runtime.mechanism_semantics import (
                    record_payload_fingerprint,
                )

                from .mechanism_contact_graph import build_mechanism_contact_graph
                from .mechanism_contact_scope import qualify_contact_provider
                from .mechanism_parent_graph import _snapshot_equal
                record['compiler'] = CONTACT_COMPILER
                provider = qualification['provider']
                expected_selection = {'refinement': provider['refinement']} if provider['relation'] == 'equipped_weapon' else {}
                need(material['source_binding']['selection'] == expected_selection
                     and material['source_binding']['subject'] == material['source_proof']['subject']
                     and _snapshot_equal(material['snapshot'], material['source_proof']['snapshot']),
                     '命中来源选择、主体或证明快照不一致')
                source = material['source_binding']
                envelope = {'id': descriptor['mechanism_id'], 'revision': descriptor['revision'],
                    'subject': source['subject'], 'payload': {'descriptor': descriptor, 'source_binding': source},
                    'evidence': source['evidence'], 'applicability': {
                        'version_line': binding['version_line'], 'upstream_commit': material['snapshot']['upstream_commit'],
                        'normalization_fingerprint': fingerprint(material['snapshot']['normalization'])},
                    # The shared payload hash intentionally excludes review;
                    # source authorization was checked separately above.
                    'review': {'status': 'pending', 'reviewer': None, 'reviewed_at': None}}
                need(record_payload_fingerprint(envelope, 'descriptor') == material['record_payload_fingerprint'],
                     '命中来源完整记录与载荷指纹不一致')
                hit_filter = qualification['parent']['callback_bridge']['hit_filter']
                graph = build_mechanism_contact_graph(plan, template, actor=binding['actor'],
                    source_snapshot=material['snapshot'],
                    scope={k: copy.deepcopy(hit_filter.get(k, [])) for k in ('action_type_ids', 'damage_type_ids', 'elements')},
                    action_records=action_event_compilations, parameter_records=parameter_compilations,
                    metadata_records=attack_metadata_compilations, periodic_records=periodic_compilations,
                    elemental_records=elemental_compilations, reaction_records=reaction_compilations)
                record['parent_graph'] = graph
                need(graph['status'] == 'ready', '; '.join(i['message'] for i in graph['issues']))
                identity, coverage = qualify_contact_provider(plan, template, binding, checked_material, graph,
                    equipment_evidence or {}, equipment_requirements, cast_identity_provider)
                record['equipment_coverage'] = coverage
            elif cast:
                from .mechanism_cast_graph import build_mechanism_cast_graph
                from .mechanism_cast_scope import qualify_cast_provider
                record['compiler'] = CAST_COMPILER
                graph = build_mechanism_cast_graph(plan, template, binding=binding, material=checked_material,
                    identity_provider=cast_identity_provider, resource_records=action_resource_compilations,
                    action_admission_records=action_admission_compilations,
                    action_records=action_event_compilations, parameter_records=parameter_compilations,
                    metadata_records=attack_metadata_compilations)
                identity, coverage = qualify_cast_provider(plan, template, binding, checked_material, graph,
                    equipment_evidence or {}, equipment_requirements)
                record['equipment_coverage'] = coverage
            else:
                need('cast_binding' not in binding, '旧接触资格不能接收施放关联')
                graph = build_mechanism_parent_graph(plan, template, actor=binding['actor'],
                    source_binding=material['source_binding'], source_proof=material['source_proof'], qualification=qualification,
                    action_records=action_event_compilations, parameter_records=parameter_compilations,
                    metadata_records=attack_metadata_compilations)
                identity = qualify_identity(plan, template, binding, checked_material, graph)
            record['parent_graph'] = graph
            need(graph['status'] == 'ready', '; '.join(i['message'] for i in graph['issues']))
            scene = qualify_scene(plan, binding, qualification)
            activation = qualify_activation(plan, binding, graph, projection)
            stage = _delivery(binding, projection, graph, activation)
            ids = {r['id'] for r in plan['mechanics_model']['rules']}
            need(not any(r['id'] in ids for r in stage['rules'])
                 and not any(r['id'].startswith(PREFIX) for r in plan['mechanics_model']['rules']),
                 '原生模型中存在未注册或重复的通用生成规则')
            scope = {'identity': identity, 'scene': scene, 'calibration': activation,
                     'stat_reads': [], 'consumers': consumer_scope(plan, template, qualification, stage['rules']),
                     'duplicate_identity': copy.deepcopy(qualification['duplicate_identity']),
                     'axis_dependencies': {'rotation': fingerprint(plan['rotation_model']),
                                           'model': fingerprint(plan['mechanics_model'])}}
            record.update(source_fingerprint=projection['source_fingerprint'],
                          authorization_fingerprint=material['authorization_fingerprint'], scope=scope, **stage)
        except (ValueError, KeyError, TypeError, OSError, sqlite3.Error, IndexError, StopIteration) as exc:
            issue(record, exc)

    # Compile all candidate sources before checking reads or competition. A
    # later source can otherwise violate an earlier source's fixed-stat scope.
    if not any(r['issues'] for r in records):
        try:
            duplicate_scope(plan, template, records)
            all_rules = [*plan['mechanics_model']['rules'], *(rule for r in records for rule in r['rules'])]
            need(len({r['id'] for r in all_rules}) == len(all_rules), '组合规则身份重复')
            for binding, record in zip(bindings, records, strict=True):
                record['scope']['stat_reads'] = stat_scope(plan, template, binding['actor'], all_rules, record['projection'])
            for binding, record in zip(bindings, records, strict=True):
                scope = record['scope']
                acceptance = fingerprint({'compiler': record['compiler'],
                    'mechanism': record['projection']['mechanism_fingerprint'],
                    'authorization': record['authorization_fingerprint'],
                    'record': record['source_material']['record_payload_fingerprint'],
                    'identity': scope['identity'], 'scene': binding['scenario'], 'calibration': binding['calibration'],
                    'fixed_stats': [r for r in scope['stat_reads'] if r['fixed_nonextra']]})
                execution = fingerprint({'compiler': record['compiler'], 'scope': scope, 'parent_graph': record['parent_graph'],
                    'rules': record['rules'], 'initial': record['initial_rules'], 'input_plan': execution_input_view(plan),
                    'template': template})
                candidate = fingerprint({
                    'contract': 'mechanism-materialization-candidate-v1',
                    'governance_profile': governance_profile,
                    'source_fingerprint': record['source_fingerprint'],
                    'authorization_fingerprint': record['authorization_fingerprint'],
                    'acceptance_fingerprint': acceptance,
                    'execution_fingerprint': execution,
                    'parts': record['parts'], 'rules': record['rules'],
                    'initial_rules': record['initial_rules'],
                })
                record.update(acceptance_fingerprint=acceptance, execution_fingerprint=execution,
                              candidate_fingerprint=candidate,
                              required_confirmation={'accepted_fingerprint': acceptance})
            context = make_replay_context(plan, template, action_event_compilations=action_event_compilations,
                attack_metadata_compilations=attack_metadata_compilations, parameter_compilations=parameter_compilations,
                action_resource_compilations=action_resource_compilations, equipment_evidence=equipment_evidence,
                action_admission_compilations=action_admission_compilations,
                equipment_requirements=equipment_requirements, records=records,
                periodic_compilations=periodic_compilations, elemental_compilations=elemental_compilations,
                reaction_compilations=reaction_compilations)
            finite_json({'records': records, 'context': context}, MAX_BYTES)
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            for record in records:
                issue(record, exc)
                record['required_confirmation'] = None

    invalid = any(r['issues'] for r in records)
    if governance_profile == SUPERVISED_PROFILE:
        for record in records:
            preview = copy.deepcopy(record['rules'])
            candidate_ready = not record['issues'] and record['candidate_fingerprint'] is not None
            if candidate_ready:
                record['required_materialization'] = {
                    'contract': 'mechanism-materialization-candidate-v1',
                    'candidate_fingerprint': record['candidate_fingerprint'],
                    'source_fingerprint': record['source_fingerprint'],
                    'scope': 'current_plan_only',
                }
                issue(record,
                      '该资料是无执行权知识；须由Agent在当前方案材料化为强类型输入后才能执行',
                      'mechanism_materialization_required', 'materialize_mechanism_source')
            record.update(status='pending', candidate_status=('ready' if candidate_ready else 'invalid'),
                          preview_rules=preview, parts=[], rules=[], initial_rules=[],
                          required_confirmation=None)
        # Knowledge candidates never become source components or mutate the
        # effective mechanics model.  Their fingerprints stay in the report
        # so a later materialization can bind exactly this evidence.
        if len(json.dumps(records, ensure_ascii=False, separators=(',', ':')).encode('utf8')) > MAX_BYTES:
            for record in records:
                record.update(source_material=None, projection=None, parent_graph=None, scope=None,
                              preview_rules=[], required_materialization=None)
                issue(record, '完整机制候选回执超过限额；未交付截断规则', 'mechanism_receipt_limit')
        return None, records, []

    for binding, record in zip(bindings, records, strict=True):
        if invalid:
            record['required_confirmation'] = None
        elif binding['accepted_fingerprint'] != record['acceptance_fingerprint']:
            issue(record, '请核对受审规则、父动作、场景与校准后接受当前机制范围', 'mechanism_confirmation_required')
        else:
            record['status'] = 'ready'
    if any(r['status'] != 'ready' for r in records):
        for record in records:
            record.update(status='pending', preview_rules=record['rules'], parts=[], rules=[], initial_rules=[])
        # Even pending receipts must not retain oversized source material.
        if len(json.dumps(records, ensure_ascii=False, separators=(',', ':')).encode('utf8')) > MAX_BYTES:
            for record in records:
                record.update(source_material=None, projection=None, parent_graph=None, scope=None, preview_rules=[],
                              required_confirmation=None)
                issue(record, '完整机制回执超过限额；未交付截断规则', 'mechanism_receipt_limit')
        return None, records, []
    model = copy.deepcopy(plan['mechanics_model'])
    for record in records:
        model['rules'].extend(copy.deepcopy(record['rules']))
        model['initial_state']['rules'].extend(copy.deepcopy(record['initial_rules']))
    sources = [{'kind': 'mechanism', 'id': r['id'], 'source_fingerprint': r['source_fingerprint'],
                'accepted_fingerprint': r['accepted_fingerprint'], 'execution_fingerprint': r['execution_fingerprint']}
               for r in records]
    return model, records, sources


def _compile_historical_mechanism_replay(plan, template, db_path, **kwargs):
    """Rebuild a legacy model only while verifying a saved result receipt.

    This private adapter is the sole authority-bearing legacy entry point.
    The authority token never appears in a plan, tool schema or persisted
    artifact, so candidate data cannot manufacture it.
    """

    return compile_mechanism_sources(
        plan,
        template,
        db_path,
        _historical_replay_authority=_HISTORICAL_REPLAY_AUTHORITY,
        **kwargs,
    )


def make_replay_context(plan, template, *, action_event_compilations, attack_metadata_compilations,
                        parameter_compilations, action_resource_compilations=(), action_admission_compilations=(),
                        equipment_evidence=None,
                        equipment_requirements=(), records=(), periodic_compilations=(),
                        elemental_compilations=(), reaction_compilations=()):
    context = {'plan': execution_input_view(plan), 'template': template,
               'action_event_compilations': list(action_event_compilations),
               'attack_metadata_compilations': list(attack_metadata_compilations),
               'parameter_compilations': list(parameter_compilations)}
    contact = any(r['compiler'] == CONTACT_COMPILER for r in records)
    admission = requires_action_admission(records)
    if contact or any(r['compiler'] == CAST_COMPILER for r in records):
        context.update(schema_version=(5 if contact else 4) if admission else 2,
                       action_resource_compilations=list(action_resource_compilations),
                       equipment_evidence=equipment_evidence or {}, equipment_requirements=list(equipment_requirements))
    if admission:
        context['action_admission_compilations'] = list(action_admission_compilations)
    if contact:
        context.update(schema_version=5 if admission else 3,
                       periodic_compilations=list(periodic_compilations),
                       elemental_compilations=list(elemental_compilations), reaction_compilations=list(reaction_compilations))
    return copy.deepcopy(context)
