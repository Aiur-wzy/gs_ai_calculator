import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import {sourceFiles,sha256,verifySourceInputs,provenancePlugin} from "./build-source-provenance.mjs";

const MARKER = "GENSHIN_PRODUCTION_INPUTS_V1";
export const PRODUCTION_FILES = ["dsh-client-accepted-manifest.json", "vite.production.config.ts",
  "scripts/production-provenance.mjs", "scripts/sync-production-dsh.mjs", "scripts/write-build-metadata.mjs"];
export const CHILD_PATHS = {business:"../src/genshin_app/gui/web/business-ui.js", domain:"packages/dsh-client-domain/lib/client.js", foundation:"../src/genshin_app/gui/web/gui-foundation-v2.js"};

/** Build inputs are captured before bundling, never inferred from a sidecar afterwards. */
export async function productionInputs(root) {
  const inputs = await sourceFiles(root, PRODUCTION_FILES);
  const accepted = JSON.parse(await readFile(resolve(root, "dsh-client-accepted-manifest.json"), "utf8"));
  if (!/^[a-f0-9]{64}$/.test(accepted.semanticSha256 ?? "")) throw new Error("dsh_production_accepted_manifest_invalid");
  const tokens = await readFile(resolve(root,"../src/genshin_app/gui/web/gui-foundation-tokens.css"));
  if (sha256(tokens) !== inputs["src/design-system/tokens/tokens.generated.css"]) throw new Error("dsh_foundation_tokens_source_mismatch");
  const children = {};
  for (const [scope, path] of Object.entries(CHILD_PATHS)) {
    const bytes = await readFile(resolve(root,path));
    children[scope] = {bytes:bytes.length, sha256:sha256(bytes), sourceInputs:await verifySourceInputs(root,scope,bytes)};
  }
  return {contract: "genshin-production-inputs-v1", acceptedSemanticSha256: accepted.semanticSha256,
    dshRelease: accepted.dshRelease, inputs, children};
}

export function inputBanner(inputs) {
  return `/*! ${MARKER}:${Buffer.from(JSON.stringify(inputs)).toString("base64")} */`;
}

export function readEmbeddedInputs(bytes) {
  const source = bytes.toString("utf8");
  const matches = [...source.matchAll(/\/\*! GENSHIN_PRODUCTION_INPUTS_V1:([A-Za-z0-9+/=]+) \*\//g)];
  if (matches.length !== 1) throw new Error("dsh_production_embedded_identity_missing_or_duplicate");
  return JSON.parse(Buffer.from(matches[0][1], "base64").toString("utf8"));
}

export function verifyEmbeddedInputs(bytes, expected) {
  if (JSON.stringify(readEmbeddedInputs(bytes)) !== JSON.stringify(expected)) {
    throw new Error("dsh_production_compiled_inputs_mismatch:rebuild_browser_after_domain_and_manifest");
  }
}

/** Changes during compilation must invalidate the build rather than produce mixed generations. */
export function productionProvenancePlugin(root) {
  return provenancePlugin({name:"genshin-production-provenance", capture:() => productionInputs(root), banner:inputBanner});
}
