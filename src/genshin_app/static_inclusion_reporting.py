"""Present admitted, already included properties without applying them again."""
from __future__ import annotations

import json

STAT_LABELS = {
    'base_atk': '基础攻击力', 'atk': '攻击力', 'hp': '生命值', 'def': '防御力',
    'crit_rate': '暴击率', 'crit_dmg': '暴击伤害', 'energy_recharge': '元素充能效率',
    'em': '元素精通', 'dmg_bonus:all': '伤害加成',
    **{f'dmg_bonus:{key}': value + '伤害加成' for key, value in (
        ('physical', '物理'), ('pyro', '火元素'), ('hydro', '水元素'), ('cryo', '冰元素'),
        ('electro', '雷元素'), ('anemo', '风元素'), ('geo', '岩元素'), ('dendro', '草元素'))},
}


def has_static_inclusion_signal(result, receipt, *, plan=None, report=None):
    """Do not bypass validation when only an adoption marker survives an edit."""
    if isinstance(receipt, dict) and 'static_inclusions' in receipt:
        return True
    containers = [result, report]
    for native in (plan, (report or {}).get('plan') if isinstance(report, dict) else None):
        if isinstance(native, dict):
            containers.append(native.get('resolution'))
    for item in containers:
        if not isinstance(item, dict):
            continue
        if any(key in item for key in ('static_inclusion_compilations', 'static_inclusion_adoption')):
            return True
        for req in item.get('effect_requirements') or []:
            if isinstance(req, dict) and isinstance(req.get('resolved_by'), dict) and req['resolved_by'].get('kind') == 'static_inclusion':
                return True
    return False


def static_inclusion_rows(result):
    records = result.get('static_inclusion_compilations') or []
    if not records:
        return []
    encoded = json.dumps(records, ensure_ascii=False, sort_keys=True, allow_nan=False)
    rows = [['面板已含来源', '只读：静态属性已包含在用户给定面板中；本页不再次增加数值。'],
            ['核验范围', '本次装配重新读取数据库并核对所选套装；用户负责给定面板本身的数值。'],
            ['持续时间', '常驻面板来源，无独立触发时点或持续Buff；四件套条件效果另行处理。'],
            ['修改后', '更换装备、面板或采用政策后，回App重新计算。此页不授权动态属性分类。'], [],
            ['提供者', '套装来源', '件数门槛', '属性', '数值', '单位', '采用状态', '来源ID',
             '效果绑定ID', '原始属性索引', '完整说明']]
    for record in records:
        declaration, display = record['declaration'], record.get('source_display', {})
        for prop in declaration['properties']:
            rows.append([record['provider_character_id'], display.get('name') or str(declaration['set_id']),
                         declaration['required_count'], STAT_LABELS.get(prop['stat'], prop['stat']),
                         prop['value'], '比例（1为100%）' if prop['unit'] == 'ratio' else '固定值',
                         '已计入初始面板' if record['status'] == 'ready' else '待核实，尚未自动采用',
                         record['id'], record['binding_id'], prop['source_index'], display.get('description', '')])
    rows += [[], ['完整静态来源记录（按序拼接JSON）']]
    rows.extend([encoded[offset:offset + 480]] for offset in range(0, len(encoded), 480))
    return rows


def static_inclusion_projection(record):
    """Stable query identity; a static property has no invented hit or duration."""
    declaration = record['declaration']
    return {'id': record['id'], 'source_id': record['id'], 'character': record['provider_character_id'],
            'status': record['status'], 'kind': 'already_in_given_panel',
            'name_zh': record.get('source_display', {}).get('name') or str(declaration['set_id'])}
