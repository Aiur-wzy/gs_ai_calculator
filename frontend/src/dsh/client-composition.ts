/**
 * Production DSH client composition over the private desktop shell carrier.
 *
 * The module deliberately delegates the client runtime to rc.5's public
 * `AppWebEntry`, index-injection interpreter and Client Module Loader.  It
 * contributes only the desktop carrier, an exact package allowlist and the
 * single-owner lifecycle required by the pywebview shell.
 *
 * The production entry delegates generic Agent UI ownership to this composition.
 * App business editors are isolated domain slots, never another chat owner.
 */
import type {ClientTransportHooks} from "@deepseek-ai/dsh-client-connection/client";
import {
  AppWebEntry,
  PLATFORM_MODULES,
  applyIndexInjections,
  getStaticModules,
} from "@deepseek-ai/dsh-client-web";

import contractJson from "../../dsh-client-composition.json";
import acceptedManifestJson from "../../dsh-client-accepted-manifest.json";
import {
  createDshShellTransport,
  type ShellBundleExecutor,
  type ShellCarrierOptions,
  type ShellCarrierPort,
} from "./shell-carrier";

type IndexInjections = Parameters<typeof applyIndexInjections>[0];

type CompositionContract = Readonly<{
  contract: string;
  contractVersion: number;
  status: "production";
  productionOwner: true;
  dshRelease: string;
  dshCommit: string;
  cordisVersion: string;
  reactVersion: string;
  localModules: Readonly<Record<string, Readonly<{version: string; hostEntry: string}>>>;
  dynamicModules: readonly string[];
  platformModules: readonly string[];
  requiredDirectDependencies: Readonly<Record<string, string>>;
  bootstrapScriptSha256: string;
  forbiddenPackageTokens: readonly string[];
  permissions: Readonly<Record<string, boolean>>;
}>;

type AcceptedEntry = Readonly<{
  id: string;
  inject: readonly string[];
  external: readonly string[];
  immediately: boolean;
  executableSha256: string;
  executableBytes: number;
}>;

type AcceptedManifest = Readonly<{
  contract: "genshin-dsh-client-accepted-manifest-v1";
  contractVersion: 1;
  dshRelease: string;
  dshCommit: string;
  bootstrapScriptSha256: string;
  platformModules: readonly string[];
  entryOrder: readonly string[];
  runtimeGraph: Readonly<{
    batchPolicy: "single-entry-canonical-v1";
    bootstrapEntry: string;
    bundleUrlShape: string;
    exactTerminalTrailer: string;
  }>;
  entries: readonly AcceptedEntry[];
  semanticSha256: string;
}>;

export const DSH_CLIENT_COMPOSITION_CONTRACT = Object.freeze(
  contractJson as CompositionContract,
);

export const DSH_CLIENT_ACCEPTED_MANIFEST = Object.freeze(
  acceptedManifestJson as AcceptedManifest,
);

export type DshClientBootPayload = Readonly<{
  /** Structured rows produced by rc.5 `bootInjections(clientModules.graph())`. */
  injections: IndexInjections;
}>;

export type DshClientCompositionOptions = Readonly<{
  container: HTMLElement;
  port: ShellCarrierPort;
  executor?: ShellBundleExecutor;
  boot: DshClientBootPayload;
  carrier?: ShellCarrierOptions;
  onFailure?: (reason: unknown) => void;
}>;

export type DshClientCompositionHandle = Readonly<{
  contract: string;
  dispose(): Promise<void>;
}>;

type BootGraph = Readonly<{
  rev: unknown;
  release?: Readonly<{version?: unknown}>;
  entries: readonly Readonly<{
    id: unknown;
    url: unknown;
    rev: unknown;
    inject?: unknown;
    immediately?: unknown;
    external?: unknown;
  }>[];
  batches: readonly Readonly<{
    phase: unknown;
    url: unknown;
    rev: unknown;
    entries: unknown;
  }>[];
}>;

