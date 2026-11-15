"""Pure renderer-safe diagnostic codes; no runtime owner imports."""

import re

NATIVE_RUNTIME_START_DIAGNOSTIC_CAUSES = frozenset({
    "bridge_frame_invalid",
    "bridge_frame_size",
    "bridge_handshake_invalid",
    "bridge_handshake_mismatch",
    "pipe_acl_failed",
    "pipe_already_accepted",
    "pipe_closed",
    "pipe_connect_failed",
    "pipe_connect_timeout",
    "pipe_create_failed",
    "pipe_disconnected",
    "pipe_event_failed",
    "pipe_invalid_handle",
    "pipe_io_failed",
    "pipe_name_invalid",
    "pipe_read_failed",
    "pipe_wait_failed",
    "pipe_write_failed",
    "platform_unsupported",
    "runtime_bootstrap_missing",
    "runtime_bootstrap_write_failed",
    "runtime_capabilities_invalid",
    "runtime_cwd_missing",
    "runtime_entry_missing",
    "runtime_incarnation_invalid",
    "runtime_integrity_failed",
    "runtime_node_missing",
    "runtime_release_entry_outside_root",
    "runtime_release_entry_unpinned",
    "runtime_release_manifest_invalid",
    "runtime_response_sequence",
    "runtime_start_failed",
})
_OFFICIAL_TURN_INTERNAL_STAGE_CAUSES = frozenset({
    # Fixed, path-free checkpoints owned by ``NativeRuntimeProcess``.  These
    # are intentionally enumerated rather than accepting an arbitrary prefix:
    # a plain Python exception may cross this boundary, but its message and
    # locals must never enter the durable GUI diagnostic.
    "runtime_official_turn_internal_initializing",
    "runtime_official_turn_internal_app_tool_execute",
    "runtime_official_turn_internal_app_tool_checkpoint",
    "runtime_official_turn_internal_app_tool_settlement_build",
    "runtime_official_turn_internal_app_tool_settlement_write",
    "runtime_official_turn_internal_app_tool_settlement_read",
    "runtime_official_turn_internal_app_tool_settlement_validate",
    "runtime_official_turn_internal_app_tool_snapshot_rollover",
    "runtime_official_turn_internal_event_projection",
    "runtime_official_turn_internal_turn_request_write",
    "runtime_official_turn_internal_turn_start_read",
    "runtime_official_turn_internal_event_read",
    "runtime_official_turn_internal_event_ack_write",
    "runtime_official_turn_internal_turn_finalize",
})

