"""Author facts and complete runtime facts share fields, never source authority."""
from __future__ import annotations

import copy

from genshin_engine.attribute_budget import (
    limits_for_schema,
    normalize_attribute_budget,
)
from genshin_engine.attribute_multistat_native import NATIVE_SCHEMA_V4
from genshin_engine.attribute_runtime_schema import ATTRIBUTE_MODEL_SCHEMA
from genshin_engine.attribute_stat_native import NATIVE_SCHEMA


def attribute_model_schema(*, author=False, limits=None):
    schema = {'oneOf': [copy.deepcopy(ATTRIBUTE_MODEL_SCHEMA), copy.deepcopy(NATIVE_SCHEMA), copy.deepcopy(NATIVE_SCHEMA_V4)]}
    for branch in schema['oneOf']:
        classification = branch['properties']['effect_classifications']['items']
        if author:
            classification['required'].remove('component')
        else:
            classification['properties']['component']['required'].append('source_fingerprint')
            def require_fingerprint(node):
                if isinstance(node, dict):
                    if node.get('properties', {}).keys() >= {'component', 'value', 'nonextra_inclusion'}:
                        component = node['properties']['component']
                        if 'source_fingerprint' not in component['required']:
                            component['required'].append('source_fingerprint')
                    for child in node.values():
                        require_fingerprint(child)
                elif isinstance(node, list):
                    for child in node:
                        require_fingerprint(child)
            require_fingerprint(branch)
    return limits_for_schema(schema, normalize_attribute_budget(limits)['effective'])