const OWNER_KEY = Symbol.for("genshin.dsh-client-composition.owner");
const BOOT_GLOBALS = Object.freeze([
  "__DSH_TRANSPORT__",
  "__DSH_BOOT__",
  "__ModuleLoader__",
] as const);

function record(value: unknown): Record<string, unknown> | undefined {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return undefined;
  return value as Record<string, unknown>;
}

function graphFrom(injections: IndexInjections): BootGraph {
  const globals = injections.filter(
    row => row.kind === "global" && row.name === "__DSH_BOOT__",
  );
  if (globals.length !== 1) throw new Error("dsh_client_composition_boot_graph_count_invalid");
  const candidate = record(globals[0]?.value);
  if (
    candidate === undefined
    || !Array.isArray(candidate.entries)
    || !Array.isArray(candidate.batches)
  ) {
    throw new Error("dsh_client_composition_boot_graph_invalid");
  }
  return candidate as unknown as BootGraph;
}

function sorted(values: readonly string[]): string[] {
  return [...values].sort((left, right) => left.localeCompare(right));
}

function nonEmptyString(value: unknown, code: string): string {
  if (typeof value !== "string" || value.length < 1) throw new Error(code);
  return value;
}

function stringArray(value: unknown, code: string): readonly string[] {
  if (!Array.isArray(value) || value.some(item => typeof item !== "string" || item.length < 1)) {
    throw new Error(code);
  }
  return value;
}

function assertRevisionedBundleUrl(
  value: unknown,
  revision: string,
  entryIds: readonly string[],
  code: string,
): string {
  const url = nonEmptyString(value, code);
  if (!/^[0-9a-f]{12}$/.test(revision) || entryIds.length < 1) throw new Error(code);
  const canonical = `plugins/??${entryIds.map(id => `${id}/client.js`).join(",")}&rev=${revision}`;
  if ((url.startsWith("/") ? url.slice(1) : url) !== canonical) throw new Error(code);
  return url;
}

async function sha256Hex(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, "0")).join("");
}

async function graphRevision(entries: BootGraph["entries"], batches: BootGraph["batches"]): Promise<string> {
  const encoded = new TextEncoder().encode(JSON.stringify({entries, batches}));
  const digest = await crypto.subtle.digest("SHA-1", encoded);
  return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, "0")).join("").slice(0, 12);
}

function exactOwnKeys(value: Record<string, unknown>, keys: readonly string[], code: string): void {
  if (JSON.stringify(sorted(Object.keys(value))) !== JSON.stringify(sorted(keys))) throw new Error(code);
}

function sameStrings(left: readonly string[], right: readonly string[]): boolean {
  return JSON.stringify(sorted(left)) === JSON.stringify(sorted(right));
}

function sameSequence(left: readonly string[], right: readonly string[]): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

async function validateAcceptedManifest(): Promise<ReadonlyMap<string, AcceptedEntry>> {
  const manifest = DSH_CLIENT_ACCEPTED_MANIFEST;
  if (
    manifest.dshRelease !== DSH_CLIENT_COMPOSITION_CONTRACT.dshRelease
    || manifest.dshCommit !== DSH_CLIENT_COMPOSITION_CONTRACT.dshCommit
    || manifest.bootstrapScriptSha256 !== DSH_CLIENT_COMPOSITION_CONTRACT.bootstrapScriptSha256
    || manifest.runtimeGraph.batchPolicy !== "single-entry-canonical-v1"
    || manifest.runtimeGraph.bootstrapEntry !== "@deepseek-ai/dsh-client-modules"
    || !sameStrings(manifest.platformModules, DSH_CLIENT_COMPOSITION_CONTRACT.platformModules)
    || JSON.stringify(manifest.entryOrder) !== JSON.stringify(DSH_CLIENT_COMPOSITION_CONTRACT.dynamicModules)
  ) {
    throw new Error("dsh_client_composition_accepted_manifest_contract_mismatch");
  }
  const entries = new Map(manifest.entries.map(entry => [entry.id, entry]));
  if (
    entries.size !== manifest.entries.length
    || !sameStrings([...entries.keys()], manifest.entryOrder)
    || manifest.entries.some(entry => (
      !/^[0-9a-f]{64}$/.test(entry.executableSha256)
      || !Number.isSafeInteger(entry.executableBytes)
      || entry.executableBytes < 1
    ))
  ) {
    throw new Error("dsh_client_composition_accepted_manifest_invalid");
  }
  const {semanticSha256, ...unsigned} = manifest;
  if (await sha256Hex(JSON.stringify(unsigned)) !== semanticSha256) {
    throw new Error("dsh_client_composition_accepted_manifest_digest_mismatch");
  }
  return entries;
}

