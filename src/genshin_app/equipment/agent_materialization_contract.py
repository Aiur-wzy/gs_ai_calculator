"""Shared public envelope shape; payload semantics remain handler-owned."""
ROOT_FIELDS = frozenset({'contract_id', 'schema_version', 'materialization_id', 'authority',
    'plan_binding', 'topology_binding', 'handler_set_fingerprint', 'sources', 'entries'})
SOURCE_FIELDS = frozenset({'source_id', 'kind', 'reference', 'fingerprint', 'status', 'note'})
ENTRY_FIELDS = frozenset({'entry_id', 'family', 'handler_version', 'node_ids', 'source_ids',
    'coverage_keys', 'payload', 'note'})


def periodic_payload_schema():
    """Exact periodic@2 shape; semantic axis checks remain handler-owned."""
    from . import agent_execution_materialization as m
    from .periodic_contract import (
        CONTROL_FIELDS,
        DERIVED_COUNT_POLICIES,
        DISPATCH_FIELDS,
        EQUAL_SCHEDULE_FIELDS,
        ESTABLISHMENT_FIELDS,
        EXPLICIT_SCHEDULE_FIELDS,
        INTERVAL_FIELDS,
        LIFECYCLE_FIELDS,
        OCCURRENCE_FIELDS,
        PERIODIC_FIELDS,
    )
    def obj(properties, fields):
        return {'type': 'object', 'properties': properties, 'required': sorted(fields), 'additionalProperties': False}
    identity = {'type': 'string', 'pattern': m._ID.pattern}
    seconds = {'type': 'number', 'minimum': 0, 'maximum': m.MAX_SECONDS,
        'description': 'Exact integer microseconds: value*1000000 must be integral under the handler precision check; no rounding to align lifecycle end.'}
    positive = {**seconds, 'exclusiveMinimum': 0}
    order = {'type': 'integer', 'minimum': -(10**9), 'maximum': 10**9}
    def array(item, maximum, minimum=0):
        return {'type': 'array', 'items': item, 'minItems': minimum, 'maxItems': maximum}
    occurrence = obj({'occurrence_id': identity, 'offset_seconds': seconds, 'order': order}, OCCURRENCE_FIELDS)
    equal = obj({'kind': {'const': 'equal_interval'}, 'schedule_id': identity,
        'first_offset_seconds': seconds, 'interval_seconds': positive,
        'count': {'type': ['integer', 'null'], 'minimum': 1, 'maximum': m.MAX_PERIODIC_OCCURRENCES},
        'derive_count': {'enum': [None, *sorted(DERIVED_COUNT_POLICIES)]}, 'order': order}, EQUAL_SCHEDULE_FIELDS)
    equal['oneOf'] = [{'properties': {'count': {'type': 'integer'}, 'derive_count': {'type': 'null'}}},
        {'properties': {'count': {'type': 'null'}, 'derive_count': {'enum': sorted(DERIVED_COUNT_POLICIES)}}}]
    explicit = obj({'kind': {'const': 'explicit_offsets'}, 'schedule_id': identity,
        'occurrences': array(occurrence, m.MAX_PERIODIC_OCCURRENCES, 1)}, EXPLICIT_SCHEDULE_FIELDS)
    life = obj({'duration_seconds': seconds, 'refresh_policy': {'enum': sorted(m._PERIODIC_REFRESH_POLICIES)},
        'off_field_policy': {'enum': sorted(m._OFF_FIELD_POLICIES)},
        'pause_resume_policy': {'enum': sorted(m._PERIODIC_PAUSE_POLICIES)}}, LIFECYCLE_FIELDS)
    establish = obj({'establishment_id': identity, 'anchor_node_id': identity, 'order': order}, ESTABLISHMENT_FIELDS)
    control = obj({'control_id': identity, 'operation': {'enum': sorted(m._PERIODIC_CONTROL_OPERATIONS)},
        'anchor_node_id': identity, 'order': order, 'duration_seconds': {'anyOf': [positive, {'type': 'null'}]}}, CONTROL_FIELDS)
    control['allOf'] = [{'if': {'properties': {'operation': {'const': 'extend_duration'}}},
        'then': {'properties': {'duration_seconds': positive}},
        'else': {'properties': {'duration_seconds': {'type': 'null'}}}}]
    interval = obj({'interval_id': identity, 'start_node_id': identity, 'end_node_id': identity}, INTERVAL_FIELDS)
    emitted = array(identity, m.MAX_EMISSIONS, 1); emitted['uniqueItems'] = True
    dispatch = obj({'dispatch_id': identity, 'emits_node_ids': emitted}, DISPATCH_FIELDS)
    schema = obj({'profile_id': identity, 'channel_id': identity, 'schedule': {'oneOf': [equal, explicit]},
        'lifecycle': life, 'establishments': array(establish, 256, 1), 'controls': array(control, 512),
        'on_field_intervals': array(interval, 512), 'dispatches': array(dispatch, 64, 1)}, PERIODIC_FIELDS)
    schema['allOf'] = [{'if': {'properties': {'lifecycle': {'properties': {'off_field_policy': {'const': 'stop'}}}}},
        'then': {'properties': {'on_field_intervals': {'minItems': 1}}},
        'else': {'properties': {'on_field_intervals': {'maxItems': 0}}}}]
    schema['description'] = ('Unique occurrence IDs and monotonic time/order; unique establishment/control IDs; '
        'each dispatch emits unique nodes across the whole group. Exact microseconds only. '
        'Strict derived count requires aligned effective end; while_active_inclusive uses floor. '
        'Topology/source/axis ownership and actual-window validation remain required by the handler.')
    return schema


