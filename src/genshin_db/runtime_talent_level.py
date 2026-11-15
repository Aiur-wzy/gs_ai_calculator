"""Static constellation level projection, distinct from complete skill execution."""
from __future__ import annotations

import copy
import json
import math
from functools import lru_cache
from importlib import resources

from genshin_db.runtime_catalog import RuntimeConfigError, dumps, fingerprint
from genshin_db.validation import QueryValidationError

LIMITS = {'max_bytes':262144, 'max_operations':128, 'max_issues':128}


def _finite_number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


@lru_cache(maxsize=1)
def _recipe():
    value=json.loads(resources.files('genshin_runtime').joinpath('resources/talent_level_profiles.json').read_text('utf8'))
    value['recipe_fingerprint']=fingerprint(value)
    return value


def talent_level_capabilities():
    p=_recipe()
    return {'contract':p['recipe_id'],'recipe_version':p['recipe_version'],'recipe_fingerprint':p['recipe_fingerprint'],
            'limits':dict(LIMITS),'base_level':{'default':10,'minimum':1,'maximum':10},
            'scope':p['scope'],'selection':'slot与skill_id二选一；只取明确所选技能组前N命座，按实际操作目标加级，不按C3/C5猜测。',
            'execution_support':'static_level_only','legacy_action_facts':'unchanged'}


