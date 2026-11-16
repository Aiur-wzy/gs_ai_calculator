import {createHash} from "node:crypto";
import {lstat,readFile,readdir} from "node:fs/promises";
import {resolve} from "node:path";

export const BASE_SOURCE_FILES = ["package.json", "pnpm-lock.yaml", "tsconfig.json",
  "dsh-client-composition.json", "vite.business.config.ts", "scripts/build-source-provenance.mjs",
  "scripts/build-dsh-domain-plugin.mjs", "scripts/sync-business-ui.mjs",
  "vite.legacy.config.ts", "scripts/sync-production-foundation.mjs"];
export const sha256 = bytes => createHash("sha256").update(bytes).digest("hex");

/** A complete source set: added files matter just as much as edits to old files. */
export async function sourceFiles(root, extra = []) {
  const paths = new Set([...BASE_SOURCE_FILES, ...extra]);
  async function collect(directory) {
    let entries;
    try {
      if ((await lstat(resolve(root,directory))).isSymbolicLink()) throw new Error("dsh_production_source_symlink");
      entries = await readdir(resolve(root, directory), {withFileTypes:true});
    }
    catch (error) { if (error.code === "ENOENT") return; throw error; }
    for (const entry of entries) {
      if (entry.isSymbolicLink()) throw new Error("dsh_production_source_symlink");
      const path = `${directory}/${entry.name}`;
      if (entry.isDirectory() && !["lib", "dist", "node_modules", "__tests__"].includes(entry.name)) await collect(path);
      else if (entry.isFile() && !/\.(test|spec)\./.test(entry.name) && /\.(ts|tsx|js|mjs|css|json)$/.test(entry.name)) paths.add(path);
    }
  }
  await collect("src"); await collect("packages/dsh-client-domain");
  const inputs = {};
  for (const path of [...paths].sort()) {
    if ((await lstat(resolve(root, path))).isSymbolicLink()) throw new Error("dsh_production_source_symlink");
    inputs[path] = sha256(await readFile(resolve(root, path)));
  }
  return inputs;
}

export async function sourceInputs(root, scope) {
  if (!["business", "domain", "foundation"].includes(scope)) throw new Error("dsh_source_scope_invalid");
  return {contract:"genshin-source-inputs-v1", scope, inputs:await sourceFiles(root)};
}

export function sourceBanner(inputs) {
  return `/*! GENSHIN_SOURCE_INPUTS_V1:${Buffer.from(JSON.stringify(inputs)).toString("base64")} */`;
}

export function readSourceInputs(bytes) {
  const matches = [...bytes.toString("utf8").matchAll(/\/\*! GENSHIN_SOURCE_INPUTS_V1:([A-Za-z0-9+/=]+) \*\//g)];
  if (matches.length !== 1) throw new Error("dsh_source_embedded_identity_missing_or_duplicate");
  return JSON.parse(Buffer.from(matches[0][1], "base64").toString("utf8"));
}

export async function verifySourceInputs(root, scope, bytes) {
  const actual = readSourceInputs(bytes), expected = await sourceInputs(root, scope);
  if (JSON.stringify(actual) !== JSON.stringify(expected)) throw new Error(`dsh_child_source_inputs_mismatch:${scope}:rebuild_child_bundle`);
  return actual;
}

/** Rollup owns banner placement and sourcemap offsets. Never mutate chunks in generateBundle. */
export function provenancePlugin({name, capture, banner}) {
  let inputs;
  return {
    name,
    async buildStart() { inputs = await capture(); },
    outputOptions(options) {
      const previous = options.banner;
      return {...options, banner:async function(chunk) {
        const existing = typeof previous === "function" ? await previous.call(this, chunk) : previous;
        return `${banner(inputs)}${existing ? `\n${existing}` : ""}`;
      }};
    },
    async generateBundle(_options, bundle) {
      if (JSON.stringify(inputs) !== JSON.stringify(await capture())) throw new Error(`${name}_inputs_changed_during_build`);
      if (!Object.values(bundle).some(item => item.type === "chunk" && item.isEntry)) throw new Error(`${name}_entry_missing`);
    },
  };
}

export function sourceProvenancePlugin(root, scope) {
  return provenancePlugin({name:`genshin-${scope}-provenance`, capture:() => sourceInputs(root, scope), banner:sourceBanner});
}