def materialization_input_schema():
    from . import agent_execution_materialization as m

    def obj(properties, fields=None):
        return {'type': 'object', 'properties': properties,
                'required': sorted(fields if fields is not None else properties), 'additionalProperties': False}

    identity = {'type': 'string', 'pattern': m._ID.pattern}
    digest = {'type': 'string', 'pattern': m._HASH.pattern}
    text = {'type': 'string', 'minLength': 1, 'maxLength': m.MAX_TEXT}
    note = {'type': 'string', 'maxLength': m.MAX_TEXT}

    def ids(maximum):
        return {'type': 'array', 'items': identity, 'minItems': 1,
                'maxItems': maximum, 'uniqueItems': True}

    source = obj({'source_id': identity, 'kind': {'enum': sorted(m._SOURCE_KINDS)},
        'reference': text, 'fingerprint': digest, 'status': {'enum': ['verified', 'user_provided']},
        'note': note}, SOURCE_FIELDS)
    entry = obj({'entry_id': identity, 'family': {'enum': sorted(m.HANDLER_REGISTRY)},
        'handler_version': {'type': 'integer', 'minimum': 1},
        'node_ids': ids(m.MAX_NODE_REFS), 'source_ids': ids(m.MAX_SOURCE_REFS),
        'coverage_keys': ids(m.MAX_COVERAGE_KEYS), 'payload': {'type': 'object',
            'description': 'Use the exact family/version payload and runtime capability; handler validation is authoritative.'},
        'note': note}, ENTRY_FIELDS)
    entry['allOf'] = [{'if': {'properties': {'family': {'const': 'periodic_channel'}}},
        'then': {'properties': {'handler_version': {'enum': list(m.HANDLER_REGISTRY['periodic_channel'].supported_versions)},
                               'payload': periodic_payload_schema()}}}]
    return obj({'contract_id': {'const': m.CONTRACT_ID}, 'schema_version': {'const': m.SCHEMA_VERSION},
        'materialization_id': identity, 'authority': {'const': m.AUTHORITY},
        'plan_binding': obj({'plan_id': text, 'revision': {'type': 'integer', 'minimum': 0},
                             'input_fingerprint': digest}),
        'topology_binding': obj({'topology_id': identity, 'topology_fingerprint': digest,
                                 'action_checkpoint_fingerprint': digest}),
        'handler_set_fingerprint': digest,
        'sources': {'type': 'array', 'items': source, 'maxItems': m.MAX_SOURCES},
        'entries': {'type': 'array', 'items': entry, 'maxItems': m.MAX_ENTRIES}}, ROOT_FIELDS)