function canonicalEntry(
  source: BootGraph["entries"][number],
  accepted: AcceptedEntry,
): BootGraph["entries"][number] {
  return Object.freeze({
    id: accepted.id,
    url: source.url,
    rev: source.rev,
    inject: Object.freeze([...accepted.inject]),
    immediately: accepted.immediately,
    external: Object.freeze([...accepted.external]),
  });
}

async function canonicalGraph(
  source: BootGraph,
  acceptedEntries: ReadonlyMap<string, AcceptedEntry>,
): Promise<BootGraph> {
  const byId = new Map(source.entries.map(entry => [entry.id as string, entry]));
  const entries = DSH_CLIENT_ACCEPTED_MANIFEST.entryOrder.map(id => {
    const entry = byId.get(id);
    const accepted = acceptedEntries.get(id);
    if (entry === undefined || accepted === undefined) {
      throw new Error(`dsh_client_composition_accepted_entry_missing:${id}`);
    }
    return canonicalEntry(entry, accepted);
  });
  const batches = entries.map(entry => Object.freeze({
    phase: entry.id === DSH_CLIENT_ACCEPTED_MANIFEST.runtimeGraph.bootstrapEntry
      ? "bootstrap"
      : "application",
    url: entry.url,
    rev: entry.rev,
    entries: Object.freeze([entry.id as string]),
  }));
  return Object.freeze({
    rev: await graphRevision(entries, batches),
    release: Object.freeze({
      version: DSH_CLIENT_COMPOSITION_CONTRACT.dshRelease,
      commit: DSH_CLIENT_COMPOSITION_CONTRACT.dshCommit,
    }),
    entries: Object.freeze(entries),
    batches: Object.freeze(batches),
  });
}

/**
 * Fail closed before any Host-supplied script runs.  The full wire validation
 * remains owned by DSH's Client Module System; this check is the narrower App
 * permission boundary proving that the graph contains exactly the reviewed
 * packages and no forbidden surface.
 */
