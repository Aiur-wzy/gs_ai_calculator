export const galleryEnvelope = Object.freeze({
  envelope_id: "gallery-envelope-1",
  projection: {
    contract: "native-harness-projection-v3",
    projection_revision: 82,
    runtime: {state: "healthy"},
    turn: {state: "failed", error_ref: "gallery-failure"},
    focus: {recommended_stage: "event_topology", reason: "topology_partial"},
    artifacts: [],
    candidate: {seal_state: "unsealed", assessment: "unknown", freshness: "fresh", binding_state: "aligned", application_state: "none"},
    interruptions: [],
    available_commands: [
      {command_id: "conversation.send", enabled: true, scope: {}, risk: "local", reason_code: null, expected_projection_revision: 82},
      {command_id: "draft.inspect", enabled: true, scope: {}, risk: "read_only", reason_code: null, expected_projection_revision: 82},
      {command_id: "candidate.revalidate", enabled: true, scope: {}, risk: "local", reason_code: null, expected_projection_revision: 82},
      {command_id: "draft.discard", enabled: true, scope: {}, risk: "local", reason_code: null, expected_projection_revision: 82},
    ],
    blocking_conditions: [],
    required_actions: [],
    issues: [{issue_id: "gallery-issue", code: "turn_failed", severity: "warning", message: "本轮失败，但会话和草稿仍可继续。", details_ref: "gallery-details"}],
    recommended_command: "conversation.send",
  },
});
