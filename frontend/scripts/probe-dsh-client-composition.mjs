/**
 * Real rc.5 host-side probe for the production client composition.
 *
 * It uses only public package roots: Cordis Loader constructs the reviewed
 * rows, ClientModuleRegistry composes the official graph, bootInjections
 * produces the browser bootstrap rows, and fetchBundle serves the exact bytes
 * that GenshinShellCarrier will request. The probe never starts a model.
 */
import {readFile} from "node:fs/promises";
import {dirname, resolve} from "node:path";
import {fileURLToPath} from "node:url";
import {createHash} from "node:crypto";

import {Context} from "@deepseek-ai/cordis";
import Loader from "@deepseek-ai/cordis-plugin-loader";
import {bootInjections} from "@deepseek-ai/dsh-client-modules";
import {buildAcceptedManifest, reviewedLoaderName, stableJson} from "./dsh-client-review-lib.mjs";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const contract = JSON.parse(await readFile(resolve(root, "dsh-client-composition.json"), "utf8"));
const acceptedManifestText = await readFile(resolve(root, "dsh-client-accepted-manifest.json"), "utf8");
const packageJson = JSON.parse(await readFile(resolve(root, "package.json"), "utf8"));

function equal(actual, expected, code) {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) throw new Error(code);
}

function sorted(values) {
  return [...values].sort((left, right) => left.localeCompare(right));
}

function packageManifestPath(name) {
  return resolve(root, "node_modules", ...name.split("/"), "package.json");
}

if (contract.productionOwner !== true || contract.status !== "production") {
  throw new Error("dsh_client_composition_probe_authority_invalid");
}
if (packageJson.dependencies.react !== contract.reactVersion
    || packageJson.dependencies["react-dom"] !== contract.reactVersion
    || packageJson.dependencies["@deepseek-ai/cordis"] !== contract.cordisVersion) {
  throw new Error("dsh_client_composition_probe_singleton_version_mismatch");
}

const requiredDependencies = contract.requiredDirectDependencies;
for (const name of contract.dynamicModules) {
  const local = contract.localModules?.[name];
  const expected = local === undefined ? contract.dshRelease : `link:${local.hostEntry.slice(0, local.hostEntry.lastIndexOf("/"))}`;
  if (requiredDependencies[name] !== expected) {
    throw new Error(`dsh_client_composition_probe_dynamic_dependency_not_explicit:${name}`);
  }
}
for (const [name, version] of Object.entries(requiredDependencies)) {
  if (packageJson.dependencies[name] !== version) {
    throw new Error(`dsh_client_composition_probe_direct_dependency:${name}`);
  }
}

const reviewedRows = [];
for (const name of contract.dynamicModules) {
  const manifest = JSON.parse(await readFile(packageManifestPath(name), "utf8"));
  if (manifest.name !== name || manifest.version !== (contract.localModules?.[name]?.version ?? contract.dshRelease)) {
    throw new Error(`dsh_client_composition_probe_installed_identity:${name}`);
  }
  if (manifest.dsh?.client?.platform !== "web") {
    throw new Error(`dsh_client_composition_probe_not_web_client:${name}`);
  }
  reviewedRows.push(Object.freeze({
    name,
    inject: Object.freeze([...(manifest.dsh.client.inject ?? [])]),
    immediately: manifest.dsh.client.immediately === true,
  }));
}

const deniedDirect = Object.keys(requiredDependencies).filter(name => (
  contract.forbiddenPackageTokens.some(token => name.includes(token))
));
if (deniedDirect.length > 0) {
  throw new Error(`dsh_client_composition_probe_forbidden_direct:${deniedDirect.join(",")}`);
}

