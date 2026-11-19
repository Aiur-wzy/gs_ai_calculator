"""Read-only resource intervals from committed rows; no game-state simulation."""
import copy
from collections import defaultdict

from genshin_engine.rotation_port import TICKS
from genshin_engine.timed_resource_window import resource_quantity


def project_resource_intervals(trace, execution_limit):
    groups = defaultdict(list)
    states, changes, issues = [], [], []
    limit = round(execution_limit*TICKS)
    for index, row in enumerate(trace or ()):
        if not isinstance(row, dict) or row.get('resource_type') != 'periodic_resource' or 'count' not in row:
            continue
        tick = row.get('time_tick')
        if row.get('status') != 'executed' or type(tick) is not int or not 0 <= tick <= limit:
            continue
        if (not row.get('owner') or not row.get('area_id') or not resource_quantity(row['count'])
                or row['count'] < 0 or row.get('mode') not in ('none', 'overflow_consume', 'extended_generation')):
            issues.append({'code':'resource_observation_incomplete', 'trace_index':index})
            continue
        groups[(row['owner'],row['area_id'])].append((index,row))
    for (owner,area), entries in sorted(groups.items()):
        entries.sort(key=lambda pair:(pair[1]['time_tick'],pair[0]))
        for pos,(index,row) in enumerate(entries):
            begin=row['time_tick']
            next_tick=entries[pos+1][1]['time_tick'] if pos+1<len(entries) else limit
            boundary={'time_tick':begin,'trace_index':index,'event_id':row['event_id'],'order':index}
            common={'reaction_type':'stellar_superconduct','owner':owner,'area_id':area,'target_id':None,
                    'scope':'owner','instance_id':f'resource:{owner}:{area}',
                    'status':'active','knowledge':'known','start':begin/TICKS,'start_boundary':boundary,
                    'source_refs':[{'trace_index':index,'event_id':row['event_id']}],
                    'cause':row['operation'],'counters':copy.deepcopy(row.get('counters',{}))}
            if row.get('count_mode') == 'expected':
                common['count_mode'] = 'expected'
            change={**copy.deepcopy(common),'id':f'resource:{owner}:{area}:{index}:change',
                    'kind':'resource_inventory','t':begin/TICKS,'count':row['count'],
                    'amount':row.get('amount'), 'mode':row['mode']}
            if 'trial' in row:
                change['trial']=copy.deepcopy(row['trial'])
            changes.append(change)
            for kind,value,expires in (
                    ('resource_inventory',{'count':row['count']},row.get('expires_at_tick')),
                    ('resource_mode',{'mode':row['mode']},row.get('mode_until_tick'))):
                if kind=='resource_mode' and row['mode']=='none':
                    continue
                end=next_tick
                reason='resource_change' if pos+1<len(entries) else 'execution_limit'
                if type(expires) is int and expires<end:
                    end=expires
                    reason='declared_expiry_unobserved'
                    issues.append({'code':reason,'trace_index':index,'kind':kind})
                if end>begin:
                    states.append({**copy.deepcopy(common),'id':f'resource:{owner}:{area}:{kind}:{index}',
                                   'kind':kind,**value,'end':end/TICKS,'end_reason':reason,
                                   'expires_at':expires/TICKS if type(expires) is int else None})
    return states,changes,issues
