"""Versioned attribute rows copied from actual receipts, never recomputed."""
import json
import math

from genshin_app.equipment.schema import PlanError

STATS = {'em': '元素精通', 'atk': '攻击力', 'hp': '最大生命', 'def': '防御力',
         'energy_recharge': '充能效率（1表示100%）'}
BASES = {'total': '总量', 'nonextra': '非额外量', 'base': '基础量'}
PHASES = {'initial': '初始状态', 'pre': '事件前', 'post': '事件后',
          'reaction_created': '反应产生时', 'reaction_damage_pre': '反应出伤前',
          'capture': '面板取样', 'trigger': '触发取样', 'damage_projection': '出伤属性投影'}
STATUSES = {'known': '已知', 'pending': '待核实', 'invalid': '无效'}


def text_cell(value):
    if value is not None and not isinstance(value, str):
        raise PlanError('属性账显示身份须为文本或空值', 'invalid_review_input')
    return value


def number_cell(value):
    if value is not None:
        valid = type(value) in (int, float)
        try:
            valid = valid and math.isfinite(value)
        except OverflowError:
            valid = False
        if not valid:
            raise PlanError('属性账显示数值须为有限数值或空值', 'invalid_review_input')
    return value


def label(mapping, value):
    return mapping.get(text_cell(value), value)


def seconds(frame):
    if 't_us' not in frame:
        return None
    value = frame['t_us']
    valid = type(value) is int and value >= 0
    try:
        valid = valid and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise PlanError('属性流水时间须为非负有限整数微秒', 'invalid_review_input')
    return value / 1_000_000


def attribute_stat_rows(result):
    ledger = result.get('attribute_ledger') or {}
    if not isinstance(ledger, dict):
        raise PlanError('属性账须为对象', 'invalid_review_input')
    if ledger.get('contract') not in ('attribute-ledger-v3', 'attribute-ledger-v4'):
        return None
    try:
        encoded = json.dumps(ledger, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (ValueError, TypeError, RecursionError) as exc:
        raise PlanError('属性账必须为有限JSON', 'invalid_review_input') from exc
    receipts = ledger.get('receipts', [])
    if not isinstance(receipts, list):
        raise PlanError('属性回执须为列表', 'invalid_review_input')
    rows = [['属性取样账', '实际只读回执；属性、取样域与来源窗口分开。初始总面板已含的组成不会再加一次。取样不代表已计入伤害。修改相关参数须回App重新计算。'],
            ['回执标识', '角色', '属性', '取值口径', '时刻（秒）', '来源窗口（0为完整输入）',
             '阶段', '状态', '初始基底', '实际取值', '来源组件数']]
    percentages = []
    for receipt in receipts:
        if not isinstance(receipt, dict) or not isinstance(receipt.get('query'), dict):
            raise PlanError('属性回执及查询须为对象', 'invalid_review_input')
        query = receipt.get('query', {}); frame = query.get('frame', {})
        if not isinstance(frame, dict) or not isinstance(receipt.get('contributions', []), list):
            raise PlanError('属性帧须为对象，贡献须为列表', 'invalid_review_input')
        rows.append([text_cell(receipt.get('receipt_id')), text_cell(query.get('actor')), label(STATS, query.get('stat')),
                     label(BASES, query.get('basis')), seconds(frame), number_cell(frame.get('origin')),
                     label(PHASES, frame.get('phase')), label(STATUSES, receipt.get('status')),
                     number_cell(receipt.get('baseline')), number_cell(receipt.get('value')), len(receipt.get('contributions', []))])
        for component in receipt.get('contributions', []):
            if (not isinstance(component, dict) or not isinstance(component.get('application', {}), dict)
                    or not isinstance(component.get('component', {}), dict)):
                raise PlanError('属性贡献、来源组件及应用方式须为对象', 'invalid_review_input')
            if (component.get('application') or {}).get('kind') != 'recipient_base_fraction':
                continue
            source = component.get('component') or {}
            percentages.append([text_cell(receipt.get('receipt_id')), text_cell(component.get('id')), text_cell(query.get('actor')),
                label(STATS, component.get('stat')), text_cell(source.get('provider')),
                text_cell(source.get('part_id')), text_cell(source.get('effect_id')), number_cell(component.get('raw_evaluated_value')),
                number_cell(component.get('application_base_value')), number_cell(component.get('evaluated_value')),
                text_cell(component.get('input_receipt_id')), text_cell(component.get('application_base_receipt_id'))])
    if percentages:
        rows += [[], ['比例加算实际明细', '比例、受益者基础攻击力和乘后平值均来自实际回执。不会把乘后平值再当比例计算；空值表示未获得成功求值证据。'],
                 ['总量回执', '贡献标识', '受益角色', '属性', '提供者', '来源部件', '收益标识',
                  '实际比例（1为100%）', '使用的基础攻击力', '乘后平值', '表达式输入回执', '基础攻击力回执'], *percentages]
    rows += [[], ['完整只读属性账', '以下有序分块可重建原始JSON，数值未截断。']]
    rows += [[f'原文块 {i//480+1}', encoded[i:i+480]] for i in range(0, len(encoded), 480)]
    return rows
