import {createHash} from "node:crypto";
import {readFile} from "node:fs/promises";
import {dirname, isAbsolute, resolve} from "node:path";
import {fileURLToPath, pathToFileURL} from "node:url";

import {Context} from "@deepseek-ai/cordis";
import Loader from "@deepseek-ai/cordis-plugin-loader";

export const FRONTEND_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");

export function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

export function stableJson(value) {
  return `${JSON.stringify(value, null, 2)}\n`;
}

export async function readCompositionContract() {
  return JSON.parse(await readFile(resolve(FRONTEND_ROOT, "dsh-client-composition.json"), "utf8"));
}

/** Only an explicit App-owned entry under the trusted frontend root is local. */
export async function reviewedLoaderName(contract, name) {
  const local = contract.localModules?.[name];
  if (local === undefined) return name;
  if (!name.startsWith("@genshin-calc/") || typeof local.version !== "string"
      || typeof local.hostEntry !== "string" || isAbsolute(local.hostEntry)
      || local.hostEntry.includes("\\") || local.hostEntry.includes(":")
      || local.hostEntry.split("/").some(part => ["", ".", ".."].includes(part))) {
    throw new Error(`dsh_client_local_module_invalid:${name}`);
  }
  const entry = resolve(FRONTEND_ROOT, local.hostEntry);
  const manifest = JSON.parse(await readFile(resolve(dirname(entry), "package.json"), "utf8"));
  if (manifest.name !== name || manifest.version !== local.version || manifest.dsh?.client?.platform !== "web") {
    throw new Error(`dsh_client_local_module_identity:${name}`);
  }
  return pathToFileURL(entry).href;
}

export async function composeReviewedRegistry(contract) {
  const ctx = new Context();
  await ctx.plugin(Loader, {baseUrl: import.meta.url});
  for (const name of contract.dynamicModules.filter(name => name !== "@deepseek-ai/dsh-client-modules")) {
    await ctx.loader.create({name: await reviewedLoaderName(contract, name)});
  }
  await ctx.loader.create({name: "@deepseek-ai/dsh-client-modules"});
  await ctx.loader.await();
  if (ctx.clientModules === undefined) {
    const modulesEntry = [...ctx.loader.entries()].find(
      entry => entry.options.name === "@deepseek-ai/dsh-client-modules",
    );
    const error = modulesEntry?.fiber?._error ?? new Error("dsh_client_review_registry_missing");
    await ctx.fiber.dispose();
    throw error;
  }
  return {ctx, graph: ctx.clientModules.graph()};
}

/**
 * Remove only the exact terminal source-map comment generated for one
 * reviewed single-entry response. Everything else remains covered by the
 * executable digest, including any other sourceURL/sourceMappingURL text.
 */
export function normalizedSingleEntryExecutable(source, id, rev) {
  const trailer = `//# sourceMappingURL=??${id}/client.js.map&rev=${rev}\n`;
  if (!source.endsWith(trailer)) {
    throw new Error(`dsh_client_review_source_map_trailer_invalid:${id}`);
  }
  return source.slice(0, -trailer.length);
}

export async function buildAcceptedManifest(contract, ctx, graph) {
  if (graph.release?.version !== contract.dshRelease || graph.release?.commit !== contract.dshCommit) {
    throw new Error("dsh_client_review_release_mismatch");
  }
  const graphEntries = new Map(graph.entries.map(entry => [entry.id, entry]));
  const entries = [];
  for (const id of contract.dynamicModules) {
    const entry = graphEntries.get(id);
    if (entry === undefined) throw new Error(`dsh_client_review_entry_missing:${id}`);
    const response = await ctx.clientModules.fetchBundle(
      new Request(new URL(entry.url, "https://dsh-shell.invalid/")),
    );
    if (response.status !== 200) throw new Error(`dsh_client_review_bundle_http:${id}:${response.status}`);
    const bytes = new Uint8Array(await response.arrayBuffer());
    const source = new TextDecoder("utf-8", {fatal: true}).decode(bytes);
    const executable = normalizedSingleEntryExecutable(source, id, entry.rev);
    entries.push({
      id,
      inject: [...(entry.inject ?? [])],
      external: [...(entry.external ?? [])],
      immediately: entry.immediately === true,
      executableSha256: sha256(executable),
      executableBytes: Buffer.byteLength(executable),
    });
  }
  if (graphEntries.size !== entries.length) throw new Error("dsh_client_review_entry_count_mismatch");
  const unsigned = {
    contract: "genshin-dsh-client-accepted-manifest-v1",
    contractVersion: 1,
    dshRelease: contract.dshRelease,
    dshCommit: contract.dshCommit,
    bootstrapScriptSha256: contract.bootstrapScriptSha256,
    platformModules: [...contract.platformModules],
    entryOrder: [...contract.dynamicModules],
    runtimeGraph: {
      batchPolicy: "single-entry-canonical-v1",
      bootstrapEntry: "@deepseek-ai/dsh-client-modules",
      bundleUrlShape: "plugins/??{entry-id}/client.js&rev={12-lower-hex}",
      exactTerminalTrailer: "//# sourceMappingURL=??{entry-id}/client.js.map&rev={12-lower-hex}\\n",
    },
    entries,
  };
  return {
    ...unsigned,
    semanticSha256: sha256(JSON.stringify(unsigned)),
  };
}
