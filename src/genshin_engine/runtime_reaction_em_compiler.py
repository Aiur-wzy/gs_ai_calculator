"""Bounded reaction EM source facts; actual App scope grants execution authority."""
from __future__ import annotations

import copy
import json
import re
from importlib import resources

from genshin_engine.runtime_compiler import _Compiler, _escape, _hash, _Stop
from genshin_engine.runtime_reaction_benefit_compiler import _ReactionCompiler
from genshin_runtime.normalization import Profile, verify_receipt

PROFILE_ID = 'ordinary-reaction-flat-team-em-reference-v1'
PROFILE_VERSION = 2
COMPILER_ID = 'database-reaction-flat-em-v1'
MAX_BYTES = 2 * 1024 * 1024
FACTS = {'output': 'em_flat', 'nonextra_inclusion': 'included',
         'trigger': 'frontline_original_target_reaction_provider',
         'recipients': 'fixed_living_party_within_source_predicate',
         'timing': 'post_parent_no_retroactive',
         'lifetime': 'fixed_duration_unique_refresh_after_source_leaves_field',
         'attachment': 'RemoveUniqueModifier_then_AttachModifier'}
CONSTANT_SCENE = {'profile_id': PROFILE_ID, 'profile_version': PROFILE_VERSION,
    'single_player_fixed': True, 'all_targets_monsters': True, 'all_recipients_alive': True,
    'single_unique_provider': True, 'simulation_clock': 'no_hitlag',
    'origin_target_range': 'within_source_range', 'recipient_range': 'all_party_within_source_range',
    'initial_source_effects': 'none', 'external_state_policy': 'no_external_removal_or_equipment_change',
    'static_policy': 'included', 'reaction_scope': 'ordinary_original_target_melt_vaporize_swirl',
    'property_domain_policy': 'current_ordinary_em', 'callback_policy': 'no_relevant_same_occurrence_reader'}
REQUIREMENTS = {key: 'app_must_verify' for key in ('actual_equipment_owner', 'actual_four_piece_selection',
    'actual_reaction_provider', 'original_target_parent_provenance', 'automatic_v3_elemental_model',
    'final_callback_dependency_closure', 'all_actual_reactions_covered', 'ordinary_property_domain_consumers')}


def _profile():
    return json.loads(resources.files('genshin_runtime').joinpath('resources/reaction_em_profiles.json').read_text('utf8'))


def default_scenario():
    return {**CONSTANT_SCENE, 'target_id': None, 'status': 'pending', 'source': '',
            'accepted_assumptions_fingerprint': None}


def reaction_em_compiler_caps():
    return {'compiler': COMPILER_ID, 'compiler_version': 1, 'profile_id': PROFILE_ID, 'profile_version': PROFILE_VERSION,
        'default_scenario': default_scenario(), 'field': 'effect_bindings[].upstream_recipe',
        'source_policy': '每次读取完整同快照来源并验证已审图、参数、原文事实；仅接受场景指纹不能批准未知结构。',
        'execution_policy': '源编译后检查最终动作及规则；执行后连接每次实际反应与附着父发生。',
        'attribute_policy': '初始面板与NONEXTRA仍须明确核对；已审来源仅自动填充自身实际精通效果的分类。',
        'scope': {'attribute_contract': 'attribute-ledger-v2', 'elemental_ruleset': 'gauge-swirl-dynamic-v3',
            'reactions': ['melt', 'vaporize', 'swirl'], 'target': 'ordinary_original_target',
            'provider': 'frontline_actual_reaction_owner', 'snapshot_support': False,
            'dynamic_em_conversion_readers': False, 'same_occurrence_order_authority': False},
        'whole_source_status': 'partial', 'report_field': 'upstream_compilations',
        'execution_result_field': 'reaction_em_execution'}