def get_runtime_talent_level(db_path, *, character_id, slot=None, skill_id=None, skill_depot_id=None,
                             constellation=0, base_level=10, version_line='release', snapshot_id=None):
    from genshin_db.runtime_queries import (
        _definition_summary,
        _integer,
        _lookup,
        _Nodes,
        _read,
    )

    _integer(character_id,'character_id'); _integer(constellation,'constellation',0,6); _integer(base_level,'base_level',1,10)
    if (slot is None)==(skill_id is None):raise QueryValidationError('slot与skill_id必须且只能提供一个')
    if slot is not None and (not isinstance(slot,str) or slot not in ('normal','skill','burst')):raise QueryValidationError('slot不支持')
    for value,name in [(skill_id,'skill_id'),(skill_depot_id,'skill_depot_id')]:
        if value is not None:_integer(value,name)
    p=_recipe()
    with _read(db_path,version_line,snapshot_id) as (conn,state):
        nodes=_Nodes(conn,version_line); issues=[]; records={}; contributions=[]; other=[]; count=0

        def issue(code, **details):
            if len(issues)>=LIMITS['max_issues']:raise RuntimeConfigError('加级问题数量超限','response_too_large',413)
            issues.append({'code':code,**details})

        def materialize(row):
            raw,origin=nodes.node(row)
            return {'definition':_definition_summary(row),**origin,'node_sha256':fingerprint(raw),'record':copy.deepcopy(raw)}

        def exact(table,key,*,required=False):
            path=f'ExcelBinOutput/{table}ExcelConfigData.json'
            rows=_lookup(conn,version_line,'excel_record',key,path)
            if len(rows)!=1:
                if required:raise RuntimeConfigError('资料入口不存在或不唯一','not_found' if not rows else 'ambiguous_reference',404 if not rows else 409,{'table':table,'key':key})
                issue('missing_record' if not rows else 'ambiguous_record',table=table,key=key);return None
            return materialize(rows[0])

        def ids(value,path):
            if not isinstance(value,list) or any(type(v) is not int or v<0 for v in value):
                issue('invalid_id_list',path=path);return []
            return value

        if state['upstream_commit']!=p['commit']:issue('talent_level_recipe_unverified',commit=state['upstream_commit'])
        avatar=exact('Avatar',{'id':character_id},required=True); records['avatar']=avatar
        av=avatar['record']; candidates=ids(av.get('candSkillDepotIds',[]),'avatar.candSkillDepotIds')
        primary=av.get('skillDepotId')
        if type(primary) is not int or primary<=0:issue('invalid_primary_depot')
        depots=list(dict.fromkeys(v for v in [primary,*candidates] if type(v) is int and v>0))
        if skill_depot_id is None:
            if len(depots)!=1:raise RuntimeConfigError('角色技能组需明确选择','skill_depot_selection_required',409,{'candidate_skill_depot_ids':depots})
            skill_depot_id=depots[0]
        elif skill_depot_id not in depots:raise QueryValidationError('skill_depot_id不属于角色明确列出的技能组')
        depot=exact('AvatarSkillDepot',{'id':skill_depot_id},required=True); records['depot']=depot; dep=depot['record']
        skills=ids(dep.get('skills',[]),'depot.skills'); subskills=ids(dep.get('subSkills',[]),'depot.subSkills')
        energy=dep.get('energySkill',0)
        if type(energy) is not int or energy<0:issue('invalid_energy_skill');energy=0
        slots={'normal':skills[0] if skills else 0,'skill':skills[1] if len(skills)>1 else 0,'burst':energy}
        if slot is not None:
            skill_id=slots[slot]
            if not skill_id:raise RuntimeConfigError('所选技能槽为空','empty_skill_slot',409,{'slot':slot})
        elif skill_id not in [*skills,*subskills,energy]:raise QueryValidationError('skill_id不属于所选技能组')
        selected=exact('AvatarSkill',{'id':skill_id},required=True); records['skill']=selected
        group=selected['record'].get('proudSkillGroupId')
        if type(group) is not int or group<=0:issue('missing_proud_skill_group')
        all_talents=ids(dep.get('talents',[]),'depot.talents')
        active=all_talents[:constellation]
        if len(active)!=constellation or any(not v for v in active):issue('active_constellation_missing')
        if len(set(active))!=len(active):issue('duplicate_constellation_id')
        text_source,text_map=nodes.source('TextMap/TextMap_MediumCHS.json')
        if not isinstance(text_map,dict):raise RuntimeConfigError('中文TextMap形状无效','dataset_invalid',500)

        def text(record,field):
            value=record['record'].get(field); content=text_map.get(str(value)) if type(value) is int and value>0 else None
            return {'status':'ready' if isinstance(content,str) and content else 'pending','text':content if isinstance(content,str) else None,
                    'hash':value,'hash_origin':{'source_path':record['source_path'],'source_sha256':record['source_sha256'],'json_pointer':record['json_pointer']+'/'+field},
                    'text_origin':{'source_path':'TextMap/TextMap_MediumCHS.json','source_sha256':text_source['source_sha256'],
                                   'json_pointer':'/'+str(value),'node_sha256':fingerprint(content)} if content is not None else None}

        extra=0; records['talents']=[]; targets={skill_id:selected}
        for rank,tid in enumerate(active,1):
            if not tid:continue
            talent=exact('AvatarTalent',{'talentId':tid})
            if talent is None:continue
            entry={'rank':rank,**talent,'description':text(talent,'descTextMapHash')}; records['talents'].append(entry)
            config=talent['record'].get('openConfig')
            if not isinstance(config,str) or not config:
                issue('missing_talent_config',talent_id=tid);continue
            found=_lookup(conn,version_line,'talent_config',config)
            if len(found)!=1:
                issue('missing_talent_config' if not found else 'ambiguous_talent_config',talent_id=tid,open_config=config);continue
            definition=found[0]; raw,origin=nodes.node(definition)
            operations=nodes.normalization.normalize(raw,kind='talent_config') if nodes.normalization else raw
            entry['config']={'definition':_definition_summary(definition),**origin,'raw':raw}
            if not isinstance(operations,list):issue('invalid_talent_operations',talent_id=tid);continue
            count+=len(operations)
            if count>LIMITS['max_operations']:raise RuntimeConfigError('加级操作数量超限','response_too_large',413)
            for index,op in enumerate(operations):
                ptr=definition['json_pointer']+'/'+str(index)
                if not isinstance(op,dict):issue('invalid_talent_operation',path=ptr);continue
                if not isinstance(op.get('$type'),str):issue('invalid_talent_operation_type',path=ptr);continue
                value={p['fields'].get(k,k):p['types'].get(v,v) if k=='$type' else v for k,v in op.items()}
                if len(value)!=len(op):issue('talent_field_collision',path=ptr);continue
                kind=value.get('$type'); keyset=set(value)
                op_origin={**origin,'json_pointer':ptr,'node_sha256':fingerprint(raw[index])}
                if kind!='AddTalentExtraLevel':
                    shape=p['non_level_operations'].get(kind)
                    accepted=(shape is not None and keyset==set(shape) and value.get('CLGIKOPIKBC') is True
                              and all(isinstance(value[k],str) and value[k] for k in keyset-{'CLGIKOPIKBC'}))
                    numeric=p.get('non_level_scalar_operations',{}).get(kind)
                    if numeric is not None and keyset==set(numeric)|{'$type','CLGIKOPIKBC'} and value.get('CLGIKOPIKBC') is True:
                        accepted=all((type(value[k]) is int and (spec!='positive_integer' or value[k]>0))
                                     if spec in ('integer','positive_integer') else _finite_number(value[k])
                                     for k,spec in numeric.items())
                    other.append({'rank':rank,'talent_id':tid,'operation':value,'origin':op_origin,'status':'outside_static_level_scope' if accepted else 'pending'})
                    if not accepted:issue('unreviewed_talent_operation',path=ptr,operation_type=kind)
                    continue
                if (keyset!=set(p['level_operation_fields']) or value.get('CLGIKOPIKBC') is not True
                        or value.get('talentType')!='AvatarSkill' or type(value.get('extraLevel')) is not int
                        or not 1<=value['extraLevel']<=15 or type(value.get('talentIndex')) is not int
                        or str(value['talentIndex']) not in p['talent_index_slots']):
                    issue('unreviewed_level_operation',path=ptr,operation=value);continue
                target_slot=p['talent_index_slots'][str(value['talentIndex'])]; target_id=slots[target_slot]
                target=targets.get(target_id)
                if not target_id:issue('missing_level_target',path=ptr,slot=target_slot);continue
                if target is None:
                    target=exact('AvatarSkill',{'id':target_id})
                    if target is None:continue
                    targets[target_id]=target
                target_group=target['record'].get('proudSkillGroupId')
                if type(target_group) is not int or target_group<=0:issue('invalid_level_target_group',path=ptr);continue
                matched=target_group==group
                if matched:extra+=value['extraLevel']
                contributions.append({'rank':rank,'talent_id':tid,'operation':value,'origin':op_origin,'target_slot':target_slot,
                  'target_skill':target,'target_name':text(target,'nameTextMapHash'),'matches_selected_group':matched,'extra_level':value['extraLevel']})
        effective=base_level+extra
        if not 1<=effective<=15:issue('effective_level_out_of_range',value=effective)
        path='ExcelBinOutput/ProudSkillExcelConfigData.json'; _,proud=nodes.source(path)
        if not isinstance(proud,list):raise RuntimeConfigError('ProudSkill形状无效','dataset_invalid',500)
        matches=[r for r in proud if isinstance(r,dict) and type(r.get('proudSkillGroupId')) is int
                 and type(r.get('level')) is int and r['proudSkillGroupId']==group and r['level']==effective]
        if len(matches)!=1:issue('missing_parameter_record' if not matches else 'ambiguous_parameter_record',group_id=group,level=effective)
        else:records['proud_skill']=exact('ProudSkill',{'proudSkillId':matches[0].get('proudSkillId')})
        result={'contract':p['recipe_id'],'recipe_fingerprint':p['recipe_fingerprint'],'scope':'declared_static_constellation_levels',
          'status':'pending' if issues else 'ready','snapshot':state,'selection':{'character_id':character_id,'skill_depot_id':skill_depot_id,
          'skill_id':skill_id,'slot':slot,'matching_slots':[k for k,v in slots.items() if v==skill_id],'constellation':constellation,'active_constellation_ids':active},
          'level':{'base_level':base_level,'extra_level':None if issues else extra,'effective_level':None if issues else effective,
                   'candidate_extra_level':extra,'candidate_effective_level':effective},
          'records':records,'contributions':contributions,'other_operations':other,'issues':issues,'limits':dict(LIMITS),
          'execution_support':'static_level_only','notes':[p['scope'],'参数表输入不是执行ModifyAbility后的最终倍率；其它命座效果和动作派发资格独立核实。']}
        result['content_fingerprint']=fingerprint(result)
        if len(dumps(result).encode('utf8'))>LIMITS['max_bytes']:raise RuntimeConfigError('加级资料响应超限','response_too_large',413)
        return result


