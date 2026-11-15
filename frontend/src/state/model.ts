export type ThemePreference = "system" | "light" | "dark";
export type WorkspaceRoute = "assistant" | "whiteboard" | "advanced" | "report" | "catalog";
export type SurfaceKind = "modal" | "drawer" | "ephemeral" | "tooltip" | "toast";
export type SurfaceGroup = "modal" | "modal-child" | "left-drawer" | "right-drawer" | "ephemeral" | "tooltip" | "toast";

export type UiCommand = Readonly<{
  commandId: string;
  enabled: boolean;
  scope: Readonly<Record<string, unknown>>;
  risk: "ui" | "local" | "read_only" | "draft" | "domain" | "external";
  reasonCode: string | null;
  expectedProjectionRevision: number;
}>;

export type Issue = Readonly<{
  issueId: string;
  code: string;
  severity: "info" | "warning" | "error";
  message: string;
  detailsRef: string | null;
}>;

export type RequiredAction = Readonly<{
  interruptionId: string;
  kind: string;
  state: "pending";
  commandIds: readonly string[];
}>;

export type HarnessProjection = Readonly<{
  contract: "native-harness-projection-v3";
  projectionRevision: number;
  runtime: Readonly<Record<string, unknown>>;
  turn: Readonly<Record<string, unknown>>;
  focus: Readonly<Record<string, unknown>> | null;
  artifacts: readonly Readonly<Record<string, unknown>>[];
  candidate: Readonly<Record<string, unknown>> | null;
  interruptions: readonly Readonly<Record<string, unknown>>[];
  availableCommands: readonly UiCommand[];
  blockingConditions: readonly Readonly<Record<string, unknown>>[];
  requiredActions: readonly RequiredAction[];
  issues: readonly Issue[];
  recommendedCommand: string | null;
}>;

export type WorkspaceState = Readonly<{
  route: WorkspaceRoute;
  activeDocumentRef: string | null;
  revision: number | null;
  dirty: boolean;
}>;

export type LayoutState = Readonly<{
  leftVisible: boolean;
  inspectorVisible: boolean;
  leftWidth: number;
  inspectorWidth: number;
  density: "comfortable" | "compact";
}>;

export type SurfaceDescriptor = Readonly<{
  id: string;
  kind: SurfaceKind;
  group: SurfaceGroup;
  ownerId: string;
  dismissOnEscape: boolean;
  dismissOnOutside: boolean;
  modal: boolean;
}>;

export type UiState = Readonly<{
  theme: ThemePreference;
  selection: string | null;
  search: string;
  composerDraft: string;
}>;

export type AppState = Readonly<{
  revision: number;
  lastEnvelopeId: string | null;
  hostProjection: HarnessProjection | null;
  workspace: WorkspaceState;
  layout: LayoutState;
  surfaces: readonly SurfaceDescriptor[];
  issues: readonly Issue[];
  ui: UiState;
}>;

export const initialAppState = (): AppState => Object.freeze({
  revision: 0,
  lastEnvelopeId: null,
  hostProjection: null,
  workspace: Object.freeze({route: "assistant", activeDocumentRef: null, revision: null, dirty: false}),
  layout: Object.freeze({leftVisible: true, inspectorVisible: true, leftWidth: 288, inspectorWidth: 360, density: "comfortable"}),
  surfaces: Object.freeze([]),
  issues: Object.freeze([]),
  ui: Object.freeze({theme: "system", selection: null, search: "", composerDraft: ""}),
});