export async function validateReviewedDshBootPayload(payload: DshClientBootPayload): Promise<BootGraph> {
  const acceptedEntries = await validateAcceptedManifest();
  const graph = graphFrom(payload.injections);
  const graphRecord = graph as unknown as Record<string, unknown>;
  exactOwnKeys(graphRecord, ["rev", "release", "entries", "batches"], "dsh_client_composition_boot_graph_shape_invalid");
  const graphRev = nonEmptyString(graph.rev, "dsh_client_composition_graph_revision_invalid");
  if (!/^[0-9a-f]{12}$/.test(graphRev)) throw new Error("dsh_client_composition_graph_revision_invalid");
  const release = record(graph.release);
  if (release === undefined) throw new Error("dsh_client_composition_release_mismatch");
  exactOwnKeys(release, ["version", "commit"], "dsh_client_composition_release_shape_invalid");
  if (graph.release?.version !== DSH_CLIENT_COMPOSITION_CONTRACT.dshRelease) {
    throw new Error("dsh_client_composition_release_mismatch");
  }
  if (release.commit !== DSH_CLIENT_COMPOSITION_CONTRACT.dshCommit) {
    throw new Error("dsh_client_composition_release_commit_mismatch");
  }
  const ids = graph.entries.map(entry => {
    const entryRecord = entry as unknown as Record<string, unknown>;
    const allowedKeys = ["id", "url", "rev", "inject", "immediately", "external"];
    if (Object.keys(entryRecord).some(key => !allowedKeys.includes(key))) {
      throw new Error("dsh_client_composition_entry_shape_invalid");
    }
    const id = nonEmptyString(entry.id, "dsh_client_composition_entry_id_invalid");
    const revision = nonEmptyString(entry.rev, "dsh_client_composition_entry_revision_invalid");
    assertRevisionedBundleUrl(entry.url, revision, [id], "dsh_client_composition_entry_url_invalid");
    const dependencies = entry.inject === undefined
      ? []
      : stringArray(entry.inject, "dsh_client_composition_entry_inject_invalid");
    // Upstream client plugins can inject an already reviewed platform owner
    // (for example UI primitives) as well as a dynamically mounted module.
    const availableOwners = new Set([
      ...DSH_CLIENT_COMPOSITION_CONTRACT.dynamicModules,
      ...DSH_CLIENT_COMPOSITION_CONTRACT.platformModules,
    ]);
    if (dependencies.some(name => !availableOwners.has(name))) {
      throw new Error("dsh_client_composition_entry_inject_outside_graph");
    }
    const external = entry.external === undefined
      ? []
      : stringArray(entry.external, "dsh_client_composition_entry_external_invalid");
    const known = new Set([
      ...DSH_CLIENT_COMPOSITION_CONTRACT.dynamicModules,
      ...DSH_CLIENT_COMPOSITION_CONTRACT.platformModules,
    ]);
    if (external.some(name => !known.has(name.endsWith("/client") ? name.slice(0, -7) : name))) {
      throw new Error("dsh_client_composition_entry_external_outside_graph");
    }
    if (entry.immediately !== undefined && typeof entry.immediately !== "boolean") {
      throw new Error("dsh_client_composition_entry_immediately_invalid");
    }
    const accepted = acceptedEntries.get(id);
    if (
      accepted !== undefined
      && (
        !sameSequence(dependencies, accepted.inject)
        || !sameSequence(external, accepted.external)
        || (entry.immediately === true) !== accepted.immediately
      )
    ) {
      throw new Error(`dsh_client_composition_entry_semantics_mismatch:${id}`);
    }
    return id;
  });
  if (new Set(ids).size !== ids.length) {
    throw new Error("dsh_client_composition_entry_duplicate");
  }
  if (JSON.stringify(sorted(ids)) !== JSON.stringify(sorted(DSH_CLIENT_COMPOSITION_CONTRACT.dynamicModules))) {
    throw new Error("dsh_client_composition_allowlist_mismatch");
  }
  const denied = ids.find(id => DSH_CLIENT_COMPOSITION_CONTRACT.forbiddenPackageTokens.some(
    token => id.includes(token),
  ));
  if (denied !== undefined) throw new Error(`dsh_client_composition_forbidden_module:${denied}`);

  const membership: string[] = [];
  const applicationUrls: string[] = [];
  const bootstrapUrls: string[] = [];
  for (const batch of graph.batches) {
    const batchRecord = batch as unknown as Record<string, unknown>;
    exactOwnKeys(batchRecord, ["phase", "url", "rev", "entries"], "dsh_client_composition_batch_shape_invalid");
    if (batch.phase !== "bootstrap" && batch.phase !== "application") {
      throw new Error("dsh_client_composition_batch_phase_invalid");
    }
    const revision = nonEmptyString(batch.rev, "dsh_client_composition_batch_revision_invalid");
    const batchEntries = stringArray(batch.entries, "dsh_client_composition_batch_entries_invalid");
    if (batchEntries.length < 1 || new Set(batchEntries).size !== batchEntries.length) {
      throw new Error("dsh_client_composition_batch_entries_invalid");
    }
    const url = assertRevisionedBundleUrl(
      batch.url,
      revision,
      batchEntries,
      "dsh_client_composition_batch_url_invalid",
    );
    membership.push(...batchEntries);
    (batch.phase === "application" ? applicationUrls : bootstrapUrls).push(url);
  }
  if (
    new Set(membership).size !== membership.length
    || JSON.stringify(sorted(membership)) !== JSON.stringify(sorted(ids))
  ) {
    throw new Error("dsh_client_composition_batch_partition_invalid");
  }
  const modulesBatch = graph.batches.find(batch => (
    Array.isArray(batch.entries) && batch.entries.includes("@deepseek-ai/dsh-client-modules")
  ));
  if (modulesBatch?.phase !== "bootstrap") {
    throw new Error("dsh_client_composition_bootstrap_owner_invalid");
  }
  if (await graphRevision(graph.entries, graph.batches) !== graphRev) {
    throw new Error("dsh_client_composition_graph_revision_mismatch");
  }

  const expectedLength = 2 + graph.batches.length;
  if (payload.injections.length !== expectedLength) {
    throw new Error("dsh_client_composition_index_injection_shape_invalid");
  }
  const first = record(payload.injections[0]);
  const last = record(payload.injections.at(-1));
  if (
    first?.kind !== "script"
    || first.placement !== "head"
    || typeof first.text !== "string"
    || last?.kind !== "global"
    || last.name !== "__DSH_BOOT__"
    || last.value !== graph
  ) {
    throw new Error("dsh_client_composition_index_injection_shape_invalid");
  }
  exactOwnKeys(first, ["kind", "placement", "text"], "dsh_client_composition_inline_script_shape_invalid");
  exactOwnKeys(last, ["kind", "name", "value"], "dsh_client_composition_boot_global_shape_invalid");
  if (await sha256Hex(first.text) !== DSH_CLIENT_COMPOSITION_CONTRACT.bootstrapScriptSha256) {
    throw new Error("dsh_client_composition_inline_script_identity_invalid");
  }
  const expectedScriptRows = [
    ...applicationUrls.map(src => ({kind: "script-preload", src})),
    ...bootstrapUrls.map(src => ({kind: "script-src", placement: "head", src})),
  ];
  for (const [offset, expected] of expectedScriptRows.entries()) {
    const actual = record(payload.injections[offset + 1]);
    if (actual === undefined || JSON.stringify(actual) !== JSON.stringify(expected)) {
      throw new Error("dsh_client_composition_index_injection_binding_invalid");
    }
  }
  return canonicalGraph(graph, acceptedEntries);
}