def get_level_parameter_context(db_path, **selection):
    """Private same-read materialization, reusable for multiple scalar projections."""
    from genshin_db.runtime_action_parameter import _description_context
    from genshin_db.runtime_queries import _read
    with _read(db_path,selection.get('version_line','release'),selection.get('snapshot_id')) as (conn,state):
        level=get_runtime_talent_level(db_path,**selection)
        record=level['records'].get('proud_skill') if level['status']=='ready' else None
        if record is not None:
            record={**record,'fields':record['record']}
        descriptions=_description_context(conn,state,{'records':{'proud_skill':record}})
        return {'level_profile':level,'record':record,'descriptions':descriptions}


def project_action_parameter_at_level(context, param_index):
    from genshin_db.runtime_action_parameter import _project_descriptions, _raw_scalar
    from genshin_db.runtime_queries import _integer

    _integer(param_index,'param_index',0,63)
    level,record=context['level_profile'],context['record']
    issues,value,source=_raw_scalar(record,param_index)
    if level['status']!='ready':issues.insert(0,{'code':'talent_level_pending','message':'有效等级尚未核实，不采用候选参数表'})
    result={'projection_version':2,'contract':'level-bound-parameter-v1','status':'pending' if issues else 'ready',
      'snapshot':copy.deepcopy(level['snapshot']),'selection':copy.deepcopy(level['selection']),'level':copy.deepcopy(level['level']),'param_index':param_index,
      'scalar_projection':{'status':'pending' if issues else 'ready','value':value,'semantics':'proud-skill-input-scalar-v1','issues':issues},
      'source':source,'level_profile':copy.deepcopy(level),'parameter_descriptions':_project_descriptions(context['descriptions'],param_index),
      'parameter_descriptions_raw':copy.deepcopy(record['fields'].get('paramDescList',[])) if record else [],
      'entry_sources':{k:copy.deepcopy(level['records'][k]) for k in ('avatar','depot','skill')},
      'operation_execution':'not_evaluated','action_dispatch':{'status':'pending'},'gameplay_interpretation':'not_evaluated',
      'scope':'仅由同快照静态命座等级确定原始表参数；不执行其它命座修改，不授权旧C0攻击/收益编译器。',
      'limits':{'max_bytes':524288,'max_param_index':63}}
    result['content_fingerprint']=fingerprint(result)
    if len(dumps(result).encode('utf8'))>result['limits']['max_bytes']:raise RuntimeConfigError('等级参数响应超限','response_too_large',413)
    return result


def get_runtime_action_parameter_at_level(db_path, *, param_index, **selection):
    from genshin_db.runtime_queries import _integer

    _integer(param_index,'param_index',0,63)
    return project_action_parameter_at_level(get_level_parameter_context(db_path,**selection),param_index)
