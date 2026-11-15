"""Shared authoring constants for generic periodic handler v2."""
STRICT_COUNT_POLICY = 'through_instance_end_inclusive'
WHILE_ACTIVE_POLICY = 'while_active_inclusive'
DERIVED_COUNT_POLICIES = frozenset({STRICT_COUNT_POLICY, WHILE_ACTIVE_POLICY})
PERIODIC_FIELDS = frozenset({'profile_id', 'channel_id', 'schedule', 'lifecycle',
    'establishments', 'controls', 'on_field_intervals', 'dispatches'})
EQUAL_SCHEDULE_FIELDS = frozenset({'kind', 'schedule_id', 'first_offset_seconds',
    'interval_seconds', 'count', 'derive_count', 'order'})
EXPLICIT_SCHEDULE_FIELDS = frozenset({'kind', 'schedule_id', 'occurrences'})
LIFECYCLE_FIELDS = frozenset({'duration_seconds', 'refresh_policy', 'off_field_policy', 'pause_resume_policy'})
ESTABLISHMENT_FIELDS = frozenset({'establishment_id', 'anchor_node_id', 'order'})
CONTROL_FIELDS = frozenset({'control_id', 'operation', 'anchor_node_id', 'order', 'duration_seconds'})
INTERVAL_FIELDS = frozenset({'interval_id', 'start_node_id', 'end_node_id'})
DISPATCH_FIELDS = frozenset({'dispatch_id', 'emits_node_ids'})
OCCURRENCE_FIELDS = frozenset({'occurrence_id', 'offset_seconds', 'order'})