/** The public DSH seed table is the single React/Cordis identity for the page. */
export function auditDshPlatformSingletons(): Readonly<{
  modules: readonly string[];
  staticModuleCount: number;
}> {
  const expected = DSH_CLIENT_COMPOSITION_CONTRACT.platformModules;
  if (JSON.stringify(PLATFORM_MODULES) !== JSON.stringify(expected)) {
    throw new Error("dsh_client_composition_platform_contract_mismatch");
  }
  const seed = getStaticModules();
  if (JSON.stringify(sorted(Object.keys(seed))) !== JSON.stringify(sorted(expected))) {
    throw new Error("dsh_client_composition_platform_seed_mismatch");
  }
  return Object.freeze({modules: PLATFORM_MODULES, staticModuleCount: Object.keys(seed).length});
}

/**
 * Browser/WebView bundle execution without `eval`, `Function`, a network URL,
 * or a second module runtime.  Bytes have already passed ShellCarrier URL,
 * media-type and size validation before reaching this executor.
 */
export function createDomScriptBundleExecutor(
  targetDocument: Document = document,
): ShellBundleExecutor {
  return Object.freeze({
    execute({url, source}: Readonly<{url: string; source: string}>): void {
      const script = targetDocument.createElement("script");
      script.dataset.dshBundle = url;
      script.textContent = source;
      targetDocument.head.append(script);
      script.remove();
    },
  });
}