class _EMCompiler(_Compiler):
    def __init__(self, bundle, owner, source_id, scenario, period):
        super().__init__(bundle, owner, source_id, scenario, period)
        self.profile = _profile()
        self.profile_valid = False
        self.branches, self.parameter_sources, self.raw_sources = [], {}, []

    def validate_profile(self):
        profile=self.profile
        required={'profile_id','profile_version','commit','review_status','review_scope','fields','types',
                  'normalization_mapping_fingerprint','admission','normalized_body_sha256',
                  'normalized_operations_sha256','facts','evidence','parent_evidence'}
        if not isinstance(profile,dict) or set(profile)!=required:
            self.fail('invalid_source_profile','/profile','本地来源档案字段缺失或出现未知字段。',severity='error')
        if (profile['profile_id'] != PROFILE_ID or type(profile['profile_version']) is not int
                or profile['profile_version'] != PROFILE_VERSION):
            self.fail('unsupported_source_profile','/profile','本地档案身份或版本不是本编译器的合同。')
        if (any(not isinstance(profile[key],dict) for key in ('fields','types','admission','facts','evidence'))
                or not isinstance(profile['parent_evidence'],list)):
            self.fail('invalid_source_profile','/profile','本地档案结构错误。',severity='error')
        if len(json.dumps(profile,ensure_ascii=False,allow_nan=False).encode())>MAX_BYTES:
            self.fail('invalid_source_profile','/profile','本地档案超过字节限制。',severity='error')
        stack,count=[(profile,0)],0
        while stack:
            value,depth=stack.pop(); count+=1
            if count>16384 or depth>64:
                self.fail('invalid_source_profile','/profile','本地档案树超过限制。',severity='error')
            if isinstance(value,dict) and any(not isinstance(key,str) for key in value):
                self.fail('invalid_source_profile','/profile','本地档案必须使用JSON文本键。',severity='error')
            stack.extend((v,depth+1) for v in (value.values() if isinstance(value,dict) else value if isinstance(value,list) else ()))
        self.profile_valid=True

    def build(self):
        profile, b = self.profile, self.bundle
        if (profile.get('review_status') != 'reviewed' or set(profile.get('facts', {})) != set(FACTS)
                or any(fact.get('status') != 'reviewed' or fact.get('value') != FACTS[name]
                       or fact.get('scope') != 'this_admitted_complete_source'
                       or fact.get('evidence_fingerprint') != _hash(profile['evidence'])
                       or not fact.get('raw_witnesses')
                       or fact.get('raw_witnesses_fingerprint') != _hash(fact['raw_witnesses'])
                       for name, fact in profile.get('facts', {}).items())):
            self.fail('source_fact_not_reviewed', '/profile', '完整来源事实待审、撤销或证据已变更，接受哈希不能升级。')
        self.fields(b, ('bundle_version','status','issues','source','snapshot','param_list','affix','talent',
            'abilities','references','reference_closed','content_fingerprint','normalization'),
            ('talent_candidates','execution_support','closure_fingerprint'), '/')
        if b['bundle_version'] != 1 or b['status'] != 'ready' or b['issues'] or b['reference_closed'] is not True or b.get('talent_candidates'):
            self.fail('source_not_closed', '/', '需要完整同快照已闭合来源包。')
        if b['content_fingerprint'] != _hash({k:v for k,v in b.items() if k != 'content_fingerprint'}):
            self.fail('bundle_fingerprint_mismatch','/content_fingerprint','完整包指纹不符。',severity='error')
        for ident in (self.owner, self.source_id):
            self.text(ident, '/identity')
            if len(ident) > 128:
                self.fail('identity_limit','/identity','身份过长。',severity='error')
        if not self.source_id.endswith(':upstream'):
            self.fail('invalid_source_id','/source_id','来源须为binding:upstream。',severity='error')
        self.time(self.period,'/period')
        source=b['source']
        self.fields(source,('kind','affix_id','affix_group_id','source_level','version_line','snapshot_id','upstream_commit'),(),'/source')
        if (source['kind'] != 'equip_affix' or source['upstream_commit'] != profile['commit']
                or source['version_line'] not in ('release','beta') or not re.fullmatch(r'[0-9a-f]{64}',source['snapshot_id'])
                or any(b['snapshot'].get(k) != source[k] for k in ('snapshot_id','upstream_commit','version_line'))):
            self.fail('source_identity_mismatch','/source','来源/快照不在受审范围。')
        for k in ('affix_id','affix_group_id','source_level'):
            if type(source[k]) is not int or source[k] < (0 if k == 'source_level' else 1):
                self.fail('source_identity_mismatch','/source/'+k,'来源ID/等级类型不符。')
        if len(b['abilities']) != 1 or len(b['references']) > 256:
            self.fail('unsupported_graph','/abilities','只接受受审完整单能力图。')
        base=verify_receipt(b['normalization'],commit=source['upstream_commit'])
        if b['normalization'] != b['snapshot'].get('normalization'):
            self.fail('mixed_normalization','/normalization','归一化证据不一致。')
        self.normalization=Profile(PROFILE_ID,(profile['commit'],),{**base.fields,**profile['fields']},{**base.types,**profile['types']})
        if self.normalization.mapping_fingerprint != profile['normalization_mapping_fingerprint']:
            self.fail('stale_mapping','/normalization','局部映射已变更，须重新审核。')
        entry,talent,affix=b['abilities'][0],b['talent'],b['affix']
        for kind,record,key in (('ability',entry,'payload'),('talent',talent,'operations'),('affix',affix,'record')):
            self.evidence(record,'/'+kind,key,definition=kind != 'affix')
            approved=profile['admission'][kind]
            if (any(record.get(k) != approved[k] for k in ('source_path','source_sha256','json_pointer'))
                    or _hash(record[key]) != approved['node_sha256']):
                self.fail('unsupported_source_node','/'+kind,'完整父节点、原指针或源字节尚未审核。')
            self.raw_sources.append({'kind':kind,**copy.deepcopy(record)})
        record=affix['record']
        if (record['affixId'] != source['affix_id'] or record['id'] != source['affix_group_id']
                or record.get('level',0) != source['source_level'] or record['paramList'] != b['param_list']):
            self.fail('source_identity_mismatch','/affix','有效等级或完整参数装配不一致。',severity='error')
        body=self.normalization.normalize(entry['payload'],kind='ability')
        operations=self.normalization.normalize(talent['operations'],kind='talent_config')
        if (_hash(body) != profile['normalized_body_sha256'] or _hash(operations) != profile['normalized_operations_sha256']
                or entry.get('variant_key') != 'Default' or record['openConfig'] != body['abilityName']
                or talent['json_pointer'] != '/'+_escape(record['openConfig'])):
            self.fail('unsupported_shape','/ability','规范化完整图与已审形状不符。')
        # A read-only structural checker shared with the old source compiler;
        # neither its old profile nor its materialization route is reused.
        _ReactionCompiler.references(self,b,body,operations)
        self.parameters=copy.deepcopy(body['abilitySpecials'])
        written=set()
        for i,op in enumerate(operations):
            if i==0:
                self.exact(op['$type'],'AddAbility','/talent/0/$type')
                continue
            token,name=op['paramDelta'],op['paramSpecial']
            if (op['$type'] != 'ModifyAbility' or name in written or name not in self.parameters
                    or self.parameters[name] != 0 or not re.fullmatch(r'%[1-8]',token)):
                self.fail('parameter_writer','/talent/'+str(i),'参数必须零初值唯一写入。')
            index=int(token[1:])-1
            self.parameters[name]=self.scalar(b['param_list'][index],'/param_list/'+str(index))
            self.parameter_sources[name]={'param_index':index,'value':self.parameters[name],
                'operation_pointer':talent['json_pointer']+'/'+str(i),
                'parameter_pointer':affix['json_pointer']+'/paramList/'+str(index)}
            written.add(name)
        if written != set(self.parameters):
            self.fail('parameter_writer','/abilitySpecials','参数未完全装配。')
        handler_name=body['onAdded'][0]['modifierName']
        handler=body['modifiers'][handler_name]
        listener,=handler['modifierMixins']
        remove,attach=listener['actions']
        self.exact([remove['$type'],attach['$type']],['RemoveUniqueModifier','AttachModifier'],'/listener/actions')
        self.exact(remove['modifierName'],attach['modifierName'],'/listener/actions/modifierName')
        target=body['modifiers'][attach['modifierName']]
        self.value=self.parameters[target['properties']['Actor_ElemMasteryDelta']]
        self.duration=self.time(self.parameters[target['duration']],'/modifier/duration')
        self.unique_keys=[attach['modifierName']]
        self.branches=[{'raw_reaction':reaction,'status':'projected' if reaction in ('Stream','Melt','SwirlFire','SwirlWater','SwirlIce') else 'excluded_by_scope',
            'source_path':entry['source_path'],'source_sha256':entry['source_sha256'],'node_sha256':_hash(reaction),
            'json_pointer':entry['json_pointer']+self.normalization.raw_pointer(entry['payload'],
                '/modifiers/'+_escape(handler_name)+'/modifierMixins/0/reactionTypes/'+str(i),kind='ability'),
            'reason':'ordinary_original_target_only' if reaction in ('Stream','Melt','SwirlFire','SwirlWater','SwirlIce') else 'actual_occurrence_requires_app_rejection'}
            for i,reaction in enumerate(listener['reactionTypes'])]

    def check_scenario(self):
        self.fields(self.scenario,(*CONSTANT_SCENE,'target_id','status','source','accepted_assumptions_fingerprint'),(),'/scenario')
        for key,value in CONSTANT_SCENE.items():
            self.exact(self.scenario[key],value,'/scenario/'+key)
        if self.scenario['status'] not in ('confirmed','assumed'):
            self.fail('scene_pending','/scenario/status','场景仍有未明确输入。')
        for key,limit in (('target_id',128),('source',4096)):
            value=self.scenario[key]
            if not isinstance(value,str) or not value.strip() or len(value)>limit:
                self.fail('scene_pending','/scenario/'+key,'需要有限非空场景值。')

    def materialize(self):
        part_id='upstream.reaction_em'
        source_id=self.source_id[:-len(':upstream')]+':'+part_id
        rid='upstream.'+_hash({'source_id':source_id,'profile':_hash(self.profile)})[:24]
        rule={'id':rid,'name_zh':'反应触发全队精通','owner':self.owner,'source_id':source_id,
            'trigger':{'event':'elemental_reaction','phase':'post','characters':[self.owner],
                'reaction_types':['melt','vaporize','swirl'],'reaction_elements':['pyro','hydro','cryo'],'owner_presence':'frontline'},
            'beneficiaries':{'mode':'team','scope':'locked'},
            'timing':{'delay':0,'duration':self.duration,'cooldown':0,'cooldown_anchor':'success'},
            'stacking':{'max':1,'add':1,'policy':'refresh'},
            'effects':[{'id':'value','benefit_type_id':'benefit.em.all','value':self.value,'domain':'provider'}],
            'status':self.scenario['status'],'source':self.scenario['source']}
        self.rules=[rule]
        self.initial=[{'rule_id':rid,'cooldown_remaining':0,'instances':[]}]
        part=self.part({'kind':'mechanics','rule_id':rid},{'kind':'mechanics'},rule['name_zh'])
        part['part_id']=part_id
        part['conditions'][0]['evidence'][0].update(profile_id=PROFILE_ID, profile_version=PROFILE_VERSION)
        self.parts=[part]


