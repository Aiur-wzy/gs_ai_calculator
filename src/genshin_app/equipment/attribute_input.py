"""Shape checks and deterministic identity preview, without classification approval."""
from __future__ import annotations

import copy
import json

from genshin_engine.attribute_budget import only_attribute_capacity_issues
from genshin_engine.attribute_contract import attribute_model_schema
from genshin_engine.execution_control import ExecutionCancelled
from genshin_engine.rotation import _schema_errors

from .schema import PlanError


def validate_author_attribute_model(model, *, limits=None):
    issues = []
    try:
        json.dumps(model, allow_nan=False)
        _schema_errors(model, attribute_model_schema(author=True, limits=limits), 'mechanics_model.attribute_model', issues)
    except ExecutionCancelled:
        raise
    except (TypeError, ValueError, RecursionError, OverflowError) as exc:
        raise PlanError('属性声明必须是有限JSON', 'invalid_attribute_source') from exc
    if issues:
        reason = 'attribute_declaration_limit' if only_attribute_capacity_issues(issues) else 'invalid_attribute_source'
        message = '属性声明超出本次计算预算，请调整预算后重新计算' if reason=='attribute_declaration_limit' else '属性声明结构不完整，请按精确字段补齐'
        raise PlanError(message, reason, issues)


def resolve_component(row, expected, path, missing, *, active=True):
    """Only a unique, actual effect_ref may supply a missing author identity."""
    supplied = row.get('component')
    keys = ('provider', 'part_id', 'effect_id')
    if supplied is not None and any(supplied[key] != expected[key] for key in keys):
        raise PlanError('属性分类的提供者、来源部件或收益身份不符', 'invalid_attribute_source', {'path':path})
    if supplied != expected and active:
        message = ('属性来源指纹已变化，须核对当前预览'
                   if supplied is not None and 'source_fingerprint' in supplied else
                   '来源身份待核对，请预览后明确采用；分类与知识状态不会自动提升')
        missing('attribute_source_stale', path, message, 'review_attributes')
    # This is an execution working copy. The original native input stays unchanged.
    # Existing explicit fingerprints remain evidence of stale input. Only an
    # omitted identity is materialized for the preview, never silently adopted.
    if supplied is None or 'source_fingerprint' not in supplied:
        row['component'] = copy.deepcopy(expected)


def adopt_preview_components(config, records):
    by_path = {row['path']:row['expected_component'] for row in records}
    for index, row in enumerate(config['effect_classifications']):
        path = f'mechanics_model.attribute_model.effect_classifications[{index}].component'
        if path not in by_path:
            raise PlanError('属性分类未能解析到唯一实际来源', 'invalid_attribute_source', {'path':path})
        row['component'] = copy.deepcopy(by_path[path])
    for index, basis in enumerate(config['initial_basis']):
        for position, row in enumerate(basis['included_components']):
            path = f'mechanics_model.attribute_model.initial_basis[{index}].included_components[{position}].component'
            if path not in by_path:
                raise PlanError('已含属性组件未能解析到实际来源', 'invalid_attribute_source', {'path':path})
            row['component'] = copy.deepcopy(by_path[path])