function reviewedInjections(graph: BootGraph, inlineScript: string): IndexInjections {
  const application = graph.batches.filter(batch => batch.phase === "application");
  const bootstrap = graph.batches.filter(batch => batch.phase === "bootstrap");
  return [
    {kind: "script", placement: "head", text: inlineScript},
    ...application.map(batch => ({kind: "script-preload" as const, src: batch.url as string})),
    ...bootstrap.map(batch => ({
      kind: "script-src" as const,
      placement: "head" as const,
      src: batch.url as string,
    })),
    {kind: "global", name: "__DSH_BOOT__", value: graph},
  ] as IndexInjections;
}

function canonicalBundleUrl(value: unknown): string {
  const url = nonEmptyString(value, "dsh_client_composition_bundle_url_invalid");
  return url.startsWith("/") ? url.slice(1) : url;
}

function createReviewedBundleExecutor(
  delegate: ShellBundleExecutor,
  graph: BootGraph,
): ShellBundleExecutor {
  const acceptedById = new Map(DSH_CLIENT_ACCEPTED_MANIFEST.entries.map(entry => [entry.id, entry]));
  const reviewedByUrl = new Map(graph.entries.map(entry => {
    const id = entry.id as string;
    const accepted = acceptedById.get(id);
    if (accepted === undefined) throw new Error(`dsh_client_composition_bundle_identity_missing:${id}`);
    return [canonicalBundleUrl(entry.url), {
      accepted,
      revision: entry.rev as string,
    }] as const;
  }));
  return Object.freeze({
    async execute(input: Readonly<{url: string; source: string}>): Promise<void> {
      const reviewed = reviewedByUrl.get(canonicalBundleUrl(input.url));
      if (reviewed === undefined) throw new Error("dsh_client_composition_bundle_not_reviewed");
      const trailer = `//# sourceMappingURL=??${reviewed.accepted.id}/client.js.map&rev=${reviewed.revision}\n`;
      if (!input.source.endsWith(trailer)) {
        throw new Error(`dsh_client_composition_bundle_trailer_mismatch:${reviewed.accepted.id}`);
      }
      const executable = input.source.slice(0, -trailer.length);
      if (new TextEncoder().encode(executable).byteLength !== reviewed.accepted.executableBytes) {
        throw new Error(`dsh_client_composition_bundle_size_mismatch:${reviewed.accepted.id}`);
      }
      if (await sha256Hex(executable) !== reviewed.accepted.executableSha256) {
        throw new Error(`dsh_client_composition_bundle_digest_mismatch:${reviewed.accepted.id}`);
      }
      // The exact terminal source-map comment is intentionally not forwarded;
      // every byte reaching the classic-script sink is covered by the digest.
      await delegate.execute(Object.freeze({url: input.url, source: executable}));
    },
  });
}

type GlobalTarget = typeof globalThis;

function uniqueGlobalTargets(container: HTMLElement): readonly GlobalTarget[] {
  const page = container.ownerDocument.defaultView as GlobalTarget | null;
  return page === null || page === globalThis ? [globalThis] : [globalThis, page];
}

function captureGlobals(targets: readonly GlobalTarget[]): Map<GlobalTarget, Map<string, PropertyDescriptor | undefined>> {
  return new Map(targets.map(target => [
    target,
    new Map(BOOT_GLOBALS.map(name => [name, Object.getOwnPropertyDescriptor(target, name)])),
  ]));
}

function restoreGlobals(saved: ReadonlyMap<GlobalTarget, ReadonlyMap<string, PropertyDescriptor | undefined>>): void {
  for (const [target, descriptors] of saved) {
    for (const [name, descriptor] of descriptors) {
      if (descriptor === undefined) Reflect.deleteProperty(target, name);
      else Object.defineProperty(target, name, descriptor);
    }
  }
}

function setGlobal(targets: readonly GlobalTarget[], name: string, value: unknown): void {
  for (const target of targets) Reflect.set(target, name, value);
}

async function disposeEntry(entry: AppWebEntry): Promise<unknown> {
  try {
    await entry.dispose();
    return undefined;
  } catch (error) {
    return error;
  }
}

/**
 * Start the reviewed rc.5 client composition over GenshinShellCarrier.
 *
 * The same entry is used by production and CLI projection qualification.
 * It never reads or writes legacy session owners.
 */