def compile_reaction_em_source(bundle,*,owner,source_id,scenario,period):
    out={'compiler':COMPILER_ID,'compiler_version':1,'profile_id':PROFILE_ID,'profile_version':PROFILE_VERSION,
         'status':'pending','execution_fingerprint':None,'rules':[],'parts':[],'static_parts':[],
         'initial_rules':[],'unique_keys':[],'attribute_writes':[],
         'execution_authority':'requires_actual_app_scope','order_authority':'no_cross_listener_order',
         'execution_requirements':copy.deepcopy(REQUIREMENTS),'whole_source_status':'partial',
         'required_confirmation':{'accepted_assumptions_fingerprint':None}}
    try:
        c=_EMCompiler(bundle,owner,source_id,scenario,period)
    except (OSError,ValueError,TypeError,RecursionError) as exc:
        out.update(status='invalid',issues=[{'code':'invalid_source_profile','severity':'error',
                   'message':str(exc)[:1024],'path':'/profile'}],provenance={},branches=[],raw_sources=[])
        return out
    try:
        c.validate_profile()
        raw=json.dumps([bundle,owner,source_id,scenario,period],ensure_ascii=False,allow_nan=False)
        if len(raw.encode('utf8'))>MAX_BYTES:
            raise ValueError('input_byte_limit')
        stack,count=[([bundle,owner,source_id,scenario,period],0)],0
        while stack:
            value,depth=stack.pop(); count+=1
            if count>32768 or depth>64:
                raise ValueError('input_tree_limit')
            if isinstance(value,dict) and any(not isinstance(k,str) for k in value):
                raise ValueError('invalid_json_key')
            stack.extend((v,depth+1) for v in (value.values() if isinstance(value,dict) else value if isinstance(value,list) else ()))
        scope={k:v for k,v in scenario.items() if k!='accepted_assumptions_fingerprint'}
        fp=_hash({'compiler':COMPILER_ID,'bundle':bundle,'profile':c.profile,'owner':owner,'source_id':source_id,'period':period,'scenario':scope})
        out['required_confirmation']['accepted_assumptions_fingerprint']=fp
        c.build()
        c.check_scenario()
        if scenario['accepted_assumptions_fingerprint']!=fp:
            c.fail('semantic_profile_acceptance_required','/scenario/accepted_assumptions_fingerprint','需接受当前完整来源与场景；App仍须验证实际轴范围。')
        c.materialize()
    except _Stop:
        pass
    except (ValueError,TypeError,KeyError,IndexError,AttributeError,OverflowError,RecursionError) as exc:
        c.issues.append({'code':'invalid_source','severity':'error','message':str(exc)[:1024],'path':'/'})
    safe_profile=c.profile if c.profile_valid else {}
    out.update(status='invalid' if any(i.get('severity')=='error' for i in c.issues) else 'pending' if c.issues else 'ready',
        issues=c.issues[:128],branches=c.branches,raw_sources=c.raw_sources,
        provenance={'profile_fingerprint':_hash(safe_profile),'parameters':c.parameter_sources,'field_usage':c.usage,
                    'evidence':copy.deepcopy(safe_profile.get('evidence',{})),
                    'parent_evidence':copy.deepcopy(safe_profile.get('parent_evidence',[])),
                    'source_facts':copy.deepcopy(safe_profile.get('facts',{}))})
    if out['status']=='ready':
        out.update(rules=c.rules,parts=c.parts,initial_rules=c.initial,unique_keys=c.unique_keys)
        out['attribute_writes']=[{'effect_ref':{'kind':'rule','rule_id':c.rules[0]['id'],'effect_id':'value'},
            'part_id':c.parts[0]['part_id'],'provider':owner,'stat':'em','nonextra_inclusion':'included',
            'classification_authority':'this_admitted_source_fact','fact_fingerprint':_hash(c.profile['facts']['nonextra_inclusion'])}]
        out['execution_fingerprint']=_hash({'accepted_source':out['required_confirmation'],'rules':c.rules,'attribute_writes':out['attribute_writes']})
    if len(json.dumps(out,ensure_ascii=False,allow_nan=False).encode())>MAX_BYTES:
        out.update(status='invalid',rules=[],parts=[],static_parts=[],initial_rules=[],unique_keys=[],attribute_writes=[],
                   raw_sources=[],branches=[],provenance={},execution_fingerprint=None,
                   issues=[{'code':'output_limit','severity':'error','message':'完整回执超过限额，未交付部分来源。'}])
    return copy.deepcopy(out)