NATIVE_RUNTIME_TURN_DIAGNOSTIC_CAUSES = frozenset({
    # A running turn can fail on the same authenticated transport and bridge
    # boundaries used during startup.  These names are fixed project enums;
    # provider text and child-process output are never admitted here.
    *NATIVE_RUNTIME_START_DIAGNOSTIC_CAUSES,
    # A long-running official turn can observe these fixed bridge lifecycle
    # failures after the startup handshake has already succeeded.  They carry
    # no provider body, path, prompt or exception text, so retaining the exact
    # machine code is both safe and materially more useful than collapsing a
    # peer close/truncated frame into ``runtime_official_turn_failed``.
    "bridge_wire_closed",
    "bridge_wire_eof",
    "bridge_wire_length",
    "bridge_wire_read_failed",
    "bridge_wire_truncated",
    "bridge_wire_write_failed",
    "heartbeat_closed",
    "heartbeat_clock_invalid",
    "heartbeat_ping_pending",
    "heartbeat_timeout",
    "heartbeat_token_mismatch",
    "heartbeat_unexpected_pong",
    "pipe_cancel_settle_timeout",
    "event_sequence_mismatch",
    "native_app_settlement_ack_mismatch",
    "native_app_settlement_effect_mask",
    "native_app_settlement_effect_mismatch",
    "native_app_settlement_missing_effect",
    "native_app_settlement_next_snapshot_stale",
    "native_app_settlement_stage_transition_mismatch",
    "native_app_settlement_stale",
    "native_app_settlement_state_projection",
    "pipe_read_timeout",
    "pipe_write_timeout",
    "runtime_app_tool_arguments_missing",
    "runtime_app_tool_call_count",
    "runtime_app_tool_call_limit",
    "runtime_app_tool_catalog_stale",
    "runtime_app_tool_event",
    "runtime_app_tool_projection",
    "runtime_app_tool_roundtrip_incomplete",
    "runtime_app_tool_terminal_invalid",
    "runtime_app_tool_unavailable",
    "runtime_research_arguments_hash",
    "runtime_research_arguments_invalid",
    "runtime_research_persistence_failed",
    "runtime_research_request_binding",
    "runtime_research_request_replay",
    "runtime_research_request_shape",
    "runtime_research_terminal_missing",
    "runtime_heartbeat_failed",
    "runtime_turn_cancel_payload",
    "runtime_turn_cancel_state",
    "runtime_turn_bridge_closed",
    "runtime_turn_start_payload",
    "runtime_turn_unexpected_frame",
    "runtime_turn_unexpected_response",
    *_OFFICIAL_TURN_INTERNAL_STAGE_CAUSES,
    # Official DSH dynamic-catalog rollover invariants.  These are fixed
    # project-owned machine codes; exception messages and provider payloads
    # remain excluded from diagnostics.
    "turn_app_tool_catalog_dispose_failed",
    "turn_app_tool_catalog_scope_changed",
    "turn_app_tool_registration_invalid",
    "turn_stale_app_tool_catalog",
    "turn_stale_catalog_rollover",
    # Fixed, payload-free failures emitted by the reviewed Node turn runtime.
    # These are useful for local diagnostics and never contain provider text,
    # tool arguments, paths, prompts or stacks.
    "bridge_observer_app_tool_epoch_rollover_required",
    "bridge_observer_duplicate_tool_ready",
    "bridge_observer_failed",
    "bridge_observer_invalid_dsh_event",
    "bridge_observer_tool_arguments_hash_mismatch",
    "bridge_observer_tool_arguments_mismatch",
    "bridge_observer_tool_name_mismatch",
    "bridge_observer_tool_not_allowed",
    "bridge_observer_turn_app_tool_registration_invalid",
    "bridge_observer_turn_stale_app_tool_catalog",
    "bridge_observer_turn_stale_catalog_rollover",
    "bridge_observer_unknown_tool",
    "runtime_failure",
    "turn_cleanup_failed",
    "turn_cleanup_timeout",
    "turn_terminal_missing",
    "provider_aborted",
    "provider_auth_failed",
    "provider_context_window_exceeded",
    "provider_empty_response",
    "provider_failure",
    "provider_http_error",
    "provider_invalid_credential",
    "provider_invalid_request",
    "provider_missing_credential",
    "provider_quota_exhausted",
    "provider_rate_limited",
    "provider_request_extension_failed",
    "provider_server_error",
    "provider_stream_closed",
    "provider_stream_idle_timeout",
    "provider_tool_json_malformed",
    "provider_transport_error",
})
_PLAIN_ERROR_CODE = re.compile(r"[a-z][a-z0-9_]{0,127}\Z")
_OFFICIAL_TURN_FAILURE = "runtime_official_turn_failed"


def reviewed_native_error_code(error: object, *, fallback: str) -> str:
    """Return one reviewed renderer-safe code, preserving a known turn cause.

    ``NativeRuntimeProcessError`` deliberately retains only machine codes at
    the public runtime boundary.  The coordinator and service may wrap that
    exception several times, so this helper also accepts the exact composite
    form after its cause has already passed this allowlist.  No exception text,
    URL, query, path or provider payload is inspected.
    """

    raw = (
        error
        if isinstance(error, str)
        else getattr(error, "code", None) or getattr(error, "reason", None)
    )
    if not isinstance(raw, str):
        return fallback
    if _PLAIN_ERROR_CODE.fullmatch(raw) is not None:
        if raw == _OFFICIAL_TURN_FAILURE:
            cause = getattr(error, "cause_code", None)
            if cause in NATIVE_RUNTIME_TURN_DIAGNOSTIC_CAUSES:
                return f"{raw}:{cause}"
        return raw
    prefix, separator, cause = raw.partition(":")
    if (
        separator
        and prefix == _OFFICIAL_TURN_FAILURE
        and cause in NATIVE_RUNTIME_TURN_DIAGNOSTIC_CAUSES
    ):
        return raw
    return fallback