export async function startReviewedDshClientComposition(
  options: DshClientCompositionOptions,
): Promise<DshClientCompositionHandle> {
  const acceptedGraph = await validateReviewedDshBootPayload(options.boot);
  auditDshPlatformSingletons();
  const targets = uniqueGlobalTargets(options.container);
  if (targets.some(target => Reflect.get(target, OWNER_KEY) !== undefined)) {
    throw new Error("dsh_client_composition_owner_already_active");
  }
  const occupied = BOOT_GLOBALS.find(name => (
    targets.some(target => Object.prototype.hasOwnProperty.call(target, name))
  ));
  if (occupied !== undefined) throw new Error(`dsh_client_composition_global_already_owned:${occupied}`);

  const saved = captureGlobals(targets);
  const inlineScript = record(options.boot.injections[0])?.text as string;
  const boot = reviewedInjections(acceptedGraph, inlineScript);
  const rawExecutor = options.executor ?? createDomScriptBundleExecutor(options.container.ownerDocument);
  const executor = createReviewedBundleExecutor(rawExecutor, acceptedGraph);
  const transport = createDshShellTransport(
    options.port,
    executor,
    options.carrier,
  );
  const typedTransport: ClientTransportHooks = transport;
  const owner = Object.freeze({contract: DSH_CLIENT_COMPOSITION_CONTRACT.contract});
  for (const target of targets) Reflect.set(target, OWNER_KEY, owner);
  setGlobal(targets, "__DSH_TRANSPORT__", typedTransport);

  const entry = new AppWebEntry(options.container, {loadBundle: transport.loadBundle});
  let failure: unknown;
  let failed = false;
  let failureObserverError: unknown;
  const cleanup = (): void => {
    restoreGlobals(saved);
    for (const target of targets) {
      if (Reflect.get(target, OWNER_KEY) === owner) Reflect.deleteProperty(target, OWNER_KEY);
    }
  };
  try {
    // The sole inline row has already been pinned by SHA-256. Execute those
    // exact bytes through the same controlled classic-script executor as the
    // revisioned bundles, then leave the remaining official rows to DSH's
    // public index-injection interpreter.
    await rawExecutor.execute(Object.freeze({
      url: `dsh-bootstrap:${DSH_CLIENT_COMPOSITION_CONTRACT.bootstrapScriptSha256}`,
      source: inlineScript,
    }));
    await applyIndexInjections(boot.slice(1), transport.loadBundle);
    const page = options.container.ownerDocument.defaultView as GlobalTarget | null;
    if (page !== null) {
      const pageLoader = Reflect.get(page, "__ModuleLoader__");
      if (pageLoader !== undefined) Reflect.set(globalThis, "__ModuleLoader__", pageLoader);
      Reflect.set(page, "__DSH_BOOT__", Reflect.get(globalThis, "__DSH_BOOT__"));
    }
    await entry.run(reason => {
      failed = true;
      failure = reason;
      try {
        options.onFailure?.(reason);
      } catch (error) {
        failureObserverError = error;
      }
    });
    if (failed) {
      const primary = failure instanceof Error ? failure : new Error(String(failure));
      if (failureObserverError !== undefined) {
        throw new AggregateError([primary, failureObserverError], "dsh_client_composition_boot_and_observer_failed");
      }
      throw primary;
    }
  } catch (error) {
    const disposeError = await disposeEntry(entry);
    cleanup();
    if (disposeError !== undefined) {
      throw new AggregateError([error, disposeError], "dsh_client_composition_boot_and_cleanup_failed");
    }
    throw error;
  }

  let disposed = false;
  return Object.freeze({
    contract: DSH_CLIENT_COMPOSITION_CONTRACT.contract,
    async dispose(): Promise<void> {
      if (disposed) return;
      disposed = true;
      const disposeError = await disposeEntry(entry);
      cleanup();
      if (disposeError !== undefined) throw disposeError;
    },
  });
}
