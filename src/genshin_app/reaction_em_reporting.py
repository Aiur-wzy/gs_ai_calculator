"""Read-only source and occurrence rows; never reconstruct missing game state."""
from __future__ import annotations

import json


def reaction_em_rows(result):
    records = result.get('reaction_em_compilations') or []
    if not records:
        return []
    proof = result.get('reaction_em_execution') or {}
    # Reject non-finite or non-JSON evidence before a workbook flattens it.
    encoded = json.dumps({'sources': records, 'execution': proof}, ensure_ascii=False,
                         sort_keys=True, allow_nan=False)
    rows = [['反应触发精通来源', '只读：来自本次实际编译和执行；改动装备、面板或动作后请回App重新计算。'],
            ['适用范围', '普通原目标融化、蒸发、扩散；提供者须在场触发。增益在触发该次伤害之后生效。'],
            ['持续规则', '全队精通按来源持续；再次合格反应刷新，不叠加；退场不清除已经授予的增益。'],
            ['边界', '当前来源投影不认证未列出的反应、范围、同次转换取样或游戏帧序。'], [],
            ['来源', '提供者', '规则', '效果', '数值', '持续（秒）', '状态']]
    for record in records:
        for rule in record['rules']:
            for effect in rule['effects']:
                rows.append([record['binding_id'], rule['owner'], rule['name_zh'], '全队元素精通',
                             effect['value'], rule['timing']['duration'], record['execution_status']])
    rows += [[], ['实际触发', '提供者', '目标', '来源轮次', '时刻（秒）', '反应', '附着父发生', '反应发生']]
    labels = {'melt': '融化', 'vaporize': '蒸发', 'swirl': '扩散'}
    for row in proof['activations']:
        rows.append([row['rule_id'], row['owner'], row['target_id'], row['source_window'], row['t'],
                     labels[row['reaction_type']], row['parent_run_id'], row['reaction_run_id']])
    rows += [[], ['完整来源及实际发生证明（按序拼接JSON）']]
    rows.extend([encoded[offset:offset + 480]] for offset in range(0, len(encoded), 480))
    return rows
