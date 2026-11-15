import type {HarnessProjection, Issue, RequiredAction, UiCommand} from "@/state/model";

type UnknownRecord = Record<string, unknown>;

export class HostEnvelopeError extends Error {
  constructor(public readonly code: string, public readonly path: string) {
    super(`${code}:${path}`);
    this.name = "HostEnvelopeError";
  }
}

const record = (value: unknown, path: string): UnknownRecord => {
  if (value === null || typeof value !== "object" || Array.isArray(value)) throw new HostEnvelopeError("object_required", path);
  return value as UnknownRecord;
};
const array = (value: unknown, path: string): unknown[] => {
  if (!Array.isArray(value)) throw new HostEnvelopeError("array_required", path);
  return value;
};
const text = (value: unknown, path: string, maximum = 4096): string => {
  if (typeof value !== "string" || value.length === 0 || value.length > maximum) throw new HostEnvelopeError("text_invalid", path);
  return value;
};
const optionalText = (value: unknown, path: string, maximum = 4096): string | null => value == null ? null : text(value, path, maximum);
const safeRevision = (value: unknown, path: string): number => {
  if (!Number.isSafeInteger(value) || (value as number) < 0) throw new HostEnvelopeError("revision_invalid", path);
  return value as number;
};

function command(value: unknown, revision: number, index: number): UiCommand {
  const row = record(value, `available_commands[${index}]`);
  const expected = safeRevision(row.expected_projection_revision, `available_commands[${index}].expected_projection_revision`);
  if (expected !== revision) throw new HostEnvelopeError("command_revision_mismatch", `available_commands[${index}]`);
  const risk = text(row.risk, `available_commands[${index}].risk`, 32);
  if (!["ui", "local", "read_only", "draft", "domain", "external"].includes(risk)) throw new HostEnvelopeError("command_risk_invalid", `available_commands[${index}].risk`);
  return Object.freeze({
    commandId: text(row.command_id, `available_commands[${index}].command_id`, 128),
    enabled: row.enabled === true,
    scope: Object.freeze({...record(row.scope ?? {}, `available_commands[${index}].scope`)}),
    risk: risk as UiCommand["risk"],
    reasonCode: optionalText(row.reason_code, `available_commands[${index}].reason_code`, 128),
    expectedProjectionRevision: expected,
  });
}

function issue(value: unknown, index: number): Issue {
  const row = record(value, `issues[${index}]`);
  const severity = text(row.severity, `issues[${index}].severity`, 16);
  if (!["info", "warning", "error"].includes(severity)) throw new HostEnvelopeError("issue_severity_invalid", `issues[${index}].severity`);
  return Object.freeze({
    issueId: text(row.issue_id, `issues[${index}].issue_id`, 128),
    code: text(row.code, `issues[${index}].code`, 128),
    severity: severity as Issue["severity"],
    message: text(row.message, `issues[${index}].message`, 8192),
    detailsRef: optionalText(row.details_ref, `issues[${index}].details_ref`, 256),
  });
}

function requiredAction(value: unknown, index: number): RequiredAction {
  const row = record(value, `required_actions[${index}]`);
  if (row.state !== "pending") throw new HostEnvelopeError("required_action_state_invalid", `required_actions[${index}].state`);
  return Object.freeze({
    interruptionId: text(row.interruption_id, `required_actions[${index}].interruption_id`, 128),
    kind: text(row.kind, `required_actions[${index}].kind`, 128),
    state: "pending",
    commandIds: Object.freeze(array(row.command_ids, `required_actions[${index}].command_ids`).map((item, commandIndex) => text(item, `required_actions[${index}].command_ids[${commandIndex}]`, 128))),
  });
}

export type HostEnvelope = Readonly<{
  envelopeId: string;
  projection: HarnessProjection;
}>;

export function parseHostEnvelope(value: unknown): HostEnvelope {
  const root = record(value, "envelope");
  const envelopeId = text(root.envelope_id, "envelope.envelope_id", 128);
  const projection = record(root.projection, "envelope.projection");
  if (projection.contract !== "native-harness-projection-v3") throw new HostEnvelopeError("projection_contract_unsupported", "envelope.projection.contract");
  const projectionRevision = safeRevision(projection.projection_revision, "envelope.projection.projection_revision");
  const commands = array(projection.available_commands, "envelope.projection.available_commands").map((item, index) => command(item, projectionRevision, index));
  if (new Set(commands.map(item => item.commandId)).size !== commands.length) throw new HostEnvelopeError("command_id_duplicate", "envelope.projection.available_commands");
  const issues = array(projection.issues ?? [], "envelope.projection.issues").map(issue);
  const requiredActions = array(projection.required_actions ?? [], "envelope.projection.required_actions").map(requiredAction);
  const result: HarnessProjection = Object.freeze({
    contract: "native-harness-projection-v3",
    projectionRevision,
    runtime: Object.freeze({...record(projection.runtime, "envelope.projection.runtime")}),
    turn: Object.freeze({...record(projection.turn, "envelope.projection.turn")}),
    focus: projection.focus == null ? null : Object.freeze({...record(projection.focus, "envelope.projection.focus")}),
    artifacts: Object.freeze(array(projection.artifacts ?? [], "envelope.projection.artifacts").map((item, index) => Object.freeze({...record(item, `envelope.projection.artifacts[${index}]`)}))),
    candidate: projection.candidate == null ? null : Object.freeze({...record(projection.candidate, "envelope.projection.candidate")}),
    interruptions: Object.freeze(array(projection.interruptions ?? [], "envelope.projection.interruptions").map((item, index) => Object.freeze({...record(item, `envelope.projection.interruptions[${index}]`)}))),
    availableCommands: Object.freeze(commands),
    blockingConditions: Object.freeze(array(projection.blocking_conditions ?? [], "envelope.projection.blocking_conditions").map((item, index) => Object.freeze({...record(item, `envelope.projection.blocking_conditions[${index}]`)}))),
    requiredActions: Object.freeze(requiredActions),
    issues: Object.freeze(issues),
    recommendedCommand: optionalText(projection.recommended_command, "envelope.projection.recommended_command", 128),
  });
  return Object.freeze({envelopeId, projection: result});
}