const ctx = new Context();
try {
  await ctx.plugin(Loader, {baseUrl: import.meta.url});
  for (const row of reviewedRows.filter(row => row.name !== "@deepseek-ai/dsh-client-modules")) {
    await ctx.loader.create({name: await reviewedLoaderName(contract, row.name)});
  }
  await ctx.loader.create({name: "@deepseek-ai/dsh-client-modules"});
  await ctx.loader.await();

  if (ctx.clientModules === undefined) {
    const modulesEntry = [...ctx.loader.entries()].find(
      entry => entry.options.name === "@deepseek-ai/dsh-client-modules",
    );
    throw modulesEntry?.fiber?._error ?? new Error("dsh_client_composition_probe_registry_missing");
  }
  const graph = ctx.clientModules.graph();
  if (graph.release?.version !== contract.dshRelease) {
    throw new Error("dsh_client_composition_probe_graph_release_mismatch");
  }
  if (graph.release?.commit !== contract.dshCommit) {
    throw new Error("dsh_client_composition_probe_graph_commit_mismatch");
  }
  equal(
    sorted(graph.entries.map(entry => entry.id)),
    sorted(contract.dynamicModules),
    "dsh_client_composition_probe_graph_allowlist_mismatch",
  );
  for (const entry of graph.entries) {
    const owners = new Set([...contract.dynamicModules, ...contract.platformModules]);
    const unresolved = (entry.inject ?? []).filter(name => !owners.has(name));
    if (unresolved.length > 0) {
      throw new Error(`dsh_client_composition_probe_inject_outside_graph:${entry.id}:${unresolved.join(",")}`);
    }
  }
  const graphSurface = JSON.stringify(graph);
  const forbiddenGraphToken = contract.forbiddenPackageTokens.find(token => graphSurface.includes(token));
  if (forbiddenGraphToken !== undefined) {
    throw new Error(`dsh_client_composition_probe_forbidden_graph:${forbiddenGraphToken}`);
  }
  const acceptedCandidate = await buildAcceptedManifest(contract, ctx, graph);
  if (stableJson(acceptedCandidate) !== acceptedManifestText) {
    throw new Error("dsh_client_composition_probe_accepted_manifest_mismatch");
  }

  const injections = bootInjections(graph);
  const graphGlobals = injections.filter(row => row.kind === "global" && row.name === "__DSH_BOOT__");
  const inlineRows = injections.filter(row => row.kind === "script");
  const expectedRows = [
    ...graph.batches.filter(batch => batch.phase === "application").map(batch => ({
      kind: "script-preload",
      src: batch.url,
    })),
    ...graph.batches.filter(batch => batch.phase === "bootstrap").map(batch => ({
      kind: "script-src",
      placement: "head",
      src: batch.url,
    })),
  ];
  if (graphGlobals.length !== 1
      || graphGlobals[0].value !== graph
      || inlineRows.length !== 1
      || inlineRows[0].placement !== "head"
      || injections.length !== graph.batches.length + 2
      || JSON.stringify(injections.slice(1, -1)) !== JSON.stringify(expectedRows)) {
    throw new Error("dsh_client_composition_probe_boot_injections_invalid");
  }
  const inlineDigest = createHash("sha256").update(inlineRows[0].text).digest("hex");
  if (inlineDigest !== contract.bootstrapScriptSha256) {
    throw new Error("dsh_client_composition_probe_boot_script_identity_mismatch");
  }
  const recomposedRevision = createHash("sha1")
    .update(JSON.stringify({entries: graph.entries, batches: graph.batches}))
    .digest("hex")
    .slice(0, 12);
  if (graph.rev !== recomposedRevision) {
    throw new Error("dsh_client_composition_probe_graph_revision_mismatch");
  }

  const urls = new Set([
    ...graph.entries.map(entry => entry.url),
    ...graph.batches.map(batch => batch.url),
  ]);
  let bundleBytes = 0;
  for (const url of urls) {
    const absolute = new URL(url, "https://dsh-shell.invalid/");
    const head = await ctx.clientModules.fetchBundle(new Request(absolute, {method: "HEAD"}));
    if (head.status !== 200 || (await head.arrayBuffer()).byteLength !== 0) {
      throw new Error(`dsh_client_composition_probe_bundle_head:${url}`);
    }
    const response = await ctx.clientModules.fetchBundle(new Request(absolute));
    if (response.status !== 200 || !/^text\/javascript(?:;|$)/i.test(response.headers.get("content-type") ?? "")) {
      throw new Error(`dsh_client_composition_probe_bundle_get:${url}`);
    }
    const bytes = (await response.arrayBuffer()).byteLength;
    if (bytes < 1) throw new Error(`dsh_client_composition_probe_bundle_empty:${url}`);
    bundleBytes += bytes;
  }

  const firstEntry = graph.entries[0];
  if (firstEntry === undefined) throw new Error("dsh_client_composition_probe_empty_graph");
  const staleRevisionUrl = firstEntry.url.replace(/rev=[0-9a-f]{12}$/u, "rev=000000000000");
  const staleRevision = await ctx.clientModules.fetchBundle(new Request(
    new URL(staleRevisionUrl, "https://dsh-shell.invalid/"),
  ));
  if (staleRevision.status !== 404) {
    throw new Error("dsh_client_composition_probe_stale_revision_admitted");
  }
  const arbitraryBundle = await ctx.clientModules.fetchBundle(new Request(
    "https://dsh-shell.invalid/plugins/unreviewed/client.js?rev=000000000000",
  ));
  if (arbitraryBundle.status !== 404) {
    throw new Error("dsh_client_composition_probe_arbitrary_bundle_admitted");
  }
  const unsupportedMethod = await ctx.clientModules.fetchBundle(new Request(
    new URL(firstEntry.url, "https://dsh-shell.invalid/"),
    {method: "POST"},
  ));
  if (unsupportedMethod.status !== 405) {
    throw new Error("dsh_client_composition_probe_bundle_method_admitted");
  }

  const entryStates = [...ctx.loader.entries()].map(entry => Object.freeze({
    name: entry.options.name,
    state: entry.fiber?.state ?? null,
  })).sort((left, right) => left.name.localeCompare(right.name));
  process.stdout.write(`${JSON.stringify({
    contract: contract.contract,
    status: "passed",
    productionOwner: contract.productionOwner,
    dshRelease: graph.release,
    acceptedManifestSha256: acceptedCandidate.semanticSha256,
    bootstrapScriptSha256: inlineDigest,
    dynamicModules: graph.entries.length,
    platformModules: contract.platformModules.length,
    bundleResources: urls.size,
    bundleBytes,
    staleRevisionRejected: true,
    arbitraryBundleRejected: true,
    unsupportedBundleMethodRejected: true,
    permissions: contract.permissions,
    entryStates,
  }, null, 2)}\n`);
} finally {
  await ctx.fiber.dispose();
}
